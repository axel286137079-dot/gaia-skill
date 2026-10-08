---
name: suge-lifestyle-content-studio
slug: suge-lifestyle-content-studio
displayName: 生活方式内容工作室
display_name: 生活方式内容工作室
display_name_en: Lifestyle Content Studio
summary: 生成生活方式图文的选题、标题、正文、封面字和排版草稿，并用离线词库提示常见高风险措辞。
license: MIT
description: 面向个人创作者、小商家和内容团队的生活方式图文创作工作流：把产品事实、受众和场景整理为选题、标题、正文、封面文字和标签，再通过离线词库检查极限词、医疗绝对化、金融诱导、站外导流、教培夸大、竞品拉踩和焦虑营销等高风险措辞。检查结果仅是启发式提示，不是法律意见或任何平台的审核结论。本技能不代发、不承诺流量或审核结果。
description_zh: 面向个人创作者、小商家和内容团队的生活方式图文创作工作流，生成选题、标题、正文、封面文字和标签，并用离线词库提示常见高风险措辞。
description_en: A lifestyle content workflow for creators and small teams that drafts topics, titles, body copy, cover text and tags, then flags common high-risk wording with an offline rule set.
version: 0.1.9
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-lifestyle-content-studio
category: 内容创作
tags: [生活方式内容, 图文创作, 敏感词自查, 种草文案, 内容合规]
platforms: [workbuddy, claude-code, cursor]
---

# 生活方式内容工作室

把生活方式图文内容整理成：**选题 → 标题 → 正文 → 封面文字 → 标签 → 风险提示 → 人工确认**。

## 何时使用

- 用户要写生活方式图文、产品介绍、新品种草或收藏型清单。
- 用户要批量生成选题、标题和封面文字备选。
- 用户要检查文案中的极限词、绝对化承诺、导流或其他高风险表达。

## 必要输入

先确认以下信息；缺失时只追问会实质改变结果的项：

1. 内容主题或产品事实（不得编造功效、价格、销量或背书）。
2. 目标受众和使用场景。
3. 希望语气、长度和必须包含或避免的信息。
4. 是否存在医疗、金融、教育、保健或其他高风险领域。

## 工作流

### 1. 选题

- 先写清「给谁看、解决什么问题、提供什么真实价值」。
- 不伪造搜索热度、用户反馈或竞品对比数据。

### 2. 文案

- 给出 3 个不同角度的标题，避免绝对化和无依据数字。
- 正文按「场景钩子 → 真实痛点 → 方法或体验 → 边界提示 → 互动问题」组织。
- 区分事实、个人体验和推测；不把推测写成事实。

### 3. 封面和排版

- 提供 2–3 个封面文字备选，每个尽量不超过 14 个汉字。
- 用短段落和适量列表提升可读性，不堆叠表情符号。

### 4. 风险词自查

```bash
python3 bin/content_risk_check.py 草稿.md
python3 bin/content_risk_check.py 草稿.md --json
```

工具仅使用本地词库，输出「命中词、类别、位置、替换建议」。词库不能替代语境判断、法律建议或目标平台的最新规则。

### 5. 交付

固定输出：

1. 3 个标题。
2. 1 版正文。
3. 2–3 个封面文字。
4. 3–8 个标签。
5. 风险命中与修改建议。
6. 发布前人工确认清单。

## 边界与红线

- 不生成医疗、金融或教育的绝对化承诺，不伪造资质、背书、业绩、证言或用户反馈。
- 不生成荐股、带单、保本保息或规避审核的暗号。
- 不贬低可识别第三方品牌，不伪造人设、数据或交易记录。
- AI 生成内容在发布前须根据适用法律和目标平台要求完成标识。
- 本技能只产出草稿和风险提示，不执行外部发布。

## 参考

- 风险词库：`bin/content_risk_check.py`
- 示例：`examples/sample_note.md`
