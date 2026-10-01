---
name: suge-small-team-admin-backlog-priority-pack
slug: suge-small-team-admin-backlog-priority-pack
displayName: 小团队行政积压优先级准备包
display_name: 小团队行政积压优先级准备包
display_name_en: "Small-Team Admin Backlog Priority Pack"
summary: "把零散的行政待办变成一份可逐条核对的优先级准备单：每条给出 DO_TODAY / PLAN_THIS_WEEK / WAITING / DONE / INSUFFICIENT_EVIDENCE，加上今日交付板、本周承诺板、等待中与阻塞根因、已逾期与即将到期、无责任人清单、决策缺口、依赖成环与悬空依赖、今日已知工作量与 Markdown 行政积压准备单。优先级只由明示事实推导：无截止时间不伪造紧急，无责任人不自动分派，被未完成前置挡住的事项不会被当成可立即完成，工作量缺失不按 0；不自动发送、删除、建任务、分派或改写任何外部系统。"
description: "面向 3-20 人小团队与微型企业的离线行政积压优先级准备工具。输入是一份 JSON：as_of（必须带时区偏移）、可选的 team（name / timezone）与 items[]，每条事项可含 item_id、category、status（open / in_progress / waiting / done）、due_at（带时区偏移）、impacts（customer / cash / operations 三态布尔）、owner、depends_on[]、decision_needed、decision_owner（internal / external）、effort_hours、external_commitment（made_to / due_at）、evidence_ref、notes。判定规则完全固定：状态值缺失或非法记 UNKNOWN_ITEM_STATUS；缺截止时间记 NO_DUE_DATE、格式无效记 INVALID_DUE_AT，并且两者都不会被当成「今天必须做」，因为无截止时间不得伪造紧急；未指定责任人记 UNKNOWN_OWNER，工具不自动分派；前置依赖指向不存在的事项记 DANGLING_DEPENDENCY，依赖成环记 DEPENDENCY_CYCLE；只要还有未完成的前置事项（不以其自身状态推断）该事项记 WAITING 并给出未完成前置与根阻塞；事项自身标记 waiting、或决策方为 external 时记 WAITING；只有具备截止时间与责任人的事项才可能进入今日或本周板：已逾期、今日到期、或 3 天内到期且存在明示的客户/现金影响、或对外承诺在 24 小时内到期时记 DO_TODAY，其余记 PLAN_THIS_WEEK；status 为 done 的事项记 DONE 并排除在逾期与无责任人清单之外；effort_hours 缺失一律保持未知并计入 unknown_items，绝不按 0 相加。输出 status_counts、today_board、week_board、waiting_board、overdue_items、due_soon_items、no_owner_items、decision_gaps、blocking_chains（含 root_blockers）、dangling_dependencies、dependency_cycles、today_effort（known_total_hours / unknown_items / complete）、responsibility、clarification_questions 与 Markdown 行政积压准备单。安全上：证据引用只接受单一文件名，含路径、链接或盘符的引用被拒绝且只保留安全化文件名，原引用一字不回显；字段名或值形如凭据直接拒绝处理且不回显原值；输入中的命令与提示注入只标记、不执行，进入简报时替换为固定占位；控制字符在进入任何输出前剥离；所有自由文本转义 Markdown 元字符。只读、离线、不使用任何第三方库。触发词：行政积压、待办优先级、今日必做、本周承诺、阻塞依赖、等待事项、无责任人、决策缺口、工作量未知、行政准备单。联系邮箱：43298568@qq.com。"
description_zh: "把零散的行政待办变成一份可逐条核对的优先级准备单：每条给出今日/本周/等待/完成/证据不足结论，加上今日交付板、本周承诺板、阻塞根因、无责任人清单、决策缺口与依赖成环。无截止时间不伪造紧急，无责任人不自动分派，被前置挡住的不算可立即完成，工作量缺失不按 0。"
description_en: "An offline admin backlog prioritisation helper for 3-20 person teams. It reads one JSON file and returns DO_TODAY, PLAN_THIS_WEEK, WAITING, DONE or INSUFFICIENT_EVIDENCE per item, plus a today board, a this-week commitment board, blocking chains with root blockers, missing-owner and decision gap lists, dependency cycles and dangling references, and an explicit effort total that never counts a missing estimate as zero. Priority comes only from explicit facts: a missing due date never manufactures urgency, a missing owner never triggers auto-assignment, an item behind an unfinished prerequisite is never called immediately doable, and nothing is ever sent, deleted, created or reassigned."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-small-team-admin-backlog-priority-pack
category: small-team-admin-ops
tags: [行政积压, 待办优先级, 今日必做, 本周承诺, 阻塞依赖, 等待事项, 无责任人, 决策缺口]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 小团队行政积压优先级准备包

三个人的工作室，行政活全压在一个人身上：合同盖章、开票、社保基数、供应商结算、资质年检、月度对账——散在群聊、便签和脑子里。每周一开会被问「这周到底先干哪几件」，得到的答案是「都很急」。**问题不是没人干活，而是这些事项从来没有被按明示事实排过一次。**

本技能做这件事：读一份 JSON，输出**可以逐条核对的优先级准备单**——每条事项的结论、今日交付板、本周承诺板、等待中与阻塞根因、无责任人清单、决策缺口、依赖异常，以及一个**明确区分「已知」与「未知」**的工作量合计。**脚本只读一个本地 JSON，不联网、不写文件、不发送、不建任务、不派活、不改动任何外部系统。**

## 输入与澄清

字段表、判定规则、状态优先级与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，`references/sample-blocked.json` 是触发缺失、成环、拒绝的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）与 `items[]`。

**必须问清的关键点**：

1. **`as_of` 不能省**。没有它就无法判断逾期与到期，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **没有截止时间就不算紧急**。`due_at` 缺失记 `NO_DUE_DATE`、格式无效记 `INVALID_DUE_AT`，两者都**不会**进入今日板。**工具不替你编一个紧急度。**
3. **没有责任人不会自动分派**。`owner` 缺失记 `UNKNOWN_OWNER`，该事项停在证据不足，并出现在无责任人清单里。
4. **被未完成的前置挡住就不能算「今天能做」**。`depends_on` 里只要还有事项不是 `done`，本条就记 `WAITING`，并给出未完成前置与**根阻塞**（没有未完成前置的那一环）。
5. **依赖编号要真实存在**。指向不存在的事项记 `DANGLING_DEPENDENCY`；两条事项互相等对方记 `DEPENDENCY_CYCLE`——这两类都需要人工先拆解。
6. **只会按明示事实升级到今天**。已逾期、今日到期、3 天内到期且存在**明示**的客户或现金影响、或对外承诺在 24 小时内到期，才记 `DO_TODAY`；其余进本周板。
7. **工作量缺失不按 0**。`effort_hours` 缺失时保持未知，`today_effort.unknown_items` 会如实计数，`complete` 为 `false`——合计只包含已知项。
8. **`status` 取值固定**。`open` / `in_progress` / `waiting` / `done`（大小写不敏感，`in_progress` 等同 `open`）；其它值记 `UNKNOWN_ITEM_STATUS`。
9. **只给文件名，不要给路径或链接**。证据引用只接受单一文件名；含路径或链接会被**拒绝**，输出里只保留安全化后的文件名，原引用不会回显。
10. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的积压清单。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` 或没有事项；`GAPS_FOUND` 表示存在等待中或证据不足的事项；`READY` 表示全部事项都已分类完成（今日 / 本周 / 完成）。
4. 看每条事项的 `state`：`DO_TODAY` / `PLAN_THIS_WEEK` / `WAITING` / `DONE` / `INSUFFICIENT_EVIDENCE`。
5. 按 `today_board` 安排今天的顺序（已按紧急度固定排序），用 `today_effort` 判断今天是否装得下。
6. 按 `week_board` 对齐本周承诺；`overdue_items` 与 `due_soon_items` 是逾期与即将到期的独立视角。
7. 按 `waiting_board` 与 `blocking_chains` 找出**根阻塞**——先解决 `root_blockers`，被挡住的事项才会解开。
8. 按 `no_owner_items` 与 `decision_gaps` 补齐责任人与待决策项，这两类都需要人工拍板。
9. 按 `clarification_questions` 的 `Q-01` 顺序**原样**发给对应责任人确认；`dangling_dependencies` 与 `dependency_cycles` 必须先人工拆解。
10. 用 `human_confirm_items` 明确哪些决定必须由人工做，本工具不下判断。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何工单、邮件、财务或协作系统。
- **不外发**：不发送消息、不建任务、不派活、不审批、不付款、不删除任何原始材料。
- **只推导不发明**：优先级只由输入中的明示事实推导；**不虚构截止时间、影响、责任人或工作量**。
- **前置门禁**：`depends_on` 中仍有非 `done` 事项时一律 `WAITING`，**阻塞事项不会被当成可立即完成**。
- **责任门禁**：`owner` 缺失一律 `UNKNOWN_OWNER`，**工具不自动分派**。
- **工作量门禁**：`effort_hours` 缺失保持未知并计入 `unknown_items`，**合计永不按 0 计算**。
- **确定性**：事项按编号、待办板按紧急度与编号、问题按编号、状态汇总按 `ITEM_STATES` 全部显式固定，同一输入输出逐字节一致。
- **文件名门禁**：`evidence_ref` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且**只输出安全化文件名**，原引用在任何字段都不出现。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注、类别等自由文本，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `items[8]/notes`）；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是行政积压的人工优先级准备材料，不是绩效评价、劳动关系结论或财务审批；是否分派、对外承诺与付款由经营者人工决定。
