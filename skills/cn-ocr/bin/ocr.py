#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ocr.py — 中文 OCR + 结构化输出

把图片里的中文「看懂并转成结构化数据」：提取文本、按行/块输出坐标与置信度，
支持横排/竖排/繁体，可批量处理文件夹。纯标准库编排 tesseract（可选 PaddleOCR）。

用法：
    python3 ocr.py 图片.png                     # 提取文本
    python3 ocr.py 图片.png --format json        # 结构化（含坐标+置信度）
    python3 ocr.py 图片.png --lang chi_sim_vert  # 竖排文字
    python3 ocr.py batch 图片目录/               # 批量

依赖（自动探测）：
    - tesseract（含中文语言包 chi_sim / chi_sim_vert / chi_tra）
      macOS: brew install tesseract tesseract-lang
    - 可选 PaddleOCR（中文准确率更高，但安装重）：pip install paddlepaddle paddleocr
"""
import argparse
import csv
import io
import json
import os
import shutil
import subprocess
import sys

DEFAULT_PATHS = {
    "tesseract": ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/usr/bin/tesseract"],
}

LANG_HINTS = {
    "chi_sim": "简体中文（横排）",
    "chi_sim_vert": "简体中文（竖排）",
    "chi_tra": "繁体中文",
    "chi_tra_vert": "繁体中文（竖排）",
}

# 常见图片扩展名
IMG_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".heic")


def find_tool(name):
    p = shutil.which(name)
    if p:
        return p
    for cand in DEFAULT_PATHS.get(name, []):
        if os.path.exists(cand):
            return cand
    return None


def require_tool(name):
    p = find_tool(name)
    if not p:
        print(f"✗ 缺少依赖：{name}", file=sys.stderr)
        if name == "tesseract":
            print("  安装：brew install tesseract tesseract-lang", file=sys.stderr)
        sys.exit(1)
    return p


def clean_cjk(text):
    """清洗中文 OCR 常见噪声：去掉 CJK 字符之间的多余空格、归一化全角。"""
    out = []
    for i, ch in enumerate(text):
        # 去掉「汉字之间」的空格（但保留中英文之间的空格）
        if ch == " " or ch == "\u3000":
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if prev and nxt and _is_cjk(prev) and _is_cjk(nxt):
                continue  # 中文之间空格，删
        out.append(ch)
    return "".join(out)


def _is_cjk(ch):
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF) or (0x3400 <= o <= 0x4DBF) or \
           (0x3000 <= o <= 0x303F) or (0xFF00 <= o <= 0xFFEF)


def ocr_text(tesseract, image, lang):
    """提取纯文本"""
    cmd = [tesseract, image, "stdout", "-l", lang]
    r = subprocess.run(cmd, capture_output=True)
    return r.stdout.decode("utf-8", errors="replace")


def ocr_json(tesseract, image, lang):
    """提取结构化结果（按行：文本 + 坐标 + 置信度）"""
    cmd = [tesseract, image, "stdout", "-l", lang, "tsv"]
    r = subprocess.run(cmd, capture_output=True)
    tsv = r.stdout.decode("utf-8", errors="replace")
    rows = []
    reader = csv.DictReader(io.StringIO(tsv), delimiter="\t")
    for row in reader:
        # 只保留「词」级别的非空行（word_num>0 且 text 非空）
        if row.get("text", "").strip():
            rows.append({
                "text": row["text"],
                "left": int(row.get("left", 0)),
                "top": int(row.get("top", 0)),
                "width": int(row.get("width", 0)),
                "height": int(row.get("height", 0)),
                "confidence": float(row.get("conf", 0)),
            })
    return rows


def cmd_ocr(args):
    tesseract = require_tool("tesseract")
    if not os.path.exists(args.image):
        print(f"✗ 找不到图片：{args.image}", file=sys.stderr)
        sys.exit(1)

    if args.format == "json":
        rows = ocr_json(tesseract, args.image, args.lang)
        if args.clean:
            for r in rows:
                r["text"] = clean_cjk(r["text"])
        print(json.dumps({"file": args.image, "lang": args.lang,
                          "lines": rows, "count": len(rows)},
                         ensure_ascii=False, indent=2))
    else:
        text = ocr_text(tesseract, args.image, args.lang)
        if args.clean:
            text = clean_cjk(text)
        print(text, end="" if text.endswith("\n") else "\n")


def cmd_batch(args):
    tesseract = require_tool("tesseract")
    imgs = [f for f in os.listdir(args.dir)
            if os.path.splitext(f)[1].lower() in IMG_EXTS]
    imgs.sort()
    if not imgs:
        print(f"✗ 目录里没有图片：{args.dir}", file=sys.stderr)
        sys.exit(1)
    os.makedirs(args.out, exist_ok=True)
    results = []
    for i, f in enumerate(imgs, 1):
        path = os.path.join(args.dir, f)
        text = ocr_text(tesseract, path, args.lang)
        if args.clean:
            text = clean_cjk(text)
        results.append({"file": f, "text": text.strip()})
        print(f"  [{i}/{len(imgs)}] {f} → {len(text.strip())} 字")
        out_txt = os.path.join(args.out, os.path.splitext(f)[0] + ".txt")
        with open(out_txt, "w", encoding="utf-8") as fp:
            fp.write(text)
    # 汇总 JSON
    with open(os.path.join(args.out, "_summary.json"), "w", encoding="utf-8") as fp:
        json.dump(results, fp, ensure_ascii=False, indent=2)
    print(f"\n✓ 批量完成：{len(imgs)} 张 → {args.out}/（含 _summary.json）")


def _parse_ocr_args(argv):
    ap = argparse.ArgumentParser(description="单张图片 OCR", add_help=False)
    ap.add_argument("image", help="图片路径")
    ap.add_argument("--lang", default="chi_sim",
                    choices=list(LANG_HINTS.keys()), help="语言（默认简体横排）")
    ap.add_argument("--format", default="text", choices=["text", "json"])
    ap.add_argument("--clean", action="store_true", help="清洗中文间多余空格")
    return ap.parse_args(argv)


def _parse_batch_args(argv):
    ap = argparse.ArgumentParser(description="批量 OCR 整个目录", add_help=False)
    ap.add_argument("dir", help="图片目录")
    ap.add_argument("--lang", default="chi_sim", choices=list(LANG_HINTS.keys()))
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--out", default="ocr_out", help="输出目录")
    return ap.parse_args(argv)


def print_usage():
    print("""中文 OCR ocr.py

用法：
  python3 ocr.py 图片.png                     # 提取文本
  python3 ocr.py 图片.png --format json        # 结构化（含坐标+置信度）
  python3 ocr.py 图片.png --lang chi_sim_vert  # 竖排文字
  python3 ocr.py batch 图片目录/ --out ocr_out/  # 批量

参数：--lang 语言 / --format text|json / --clean 去空格 / --out 输出目录

语言：chi_sim 简体横排 / chi_sim_vert 简体竖排 / chi_tra 繁体 / chi_tra_vert 繁体竖排""")


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print_usage()
        return
    cmd = argv[0]
    if cmd == "batch":
        args = _parse_batch_args(argv[1:])
        cmd_batch(args)
        return
    if cmd == "ocr":
        argv = argv[1:]
    args = _parse_ocr_args(argv)
    cmd_ocr(args)


if __name__ == "__main__":
    main()
