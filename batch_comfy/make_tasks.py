#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描素材目录，生成待填写的任务表 CSV（已有提示词的不会被覆盖）"""
import csv
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 素材目录默认在项目根下的 批量素材/，可用环境变量 BATCH_BASE 覆盖
BASE = Path(os.environ.get("BATCH_BASE") or (HERE.parent / "批量素材"))
IMG_DIR = BASE / "01_图片素材"
VID_DIR = BASE / "02_视频素材"
OUT = BASE / "任务表.csv"
OUT_VID = BASE / "任务表_视频.csv"

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VID_EXT = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}

for _d in (IMG_DIR, VID_DIR, BASE / "03_输出"):
    _d.mkdir(parents=True, exist_ok=True)

FIELDS = ["source", "prompt", "negative", "seed", "denoise", "prefix"]
DEFAULT_NEG = "模糊，低质量，水印，乱码，扭曲的肢体"


def load_existing(path):
    """读已存在的任务表，保留用户已经填过的提示词（兼容 GBK）"""
    if not path.exists():
        return {}
    old = {}
    try:
        raw = path.read_bytes()
        text = None
        for enc in ("utf-8-sig", "gb18030", "utf-16"):
            try:
                text = raw.decode(enc)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        if text is None:
            print("  旧任务表编码无法识别（将重新生成）")
            return {}
        import io
        for row in csv.DictReader(io.StringIO(text)):
            src = (row.get("source") or "").strip()
            if src:
                old[str(Path(src)).lower()] = row
    except Exception as e:
        print("  读取旧任务表失败（将重新生成）:", e)
    return old


def scan(folder, exts):
    if not folder.is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts)


def build(files, old, out_path, kind):
    rows = []
    kept = new = 0
    for i, p in enumerate(files, 1):
        key = str(p).lower()
        stem = p.stem[:30]
        if key in old:
            r = old[key]
            row = {
                "source": str(p),
                "prompt": (r.get("prompt") or "").strip(),
                "negative": (r.get("negative") or DEFAULT_NEG).strip(),
                "seed": (r.get("seed") or "").strip(),
                "denoise": (r.get("denoise") or ("0.6" if kind == "img" else "")).strip(),
                "prefix": (r.get("prefix") or stem).strip(),
            }
            if row["prompt"]:
                kept += 1
        else:
            row = {"source": str(p), "prompt": "", "negative": DEFAULT_NEG,
                   "seed": "", "denoise": "0.6" if kind == "img" else "", "prefix": stem}
            new += 1
        rows.append(row)

    if not rows:
        return 0, 0, 0
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows), kept, new


def main():
    print("扫描素材目录…\n")
    imgs = scan(IMG_DIR, IMG_EXT)
    vids = scan(VID_DIR, VID_EXT)

    print("图片素材: %d 个  (%s)" % (len(imgs), IMG_DIR))
    print("视频素材: %d 个  (%s)" % (len(vids), VID_DIR))

    if not imgs and not vids:
        print("\n两个文件夹都是空的。先把素材复制进去再运行本脚本。")
        print("图片放:", IMG_DIR)
        print("视频放:", VID_DIR)
        return

    if imgs:
        old = load_existing(OUT)
        n, kept, new = build(imgs, old, OUT, "img")
        print("\n[图片] 任务表已生成: %s" % OUT)
        print("       共 %d 行 | 已有提示词 %d 行 | 待填写 %d 行" % (n, kept, new))
        if new:
            print("       ⚠ 还有 %d 行 prompt 是空的，用 Excel/WPS 打开填上" % new)

    if vids:
        old = load_existing(OUT_VID)
        n, kept, new = build(vids, old, OUT_VID, "vid")
        print("\n[视频] 任务表已生成: %s" % OUT_VID)
        print("       共 %d 行 | 已有提示词 %d 行 | 待填写 %d 行" % (n, kept, new))

    print("\n下一步：用 WPS/Excel 打开任务表填 prompt 列，存成 CSV，")
    print("然后双击 batch_comfy 里的 开始批处理.bat")


if __name__ == "__main__":
    main()
    input("\n按回车键关闭…")
