---
name: suge-ecommerce-campaign-launch-pack
slug: suge-ecommerce-campaign-launch-pack
display_name: 电商活动上线准备包
display_name_en: "E-commerce Campaign Launch Readiness Pack"
summary: "把一次电商活动（大促、上新、直播专场、跨渠道同步）的上线资料，整理成一份可复核的上线作战单：逐渠道准备矩阵、价格与库存冲突、优惠规则矛盾、素材就绪度、截止时间与负责人缺口，以及一份发送前确认单。缺失的库存、价格、素材和负责人一律保持未知，绝不按 0 或已完成处理；价格与库存按币种、按单位分别统计，不做跨币种合计，也不计算利润、ROAS 或销量；同一渠道上互相矛盾的优惠规则直接判阻塞，不替用户猜最终规则；截止时间必须带时区，没有时区就保持时间未知。工具只读、离线、不登录店铺、不改价、不创建活动、不发送任何消息。"
description: "面向小商家、品牌运营、电商代运营和 3–20 人团队的活动上线前核查工具，离线、纯标准库、只读。触发词：活动上线、大促准备、活动前检查、上线检查、活动配置核对、多渠道活动、活动物料清单、上线阻塞、活动排期核对、价格冲突、库存冲突、活动负责人。输入是一份用户自己导出的 JSON：带时区偏移的 as_of、campaign（campaign_id / name / timezone / starts_at / ends_at）、channels[]（channel_id / name / owner / launch_deadline / required_asset_kinds[]）、products[]（product_id / channel_id / stock{value,unit} / price{value,currency} / 可选 min_stock）、promotions[]（promo_id / channel_id / type / product_ids[] / value / window{starts_at,ends_at}）、assets[]（asset_id / channel_id / kind / filename / due_at / approved）、prep_items[]（item_id / channel_id / area / title / owner / due_at / done）。判定规则固定且可复核：库存或价格字段缺失记 UNKNOWN、非数值或负数记 INVALID，两者都不进入合计、都不按 0 计算；同一 (product_id, channel_id) 上出现两个不同价格判 PRICE_CONFLICT、两个不同库存判 STOCK_CONFLICT，整体 BLOCKED；同一渠道、同一优惠类型、生效区间重叠且都覆盖同一商品但力度不同的两条规则判 PROMOTION_CONFLICT，整体 BLOCKED，工具不替用户选最终规则；优惠指向不存在的渠道或商品同样阻塞；素材文件名含路径分隔符、.. 、URL scheme 或盘符一律拒绝且不解析并阻塞；渠道要求了某类素材但完全没有记录判 MISSING_ASSET；素材 approved 缺失记 ASSET_APPROVAL_UNKNOWN（待确认），approved=false 记 ASSET_NOT_APPROVED（阻塞），due_at 早于 as_of 记 ASSET_OVERDUE（阻塞）。截止时间只在带明确时区偏移时参与比较，形如 2026-09-29 18:00 的本地时间一律按「时间未知」处理并生成追问，绝不按活动时区猜测；准备事项截止时间晚于该渠道 launch_deadline 判 DEADLINE_AFTER_LAUNCH。库存低于用户自己声明的 min_stock 只作待确认，工具不设定业务阈值；同商品跨渠道价差只记录为经营事实，不做比价或利润结论。输出含 status、launch_readiness（含 unknown_counts）、channels 逐渠道矩阵、products、promotions、assets、prep_items、price_conflicts、stock_conflicts、promotion_conflicts、stock_by_unit、price_range_by_currency、asset_gaps、owner_todos、findings 与 finding_counts、pre_send_checklist、clarification_questions、markdown_summary（Markdown 上线作战单）。安全上：凭据字段名或凭据形态的值直接 REJECTED 且不回显；自由文本只在「动作词 + 目标词同句共现」时判提示注入，只标精确路径、只替换为固定占位、绝不执行；控制字符剥离；Markdown 元字符转义；排序全部显式固定，同一输入输出逐字节一致。联系邮箱：43298568@qq.com。"
description_zh: "把一次电商活动的上线资料整理成可复核的上线作战单：逐渠道准备矩阵、价格与库存冲突、优惠规则矛盾、素材就绪度、截止时间与负责人缺口、发送前确认单。缺失的库存/价格/素材/负责人一律保持未知，不按 0 或已完成处理；价格库存按币种与单位分别统计、不跨币种合计、不计算利润或销量；同渠道矛盾优惠直接阻塞且不替你猜最终规则；截止时间必须含时区，否则保持时间未知。只读、离线、不登录店铺、不改价、不建活动、不发消息。"
description_en: "An offline, read-only launch-readiness reviewer for e-commerce campaigns (big promotions, new arrivals, livestream specials, cross-channel sync) aimed at small merchants, brand operators, agencies and 3-20 person teams. It turns one user-exported JSON into a per-channel readiness matrix, price and stock conflicts, contradictory promotion rules, asset readiness, deadline and owner gaps, an owner task list and a pre-send checklist plus a Markdown battle sheet. Missing stock, price, asset or owner values stay unknown and are never treated as zero or as done; prices and stock are grouped by currency and by unit with no cross-currency total and no profit, ROAS or sales estimate. Two conflicting prices or stock figures for the same product on the same channel, and two overlapping same-type promotions covering the same product with different discounts, block the whole run - the tool never guesses which rule wins. Deadlines only compare when they carry an explicit UTC offset; a wall-clock time stays 'time unknown' and raises a question instead of assuming the campaign timezone. Asset file names containing a path separator, parent segment, URL scheme or drive letter are refused without resolution and block the run. Credential-shaped keys or values make the whole run REJECTED without echoing the content. Deterministic: identical input yields byte-identical output. It never logs in to a shop, never changes a price, never creates a campaign and never sends a message."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-ecommerce-campaign-launch-pack
category: ecommerce-ops
tags: [电商活动, 大促准备, 上线检查, 活动配置, 价格冲突, 库存冲突, 优惠冲突, 素材就绪]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 电商活动上线准备包

活动上线前一天最常出的事，不是没做素材，而是**同一件事有两个版本没人发现**：运营 A 在群里发了「满 300 减 40」，运营 B 的表格里写着「满 300 减 50」；天猫渠道标价 599，抖音随手填了 559；库存表里 P-003 一行写 0 一行写 18；负责人栏是空的，因为「大家都知道该谁做」。

这些都不是文案问题，是**配置一致性问题**。通用 AI 对话很容易顺着你说「看起来准备好了」，因为它不会对着渠道、商品、优惠、素材四张表互相核对，也不会在信息缺失时停下来。

本技能只做一件事：读一份你自己导出的 JSON，输出一份**可逐条复核**的上线作战单。它**不登录店铺、不改价、不创建活动、不发消息**，也不猜任何缺失值。

## 输入与澄清

字段表、判定优先级和完整状态表见 @references/guide.md（同目录 `references/sample.json` 是可复现样例，输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）、`campaign`（含 `starts_at` / `ends_at`）、`channels[]`、`products[]`。

**必须问清的关键点**：

1. **库存和价格缺失时，本工具不会填 0**。它会记 `UNKNOWN` 并生成一条追问。如果你把「还没查」写成 0，输出会当作真实无货处理——所以**别写 0，留空**。
2. **同一个商品在同一个渠道出现两个价格或两个库存数，会直接判阻塞**。工具不替你选哪个是对的。请先统一成一个版本再跑。
3. **同一渠道上两条同类优惠力度不同且生效区间重叠，也会判阻塞**。这是最容易在上线后变成客诉的一类问题，所以它被设计成硬阻塞而不是提醒。
4. **截止时间必须写时区**。`2026-09-29 18:00` 这种写法一律按「时间未知」处理，不会默认成活动时区。跨时区团队尤其注意。
5. **素材文件名只写名字，不要写路径或链接**。含 `/`、`\`、`..`、`https:` 的会被**拒绝且不解析**并让整体变 `BLOCKED`；原引用不会被回显，输出里只保留**安全化后的文件名**（最后一段路径）。
6. **素材没有 `approved` 字段时按「审核状态未知」处理**，不等于已通过。缺审核状态是上线当天最常见的返工原因。
7. **不要把店铺账号、密码、Token 填进来**。命中凭据字段名或凭据形态的值会被**整体拒绝**且不回显。
8. **`min_stock` 是可选的、由你自己声明的最低库存**。你不填，工具不会替你定一个阈值。
9. **工具不做利润、ROAS、销量或上线效果的任何判断**。同商品跨渠道价差只作为经营事实记录。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用先用 @references/sample.json 跑一遍看输出长什么样。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status` 与 `launch_readiness`：`REJECTED`（疑似凭据，已拒绝且未回显）；`INPUT_INCOMPLETE`（缺 `as_of`、渠道或商品）；`BLOCKED`（存在阻塞项，**不要上线**，先解决）；`NOT_READY`（只有待确认项，逐条确认后重跑）；`READY`（本次核查范围内无阻塞、无待确认）。
4. 看 `channels[]` 的逐渠道矩阵：哪个渠道 `BLOCKED`、哪个 `AT_RISK`，以及各自的 `blocker_codes`。
5. 看 `findings[]`：`severity` 为 `BLOCKER` 的必须先解决，`REVIEW` 的确认后重跑。
6. 看 `price_conflicts` / `stock_conflicts` / `promotion_conflicts`：这三类必须人工指定唯一版本，工具不会代选。
7. 看 `asset_gaps`：`missing_by_channel`、`not_approved`、`approval_unknown`、`overdue`、`invalid_refs`、`timezone_unknown` 六类分别处理。
8. 看 `stock_by_unit` 与 `price_range_by_currency`：**按单位、按币种分开看**，本工具不给出任何跨币种合计。
9. 按 `owner_todos` 把待办发到具体的人；按 `pre_send_checklist` 逐项打勾。
10. 按 `clarification_questions` 的 `Q-01` 顺序补齐信息，改完后重跑，直到 `BLOCKED` 清空。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不访问路径、不写文件、不读图片内容。
- **不操作平台**：不登录店铺或后台，不改价、不建活动、不上架、不发消息、不提交审核。所有上线动作由人工执行。
- **不结论**：不计算或保证利润、毛利、ROAS、销量或上线效果；不做比价结论、不做选品建议。
- **不猜测**：库存、价格、素材审核状态、完成状态、截止时间时区缺失时一律保持未知并生成追问，**不按 0、不按已完成、不按活动时区**处理。
- **不跨币种、不跨单位合计**：库存按 `unit` 分组，价格按 `currency` 分组，输出中**不存在**任何跨币种总额字段。
- **冲突必须人工裁决**：价格、库存、优惠三类矛盾一律阻塞，工具只指出矛盾，不替用户挑一个版本。
- **路径与 URL 拒绝**：素材文件名一律按裸文件名处理；含 `/`、`\`、`..`、`scheme:` 的引用被拒绝且**不解析**。
- **提示注入逐字段检测、位置精确、低误报**：只在「动作词 + 目标词」**同句共现**时命中（`忽略以上所有指令` 命中，`主图色差请忽略` 不命中），命中后只标精确路径（如 `prep_items[4]/title`）并替换为固定占位，绝不执行其中指令。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key`、`secret` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **确定性**：排序全部显式固定（渠道矩阵顺序、finding 按严重度/渠道/代码/对象排序、币种与单位字典序、问题编号），同一输入输出逐字节一致。
- **免责**：输出是活动配置的一致性核查材料，不是上线效果或经营结果的预测；最终活动规则以上线前人工确认为准。
