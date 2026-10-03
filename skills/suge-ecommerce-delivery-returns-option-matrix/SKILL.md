---
name: suge-ecommerce-delivery-returns-option-matrix
slug: suge-ecommerce-delivery-returns-option-matrix
displayName: 电商配送与退货选项矩阵
display_name: 电商配送与退货选项矩阵
display_name_en: "E-commerce Delivery & Returns Option Matrix"
summary: "把「我们的配送和退货到底怎么配」整理成一张可以逐条核对的区域×渠道矩阵：每个单元格给出 READY / ACTION_NEEDED / INSUFFICIENT_EVIDENCE / BLOCKED，加上配送与退货选项清单、无配送覆盖、无退货路径、币种不一致、时效倒置、失效或未生效选项、缺证据、承诺与结构化事实不一致、只依据用户 requirements 的缺口表、责任字段、上线前人工检查表、澄清问题与 Markdown 矩阵。结论只由明示事实推导：缺失费用与零费用是两种事实，币种不换算，承诺与配置对不上先指出冲突，没有 requirements 就不替你判断是否满足客户期望；不联网查费率、不选承运商、不改结账页、不生成面单、不批退款。"
description: "面向 1-10 人电商团队与小商家的离线配送与退货选项矩阵工具。输入是一份 JSON：as_of（必须带时区偏移）、store（name / default_currency / sales_channels）、regions[]（region_id / country_or_area / postal_scope / channels[] / customer_segments[]）、delivery_options[]（option_id / regions[] / channels[] / provider / service_level / fee / currency / min_days / max_days / cutoff_time / tracking / pickup_type / evidence_ref / effective_from / effective_to）、return_options[]（return_id / regions[] / channels[] / window_days / fee_payer / dropoff_type / provider / condition_scope / evidence_ref / effective_from / effective_to）、promises[]（surface / region_id / text / 结构化时效费用退货窗）、可选 requirements[]（max_days / max_fee / min_return_window_days / currency）。逐项判定规则完全固定：记录不是对象记 INVALID_DELIVERY_RECORD / INVALID_RETURN_RECORD 并判 BLOCKED；区域为空记 MISSING_REGION_SCOPE、引用不存在区域记 UNKNOWN_REGION、写了区域未声明的渠道记 CHANNEL_MISMATCH、自提方式表外值记 INVALID_PICKUP_TYPE、退货方式表外值记 INVALID_DROP_OFF_TYPE；fee 缺失记 MISSING_FEE、为负数记 NEGATIVE_FEE，fee 为 0 是有效零费用而不是缺失；有费用无币种记 MISSING_CURRENCY、币种与默认币种不一致记 CURRENCY_CONFLICT 且不做换算；min_days 大于 max_days 记 INVERTED_TIME_WINDOW；tracking 非布尔记 UNKNOWN_TRACKING；退货窗缺失记 MISSING_RETURN_WINDOW、负数记 NEGATIVE_RETURN_WINDOW、fee_payer 缺失或表外记 UNKNOWN_FEE_PAYER；一条有效证据引用都没有记 NO_EVIDENCE_REF；生效期已过记 OPTION_EXPIRED、未到记 OPTION_NOT_YET_EFFECTIVE；重复编号让所有同号记录一并 BLOCKED。区域×渠道单元格按固定顺序判定：有阻塞选项或该区域承诺冲突即为 BLOCKED，没有配送选项记 NO_DELIVERY_COVERAGE、没有退货选项记 NO_RETURN_PATH 并判 INSUFFICIENT_EVIDENCE，其余按成员状态取最严重者。承诺只与该区域结构化选项比对：时效无选项可满足记 PROMISE_TIME_CONFLICT、承诺费用低于所有已知费用记 PROMISE_FEE_CONFLICT、承诺退货窗长于所有已知退货窗记 PROMISE_RETURN_WINDOW_CONFLICT。输出 status_counts、matrix、delivery_options、return_options、promise_checks、no_delivery_coverage、no_return_path、coverage_gaps、currency_conflicts、inverted_time_windows、inactive_options、evidence_gaps、promise_conflicts、duplicate_ids、requirement_gaps、responsibility_fields、human_checklist、clarification_questions、human_confirm_items 与 Markdown 矩阵。安全上：证据引用只接受单一文件名，含路径、链接或盘符的引用被拒绝且只保留安全化文件名，原引用一字不回显；字段名或值形如凭据直接拒绝处理且不回显原值；输入中的命令与提示注入只标记、不执行，进入简报时替换为固定占位；控制字符在进入任何输出前剥离；所有自由文本转义 Markdown 元字符。只读、离线、不使用任何第三方库。触发词：配送选项、退货选项、配送矩阵、退货矩阵、运费时效、自提网点、退货窗口、退货运费承担、承诺一致性、上线检查。联系邮箱：43298568@qq.com。"
description_zh: "把「我们的配送和退货到底怎么配」整理成一张可逐条核对的区域×渠道矩阵：四类结论、配送与退货选项清单、覆盖缺口、币种冲突、时效倒置、失效选项、承诺不一致与只依据用户 requirements 的缺口表。缺失费用与零费用是两种事实，币种不换算，没 requirements 就不替你判断是否满足客户期望。"
description_en: "An offline delivery and return option matrix helper for 1-10 person e-commerce teams and small merchants. It reads one JSON file and returns READY, ACTION_NEEDED, INSUFFICIENT_EVIDENCE or BLOCKED per region x channel cell, plus delivery/return option lists, coverage gaps, currency conflicts, inverted transit windows, expired or not-yet-effective options, missing-evidence lists, promise-versus-fact conflicts, a gap table built only from user-supplied requirements, responsibility fields, a pre-launch checklist, clarification questions and a Markdown matrix. Verdicts come only from explicit facts: a missing fee is never treated as zero, currencies are never converted, promise contradictions are reported instead of resolved, and without requirements the tool never claims customer expectations are unmet. It never queries carrier rates, selects a carrier, edits checkout, generates labels or approves refunds."
version: 1.0.0
author: 苏格
homepage: "https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-ecommerce-delivery-returns-option-matrix"
category: ecommerce-ops
tags: [配送选项, 退货选项, 配送矩阵, 运费时效, 自提网点, 退货窗口, 承诺一致性, 上线检查]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 电商配送与退货选项矩阵

一个 1-10 人的电商团队要在一个新区域开卖，最容易漏掉的不是商品，而是**配送和退货到底怎么配**：某个渠道其实没有配送方案、退货窗只写了大概、运费还带着另一个币种、结账页承诺的「次日达、包邮」在实际配置里根本做不到。**问题不是不知道这些事，而是没有一个地方把它们按区域×渠道摆在一起逐条核对。**

本技能做这件事：读一份 JSON，输出**可以逐条核对的区域×渠道矩阵**——每个单元格的结论、适用的配送与退货选项、无配送覆盖、无退货路径、币种不一致、时效倒置、失效/未生效、缺证据、承诺与结构化事实不一致的清单，加上只依据用户 `requirements` 的缺口表、责任字段、上线前人工检查表、澄清问题与 Markdown 矩阵。**脚本只读一个本地 JSON，不联网、不写文件、不查承运商费率、不改结账页、不生成面单、不批退款。**

## 输入与澄清

字段表、判定顺序与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，`references/sample-blocked.json` 是重复编号、缺失、拒绝与注入的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）、`store.default_currency` 与至少一个 `regions[]`。

**必须问清的关键点**：

1. **`as_of` 与默认币种不能省**。没有它们就无法判断生效期与币种冲突，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间或币种。
2. **缺失费用 ≠ 零费用**。`fee` 缺失记 `MISSING_FEE` 并停在证据不足；`fee: 0` 是有效的零费用，会被正常使用。
3. **币种不换算**。选项币种与 `store.default_currency` 不一致记 `CURRENCY_CONFLICT`；工具**不会**替你折算，也不会把不同币种相加。
4. **时效区间必须完整**。`min_days` / `max_days` 缺一记 `MISSING_TRANSIT_TIME`；`min_days > max_days` 记 `INVERTED_TIME_WINDOW`；负数时效也是硬矛盾。
5. **自提方式只有三类**。`pickup_type` 必须是 `door`（上门）/ `pickup_point`（网点）/ `store_pickup`（自提），表外值判 `BLOCKED`；退货方式同理（`door_pickup` / `dropoff_point` / `mail_back`）。
6. **退货窗与运费承担方是两件事**。`window_days` 缺失或为负、`fee_payer` 缺失或表外，都会单独记出来；**缺失不会被当成「商家承担」**。
7. **承诺对不上先指出冲突**。`promises[]` 里的时效/费用/退货窗只与该区域的结构化选项比对：没有选项能满足就记 `PROMISE_TIME_CONFLICT` 等，工具**不替你改文案，也不替你改配置**。
8. **覆盖缺口要显式列出**。某区域×渠道没有任何配送选项记 `NO_DELIVERY_COVERAGE`，没有退货选项记 `NO_RETURN_PATH`——**不说「应该没问题」**。
9. **没有 `requirements` 就不判断期望**。缺口表只依据你显式提供的要求；没有要求时输出明确声明，**不会替你宣称「不满足客户期望」**。
10. **只给文件名，不要给路径或链接**。`evidence_ref` 只接受单一文件名；含路径或链接的引用会被**拒绝**，输出里只保留安全化后的文件名，原引用不会回显。
11. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的区域与选项。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` / 默认币种 / 区域；`BLOCKED` 表示存在硬矛盾；`GAPS_FOUND` 表示存在待补齐、证据不足或目标缺口；`READY` 表示全部单元格可用。
4. 按 `matrix` 看每个区域×渠道的结论：`READY` / `ACTION_NEEDED` / `INSUFFICIENT_EVIDENCE` / `BLOCKED`。
5. 按 `no_delivery_coverage` / `no_return_path` / `coverage_gaps` 补覆盖；按 `currency_conflicts` / `inverted_time_windows` 修数据；按 `inactive_options` 决定续期或下架。
6. 按 `evidence_gaps` 与 `clarification_questions` 逐条追问；**不要自行填数，也不要把缺失当成 0**。
7. 按 `promise_conflicts` 处理对外文案：**先改文案或配置到一致，再上线**。
8. 按 `requirement_gaps` 看目标要求缺口；没有 `requirements` 时这一项为空且附带固定说明。
9. 按 `responsibility_fields` 给每个关键字段指定责任人；按 `human_checklist` 完成上线前人工核对。
10. 用 `human_confirm_items` 明确哪些决定必须由人工做（是否开卖、是否改文案、是否调政策），本工具不下判断。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何承运商、平台或店铺后台。
- **不自动执行**：不选择承运商、不改结账页或商品页、不生成面单、不批准退款、不取消订单、不联系任何人。
- **不换算币种**：币种冲突只报告，不折算、不跨币种合计。
- **只推导不发明**：结论只由输入中的明示事实推导；**不虚构费率、时效、退货窗、运费承担方或追踪能力**。
- **未知保持未知**：缺失费用不按 0、缺失退货窗不按 0 天、缺失 `fee_payer` 不按商家承担。
- **硬矛盾门禁**：时效倒置、负费用、负时效、负退货窗、表外自提/退货方式、重复编号、承诺冲突都判 `BLOCKED`，必须先人工修正。
- **覆盖门禁**：无配送覆盖与无退货路径分别列出，不静默略过。
- **要求门禁**：没有 `requirements` 时**不生成任何缺口结论**，并明确声明不据此判断客户期望。
- **确定性**：选项按编号、单元格按 `(region_id, channel)`、问题按生成顺序、状态汇总按固定顺序，同一输入输出逐字节一致。
- **文件名门禁**：`evidence_ref` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且**只输出安全化文件名**，原引用在任何字段都不出现。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注、承诺文案等自由文本，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `promises[1]/text`）；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是配送与退货配置的人工准备材料，不是承运商报价、时效承诺、结账页配置修改或法律/税务结论；真实费率、时效、可用性与消费者保护合规必须由人工核实并决定。
