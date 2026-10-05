---
name: suge-new-hire-first-week-readiness-pack
slug: suge-new-hire-first-week-readiness-pack
displayName: 新员工首周入职准备包
display_name: 新员工首周入职准备包
display_name_en: "New-Hire First-Week Readiness Pack"
summary: "把一位已入职人员的首周准备事实整理成一张可人工复核的准备卡：设备、访问、培训、会议、首周结果与政策确认逐项给出 READY_FOR_DAY_ONE / ACTION_NEEDED / WAITING_ON_OWNER / INSUFFICIENT_EVIDENCE / BLOCKED，加上首日清单、首周日程、负责人矩阵、缺失事实、时间冲突、拒绝字段、危险引用、注入标记、逾期项、澄清问题与人工检查表。未知保持未知，绝不把未知当已完成；不决定是否录用、不评价员工、不推断表现、不处理薪资税务、不通开或回收账号、不采购设备、不发送消息、不给法律结论，草稿恒为 DRAFT_NOT_SENT 且仅在明确同意时生成。"
description: "面向小商家、工作室与 3-20 人小团队的离线新员工首周入职准备工具。输入是一份 JSON：as_of（必须带时区偏移）、organization（organization_id / work_mode / timezone / working_days（1-7，周一=1）/ first_week_availability，不得内置默认）、new_hire（仅匿名 role_instance_id / role_title / start_date / timezone / work_location_mode）、owners[]（匿名 owner_id / responsibilities / availability）、equipment[] / access_items[] / training_items[] / meetings[] / first_week_outcomes[] / policy_acknowledgements[]（每项含匿名 id、显式状态、负责人、到期时间与证据引用）、communication_consent（仅控制是否生成内部提醒草稿，绝不外发）、notes、evidence_refs。判定顺序固定：非对象记录记 INVALID_ITEM_RECORD、编号缺失记 MISSING_ITEM_ID、重复编号让同号条目一并 BLOCKED、状态表外记 INVALID_ITEM_STATUS、显式 BLOCKED 记 BLOCKED_ITEM、非法到期/会议时间与倒置会议区间为硬阻塞；缺失状态、负责人缺失或不在清单、缺失到期、首周结果缺证据记证据不足；WAITING_ON_OWNER 记等待负责人；逾期、进行中与到期晚于入职日记待处理。总体状态取最严重者：BLOCKED > INSUFFICIENT_EVIDENCE > WAITING_ON_OWNER > ACTION_NEEDED > READY_FOR_DAY_ONE。输出 status、readiness_state、六个分区状态、首日清单、首周日程、负责人矩阵、缺失事实、时间冲突、逾期项、政策确认清单、拒绝字段、危险引用、注入标记、澄清问题、人工清单与 Markdown 准备卡。安全上：证据引用只接受单一文件名，含路径、链接或盘符的引用被拒绝且原引用一字不回显；凭据形字段或值直接拒绝处理且不回显；个人信息与受保护属性字段被拒绝且不回显；输入中的命令与提示注入不执行，命中时在 markdown_summary 中隐藏该值并给出精确路径。"
description_zh: "把一位已入职人员的首周准备事实整理成一张人工复核准备卡：五态、首日清单、首周日程、负责人矩阵、缺失事实、时间冲突、拒绝字段、危险引用、注入标记、逾期项、政策确认、澄清问题与人工检查表。未知保持未知，绝不把未知当已完成，不作出录用或绩效判断，不处理薪资税务，草稿恒为 DRAFT_NOT_SENT 且仅在明确同意时生成。"
description_en: "An offline new-hire first-week readiness helper for small businesses, studios and 3-20 person teams. It reads one JSON file and returns READY_FOR_DAY_ONE, ACTION_NEEDED, WAITING_ON_OWNER, INSUFFICIENT_EVIDENCE or BLOCKED for every item, area and the batch, plus a day-one checklist, a first-week schedule, an owner matrix, missing facts, time conflicts, refused fields, refused references, injection markers, overdue items, policy-confirmation records, clarification questions and a Markdown readiness card. Verdicts come only from explicit anonymous facts: unknown is never treated as done, no protected attribute is ever used, drafts are always DRAFT_NOT_SENT and exist only when communication consent is explicitly granted. It never decides hiring, never evaluates performance, never handles payroll or tax, never provisions or recovers accounts, never buys equipment, never sends messages and never gives a legal conclusion."
version: 1.0.0
author: 苏格
homepage: "https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-new-hire-first-week-readiness-pack"
category: people-ops
tags: [新员工入职, 首周准备, 入职清单, 首日清单, 负责人矩阵, 设备与访问, 培训安排, 人工复核]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 新员工首周入职准备包

小商家和 3-20 人团队最常卡住的地方，不是「没有入职流程」，而是**一位新人的首周准备散在设备、账号、培训、会议和负责人口头约定里，没有人把它摆成一张可逐条核对的清单**：电脑到了没有、账号通没通、培训谁带、首日会议撞不撞、首周要交付什么、材料谁确认——全靠记忆。工信部、教育部 2026 年百日招聘活动明确覆盖智能匹配、面试与**岗前培训**，ASE 2026 招聘与留任调查显示 62% 的参与机构改进了新员工入职与融入流程，TIGTA 2026 审计指出部分新员工未及时获得设备或绩效期望——**需求真实，但 HRIS 与协作表格是成熟替代品**。所以本工具不做「入职评分」，只做一件确定的事：把匿名准备事实整理成一张**可逐条人工复核的准备卡**。

本工具读一份 JSON，输出**准备卡**——每个条目、每个分区与整批的五态结论，加上首日清单、首周日程、负责人矩阵、缺失事实、时间冲突、逾期项、政策确认清单、拒绝字段、危险引用、注入标记、澄清问题、人工检查表、内部提醒草稿（一律未发送）与 Markdown 准备卡。**脚本只读一个本地 JSON，不联网、不写文件、不读取 HRIS/邮箱/日历/账号系统、不自动开通或回收账号、不采购设备、不发送消息、不作出录用或绩效判断、不处理薪资税务、不使用受保护属性、不给法律结论。**

## 输入与澄清

字段表、判定顺序与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，覆盖三种分区状态并生成一条草稿；`references/sample-blocked.json` 是重复编号、非对象记录、未识别状态、非法会议区间、个人信息、受保护属性、拒绝引用、注入与无同意的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）、`organization.organization_id`、`organization.timezone`，以及 `new_hire.role_instance_id` 与 `new_hire.start_date`。

**必须问清的关键点**：

1. **`as_of`、`organization_id` 与 `timezone` 不能省**。没有它们无法判断当前时点与组织边界，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **只接受匿名事实**。姓名、手机号、邮箱、账号、地址、Cookie、令牌、薪资等一律**禁止**：字段名或值命中时记 `refused_fields`，该条目判 `BLOCKED`，**工具不回显该内容**。
3. **不使用受保护属性**。性别、年龄、民族、宗教、健康、残疾、婚姻、国籍、政治倾向等字段会被拒绝（`SENSITIVE_ATTRIBUTE`），**绝不参与任何判定或排序**。
4. **未知不等于已完成**。条目状态缺失或为 `UNKNOWN` 时判 `INSUFFICIENT_EVIDENCE`，**绝不当作 `DONE`**；负责人缺失、到期缺失、首周结果缺证据同理。
5. **工作日不默认**。缺 `working_days` 记 `MISSING_WORKING_DAYS`，首周日程的工作日标记保持 `null`，**不假设周一至周五**。
6. **`READY_FOR_DAY_ONE` 有前提**。只有必需项事实齐全、无未知、无等待、无待处理、无阻塞时才是就绪；任何缺口都会落到相应状态。
7. **政策项只做确认**。`policy_confirmations` 只登记「由人工确认已阅知」，**工具不判断材料是否合规**。
8. **只在明确同意时生成草稿**。`reminder_draft` 恒为 `DRAFT_NOT_SENT`；`communication_consent` 不为 `true`、总体被阻塞或证据不足时**不生成任何草稿**，也**绝不自动发送**。
9. **只给文件名，不要给路径或链接**。`evidence_refs` 只接受单一文件名；含路径、链接或盘符的引用会被**拒绝**，输出里只保留字段路径，原引用不会回显。
10. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。
11. **所有动作由人工执行**。是否录用、是否评价、是否开通账号、是否采购、是否发送、是否合规，全部由人工决定；工具只整理事实。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的事实。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` / 组织编号 / 时区；其余为五态之一。
4. 再看 `readiness_state` 与 `readiness_areas`：先处理 `BLOCKED` 与 `INSUFFICIENT_EVIDENCE`，再看 `WAITING_ON_OWNER` 与 `ACTION_NEEDED`。
5. 按 `day_one_checklist` 与 `first_week_schedule` 核对首日与首周安排；按 `owner_matrix` 逐位负责人跟进。
6. 按 `missing_facts` 与 `clarification_questions` 逐条追问；**不要自行填数，也不要把缺失当成 0 或 `DONE`**。
7. 按 `refused_fields` 删除个人信息 / 受保护属性字段后重跑；按 `refused_refs` 把引用改为单一文件名。
8. 按 `policy_confirmations` 交由人工确认材料；**工具不作合规判断**。
9. 按 `reminder_draft`（恒 `DRAFT_NOT_SENT`）准备人工沟通；未同意者**没有草稿**，须先人工确认同意。
10. 用 `human_checklist` 完成复核，用 `human_confirm_items` 明确哪些决定必须由人工做。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何 HRIS、邮箱、日历或账号系统。
- **不自动执行**：不自动开通或回收账号、不采购设备、不发送消息、不作出录用或绩效判断、不处理薪资税务、不生成劳动合同、不给出法律结论、不使用受保护属性。
- **只推导不发明**：结论只由输入中的明示匿名事实推导；不虚构负责人、到期时间、工作日、状态或证据。
- **未知保持未知**：缺状态不当作完成、缺负责人不猜、缺工作日不默认、缺到期不猜、缺证据不放过、缺同意不生成草稿。
- **状态门禁**：五态固定聚合顺序；硬阻塞（非对象记录、重复编号、未识别状态、非法时间、个人信息、受保护属性）必须先人工修正。
- **同意门禁**：`reminder_draft` 恒为 `DRAFT_NOT_SENT`；`communication_consent` 不为 `true` 或总体被阻塞 / 证据不足时**不生成任何草稿**。
- **确定性**：条目按输入顺序、分区与队列按固定顺序稳定排序，同一输入输出逐字节一致。
- **文件名门禁**：`evidence_refs` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且只输出字段路径，原引用在任何字段都不出现。
- **隐私门禁**：字段名或值命中个人信息 / 受保护属性时只输出字段路径与原因，**值不回显**；字段名命中凭据名或值具备凭据形态时**整体拒绝**且不回显。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注、岗位名、工作方式、职责、可用时段与条目标签；命中时只加标记并在 `injection_flagged` 中给出精确路径；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入准备卡，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入或被拒绝的字段改用固定占位文本。
- **免责**：输出是入职准备的人工材料，不是录用、绩效、合规或法律结论；是否录用、如何评价、是否开通账号、是否采购、是否发送与材料是否合规必须由人工确认。
