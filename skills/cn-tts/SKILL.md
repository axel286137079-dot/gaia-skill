---
name: cn-tts
slug: cn-tts
displayName: 中文语音合成
summary: 文本转自然中文语音（TTS）——14 种音色（普通话/粤语/台腔/东北/陕西口音），可调语速/音调/音量，支持批量合成，免 API key。
license: MIT
description: 中文文本转语音 TTS。使用需联网的 edge-tts 生成 MP3，或在 macOS 上用 say 离线生成 AIFF/WAV；支持 14 种 edge-tts 中文音色、语速/音调/音量参数和 JSON 批量任务。用于 AI 配音、朗读、课程旁白、有声书和批量语音合成。
version: 0.1.2
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/cn-tts
category: 内容创作
tags: [语音合成, TTS, AI配音, 中文语音, 有声书]
platforms: [workbuddy, claude-code, cursor]
---

# 中文语音合成

把中文文本转成**自然的语音音频**，支持 14 种音色 + 语速/音调/音量调节 + 批量合成，免 API key。

## 何时使用

- 用户要把文字转成语音 / 做 AI 配音 / 有声书 / 课程配音 / 视频旁白 / 播客
- 用户要批量把多段文本合成语音
- 用户要选不同音色（女声/男声/粤语/台腔/方言）

## 引擎（自动选择）

| 引擎 | 音色数 | 特点 | 依赖 |
|---|---|---|---|
| edge-tts（首选） | 14 种中文 | 免费、高质量、可调语速/音调/音量 | `pip install edge-tts`，需联网 |
| say（回退） | 3 种（普通话/台腔/粤语） | 本地离线，输出 AIFF/WAV | macOS 自带 |

## 工作流

### 单条合成
```bash
python3 bin/tts.py "你好，欢迎来到强大心态训练。" --voice 晓晓 --out 音频.mp3
```

### 从文件合成
```bash
python3 bin/tts.py --file 文本.txt --voice 云希 --rate +10% --out 音频.mp3
```

### 批量合成（JSON manifest）
```bash
python3 bin/tts.py batch manifest.json --out-dir 输出目录/
```

### 查看全部中文音色
```bash
python3 bin/tts.py list-voices
```

## 参数速查

| 参数 | 说明 | 默认 |
|---|---|---|
| `--voice` | 音色名（中文昵称或完整名） | 晓晓 |
| `--rate` | 语速，如 +10% / -20% | +0% |
| `--pitch` | 音调，如 +10Hz / -5Hz | +0Hz |
| `--volume` | 音量，如 +20% | +0% |
| `--engine` | 引擎（edge-tts / say） | 自动 |
| `--out` | 输出音频路径 | out.mp3 |

## 音色速查（14 种）

| 昵称 | 完整名 | 性别/风格 |
|---|---|---|
| 晓晓 | zh-CN-XiaoxiaoNeural | 女·温暖（默认） |
| 晓伊 | zh-CN-XiaoyiNeural | 女·活泼 |
| 云健 | zh-CN-YunjianNeural | 男·激情 |
| 云希 | zh-CN-YunxiNeural | 男·阳光 |
| 云夏 | zh-CN-YunxiaNeural | 男·可爱 |
| 云扬 | zh-CN-YunyangNeural | 男·专业新闻 |
| 晓北 | zh-CN-liaoning-XiaobeiNeural | 女·东北口音 |
| 晓妮 | zh-CN-shaanxi-XiaoniNeural | 女·陕西口音 |
| 曉佳 | zh-HK-HiuGaaiNeural | 女·粤语 |
| 曉曼 | zh-HK-HiuMaanNeural | 女·粤语 |
| 雲龍 | zh-HK-WanLungNeural | 男·粤语 |
| 曉臻 | zh-TW-HsiaoChenNeural | 女·台腔 |
| 曉雨 | zh-TW-HsiaoYuNeural | 女·台腔 |
| 雲哲 | zh-TW-YunJheNeural | 男·台腔 |

## 边界与红线

- edge-tts 免费用于个人/学习场景，**商用请确认 Azure 语音服务授权**（个人免费额度≠商用许可）。
- 只做「语音合成」，**不做声纹克隆**（涉及他人声音权/肖像权，合规风险高）。
- 生成内容不得用于诈骗、冒充他人、传播虚假信息。

## 参考

- edge-tts: https://github.com/rany2/edge-tts
- 音色表内置于 `bin/tts.py`。
