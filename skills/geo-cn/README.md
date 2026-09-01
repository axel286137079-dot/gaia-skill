# GEO-CN · 懂国产引擎的生成式引擎优化

让内容被 **ChatGPT / DeepSeek / 豆包 / Kimi / 元宝 / 文心 / 通义** 检索、引用、归因——而不是只被搜索引擎收录。

## 一句话定位

海外 GEO 工具只懂 ChatGPT/Perplexity，本 skill 专攻**国产引擎的引用机制差异**，做「可引用度审计 + 分诊式改写 + 信源布局」。

## 为什么值得做

- 生成式引擎正在替代传统搜索：越来越多用户直接问 AI「该买什么 / 哪个好」，AI 的回答会「点名」某些品牌、产品、观点。
- **被 AI 引用 = 新形态的流量入口**，且目前几乎没有竞品覆盖国产引擎。
- Princeton 实证（KDD 2024）：只要给内容加「专家引言 + 数据 + 引用来源」，被 AI 引用概率可提升 **30~40%**，小站收益更可达 **+115%**。

## 目录结构

```
geo-cn/
├── SKILL.md                      # 路由逻辑 + GEO 九法 + 国产引擎矩阵
├── README.md                     # 本文件
├── bin/
│   ├── citability_audit.py       # 可引用度审计（9 法打分，纯标准库，离线可跑）
│   └── multi_engine_rewrite.py   # 多引擎分诊式改写（规则化 + 可选 LLM）
└── examples/
    ├── sample_bad.md             # 反例（0 分，含关键词堆砌）
    └── sample_good.md            # 正例（78 分，高可引用）
```

## 快速开始

### 1. 可引用度审计（无需任何 key）

```bash
python3 bin/citability_audit.py examples/sample_good.md
# 输出：总分 / 100 + 逐项缺失建议 + 等级（高/中/低可引用）
```

### 2. 分诊式改写

```bash
# 规则化改写（无 key，离线）
python3 bin/multi_engine_rewrite.py examples/sample_good.md --engine doubao

# LLM 改写（自动读本机 DEEPSEEK_API_KEY / OPENAI_API_KEY）
python3 bin/multi_engine_rewrite.py examples/sample_good.md --engine deepseek

# 指定 key 与自定义 OpenAI 兼容接口
python3 bin/multi_engine_rewrite.py 内容.md --engine kimi \
  --api-key sk-xxx --base-url https://api.deepseek.com/v1 --model deepseek-v4-flash
```

支持的引擎（18 个）：`doubao`豆包 / `deepseek` / `kimi` / `tongyi`通义千问Qwen / `wenxin`文心 / `hunyuan`腾讯混元 / `zhipu`智谱清言 / `xunfei`讯飞星火 / `minimax`MiniMax海螺 / `xiaomi`小米MiMo / `pangu`华为盘古 / `step`阶跃星辰 / `baichuan`百川 / `yi`零一万物 / `sensenova`商汤日日新 / `skywork`昆仑天工 / `metaso`秘塔AI搜索 / `nano360`360纳米搜索。

### 3. 竞品 AI 引用对比

无国产引擎开放 API，此流程用 WebSearch 模拟：检索「竞品品牌 + 关键词」，观察各引擎结果页中竞品是否被引用、以什么姿态被引用，输出对比表（见 SKILL.md 流程 C）。

## 核心方法论速览

| 方法 | 效果 | 中文实操 |
|---|---|---|
| 专家引言 | +30~41% | 开头放有出处的权威引言 |
| 数据植入 | +31~40% | 可验证的数字/百分比/年份 |
| 引用来源 | +27~42% | 文末列来源链接 + 机构名 |
| 流畅度 | +15~28% | 短句、衔接词 |
| 权威语气 | +10~18% | 肯定式结论 |
| 术语 | +8~18% | 准确专业名词 |
| 关键词堆砌 | **-8~10%（有害）** | **严禁** |

## 合规红线

- 严禁造假引用、编造数据、虚构引言——AI 会追溯来源，造假会反噬。
- 不做「保证被 AI 引用」的承诺，只做「提升概率」的工程。

## License

MIT
