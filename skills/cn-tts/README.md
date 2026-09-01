# 中文语音合成

把中文文本转成**自然的语音音频**，14 种音色 + 语速/音调/音量调节 + 批量合成，免 API key。

## 一句话定位

短视频 / 有声书 / 课程配音需求巨大，但「好用、低成本、能接进 agent」的中文 TTS skill 稀缺；本 skill 用 edge-tts（免费高质量 14 音色）+ macOS say（本地离线）双引擎封装，一条命令搞定中文配音。

## 为什么值得做

- 中文是多音字 + 多声调语言，通用英文 TTS 效果差，中文音色（含粤语/台腔/东北/陕西口音）是刚需。
- 大厂 TTS 都要 API key + 按量计费；edge-tts 免费且音质接近大厂，是性价比最优解。
- 只做「合成」不做「声纹克隆」，合规风险低，适合内容生产链路长期复用。

## 快速开始

```bash
# 单条合成
python3 bin/tts.py "你好，欢迎来到强大心态训练。" --voice 晓晓 --out 音频.mp3

# 从文件合成
python3 bin/tts.py --file 文本.txt --voice 云希 --rate +10%

# 批量合成
python3 bin/tts.py batch examples/manifest.json --out-dir out/

# 查看全部音色
python3 bin/tts.py list-voices
```

## 音色速查（14 种）

普通话：晓晓(女温暖)/晓伊(女活泼)/云健(男激情)/云希(男阳光)/云夏(男可爱)/云扬(男专业)
方言口音：晓北(东北)/晓妮(陕西)
粤语：曉佳/曉曼/雲龍
台腔：曉臻/曉雨/雲哲

## 目录结构

```
cn-tts/
├── SKILL.md            # 路由逻辑 + 引擎说明 + 音色表 + 参数 + 红线
├── README.md           # 本文件
├── bin/tts.py          # 核心脚本（纯标准库编排，免 API key）
└── examples/manifest.json  # 批量合成示例
```

## 依赖

| 引擎 | 依赖 | 说明 |
|---|---|---|
| edge-tts（首选） | `pip install edge-tts` | 免费高质量，需联网 |
| say（回退） | macOS 自带 | 本地离线，3 音色 |

## 边界与红线

- edge-tts 免费用于个人/学习，**商用请确认 Azure 语音服务授权**。
- 只做合成，**不做声纹克隆**（涉及声音权/肖像权）。
- 不得用于诈骗、冒充他人、传播虚假信息。

## License

MIT
