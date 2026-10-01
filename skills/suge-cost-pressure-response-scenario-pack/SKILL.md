---
name: suge-cost-pressure-response-scenario-pack
slug: suge-cost-pressure-response-scenario-pack
displayName: 成本压力应对情景包
display_name: 成本压力应对情景包
display_name_en: "Cost-Pressure Response Scenario Pack"
summary: "把「成本涨了怎么办」变成一张可以逐条核对的决策准备板：逐项成本增量、月度影响（仅输入齐全时）、当前售价下的毛利变化情景、多个响应情景的可比表、未知项、证据缺口、人工决策问题和 Markdown 决策准备板。只做数学情景，不写成建议：不排序、不推荐、不替你调价、不下单、不联系供应商，不出会计、税务或法律结论；金额用 Decimal 精确计算，币种不换算也不跨币种合计。"
description: "面向小商家、工作室与 3-20 人小团队的离线成本压力情景工具。输入是一份 JSON：as_of（必须带时区偏移）、可选的 business（name / timezone）、items[]（item_id、unit_cost_before、unit_cost_after、currency、monthly_volume、current_price、gross_margin_before 可选、supplier、effective_at、evidence_refs[]、notes）与 scenarios[]（显式选项及约束）。逐项判定固定：记录不是对象记 INVALID_ITEM_RECORD；同一 item_id 出现多次记 DUPLICATE_ITEM_ID；单位成本或售价为负数记 NEGATIVE_COST / NEGATIVE_PRICE，月用量为负数记 NEGATIVE_VOLUME，以上任一命中即 BLOCKED；缺少变动前或变动后的单位成本、缺少币种、缺少或写错生效时间则记 INSUFFICIENT_EVIDENCE 并生成待确认问题，绝不按 0 补齐。事实齐全后计算单位增量与方向（UP / DOWN / FLAT）与增量百分比；缺月用量只给单位口径并记 NO_MONTHLY_VOLUME、缺售价只记 NO_CURRENT_PRICE，两种都判 PARTIAL；基准为 0 时百分比无定义并记 DIVISION_BY_ZERO_PRICE；月用量为 0 只记 ZERO_VOLUME 提示；填写的毛利率基线与售价成本推出的比值不一致时记 MARGIN_BASELINE_MISMATCH，两者都原样列出、以售价与成本的明示事实为准。月度影响只汇总有月用量的成本项，按币种分别列示，绝不换算、绝不跨币种合计。情景类型固定为 absorb / price_adjust / substitute_supplier / reduce_volume / discontinue，每行按同一口径输出情景月度成本、成本增量、抵消金额与残余敞口，并支持 max_price_change_pct、min_gross_margin_pct、max_residual_exposure 约束；减少用量与停售会明确标注收入影响未建模。输出 status_counts、items、cost_changes、monthly_impact、margin_scenarios、scenarios、scenario_table、evidence_gaps、unknowns_summary、duplicate_items、human_confirm_items、clarification_questions 与 Markdown 决策准备板。安全上：证据引用只接受单一文件名，含路径、链接或盘符的引用被拒绝且只保留安全化文件名，原引用一字不回显；字段名或值形如凭据直接拒绝处理且不回显原值；输入中的命令与提示注入只标记、不执行，进入简报时替换为固定占位；控制字符在进入任何输出前剥离；所有自由文本转义 Markdown 元字符。只读、离线、不使用任何第三方库。触发词：成本上涨、成本压力、涨价应对、毛利变化、月度成本影响、替代供应商、减少用量、停售情景、成本情景、决策准备板。联系邮箱：43298568@qq.com。"
description_zh: "把「成本涨了怎么办」变成一张可以逐条核对的决策准备板：逐项成本增量、月度影响（仅输入齐全时）、当前售价下的毛利变化情景、多个响应情景的可比表、未知项、证据缺口与 Markdown 决策准备板。只做数学情景，不写成建议；不调价、不下单、不联系供应商，币种不换算、不跨币种合计。"
description_en: "An offline cost-pressure scenario helper for small businesses and 3-20 person teams. It reads one JSON file and returns per-item cost deltas, monthly impact only when the inputs are complete, gross-margin changes at the current price, a comparable table of response scenarios, explicit unknowns, evidence gaps, human decision questions and a Markdown decision board. It produces arithmetic scenarios only, never advice: it does not rank options, does not reprice, does not order, does not contact suppliers, and never converts or sums across currencies."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-cost-pressure-response-scenario-pack
category: small-business-cost-ops
tags: [成本上涨, 成本压力, 涨价应对, 毛利变化, 月度成本影响, 替代供应商, 减少用量, 停售情景]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 成本压力应对情景包

供应商发来一封涨价通知，或者运费突然调整，小老板当下要回答的问题是：**这一涨，一个月多花多少？现在这个售价还能剩多少毛利？我能做点什么？** 现实里这一步通常靠心算和一句「应该还行吧」过去，等到月底对账才发现毛利已经被吃掉。**问题不是没钱赚，而是从来没有把这些明示事实摆在一张表上算过一次。**

本技能做这件事：读一份 JSON，输出**可以逐条核对的决策准备板**——逐项成本增量、月度影响（**只在输入齐全时**）、当前售价下的毛利变化情景、多个响应情景的**可比表**（情景月度成本 / 成本增量 / 抵消金额 / 残余敞口）、未知项、证据缺口、人工决策问题与 Markdown 准备板。**脚本只读一个本地 JSON，不联网、不写文件、不改价、不下单、不联系供应商、不写任何外部系统。**

## 输入与澄清

字段表、判定顺序、情景口径与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，`references/sample-blocked.json` 是负值、重复编号、拒绝与注入的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）与 `items[]`；`scenarios[]` 可选，缺省时只输出逐项与月度口径。

**必须问清的关键点**：

1. **`as_of` 不能省**。没有它就无法判断成本是否已经生效，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **缺月用量就只给单位口径**。`monthly_volume` 缺失记 `NO_MONTHLY_VOLUME` 并判 `PARTIAL`；**月度影响只汇总有月用量的成本项**，缺失值**永不按 0 计算**。
3. **缺售价就没有毛利情景**。`current_price` 缺失记 `NO_CURRENT_PRICE` 并判 `PARTIAL`，工具不会替你推算一个售价。
4. **币种不换算、不跨币种合计**。`monthly_impact.by_currency` 与情景表都按币种分别列示；缺币种记 `UNKNOWN_CURRENCY` 并判 `INSUFFICIENT_EVIDENCE`，该金额不进入任何合计。
5. **负数与重复编号是硬矛盾**。单位成本为负记 `NEGATIVE_COST`、售价为负记 `NEGATIVE_PRICE`、月用量为负记 `NEGATIVE_VOLUME`、同一 `item_id` 出现多次记 `DUPLICATE_ITEM_ID`，任一项判 `BLOCKED`。
6. **生效时间要带偏移**。`effective_at` 缺失记 `NO_EFFECTIVE_AT`、不带时区偏移记 `INVALID_EFFECTIVE_AT`；能解析时只标 `EFFECTIVE_IN_FUTURE` 或 `EFFECTIVE_ALREADY`，**不推断生效日期**。
7. **毛利率基线只做核对**。填了 `gross_margin_before` 时，工具会与「售价 − 单位成本」推出的比值对比；不一致只记 `MARGIN_BASELINE_MISMATCH` 并**把两个值都列出**，始终以售价与成本的明示事实为准。
8. **情景类型固定**。`absorb` / `price_adjust` / `substitute_supplier` / `reduce_volume` / `discontinue`；写了表外的类型记 `UNKNOWN_SCENARIO_TYPE`，**工具不发明情景**。
9. **情景不是建议**。可比表只给算术结果（情景月度成本、成本增量、抵消金额、残余敞口），**不排序、不推荐、不说哪个更优**；`discontinue` 与 `reduce_volume` 会明确标注**收入影响未建模**。
10. **只给文件名，不要给路径或链接**。`evidence_refs` 只接受单一文件名；含路径或链接的引用会被**拒绝**，输出里只保留安全化后的文件名，原引用不会回显。
11. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的成本清单。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` 或没有成本项；`BLOCKED` 表示存在硬矛盾成本项；`GAPS_FOUND` 表示有 `PARTIAL` / 证据不足的成本项，或情景未满足约束；`READY` 表示全部齐全且情景都在约束内。
4. 看每个成本项的 `state`：`COMPUTED` / `PARTIAL` / `INSUFFICIENT_EVIDENCE` / `BLOCKED`。
5. 用 `cost_changes` 看逐项单位增量与增量百分比；用 `monthly_impact` 看按币种的月度影响，并注意 `items_without_volume` 与 `complete` 两个字段。
6. 用 `margin_scenarios` 看当前售价下的毛利变化（单位毛利、毛利率与变化），注意它**不含税、不含销量变化**。
7. 用 `scenario_table` 横向比较各响应情景的**残余敞口**；同一币种内可比，**不同币种不可直接比**。
8. 用 `scenarios[].violations` 找出违反约束的情景（如超出允许调价幅度）；用 `skipped_items` 找出因输入缺失被跳过的成本项。
9. 按 `unknowns_summary` 与 `clarification_questions` 的 `Q-01` 顺序补齐事实，**不要自行填数**。
10. 用 `human_confirm_items` 明确哪些决定必须由人工做（改不改价、换不换供应商、要不要减量），本工具只给数学情景。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何 ERP、财务、采购或供应商系统。
- **不动作**：不改价、不下单、不采购、不联系供应商、不签合同、不删除任何原始材料。
- **不给建议**：不排序、不推荐、不写「应该调价」一类结论；情景之间的取舍由人工判断。
- **只推导不发明**：结论只由输入中的明示事实推导；**不推算售价、不推算销量、不推算生效日期、不补齐缺失成本**。
- **只做数学**：不出会计、税务、法律或合规结论；合同条款、账期与税由人工或专业人士确认。
- **月度门禁**：月度影响只汇总有月用量的成本项，缺失值**永不按 0 计算**；`monthly_impact.complete` 会如实为 `false`。
- **币种门禁**：金额按币种分别列示，**不换算、不跨币种合计**；缺币种的成本项不进入任何合计。
- **确定性**：成本项按编号、情景按编号、可比表按情景与币种、问题按编号、状态汇总按 `ITEM_STATES` 全部显式固定，同一输入输出逐字节一致。
- **文件名门禁**：`evidence_refs` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且**只输出安全化文件名**，原引用在任何字段都不出现。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注等自由文本，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `items[0]/notes`）；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是成本压力的人工决策准备材料，只是对输入事实做的算术情景，不是定价建议、不是会计、税务或法律结论，也不代表任何选项更优。
