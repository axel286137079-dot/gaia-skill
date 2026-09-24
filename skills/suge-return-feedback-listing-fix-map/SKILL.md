---
name: suge-return-feedback-listing-fix-map
slug: suge-return-feedback-listing-fix-map
display_name: 退货反馈到详情页修正地图
display_name_en: "Return Feedback to Listing Fix Map"
summary: "把脱敏的退货原因与差评反馈整理成一张可复核的详情页修正地图：按固定词库聚类原因，分成可归因 / 可能相关 / 不可归因三层，标出每条证据、受影响 SKU 与批次、建议修正的字段、样本是否足够、哪些商品事实未知不得编造、哪些功效或绝对化表述禁止复制，并给出人工改版任务单与两周对比实验。不声称详情页导致退货，不登录店铺、不抓取平台、不接触顾客 PII、不自动改商品、不回复顾客、不计算退款。"
description: "面向电商个体卖家、小品牌、代运营和 3 到 20 人商品团队的离线反馈归因工具：把已经导出的脱敏退货与差评记录，整理成一张可以直接派给运营的详情页修正地图。输入是带时区偏移的 as_of、window（from / to）、可选 min_sample（默认 3）、listing_fields（当前详情页实际存在哪些字段）、listing_facts（字段背后的已知事实，null 表示未知）与 records[]（record_id / sku / batch / channel / occurred_at / reason_text / 可选 reason_code / 可选 rating）。判定规则完全固定且可复核：原因先做 NFKC 加去标点加 casefold 归一化，再按固定词库顺序首个命中分类，因此运输破损先于破损命中、尺码偏小与 size too small 归入同一簇；归因分三层且本工具不主张因果，ATTRIBUTABLE 要求候选字段已在 listing_fields 声明且对应事实在 listing_facts 中已知，POSSIBLY_RELATED 表示字段未声明或事实未知或原因过于笼统，NOT_ATTRIBUTABLE 覆盖物流破损、发错货、缺件、功能与质量缺陷、配送延迟、功效主张与无文本原因，这些一律不写进详情页修正清单；优先级先判不可归因再判样本量，因此再加一百条到货破损也不会变成详情页问题，占比分母是排除重复、冲突、窗口外与无效记录后的分析总体；重复编号按一条计入、同号不同内容判冲突并使整体 BLOCKED、窗口外记录单独列出并排除；未知事实只在实际观察到的问题原因需要时才报告，避免噪音淹没信号；反馈命中功效词只标记不改写，绝不据此类反馈修改详情页功效表述。安全上：凭据形态的字段名或值直接拒绝且不回显；提示注入仅在动作词与目标词同句共现时命中，只标位置不执行并替换为固定占位；控制字符进入输出前剥离且不做 NFKC 到展示文本以免改写中文全角标点；所有自由文本转义 Markdown 元字符。只读、离线、不使用第三方库、不登录店铺、不抓取平台、不处理顾客 PII。触发词：退货原因、退货分析、差评分析、详情页优化、商品页修正、色差、尺码不准、材质不符、适配问题、样本不足、反馈聚类。联系邮箱：43298568@qq.com。"
description_zh: "把脱敏退货与差评反馈整理成详情页修正地图：固定词库聚类原因，分可归因 / 可能相关 / 不可归因三层，标出证据记录、受影响 SKU 与批次、建议修正字段、样本是否足够、哪些事实未知不得编造、哪些功效表述禁止复制，并给出人工改版任务单与两周对比实验。不主张因果、不登录店铺、不接触顾客 PII、不自动改商品。"
description_en: "An offline feedback attribution mapper for e-commerce solo sellers, small brands and 3 to 20 person product teams: it turns already-exported, de-identified return reasons and negative reviews into a listing-fix map that can be handed straight to an operator. Reasons are normalised (NFKC, punctuation stripped, case folded) then classified by a fixed keyword table where the first match wins, so in-transit damage resolves before generic damage; attribution is layered into attributable, possibly-related and not-attributable, and the tool never claims the listing caused the return - attributable requires the candidate field to be declared AND the fact behind it to be known; not-attributable covers logistics damage, wrong item, missing parts, functional and quality defects, delivery delay, efficacy claims and blank reasons, none of which become detail-page tasks. Priority checks not-attributable before sample size, duplicate record ids collapse to one, conflicting ids block the run, out-of-window records are excluded and listed, and unknown facts are only reported when an actually observed reason needs them. Efficacy language is flagged but never rewritten. It never logs in to a store, never scrapes a platform, never handles customer PII, never edits a listing and never replies to a customer."
version: 1.0.1
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-return-feedback-listing-fix-map
category: ecommerce-ops
tags: [退货原因, 差评分析, 详情页优化, 反馈聚类, 色差, 尺码, 归因分层, 样本不足]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 退货反馈到详情页修正地图

卖家最容易做错的一件事，是**看到几条「尺码偏小」就改尺码表，看到两条「到货破损」就把包装说明改了三遍**。前者可能成立，后者根本不该出现在详情页清单里——破损是物流或包装工艺问题，改描述解决不了。

本技能处理的是这中间的一步：把已经导出的脱敏退货与差评记录，整理成一张**能派给运营、每条都有证据、并且明确告诉你「这条不该改详情页」**的修正地图。**脚本不登录店铺、不抓取平台、不接触顾客 PII、不自动修改商品、不回复顾客、不计算或执行退款。**

## 输入与澄清

字段表、原因分类表、归因规则与优先级见 @references/guide.md（同目录 `references/sample.json` 是可复现样例，输出分布固定）。

最少确认：`as_of`、`window`、`listing_fields`、`listing_facts`、`records[]`。

**必须问清的关键点**：

1. **`listing_fields` 是「现在真的有哪些字段」**。工具只在这些字段里给建议。没声明的字段只会出现在「建议先声明的字段」里，不会被当成现有字段指路。
2. **`listing_facts` 里留 `null` 就是「不知道」**。工具不会替你补成分、适配型号或颜色名——**未知事实会在改写前被拦下**。
3. **不要提交顾客姓名、电话、地址、订单号或店铺凭据**。本工具不需要 PII；出现凭据形态的内容会被整体拒绝且不回显。
4. **归因不是因果**。「可归因」只表示这条反馈能落到一个可以改写的字段上，**不代表详情页导致了退货**。
5. **「到货破损」这类不会出现在修正清单里**。物流破损、发错货、缺件、功能与质量缺陷、配送延迟、功效主张都会被判为不可归因，按各自流程处理。
6. **样本不够时不给改写建议，只给收集清单**。低于 `min_sample`（默认 3 条）的原因簇**不会**进入人工改版任务单，也不会附带目标字段；它们只出现在独立的 `evidence_collection_tasks`（「证据不足：先收集，暂不改写」）里，说明还差几条、先观察什么。两个清单互斥，同一条原因不会同时出现在两边。
7. **功效类反馈只记录不改写**。出现「缓解」「没效果」一类表述时会被标出，**不得**据此修改详情页的功效描述。
8. **批次要填**。填了批次，修正范围就会被限定到具体批次，不会扩散成全量改版。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看 `status`：`REJECTED`（疑似凭据，已拒绝且未回显）；`BLOCKED`（窗口非法或记录冲突）；`INPUT_INCOMPLETE`（缺 `as_of` 或没有可分析记录）；`GAPS_FOUND`（可用但有缺口）。
4. 看 `clusters`：只处理 `ATTRIBUTABLE` 且优先级为 `HIGH` / `MEDIUM` 的簇。
5. 看 `revision_tasks`：改哪些字段、限定哪个批次、依据哪几条 `record_id`。**只包含达到样本线的原因**。
6. 看 `evidence_collection_tasks`：样本还不够的原因，这里**只有观察项，没有任何改写动作、没有目标字段**，本次不要据此改详情页。
7. 看 `unknown_facts`：**补齐之前不要动对应字段**。
8. 看 `not_to_rewrite` 与 `claim_flags`：功效与绝对化表述不得复制到文案里。
9. 看 `verification_experiments`：按两周窗口对比该原因的占比变化。
10. 看 `clarification_questions`，按 `Q-01` 顺序去问。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不登录店铺、不抓取平台、不读取订单或顾客数据、不写文件、不访问任何 URL。
- **不改动**：不自动修改商品、不发布详情页、不回复顾客、不计算或执行退款。
- **不主张因果**：归因分层只表示「能否落到可改写字段」与证据强弱，不是因果结论。
- **不编造**：成分、材质、适配型号、认证、功效一律不推断；未知事实进入 `unknown_facts` 并在改写前拦下。
- **不默认 0**：缺失的评分、批次、SKU 保持未知并单列，不参与计算。
- **确定性**：排序全部显式固定（簇按优先级、条数、原因码排序；字段名、问题编号均固定），同一输入输出逐字节一致。
- **批次限定**：每个簇都给出 `affected_batches` 与 `affected_skus`，修正任务单据此限定范围。
- **样本门槛**：低于 `min_sample` 不给改写建议——这类簇只进 `evidence_collection_tasks` 观察清单，**不带目标字段、不带改写动作**，与 `revision_tasks` 严格互斥；「不可归因」优先于「样本不足」。
- **提示注入逐字段检测、位置精确、低误报**：覆盖 `records[].reason_text`，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `records[5]/reason_text`）。检测要求「动作词 + 目标词」**同句共现**，普通抱怨不会被误标。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。**不做 NFKC 到展示文本**，避免把中文全角标点改写成半角。
- **超长文本截断**：`reason_text` 超过 500 字符时简报中截断并标记 `REASON_TEXT_TRUNCATED`，分类仍用完整文本。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是反馈归因与改写候选材料，不构成因果结论、合规意见或平台规则解释；是否修改详情页由人工决定。
