# 盖亚-skill（Gaia Skills）

> 11 个面向中国用户的实用 AI 技能合集 —— 每个都能独立安装、独立使用，覆盖内容创作、研究、电商、法律、办公自动化、智能家居、金融投资等高频场景。

**品牌定位**：不追热点、不堆参数，只做「装上就能干活」的国产场景技能。全部开源（MIT）、零第三方依赖（纯 Python 标准库优先）、可离线回退。

---

## 技能清单（11 个）

| # | 技能 | slug | 一句话说明 |
|---|------|------|-----------|
| 1 | GEO 生成式引擎优化 | `geo-cn` | 让内容被豆包/DeepSeek/Kimi/元宝等 18 个国产 AI 引擎检索、引用、归因 |
| 2 | 录制成 skill | `skill-forge` | 元工具：把「口述/粘贴的操作流程」一键结构化生成标准 SKILL.md |
| 3 | 视频字幕配音 | `video-subtitle` | 视频→字幕（whisper）+ 字幕→中文配音（say/edge-tts）双向闭环 |
| 4 | 中文深度研究 | `cn-shendu-research` | 多源检索 + 信源四级分级 + 反幻觉审计，产出可溯源中文报告 |
| 5 | 小红书内容工厂 | `xhs-neirong-gongchang` | 选题→标题四公式→文案→五类违禁词自检，一站式产出 |
| 6 | 中文 TTS | `cn-tts` | edge-tts 14 种中文音色（含粤语/台腔/东北/陕西口音）+ say 离线回退 |
| 7 | 中文 OCR | `cn-ocr` | tesseract 中文识别，输出纯文本或带坐标+置信度的结构化 JSON |
| 8 | 跨境 Listing | `cross-border-listing` | 中文产品信息→亚马逊合规 Listing（标题/五点/关键词 + 违禁词自检） |
| 9 | 法律合同审查 | `contract-review` | 三级风险词库扫描（单方解除/无限责任/免责等）+ 白话修改建议 |
| 10 | 智能家居 MCP | `smart-home-mcp` | Home Assistant 自然语言控制（Bearer 认证，凭据零落盘） |
| 11 | 黄金盘前分析 | `gold-premarket` | 一键盘前简报：行情+技术位+宏观快讯+三档持仓决策，配置型研判不喊单 |

---

## 快速安装

全部技能已上架 SkillHub（腾讯云 AI Skills 社区）。以 `geo-cn` 为例：

```bash
skillhub install geo-cn --namespace user_d8f19d50
```

> 也可在 SkillHub 网页（skillhub.cn）搜索技能名直接安装。每个技能的 `skills/<slug>/SKILL.md` 里有完整的使用说明与边界红线。

---

## 目录结构

```
gaia-skill/
├── LICENSE                  # MIT
├── README.md                # 本文件
└── skills/
    ├── geo-cn/              # ① GEO 优化
    ├── skill-forge/         # ② 录制成 skill
    ├── video-subtitle/      # ③ 视频字幕配音
    ├── cn-shendu-research/  # ④ 中文深度研究
    ├── xhs-neirong-gongchang/  # ⑤ 小红书
    ├── cn-tts/              # ⑥ 中文 TTS
    ├── cn-ocr/              # ⑦ 中文 OCR
    ├── cross-border-listing/ # ⑧ 跨境 Listing
    ├── contract-review/     # ⑨ 法律合同审查
    ├── smart-home-mcp/      # ⑩ 智能家居 MCP
    └── gold-premarket/      # ⑪ 黄金盘前分析
```

每个技能目录自包含：`SKILL.md`（技能定义 + 使用说明）+ `README.md` + `bin/`（纯标准库脚本）+ 可选 `examples/`。

---

## 设计原则

1. **装上就能用**：脚本优先纯 Python 标准库，免 pip install；外部工具（ffmpeg/whisper/tesseract）缺失时给出明确的探测与安装提示。
2. **凭据零落盘**：需要认证的技能（智能家居 HA、视频字幕等）凭据一律走环境变量注入，包内不含任何密钥。
3. **合规内置**：小红书/亚马逊/合同审查等技能内建违禁词库与红线声明，从源头规避风险。
4. **边界诚实**：法律审查明确「非法律意见」，心理类内容明确「不替代专业诊疗」。

---

## 关于作者

苏格 —— 独立开发者，专注「国产场景 AI 技能」孵化。另有技能匹配器 [skill-matcher](https://github.com/axel286137079-dot/skill-matcher) 与心态成长专家 psych-mentor。

联系邮箱：43298568@qq.com

---

## License

[MIT](./LICENSE)
