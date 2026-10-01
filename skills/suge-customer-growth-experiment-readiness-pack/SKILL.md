---
name: suge-customer-growth-experiment-readiness-pack
slug: suge-customer-growth-experiment-readiness-pack
displayName: 客户增长实验准备包
display_name: 客户增长实验准备包
display_name_en: "Customer Growth Experiment Readiness Pack"
summary: "把「想拉客户增长」变成一张可以逐条核对的实验准备板：每个实验给出 READY / ACTION_NEEDED / INSUFFICIENT_EVIDENCE / BLOCKED，加上实验卡、缺失事实、指标口径、预算上限、起止窗口、责任人、停止条件、外发草稿、不会外发清单、不可外发与不可投放声明、澄清问题和 Markdown 准备板。结论只由明示事实推导：没有基线不替你推算，目标不高于基线直接判定为硬矛盾，没有同意依据就不生成任何外发动作，币种不换算也不跨币种合计；不投放、不联系客户、不花钱、不写 CRM。"
description: "面向小商家与 3-20 人小团队的离线客户增长实验准备工具。输入是一份 JSON：as_of（必须带时区偏移）、可选的 business（name / timezone）与 experiments[]，每个实验可含 experiment_id、goal、audience、channel、offer、baseline、target、metric（name / definition）、budget_cap（amount / currency）、start_at、end_at、owner、outreach_required、consent_basis、evidence_refs[]、stop_conditions[]、notes。判定规则完全固定，逐条取第一个命中的分支：记录不是对象记 INVALID_EXPERIMENT_RECORD 并判 BLOCKED；channel 取值不在固定词表内记 INVALID_CHANNEL；start_at 与 end_at 都能解析且 end <= start 记 INVALID_EXPERIMENT_WINDOW；budget_cap.amount 为负数记 NEGATIVE_BUDGET；baseline 与 target 都能解析且 target <= baseline 记 TARGET_NOT_ABOVE_BASELINE，以上任一命中即 BLOCKED，因为硬矛盾无法由工具替你解决。没有硬矛盾时才检查缺失事实：无 goal / audience / offer、无基线、无目标、无指标名、无渠道、无责任人、无窗口、窗口缺时区或格式错误、无预算、有金额无币种、未说明是否需要外发，任一项都记 INSUFFICIENT_EVIDENCE 并生成待确认问题，绝不按 0 或「已完成」补齐。事实齐全后进入准备门禁：缺 stop_conditions 记 NO_STOP_CONDITIONS；outreach_required 为 true 但没有 consent_basis 时记 NO_CONSENT_BASIS，该实验只进「不会外发」清单，永远不会产生外发动作；窗口已结束记 WINDOW_IN_PAST，只标记不判定成败；指标缺 definition 记 METRIC_DEFINITION_MISSING 并记入提示。除指标口径缺说明外，任一准备门禁命中即 ACTION_NEEDED，否则为 READY。输出 status_counts、experiments、experiment_cards、ready_board、action_needed_board、blocked_board、insufficient_board、missing_facts、budgets_by_currency、outreach_plan（恒为 DRAFT_NOT_SENT）、do_not_outreach、refused_refs、human_confirm_items、clarification_questions、no_send_declaration、no_spend_declaration 与 Markdown 客户增长实验准备板。安全上：证据引用只接受单一文件名，含路径、链接或盘符的引用被拒绝且只保留安全化文件名，原引用一字不回显；字段名或值形如凭据直接拒绝处理且不回显原值；输入中的命令与提示注入只标记、不执行，进入简报时替换为固定占位；控制字符在进入任何输出前剥离；所有自由文本转义 Markdown 元字符。只读、离线、不使用任何第三方库。触发词：增长实验、实验准备、拉新实验、复购实验、渠道测试、基线目标、停止条件、同意依据、实验卡、增长准备板。联系邮箱：43298568@qq.com。"
description_zh: "把「想拉客户增长」变成一张可以逐条核对的实验准备板：每个实验给出就绪/待补齐/证据不足/阻塞四类结论，加上实验卡、缺失事实、指标口径、预算上限、起止窗口、责任人、停止条件、外发草稿、不会外发清单与 Markdown 准备板。没基线不推算，目标不高于基线判为硬矛盾，没同意依据不生成任何外发动作，币种不换算。"
description_en: "An offline customer-growth experiment readiness helper for small businesses and 3-20 person teams. It reads one JSON file and returns READY, ACTION_NEEDED, INSUFFICIENT_EVIDENCE or BLOCKED per experiment, plus experiment cards, missing facts, metric definitions, budget caps, windows, owners, stop conditions, draft-only outreach, a will-not-outreach list, clarification questions and a Markdown readiness board. Verdicts come only from explicit facts: a missing baseline is never estimated, a target at or below the baseline is a hard contradiction, no outreach action is ever produced without a stated consent basis, and currencies are never converted or summed across each other."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-customer-growth-experiment-readiness-pack
category: small-business-growth-ops
tags: [增长实验, 实验准备, 拉新实验, 复购实验, 渠道测试, 基线目标, 停止条件, 同意依据]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 客户增长实验准备包

一个小工作室或门店想拉客户增长，第一反应通常是「投点广告试试」或者「发一批消息」。真正卡住的地方不是想法，而是**没人把「到底想改变什么、拿什么当基线、什么时候算失败」写清楚**：目标随手写，基线靠感觉，预算没上限，跑完也说不清算没算成功。**问题不是没做实验，而是每个实验从来没有被按明示事实过一遍。**

本技能做这件事：读一份 JSON，输出**可以逐条核对的实验准备板**——每个实验的结论、实验卡、缺失事实、指标口径、预算上限、起止窗口、责任人、停止条件、外发草稿与「不会外发」清单，还有明确的**不可外发 / 不可投放声明**。**脚本只读一个本地 JSON，不联网、不写文件、不投放、不联系客户、不花钱、不写 CRM。**

## 输入与澄清

字段表、判定顺序与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，`references/sample-blocked.json` 是缺失、硬矛盾、拒绝与注入的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）与 `experiments[]`。

**必须问清的关键点**：

1. **`as_of` 不能省**。没有它就无法判断窗口是否已经结束，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **没有基线就不推算**。`baseline` 缺失记 `NO_BASELINE`、给了但不是数值记 `INVALID_BASELINE`，两种情况都停在证据不足；**工具绝不替你补一个基线**。
3. **目标不高于基线是硬矛盾**。`target` 与 `baseline` 都能解析且 `target <= baseline` 时判 `BLOCKED`（`TARGET_NOT_ABOVE_BASELINE`）——这不是实验，是一个需要人工先改的数字。
4. **没有同意依据就不生成外发动作**。`outreach_required` 为 `true` 但 `consent_basis` 为空时，该实验只进 `do_not_outreach`，**永远不会出现在 `outreach_plan` 里**；`consent_basis` 只登记你给出的依据文本，工具不核实其真实性。
5. **草稿永远是草稿**。`outreach_plan` 中的记录状态恒为 `DRAFT_NOT_SENT`，工具不发送、不投放、不建广告、不导名单到任何平台。
6. **币种不换算**。`budgets_by_currency` 按币种分别列示；有金额但没有币种时记 `UNKNOWN_BUDGET_CURRENCY`，该金额不进入任何合计。**缺失金额一律保持未知，不按 0 计算。**
7. **窗口必须带偏移**。`start_at` / `end_at` 不带时区偏移（如 `2026-10-10 09:00`）视为格式无效，记 `INVALID_WINDOW`；`end <= start` 记 `INVALID_EXPERIMENT_WINDOW` 并判 `BLOCKED`。
8. **渠道取值固定**。`channel` 必须来自固定词表（`content` / `email` / `sms` / `paid_ads` / `social` / `referral` / `in_store` / `phone` / `event` / `other`，大小写不敏感）；写了表外的值记 `INVALID_CHANNEL`。
9. **窗口结束只是事实**。`end_at` 早于 `as_of` 记 `WINDOW_IN_PAST` 并进 `ACTION_NEEDED`，工具**不判定实验成功或失败**。
10. **只给文件名，不要给路径或链接**。`evidence_refs` 只接受单一文件名；含路径或链接的引用会被**拒绝**，输出里只保留安全化后的文件名，原引用不会回显。
11. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的实验清单。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` 或没有实验；`BLOCKED` 表示存在硬矛盾实验；`GAPS_FOUND` 表示存在待补齐或证据不足的实验；`READY` 表示全部实验都可开始。
4. 看每个实验的 `state`：`READY` / `ACTION_NEEDED` / `INSUFFICIENT_EVIDENCE` / `BLOCKED`。
5. 按 `ready_board` 决定今天就能开始哪几个；`experiment_cards` 是每个实验的执行卡（目标、受众、钩子、指标口径、基线→目标、预算上限、窗口、停止条件）。
6. 按 `action_needed_board` 在开始前补齐：`NO_STOP_CONDITIONS` 先定什么时候停，`NO_CONSENT_BASIS` 先补同意依据，`WINDOW_IN_PAST` 先决定复盘还是重开。
7. 按 `insufficient_board` 与 `missing_facts` 逐条追问；**不要自行填数**。
8. 按 `blocked_board` 处理硬矛盾：改目标、改基线或改窗口，工具不会替你选一个。
9. 按 `clarification_questions` 的 `Q-01` 顺序**原样**发给对应责任人确认。
10. 用 `human_confirm_items` 明确哪些决定必须由人工做（是否做、是否算成功、是否花钱、是否外发），本工具不下判断。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何广告平台、CRM、名单或店铺后台。
- **不外发**：不发送消息、不投放、不建广告、不导名单、不邀请、不收款、不修改任何原始材料；`outreach_plan` 恒为草稿。
- **不花钱**：不绑定广告账号、不下单、不改预算；`budget_cap` 只作为输入事实登记。
- **只推导不发明**：结论只由输入中的明示事实推导；**不虚构基线、目标、指标口径、预算、责任人、同意依据或实验结果**。
- **基线门禁**：`baseline` 缺失或不可解析一律停在证据不足，**永不按 0 或按感觉补齐**。
- **硬矛盾门禁**：`target <= baseline`、窗口倒置、负数预算、表外渠道都判 `BLOCKED`，必须先人工修正。
- **同意门禁**：`outreach_required` 为 `true` 但没有 `consent_basis` 时**不产生任何外发动作**，只进 `do_not_outreach`。
- **币种门禁**：金额按币种分别列示，**不换算、不跨币种合计**；有金额无币种时该金额保持未知。
- **确定性**：实验按编号、看板按编号、问题按编号、状态汇总按 `EXPERIMENT_STATES` 全部显式固定，同一输入输出逐字节一致。
- **文件名门禁**：`evidence_refs` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且**只输出安全化文件名**，原引用在任何字段都不出现。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注、钩子等自由文本，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `experiments[1]/notes`）；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是增长实验的人工准备材料，不是增长预测、投放建议、财务结论或效果承诺；是否做、是否花钱、是否外发与是否算成功由经营者人工决定。
