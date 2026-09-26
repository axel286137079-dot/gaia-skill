---
name: suge-small-team-sop-automation-map
slug: suge-small-team-sop-automation-map
display_name: 小团队 SOP 自动化机会地图
display_name_en: "Small-Team SOP Automation Opportunity Map"
summary: "把个人创业者、小商家和 3–20 人团队手上那些「一直在重复做」的流程，逐步骤判定成 keep_manual / assist / automate_candidate / insufficient_evidence 四类，并给出优先级矩阵、节省时间假设、数据与权限前置项、人工检查点、失败回退和实施顺序。硬规则：单次耗时、运行频率或错误率缺失时必须标 insufficient_evidence，不虚构优先级；涉及付款、删除、发信、对外发布、账号权限、法律/医疗/金融判断的步骤强制保留人工审批，最多只能到 assist；无法识别或非法的小时成本不参与任何金额计算，ROI 只使用你显式提供的月均次数、节省比例与小时成本；不同币种分别列出、不做跨币种合计也不换汇。不生成任何可执行的 n8n/Zapier/脚本或工作流，不连接你的任何系统，不索取任何凭据，也不代执行、发送、删除或付款。"
description: "面向个人创业者、小商家、3–20 人团队和运营负责人的离线流程盘点工具，纯标准库、只读、不联网。触发词：SOP 自动化、流程自动化、自动化机会、该不该自动化、能自动化什么、流程盘点、重复劳动、节省时间、自动化优先级、实施顺序、人工审批点、失败回退。输入是一份用户脱敏后的 JSON：带时区偏移的 as_of、可选 team（name / size / default_hourly_cost{value,currency}）、processes[]（process_id / name / owner / steps[]）；每个 step 含 step_id、name、frequency（daily/weekly/monthly/per_delivery/ad_hoc）、minutes_per_run、monthly_runs、error_rate_pct、error_consequence（low/medium/high）、data_sensitivity（public/internal/customer_pii/financial/regulated）、rule_based、touches[]（如 read_only / data_entry / reporting / payment / deletion / outbound_message / external_publish / account_permission / legal / medical / financial）、可选 external_dependency、manual_only、human_approval_required、hourly_cost、assumed_saving_ratio、shadow_runs。判定顺序固定且可复核：证据不完整（频率非法或缺失、单次耗时缺失/非法、错误率缺失/非法任一成立）→ insufficient_evidence；你声明 manual_only → keep_manual；frequency 为 ad_hoc（频率不稳定）→ keep_manual；touches 命中高风险动作、或存在未识别动作标签、或 data_sensitivity 为 financial/regulated、或 error_consequence 为 high、或你声明 human_approval_required → assist 且强制人工审批；存在 external_dependency → assist；rule_based 为 true 且数据敏感度为 public/internal/customer_pii 且错误后果为 low/medium → automate_candidate；其余 → assist。优先级分值 = 月耗时（小时，保留两位，由 minutes_per_run × monthly_runs ÷ 60 算出）× 10 + 错误率，只有证据完整且判定为 assist/automate_candidate 的步骤进入排序，其余进 unranked_steps 并写明原因，绝不为了排序而补数。金额只在你同时提供月均次数、节省比例与可用小时成本时计算，小时成本优先取步骤自身的 hourly_cost、缺失才回退 team.default_hourly_cost，按币种分组输出、无跨币种总额。输出含 status、counts、processes、steps、priority_matrix、unranked_steps、implementation_order（先可自动化候选、再辅助、再保持人工、再证据不足）、manual_only_steps、insufficient_evidence_steps、human_checkpoints（APPROVE_BEFORE_USE / FULLY_MANUAL / WAIT_FOR_EVIDENCE）、data_permission_prerequisites、failure_fallbacks、time_saving_hypothesis（含 assumptions）、markdown_summary（Markdown 决策包）。安全上：凭据字段名或凭据形态的值直接 REJECTED 且不回显；自由文本只在「动作词 + 目标词同句共现」时判提示注入，只标精确路径、只替换为固定占位、绝不执行；控制字符剥离；Markdown 元字符转义；排序全部显式固定，同一输入输出逐字节一致。联系邮箱：43298568@qq.com。"
description_zh: "把小团队一直在重复做的流程逐步骤判成 keep_manual / assist / automate_candidate / insufficient_evidence，并给出优先级矩阵、节省时间假设、数据权限前置项、人工检查点、失败回退与实施顺序。缺耗时/频率/错误率一律标证据不足、不虚构优先级；涉付款、删除、发信、对外发布、账号权限、法律医疗金融的步骤强制人工审批；ROI 只用显式输入，不同币种分别列出。不生成任何可执行脚本或工作流、不连系统、不索取凭据、不代执行。"
description_en: "An offline opportunity mapper for small teams (solo founders, small merchants, 3-20 person teams) that turns an inventory of existing processes into a per-step verdict: keep_manual, assist, automate_candidate or insufficient_evidence, plus a priority matrix, time-saving hypotheses, data and permission prerequisites, human checkpoints, failure fallbacks and an implementation order. Insufficient evidence (missing or invalid frequency, minutes per run or error rate) forces insufficient_evidence and never fabricates a priority. Steps touching payment, deletion, outbound messaging, external publishing, account permissions, or legal, medical or financial judgement are forced to keep a human approval and can never become an unattended automation candidate; unrecognised action tags are treated as unknown risk rather than safe. Cost savings are computed only from the values you actually supply (monthly runs, assumed saving ratio, hourly cost), grouped by currency with no cross-currency total and no FX conversion. The tool never emits executable n8n/Zapier/script code or a workflow, never connects to any system, never asks for credentials and never sends, publishes, deletes or pays on your behalf. Credential-shaped input makes the whole run REJECTED without echoing the content. Deterministic: identical input yields byte-identical output."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-small-team-sop-automation-map
category: ops-automation
tags: [SOP, 自动化机会, 流程盘点, 重复劳动, 优先级, 人工审批, 节省时间, 实施顺序]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 小团队 SOP 自动化机会地图

小团队最常问的一句话是「**我这些活能不能交给 AI**」。通用 AI 对话几乎一定会回答「可以」，还会顺手给你一份看起来很专业的清单——问题在于，它不知道你这一步每次要花多久、一个月跑几次、错了会怎样，也不知道这一步在给客户发消息。

这个工具只做一件事：**把「该不该自动化」变成一张逐步骤、可复核的表**。它的第一原则不是多自动化，而是**先说清楚哪些步骤数据不够，还不能判**。

有一类判断值得单独说明：如果某个流程本身还在天天变，自动化它只会让混乱跑得更快。所以 `frequency` 为 `ad_hoc` 的步骤一律判 `keep_manual`——不是工具偷懒，是这类投入通常收不回来。

## 输入与澄清

字段表、判定顺序与分值公式见 @references/guide.md（同目录 `references/sample.json` 是可复现样例，输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）、`processes[].steps[]`，以及每步的 `frequency`、`minutes_per_run`、`error_rate_pct`、`data_sensitivity`。

**必须问清的关键点**：

1. **单次耗时、运行频率、错误率三个都要有**。缺任意一个，这一步直接判 `insufficient_evidence`：不排序、不建议自动化、只告诉你缺什么。这是本工具和通用对话最大的区别。
2. **`monthly_runs` 请自己填。** 工具**不会**用「每天 = 21 次」替你推算——那会让后面的金额全部失真。
3. **`assumed_saving_ratio` 是个假设，必须由你给出。** 不填就没有金额，只有小时数。
4. **高风险动作请如实标进 `touches`**：`payment`、`deletion`、`outbound_message`、`external_publish`、`account_permission`、`legal`、`medical`、`financial`。命中后最多只能判 `assist`，并强制人工审批——**它不可能变成无人值守的自动化**。
5. **不认识的 `touches` 标签按「风险未知」处理**，不会被当作安全，只会降级到 `assist` 并要求人工确认。
6. **小时成本缺失时不推算货币收益。** 步骤没填就看 `team.default_hourly_cost`，都没有就只给小时数，不给金额。
7. **不同币种不会合计**，也不会换算。`CNY` 和 `USD` 的节省金额分开列。
8. **不要把账号、密码、Token 填进来。** 命中凭据会被**整体拒绝**且不回显。
9. **本工具不生成任何可执行脚本或工作流**，不连接你的系统，也不替你执行。它给的是判断顺序，不是 n8n 节点。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。先用 @references/sample.json 跑一遍。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 看顶层 `status`：`REJECTED`（疑似凭据，已拒绝且未回显）；`INPUT_INCOMPLETE`（缺 `as_of`、流程或步骤）；`NO_CANDIDATE`（没有任何辅助或自动化候选）；`MAPPED`（已产出地图）。
4. 看 `steps[]` 的 `verdict` 与 `verdict_reason`：先读判定理由，再决定要不要争。
5. 看 `priority_matrix`：**只包含证据完整的步骤**。榜上没有不等于不重要，可能只是数据不够。
6. 看 `unranked_steps`：**先补这里缺的数据**，不要先做自动化。
7. 看 `human_checkpoints`：`APPROVE_BEFORE_USE` 的产出必须人工确认后才能用；`FULLY_MANUAL` 的整步保持人工；`WAIT_FOR_EVIDENCE` 的先补数据。
8. 看 `data_permission_prerequisites`，把权限与数据前置项作为实施前提谈清楚。
9. 看 `time_saving_hypothesis`：`by_currency` 是**按你的假设**算出的金额，`assumptions` 里写明了它依赖什么；不同币种分别看。
10. 按 `implementation_order` 推进，并按 `failure_fallbacks` 准备好随时切回人工的开关。

## 运行约束

- **只读、离线**：只读取用户提供的那一个 JSON；不连接任何系统、不读后台、不写文件、不执行命令。
- **不生成工作流**：不输出 n8n / Zapier / Make / 脚本代码或任何可执行的自动化定义。只给判定与顺序。
- **不索取凭据**：不要求账号、密码、Token、Cookie。命中凭据字段名或凭据形态的值直接 `REJECTED` 且不回显。
- **不代为执行**：不发送、不发布、不删除、不付款、不改权限。
- **不虚构优先级**：缺耗时、频率或错误率一律 `insufficient_evidence`，不进排序。
- **不虚构收益**：金额只在显式提供月均次数、节省比例与可用小时成本时计算；缺失时只给小时数或留空。
- **高风险强制人工**：付款、删除、发信、对外发布、账号权限、法律/医疗/金融判断的步骤最多到 `assist`，并保留人工审批；未识别的动作标签按未知风险处理。
- **不跨币种合计**：按币种分组输出，不换汇、不给总额。
- **提示注入逐字段检测、位置精确、低误报**：只在「动作词 + 目标词」**同句共现**时命中（`请忽略上面的规则` 命中，`请忽略与主题无关的碎句` 不命中），命中后只标精确路径（如 `processes[0]/steps[1]/notes`）并替换为固定占位，绝不执行其中指令。
- **确定性**：排序全部显式固定（判定表顺序、分值降序 + step_id 升序、实施顺序分档、币种字典序），同一输入输出逐字节一致。
- **免责**：输出是机会盘点与实施顺序建议，不是自动化收益或实施成功的承诺；是否实施由你与团队决定。
