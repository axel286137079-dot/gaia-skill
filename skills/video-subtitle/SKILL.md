---
name: video-subtitle
slug: video-subtitle
displayName: 视频字幕配音
summary: 本地将视频转写为 SRT 字幕，并可从字幕文本生成独立 AI 配音音频。
license: MIT
description: 视频转写和字幕配音。用 ffmpeg 提取音频、用本地 Whisper 生成 SRT，并可用 macOS say 或需联网的 edge-tts 将字幕文本生成独立配音文件。用于视频转文字、SRT 生成、字幕提取和 AI 旁白音频制作；不负责翻译、烧录字幕或合成最终视频。
version: 0.1.4
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/video-subtitle
category: 内容创作
tags: [视频字幕, 语音转文字, 字幕生成, 视频配音, whisper]
platforms: [workbuddy, claude-code, cursor]
---

# 视频字幕配音

本地一站式流水线：**视频 → 中文字幕（.srt）+ AI 配音**，无需 API key，数据不出本机。

## 何时使用

- 用户要给视频加字幕 / 提取字幕
- 用户要把视频里的语音转成文字稿
- 用户要给视频配 AI 旁白/配音

## 依赖（自动探测）

| 工具 | 用途 | 缺失时 |
|---|---|---|
| ffmpeg | 提取音频 | `brew install ffmpeg` |
| whisper | 语音转文字（中文 srt） | `pip install -U openai-whisper`；追求速度可换 `faster-whisper` 或 `brew install whisper-cpp`（v1.9.3） |
| say | macOS 配音（婷婷=普通话） | Windows/Linux 用 `--tts edge-tts` |

## 工作流

### 一键：视频 → 字幕 + 配音
```bash
python3 bin/subtitle.py all 视频.mp4 --voice Tingting
```
输出：`subtitle_out/视频.srt`（字幕）+ `subtitle_out/视频.aiff`（配音）。

### 只转字幕
```bash
python3 bin/subtitle.py transcribe 视频.mp4 --model small --language zh
```

### 只做配音（从已有字幕）
```bash
python3 bin/subtitle.py voice 字幕.srt --voice Tingting
```

## 参数速查

| 参数 | 说明 | 默认 |
|---|---|---|
| `--model` | whisper 模型，tiny→large 越大越准越慢 | small |
| `--language` | 语言（zh 中文 / en 英文） | zh |
| `--voice` | say 语音（Tingting=普通话/Meijia=台腔/Sinji=粤语） | Tingting |
| `--tts` | 配音引擎（say / edge-tts） | say |

## Whisper 生态版本校准（2026-09 核对）

同一个 Whisper 模型权重，换推理实现只影响速度/显存，**不影响识别准确度**——中文准不准取决于选多大的模型，与选哪个工具无关。

| 实现 | 语言/引擎 | 2026-09 版本 | 特点 |
|---|---|---|---|
| openai-whisper | Python/PyTorch | OSS v20250625 | 参考实现，最慢、显存最高；适合对标论文基准 |
| **faster-whisper** | Python/CTranslate2 | **v1.2.1**（已升级 Silero VAD v6） | 同权重、吞吐可达参考实现 4×；int8 量化省显存；**上游维护趋缓（2025-10 后无新版本，属稳定而非停滞）** |
| **whisper.cpp** | C/C++ (ggml) | **v1.9.3**（2026-08-20） | 无 Python 依赖、CPU/Metal 可跑，当前最活跃；ggml 同步至 v0.20.2 |
| whisperX | Python（faster-whisper + pyannote + wav2vec2） | v3.8.6（2026-06-26） | 唯一开箱提供**词级时间戳 + 说话人分离**，做访谈/会议字幕首选 |
| Whisper.net | C# | 1.9.1 | .NET 桌面应用嵌入（其内置 whisper.cpp 仅同步到 1.8.5，落后上游） |

> **v1.9.2 → v1.9.3 变化（2026-08-20）**：同步 ggml v0.20.2（Metal 新增 TQ2_0 量化支持、融合 CUDA/SYCL 内核、ARM/WASI 修复）；whisper.cpp 现已提供**官方 Debian 软件包**（`whisper.cpp` / `libwhisper-dev` / `libwhisper1`），不必再依赖非官方 Snap。注意：**2026 年没有任何新的 Whisper 基础模型**——large-v3 与 distil-large-v3 仍是最新权重，因此各实现的版本推进都属于运行时/工具链变化，不影响识别准确度。
>
> 另注：whisper.cpp 的 CLI 只接受 **16-bit WAV**，MP3/M4A 需先转码（`ffmpeg -i in.m4a -ar 16000 -ac 1 -c:a pcm_s16le out.wav`）；官方 quick start 用的可执行文件名为 `whisper-cli`（旧版叫 `main`）。

**模型选择要点（中文场景）**：
- 中文是多音字、连读、口音都重的语言，**tiny/base 转出来基本不能直接用**，建议 medium 起步、精度优先上 large。
- **large-v3-turbo**（809M 参数）接近 large 精度、速度约 8×，是性价比首选。
- 硬盘/内存吃紧时用 **large-v3 的 q5_0 量化版**（约 1.1 GiB），精度损失有限。
- 长时间任务可开 `vad_filter`（faster-whisper 内置 Silero VAD）过滤静音段，明显提速；whisper.cpp 近期版本也已内置原生 VAD。
- 量化版本不同体积差异很大，**别把「参数数」当「文件体积」**（medium 与 large-v3-turbo 的 q5_0 文件体积接近，不代表速度或准确度相同）。

> 本 skill 默认调用 `openai-whisper` 以保持零额外依赖。若你已自建 faster-whisper 环境，可在 `--tts`/转写参数外自行替换后端命令，输出格式保持 SRT 即可复用本 skill 的配音流程。

## 边界与红线

- whisper 转写可能有错别字（尤其口音/术语），生成后建议人工校对一遍再发布。
- Whisper 转写的字体和准确度受模型、语音和版本影响，需人工校对；如需简繁转换，使用独立后处理工具。
- 首次运行会下载模型（tiny≈72MB / small≈460MB / medium≈1.5GB / large≈2.9GB）；若报 SSL 证书错误，是代理环境证书问题，先手动 `PYTHONHTTPSVERIFY=0 whisper …` 下载一次。
- say 配音是 TTS 合成音，非真人音色，涉及商用/版权场景需自行确认合规。
- 处理大视频时 whisper 会占较多内存/CPU，建议先用 `--model base` 快速预览，再用 `small/medium` 出精稿。

## 参考

- OpenAI Whisper: https://github.com/openai/whisper
- FFmpeg: https://ffmpeg.org
