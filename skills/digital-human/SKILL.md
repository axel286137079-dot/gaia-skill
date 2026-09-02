---
name: digital-human
slug: digital-human
displayName: AI 数字人口播
summary: 数字人口播方案向导：检查本机环境、比较候选工具、生成 TTS 音频并给出后续驱动与部署提示。
license: MIT
description: 数字人口播的环境检查、工具选型和半自动编排向导。它可检测常见硬件与命令、用 edge-tts 或 macOS say 生成配音，并打印第三方驱动和部署提示；不包含数字人模型、不执行换脸或声音克隆，也不保证第三方工具兼容性。适用于 AI 口播、虚拟主播、说话头像、声音克隆方案比较和部署准备。
version: 0.1.1
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/digital-human
category: 内容创作
tags: [数字人, AI口播, 数字人克隆, 换脸, 声音克隆, 虚拟主播]
platforms: [workbuddy, claude-code, cursor]
---

# AI 数字人口播

数字人口播通常由商业平台或开源模型完成形象驱动与唇形同步，效果与素材、模型、硬件和参数有关。

本 skill **不搬运大模型**，只做「懂行向导 + 半自动编排」：检测你的环境、给你选型建议、把「文案→配音」这一步在本机自动跑通、把「形象驱动→合成」这一步给你清晰的部署指引。推理交给商业平台或你已装的开源工具。

## 何时使用

- 用户想做数字人克隆口播 / AI 口播视频 / 虚拟主播
- 用户问「数字人怎么搞」「能不能克隆一个我做视频」
- 用户想给文案配 AI 配音，再驱动数字人形象
- 用户要做换脸 / 声音克隆 / 数字人直播

## 核心命令

```bash
# 1. 环境体检：看本机能跑什么、不能跑什么
python3 bin/digital_human.py doctor

# 2. 选型推荐：按场景给方案表
python3 bin/digital_human.py select --scene 口播

# 3. 半自动编排：文案 → 配音（本机可跑）→ 提示形象驱动 → 合成
python3 bin/digital_human.py pipeline --script 文案.txt --voice 晓晓

# 4. 部署指引：某开源方案的分步安装
python3 bin/digital_human.py deploy --tool sadtalker
```

## 关键结论（先看这个）

许多主流开源方案优先支持 NVIDIA CUDA；Mac 对具体方案的支持程度会随版本变化。开始前应以项目当前 README 和硬件要求为准。常见路径包括：

1. **商业云平台**：上手较快，但价格、授权和数据处理条款需在使用时核验。
2. **本地或云 GPU 的开源方案**：控制力更高，但需要部署、模型权重与兼容性排查。
3. **轻量 CPU/MPS 路径**：部分工具可能支持，但速度和功能通常受限。

## 技术选型速查

| 方案 | 类型 | 输入 | 门槛 | 许可 | Mac 本机 |
|---|---|---|---|---|---|
| HeyGem/Duix.Avatar | 形象+声音克隆 | 10 秒视频 | NVIDIA+32G RAM+130G 磁盘 | Apache-2.0 | ✗ |
| SadTalker | 单图说话头 | 图+音频 | ~4GB 显存 | MIT | ✗ |
| MuseTalk | 近实时唇同步 | 视频+音频 | 中高显存 | 腾讯 | ✗ |
| EchoMimic | 音频+姿态驱动 | 图+音频 | 中 | 蚂蚁 | ✗ |
| LivePortrait | 表情迁移 | 图/视频 | 中 | MIT(快手) | ✗ |
| Hallo/Hallo2 | 扩散说话头 | 图+音频 | 12-16GB 显存 | 复旦 | ✗ |
| Wav2Lip | 唇形替换 | 已有视频+音频 | 低(有CPU) | 非商用 | △慢 |
| LiveTalking | 实时流式 | WebRTC/RTMP | GPU | lipku | ✗ |
| GPT-SoVITS | 声音克隆 | 1 分钟音频 | 中高显存 | MIT | ✗ |
| CosyVoice | 声音克隆 | 3 秒音频 | 中 | 阿里 | ✗ |
| Edge-TTS | TTS 免克隆 | 文本 | 纯CPU免费 | 微软 | ✓ |
| macOS say | TTS 免克隆 | 文本 | macOS 自带 | macOS | ✓ |
| Pixelle-Video | 全自动成片 | 主题→成片 | 中 | 阿里 | ✗ |
| HeyGen | 商业克隆 | — | $29-299/月 | 商业 | ✓云 |
| 魔珐有言 | 商业 3D 数字人 | — | 付费 | 商业 | ✓云 |

## 参数速查

| 参数 | 说明 | 默认 |
|---|---|---|
| `doctor` | 无参，输出环境体检报告 | — |
| `select --scene` | 口播 / 换脸 / 声音克隆 / 直播 / 全自动短视频 | 口播 |
| `pipeline --script` | 文案文本文件（必填） | — |
| `pipeline --engine` | edge-tts / say | edge-tts |
| `pipeline --voice` | 音色（晓晓/云希/云健/曉臻…） | 晓晓 |
| `pipeline --driver` | 唇形驱动方案（sadtalker/wav2lip/heygem…），缺省自动提示 | 自动 |
| `deploy --tool` | heygem/sadtalker/musetalk/cosyvoice/gpt-sovits/wav2lip/pixelle-video/all | all |

## 边界与红线

- **本 skill 不做声纹克隆、不做换脸生成**——只编排和指引。涉及他人声音权/肖像权的克隆与换脸，务必获得本人授权，且不得用于诈骗、冒充、传播虚假信息。
- 开源方案的部署参数以各自官方 README 为准，本 skill 的 `deploy` 指引是「起步摘要」，环境差异可能导致步骤不同。
- edge-tts 免费用于个人/学习场景，**商用请确认微软/Azure 语音授权**。
- Wav2Lip 许可为非商用，商用需另行确认。
- 形象克隆效果受素材与实现影响很大，不承诺固定同步率或商业平台一定优于开源方案。

## 参考

- HeyGem: https://github.com/GuijiAI/HeyGem.ai
- SadTalker: https://github.com/OpenTalker/SadTalker
- MuseTalk: https://github.com/TMElyralab/MuseTalk
- CosyVoice: https://github.com/FunAudioLLM/CosyVoice
- GPT-SoVITS: https://github.com/RVC-Boss/GPT-SoVITS
- Pixelle-Video: https://github.com/alipay/Pixelle-Video
- 商业平台：HeyGen（heygen.com）/ 魔珐有言（mofa.com）
