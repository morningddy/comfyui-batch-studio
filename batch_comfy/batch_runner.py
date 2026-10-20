#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ComfyUI 批量执行器（纯标准库，无需 pip install）

用法示例
--------
# 0) 只看解析出的节点映射，不提交
python batch_runner.py --workflow workflows/z-image-t2i.api.json --inspect

# 1) 一个文件夹 + 一条提示词：所有素材跑同一套词
python batch_runner.py --workflow workflows/z-image-i2i.api.json \
    --folder "D:/素材/线稿" --prompt "线稿上色，日系动画赛璐璐，柔和光影" \
    --out "D:/输出/上色"

# 2) CSV：每个素材一条（可各带自己的提示词/种子/尺寸）
python batch_runner.py --workflow workflows/z-image-i2i.api.json \
    --tasks tasks.csv --out "D:/输出/批量"

# 3) 素材旁放同名 .txt 当提示词
python batch_runner.py --workflow workflows/z-image-i2i.api.json \
    --folder "D:/素材/线稿" --prompt-from-txt --out "D:/输出/批量"
"""
import argparse
import csv
import hashlib
import json
import mimetypes
import os
import random
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".gif", ".m4v"}

DEFAULT_SERVERS = ["http://127.0.0.1:8188", "http://127.0.0.1:8000", "http://127.0.0.1:8189"]

# CSV 列名别名（统一转小写匹配）
COL_ALIAS = {
    "素材": "source", "文件": "source", "路径": "source", "图片": "source", "视频": "source",
    "提示词": "prompt", "正向": "prompt", "positive": "prompt",
    "反向": "negative", "反向提示词": "negative", "负面": "negative",
    "种子": "seed", "随机种子": "seed",
    "前缀": "prefix", "文件名": "prefix", "输出名": "prefix",
    "宽": "width", "高": "height", "降噪": "denoise", "重绘幅度": "denoise",
    "步数": "steps", "cfg": "cfg",
}


# ---------------------------------------------------------------- HTTP helpers
def http_json(url, payload=None, timeout=60):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    return json.loads(body.decode("utf-8")) if body else {}


def http_get_bytes(url, timeout=120):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def http_upload(server, path, subfolder="_batch", timeout=300):
    """POST /upload/image  multipart（手写，免依赖）。可传任意文件类型（含视频）"""
    boundary = "----comfybatch" + uuid.uuid4().hex
    p = Path(path)
    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    digest = hashlib.md5(str(p.resolve()).encode("utf-8")).hexdigest()[:8]
    upload_name = "%s_%s%s" % (p.stem[:40], digest, p.suffix.lower())
    parts = []
    for k, v in (("type", "input"), ("subfolder", subfolder), ("overwrite", "true")):
        parts.append(("--" + boundary + "\r\n").encode())
        parts.append(('Content-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (k, v)).encode("utf-8"))
    parts.append(("--" + boundary + "\r\n").encode())
    parts.append(
        ('Content-Disposition: form-data; name="image"; filename="%s"\r\n' % upload_name).encode("utf-8")
    )
    parts.append(("Content-Type: %s\r\n\r\n" % ctype).encode())
    parts.append(p.read_bytes())
    parts.append(("\r\n--" + boundary + "--\r\n").encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        server + "/upload/image",
        data=body,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def pick_server(arg):
    if arg and arg.lower() != "auto":
        return arg.rstrip("/")
    for s in DEFAULT_SERVERS:
        try:
            http_json(s + "/system_stats", timeout=3)
            return s
        except Exception:
            continue
    raise SystemExit(
        "连不上 ComfyUI。先启动 ComfyUI，或用 --server http://127.0.0.1:端口 指定。\n"
        "试过：" + ", ".join(DEFAULT_SERVERS)
    )


# ---------------------------------------------------------------- workflow map
def upstream(api, node_id, input_name):
    node = api.get(str(node_id))
    if not node:
        return None
    v = node.get("inputs", {}).get(input_name)
    if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
        return v[0]
    return None


TEXT_CLASSES = {
    "CLIPTextEncode", "CLIPTextEncodeFlux", "CLIPTextEncodeSDXL",
    "T5TextEncode", "BNK_CLIPTextEncodeAdvanced", "smZ CLIPTextEncode",
}


def walk_to_text(api, node_id, depth=0):
    """沿 conditioning 链向上游找文本编码节点"""
    seen = set()
    while node_id and node_id not in seen and depth < 20:
        seen.add(node_id)
        node = api.get(node_id)
        if not node:
            return None
        if node["class_type"] in TEXT_CLASSES:
            return node_id
        nxt = None
        for k in ("conditioning", "conditioning_to", "conditioning_from",
                  "positive", "negative", "conditioning_1", "conditioning_2"):
            nxt = upstream(api, node_id, k)
            if nxt:
                break
        node_id = nxt
        depth += 1
    return None


def sampler_slots(api, nid):
    """返回 (positive节点id, negative节点id)，找不到给 None"""
    cls = api[nid]["class_type"]
    if cls == "SamplerCustomAdvanced":
        g = upstream(api, nid, "guider")
        if not g:
            return None, None
        if "positive" in api[g]["inputs"]:
            return walk_to_text(api, upstream(api, g, "positive")), \
                   walk_to_text(api, upstream(api, g, "negative"))
        return walk_to_text(api, upstream(api, g, "conditioning")), None
    return walk_to_text(api, upstream(api, nid, "positive")), \
           walk_to_text(api, upstream(api, nid, "negative"))


def detect_map(api):
    """自动识别可注入的节点，返回 dict"""
    m = {"loaders": [], "samplers": [], "positive": None, "negative": None,
         "latent": None, "savers": [], "seed_key": "seed"}
    sampler_priority = ["KSampler", "KSamplerAdvanced", "SamplerCustomAdvanced"]
    for nid, node in api.items():
        cls = node["class_type"]
        ins = node.get("inputs", {})
        if cls == "LoadImage":
            m["loaders"].append((nid, "image"))
        elif "LoadVideo" in cls or "LoadVideoPath" in cls:
            key = "video" if "video" in ins else ("file" if "file" in ins else "video")
            m["loaders"].append((nid, key))
        elif cls in ("SaveImage", "SaveAnimatedWEBP", "SaveAnimatedPNG", "VHS_VideoCombine"):
            m["savers"].append(nid)
        elif cls in sampler_priority or cls.startswith("KSampler"):
            m["samplers"].append(nid)
            if "noise_seed" in ins:
                m["seed_key"] = "noise_seed"
        elif cls == "RandomNoise":
            m["samplers"].append(nid)
            m["seed_key"] = "noise_seed"
        elif cls.startswith("Empty") and "Latent" in cls:
            m["latent"] = nid

    # 正/负提示词：优先取优先级最高的采样器
    order = {c: i for i, c in enumerate(sampler_priority)}
    m["samplers"].sort(key=lambda s: order.get(api[s]["class_type"], 9))
    for sid in m["samplers"]:
        pos, neg = sampler_slots(api, sid)
        if pos:
            m["positive"], m["negative"] = pos, neg
            m["main_sampler"] = sid
            break
    m.setdefault("main_sampler", m["samplers"][0] if m["samplers"] else None)
    return m


def print_map(api, m):
    print("节点映射：")
    for nid, node in api.items():
        tags = []
        if (nid, "image") in m["loaders"]:
            tags.append("← 素材图")
        if any(nid == l[0] for l in m["loaders"]) and not tags:
            tags.append("← 素材(视频)")
        if nid == m["positive"]:
            tags.append("← 正向提示词")
        if nid == m["negative"]:
            tags.append("← 反向提示词")
        if nid == m["latent"]:
            tags.append("← 尺寸")
        if nid in m["samplers"]:
            tags.append("← 种子")
        if nid in m["savers"]:
            tags.append("← 输出前缀")
        if tags:
            print("  %-4s %-28s %s" % (nid, node["class_type"], "  ".join(tags)))
    if not m["positive"]:
        print("  ⚠ 没自动找到正向提示词节点，用 --map 手动指定")
    if not m["loaders"]:
        print("  · 没有素材加载节点（纯文生图/文生视频工作流）")


# ---------------------------------------------------------------- input staging
def guess_comfy_root(workflow_path):
    """反推 ComfyUI 根目录（含 input/models）。仅 --local-copy 时用到"""
    # 1) ComfyUI Desktop 的 settings.json 里有真实 inputDir
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        cfg = Path(appdata) / "Comfy Desktop" / "settings.json"
        if cfg.exists():
            try:
                s = json.load(open(cfg, encoding="utf-8"))
                inp = s.get("inputDir")
                if inp and Path(inp).is_dir():
                    return Path(inp).parent
            except Exception:
                pass
    # 2) 从 workflow 路径往上找
    p = Path(workflow_path).resolve()
    for parent in p.parents:
        if (parent / "input").is_dir() and (parent / "models").is_dir():
            return parent
    # 3) 常见安装位置（按盘符遍历，不写死用户机器路径）
    roots = ["C:/", "D:/", "E:/", "F:/"]
    subs = [
        "ComfyUI", "comfyui", "Comfy-Desktop/ComfyUI-Installs/ComfyUI/ComfyUI",
        "ComfyUI_windows_portable/ComfyUI", "AI/ComfyUI",
    ]
    for r in roots:
        for s in subs:
            cand = Path(r) / s
            try:
                if (cand / "input").is_dir() and (cand / "models").is_dir():
                    return cand
            except OSError:
                continue
    return None


def stage_file(path, comfy_root, cache, server, prefer_local=False):
    """把素材送进 ComfyUI 的 input 目录，返回节点可直接用的相对路径。

    默认走 HTTP /upload/image（对 Desktop / 便携版 / 远程服务都通用），
    只有 --local-copy 或没有服务时才直接复制文件。
    """
    p = Path(path).resolve()
    if not p.exists():
        raise FileNotFoundError(path)
    key = str(p).lower()
    if key in cache:
        return cache[key]

    rel = None
    if prefer_local and comfy_root:
        inp = comfy_root / "input" / "_batch"
        inp.mkdir(parents=True, exist_ok=True)
        digest = hashlib.md5(str(p).encode("utf-8")).hexdigest()[:8]
        name = "%s_%s%s" % (p.stem[:40], digest, p.suffix.lower())
        dst = inp / name
        if not dst.exists() or dst.stat().st_size != p.stat().st_size:
            shutil.copy2(p, dst)
        rel = "_batch/" + name
    elif server:
        r = http_upload(server, str(p))
        name = r.get("name", p.name)
        sub = (r.get("subfolder") or "").strip("/")
        rel = (sub + "/" + name) if sub else name
    elif comfy_root:
        inp = comfy_root / "input" / "_batch"
        inp.mkdir(parents=True, exist_ok=True)
        digest = hashlib.md5(str(p).encode("utf-8")).hexdigest()[:8]
        name = "%s_%s%s" % (p.stem[:40], digest, p.suffix.lower())
        dst = inp / name
        if not dst.exists():
            shutil.copy2(p, dst)
        rel = "_batch/" + name
    else:
        raise SystemExit("既连不上 ComfyUI，也找不到 input 目录；用 --comfy-root 指定")

    cache[key] = rel
    return rel


# ---------------------------------------------------------------- task loading
def norm_row(row):
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        k2 = k.strip().lower()
        k2 = COL_ALIAS.get(k2, COL_ALIAS.get(k.strip(), k2))
        out[k2] = (v or "").strip()
    return out


def open_csv_smart(path):
    """兼容 UTF-8 / UTF-8 BOM / GBK（WPS、Excel 另存的 ANSI）"""
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            import io
            return io.StringIO(raw.decode(enc))
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise SystemExit("任务表编码识别失败，请在 WPS 里另存为 UTF-8 CSV: " + str(path))


def load_tasks(args):
    tasks = []
    if args.tasks:
        f = open_csv_smart(args.tasks)
        for i, row in enumerate(csv.DictReader(f), 1):
                r = norm_row(row)
                if not any(r.values()):
                    continue
                r["_row"] = i
                tasks.append(r)
    elif args.folder:
        folder = Path(args.folder)
        if not folder.is_dir():
            raise SystemExit("文件夹不存在: " + str(folder))
        exts = VIDEO_EXT if args.kind == "video" else IMAGE_EXT
        files = sorted(
            [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts]
        )
        if args.recursive:
            files = sorted(
                [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in exts]
            )
        for i, p in enumerate(files, 1):
            t = {"source": str(p), "_row": i}
            if args.prompt:
                t["prompt"] = args.prompt
            elif args.prompt_from_txt:
                txt = p.with_suffix(".txt")
                if txt.exists():
                    t["prompt"] = txt.read_text(encoding="utf-8").strip()
            tasks.append(t)
    else:
        raise SystemExit("需要 --tasks 或 --folder")

    # 兜底：CSV 里没写提示词的，读同名 txt
    if args.prompt_from_txt:
        for t in tasks:
            if not t.get("prompt") and t.get("source"):
                txt = Path(t["source"]).with_suffix(".txt")
                if txt.exists():
                    t["prompt"] = txt.read_text(encoding="utf-8").strip()

    if args.limit:
        tasks = tasks[: args.limit]

    for t in tasks:  # 校验
        if not t.get("prompt") and not args.allow_empty_prompt:
            raise SystemExit(
                "第 %s 条没有提示词。请先用 WPS 打开任务表 CSV，填写 prompt 列再跑；"
                "或者临时用 --prompt \"统一提示词\" 给所有素材一条同样的提示词。"
                % t.get("_row", "?")
            )
    return tasks


# ---------------------------------------------------------------- patch & run
def patch(api, m, task, staged_path):
    wf = json.loads(json.dumps(api))  # deep copy
    prompt = task.get("prompt", "")
    negative = task.get("negative", "")

    if staged_path:
        for nid, key in m["loaders"]:
            wf[nid]["inputs"][key] = staged_path
        if not m["loaders"]:
            raise SystemExit("工作流里没有 LoadImage/LoadVideo 节点，但这一条带了素材")
    elif m["loaders"]:
        raise SystemExit("工作流需要素材，但这一条没给 source")

    if prompt:
        if not m["positive"]:
            raise SystemExit("没找到正向提示词节点，请用 --map 指定")
        wf[m["positive"]]["inputs"]["text"] = prompt
    if negative and m["negative"]:
        wf[m["negative"]]["inputs"]["text"] = negative

    seed = task.get("seed")
    seed = int(seed) if seed else random.randint(0, 2 ** 32 - 1)
    for sid in m["samplers"]:
        ins = wf[sid]["inputs"]
        if "noise_seed" in ins:
            ins["noise_seed"] = seed
        elif "seed" in ins:
            ins["seed"] = seed

    for k in ("width", "height", "steps", "cfg", "denoise"):
        if task.get(k) and m["latent"] and k in ("width", "height"):
            wf[m["latent"]]["inputs"][k] = int(float(task[k]))
        elif task.get(k) and m["main_sampler"] and k in wf[m["main_sampler"]]["inputs"]:
            val = float(task[k]) if k in ("cfg", "denoise") else int(float(task[k]))
            wf[m["main_sampler"]]["inputs"][k] = val

    # 自定义映射：--map "12.text=prompt"
    for spec in task.get("_maps", []):
        left, col = spec.split("=", 1)
        nid, inp = left.split(".", 1)
        if task.get(col):
            wf[nid]["inputs"][inp] = task[col]
    return wf, seed


def apply_prefix(wf, m, prefix):
    for sid in m["savers"]:
        wf[sid]["inputs"]["filename_prefix"] = prefix


def wait_done(server, pid, timeout):
    t0 = time.time()
    delay = 0.6
    while True:
        if time.time() - t0 > timeout:
            raise TimeoutError("等待超过 %ss" % timeout)
        try:
            h = http_json(server + "/history/" + pid, timeout=30)
        except Exception:
            h = {}
        if pid in h:
            entry = h[pid]
            status = (entry.get("status") or {})
            if status.get("status_str") == "error":
                msgs = json.dumps(status.get("messages", []), ensure_ascii=False)[:600]
                raise RuntimeError("ComfyUI 执行出错: " + msgs)
            if status.get("completed") or entry.get("outputs"):
                return entry
        time.sleep(delay)
        delay = min(delay * 1.25, 3.0)


def save_outputs(server, entry, out_dir, prefix, timeout):
    saved = []
    for nid, out in (entry.get("outputs") or {}).items():
        for key, items in out.items():
            if not isinstance(items, list):
                continue
            for i, it in enumerate(items):
                if not isinstance(it, dict) or "filename" not in it:
                    continue
                q = urllib.parse.urlencode(
                    {"filename": it["filename"], "subfolder": it.get("subfolder", ""),
                     "type": it.get("type", "output")}
                )
                data = http_get_bytes("%s/view?%s" % (server, q), timeout=timeout)
                ext = Path(it["filename"]).suffix or ".bin"
                tag = "" if len(items) == 1 else "_%d" % (i + 1)
                dst = out_dir / (prefix + tag + ext)
                k = 2
                while dst.exists():
                    dst = out_dir / ("%s%s_%d%s" % (prefix, tag, k, ext))
                    k += 1
                dst.write_bytes(data)
                saved.append(dst.name)
    return saved


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(
        description="ComfyUI 批量执行器：文件夹素材 + 提示词 → 批量出图/出视频",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("--workflow", required=True, help="API 格式工作流 json（ComfyUI 里 工作流→导出(API)）")
    ap.add_argument("--tasks", help="任务表 csv")
    ap.add_argument("--folder", help="素材文件夹")
    ap.add_argument("--recursive", action="store_true", help="递归子文件夹")
    ap.add_argument("--kind", choices=["image", "video"], default="image", help="--folder 模式扫描的文件类型")
    ap.add_argument("--prompt", help="统一提示词（覆盖所有任务）")
    ap.add_argument("--prompt-from-txt", action="store_true", help="读素材同名 .txt 作为提示词")
    ap.add_argument("--out", help="输出目录（--inspect 时可省略）")
    ap.add_argument("--server", default="auto", help="ComfyUI 地址，默认自动探测 8188/8000")
    ap.add_argument("--comfy-root", help="ComfyUI 根目录（含 input/models），默认自动识别")
    ap.add_argument("--local-copy", action="store_true",
                    help="直接复制文件到 input 目录，而不是走 HTTP 上传")
    ap.add_argument("--map", action="append", default=[], metavar="节点.输入=列名",
                    help="手动指定注入，如 --map 12.text=prompt")
    ap.add_argument("--set", action="append", default=[], metavar="节点.输入=值",
                    help="固定改某个节点值，如 --set 3.denoise=0.75")
    ap.add_argument("--prefix", default=None, help="输出文件名前缀模板，可用 {stem} {idx}")
    ap.add_argument("--limit", type=int, help="只跑前 N 条")
    ap.add_argument("--retry", type=int, default=1, help="失败重试次数")
    ap.add_argument("--timeout", type=int, default=1800, help="单条超时秒数")
    ap.add_argument("--skip-done", action="store_true", default=True, help="断点续跑（默认开）")
    ap.add_argument("--no-skip-done", dest="skip_done", action="store_false")
    ap.add_argument("--inspect", action="store_true", help="只打印节点映射后退出")
    ap.add_argument("--dry-run", action="store_true", help="只解析不提交")
    ap.add_argument("--allow-empty-prompt", action="store_true")
    args = ap.parse_args()

    api = json.load(open(args.workflow, encoding="utf-8"))
    if "nodes" in api and isinstance(api["nodes"], list):
        raise SystemExit("这是界面格式工作流。请在 ComfyUI 里用 工作流 → 导出 (API) 重新导出。")
    m = detect_map(api)
    print_map(api, m)
    if args.inspect:
        return
    if not args.out:
        raise SystemExit("需要 --out 输出目录")

    comfy_root = None
    server = None if args.dry_run else pick_server(args.server)
    if server and not args.local_copy:
        print("服务地址:", server, "（素材走 HTTP 上传）")
    else:
        comfy_root = Path(args.comfy_root) if args.comfy_root else guess_comfy_root(args.workflow)
        if comfy_root:
            print("ComfyUI input 目录:", comfy_root / "input")
        elif not args.dry_run:
            raise SystemExit("找不到 input 目录，用 --comfy-root 指定")

    tasks = load_tasks(args)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    state_path = out_dir / "_state.json"
    state = json.load(open(state_path, encoding="utf-8")) if state_path.exists() else {}

    cache = {}
    ok = fail = skip = 0
    t_start = time.time()

    for i, task in enumerate(tasks, 1):
        src = task.get("source")
        key = hashlib.md5(
            ((src or "") + "|" + task.get("prompt", "")).encode("utf-8")
        ).hexdigest()
        stem = Path(src).stem if src else "gen%03d" % i
        prefix = (task.get("prefix") or args.prefix or "{stem}").format(stem=stem, idx="%03d" % i)

        head = "[%d/%d] %s" % (i, len(tasks), Path(src).name if src else prefix)
        if args.skip_done and key in state and any(
            (out_dir / f).exists() for f in state[key].get("outputs", [])
        ):
            print(head, "→ 已存在，跳过")
            skip += 1
            continue

        staged = None
        if src:
            if args.dry_run:
                sp = Path(src)
                staged = "_batch/%s_%s%s" % (
                    sp.stem[:40], hashlib.md5(str(sp.resolve()).encode("utf-8")).hexdigest()[:8],
                    sp.suffix.lower())
            else:
                staged = stage_file(src, comfy_root, cache, server, args.local_copy)

        wf, seed = patch(api, m, task, staged)
        apply_prefix(wf, m, prefix)
        for spec in args.set:
            left, val = spec.split("=", 1)
            nid, inp = left.split(".", 1)
            old = wf[nid]["inputs"].get(inp)
            try:
                wf[nid]["inputs"][inp] = type(old)(float(val)) if isinstance(old, (int, float)) else val
            except (TypeError, ValueError):
                wf[nid]["inputs"][inp] = val

        if args.dry_run:
            print(head, "→ 就绪 seed=%s prefix=%s loader=%s" % (seed, prefix, staged))
            print("     正向:", (task.get("prompt") or "")[:60])
            ok += 1
            continue

        for attempt in range(args.retry + 1):
            t0 = time.time()
            try:
                pid = http_json(server + "/prompt",
                                {"prompt": wf, "client_id": uuid.uuid4().hex})["prompt_id"]
                entry = wait_done(server, pid, args.timeout)
                files = save_outputs(server, entry, out_dir, prefix, args.timeout)
                state[key] = {"outputs": files, "seed": seed, "prompt": task.get("prompt", ""),
                              "source": src or "", "time": time.strftime("%Y-%m-%d %H:%M:%S")}
                json.dump(state, open(state_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                print(head, "→ 完成 %.1fs  %s" % (time.time() - t0, ", ".join(files)))
                ok += 1
                break
            except Exception as e:
                msg = str(e).replace("\n", " ")[:300]
                if attempt < args.retry:
                    print(head, "→ 失败(第%s次) %s，重试…" % (attempt + 1, msg))
                    time.sleep(3)
                else:
                    print(head, "→ 失败 %s" % msg)
                    fail += 1

    dur = time.time() - t_start
    print("\n完成 %d 条，失败 %d 条，跳过 %d 条，用时 %.1f 分钟" % (ok, fail, skip, dur / 60))
    print("输出目录:", out_dir)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
