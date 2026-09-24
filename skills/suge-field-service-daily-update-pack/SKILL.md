---
name: suge-field-service-daily-update-pack
slug: suge-field-service-daily-update-pack
display_name: 现场服务每日进度更新包
display_name_en: "Field Service Daily Update Pack"
summary: "把装修、维修、安装、保洁等现场团队的当日计划和记录，整理成一份内部日报加一份客户可读进度稿：完成、部分完成、未开始、受阻、无记录待确认五栏分开，标出计划外记录、状态冲突、工时偏差、材料与人员缺口、照片证据索引、明日计划与发送前检查表。没有当日记录的计划任务一律进「无记录待确认」，绝不写进客户稿的已完成部分；照片只按文件名与说明索引，不读取图片内容；含路径或 URL 的照片引用直接拒绝并不解析。不外发消息、不做质量或安全结论、不承诺工期。"
description: "面向装修、维修、安装、保洁、摄影执行、活动搭建等现场服务小团队的离线日报与客户进度稿生成工具：当天收工后，把散在聊天、纸单和照片里的进度整理成同源的两份材料——一份给内部（含阻塞、工时偏差、记录缺陷和发送前检查表），一份可以直接发给客户。输入是带时区偏移的 as_of、可选 project（含 IANA 时区）、work_date、plan[]（task_id / title / owner / planned_hours）、progress[]（task_id / state / note / hours_spent / photo_refs / occurred_at）、可选 photos[]（filename / caption）、可选 blockers[]（blocker_id / kind / description / owner / eta）、可选 customer_pending[] 与 next_day_plan[]。判定规则完全固定且可复核：计划任务按下表映射到已完成、部分完成、未开始、受阻或无记录待确认，state 缺失或越界也算无记录待确认，progress 指向计划外任务单独列计划外记录；同一 task_id 状态不一致判冲突并使整体 BLOCKED，状态一致判重复且只保留首条参与判定；工时偏差用 Decimal 计算并区分 ON_PLAN / OVER_PLAN / UNDER_PLAN，任一侧缺数据时合计留 null 而不按 0 计算；照片引用用 NFKC 加 casefold 归一化后比较，因此 IMG_1.JPG 与 img_1.jpg 视为同一张（在默认 macOS 与 Windows 文件系统上它们确实会互相覆盖），同名照片的所有声明都标记为已引用并单独报告重复，缺说明与未被引用的照片分别列出；photo_refs 或 filename 含路径分隔符、.. 、URL scheme 或盘符一律拒绝且不解析，整体 BLOCKED。客户稿只描述记录到的事实：已完成段落写作按现场记录，无记录与未开始合并为需进一步确认，需要你确认段落只列用户自己提交的问题，内部推导的提示不进客户稿。安全上：凭据形态的字段名或值直接拒绝且不回显；提示注入仅在动作词与目标词同句共现时命中，只标位置不执行并替换为固定占位；控制字符进入输出前剥离且不做 NFKC 以免改写中文全角标点；所有自由文本转义 Markdown 元字符。只读、离线、不使用第三方库、不读取图片。触发词：现场日报、施工日报、工程日报、进度更新、客户进度、每日进度、完工汇报、材料缺口、人员缺口、照片索引。联系邮箱：43298568@qq.com。"
description_zh: "把现场团队的当日计划与记录整理成内部日报加客户可读进度稿：完成、部分完成、未开始、受阻、无记录待确认五栏分开，标出计划外记录、状态冲突、工时偏差、材料与人员缺口、照片索引与发送前检查表。无记录任务绝不写进客户稿已完成部分；照片只按文件名索引不读图片；含路径或 URL 的引用直接拒绝。不外发、不做质量结论、不承诺工期。"
description_en: "An offline daily-report and customer-update builder for field service teams (renovation, repair, installation, cleaning, on-site production): it turns the day's scattered notes, paper sheets and photo names into two sibling documents - an internal report with blockers, hour deviations, record defects and a pre-send checklist, and a customer-readable progress note. Plan tasks resolve to completed, partial, not-started, blocked or unverifiable; a task with no record of the day is never written into the customer note's completed section. Photo references are compared after NFKC and case folding, every declaration sharing a file name is marked referenced, missing captions and unreferenced photos are listed separately, and any reference containing a path separator, parent segment, URL scheme or drive letter is refused without being resolved. It never sends messages, never concludes on quality or safety, and never promises a completion date."
version: 1.0.1
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-field-service-daily-update-pack
category: field-service-ops
tags: [现场日报, 施工日报, 进度更新, 客户进度, 材料缺口, 人员缺口, 照片索引, 工时偏差]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 现场服务每日进度更新包

现场服务团队每天收工后都要写两样东西：一份给自己看的日报，和一份发给客户的进度说明。现实是——**这两样东西通常是同一条微信改两遍**：给客户的版本里混着材料涨价、工人请假、昨天的返工；给内部的版本又漏了客户已经问过三次的问题。更麻烦的是「计划里写了但今天根本没做」的活，很容易被顺手写成「已完成」。

本技能把这件事拆开：读一份 JSON，输出**同源但面向不同读者**的两份材料——内部日报（含阻塞、工时偏差、记录缺陷、发送前检查表）和客户进度稿（只描述记录到的事实）。**脚本不读图片内容、不访问任何路径或 URL、不发消息、不做质量或安全结论、不承诺工期。**

## 输入与澄清

字段表、状态映射、照片与路径规则见 @references/guide.md（同目录 `references/sample.json` 是可复现样例，输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）、`work_date`、`plan[]`、`progress[]`。

**必须问清的关键点**：

1. **计划里写了、当天没记录的，不会算完成**。这类任务进「无记录待确认」，并生成一条要现场负责人回答的问题。这是本工具最主要的价值点。
2. **照片只写文件名，不要写路径或链接**。含 `/`、`\`、`..`、`https:` 的引用会被**拒绝且不解析**（既是防目录穿越，也是防「读取任意文件」），并让整体状态变成 `BLOCKED`。
3. **同名照片请重命名**。`IMG_2204.jpg` 出现两次，在默认文件系统上会互相覆盖，客户最后看到的可能是错的那张。工具会标出来，但不会替你改名。
4. **记录与项目时区不同步是常见错误**。跨时区团队尤其注意：`work_date` 与 `as_of` 在项目时区下的本地日期不一致时会被标出。
5. **照片说明不能省**。没有说明的照片对客户没有信息量，工具会列入 `caption_missing`。
6. **不要把登录凭据填进来**。出现密码、令牌一类字段会被**整体拒绝**且不回显。
7. **不要指望工具替你判断「实际完成了没有」**。它只反映你提交的记录；现场是否真的完成，要现场负责人确认。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED`（疑似凭据，已拒绝且未回显）；`BLOCKED`（状态冲突或非法照片引用）；`INPUT_INCOMPLETE`（缺 `as_of` 或计划 / 记录）；`GAPS_FOUND`（可用但有缺口）。
4. 看 `sections.unverifiable`：**这些一律不能写进客户稿的已完成部分**。
5. 看 `conflicts`、`unplanned_records`、`record_gaps`。
6. 看 `blockers` 与 `material_gaps`：材料缺口要提前告诉客户。
7. 看 `plan_deviation`：偏差大不等于出错，但要能解释。
8. 看 `photo_issues`：同名、缺说明、未被引用三类分别处理。
9. 按 `human_send_checklist` 逐项打勾，再取 `customer_progress_draft` 发给客户（发送动作由人工执行）。
10. 看 `clarification_questions`，按 `Q-01` 顺序去问。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不访问路径、**不读取图片内容**、不写文件。
- **不外发**：不向客户或班组发送任何消息；本工具只产出草稿。
- **不结论**：不做工程质量、安全验收、工期或责任判断；不计算报价。
- **不猜测**：工时、状态、预计解除时间缺失一律保持未知并生成追问，**不按 0 计算**。
- **确定性**：排序全部显式固定（分栏顺序、任务输入顺序、分组键、字段名、问题编号），同一输入输出逐字节一致。
- **记录与任务可对账**：计划外记录、重复记录、状态冲突、记录级缺陷（空记录、非法工时、时区偏移不一致）全部分别列出，不静默修复。
- **照片为纯索引**：只按文件名与说明列索引，**不识别图片内容**，也不验证照片是否拍到了所述事项。
- **路径与 URL 拒绝**：照片引用与文件名一律按裸文件名处理；含 `/`、`\`、`..`、`scheme:` 的引用被拒绝且**不解析**。
- **提示注入逐字段检测、位置精确、低误报**：覆盖 `progress[].note` 等自由文本叶子，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `progress[2]/note`）。检测要求「动作词 + 目标词」**同句共现**，正常施工备注不会被误标。
- **不可信文本先转义再入简报，命中即隐藏**：两份 Markdown 产物中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。**不做 NFKC**，避免把中文全角标点改写成半角。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是进度整理材料，不是现场验收结论；实际完成情况以现场负责人确认与验收记录为准。
