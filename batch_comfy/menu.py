#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批处理菜单（bat 只负责拉起本文件，中文/路径都在这里，避开 cmd 编码坑）"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
RUNNER = str(HERE / "batch_runner.py")
# 素材目录默认在项目根下的 批量素材/，可用环境变量 BATCH_BASE 覆盖
BASE = Path(os.environ.get("BATCH_BASE") or (HERE.parent / "批量素材"))
OUT = BASE / "03_输出"
for _d in (BASE / "01_图片素材", BASE / "02_视频素材", OUT):
    _d.mkdir(parents=True, exist_ok=True)


def run(args):
    cmd = [PY, RUNNER] + args
    print()
    code = subprocess.call(cmd)
    print()
    if code != 0:
        input("有报错，按回车键关闭…")
    else:
        input("按回车键返回菜单…")


def menu_api():
    cfg_path = HERE / "api_config.json"
    table = str(BASE / "文生图任务表.csv")
    out = str(OUT / "API生成")
    while True:
        print("=" * 46)
        print("  API 并行生图（不占显存，速度快）")
        print("=" * 46)
        print("  配置:", cfg_path)
        print("  任务表:", table)
        print("  输出到:", out)
        print()
        print("  1. 先检查连接（生成一张测试图）")
        print("  2. 试跑 3 条（并发 5）")
        print("  3. 全量跑（并发 5）")
        print("  4. 全量跑（并发 10，更快但可能触发限流）")
        print("  5. 只看会跑哪些，不请求")
        print("  0. 退出")
        c = input("请输入数字: ").strip()
        base = ["--config", str(cfg_path), "--tasks", table, "--out", out]
        if c == "1":
            run_script("api_batch.py", ["--config", str(cfg_path), "--out", out, "--check"])
        elif c == "2":
            run_script("api_batch.py", base + ["--workers", "5", "--limit", "3"])
        elif c == "3":
            run_script("api_batch.py", base + ["--workers", "5"])
        elif c == "4":
            run_script("api_batch.py", base + ["--workers", "10"])
        elif c == "5":
            run_script("api_batch.py", base + ["--dry-run"])
        elif c == "0":
            return


def run_script(name, args):
    cmd = [PY, str(HERE / name)] + args
    print()
    code = subprocess.call(cmd)
    print()
    input("有报错，按回车键关闭…" if code != 0 else "按回车键返回菜单…")


def menu_t2i():
    table = str(BASE / "文生图任务表.csv")
    out = str(OUT / "文生图")
    wf = str(HERE / "workflows" / "z-image-t2i.api.json")
    while True:
        print("=" * 46)
        print("  文生图批量（纯提示词，不需要素材）")
        print("=" * 46)
        print("  任务表:", table)
        print("  输出到:", out)
        print()
        print("  1. 先试跑 3 条")
        print("  2. 全量跑")
        print("  3. 只看会跑哪些，不生成")
        print("  0. 退出")
        c = input("请输入数字: ").strip()
        if c == "1":
            run(["--workflow", wf, "--tasks", table, "--out", out, "--limit", "3"])
        elif c == "2":
            run(["--workflow", wf, "--tasks", table, "--out", out])
        elif c == "3":
            run(["--workflow", wf, "--tasks", table, "--out", out, "--dry-run"])
        elif c == "0":
            return


def menu_img():
    table = str(BASE / "任务表.csv")
    out = str(OUT)
    wf = str(HERE / "workflows" / "z-image-i2i.api.json")
    # 任务表不存在时自动生成一次
    if not Path(table).exists():
        print("还没生成过任务表，现在自动扫描素材文件夹…\n")
        make_tasks()
        if not Path(table).exists():
            print("\n还是没生成出来 —— 说明 01_图片素材 文件夹里没有图片。")
            print("先把图片放进:", BASE / "01_图片素材")
            print("然后再运行本菜单。")
            input("按回车键返回…")
            return
    while True:
        print("=" * 46)
        print("  图生图批量（素材 + 每张自己的提示词）")
        print("=" * 46)
        print("  任务表:", table)
        print("  输出到:", out)
        print()
        print("  1. 先试跑 3 张")
        print("  2. 全量跑")
        print("  3. 只看会跑哪些，不生成")
        print("  4. 重新生成任务表（扫描素材文件夹）")
        print("  0. 退出")
        c = input("请输入数字: ").strip()
        if c == "1":
            run(["--workflow", wf, "--tasks", table, "--out", out, "--limit", "3"])
        elif c == "2":
            run(["--workflow", wf, "--tasks", table, "--out", out])
        elif c == "3":
            run(["--workflow", wf, "--tasks", table, "--out", out, "--dry-run"])
        elif c == "4":
            make_tasks()
        elif c == "0":
            return


def menu_vid():
    table = str(BASE / "任务表_视频.csv")
    out = str(OUT / "视频")
    wf = str(HERE / "workflows" / "video.api.json")
    while True:
        print("=" * 46)
        print("  视频批量")
        print("=" * 46)
        print("  任务表:", table)
        print("  输出到:", out)
        print()
        print("  1. 全量跑（需要 workflows/video.api.json 已就绪）")
        print("  0. 退出")
        c = input("请输入数字: ").strip()
        if c == "1":
            if not Path(wf).exists():
                print("\n还没有视频工作流。请在 ComfyUI 里搭好 VHS 工作流，")
                print("用 工作流 → 导出(API) 存为:", wf)
                input("按回车键返回…")
                return
            run(["--workflow", wf, "--tasks", table, "--out", out, "--timeout", "3600"])
        elif c == "0":
            return


def make_tasks():
    print()
    subprocess.call([PY, str(HERE / "make_tasks.py")])


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "t2i":
        menu_t2i()
    elif mode == "img":
        menu_img()
    elif mode == "vid":
        menu_vid()
    else:
        while True:
            print("=" * 46)
            print("  ComfyUI 批量处理")
            print("=" * 46)
            print()
            print("  1. 文生图（纯提示词批量生成）")
            print("  2. 图生图（素材 + 提示词）")
            print("  3. 视频批量")
            print("  4. API 并行生图（需要先在 api_config.json 填 Key）")
            print("  0. 退出")
            c = input("请输入数字: ").strip()
            if c == "1":
                menu_t2i()
            elif c == "2":
                menu_img()
            elif c == "3":
                menu_vid()
            elif c == "4":
                menu_api()
            elif c == "0":
                return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
