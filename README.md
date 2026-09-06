# 盖亚-skill（Gaia Skills）

> 21 个面向中国用户的实用 AI 技能合集 —— 每个都能独立安装、独立使用，覆盖内容创作、研究、电商、法律、办公自动化、智能家居、金融投资等高频场景。

**品牌定位**：不追热点、不堆参数，只做「装上就能干活」的国产场景技能。全部开源（MIT）、零第三方依赖（纯 Python 标准库优先）、可离线回退。

---

## 技能清单（21 个）

| # | 技能 | slug | 一句话说明 |
|---|------|------|-----------|
| 1 | GEO 生成式引擎优化 | `geo-cn` | 启发式可引用友好度审计与多引擎改写建议 |
| 2 | 录制成 skill | `skill-forge` | 元工具：把「口述/粘贴的操作流程」一键结构化生成标准 SKILL.md |
| 3 | 视频字幕配音 | `video-subtitle` | 视频→字幕（whisper）+ 字幕→中文配音（say/edge-tts）双向闭环 |
| 4 | 中文深度研究 | `cn-shendu-research` | 多源检索 + 信源四级分级 + 反幻觉审计，产出可溯源中文报告 |
| 5 | 小红书内容工厂 | `xhs-neirong-gongchang` | 选题→标题四公式→文案→五类违禁词自检，一站式产出 |
| 6 | 中文 TTS | `cn-tts` | edge-tts 14 种中文音色（含粤语/台腔/东北/陕西口音）+ say 离线回退 |
| 7 | 中文 OCR | `cn-ocr` | tesseract 中文识别，输出纯文本或带坐标+置信度的结构化 JSON |
| 8 | 跨境 Listing | `cross-border-listing` | Listing 草稿与常见违禁词、长度和格式检查 |
| 9 | 法律合同审查 | `contract-review` | 三级风险词库扫描（单方解除/无限责任/免责等）+ 白话修改建议 |
| 10 | 智能家居控制 | `smart-home-mcp` | Home Assistant REST CLI，写操作默认 dry-run，高风险操作二次确认 |
| 11 | 黄金盘前分析 | `gold-premarket` | 公开行情、指标与快讯的研究简报骨架，不构成投资建议 |
| 12 | AI 数字人口播 | `digital-human` | 环境检查、方案选型、TTS 配音与第三方部署向导 |
| 13 | 服务报价与增项测算 | `suge-service-quote-guard` | 工时×内部成本的毛利反算报价，客户版隐藏底价与利润 |
| 14 | 供应商报价归一比价 | `suge-supplier-quote-compare` | 起订量/税运费/交期归一后的总支出比价，未知运费不按 0 |
| 15 | 客户反馈证据简报 | `suge-feedback-evidence-brief` | 带证据 ID 与样本分母的反馈主题简报与改进行动清单 |
| 16 | Pay Skill 盈亏与定价守门 | `suge-pay-skill-margin-guard` | 三价核对、毛利/盈亏平衡/目标售价三情景测算，缺失成本不默认为 0 |
| 17 | Skill 发布安全审计 | `suge-skill-release-security-audit` | 只读静态门禁：密钥/路径穿越/危险命令/引用完整性，PASS/FAIL/REVIEW |
| 18 | A2M 上线证据验收 | `suge-a2m-release-evidence-checker` | 离线逐跳核对 402→证明→交付→验付→履约，区分沙箱与生产证据 |
| 19 | SaaS 席位与续费审计 | `suge-saas-seat-renewal-audit` | 闲置席位与可削减候选、取消截止日与 30/60/90 天续费日历，不代取消或降配 |
| 20 | 达人合作交付与结算核对 | `suge-creator-campaign-settlement-reconcile` | 净额佣金、退款暂缓、交付/发票缺口与疑似多付的结算核对表 |
| 21 | 商用素材授权台账 | `suge-commercial-asset-license-ledger` | 凭证/渠道/地域/期限/署名台账，BLOCK/REVIEW/PASS 与到期提醒 |

---

## 快速安装

除新增技能在首次发布前可能尚未可见外，其余技能已上架 SkillHub。以 `geo-cn` 为例：

```bash
skillhub install geo-cn --namespace user_d8f19d50
```

> 也可在 SkillHub 网页（skillhub.cn）搜索技能名直接安装。每个技能的 `skills/<slug>/SKILL.md` 里有完整的使用说明与边界红线。

---

## MCP 版（黄金盘前分析）

「黄金盘前分析」额外提供标准 **MCP Server** 版（`mcp/gold-premarket-mcp/`），可接入 Claude Desktop / Cursor / WorkBuddy 等任意 MCP 客户端，直接调用实时行情、技术指标、快讯与一键盘前简报（5 个工具：`get_quote` / `get_kline` / `get_indicators` / `get_news` / `generate_report`）。详见 [`mcp/gold-premarket-mcp/README.md`](mcp/gold-premarket-mcp/README.md)。

---

## 目录结构

```
gaia-skill/
├── LICENSE                  # MIT
├── README.md                # 本文件
├── skills/
│   ├── geo-cn/              # ① GEO 优化
│   ├── skill-forge/         # ② 录制成 skill
│   ├── video-subtitle/      # ③ 视频字幕配音
│   ├── cn-shendu-research/  # ④ 中文深度研究
│   ├── xhs-neirong-gongchang/  # ⑤ 小红书
│   ├── cn-tts/              # ⑥ 中文 TTS
│   ├── cn-ocr/              # ⑦ 中文 OCR
│   ├── cross-border-listing/ # ⑧ 跨境 Listing
│   ├── contract-review/     # ⑨ 法律合同审查
│   ├── smart-home-mcp/      # ⑩ 智能家居 MCP
│   ├── gold-premarket/      # ⑪ 黄金盘前分析
│   └── digital-human/       # ⑫ AI 数字人口播
├── scripts/                 # 校验与 Codex 严格包构建
├── tests/                   # 离线单元测试
└── mcp/
    └── gold-premarket-mcp/  # 黄金盘前分析 MCP Server 版
```

每个技能目录自包含：`SKILL.md`（技能定义 + 使用说明）+ `README.md` + `bin/`（纯标准库脚本）+ 可选 `examples/`。

---

## 设计原则

1. **装上就能用**：脚本优先纯 Python 标准库，免 pip install；外部工具（ffmpeg/whisper/tesseract）缺失时给出明确的探测与安装提示。
2. **凭据零落盘**：需要认证的技能（智能家居 HA、视频字幕等）凭据一律走环境变量注入，包内不含任何密钥。
3. **风险提示内置**：小红书/Listing/合同审查等技能内建启发式检查与红线声明，但不能替代平台审核或专业意见。
4. **边界诚实**：法律审查明确「非法律意见」，心理类内容明确「不替代专业诊疗」。

---

## 关于作者

苏格 —— 独立开发者，专注「国产场景 AI 技能」孵化。另有技能匹配器 [skill-matcher](https://github.com/axel286137079-dot/skill-matcher) 与心态成长专家 psych-mentor。

## License

[MIT](./LICENSE)
