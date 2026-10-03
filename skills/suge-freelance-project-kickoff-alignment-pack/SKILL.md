---
name: suge-freelance-project-kickoff-alignment-pack
slug: suge-freelance-project-kickoff-alignment-pack
displayName: 自由职业项目启动对齐包
display_name: 自由职业项目启动对齐包
display_name_en: "Freelance Project Kickoff Alignment Pack"
summary: "把「这个项目到底做什么、谁验收、谁给素材、多久同步、怎么改」整理成一张双方可逐条核对的启动对齐卡：每个交付物给出 READY / ACTION_NEEDED / WAITING_ON_CLIENT / INSUFFICIENT_EVIDENCE / BLOCKED，加上范围内/范围外、验收标准、审批人、客户输入、沟通节奏、升级路径、变更流程、依赖图与根阻塞、时间线冲突、责任缺口、澄清问题与人工启动检查表。缺失事实保持未知，不把「有交付物」当「已验收」、不把「有审批人」当「已批准」；所有外发内容只能是 DRAFT_NOT_SENT，未获得联系同意不生成任何可发送草稿。"
description: "面向自由职业者、工作室与 3-20 人小团队的离线项目启动对齐工具。输入是一份 JSON：as_of（必须带时区偏移）、project（project_id / name / start_at / target_delivery_at / timezone）、parties[]（party_id / role / decision_authority / contact_consent）、deliverables[]（deliverable_id / description / format / quantity / due_at / acceptance_criteria[] / approver_party_id / dependencies[] / out_of_scope[]）、communication（channel / cadence / response_target / urgent_escalation，全部来自用户事实）、approvals[]（approval_id / subject / owner_party_id / deliverable_id / status / due_at / evidence_ref）、client_inputs[]（input_id / kind / description / owner_party_id / deliverable_id / status / due_at / evidence_ref）、change_process（propose / estimate / approve / schedule）、notes、evidence_refs。判定顺序固定：记录不是对象记 INVALID_DELIVERABLE_RECORD 并判 BLOCKED；编号缺失记 MISSING_DELIVERABLE_ID、重复编号让所有同号记录一并 BLOCKED；审批人不在 parties 记 UNKNOWN_APPROVER；依赖指向不存在编号记 UNKNOWN_DEPENDENCY；依赖成环记 DEPENDENCY_CYCLE；到期时间无法解析记 INVALID_DUE_AT、早于启动记 DUE_BEFORE_START；这些都为硬阻塞。缺描述/格式/数量/验收标准/审批人/审批记录/范围外声明/到期时间，以及审批状态表外、客户输入状态表外，都记 INSUFFICIENT_EVIDENCE。审批 pending / not_requested 记 ACTION_NEEDED，rejected 也记 ACTION_NEEDED；客户输入未收到记 WAITING_ON_CLIENT。状态取最严重者：BLOCKED > INSUFFICIENT_EVIDENCE > WAITING_ON_CLIENT > ACTION_NEEDED > READY。项目级再聚合交付物状态与项目自身的沟通/变更/同意缺口。输出 alignment_card、deliverables、dependency_graph、root_blockers、dependency_cycles、waiting_on_client、pending_approvals、missing_facts、timeline_conflicts、responsibility_gaps、duplicate_ids、unknown_parties、refused_refs、injection_flagged、contact_consent、drafts（一律 DRAFT_NOT_SENT）、clarification_questions、human_checklist、human_confirm_items 与 Markdown 对齐卡。安全上：证据引用只接受单一文件名，含路径、链接或盘符的引用被拒绝且只保留安全化文件名，原引用一字不回显；字段名或值形如凭据直接拒绝处理且不回显；输入中的命令与提示注入不执行，命中时在 markdown_summary 中隐藏该值并给出精确路径。"
description_zh: "把「这个项目到底做什么、谁验收、谁给素材、多久同步、怎么改」整理成一张可逐条核对的启动对齐卡：五类状态、范围内/范围外、验收标准、审批人、客户输入、沟通节奏、升级路径、变更流程、依赖图与根阻塞、时间线冲突、责任缺口、澄清问题与人工检查表。缺失保持未知，有审批人≠已批准，外发内容只能是 DRAFT_NOT_SENT。"
description_en: "An offline kickoff alignment helper for freelancers, studios and 3-20 person teams. It reads one JSON file and returns READY, ACTION_NEEDED, WAITING_ON_CLIENT, INSUFFICIENT_EVIDENCE or BLOCKED for the project and for every deliverable, plus an alignment card (in/out of scope, deliverables, acceptance criteria, approvers, client inputs, communication cadence, escalation path, change process), a dependency graph with root blockers, a waiting-on-client list, missing facts, timeline conflicts, responsibility gaps, clarification questions, a human kickoff checklist and a Markdown card. Verdicts come only from explicit facts: an existing deliverable is never treated as accepted, an existing approver is never treated as approved, and messages are always DRAFT_NOT_SENT with no draft at all when contact consent is absent. It never signs contracts, accepts changes, promises price or deadlines, contacts clients, writes PM/CRM tools, uploads files or infers legal terms."
version: 1.0.0
author: 苏格
homepage: "https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-freelance-project-kickoff-alignment-pack"
category: freelance-ops
tags: [项目启动, 范围对齐, 验收标准, 审批人, 客户输入, 沟通节奏, 依赖图, 变更流程]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 自由职业项目启动对齐包

自由职业项目最容易翻车的地方不在执行，而在**启动**：范围没对齐、验收标准没写、审批人是谁不知道、客户素材什么时候给不清楚、沟通节奏靠感觉、变更来了没有流程。iHire 2026 对 2,250 名美国劳动者的调查把「期望/项目范围不清」与「沟通缺失或质量差」列为自由职业者最主要的两大挑战。**问题不是不知道这些事重要，而是没有一个地方把它们按交付物逐条摆出来、并且如实标出哪些还没确认。**

本技能做这件事：读一份 JSON，输出**双方可逐条核对的启动对齐卡**——项目与每个交付物的五类状态、范围内/范围外、验收标准、审批人、客户输入、沟通节奏、升级路径、变更流程、依赖图与根阻塞项、等待客户清单、缺失事实、时间线冲突、责任缺口、澄清问题、人工启动检查表、草稿（一律未发送）与 Markdown 对齐卡。**脚本只读一个本地 JSON，不联网、不写文件、不签合同、不接受变更、不承诺价格与工期、不联系客户、不写 PM/CRM、不上传文件、不推断法律条款。**

## 输入与澄清

字段表、判定顺序与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，五个交付物分别落在五种状态；`references/sample-blocked.json` 是重复编号、非对象记录、拒绝引用、注入与循环依赖的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）、`project.project_id`、`project.start_at`、`project.timezone`。

**必须问清的关键点**：

1. **`as_of` 与项目时区不能省**。没有它们无法判断当前时点与逾期，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **有交付物 ≠ 已验收**。每个交付物必须有非空的 `acceptance_criteria`，否则记 `MISSING_ACCEPTANCE_CRITERIA` 并停在证据不足。**范围外声明同样是必需字段**：没有 `out_of_scope` 记 `MISSING_OUT_OF_SCOPE`。
3. **有审批人 ≠ 已批准**。`approver_party_id` 只是指定了审批人；真正的结论来自 `approvals[]` 里的状态。只有 `approved` 才算通过；`pending` / `not_requested` / `rejected` 都推进 `ACTION_NEEDED`；有审批人却没有任何审批记录记 `MISSING_APPROVAL_RECORD`。
4. **审批状态是封闭词表**。只接受 `approved` / `pending` / `not_requested` / `rejected`；表外值记 `UNKNOWN_APPROVAL_STATUS`，**不猜**。
5. **客户输入状态是封闭词表**。只接受 `received` / `not_received` / `partial` / `waived`；只有 `received` 与 `waived` 算完成，缺省或表外一律按「未收到」进入等待客户，并记 `UNKNOWN_INPUT_STATUS`。
6. **状态取最严重者**。交付物与项目都用同一顺序聚合：`BLOCKED` > `INSUFFICIENT_EVIDENCE` > `WAITING_ON_CLIENT` > `ACTION_NEEDED` > `READY`。
7. **硬阻塞必须先人工修正**：重复编号、审批人或依赖指向不存在的编号、依赖成环、到期时间无法解析或早于项目启动、非对象记录。这些判 `BLOCKED`，工具**不会**替你自动挑选一条。
8. **时间线冲突单独列出**。到期晚于项目目标交付记 `DUE_AFTER_TARGET`（推进 `ACTION_NEEDED`）；目标交付早于启动记 `TARGET_BEFORE_START`（项目级硬阻塞）。
9. **沟通与变更全部来自你的事实**。工具**不内置任何行业默认**（不说「一般每周同步一次」）。缺渠道/节奏/响应目标/升级路径分别记缺口；`change_process` 缺整段或缺任一步骤都记缺口。
10. **不生成任何可发送内容**。所有草稿的状态恒为 `DRAFT_NOT_SENT`；**没有 `contact_consent: true` 就没有草稿**，明确拒绝（`false`）会被记录，未表态（缺失）按未知处理并列入检查表。
11. **只给文件名，不要给路径或链接**。`evidence_ref` 与 `evidence_refs` 只接受单一文件名；含路径、链接或盘符的引用会被**拒绝**，输出里只保留安全化文件名，原引用不会回显。
12. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的项目。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` / 项目编号 / 启动时间 / 时区；`BLOCKED` 表示存在硬矛盾；`GAPS_FOUND` 表示存在待补齐、证据不足或等待项；`READY` 表示项目与全部交付物就绪。
4. 再看 `project_state` 与 `status_counts`：`READY` / `ACTION_NEEDED` / `WAITING_ON_CLIENT` / `INSUFFICIENT_EVIDENCE` / `BLOCKED`。
5. 按 `deliverables[].state` 与 `deliverables[].findings` 逐条处理；按 `root_blockers` 先解根阻塞，按 `dependency_cycles` 打破循环。
6. 按 `waiting_on_client` 追客户输入与审批；按 `missing_facts` 与 `clarification_questions` 逐条追问；**不要自行填数，也不要把缺失当成 0**。
7. 按 `timeline_conflicts` / `responsibility_gaps` / `unknown_parties` 修正时间线、责任人与编号。
8. 按 `drafts`（全部 `DRAFT_NOT_SENT`）准备人工沟通；`contact_consent` 未同意者**没有草稿**，须先人工确认同意。
9. 用 `human_checklist` 完成启动前核对，用 `human_confirm_items` 明确哪些决定必须由人工做。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何项目管理或客户系统。
- **不自动执行**：不签合同、不接受范围/价格/工期变更、不承诺价格与交期、不自动联系客户、不写 PM/CRM、不上传文件、不推断法律条款。
- **只推导不发明**：结论只由输入中的明示事实推导；不虚构验收标准、审批结论、客户输入、沟通节奏或变更流程。
- **未知保持未知**：缺失验收不按通过、缺失审批记录不按批准、缺失客户输入不按已收到、缺失格式/数量不按 0。
- **状态门禁**：五类状态固定聚合顺序；硬阻塞（重复编号、悬空依赖、依赖成环、非法/倒置时间、非对象记录）必须先人工修正。
- **同意门禁**：`drafts` 恒为 `DRAFT_NOT_SENT`；`contact_consent` 不为 `true` 时**不生成任何草稿**。
- **确定性**：交付物按输入顺序、编号映射按首次出现、列表按固定顺序，同一输入输出逐字节一致。
- **文件名门禁**：`evidence_ref` / `evidence_refs` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且只输出安全化文件名，原引用在任何字段都不出现。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注、项目名、沟通/变更文本、交付物描述、验收/范围外、审批主题、客户输入描述；命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `notes`、`deliverables[1]/description`）；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是项目启动对齐的人工准备材料，不是合同、报价、工期承诺、法律或税务结论；范围、验收、审批与合规必须由双方人工确认。
