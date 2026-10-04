---
name: suge-lead-intake-readiness-priority-pack
slug: suge-lead-intake-readiness-priority-pack
displayName: 销售线索接入完整度与下一步优先包
display_name: 销售线索接入完整度与下一步优先包
display_name_en: "Lead Intake Readiness & Priority Pack"
summary: "把每条匿名线索的接入事实、缺口、范围适配证据、时效与容量整理成一张人工复核优先卡：每条线索给出 READY_FOR_HUMAN_FOLLOWUP / NEEDS_CLARIFICATION / OUT_OF_DECLARED_SCOPE / WAITING_FOR_CONSENT / INSUFFICIENT_EVIDENCE / BLOCKED，加上接入完整度清单、范围适配证据、缺失事实、时间冲突、拒绝字段、危险引用、注入标记、人工复核优先带、下一步准备卡、澄清问题与人工检查表。缺失事实保持未知，预算未提供不等于低价值，绝不输出客户价值评分，绝不自动联系或淘汰客户，草稿恒为 DRAFT_NOT_SENT 且仅在明确同意时生成。"
description: "面向小商家、工作室与 3-20 人小团队的离线销售线索接入整理工具。输入是一份 JSON：as_of（必须带时区偏移）、business（business_id / timezone / product_scope / service_area / out_of_scope / channels，不得内置默认）、leads[]（仅匿名 lead_id 与 source / received_at / requested_outcome / product_or_service / service_area / deadline / budget_status / decision_timing / evidence_refs / contact_consent）、qualification_rules（范围适配、区域适配、最低必需事实、时效规则与优先顺序）、capacity（人工可处理数量与可用时段，仅用于排队建议）、notes、evidence_refs。判定顺序固定：非对象记录记 INVALID_LEAD_RECORD，编号缺失记 MISSING_LEAD_ID、重复编号让所有同号记录一并 BLOCKED，接收时间无法解析记 INVALID_RECEIVED_AT、晚于 as_of 记 RECEIVED_IN_FUTURE，截止时间无法解析记 INVALID_DEADLINE、早于接收时间记 DEADLINE_BEFORE_RECEIVED，个人信息或受保护属性字段记 PERSONAL_DATA_REFUSED / SENSITIVE_ATTRIBUTE_REFUSED，这些都为硬阻塞。缺需求/产品/来源/接收时间记证据不足；产品命中明确不服务、超出声明产品或超出声明区域、边界地区记 OUT_OF_DECLARED_SCOPE；未同意或同意未知记 WAITING_FOR_CONSENT；预算未提供、缺服务区域或区域不明、缺截止时间、缺决策时间点、截止时间已过记 NEEDS_CLARIFICATION。状态取最严重者（BLOCKED > INSUFFICIENT_EVIDENCE > OUT_OF_DECLARED_SCOPE > WAITING_FOR_CONSENT > NEEDS_CLARIFICATION > READY）。输出 status、project_state、business、status_counts、leads、queue、capacity_summary、intake_completeness、scope_evidence、next_step_cards、missing_facts、timeline_conflicts、refused_fields、refused_refs、injection_flagged、contact_consent、drafts（一律 DRAFT_NOT_SENT）、clarification_questions、human_checklist、human_confirm_items 与 Markdown 优先卡。安全上：证据引用只接受单一文件名，含路径、链接或盘符的引用被拒绝且原引用一字不回显；凭据形字段或值直接拒绝处理且不回显；个人信息与受保护属性字段被拒绝且不回显；输入中的命令与提示注入不执行，命中时在 markdown_summary 中隐藏该值并给出精确路径。"
description_zh: "把每条匿名线索整理成一张人工复核优先卡：六类状态、接入完整度、范围适配证据、缺失事实、时间冲突、拒绝字段、危险引用、注入标记、人工复核优先带、下一步准备卡、澄清问题与人工检查表。缺失保持未知，预算未提供不等于低价值，不输出客户价值评分，不自动联系或淘汰，草稿恒为 DRAFT_NOT_SENT 且仅在明确同意时生成。"
description_en: "An offline lead-intake readiness helper for small businesses, studios and 3-20 person teams. It reads one JSON file and returns READY_FOR_HUMAN_FOLLOWUP, NEEDS_CLARIFICATION, WAITING_FOR_CONSENT, OUT_OF_DECLARED_SCOPE, INSUFFICIENT_EVIDENCE or BLOCKED for every lead and for the batch, plus an intake-completeness checklist, scope-fit evidence, missing facts, time conflicts, refused fields, refused references, injection markers, a human-review priority band (never a customer-value score), next-step cards, clarification questions, a human checklist and a Markdown card. Verdicts come only from explicit anonymous facts: a missing budget is never treated as low value, an unstated area is never guessed, no protected attribute is ever used, drafts are always DRAFT_NOT_SENT and exist only when contact consent is explicitly granted. It never scrapes leads, never reads CRM, e-mail or direct messages, never contacts or disqualifies anyone, never promises price, discount or delivery, and never infers purchase intent."
version: 1.0.0
author: 苏格
homepage: "https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-lead-intake-readiness-priority-pack"
category: sales-ops
tags: [销售线索, 线索接入, 完整度, 下一步优先, 范围适配, 区域适配, 联系同意, 人工复核]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 销售线索接入完整度与下一步优先包

小商家和 3-20 人团队最常卡住的地方，不是没有线索，而是**每条新咨询进来后没有一处把事实、缺口和下一步按优先顺序摆清楚**：需求写在聊天里、区域只在脑子里、截止时间没人记、预算空着被当成「没价值」、同意没确认就开始想话术。美国联邦储备银行 2026 Small Business Credit Survey 显示，57% 的小企业把「触达客户/增长销售」列为经营挑战；U.S. Bank 2026 调查显示 40% 的小企业已采用 CRM——**需求真实，替代品也真实**。所以本技能不做「线索评分」，只做一件确定的事：把匿名事实整理成一张**可逐条人工复核的优先卡**。

本技能读一份 JSON，输出**人工复核优先卡**——每条线索的六类状态、接入完整度、范围适配证据、缺失事实、时间冲突、拒绝字段、危险引用、注入标记、人工复核优先带、下一步准备卡、澄清问题、人工检查表、草稿（一律未发送）与 Markdown 优先卡。**脚本只读一个本地 JSON，不联网、不写文件、不抓取线索、不读取 CRM/邮箱/私信、不自动联系、不自动淘汰客户、不承诺价格与交期、不使用受保护属性、不推断购买意愿。**

## 输入与澄清

字段表、判定顺序与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，八条线索覆盖六种状态；`references/sample-blocked.json` 是重复编号、非对象记录、个人信息、受保护属性、拒绝引用、注入与无同意的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）、`business.business_id`、`business.timezone`，以及每条线索的 `lead_id`。

**必须问清的关键点**：

1. **`as_of` 与 `business.timezone` 不能省**。没有它们无法判断当前时点与时效，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **只接受匿名事实**。姓名、手机号、邮箱、账号、地址、Cookie、令牌等一律**禁止**：字段名或值命中时记 `refused_fields`，该线索判 `BLOCKED`，**工具不回显该内容**。
3. **不使用受保护属性**。性别、年龄、民族、宗教、健康、残疾、婚姻、国籍、政治倾向等字段会被拒绝（`SENSITIVE_ATTRIBUTE`），**绝不参与任何判定或排序**。
4. **预算未提供不等于低价值**。`budget_status: not_provided` 只记 `BUDGET_NOT_PROVIDED` 并进入澄清，**不降级、不判低价值**；工具没有「价值分」这个概念。
5. **没有规则就保持未知**。缺 `qualification_rules` 记 `MISSING_QUALIFICATION_RULES`，时效窗口与优先顺序**不猜**；范围匹配只用你在 `business` 里声明的范围事实。
6. **没有区域不猜**。线索缺 `service_area` 时区域适配保持 `UNKNOWN` 并记 `MISSING_SERVICE_AREA`；边界地区（如「徐汇区」与「上海市徐汇区」）记 `AREA_BOUNDARY_REVIEW`，交人工复核。
7. **`OUT_OF_DECLARED_SCOPE` 不是拒客**。它只是依据你显式范围的人工复核提示，是否跟进由人工决定。
8. **容量只是排队建议**。`capacity.available_slots` 只用于给出队列位次，**不构成任何交付、响应或时效承诺**；缺失时不猜名额。
9. **状态取最严重者**。`BLOCKED` > `INSUFFICIENT_EVIDENCE` > `OUT_OF_DECLARED_SCOPE` > `WAITING_FOR_CONSENT` > `NEEDS_CLARIFICATION` > `READY_FOR_HUMAN_FOLLOWUP`。
10. **只在明确同意时生成草稿**。`drafts` 恒为 `DRAFT_NOT_SENT`；`contact_consent` 不为 `true` 时**不生成任何草稿**，也**绝不自动发送**。
11. **只给文件名，不要给路径或链接**。`evidence_refs` 只接受单一文件名；含路径、链接或盘符的引用会被**拒绝**，输出里只保留安全化文件名，原引用不会回显。
12. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的线索。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` / 业务编号 / 时区；`BLOCKED` 表示存在硬矛盾；`GAPS_FOUND` 表示存在待补齐、证据不足或等待项；`READY` 表示全部线索可进入人工跟进队列。
4. 再看 `project_state` 与 `status_counts`：六个线索状态。
5. 按 `queue` 与每条 `leads[].priority_band` 逐条人工复核；**优先带只由状态与时效推导，不是客户价值分**。
6. 按 `scope_evidence` 与 `timeline_conflicts` 核对范围、区域与时间；按 `missing_facts` 与 `clarification_questions` 逐条追问；**不要自行填数，也不要把缺失当成 0**。
7. 按 `refused_fields` 删除个人信息 / 受保护属性字段后重跑；按 `refused_refs` 把引用改为单一文件名。
8. 按 `capacity_summary` 与 `within_capacity` 排人工队列（仅为建议，不是承诺）。
9. 按 `drafts`（全部 `DRAFT_NOT_SENT`）准备人工沟通；未同意者**没有草稿**，须先人工确认同意。
10. 用 `human_checklist` 完成复核，用 `human_confirm_items` 明确哪些决定必须由人工做。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何 CRM、客服、邮箱或私信系统。
- **不自动执行**：不抓取线索、不自动联系、不自动淘汰客户、不承诺价格/折扣/交期、不使用受保护属性、不推断购买意愿。
- **只推导不发明**：结论只由输入中的明示匿名事实推导；不虚构范围规则、区域、时效窗口、预算或同意。
- **未知保持未知**：缺预算不按低价值、缺区域不按范围内、缺规则不发明策略、缺同意不生成草稿、缺容量不猜名额。
- **状态门禁**：六类状态固定聚合顺序；硬阻塞（非对象记录、重复编号、非法/倒置时间、个人信息、受保护属性）必须先人工修正。
- **同意门禁**：`drafts` 恒为 `DRAFT_NOT_SENT`；`contact_consent` 不为 `true` 时**不生成任何草稿**。
- **确定性**：线索按输入顺序、队列按 `priority_band` 与声明的优先顺序稳定排序，同一输入输出逐字节一致。
- **文件名门禁**：`evidence_refs` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且只输出安全化文件名，原引用在任何字段都不出现。
- **隐私门禁**：字段名或值命中个人信息 / 受保护属性时只输出字段路径与原因，**值不回显**；字段名命中凭据名或值具备凭据形态时**整体拒绝**且不回显。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注、业务范围、渠道、资格规则、容量时段与线索自由文本；命中时只加标记并在 `injection_flagged` 中给出精确路径；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入或被拒绝的字段改用固定占位文本。
- **免责**：输出是线索接入整理的人工准备材料，不是资格判定、价值判定、报价或承诺；范围、区域、是否跟进、是否联系与合规必须由人工确认。
