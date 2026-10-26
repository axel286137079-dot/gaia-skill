# 跨境 Listing 优化

把中文商品信息整理为面向搜索的多语种 Listing 草稿，并做常见措辞、长度和格式检查。

## 一句话定位

本 skill 覆盖标题、五点、Search Terms 和多语种草稿，并在交稿前运行可配置的基础检查。

## 为什么值得做

- Listing 是跨境电商的「门面」，标题/五点的好坏直接决定点击率和转化率。
- 夸大或未经证实的功效主张可能带来审核、投诉或退货风险，值得在发布前人工复核。
- 多语种 Listing 需要按目标市场表达商品事实；本地脚本可先找出一部分格式与措辞风险。

## 快速开始

### 合规自查（核心工具）
```bash
python3 bin/listing_check.py 你的listing.md
# 输出：需复核措辞 + 可配置标题/亮点长度 + 符号/重复词/全大写检查

python3 bin/listing_check.py 你的listing.md --json --title-max 75 --bullet-max 500
# Media（图书/影音）类目标题仍为 200：--title-max 200
```

### 示例
```bash
python3 bin/listing_check.py examples/sample_listing.md
```

## 覆盖的检查项

| 类别 | 典型 | 风险 |
|---|---|---|
| 绝对化/夸大 | best/#1/100%/perfect/ultimate | 需要事实依据及站点规则复核 |
| 健康疗效宣称 | cure/treat/heal/anti-cancer/FDA approved | 需核对资质与适用类目 |
| 促销/引流 | sale/free shipping/.com/官网链接 | 需核对目标字段的站点规则 |
| 标题规范 | 长度/全大写/emoji/重复标点/特殊符号/重复词 | 默认值只适用于所述站点类目；可配置 |
| Item Highlights | 长度及未经证实的主张 | 需核对站点和类目 |

## 目录结构

```
cross-border-listing/
├── SKILL.md                 # 路由逻辑 + 标题/五点公式 + 合规红线
├── README.md                # 本文件
├── bin/listing_check.py     # 合规自查（纯标准库，离线可跑）
├── examples/sample_listing.md  # 示例（含演示用的风险措辞）
└── references/official-policy.md # 已核对的政策来源与边界
```

## 边界与红线

- 不生成虚假功效、伪造认证、侵权品牌词。
- 医疗/保健品/化妆品务必核实目标站点合规要求。
- 只做「优化 + 合规自查」，不承诺「保证上架/排名」。

## License

MIT
