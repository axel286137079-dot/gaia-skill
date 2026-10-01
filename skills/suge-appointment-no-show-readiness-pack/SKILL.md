---
name: suge-appointment-no-show-readiness-pack
slug: suge-appointment-no-show-readiness-pack
displayName: 预约服务爽约风险准备包
display_name: 预约服务爽约风险准备包
display_name_en: "Appointment No-Show Readiness Pack"
summary: "把「这个预约到底准备好没有」做成一份可逐条核对的人工准备板：逐条给出 READY / ACTION_NEEDED / WAITING_ON_CUSTOMER / INSUFFICIENT_EVIDENCE / BLOCKED，加上今日人工确认清单、不可联系清单、未发送的确认草稿、前置准备缺口、备案时段表与 Markdown 预约准备板。预约存在不等于已确认；未取得联系同意不会生成任何可发送动作；未知提醒状态不会被当成已发送；过期预约只标 WINDOW_IN_PAST，绝不判定客户爽约；不自动联系、改期、取消、收款或写入日历。"
description: "面向本地服务小商家（美容美发、维修安装、健康理疗、家政、教培等 1-20 人门店）的离线预约爽约风险准备工具。输入是一份 JSON：as_of（必须带时区偏移）、可选的 business（name / timezone / no_show_policy）与 appointments[]，每条预约可含 appointment_id、service_name、mode（on_site / in_store / remote）、start / end / timezone、customer（name / contact_channel / contact_consent / confirmed）、reminders[]（reminder_id / channel / state / sent_at / receipt_basename）、preparation[]（item_id / label / required / ready）、policy_acknowledged、policy_ref、standby（start / end）、owner、notes。判定规则完全固定：预约存在不等于已确认，客户确认状态未知一律进入待确认问题；联系同意保持三态，只有显式 true 才可能生成动作，显式 false 进不可联系清单且不生成任何可发送动作；reminders 缺失或为空记 NO_REMINDER_EVIDENCE，state 不在 sent / delivered 内记 UNKNOWN_REMINDER_STATE，未知提醒状态永远不算已发送；reminders / preparation 未提供时记未知而不是默认完成；预约开始早于 as_of 只标 WINDOW_IN_PAST 并转人工决定，绝不自动标记客户爽约；同一责任人两条预约时窗重叠记 OWNER_DOUBLE_BOOKED 并阻塞；mode 缺失记未知、取值非法记 INVALID_MODE 并阻塞；policy_acknowledged 为 false、必需前置准备 ready 为 false、或完全没有已发提醒证据时记 ACTION_NEEDED；未指定责任人记未知。输出逐条结论、status_counts、today_confirmation_list（今日人工确认清单，只含真正需要动作的预约）、contact_actions（全部为 DRAFT_NOT_SENT 的未发送草稿）、do_not_contact（不可联系清单）、preparation_gaps（前置准备缺口）、standby_windows（备案时段表）、owner_conflicts、responsibility、clarification_questions、Markdown 预约准备板。安全上：回执等附件引用只接受单一文件名，含路径、链接或盘符的引用被拒绝且只保留安全化文件名，原引用一字不回显；字段名或值形如凭据直接拒绝处理且不回显原值；输入中的命令与提示注入只标记、不执行，进入简报时替换为固定占位；控制字符在进入任何输出前剥离；所有自由文本转义 Markdown 元字符。只读、离线、不使用任何第三方库。触发词：爽约风险、预约准备、预约确认、到店确认、提醒状态、联系同意、不可联系清单、备案时段、前置准备缺口、预约准备板。联系邮箱：43298568@qq.com。"
description_zh: "把预约到期前的准备做成一份可逐条核对的人工准备板：逐条给出 READY / ACTION_NEEDED / WAITING_ON_CUSTOMER / INSUFFICIENT_EVIDENCE / BLOCKED，加上今日人工确认清单、不可联系清单、未发送的确认草稿、前置准备缺口与备案时段表。预约存在不等于已确认；未取得联系同意不生成任何可发送动作；未知提醒状态不当已发送；过期预约不判定爽约。"
description_en: "An offline no-show readiness checker for small local-service businesses. It reads one JSON file and, for every appointment, returns READY, ACTION_NEEDED, WAITING_ON_CUSTOMER, INSUFFICIENT_EVIDENCE or BLOCKED together with today's human checklist, a do-not-contact list, unsent confirmation drafts, preparation gaps, a standby-window table and a Markdown board. An appointment is never treated as confirmed, consent is tri-state so no sendable action exists unless contact consent is explicitly true, an unknown reminder state never counts as sent, an overlapping window for one owner blocks, and a past window is only flagged WINDOW_IN_PAST - never labelled a no-show. Attachment-style references are accepted as file basenames only, and credential-shaped input is rejected without echo."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-appointment-no-show-readiness-pack
category: local-service-ops
tags: [爽约风险, 预约准备, 预约确认, 到店确认, 提醒状态, 联系同意, 不可联系清单, 备案时段]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 预约服务爽约风险准备包

排班表上写着「周三 9 点，李女士，空调清洗」。到了周三早上才发现：她其实一直没回确认、提醒短信到底发没发出去没人知道、备用备件还在仓库、而这条预约有没有跟另一位客人撞车也没人核过。**预约存在从来不等于已经准备好，而这些事实散在聊天记录、短信后台和排班表里，从来没有人逐条对过。**

本技能做这件事：读一份 JSON，输出**到期前可以逐条核对的人工准备板**——每条预约的结论、今日人工确认清单、不可联系清单、未发送的确认草稿、前置准备缺口、备案时段表与责任人。**脚本只读一个本地 JSON，不联网、不写文件、不联系客户、不改期、不取消、不收款、不写入日历。**

## 输入与澄清

字段表、判定规则、状态优先级与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，`references/sample-blocked.json` 是触发阻塞与拒绝的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）与 `appointments[]`。

**必须问清的关键点**：

1. **`as_of` 不能省**。没有它就无法判断预约是否已过期，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **预约存在不等于已确认**。`customer.confirmed` 缺失时记 `UNKNOWN_CUSTOMER_CONFIRMATION` 并追问；**不会因为排班表上有这条就当成已确认**。
3. **联系同意是三态，只有显式 `true` 才可能生成动作**。`contact_consent` 为 `false` 的预约进**不可联系清单**，任何草稿都不会生成；为 `null` 时先追问同意情况。
4. **未知提醒状态不算已发送**。`reminders[].state` 只有 `sent` / `delivered` 才算已发证据；`unknown`（或写错的值）记为 `UNKNOWN_REMINDER_STATE` 并追问。整份 `reminders` 缺失或为空记 `NO_REMINDER_EVIDENCE`。
5. **预约时间已过不等于客户爽约**。过期只写 `WINDOW_IN_PAST` 与一条「需人工决定处理方式」，**本工具不判定爽约**，也不产生任何客户信用结论。
6. **同一责任人时段重叠会被拦下**。两条预约若属于同一 `owner` 且时窗重叠，双方都记 `OWNER_DOUBLE_BOOKED` 并阻塞；工具不替你改期，只标出来。
7. **模式取值固定**。`mode` 只能是 `on_site` / `in_store` / `remote`；缺失记未知，取值非法直接阻塞（`INVALID_MODE`）。
8. **只给文件名，不要给路径或链接**。提醒回执一类的引用只接受单一文件名；含路径或链接会被**拒绝**，输出里只保留安全化后的文件名，原引用不会回显。
9. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。
10. **未知就是未知**。确认状态、同意、提醒、前置准备、政策知情、责任人或时区缺失一律标未知并追问，**不默认为已完成**。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的预约。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` 或没有预约；`BLOCKED` 表示至少一条预约确定阻塞；`GAPS_FOUND` 表示没有确定阻塞但存在待办或未知；`READY` 表示全部预约已就绪。
4. 看每条预约的 `status`：`READY` / `ACTION_NEEDED` / `WAITING_ON_CUSTOMER` / `INSUFFICIENT_EVIDENCE` / `BLOCKED`。
5. 按 `today_confirmation_list` 逐条处理今日动作（**只列真正需要动作的预约**；`WAITING_ON_CUSTOMER` 不会出现在这里）。
6. `contact_actions` 全部是 `DRAFT_NOT_SENT` 的**未发送草稿**，由人工决定是否发出。
7. 按 `do_not_contact` 排除**不可联系**的预约；`preparation_gaps` 补齐前置准备。
8. 用 `standby_windows` 看出哪些预约已有备案时段、哪些未备案、哪些备案时段与预约本身重叠。
9. 看 `owner_conflicts` 与 `clarification_questions`：前者是责任人时段冲突，后者按 `Q-01` 顺序**原样**发给对应责任人确认。
10. 用 `human_confirm_items` 明确哪些结论必须由人工确认，本工具不下判断。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何日历、短信或预约系统。
- **不接触客户**：不联系客户、不发提醒、不改期、不取消、不收款；只输出准备状态与未发送草稿。
- **不判定爽约**：过期预约只记 `WINDOW_IN_PAST`，不输出任何爽约、信用或黑名单结论。
- **同意门禁**：只有 `contact_consent` 显式为 `true` 的预约才可能生成 `contact_actions`；显式 `false` 进 `do_not_contact`；未知先追问。
- **提醒单调**：`state` 不在 `sent` / `delivered` 内一律视为未知，**未知永远不当已发送**。
- **不猜测**：确认状态、同意、提醒、前置准备、政策知情、责任人、时区、备案时段缺失一律保持未知并转成待确认问题，**不默认为已完成**。
- **确定性**：预约按编号、待办按编号与原因、问题按编号、状态汇总按 `APPT_STATES` 全部显式固定，同一输入输出逐字节一致。
- **文件名门禁**：回执类引用的 `basename` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且**只输出安全化文件名**，原引用在任何字段都不出现。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注、服务名等自由文本，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `appointments[0]/notes`）；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是预约到期前的人工准备材料，不是爽约判定、客户信用评价或收入结论；是否改期、取消、收费或终止服务由经营者依预约政策与当地规定人工决定。
