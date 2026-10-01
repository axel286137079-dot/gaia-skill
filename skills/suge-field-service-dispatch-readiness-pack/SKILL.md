---
name: suge-field-service-dispatch-readiness-pack
slug: suge-field-service-dispatch-readiness-pack
displayName: 上门服务出发前工单准备包
display_name: 上门服务出发前工单准备包
display_name_en: "Field Service Dispatch Readiness Pack"
summary: "把出发前能不能派工的判断做成一份可逐条核对的准备板：每张工单给出 READY_FOR_DIAGNOSIS / READY_FOR_PLANNED_WORK / BLOCKED / INSUFFICIENT_EVIDENCE，加上人员技能覆盖、零件证据链、准入/范围/批准缺口、出发前交接单和人工确认项。预约不等于准备完成，ordered 不等于 received/reserved，诊断工单不会因为没有维修零件被错误阻塞；不判断技术安全、许可或资质，不自动派工、改期、联系客户或下单。"
description: "面向上门服务、安装维修与保养团队（3–20 人）的离线派工准备核对工具。输入是一份 JSON：as_of（必须带时区偏移）与 work_orders[]，每张工单可含 work_order_id、visit_type（diagnosis / installation / followup / maintenance）、appointment（start / end / timezone）、site（contact / access_notes / access_confirmed）、approved_scope、approval（scope_approved / approved_by / approval_ref）、equipment[]、required_skills[]、assigned_technician（technician_id / skills[] / available_windows[]）、parts[]（part_id / state / required，state 取 ordered / received / inspected / reserved / unknown）、tools[]、documents[]（只接受单一文件名）、owner、due_at。判定规则完全固定：只有 reserved 才满足安装与保养的零件门禁，ordered / received / inspected 一律记为尚未预留并阻塞；诊断与回访不看零件预留，所以诊断工单不会被未准备好的维修零件错误阻塞；所需技能未被技术员技能覆盖记技能缺口并阻塞，技能清单缺失记未知；预约时窗必须落在技术员的可用时段内，否则阻塞，可用时段缺失记未知；现场准入未确认、范围未批准、必需工具或文档不可用一律阻塞；同一技术员两张工单时段重叠记为重复派工并阻塞；预约时窗缺失或时间格式无效、时区缺失、准入/批准/技能/零件状态未知一律进入待确认问题而不是猜一个值；预约时间早于 as_of 记为过期并阻塞。输出每张工单的结论、人员技能覆盖矩阵、零件证据链（ordered / received / inspected / reserved 四列独立）、准入/范围/批准缺口表、出发前交接单、人工确认项、责任与截止表、待确认问题、重复派工清单与 Markdown 派工准备板。安全上：附件字段只接受单一文件名，含路径、链接或盘符的引用被拒绝且只保留安全化文件名，原引用一字不回显；字段名或值形如凭据直接拒绝处理且不回显原值；输入中的命令与提示注入只标记、不执行，进入简报时替换为固定占位；控制字符在进入任何输出前剥离；所有自由文本转义 Markdown 元字符。只读、离线、不使用任何第三方库。触发词：派工准备、出发前检查、工单准备、上门服务、现场服务、技师技能、零件预留、准入确认、工单是否可派、派工交接单。联系邮箱：43298568@qq.com。"
description_zh: "把上门服务出发前的派工准备做成一份可逐条核对的准备板：每张工单给出可诊断/可施工/阻塞/证据不足结论，加上人员技能覆盖、零件证据链、准入与批准缺口、出发前交接单和人工确认项。ordered 不等于 reserved，诊断工单不会因为没有维修零件被阻塞，未知值保持未知。"
description_en: "An offline pre-departure readiness checker for small field-service teams. It reads one JSON file and, for every work order, returns READY_FOR_DIAGNOSIS, READY_FOR_PLANNED_WORK, BLOCKED or INSUFFICIENT_EVIDENCE together with skill coverage, a four-step parts evidence chain, access/scope/approval gaps, a departure checklist, human-confirmation items and a Markdown dispatch board. Only `reserved` satisfies the parts gate, so `ordered` parts block planned installation and maintenance work while a diagnosis visit is never blocked for missing repair parts; overlapping appointments for one technician block; unknown facts become questions instead of guesses; credential-shaped input is rejected without echo; attachments are handled as file basenames only."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-field-service-dispatch-readiness-pack
category: field-service-ops
tags: [派工准备, 出发前检查, 工单准备, 上门服务, 技师技能, 零件预留, 准入确认, 派工交接单]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 上门服务出发前工单准备包

派工单上写着「9 点上门安装」，出发才发现：支架还在采购流程里、现场门禁没报备、这台的型号需要持证电工而派去的人只有普通安装资质。**预约从来不等于准备完成，而准备状态本来散在采购、客服和调度三个系统里，只是没人把它逐条核对过。**

本技能做这件事：读一份 JSON，输出**出发前可以逐条核对的准备板**——每张工单的结论、人员技能覆盖、零件证据链、准入/范围/批准缺口、出发前交接单与人工确认项。**脚本只读一个本地 JSON，不联网、不写文件、不派工、不改期、不联系客户、不下单。**

## 输入与澄清

字段表、判定规则、状态优先级与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，`references/sample-blocked.json` 是触发阻塞的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）与 `work_orders[]`。

**必须问清的关键点**：

1. **`as_of` 不能省**。没有它就无法判断预约是否已过期，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **零件状态只有 `reserved` 算准备好**。`ordered`、`received`、`inspected` 都会被记为**尚未预留并阻塞安装/保养**。`ordered` 不等于 `received`，更不等于 `reserved`。
3. **诊断工单不看零件预留**。上门诊断时维修零件本来就还不确定，所以诊断与回访工单**不会因为没有预留零件被阻塞**；这一点是刻意的。
4. **技能清单缺失不是零**。技术员 `skills` 未提供时记 `UNKNOWN` 并转成待确认问题，**不会当成「没有技能」直接判缺口**。
5. **可用时段决定能否到场**。预约时窗必须完整落在技术员某一段 `available_windows` 内；未提供可用时段记未知并追问。
6. **同一人两张工单时间重叠会被拦下**。工具不替你改期，只把重复派工标出来由你决定。
7. **只给文件名，不要给路径或链接**。附件引用只接受单一文件名；含路径或链接会被**拒绝**，输出里只保留安全化后的文件名，原引用不会回显。
8. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。
9. **未知就是未知**。准入、批准、设备事实、时区缺失一律标未知并追问，**不默认为「已完成」**。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的工单。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` 或没有工单；`BLOCKED` 表示至少一张工单存在确定阻塞；`GAPS_FOUND` 表示没有确定阻塞但存在未知字段；`READY` 表示全部工单可派。
4. 看每张工单的 `status`：`READY_FOR_DIAGNOSIS` / `READY_FOR_PLANNED_WORK` / `BLOCKED` / `INSUFFICIENT_EVIDENCE`。
5. 看 `parts_evidence`：`ordered / received / inspected / reserved` 四列是独立的，**只有 `reserved` 为「是」才算零件到位**。
6. 看 `coverage_matrix`：`missing` 非空即为技能缺口，需要换人或补技能。
7. 看 `access_scope_approval_gaps` 与 `double_bookings`：这两类必须在出发前处理。
8. 看 `departure_checklist` 与 `responsibility`：作为出发前的交接核对表。
9. 看 `clarification_questions`：按 `Q-01` 顺序**原样**发给对应责任人确认。
10. 用 `human_confirm_items` 明确哪些结论必须由人工确认，本工具不下判断。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何平台或调度系统。
- **不派工**：不派工、不改期、不联系客户、不下单、不修改任何工单；只输出准备状态与缺口。
- **不判定**：不判断技术安全、作业许可、资质或合规；这些一律进入 `human_confirm_items` 由责任单位确认。
- **不猜测**：准入、批准、技能、可用时段、零件状态、时区缺失一律保持未知并转成待确认问题，**不默认为已完成**。
- **零件单调链**：`reserved` 蕴含 `inspected` 蕴含 `received` 蕴含 `ordered`，因此 `ordered` 单独存在**永远不满足门禁**。
- **诊断不误阻塞**：`diagnosis` 与 `followup` 工单不因维修零件未预留而被阻塞。
- **确定性**：工单按编号、问题按编号、字段名与状态汇总顺序全部显式固定，同一输入输出逐字节一致。
- **文件名门禁**：附件 `basename` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且**只输出安全化文件名**，原引用在任何字段都不出现。
- **阻塞即停**：技能缺口、准入未确认、范围未批准、必需工具/文档不可用、零件未预留、重复派工一律整体 `BLOCKED`，不做静默兜底。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注、范围说明、现场说明等自由文本，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `work_orders[0]/site/access_notes`）；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是派工出发前的信息核对材料，不是技术安全、许可、资质或合规结论；现场准入与作业要求以责任单位和主管部门的规定为准。
