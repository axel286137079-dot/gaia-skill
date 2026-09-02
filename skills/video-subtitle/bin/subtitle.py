#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
subtitle.py — 视频字幕配音（本地一站式流水线）

视频 → 字幕（whisper 转写 .srt）+ 字幕 → 配音（macOS say / edge-tts）。
纯标准库编排 ffmpeg + whisper + say，无 API key，离线可跑。

用法：
    # 一键：视频 → 中文字幕(srt) + 配音音频
    python3 subtitle.py all 视频.mp4

    # 只转字幕
    python3 subtitle.py transcribe 视频.mp4 --model small --language zh

    # 只做配音（从 srt 或纯文本）
    python3 subtitle.py voice 字幕.srt --voice Tingting

依赖（自动探测，缺失时给出安装提示）：
    - ffmpeg（视频/音频处理）
    - whisper（语音转文字，OpenAI Whisper）
    - say（macOS 自带 TTS）或 edge-tts（跨平台）
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

# ---------------- 工具探测 ----------------
TOOL_HINTS = {
    "ffmpeg": "brew install ffmpeg   （macOS）/ apt install ffmpeg（Linux）",
    "whisper": "pip install -U openai-whisper",
    "say": "macOS 自带；Windows/Linux 请改用 --tts edge-tts 并 pip install edge-tts",
}

DEFAULT_PATHS = {
    "ffmpeg": ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"],
    "whisper": ["/Library/Frameworks/Python.framework/Versions/3.10/bin/whisper",
                "/opt/homebrew/bin/whisper", "/usr/local/bin/whisper"],
    "say": ["/usr/bin/say"],
}

ZH_VOICES = ["Tingting", "Meijia", "Sinji"]  # 婷婷(普通话) / 美佳(台) / 善怡(粤)


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
        print(f"  安装：{TOOL_HINTS.get(name, '')}", file=sys.stderr)
        sys.exit(1)
    return p


# ---------------- 核心步骤 ----------------
def extract_audio(video, out_wav):
    """ffmpeg 提取 16k 单声道 wav（whisper 最佳输入）"""
    ffmpeg = require_tool("ffmpeg")
    cmd = [ffmpeg, "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000",
           "-acodec", "pcm_s16le", out_wav]
    print(f"  [1/3] 提取音频 → {out_wav}")
    subprocess.run(cmd, check=True, capture_output=True)


def transcribe(audio, output_dir, model, language, output_stem=None):
    """whisper 转写为 srt 字幕"""
    whisper = require_tool("whisper")
    os.makedirs(output_dir, exist_ok=True)
    cmd = [whisper, audio, "--model", model, "--language", language,
           "--output_format", "srt", "--output_dir", output_dir]
    print(f"  [2/3] whisper 转写（model={model}, lang={language}）…")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        print("\n✗ whisper 转写失败。常见原因：", file=sys.stderr)
        print("  1) 首次运行需下载模型（tiny≈72MB / small≈460MB / medium≈1.5GB / large≈2.9GB），", file=sys.stderr)
        print("     若报 SSL/CERTIFICATE 证书错误，是代理环境证书问题，可先手动下载：", file=sys.stderr)
        print("     PYTHONHTTPSVERIFY=0 whisper 音频.wav --model small --language zh --output_format srt", file=sys.stderr)
        print("     或 export SSL_CERT_FILE=/opt/homebrew/etc/ca-certificates/cert.pem", file=sys.stderr)
        print("  2) 视频可能无声/音频过短，导致转写无内容。", file=sys.stderr)
        return None
    generated = os.path.join(output_dir, os.path.splitext(os.path.basename(audio))[0] + ".srt")
    if not os.path.exists(generated):
        print(f"✗ whisper 未生成预期字幕：{generated}", file=sys.stderr)
        return None
    if output_stem:
        wanted = os.path.join(output_dir, output_stem + ".srt")
        if os.path.abspath(generated) != os.path.abspath(wanted):
            os.replace(generated, wanted)
        generated = wanted
    return generated


def parse_srt_text(srt_path):
    """解析 srt，提取纯文本（去序号/时间轴/空行）"""
    text_lines = []
    with open(srt_path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if re.match(r'^\d+$', s):            # 序号
                continue
            if re.match(r'^\d{2}:\d{2}:\d{2}', s):  # 时间轴
                continue
            text_lines.append(s)
    return "\n".join(text_lines)


def srt_to_voice(srt_path, out_audio, voice, tts):
    """字幕文本 → 配音音频"""
    text = parse_srt_text(srt_path)
    if not text.strip():
        print("✗ 字幕为空，跳过配音。", file=sys.stderr)
        return False
    if tts == "say":
        say = require_tool("say")
        # 注意：--data-format=LEI16 与 .aiff 扩展名冲突（aiff 需大端），
        # 直接让 say 按输出扩展名选默认格式（.aiff→AIFF，.wav→WAVE）。
        cmd = [say, "-v", voice, "-o", out_audio]
        print(f"  [3/3] say 配音（{voice}）→ {out_audio}")
        subprocess.run(cmd, input=text.encode("utf-8"), check=True)
    else:  # edge-tts
        try:
            import edge_tts
        except ImportError:
            print("✗ 未安装 edge-tts：pip install edge-tts", file=sys.stderr)
            return None
        import asyncio
        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(out_audio)
        print(f"  [3/3] edge-tts 配音 → {out_audio}")
        asyncio.run(_run())
    return out_audio


# ---------------- 子命令 ----------------
def cmd_transcribe(args):
    base = os.path.splitext(os.path.basename(args.video))[0]
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "audio.wav")
        extract_audio(args.video, wav)
        srt = transcribe(wav, args.out, args.model, args.language, base)
        if not srt:
            sys.exit(1)
    print(f"\n✓ 字幕已生成：{srt}")


def cmd_voice(args):
    default_ext = ".aiff" if args.tts == "say" else ".mp3"
    out_audio = args.out or os.path.splitext(args.srt)[0] + default_ext
    generated = srt_to_voice(args.srt, out_audio, args.voice, args.tts)
    if generated:
        print(f"\n✓ 配音已生成：{generated}")


def cmd_all(args):
    """一键：视频 → 字幕 + 配音"""
    os.makedirs(args.out, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.video))[0]
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "audio.wav")
        extract_audio(args.video, wav)
        srt = transcribe(wav, args.out, args.model, args.language, base)
        if not srt:
            sys.exit(1)
    if args.voice:
        ext = ".aiff" if args.tts == "say" else ".mp3"
        out_audio = os.path.join(args.out, base + ext)
        generated = srt_to_voice(srt, out_audio, args.voice, args.tts)
        if generated:
            print(f"\n✓ 字幕：{srt}")
            print(f"✓ 配音：{out_audio}")
    else:
        print(f"\n✓ 字幕：{srt}")


def main():
    ap = argparse.ArgumentParser(description="视频字幕配音：视频→字幕(srt) + 字幕→配音")
    sub = ap.add_subparsers(dest="cmd")

    p_all = sub.add_parser("all", help="一键：视频→字幕+配音")
    p_all.add_argument("video", help="视频文件")
    p_all.add_argument("--out", default="subtitle_out", help="输出目录")
    p_all.add_argument("--model", default="small", help="whisper 模型（tiny/base/small/medium/large）")
    p_all.add_argument("--language", default="zh", help="语言（zh/en…）")
    p_all.add_argument("--voice", default=None, help="say 音色或 edge-tts 完整音色名")
    p_all.add_argument("--tts", default="say", choices=["say", "edge-tts"], help="TTS 引擎")
    p_all.set_defaults(func=cmd_all)

    p_tr = sub.add_parser("transcribe", help="只转字幕")
    p_tr.add_argument("video", help="视频文件")
    p_tr.add_argument("--out", default="subtitle_out", help="输出目录")
    p_tr.add_argument("--model", default="small")
    p_tr.add_argument("--language", default="zh")
    p_tr.set_defaults(func=cmd_transcribe)

    p_vo = sub.add_parser("voice", help="只做配音（从 srt）")
    p_vo.add_argument("srt", help="字幕文件(.srt)")
    p_vo.add_argument("--out", default=None, help="输出音频路径")
    p_vo.add_argument("--voice", default=None)
    p_vo.add_argument("--tts", default="say", choices=["say", "edge-tts"])
    p_vo.set_defaults(func=cmd_voice)

    args = ap.parse_args()
    if not getattr(args, "cmd", None):
        ap.print_help()
        sys.exit(1)
    if hasattr(args, "tts") and not args.voice:
        args.voice = "Tingting" if args.tts == "say" else "zh-CN-XiaoxiaoNeural"
    args.func(args)


if __name__ == "__main__":
    main()
