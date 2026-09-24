---
name: suge-retail-shift-handover-pack
slug: suge-retail-shift-handover-pack
display_name: 门店交接班准备包
display_name_en: "Retail Shift Handover Pack"
summary: "把交班时的口头交代整理成一份可逐条核对的交接板：按立即处理 / 下一班 / 待负责人确认 / 仅记录分组，标出未完成、责任人、截止时间、证据引用、重复编号、状态冲突、金额与数量未知、设备停机，并给出开班确认清单与交接单。现金差异一律进立即处理，跨币种不合并，未知不按 0 处理，疑似凭据直接拒绝。不登录 POS 或排班系统、不算工资、不判责任、不外发消息。"
description: "面向零售门店、餐饮、烘焙、美业、宠物服务、工作室等 3 到 20 人轮班小团队的离线交接班整理工具：把交班时散在聊天、纸条和口头交代里的事项，一次性整理成可以逐条核对、可以打印签字的交接板。输入是带时区偏移的 as_of、可选 store（含 IANA 时区）、可选 shift（班次起止）、可选 equipment（资产与状态）与 items[]（事项记录：item_id / summary / category / status / owner / due_at / asset_id / evidence_refs / amount / quantity / notes）。判定规则完全固定且可复核：班次窗口按绝对时刻比较，ends_at 早于或等于 starts_at 判 INVALID_SHIFT_WINDOW 并使整体 BLOCKED，跨午夜按门店时区的本地日期识别（22:00 到次日 06:00 是合法班次，时长 8.00 小时）；分板路由按固定顺序首个命中生效，记录无效或状态冲突进待负责人确认，status 为 done 进仅记录，已逾期或关联设备停机或属于现金类进立即处理，status 为 unknown 或责任人缺失进待负责人确认，其余进下一班；同一 item_id 状态全同记重复（仅首次进板），状态不一致记冲突并使整体 BLOCKED，工具不替你挑状态。金额用 Decimal 计算保留两位、允许负数、消掉负零；amount 键不存在表示该事项不涉及金额，键存在而 value 为 null 才记 AMOUNT_UNKNOWN；币种缺失不进任何合计，totals_by_currency 只按币种分组，永不产生跨币种总额；未知金额与未知数量分别计数，绝不按 0 处理。安全上：字段名或值形如凭据（sk- / AKIA / ghp_ / JWT / PEM 私钥头）直接拒绝处理且不回显原值；提示注入仅在同一句内同时出现动作词与目标词时命中，只标位置不执行，进入简报时替换为固定占位；控制字符在进入任何输出前剥离；所有自由文本转义 Markdown 元字符，无法伪造标题、列表、链接或表格。输出 JSON 另带 markdown_summary 交接单。只读、离线、不使用任何第三方库。触发词：交接班、交班、接班、交接单、交接板、开班确认、班次交接、现金短款、设备停机、未完成事项、责任交接。联系邮箱：43298568@qq.com。"
description_zh: "把交班时的口头交代整理成可逐条核对的交接板：立即处理 / 下一班 / 待负责人确认 / 仅记录分组，标出未完成、责任人、截止、证据引用、重复编号、状态冲突、金额与数量未知、设备停机，并给出开班确认清单。现金进立即处理，跨币种不合并，未知不按 0 处理，疑似凭据拒绝。"
description_en: "An offline handover-board builder for 3 to 20 person shift-based store teams: it turns the notes, chat messages and verbal briefings of a shift change into a checkable board grouped into handle-now, next-shift, confirm-with-owner and record-only, with unfinished items, owners, due times, evidence references, duplicate ids, conflicting statuses, unknown amounts and quantities, equipment downtime, an opening checklist and a Markdown handover sheet. Cash differences always go to handle-now, currencies are never merged, unknown is never turned into zero, and credential-shaped input is rejected without echo. It never logs in to a POS or rostering system, never computes payroll, never assigns blame and never sends messages."
version: 1.0.1
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-retail-shift-handover-pack
category: retail-ops
tags: [交接班, 交接单, 开班确认, 现金短款, 设备停机, 未完成事项, 轮班, 门店运营]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 门店交接班准备包

轮班门店最常见的事故不是忙不过来，而是**交班那一刻丢了一句话**：「三号柜的蛋糕要转走」只在口头说了，「现金少了 120 块」只在纸条上写了一行，「客人说积分没到账」只留在微信里。接班的人不知道，下一班就变成投诉。

真正的难点不是写作，是**把散落的事项收成一份能逐条核对、能签字、能追责到具体班次的交接板**。本技能做这件事：读一份 JSON，输出分好组的交接板、缺什么字段、哪些编号重复、哪些状态互相矛盾、金额分币种各是多少、开班前要确认哪几件事。**脚本不登录 POS 或排班系统、不读照片、不算工资、不判责任、不向外发消息。**

## 输入与澄清

字段表、路由规则、状态优先级与安全边界见 @references/guide.md（同目录 `references/sample.json` 与 `references/sample-blocked.json` 是可复现样例，输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）与 `items[]`。

**必须问清的关键点**：

1. **`as_of` 不能省**。没有它就无法判断「已过截止」，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **责任人用脱敏工号，不要写姓名和电话**。本工具只需要「谁跟进」，不需要个人信息。
3. **不要把登录凭据填进来**。输入里出现密码、令牌一类字段会被**整体拒绝**，并且不会回显该内容。
4. **未知就是未知**。盘点的空格子、没填的到货数量，请留空；留空会被标 `AMOUNT_UNKNOWN` / `QUANTITY_UNKNOWN` 并计入待确认问题。填 0 会让下一班以为「真的是零」。
5. **不同币种请分开写**。工具只分币种合计，不会替你折算。
6. **跨午夜班次请写清楚两个完整时间戳**（含日期与偏移）。`22:00 → 次日 06:00` 是合法班次，不会被判错。
7. **同一件事被两班各录一次是常态**。请保留两行，工具会识别重复与冲突，而不是静默去重。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的门店。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`BLOCKED` 表示班次窗口非法、存在状态冲突或无效记录；`INPUT_INCOMPLETE` 表示缺 `as_of` 或没有事项；`GAPS_FOUND` 表示可用但有字段待补。
4. 看 `board.immediate`：这几件事必须在交班前落地。
5. 看 `conflicts` 与 `duplicate_item_ids`：先合并重复，再谈跟进。
6. 看 `items[].review_flags`：优先处理 `OWNER_MISSING`、`AMOUNT_UNKNOWN`、`INVALID_DUE_AT`。
7. 看 `totals_by_currency`：**分币种**核对，不要把不同币种相加。
8. 看 `clarification_questions`：按 `Q-01` 顺序**原样**发给交班人确认。
9. 用 `opening_checklist` 与 `markdown_summary` 作为开班前检查表与交接单。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何业务系统。
- **不外发**：不给员工或顾客发任何消息，不创建任务，不修改库存。
- **不判定**：不做责任认定、不做工资或绩效核算、不判定现金差额归属；只把事项与证据摆到交接板上。
- **不猜测**：金额、数量、币种、责任人和截止时间缺失一律保持未知并转成待确认问题，**不默认为 0**。
- **确定性**：排序全部显式固定（分板顺序、事项输入顺序、分组键、字段名、问题编号），同一输入输出逐字节一致。
- **跨币种不合并**：只按币种分组，不生成跨币种总额，不静默折算。
- **时段正确**：班次与截止时间一律按显式时区偏移比较；门店时区无法解析时记 `TIMEZONE_UNRESOLVED` 并追问，而不是用本机时区冒充。
- **脏数据不中断**：重复编号、状态冲突、非法类别或状态、非法截止时间、负金额都单列标记，不中断整批处理，也不静默修复原始数据。
- **提示注入逐字段检测、位置精确、低误报**：覆盖 `items[].notes`、`items[].summary` 等自由文本叶子，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `items[3]/notes`）。检测要求「动作词 + 目标词」**同句共现**，因此「请忽略小额尾差，财务已核销。」这类正常备注不会被误标。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」；结构化 JSON 保留原始语义并在 `injection_flagged` 中带风险标记。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是交接信息核对材料，不是责任判定或合规结论；现金与库存差异的最终认定以门店制度、当面清点与票据为准。
