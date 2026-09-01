---
name: video-subtitle
slug: video-subtitle
displayName: 视频字幕配音
summary: 本地一站式把视频转成带时间轴的中文字幕，并把字幕合成 AI 配音——无需 API key，离线可跑。
license: MIT
description: 视频字幕配音（本地一站式流水线）。把视频转成带时间轴的中文字幕（whisper 转写 .srt），并把字幕文本合成 AI 配音（macOS say / edge-tts）。纯标准库编排 ffmpeg + whisper + say，无需任何 API key，离线可跑。用于：给视频加字幕、把视频语音转成文字稿、给视频配 AI 旁白、做视频本地化/翻译字幕、短视频批量上字幕。触发词：视频字幕、加字幕、字幕提取、语音转文字、视频转文字、视频配音、AI 配音、字幕生成、srt、视频转写。联系邮箱：43298568@qq.com。
version: 0.1.1
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
- 用户要做视频翻译、本地化（先转字幕再翻译）

## 依赖（自动探测）

| 工具 | 用途 | 缺失时 |
|---|---|---|
| ffmpeg | 提取音频 | `brew install ffmpeg` |
| whisper | 语音转文字（中文 srt） | `pip install -U openai-whisper` |
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

## 边界与红线

- whisper 转写可能有错别字（尤其口音/术语），生成后建议人工校对一遍再发布。
- **whisper 中文默认输出繁体字**（如「歡迎」），需简体可后处理转换，或用 `--initial_prompt "以下是简体中文"` 引导。
- 首次运行会下载模型（tiny≈72MB / small≈460MB / medium≈1.5GB / large≈2.9GB）；若报 SSL 证书错误，是代理环境证书问题，先手动 `PYTHONHTTPSVERIFY=0 whisper …` 下载一次。
- say 配音是 TTS 合成音，非真人音色，涉及商用/版权场景需自行确认合规。
- 处理大视频时 whisper 会占较多内存/CPU，建议先用 `--model base` 快速预览，再用 `small/medium` 出精稿。

## 参考

- OpenAI Whisper: https://github.com/openai/whisper
- FFmpeg: https://ffmpeg.org
