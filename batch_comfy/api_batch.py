#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API 并行批量生图（纯标准库）

支持三类主流接口，自动按 base_url / 模型名判断：
  1. OpenAI 风格  POST {base}/images/generations      （gpt-image-1 / dall-e-3 / 多数中转站）
  2. Gemini 风格  POST {base}/models/{model}:generateContent  （gemini-*-image / nano-banana）
  3. 自定义       在 config 里写 "type" 指定

用法
----
# 0) 先体检：看配置对不对、能不能连通
python api_batch.py --check

# 1) 用任务表批量跑（和本地 ComfyUI 共用同一份 CSV）
python api_batch.py --tasks "任务表.csv" \
    --out "./output/api" --workers 5

# 2) 先试 3 条
python api_batch.py --tasks ... --limit 3 --workers 5

# 3) 图生图（把 source 列当参考图，仅支持 edits 的接口可用）
python api_batch.py --tasks 任务表.csv --out 输出 --workers 3 --edit
"""
import argparse
import base64
import concurrent.futures as cf
import csv
import io
import json
import mimetypes
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "api_config.json"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

COL_ALIAS = {
    "素材": "source", "文件": "source", "路径": "source", "图片": "source",
    "提示词": "prompt", "正向": "prompt", "positive": "prompt",
    "反向": "negative", "反向提示词": "negative", "负面": "negative",
    "种子": "seed", "随机种子": "seed",
    "前缀": "prefix", "文件名": "prefix", "输出名": "prefix",
    "宽": "width", "高": "height", "尺寸": "size",
    "质量": "quality", "张数": "n",
}

DEFAULT_CONFIG = {
    "base_url": "https://api.openai.com/v1",
    "api_key": "在这里填你的 key",
    "model": "gpt-image-1",
    "type": "auto",
    "size": "1024x1024",
    "quality": "high",
    "timeout": 300,
    "retry": 2,
    "extra_headers": {},
    "extra_body": {},
}


# ------------------------------------------------------------------ 配置
def load_config(path=None, silent=False):
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        json.dump(DEFAULT_CONFIG, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        if not silent:
            print("已创建配置模板:", p)
            print("请打开填入 base_url / api_key / model，然后重新运行。")
        return None
    cfg = json.load(open(p, encoding="utf-8"))
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    if not cfg.get("api_key") or "填" in str(cfg["api_key"]) or "在这里" in str(cfg["api_key"]):
        if not silent:
            print("配置里 api_key 还没填，打开这个文件填好再运行：")
            print("   ", p)
            print("需要填三行：base_url（接口地址）、api_key（密钥）、model（模型名）")
        return None
    return cfg


def detect_type(cfg):
    t = (cfg.get("type") or "auto").lower()
    if t != "auto":
        return t
    base = (cfg.get("base_url") or "").lower()
    model = (cfg.get("model") or "").lower()
    if "generativelanguage" in base or model.startswith("gemini") or "image" in model and "gpt" not in model:
        return "gemini"
    return "openai"


# ------------------------------------------------------------------ HTTP
def post_json(url, payload, headers, timeout=300):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("HTTP %s: %s" % (e.code, body)) from None


def get_bytes(url, headers=None, timeout=300):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def tidy_base(base):
    base = (base or "").rstrip("/")
    for suf in ("/images/generations", "/images/edits", "/chat/completions"):
        if base.endswith(suf):
            base = base[: -len(suf)]
    if base.endswith("/v1"):
        return base
    return base


# ------------------------------------------------------------------ 各协议
def pick_supported_size(cfg, want):
    """把任意宽高映射到接口支持的尺寸，避免 400 报错"""
    sizes = cfg.get("supported_sizes") or []
    if not sizes:
        # gpt-image 系列只支持这三种
        model = (cfg.get("model") or "").lower()
        if "gpt-image" in model:
            sizes = ["1024x1024", "1536x1024", "1024x1536"]
        else:
            return want
    if not want:
        return sizes[0]
    try:
        w, h = [int(x) for x in str(want).lower().split("x")]
    except ValueError:
        return sizes[0]
    ratio = w / float(h) if h else 1.0
    best, best_d = sizes[0], 9e9
    for s in sizes:
        try:
            sw, sh = [int(x) for x in s.lower().split("x")]
        except ValueError:
            continue
        d = abs(sw / float(sh) - ratio) + abs(sw * sh - w * h) / (1024.0 * 1024.0) * 0.1
        if d < best_d:
            best, best_d = s, d
    return best


def build_headers(cfg):
    h = {
        "Authorization": "Bearer " + cfg["api_key"],
        "Content-Type": "application/json",
    }
    h.update(cfg.get("extra_headers") or {})
    return h


def call_openai(cfg, prompt, task, ref_image=None):
    """OpenAI /images/generations（文生图）或 /images/edits（图生图）"""
    base = tidy_base(cfg["base_url"])
    want = cfg["size"]
    if task.get("size"):
        want = task["size"]
    elif task.get("width") and task.get("height"):
        want = "%sx%s" % (task["width"], task["height"])
    size = pick_supported_size(cfg, want)

    if ref_image:
        # multipart 上传
        url = base + "/images/edits"
        boundary = "----apibatch" + str(random.randint(10**8, 10**9))
        parts = []
        for k, v in (("model", cfg["model"]), ("prompt", prompt), ("size", size),
                     ("n", str(task.get("n") or 1))):
            parts.append(("--" + boundary + "\r\n").encode())
            parts.append(('Content-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (k, v)).encode("utf-8"))
        p = Path(ref_image)
        ctype = mimetypes.guess_type(p.name)[0] or "image/png"
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append(('Content-Disposition: form-data; name="image"; filename="%s"\r\n' % p.name).encode("utf-8"))
        parts.append(("Content-Type: %s\r\n\r\n" % ctype).encode())
        parts.append(p.read_bytes())
        parts.append(("\r\n--" + boundary + "--\r\n").encode())
        body = b"".join(parts)
        headers = build_headers(cfg)
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=cfg["timeout"]) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError("HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:400])) from None
    else:
        payload = {"model": cfg["model"], "prompt": prompt, "n": int(task.get("n") or 1)}
        if size:
            payload["size"] = size
        if cfg.get("quality"):
            payload["quality"] = cfg["quality"]
        payload.update(cfg.get("extra_body") or {})
        resp = post_json(base + "/images/generations", payload, build_headers(cfg), cfg["timeout"])
    return extract_openai_images(resp, cfg)


def extract_openai_images(resp, cfg):
    out = []
    for item in resp.get("data") or []:
        if item.get("b64_json"):
            out.append(base64.b64decode(item["b64_json"]))
        elif item.get("url"):
            out.append(get_bytes(item["url"], timeout=cfg["timeout"]))
    if not out:
        raise RuntimeError("返回里没有图片: " + json.dumps(resp, ensure_ascii=False)[:300])
    return out


def call_gemini(cfg, prompt, task, ref_image=None):
    base = (cfg["base_url"] or "").rstrip("/")
    if base.endswith("/v1beta") or base.endswith("/v1"):
        root = base
    else:
        root = base + "/v1beta"
    url = "%s/models/%s:generateContent?key=%s" % (root, cfg["model"], cfg["api_key"])
    parts = [{"text": prompt}]
    if ref_image:
        p = Path(ref_image)
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        parts.append({"inline_data": {"mime_type": mime,
                                      "data": base64.b64encode(p.read_bytes()).decode()}})
    payload = {"contents": [{"role": "user", "parts": parts}]}
    cfg2 = dict(cfg)
    cfg2.pop("extra_body", None)
    body_extra = cfg.get("extra_body") or {}
    if body_extra:
        payload.update(body_extra)
    resp = post_json(url, payload, {"Content-Type": "application/json"}, cfg["timeout"])
    out = []
    for cand in resp.get("candidates") or []:
        for part in (cand.get("content") or {}).get("parts") or []:
            inline = part.get("inline_data") or part.get("inlineData")
            if inline and inline.get("data"):
                out.append(base64.b64decode(inline["data"]))
    if not out:
        raise RuntimeError("返回里没有图片: " + json.dumps(resp, ensure_ascii=False)[:300])
    return out


def generate_one(cfg, prompt, task, ref_image=None):
    t = detect_type(cfg)
    if t == "gemini":
        return call_gemini(cfg, prompt, task, ref_image)
    return call_openai(cfg, prompt, task, ref_image)


# ------------------------------------------------------------------ 任务表
def open_csv_smart(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return io.StringIO(raw.decode(enc))
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise SystemExit("任务表编码识别失败，请在 WPS 里另存为 UTF-8 CSV")


def norm_row(row):
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        k2 = k.strip().lower()
        out[COL_ALIAS.get(k2, COL_ALIAS.get(k.strip(), k2))] = (v or "").strip()
    return out


def load_tasks(args):
    if not args.tasks:
        raise SystemExit("需要 --tasks 任务表 CSV")
    tasks = []
    for i, row in enumerate(csv.DictReader(open_csv_smart(args.tasks)), 1):
        r = norm_row(row)
        if not any(r.values()):
            continue
        r["_row"] = i
        tasks.append(r)
    if not tasks:
        raise SystemExit("任务表是空的")
    missing = [t["_row"] for t in tasks if not t.get("prompt")]
    if missing:
        raise SystemExit(
            "这些行没有提示词: %s\n请用 WPS 打开任务表填写 prompt 列。" % missing[:10]
        )
    return tasks


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(description="API 并行批量生图")
    ap.add_argument("--config", default=None, help="配置文件路径，默认 api_config.json")
    ap.add_argument("--tasks", help="任务表 CSV")
    ap.add_argument("--out", help="输出目录")
    ap.add_argument("--workers", type=int, default=4, help="并发数（默认 4）")
    ap.add_argument("--limit", type=int, help="只跑前 N 条")
    ap.add_argument("--edit", action="store_true", help="用 source 列作参考图（图生图）")
    ap.add_argument("--no-skip-done", action="store_true", help="不跳过已完成的")
    ap.add_argument("--check", action="store_true", help="只检查配置连通性")
    ap.add_argument("--dry-run", action="store_true", help="只解析不请求")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if cfg is None:
        sys.exit(1)
    proto = detect_type(cfg)
    print("接口:", cfg["base_url"])
    print("模型:", cfg["model"], "| 协议:", proto, "| 并发:", args.workers)

    if args.check:
        t0 = time.time()
        try:
            imgs = generate_one(cfg, "a simple red circle on white background", {})
            print("连通成功，用时 %.1fs，返回 %d 张图（%d 字节）"
                  % (time.time() - t0, len(imgs), len(imgs[0])))
            Path(args.out or ".").mkdir(parents=True, exist_ok=True)
            probe = Path(args.out or ".") / "_api_check.png"
            probe.write_bytes(imgs[0])
            print("测试图已存到:", probe)
        except Exception as e:
            print("失败:", e)
            sys.exit(1)
        return

    if not args.tasks or not args.out:
        raise SystemExit("需要 --tasks 和 --out")

    tasks = load_tasks(args)
    if args.limit:
        tasks = tasks[: args.limit]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    state_path = out_dir / "_state.json"
    state = json.load(open(state_path, encoding="utf-8")) if state_path.exists() else {}
    lock = threading.Lock()
    counter = {"ok": 0, "fail": 0, "skip": 0}
    total = len(tasks)
    t_start = time.time()

    def key_of(t):
        return "%s|%s|%s" % (t.get("source", ""), t["prompt"], t.get("seed", ""))

    def work(idx_task):
        idx, t = idx_task
        prompt = t["prompt"]
        stem = Path(t["source"]).stem if t.get("source") else ""
        prefix = t.get("prefix") or stem or "gen_%03d" % idx
        if t.get("seed"):
            # 有的接口支持 seed，写进 extra_body 的用户可自行扩展
            pass
        head = "[%d/%d] %s" % (idx, total, prefix)

        if not args.no_skip_done and key_of(t) in state:
            have = [f for f in state[key_of(t)].get("outputs", []) if (out_dir / f).exists()]
            if have:
                with lock:
                    counter["skip"] += 1
                print(head, "→ 已存在，跳过")
                return

        ref = t.get("source") if (args.edit and t.get("source")) else None
        if ref and not Path(ref).exists():
            with lock:
                counter["fail"] += 1
            print(head, "→ 素材不存在:", ref)
            return

        t0 = time.time()
        last_err = None
        for attempt in range(cfg["retry"] + 1):
            try:
                blobs = generate_one(cfg, prompt, t, ref)
                saved = []
                for i, b in enumerate(blobs):
                    tag = "" if len(blobs) == 1 else "_%d" % (i + 1)
                    dst = out_dir / ("%s%s.png" % (prefix, tag))
                    k = 2
                    while dst.exists():
                        dst = out_dir / ("%s%s_%d.png" % (prefix, tag, k))
                        k += 1
                    dst.write_bytes(b)
                    saved.append(dst.name)
                with lock:
                    state[key_of(t)] = {"outputs": saved, "prompt": prompt,
                                        "source": t.get("source", ""),
                                        "time": time.strftime("%Y-%m-%d %H:%M:%S")}
                    json.dump(state, open(state_path, "w", encoding="utf-8"),
                              ensure_ascii=False, indent=1)
                    counter["ok"] += 1
                print(head, "→ 完成 %.1fs  %s" % (time.time() - t0, ", ".join(saved)))
                return
            except Exception as e:
                last_err = str(e).replace("\n", " ")[:250]
                if attempt < cfg["retry"]:
                    time.sleep(2 * (attempt + 1))
        with lock:
            counter["fail"] += 1
        print(head, "→ 失败", last_err)

    # dry-run 时按并发顺序打印任务，不实际请求
    if args.dry_run:
        for i, t in enumerate(tasks, 1):
            print("[%d/%d] %s  size=%s" % (i, total, (t.get("prefix") or "gen_%03d" % i),
                                           t.get("size") or cfg["size"]))
            print("     ", t["prompt"][:60])
        print("\n共 %d 条，并发 %d" % (total, args.workers))
        return

    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        list(ex.map(work, list(enumerate(tasks, 1))))

    dur = time.time() - t_start
    print("\n完成 %d 条，失败 %d 条，跳过 %d 条，用时 %.1f 分钟"
          % (counter["ok"], counter["fail"], counter["skip"], dur / 60))
    if counter["ok"]:
        print("平均每条 %.1fs" % (dur / counter["ok"]))
    print("输出目录:", out_dir)
    if counter["fail"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
