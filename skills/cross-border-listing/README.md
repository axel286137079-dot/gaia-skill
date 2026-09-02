# 跨境 Listing 优化

把中文商品信息整理为面向搜索的多语种 Listing 草稿，并做常见措辞、长度和格式检查。

## 一句话定位

本 skill 覆盖标题、五点、Search Terms 和多语种草稿，并在交稿前运行可配置的基础检查。

## 为什么值得做

- Listing 是跨境电商的「门面」，标题/五点的好坏直接决定点击率和转化率。
- 亚马逊违禁词（绝对化/夸大/健康宣称）是下架重灾区，多数卖家靠感觉自查，遗漏率高。
- 多语种 Listing 本地化是刚需，但「合规 + 关键词本地化」并重的工具稀缺。

## 快速开始

### 合规自查（核心工具）
```bash
python3 bin/listing_check.py 你的listing.md
# 输出：违禁词 + 标题长度/符号 + 全大写 检查

python3 bin/listing_check.py 你的listing.md --json --title-max 200 --bullet-max 500
```

### 示例
```bash
python3 bin/listing_check.py examples/sample_listing.md
```

## 覆盖的检查项

| 类别 | 典型 | 风险 |
|---|---|---|
| 绝对化/夸大 | best/#1/100%/perfect/ultimate | 违规下架 |
| 健康疗效宣称 | cure/treat/heal/anti-cancer/FDA approved | FDA 严查 |
| 促销/引流 | sale/free shipping/.com/官网链接 | 亚马逊禁 |
| 标题/五点规范 | 全大写/特殊符号/超过配置长度 | 可能违反目标类目规则 |

## 目录结构

```
cross-border-listing/
├── SKILL.md                 # 路由逻辑 + 标题/五点公式 + 合规红线
├── README.md                # 本文件
├── bin/listing_check.py     # 合规自查（纯标准库，离线可跑）
└── examples/sample_listing.md  # 示例（含演示用的违规词）
```

## 边界与红线

- 不生成虚假功效、伪造认证、侵权品牌词。
- 医疗/保健品/化妆品务必核实目标站点合规要求。
- 只做「优化 + 合规自查」，不承诺「保证上架/排名」。

## License

MIT
