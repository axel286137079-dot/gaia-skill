#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tts.py — 中文语音合成（文本转语音 TTS）

把中文文本转成自然语音，支持 14 种音色 + 语速/音调/音量调节 + 批量合成。
引擎自动选择：edge-tts（免费高质量，需联网）→ say（macOS 本地离线回退）。

纯标准库编排，无 API key。

用法：
    # 单条合成（指定音色）
    python3 tts.py "你好，欢迎来到强大心态训练。" --voice 晓晓 --out 音频.mp3

    # 从文件合成
    python3 tts.py --file 文本.txt --voice 云希 --rate +10%

    # 批量合成（JSON manifest）
    python3 tts.py batch manifest.json --out-dir out/

    # 查看全部中文音色
    python3 tts.py list-voices
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

# ---------------- 中文音色表 ----------------
# 昵称 → (edge-tts 完整名, 说明)；昵称可作 --voice 的快捷写法
VOICES = {
    "晓晓": ("zh-CN-XiaoxiaoNeural", "女·温暖"),
    "晓伊": ("zh-CN-XiaoyiNeural", "女·活泼"),
    "云健": ("zh-CN-YunjianNeural", "男·激情"),
    "云希": ("zh-CN-YunxiNeural", "男·阳光"),
    "云夏": ("zh-CN-YunxiaNeural", "男·可爱"),
    "云扬": ("zh-CN-YunyangNeural", "男·专业新闻"),
    "晓北": ("zh-CN-liaoning-XiaobeiNeural", "女·东北口音"),
    "晓妮": ("zh-CN-shaanxi-XiaoniNeural", "女·陕西口音"),
    "曉佳": ("zh-HK-HiuGaaiNeural", "女·粤语"),
    "曉曼": ("zh-HK-HiuMaanNeural", "女·粤语"),
    "雲龍": ("zh-HK-WanLungNeural", "男·粤语"),
    "曉臻": ("zh-TW-HsiaoChenNeural", "女·台腔"),
    "曉雨": ("zh-TW-HsiaoYuNeural", "女·台腔"),
    "雲哲": ("zh-TW-YunJheNeural", "男·台腔"),
}

# say 回退音色（macOS）
SAY_VOICES = {"婷婷": "Tingting", "美佳": "Meijia", "善怡": "Sinji"}

DEFAULT_PATHS = {
    "edge-tts": ["/Users/suge/.workbuddy/binaries/python/envs/default/bin/edge-tts",
                 "/opt/homebrew/bin/edge-tts", "/usr/local/bin/edge-tts"],
    "say": ["/usr/bin/say"],
}


def find_tool(name):
    p = shutil.which(name)
    if p:
        return p
    for cand in DEFAULT_PATHS.get(name, []):
        if os.path.exists(cand):
            return cand
    return None


def resolve_voice(name):
    """把昵称或完整名解析为 edge-tts 完整名；返回 None 表示不是 edge 音色。"""
    if name in VOICES:
        return VOICES[name][0]
    if name in [v[0] for v in VOICES.values()]:
        return name
    return None


def synth_edge_tts(text, voice, rate, pitch, volume, out):
    edge = find_tool("edge-tts")
    if not edge:
        print("✗ 未找到 edge-tts。安装：pip install edge-tts", file=sys.stderr)
        print("  或加 --engine say 用 macOS 本地 say 回退。", file=sys.stderr)
        return False
    cmd = [edge, "--voice", voice, "--text", text,
           "--rate=" + rate, "--pitch=" + pitch, "--volume=" + volume,
           "--write-media", out]
    subprocess.run(cmd, check=True)
    return True


def synth_say(text, voice, out):
    say = find_tool("say")
    if not say:
        print("✗ 未找到 say（仅 macOS）。", file=sys.stderr)
        return False
    v = SAY_VOICES.get(voice, voice)  # 允许直接传 Tingting 等
    cmd = [say, "-v", v, "-o", out]
    subprocess.run(cmd, input=text.encode("utf-8"), check=True)
    return True


def synth(text, voice, rate, pitch, volume, out, engine):
    """按引擎合成；engine='auto' 时优先 edge-tts，回退 say。"""
    edge_voice = resolve_voice(voice)
    if engine == "say":
        return synth_say(text, voice, out)
    if engine == "edge-tts" or (engine == "auto" and edge_voice):
        if synth_edge_tts(text, edge_voice, rate, pitch, volume, out):
            return True
        if engine == "edge-tts":
            return False
        print("  ↳ edge-tts 失败，回退 say …")
    return synth_say(text, voice, out)


def cmd_single(args):
    text = args.text
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    if not text or not text.strip():
        print("✗ 没有文本可合成。", file=sys.stderr)
        sys.exit(1)
    out = args.out or "out.mp3"
    print(f"  合成中（音色={args.voice}, 语速={args.rate}）→ {out}")
    ok = synth(text.strip(), args.voice, args.rate, args.pitch, args.volume,
               out, args.engine)
    if ok:
        print(f"✓ 音频已生成：{out}")


def cmd_batch(args):
    with open(args.manifest, "r", encoding="utf-8") as f:
        items = json.load(f)
    os.makedirs(args.out_dir, exist_ok=True)
    total = len(items)
    for i, item in enumerate(items, 1):
        text = item.get("text", "")
        voice = item.get("voice", "晓晓")
        rate = item.get("rate", "+0%")
        pitch = item.get("pitch", "+0Hz")
        volume = item.get("volume", "+0%")
        out = item.get("out") or os.path.join(args.out_dir, f"{i:04d}.mp3")
        if not os.path.isabs(out):
            out = os.path.join(args.out_dir, out)
        print(f"  [{i}/{total}] 合成 → {out}")
        try:
            synth(text.strip(), voice, rate, pitch, volume, out, args.engine)
        except Exception as e:
            print(f"  ✗ 第 {i} 条失败：{e}", file=sys.stderr)
    print(f"\n✓ 批量完成：{total} 条 → {args.out_dir}")


def cmd_list_voices(args):
    print("edge-tts 中文音色（14 种）：")
    print(f"{'昵称':<6}{'完整名':<36}{'风格'}")
    print("-" * 56)
    for nick, (full, desc) in VOICES.items():
        print(f"{nick:<6}{full:<36}{desc}")
    print("\nmacOS say 回退音色：")
    for nick, full in SAY_VOICES.items():
        print(f"  {nick} ({full})")


def _parse_synth_args(argv):
    """解析单条合成参数（text 可为位置参数，或用 --file）"""
    ap = argparse.ArgumentParser(description="单条合成：文本 → 语音", add_help=False)
    ap.add_argument("text", nargs="?", help="要合成的文本")
    ap.add_argument("--file", help="从文本文件读取")
    ap.add_argument("--voice", default="晓晓", help="音色（昵称或完整名）")
    ap.add_argument("--rate", default="+0%", help="语速，如 +10%")
    ap.add_argument("--pitch", default="+0Hz", help="音调，如 +10Hz")
    ap.add_argument("--volume", default="+0%", help="音量，如 +20%")
    ap.add_argument("--engine", default="auto", choices=["auto", "edge-tts", "say"])
    ap.add_argument("--out", default=None, help="输出音频路径")
    return ap.parse_args(argv)


def _parse_batch_args(argv):
    ap = argparse.ArgumentParser(description="批量合成", add_help=False)
    ap.add_argument("manifest", help="manifest.json：[{text,voice,rate,pitch,out}...]")
    ap.add_argument("--out-dir", default="tts_out", help="输出目录")
    ap.add_argument("--engine", default="auto", choices=["auto", "edge-tts", "say"])
    return ap.parse_args(argv)


def print_usage():
    print("""中文语音合成 tts.py

用法：
  python3 tts.py "要合成的文本" [--voice 晓晓] [--rate +10%] [--out 音频.mp3]
  python3 tts.py --file 文本.txt [--voice 云希] [--engine say]
  python3 tts.py batch manifest.json [--out-dir out/]
  python3 tts.py list-voices

参数：--voice 音色 / --rate 语速 / --pitch 音调 / --volume 音量 / --engine 引擎 / --out 输出""")


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print_usage()
        return
    cmd = argv[0]
    if cmd == "list-voices":
        cmd_list_voices(None)
        return
    if cmd == "batch":
        args = _parse_batch_args(argv[1:])
        cmd_batch(args)
        return
    if cmd == "synth":
        argv = argv[1:]
    args = _parse_synth_args(argv)
    cmd_single(args)


if __name__ == "__main__":
    main()
