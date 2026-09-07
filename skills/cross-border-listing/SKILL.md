---
name: cross-border-listing
slug: cross-border-listing
displayName: 跨境Listing优化
summary: 将商品信息整理成跨境电商 Listing 草稿，并离线检查常见违禁词、标题与五点长度、禁止符号和全大写问题。
license: MIT
description: 用于起草亚马逊、独立站或 TikTok Shop 商品标题、五点描述和 Search Terms，并用离线脚本检查常见违禁词、绝对化或疗效宣称、75 字符标题与 Item Highlights（2026-07 亚马逊新政）长度、禁止符号及全大写。平台类目规则会变化，发布前仍须按目标站点和类目复核。
version: 0.1.3
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/cross-border-listing
category: 电商
tags: [跨境电商, 亚马逊Listing, 产品文案, SEO, 合规自查]
platforms: [workbuddy, claude-code, cursor]
---

# 跨境 Listing 优化

把中文商品信息整理为**面向搜索的多语种 Listing 草稿**，并做基础合规提示。脚本不能保证排名、避免侵权或防止下架。

## 何时使用

- 用户要写亚马逊 / 独立站 / TikTok Shop 的 Listing
- 用户要生成标题、五点描述、Search Terms、多语种版本
- 用户要自查 Listing 会不会踩违禁词 / 超长 / 违规符号

## 工作流（四步）

### 1. 理解商品
- 输入：中文产品名 + 卖点 + 目标站点（默认美国站）。
- 提炼：核心卖点、目标人群、使用场景、差异化。

### 2. 生成标题（亚马逊 2026 新政）
- **Item name ≤75 字符（含空格）**：2026-07-27 起非 Media 类目统一执行；Media（图书/影音）仍 200。
- **公式**：`[品牌] + [核心产品词] + [关键规格] + [强区分属性]`，核心词前置（移动端仅完整展示前 50 字符）。
- 同一实词最多出现 2 次；禁止 emoji、重复标点（!!! / …）、HTML、官方禁用符号（! $ ? _ { } ^ ¬ ¦，品牌名内含时例外）。
- 标题放不下的材质/兼容/场景/卖点 → 写入 **Item Highlights**（≤125 字符、可搜索、随标题展示），勿堆回标题。
- 禁止：全大写、促销词、夸大词、竞品对比。标题不要写到正好 75，留 5~10 字符冗余。

### 3. Item Highlights + 五点描述
- 每点：卖点前置 + 场景化 + 量化（尺寸/材质/容量/适用人群）。
- 结构：`[卖点词] - [一句话说明 + 场景/数据]`，每点 ≤250 字符。

### 4. Search Terms + 多语种
- Search Terms：埋长尾关键词 + 同义词 + 拼写变体（不重复标题已有关键词）。
- 多语种：按目标站点翻译（英/德/日/西/法），保留关键词本地化。

### 5. 合规自查（发布前必做）
```bash
python3 bin/listing_check.py listing.md
# 输出：违禁词 + 标题长度(≤75)/符号/重复词 + Item Highlights(≤125) + 全大写 检查
# Media 类目（标题仍 200）：python3 bin/listing_check.py listing.md --title-max 200
```

> **2026-07-27 亚马逊新政速记**：标题上限 200→75（非 Media），多出的空间由 Item Highlights（125）承接；品牌卖家对平台 AI 建议改写有 14 天复核窗口，主动先改可避免关键词/措辞失控。

## 合规红线

| 类别 | 典型违禁 | 风险 |
|---|---|---|
| 绝对化/夸大 | best、#1、100%、perfect、ultimate | 亚马逊禁，违规下架 |
| 健康疗效宣称 | cure、treat、heal、anti-cancer、FDA approved | FDA 严查，高危 |
| 促销/引流 | sale、discount、free shipping、官网链接 | 亚马逊禁 |
| 标题规范 | 超 75 字符（2026-07 起，非 Media）、全大写、emoji/重复标点/官方禁用符号、同词超 2 次 | 违反格式规范，可能被 AI 改写 |
| Item Highlights | 超 125 字符；承载夸大/不可验证宣称 | 违反 2026 新政 |

## 边界与红线

- 不生成虚假功效、伪造认证（FDA/CE 等）、侵权品牌词的描述。
- 涉及医疗器械、保健品、化妆品，务必核实目标站点合规要求（各国法规不同）。
- 不承诺「保证上架 / 保证排名」，只做「优化 + 合规自查」。

## 参考

- 违禁词库内置于 `bin/listing_check.py`。
- 示例见 `examples/`。
