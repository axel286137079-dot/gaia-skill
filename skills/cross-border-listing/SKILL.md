---
name: cross-border-listing
slug: cross-border-listing
displayName: 跨境Listing优化
summary: 中文商品信息 → 亚马逊 A9/SEO 优化 Listing（标题/五点/关键词/多语种），内置合规自查——违禁词、标题长度、禁止符号，防侵权防下架。
license: MIT
description: 跨境 Listing 优化（亚马逊/独立站/TikTok Shop）。把中文商品信息生成 A9/SEO 优化的英文 Listing：标题（公式 + 关键词前置 + ≤200字符）、五点描述（卖点前置 + 场景化）、Search Terms 关键词埋入、多语种版本（英/德/日/西等），并内置合规自查（bin/listing_check.py：亚马逊违禁词、绝对化/夸大词、健康疗效宣称、标题长度与禁止符号、全大写检测，纯标准库离线可跑），防侵权防下架。用于：写亚马逊 Listing、跨境 Listing 优化、五点描述、标题优化、产品关键词、多语种 Listing、亚马逊标题、产品文案、listing 合规、Search Terms。触发词：亚马逊 Listing、跨境 Listing、五点描述、产品标题、产品关键词、Search Terms、多语种 Listing、listing 优化、产品文案、亚马逊标题、listing 合规。联系邮箱：43298568@qq.com。
version: 0.1.0
category: 电商
tags: [跨境电商, 亚马逊Listing, 产品文案, SEO, 合规自查]
platforms: [workbuddy, claude-code, cursor]
---

# 跨境 Listing 优化

把中文商品信息生成**A9/SEO 优化的多语种 Listing**，内置合规自查，防侵权防下架。

## 何时使用

- 用户要写亚马逊 / 独立站 / TikTok Shop 的 Listing
- 用户要生成标题、五点描述、Search Terms、多语种版本
- 用户要自查 Listing 会不会踩违禁词 / 超长 / 违规符号

## 工作流（四步）

### 1. 理解商品
- 输入：中文产品名 + 卖点 + 目标站点（默认美国站）。
- 提炼：核心卖点、目标人群、使用场景、差异化。

### 2. 生成标题（A9 公式）
- **公式**：`[核心关键词] + [品牌] + [卖点1] + [卖点2] + [规格/材质]`
- 关键词前置（A9 权重高）、≤200 字符、每个词首字母大写（除介词/冠词）。
- 禁止：全大写、特殊符号（! ? $ & 等）、促销词、夸大词。

### 3. 五点描述（Bullet Points）
- 每点：卖点前置 + 场景化 + 量化（尺寸/材质/容量/适用人群）。
- 结构：`[卖点词] - [一句话说明 + 场景/数据]`，每点 ≤250 字符。

### 4. Search Terms + 多语种
- Search Terms：埋长尾关键词 + 同义词 + 拼写变体（不重复标题已有关键词）。
- 多语种：按目标站点翻译（英/德/日/西/法），保留关键词本地化。

### 5. 合规自查（发布前必做）
```bash
python3 bin/listing_check.py listing.md
# 输出：违禁词 + 标题长度/符号 + 全大写 + 绝对化词 检查
```

## 合规红线

| 类别 | 典型违禁 | 风险 |
|---|---|---|
| 绝对化/夸大 | best、#1、100%、perfect、ultimate | 亚马逊禁，违规下架 |
| 健康疗效宣称 | cure、treat、heal、anti-cancer、FDA approved | FDA 严查，高危 |
| 促销/引流 | sale、discount、free shipping、官网链接 | 亚马逊禁 |
| 标题规范 | 全大写、特殊符号、超 200 字符 | 违反格式规范 |

## 边界与红线

- 不生成虚假功效、伪造认证（FDA/CE 等）、侵权品牌词的描述。
- 涉及医疗器械、保健品、化妆品，务必核实目标站点合规要求（各国法规不同）。
- 不承诺「保证上架 / 保证排名」，只做「优化 + 合规自查」。

## 参考

- 违禁词库内置于 `bin/listing_check.py`。
- 示例见 `examples/`。
