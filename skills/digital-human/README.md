# AI 数字人口播（digital-human）

数字人口播通常由商业平台或开源模型完成形象驱动与唇形同步，实际效果取决于素材、模型、硬件和参数。

这个 skill **不搬运大模型、不做重 GPU 推理**，只做**「懂行向导 + 半自动编排」**：

1. **doctor** — 环境体检，探测本机显卡与已装方案，告诉你「能跑什么、不能跑什么」。
2. **select** — 选型推荐，按场景 + 硬件给开源本地 vs 商业云平台的方案表。
3. **pipeline** — 半自动编排，「文案 → 配音」本机自动跑通，「形象驱动 → 合成」给清晰指引。
4. **deploy** — 部署指引，打印 HeyGem / SadTalker / MuseTalk / CosyVoice / GPT-SoVITS 等分步安装。

纯标准库（argparse + subprocess + shutil + json + platform），零依赖、免 API key。

## 为什么不做重活

| 理由 | 说明 |
|---|---|
| 硬件绑定 | 很多方案优先支持 NVIDIA CUDA；Mac/CPU 支持以各项目当前文档为准 |
| 包体积 | 模型权重 GB 级，SkillHub 连 `.png` 都拒绝，塞权重直接判死 |
| 产品哲学 | 盖亚-skill 的护城河是「纯标准库、免安装、懂行」，不是搬运大模型 |

**关键结论**：本 skill 可靠覆盖的是环境检查与「文案→配音」。形象驱动需另选商业平台或开源工具，并在使用时核验兼容性、价格、授权和隐私条款。

## 快速上手

```bash
# 1. 看本机能跑什么
python3 bin/digital_human.py doctor

# 2. 按场景选型
python3 bin/digital_human.py select --scene 口播

# 3. 文案 → 配音（本机直接出 mp3）
python3 bin/digital_human.py pipeline --script 文案.txt --voice 晓晓

# 4. 想跑开源方案？看部署指引
python3 bin/digital_human.py deploy --tool sadtalker
```

## 目录结构

```
digital-human/
├── SKILL.md              # skill 定义（frontmatter + 使用说明）
├── README.md             # 本文件
└── bin/
    └── digital_human.py  # 核心脚本（doctor/select/pipeline/deploy 四子命令）
```

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

## 合规红线

- 本 skill 只编排和指引，**不生成声纹克隆、不生成换脸**。涉及他人声音权/肖像权须获得本人授权，不得用于诈骗、冒充、传播虚假信息。
- edge-tts 商用需确认微软/Azure 授权；Wav2Lip 为非商用许可。
- 开源方案部署参数以各自官方 README 为准，`deploy` 是起步摘要。
