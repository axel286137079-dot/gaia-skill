#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
digital_human.py — AI 数字人口播工作流（轻向导 + 半自动编排）

不做重 GPU 推理，只做「懂行的向导 + 半自动编排」：
  1. doctor  环境体检 —— 探测本机 GPU / 已装开源方案，告诉你「能跑什么、不能跑什么」。
  2. select  选型推荐 —— 按场景（口播/换脸/声音克隆/直播/全自动成片）+ 硬件给推荐方案表。
  3. pipeline 半自动编排 —— 文案脚本 → TTS 配音（本机可跑）→ 提示/编排唇形驱动 → ffmpeg 合成。
  4. deploy  部署指引 —— 打印 HeyGem / SadTalker / MuseTalk / CosyVoice / GPT-SoVITS 分步安装。

设计原则：纯标准库（argparse + subprocess + shutil + json + platform），零依赖、免 API key。
重活（形象克隆/唇形驱动）交给用户已装的开源工具或商业云平台，本脚本只负责「检测 + 编排 + 指引」。

用法：
    python3 digital_human.py doctor
    python3 digital_human.py select --scene 口播
    python3 digital_human.py pipeline --script 文案.txt --voice 晓晓
    python3 digital_human.py deploy --tool sadtalker
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

# ============================================================
# 选型知识库（机器可读，人读版见 README.md）
# ============================================================

# 形象克隆 / 说话头驱动方案
AVATAR_SOLUTIONS = [
    {
        "id": "heygem",
        "name": "HeyGem / Duix.Avatar",
        "kind": "形象+声音克隆",
        "input": "10 秒视频克隆形象+声音",
        "req": "NVIDIA GPU + 32GB RAM + 130GB 磁盘（Docker）",
        "license": "Apache-2.0（硅基智能开源）",
        "scene": ["口播", "直播", "数字人克隆"],
        "runs_on_mac": False,
        "note": "开源里最接近 HeyGen 的一键克隆，但硬件门槛高、必须 NVIDIA。",
    },
    {
        "id": "sadtalker",
        "name": "SadTalker",
        "kind": "单图说话头",
        "input": "1 张人像图 + 音频",
        "req": "约 4GB 显存（CUDA）",
        "license": "MIT",
        "scene": ["口播", "图片开口"],
        "runs_on_mac": False,
        "note": "最经典、门槛最低的说话头方案，但依赖 CUDA，Mac 需折腾。",
    },
    {
        "id": "musetalk",
        "name": "MuseTalk",
        "kind": "近实时唇同步",
        "input": "视频 + 音频",
        "req": "中高显存（CUDA）",
        "license": "腾讯 TME",
        "scene": ["直播", "实时口播"],
        "runs_on_mac": False,
        "note": "近实时，适合直播/流式，但同样依赖 NVIDIA。",
    },
    {
        "id": "echomimic",
        "name": "EchoMimic",
        "kind": "音频+姿态驱动",
        "input": "图 + 音频",
        "req": "中等显存（CUDA）",
        "license": "蚂蚁（开源）",
        "scene": ["口播"],
        "runs_on_mac": False,
        "note": "蚂蚁出品，姿态+口型同步，口碑不错。",
    },
    {
        "id": "liveportrait",
        "name": "LivePortrait",
        "kind": "表情迁移",
        "input": "图 / 视频",
        "req": "中等（CUDA，有 CPU 模式但慢）",
        "license": "MIT（快手）",
        "scene": ["换脸", "表情迁移"],
        "runs_on_mac": False,
        "note": "快手开源，表情/头部迁移，驱动视频更自然。",
    },
    {
        "id": "hallo",
        "name": "Hallo / Hallo2",
        "kind": "扩散说话头",
        "input": "图 + 音频",
        "req": "12-16GB 显存",
        "license": "复旦（开源）",
        "scene": ["口播", "高质量成片"],
        "runs_on_mac": False,
        "note": "质量最高，但显存需求重，Mac 无望。",
    },
    {
        "id": "wav2lip",
        "name": "Wav2Lip",
        "kind": "唇形替换",
        "input": "已有视频 + 音频",
        "req": "低（有 CPU 模式，慢）",
        "license": "非商业许可（慎商用）",
        "scene": ["已有视频换口型"],
        "runs_on_mac": True,
        "note": "把已有视频的口型替换成新音频，CPU 可跑但慢，许可非商用。",
    },
    {
        "id": "livetalking",
        "name": "LiveTalking",
        "kind": "实时流式",
        "input": "WebRTC/RTMP/虚拟摄像头",
        "req": "GPU",
        "license": "lipku（开源）",
        "scene": ["直播", "实时交互"],
        "runs_on_mac": False,
        "note": "直播/流式场景的一站式框架。",
    },
]

# 声音克隆 / TTS 方案
VOICE_SOLUTIONS = [
    {
        "id": "edge-tts",
        "name": "Edge-TTS",
        "kind": "TTS（免克隆）",
        "input": "文本",
        "req": "纯 CPU，免费无 key，需联网",
        "license": "微软（个人免费）",
        "scene": ["配音", "口播旁白"],
        "runs_on_mac": True,
        "note": "首选：免费高质量、免 key、可调语速/音调，14 种中文音色。",
    },
    {
        "id": "say",
        "name": "macOS say",
        "kind": "TTS（免克隆）",
        "input": "文本",
        "req": "macOS 自带，离线",
        "license": "macOS",
        "scene": ["配音"],
        "runs_on_mac": True,
        "note": "本地离线兜底，音色有限（Tingting 普通话等）。",
    },
    {
        "id": "gpt-sovits",
        "name": "GPT-SoVITS",
        "kind": "声音克隆",
        "input": "1 分钟音频",
        "req": "中高显存（CUDA，CPU 可但慢）",
        "license": "开源（MIT）",
        "scene": ["声音克隆"],
        "runs_on_mac": False,
        "note": "中文社区口碑最好的声音克隆，1 分钟音频即可。",
    },
    {
        "id": "cosyvoice",
        "name": "CosyVoice",
        "kind": "声音克隆",
        "input": "3 秒音频",
        "req": "中（CUDA）",
        "license": "阿里（开源）",
        "scene": ["声音克隆"],
        "runs_on_mac": False,
        "note": "阿里出品，3 秒音频即可克隆，速度快。",
    },
]

# 全自动短视频引擎
AUTO_ENGINES = [
    {
        "id": "pixelle-video",
        "name": "Pixelle-Video",
        "kind": "全自动成片",
        "input": "主题 → 脚本 → 分镜 → 画面 → 配音 → 合成",
        "req": "中等 GPU",
        "license": "阿里（开源）",
        "scene": ["全自动短视频"],
        "runs_on_mac": False,
        "note": "输入一个主题，端到端出片，适合批量内容生产。",
    },
]

# 商业云平台（无需本地 GPU）
COMMERCIAL = [
    {
        "id": "heygen",
        "name": "HeyGen",
        "kind": "商业数字人克隆",
        "req": "$29-299/月，云上跑",
        "scene": ["口播", "数字人克隆", "快速出片"],
        "note": "商业数字人平台；价格、授权、隐私与效果需在使用时核验。",
    },
    {
        "id": "mofa",
        "name": "魔珐有言",
        "kind": "商业 3D 数字人",
        "req": "付费，云上跑",
        "scene": ["口播", "3D 数字人"],
        "note": "国内 3D 数字人平台，中文场景友好。",
    },
]

# 部署指引（按 tool id 检索）
DEPLOY_GUIDES = {
    "heygem": [
        "HeyGem / Duix.Avatar 部署（需 NVIDIA GPU 服务器或本机 NVIDIA 卡）：",
        "  1) 安装 Docker 与 NVIDIA Container Toolkit。",
        "  2) 克隆：git clone https://github.com/GuijiAI/HeyGem.ai.git",
        "  3) 按官方 README 一键 docker compose up，首次会拉取约 130GB 模型/镜像。",
        "  4) 浏览器打开 WebUI，上传 10 秒正脸视频即可克隆形象+声音。",
        "  硬门槛：NVIDIA GPU + 32GB 内存 + 130GB 磁盘；Mac(Apple Silicon) 跑不了，",
        "  建议租云 GPU（如 AutoDL / 阿里云 GPU 实例）或用商业平台 HeyGen。",
    ],
    "sadtalker": [
        "SadTalker 部署：",
        "  1) pip install torch torchvision（CUDA 版）",
        "  2) git clone https://github.com/OpenTalker/SadTalker && cd SadTalker",
        "  3) 下载权重：bash scripts/download_models.sh",
        "  4) 运行：python inference.py --driven_audio 配音.wav --source_image 人像.jpg --result_dir out/",
        "  约 4GB 显存；Mac 需 ROCm/MPS 折腾，成功率低，建议云 GPU 或商业平台。",
    ],
    "musetalk": [
        "MuseTalk 部署：",
        "  1) git clone https://github.com/TMElyralab/MuseTalk && cd MuseTalk",
        "  2) 按官方 README 配 conda 环境 + 下载权重。",
        "  3) 用于视频唇形同步/实时口播，依赖 NVIDIA GPU。",
    ],
    "cosyvoice": [
        "CosyVoice 部署：",
        "  1) git clone https://github.com/FunAudioLLM/CosyVoice && cd CosyVoice",
        "  2) 按官方 README 配环境 + 下载模型（约数 GB）。",
        "  3) 3 秒音频即可克隆音色，生成中文语音。",
    ],
    "gpt-sovits": [
        "GPT-SoVITS 部署：",
        "  1) git clone https://github.com/RVC-Boss/GPT-SoVITS && cd GPT-SoVITS",
        "  2) 按官方 README 下载整合包（Windows 一键包）或配 conda。",
        "  3) 上传 1 分钟干净人声即可训练克隆，支持中英日。",
    ],
    "wav2lip": [
        "Wav2Lip 部署（可在 Mac CPU 跑，但慢）：",
        "  1) git clone https://github.com/Rudrabha/Wav2Lip && cd Wav2Lip",
        "  2) pip install -r requirements.txt，下载 s3fd 预训练权重。",
        "  3) python inference.py --checkpoint_path wav2lip.pth --face 视频.mp4 --audio 配音.wav",
        "  注意：许可为非商用，商用需另行确认。",
    ],
    "pixelle-video": [
        "Pixelle-Video 部署：",
        "  1) git clone https://github.com/alipay/Pixelle-Video && cd Pixelle-Video",
        "  2) 按官方 README 配环境 + API key（内部调用 LLM/TTS）。",
        "  3) 输入主题，端到端生成脚本→分镜→画面→配音→合成。",
    ],
}


# ============================================================
# 工具探测
# ============================================================
def which(cmd):
    return shutil.which(cmd)


def run_quiet(args):
    try:
        subprocess.run(args, check=True, capture_output=True, timeout=10)
        return True
    except Exception:
        return False


def detect_gpu():
    """返回 (类型, 说明)。类型：nvidia / apple / cpu"""
    # NVIDIA
    if which("nvidia-smi") and run_quiet(["nvidia-smi"]):
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                                  "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            return "nvidia", "NVIDIA GPU：" + (out or "检测到")
        except Exception:
            return "nvidia", "NVIDIA GPU（nvidia-smi 可用）"
    # Apple Silicon
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64") and sys.platform == "darwin":
        return "apple", "Apple Silicon（" + machine + "）"
    if sys.platform == "darwin":
        return "apple", "macOS（Intel）"
    return "cpu", "未检测到 GPU（纯 CPU）"


def detect_installed_tools():
    """探测已装的开源方案命令/目录，返回 {id: bool}"""
    result = {}
    markers = {
        "heygem": ["heygem", "HeyGem"],
        "sadtalker": ["sadtalker"],
        "musetalk": ["musetalk"],
        "echomimic": ["echomimic"],
        "liveportrait": ["liveportrait"],
        "hallo": ["hallo"],
        "wav2lip": ["wav2lip"],
        "livetalking": ["livetalking"],
        "gpt-sovits": ["gpt-sovits", "GPT_SoVITS"],
        "cosyvoice": ["cosyvoice"],
        "edge-tts": ["edge-tts"],
        "pixelle-video": ["pixelle-video"],
    }
    for tid, names in markers.items():
        found = False
        for n in names:
            if which(n):
                found = True
                break
            # 兜底：查常见安装目录
            for base in (os.path.expanduser("~"), "/opt", "/usr/local"):
                if os.path.isdir(os.path.join(base, n)):
                    found = True
                    break
            if found:
                break
        result[tid] = found
    # edge-tts 用 Python 包判断
    if not result["edge-tts"]:
        try:
            import importlib.util
            if importlib.util.find_spec("edge_tts"):
                result["edge-tts"] = True
        except Exception:
            pass
    return result


# ============================================================
# 子命令：doctor 环境体检
# ============================================================
def cmd_doctor(args):
    gtype, gdesc = detect_gpu()
    print("=" * 60)
    print("AI 数字人口播 · 环境体检报告")
    print("=" * 60)
    print(f"  操作系统   : {platform.system()} {platform.release()}")
    print(f"  架构       : {platform.machine()}")
    print(f"  Python     : {platform.python_version()}")
    print(f"  显卡       : {gdesc}")
    print(f"  ffmpeg     : {'✓ 已装' if which('ffmpeg') else '✗ 未装（brew install ffmpeg）'}")
    print(f"  docker     : {'✓ 已装' if which('docker') else '✗ 未装'}")
    print(f"  git        : {'✓ 已装' if which('git') else '✗ 未装'}")
    print()

    installed = detect_installed_tools()
    print("已装开源方案探测：")
    any_installed = False
    for tid, found in installed.items():
        mark = "✓" if found else "—"
        if found:
            any_installed = True
        print(f"  {mark} {tid}")
    print()

    # 结论
    print("结论与建议：")
    if gtype == "nvidia":
        print("  · 本机有 NVIDIA GPU：可本地跑 SadTalker / MuseTalk / HeyGem 等重方案。")
        print("  · 推荐：select --hardware nvidia 看选型，deploy --tool sadtalker 起步。")
    elif gtype == "apple":
        print("  · 本机是 Mac（Apple Silicon/Intel），无 NVIDIA CUDA。")
        print("  · 很多形象驱动方案优先支持 CUDA；Mac/CPU 兼容性需按项目当前文档核验。")
        print("  · 本 skill 可在本机做 TTS 配音；形象驱动兼容性请核验各项目当前文档。")
        print("  · 常见后续路径：")
        print("      1) 商业云平台：HeyGen / 魔珐有言（零门槛，付费，效果最接近视频号那种）。")
        print("      2) 租 NVIDIA 云 GPU：跑开源 HeyGem（成本低，需自己部署）。")
    else:
        print("  · 纯 CPU 环境：本 skill 可做 TTS；部分第三方工具可能支持 CPU，但通常较慢。")
    if not which("ffmpeg"):
        print("  · 注意：缺 ffmpeg，pipeline 合成会失败。")
    print()


# ============================================================
# 子命令：select 选型推荐
# ============================================================
SCENE_ALIAS = {
    "口播": "口播", "数字人": "数字人克隆", "克隆": "数字人克隆",
    "换脸": "换脸", "声音": "声音克隆", "声音克隆": "声音克隆",
    "直播": "直播", "实时": "直播", "全自动": "全自动短视频",
    "成片": "全自动短视频", "短视频": "全自动短视频",
}


def cmd_select(args):
    gtype, gdesc = detect_gpu()
    scene = SCENE_ALIAS.get(args.scene, args.scene)

    def hit(sol, scenes):
        return any(scene in s for s in scenes)

    print("=" * 60)
    print(f"选型推荐 · 场景={scene} · 硬件={gtype}")
    print("=" * 60)

    print("\n【形象克隆 / 说话头驱动】")
    for s in AVATAR_SOLUTIONS:
        if scene in s["scene"]:
            ok = "本机可跑" if (s["runs_on_mac"] and gtype == "apple") or gtype == "nvidia" else "需 NVIDIA/云"
            print(f"  · {s['name']}（{s['kind']}）")
            print(f"      输入：{s['input']}｜门槛：{s['req']}｜许可：{s['license']}｜[{ok}]")
            print(f"      {s['note']}")

    print("\n【声音克隆 / TTS】")
    for s in VOICE_SOLUTIONS:
        if scene in s["scene"] or scene == "声音克隆" or scene == "口播":
            ok = "本机可跑" if s["runs_on_mac"] else "需 GPU"
            print(f"  · {s['name']}（{s['kind']}）｜[{ok}]")
            print(f"      {s['note']}")

    if scene == "全自动短视频":
        print("\n【全自动成片引擎】")
        for s in AUTO_ENGINES:
            print(f"  · {s['name']}（{s['kind']}）")
            print(f"      {s['note']}")

    print("\n【商业云平台（零门槛，付费）】")
    for s in COMMERCIAL:
        if scene in s["scene"] or scene in ("口播", "数字人克隆"):
            print(f"  · {s['name']}（{s['kind']}）｜{s['req']}")
            print(f"      {s['note']}")

    print("\n推荐结论：")
    if gtype == "apple":
        print("  Mac 本机 → 可先做「文案→配音」（pipeline 命令）。")
        print("  形象驱动 → 核验商业平台条款，或检查开源项目当前的 Mac/MPS 支持；")
        print("  需要 CUDA → 可评估 NVIDIA 云 GPU 与部署成本。")
    elif gtype == "nvidia":
        print("  有 NVIDIA → 开源一条龙：GPT-SoVITS 克隆声音 + SadTalker/HeyGem 驱动形象。")
    else:
        print("  纯 CPU → 本 skill 可做配音；第三方形象驱动可能很慢或不受支持。")
    print()


# ============================================================
# 子命令：pipeline 半自动编排（文案 → 配音 → 提示唇形驱动 → 合成）
# ============================================================
def _tts_to_audio(text, out_audio, engine, voice):
    """把文本合成为音频，返回 (成功, 音频路径)。复用 edge-tts / say。"""
    if engine == "say":
        if not which("say"):
            print("✗ macOS say 不可用（非 macOS）。", file=sys.stderr)
            return False, None
        print(f"  [1/2] macOS say 配音（{voice}）→ {out_audio}")
        try:
            subprocess.run(["say", "-v", voice, "-o", out_audio],
                           input=text.encode("utf-8"), check=True)
            return True, out_audio
        except Exception as e:
            print(f"✗ say 失败：{e}", file=sys.stderr)
            return False, None
    else:  # edge-tts
        try:
            import edge_tts
        except ImportError:
            print("✗ 未安装 edge-tts：pip install edge-tts", file=sys.stderr)
            return False, None
        import asyncio
        # 中文音色名 → edge-tts 完整名（内置常见几个）
        voice_map = {
            "晓晓": "zh-CN-XiaoxiaoNeural", "晓伊": "zh-CN-XiaoyiNeural",
            "云健": "zh-CN-YunjianNeural", "云希": "zh-CN-YunxiNeural",
            "云夏": "zh-CN-YunxiaNeural", "云扬": "zh-CN-YunyangNeural",
            "晓北": "zh-CN-liaoning-XiaobeiNeural", "晓妮": "zh-CN-shaanxi-XiaoniNeural",
            "曉佳": "zh-HK-HiuGaaiNeural", "雲龍": "zh-HK-WanLungNeural",
            "曉臻": "zh-TW-HsiaoChenNeural", "雲哲": "zh-TW-YunJheNeural",
        }
        v = voice_map.get(voice, "zh-CN-XiaoxiaoNeural")

        async def _run():
            communicate = edge_tts.Communicate(text, v)
            await communicate.save(out_audio)
        print(f"  [1/2] edge-tts 配音（{voice}）→ {out_audio}")
        try:
            asyncio.run(_run())
            return True, out_audio
        except Exception as e:
            print(f"✗ edge-tts 失败：{e}", file=sys.stderr)
            return False, None


def cmd_pipeline(args):
    gtype, _ = detect_gpu()
    script_path = args.script
    if not os.path.exists(script_path):
        print(f"✗ 文案文件不存在：{script_path}", file=sys.stderr)
        sys.exit(1)
    with open(script_path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        print("✗ 文案为空。", file=sys.stderr)
        sys.exit(1)

    os.makedirs(args.out, exist_ok=True)
    base = os.path.splitext(os.path.basename(script_path))[0]
    # 根据引擎决定输出扩展名与音色映射（say 不支持 mp3，且用英文音色名）
    if args.engine == "say":
        ext = ".aiff"
        say_voice_map = {"晓晓": "Tingting", "云希": "Alex", "云健": "Daniel",
                         "曉臻": "Meijia", "雲龍": "Sinji"}
        voice = say_voice_map.get(args.voice, "Tingting")
    else:
        ext = ".mp3"
        voice = args.voice
    out_audio = os.path.join(args.out, base + ext)

    print("=" * 60)
    print("数字人口播 · 半自动编排")
    print("=" * 60)
    print(f"  文案：{script_path}（{len(text)} 字）")
    print(f"  配音引擎：{args.engine}｜音色：{voice}")
    print(f"  显卡：{_}")

    # [1/2] 文案 → 配音（本机可跑）
    ok, audio = _tts_to_audio(text, out_audio, args.engine, voice)
    if not ok:
        sys.exit(1)

    # [2/2] 唇形驱动 / 形象口播（重活，检测 + 指引，不擅自跑）
    print("\n  [2/2] 形象口播（唇形驱动）")
    driver = args.driver
    if not driver:
        print("    未指定驱动方案（--driver）。")
    installed = detect_installed_tools()
    if driver and installed.get(driver):
        print(f"    检测到已装 {driver}，可执行驱动。")
        if driver == "sadtalker":
            print(f"    sadtalker 驱动示例（需人像图）：")
            print(f"      python inference.py --driven_audio {audio} --source_image 人像.jpg --result_dir out/")
        elif driver == "wav2lip":
            print(f"    wav2lip 驱动示例（需已有视频）：")
            print(f"      python inference.py --checkpoint_path wav2lip.pth --face 视频.mp4 --audio {audio}")
        elif driver == "heygem":
            print("    HeyGem：打开 WebUI 上传 10 秒视频克隆，再导入配音音频即可。")
        else:
            print(f"    请参考该方案官方 README，用配音文件 {audio} 驱动形象。")
    elif driver:
        print(f"    未检测到 {driver}，下面是部署指引：")
        for line in DEPLOY_GUIDES.get(driver, ["    无此方案指引，请查看官方 README。"]):
            print("    " + line)
    else:
        if gtype == "apple":
            print("    当前未检测到 NVIDIA CUDA，可考虑：")
            print(f"      1) 核验商业平台条款后，导入配音文件 {audio}；")
            print("      2) 租 NVIDIA 云 GPU 跑 HeyGem（deploy --tool heygem）。")
        else:
            print("    可用 --driver sadtalker / wav2lip / heygem 指定驱动方案。")

    # 合成提示（ffmpeg）
    print("\n  合成提示：把配音与形象视频合成，可用 ffmpeg：")
    print(f"    ffmpeg -i 形象视频.mp4 -i {audio} -c:v copy -c:a aac -shortest 成片.mp4")

    print("\n✓ 配音已生成：" + audio)
    print("  下一步：按上面指引完成形象驱动 + 合成。")
    print()


# ============================================================
# 子命令：deploy 部署指引
# ============================================================
def cmd_deploy(args):
    tool = args.tool
    if tool == "all":
        print("可用部署指引：" + "、".join(DEPLOY_GUIDES.keys()))
        print("用法：deploy --tool <名字>")
        return
    if tool not in DEPLOY_GUIDES:
        print(f"✗ 无「{tool}」的部署指引。可用：{list(DEPLOY_GUIDES.keys())}", file=sys.stderr)
        sys.exit(1)
    print("=" * 60)
    print(f"部署指引 · {tool}")
    print("=" * 60)
    for line in DEPLOY_GUIDES[tool]:
        print(line)
    print()


# ============================================================
# 主入口
# ============================================================
def main():
    ap = argparse.ArgumentParser(
        description="AI 数字人口播工作流（轻向导 + 半自动编排）")
    sub = ap.add_subparsers(dest="cmd")

    p_doc = sub.add_parser("doctor", help="环境体检：探测 GPU / 已装方案，给结论")
    p_doc.set_defaults(func=cmd_doctor)

    p_sel = sub.add_parser("select", help="选型推荐：按场景 + 硬件给方案表")
    p_sel.add_argument("--scene", default="口播",
                       help="场景：口播/换脸/声音克隆/直播/全自动短视频")
    p_sel.set_defaults(func=cmd_select)

    p_pipe = sub.add_parser("pipeline", help="半自动编排：文案→配音→(指引)唇形驱动→合成")
    p_pipe.add_argument("--script", required=True, help="文案文本文件")
    p_pipe.add_argument("--out", default="digital_human_out", help="输出目录")
    p_pipe.add_argument("--engine", default="edge-tts", choices=["edge-tts", "say"],
                        help="配音引擎")
    p_pipe.add_argument("--voice", default="晓晓", help="音色（晓晓/云希/云健…）")
    p_pipe.add_argument("--driver", default=None,
                        help="唇形驱动方案（sadtalker/wav2lip/heygem/musetalk…），缺省自动提示")
    p_pipe.set_defaults(func=cmd_pipeline)

    p_dep = sub.add_parser("deploy", help="部署指引：打印某开源方案的分步安装")
    p_dep.add_argument("--tool", default="all",
                       help="方案名（heygem/sadtalker/musetalk/cosyvoice/gpt-sovits/wav2lip/pixelle-video/all）")
    p_dep.set_defaults(func=cmd_deploy)

    args = ap.parse_args()
    if not getattr(args, "cmd", None):
        ap.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
