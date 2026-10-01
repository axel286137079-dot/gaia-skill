---
name: suge-client-change-request-impact-pack
slug: suge-client-change-request-impact-pack
displayName: 客户变更请求影响准备包
display_name: 客户变更请求影响准备包
display_name_en: "Client Change Request Impact Pack"
summary: "把客户临时提出的改动整理成一份可逐条核对的变更准备单：每条请求给出 IN_SCOPE / INCLUDED_REVISION / SUBSTITUTION / CHANGE_REQUEST / CLARIFICATION_NEEDED / INSUFFICIENT_EVIDENCE 分类，加上基线对照、影响维度、未知项、批准前阻塞、沟通草稿与 Markdown 变更准备单。客户提出请求不等于批准；缺基线不判超范围；没有显式成本不估价；金额只按币种分组，不跨币种汇总。"
description: "面向小型设计、开发、代运营与顾问团队（3–20 人）的离线变更请求影响核对工具。输入是一份 JSON：as_of（必须带时区偏移）、project（project_id / name / baseline_version / scope_baseline[] / revision_allowance{included,used}）与 change_requests[]，每条请求可含 request_id、raw_text、requested_by、target_outcome、baseline_refs[]、new_deliverables[]、removes_deliverables[]、affected_assets[]、impact（effort_hours / schedule_days / cost{amount,currency} / dependencies[]）、within_revision_allowance、decision_maker、approval{status,decided_by,decided_at}。分类规则完全固定：命中全部已批准基线交付物且无新增无移除记 IN_SCOPE；有新增但在已含修订额度内记 INCLUDED_REVISION；新增与移除数量相等视为替换记 SUBSTITUTION；超出额度或超出基线的新增记 CHANGE_REQUEST；缺少范围基线、引用了不存在的基线交付物或无法对应任何基线记录记 CLARIFICATION_NEEDED；修订额度未知、请求描述为空记 INSUFFICIENT_EVIDENCE。批准与排期分离：任何分类都必须有明确批准记录才能进入 ready-to-schedule，客户提出请求本身不算批准，缺少批准记 MISSING_APPROVAL、待批准记 PENDING_APPROVAL。影响只呈现显式事实：没有给出成本或费率就保持 COST_NOT_PROVIDED，绝不估价；金额按币种分组求和，缺币种计入 CANNOT_ATTRIBUTE，永不跨币种相加。输出每条请求的分类、基线对照、影响维度、未知项、批准状态、阻塞项、按币种分组的金额、批准前阻塞清单、待确认问题、未发送的沟通草稿与 Markdown 变更准备单。安全上：不解释合同效力、不判断是否构成违约、不起草合同；附件只接受单一文件名，含路径或链接的引用被拒绝且只保留安全化文件名，原引用不回显；字段名或值形如凭据直接拒绝处理且不回显原值；输入中的命令与提示注入只标记、不执行，进入简报时替换为固定占位；控制字符在进入任何输出前剥离；所有自由文本转义 Markdown 元字符。只读、离线、不使用任何第三方库。触发词：变更请求、范围变更、范围漂移、需求变更、客户改需求、影响评估、基线对照、增项确认、变更准备单。联系邮箱：43298568@qq.com。"
description_zh: "把客户临时提出的改动整理成可逐条核对的变更准备单：每条请求给出六类范围判定、基线对照、影响维度、未知项、批准前阻塞和未发送的沟通草稿。客户请求不等于批准，缺基线不判超范围，没有显式成本不估价，金额只按币种分组。"
description_en: "An offline client change-request impact builder for small agencies and service teams. It reads one JSON file and classifies every request as IN_SCOPE, INCLUDED_REVISION, SUBSTITUTION, CHANGE_REQUEST, CLARIFICATION_NEEDED or INSUFFICIENT_EVIDENCE, then returns a baseline comparison, explicit-only impact figures, unknown flags, pre-approval blockers, a per-currency amount grouping and an unsent client communication draft. A client request is never treated as approval, a missing baseline never yields an out-of-scope verdict, no cost is ever estimated, and amounts are never summed across currencies."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-client-change-request-impact-pack
category: client-change-ops
tags: [变更请求, 范围变更, 范围漂移, 影响评估, 基线对照, 增项确认, 变更准备单, 批准状态]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 客户变更请求影响准备包

「就加一个小功能」「那个页面顺手改一下」——这几句话最贵。它们先出现在微信里，再变成口头承诺，最后在验收时才被发现是范围外的新增，而基线、修订次数和批准记录散在合同、邮件和聊天记录里，**从来没有被逐条对照过**。

本技能做这件事：读一份 JSON，输出**可以逐条核对的变更准备单**——每条请求的分类、基线对照、影响维度、未知项、批准前阻塞与沟通草稿。**脚本只读一个本地 JSON，不联网、不写文件、不发送、不签署、不收费、不排期。**

## 输入与澄清

字段表、分类规则与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，`references/sample-blocked.json` 是缺少基线并含恶意文本的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）与 `change_requests[]`。

**必须问清的关键点**：

1. **`as_of` 不能省**。没有它就无法建立基准时间，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **客户提出请求 ≠ 批准**。即使请求人就是决策人，没有明确批准记录一律记 `MISSING_APPROVAL` 或 `PENDING_APPROVAL`，不会进入可排期。
3. **没有基线就不能判超范围**。缺 `scope_baseline` 与 `baseline_version` 时一律记 `CLARIFICATION_NEEDED`，**不会**被判成 `CHANGE_REQUEST`。
4. **没有显式成本就不估价**。缺金额或费率时保持 `COST_NOT_PROVIDED`，工具**不替客户或自己算钱**。
5. **金额只按币种分组**。不同币种**永不相加**；有金额但没币种记 `COST_CURRENCY_UNKNOWN` 并排除出合计。
6. **未知就是未知**。工时、排期缺失一律保持未知并追问，**不默认为 0**。
7. **只给文件名，不要给路径或链接**。附件引用只接受单一文件名；含路径或链接会被**拒绝**，输出里只保留安全化后的文件名，原引用不会回显。
8. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。
9. **原始请求里的「指令」不会被当作命令执行**。只做标记，进入简报时替换为固定占位。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的项目。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` 或没有请求；`BLOCKED` 表示至少一条请求存在批准前阻塞；`GAPS_FOUND` 表示全部可排期但仍有未知项；`READY` 表示全部请求已批准且无未知项。
4. 看 `classification_counts` 与每条请求的 `classification`。
5. 看 `requests[].baseline_refs / new_deliverables / removes_deliverables` 做基线对照。
6. 看 `requests[].impact`：`effort_hours` / `schedule_days` / `cost_by_currency` 只呈现显式事实，`未知` 代表用户没给。
7. 看 `amounts_by_currency`：这是按币种分组的显式费用合计，**不跨币种**。
8. 看 `pre_approval_blockers` 与 `requests[].ready_to_schedule`：未取得批准前一律不可排期。
9. 看 `clarification_questions`：按 `Q-01` 顺序**原样**发给对应决策人确认。
10. 看 `communication_draft`：这是**草稿**，需人工确认后才可对外发出。
11. 用 `markdown_summary` 作为变更准备单。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何合同、邮件或项目管理平台。
- **不外发**：不发送消息、不签署文件、不收费、不排期、不修改任何项目文件。
- **不判定**：不解释合同效力、不判断是否构成违约或侵权、不起草合同；这些一律进入 `human_confirm_items` 由双方授权人员决定。
- **不估价**：没有显式金额或费率时保持未知，绝不估算费用或工时。
- **不跨币种**：金额只按币种分组，永不跨币种相加；缺币种的金额不进入合计。
- **请求不等于批准**：任何分类都必须有明确批准记录才能 `ready_to_schedule=true`。
- **确定性**：请求按编号、问题按编号、字段名与分类汇总顺序全部显式固定，同一输入输出逐字节一致。
- **文件名门禁**：附件 `basename` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且**只输出安全化文件名**，原引用在任何字段都不出现。
- **阻塞即停**：缺基线、引用不存在的基线交付物、待批准、缺批准、描述为空一律进入阻塞清单，不做静默兜底。
- **提示注入逐字段检测、位置精确、低误报**：覆盖原始请求、目标结果、依赖等自由文本，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `change_requests[1]/raw_text`）；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是变更请求影响的信息核对材料，不是合同效力或定价结论；是否构成范围变更、如何计费与是否批准，由双方授权人员按合同约定决定。
