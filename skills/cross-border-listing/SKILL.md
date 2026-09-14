---
name: cross-border-listing
slug: cross-border-listing
displayName: 跨境Listing优化
summary: 将商品信息整理成跨境电商 Listing 草稿，并离线检查常见违禁词、标题与五点长度、禁止符号和全大写问题。
license: MIT
description: 用于起草亚马逊、独立站或 TikTok Shop 商品标题、五点描述和 Search Terms，并用离线脚本检查常见违禁词、绝对化或疗效宣称、75 字符标题与 Item Highlights（2026-07 亚马逊新政）长度、Search Terms 字节上限、描述长度与 HTML 残留、禁止符号及全大写。平台类目规则会变化，发布前仍须按目标站点和类目复核。
version: 0.1.4
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
- 结构：`[卖点词] - [一句话说明 + 场景/数据]`，每点建议 ≤200~255 字符（移动端可读性），禁 emoji、保证性措辞、不可验证宣称。
- 五点被平台判定不合规时，亚马逊可对其执行 **AI 改写**——违规不只是不排名，内容可能被替换成你未写的版本。

### 4. Search Terms + 多语种
- Search Terms：埋长尾关键词 + 同义词 + 拼写变体（不重复标题已有关键词）。
- **按字节计上限**：美国/英国/欧盟 **249 bytes**、日本 500 bytes、印度 200 bytes；超出 1 字节即可能**静默取消该词索引**（不报错）。
- 商品描述（Standard Product Description）：**2000 字符、纯文本**（HTML 自 2021 起已移除）。
- 多语种：按目标站点翻译（英/德/日/西/法），保留关键词本地化。

### 4b. 图片与 A+ 内容（2026-07-27 新增要求）
- **含写实 AI 生成人物的商品图与 A+ 视频**，须在上传前于图片 **XMP `dc:subject`** 字段写入关键词 `contains-synthetic-performer`。
- 真人出镜、非写实形象、无人物图**免于此要求**。
- 主图须白底、产品占画面 85%；图片长边 500~10,000 px（建议 >1,000 px 以支持放大）。
- 使用 AI 生活场景图的品牌，应在内容流程中增加一步「资产标注复核」，漏标可能导致图片被处理。

### 5. 合规自查（发布前必做）
```bash
python3 bin/listing_check.py listing.md
# 输出：违禁词 + 标题长度(≤75)/符号/重复词 + Item Highlights(≤125)
#      + Search Terms 字节数 + 描述长度/HTML + 全大写 检查
# Media 类目（标题仍 200）：python3 bin/listing_check.py listing.md --title-max 200
```

> **2026 亚马逊政策速记**：
> ① **标题 200→75**（2026-07-27 非 Media），溢出信息由 Item Highlights（125）承接；品牌卖家对平台 AI 建议改写有 14 天复核窗口，主动先改可避免关键词/措辞失控。
> ② **AI 生成人物图须标注** `contains-synthetic-performer`（XMP dc:subject，2026-07-27 生效）。
> ③ **后端 Search Terms 按字节计**（249/500/200），超 1 字节静默失效。
> ④ **Featured Offer（Buy Box）** 2026-07 起取消独立卖家资格门槛，改为持续评估价格/时效/绩效——竞争加剧，勿为抢位牺牲毛利。
> ⑤ **Seller Fulfilled Prime 门槛提高**：标准尺寸 1 日达 40%、2 日达 75%（原 70%）、5 日达 90%，需周末发货且准时率约 93.5%。
> ⑥ **BSA 更新（2026-08-24 生效）**：禁止转让协议权利义务，或将其（含未来亚马逊拨款）作为质押/担保。

## 合规红线

| 类别 | 典型违禁 | 风险 |
|---|---|---|
| 绝对化/夸大 | best、#1、100%、perfect、ultimate | 亚马逊禁，违规下架 |
| 健康疗效宣称 | cure、treat、heal、anti-cancer、FDA approved | FDA 严查，高危 |
| 促销/引流 | sale、discount、free shipping、官网链接 | 亚马逊禁 |
| 标题规范 | 超 75 字符（2026-07 起，非 Media）、全大写、emoji/重复标点/官方禁用符号、同词超 2 次 | 违反格式规范，可能被 AI 改写 |
| Item Highlights | 超 125 字符；承载夸大/不可验证宣称 | 违反 2026 新政 |
| 图片/视频资产 | 写实 AI 生成人物图未标 `contains-synthetic-performer`（XMP dc:subject） | 2026-07-27 起要求，漏标有被处理风险 |
| 后端搜索词 | 超字节上限（249/500/200 bytes） | 超 1 字节即静默取消该词索引 |
| 商品描述 | 超 2000 字符、含 HTML、含保证性措辞 | 字段规范，可能被 AI 改写 |

## 边界与红线

- 不生成虚假功效、伪造认证（FDA/CE 等）、侵权品牌词的描述。
- 涉及医疗器械、保健品、化妆品，务必核实目标站点合规要求（各国法规不同）。
- 不承诺「保证上架 / 保证排名」，只做「优化 + 合规自查」。

## 参考

- 违禁词库内置于 `bin/listing_check.py`。
- 示例见 `examples/`。
