---
name: cn-tts
slug: cn-tts
displayName: 中文语音合成
summary: 文本转自然中文语音（TTS）——14 种音色（普通话/粤语/台腔/东北/陕西口音），可调语速/音调/音量，支持批量合成，免 API key。
license: MIT
description: 中文文本转语音 TTS。使用需联网的 edge-tts（须 ≥7.2，旧版因微软接口验证变更会 403）生成 MP3，或在 macOS 上用 say 离线生成 AIFF/WAV；支持 14 种 edge-tts 中文音色、语速/音调/音量参数和 JSON 批量任务。用于 AI 配音、朗读、课程旁白、有声书和批量语音合成。含版本红线与 403 排障步骤、商用授权说明。
version: 0.1.3
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
| edge-tts（首选） | 14 种中文 | 免费、高质量、可调语速/音调/音量 | `pip install -U edge-tts`（**须 ≥7.2**），需联网 |
| say（回退） | 3 种（普通话/台腔/粤语） | 本地离线，输出 AIFF/WAV | macOS 自带 |

### ⚠️ 版本红线：必须 edge-tts ≥ 7.2

微软 2024-10 起对 Edge 朗读接口收紧了身份验证（`Sec-MS-GEC` 参数：Windows 文件时间戳向下取整到 300 秒 × 10⁷ 拼接固定 token 后做 SHA-256 取大写），**旧版本握手逻辑已失效**：

| 版本 | 状态 |
|---|---|
| ≤ 6.1.12 | ❌ 403，完全不可用 |
| 6.1.16 | ⚠️ 修复第一波握手问题，但**当前已被再次拒绝**（带第一代新验证参数仍 403） |
| 6.1.19 | ❌ 403（6.1 系列天花板，仍不可用） |
| **7.2.8** | ✅ 当前最新稳定版，正常合成 |

**排障顺序**：① 先 `pip install -U edge-tts` 升级（可解决绝大多数 403）；② 仍失败则检查系统时钟偏差——`Sec-MS-GEC` 依赖本机时间，偏差过大会导致暗号失准（7.x 已内置「握手失败时读服务端 Date 头自动校准并重试」）；③ 再考虑网络出口，可临时加 `--proxy http://127.0.0.1:7890` 绕行（治标，不解决握手参数问题）。

常见异常：`WebSocketError`（握手/连接异常）、`NoAudioReceived`（未收到音频，服务端偶发、本库不自动重试，需自行包重试循环）。

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

- **授权性质**：edge-tts 调用的是 Edge 浏览器「大声朗读」功能背后的接口，**微软未对其授予任何商用许可**（官方口径：该接口属于 Edge 浏览器功能，受 Microsoft 服务协议约束；浏览器朗读服务可用 ≠ 获得商用输出权）。个人/学习场景可用，**商用与高可用场景请改用正式授权渠道**（Azure Speech S0 付费层——注意免费层 F0 的产物**不带商用使用权**，微软 2026-03 已明确 F0 属评估/测试额度；或本地引擎 Kokoro-82M / Piper 等）。
- 该接口为逆向调用的内部端点，**无 SLA 保障**，微软可随时变更验证策略导致旧版本集体失效（2024-10、2026-10 各发生过一次）。请勿用于对可用性有承诺的生产链路。
- say 引擎产物仅供本地使用，商用同样需确认授权。
- 只做「语音合成」，**不做声纹克隆**（涉及他人声音权/肖像权，合规风险高；微软 Custom Neural Voice / Personal Voice 属单独受限功能，需本人书面同意与登记）。
- 生成内容不得用于诈骗、冒充他人、传播虚假信息。

## 参考

- edge-tts: https://github.com/rany2/edge-tts
- 音色表内置于 `bin/tts.py`。
- 商用替代（本地、明确许可）：Kokoro-82M（Apache-2.0，CPU 可跑）、Piper（MIT）。
