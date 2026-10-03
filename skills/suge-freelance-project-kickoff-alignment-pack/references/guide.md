# 自由职业项目启动对齐包 — 字段表与判定顺序

本文件是 `suge-freelance-project-kickoff-alignment-pack` 的参考手册。脚本
`scripts/run.py` 只读一个本地 JSON，向 stdout 输出一个 JSON 文档。

- 常规样例：@references/sample.json（五个交付物分别落在五种状态）
- 阻塞样例：@references/sample-blocked.json（重复编号、非对象记录、拒绝引用、注入、循环依赖）

---

## 1. 输入字段

### 1.1 顶层

| 字段 | 必需 | 说明 |
|---|---|---|
| `as_of` | 是 | 判断当前时点的基准，**必须带时区偏移**（如 `2026-10-02T19:00:00+08:00`）。缺失或无法解析时输出 `INPUT_INCOMPLETE`。 |
| `project` | 是 | 见 1.2 |
| `parties[]` | 建议 | 见 1.3 |
| `deliverables[]` | 建议 | 见 1.4 |
| `approvals[]` | 建议 | 见 1.5 |
| `client_inputs[]` | 建议 | 见 1.6 |
| `communication` | 建议 | 见 1.7 |
| `change_process` | 建议 | 见 1.8 |
| `notes` | 否 | 自由文本；参与注入扫描。 |
| `evidence_refs[]` | 否 | 单一文件名列表；含路径或链接者被拒绝。 |

### 1.2 `project`

| 字段 | 必需 | 说明 |
|---|---|---|
| `project_id` | 是 | 缺失记 `MISSING_PROJECT_ID`（`INPUT_INCOMPLETE`）。 |
| `name` | 否 | 缺失记 `MISSING_PROJECT_NAME`（证据不足）。 |
| `start_at` | 是 | 带偏移时间。缺失记 `MISSING_START_AT`；无法解析记 `INVALID_START_AT`（均 `INPUT_INCOMPLETE`）。 |
| `target_delivery_at` | 否 | 带偏移时间；缺失记 `MISSING_TARGET_DELIVERY_AT`；无法解析记 `INVALID_TARGET_DELIVERY_AT`（`INPUT_INCOMPLETE`）；早于 `start_at` 记 `TARGET_BEFORE_START`（`BLOCKED`）。 |
| `timezone` | 是 | 缺失记 `MISSING_TIMEZONE`（`INPUT_INCOMPLETE`）。 |

### 1.3 `parties[]`

| 字段 | 说明 |
|---|---|
| `party_id` | 唯一编号；重复记 `DUPLICATE_PARTY_ID`。 |
| `role` | 角色，自由文本。 |
| `decision_authority` | 布尔；非布尔按未知。 |
| `contact_consent` | 布尔。`true` 才允许生成草稿；`false` 记明确拒绝；缺失记 `CONTACT_CONSENT_UNKNOWN`。 |

### 1.4 `deliverables[]`

| 字段 | 说明 |
|---|---|
| `deliverable_id` | 唯一编号。缺失记 `MISSING_DELIVERABLE_ID`；重复让所有同号记录记 `DUPLICATE_DELIVERABLE_ID`，一并 `BLOCKED`。 |
| `description` | 缺失记 `MISSING_DESCRIPTION`。 |
| `format` | 缺失记 `MISSING_FORMAT`。 |
| `quantity` | 数字或文本；缺失记 `MISSING_QUANTITY`。布尔不计为数量。 |
| `due_at` | 带偏移时间。缺失记 `MISSING_DUE_AT`；无法解析记 `INVALID_DUE_AT`（阻塞）；早于 `start_at` 记 `DUE_BEFORE_START`（阻塞）；晚于 `target_delivery_at` 记 `DUE_AFTER_TARGET`（推进 `ACTION_NEEDED`）。 |
| `acceptance_criteria[]` | 非空字符串列表；空或缺失记 `MISSING_ACCEPTANCE_CRITERIA`。 |
| `approver_party_id` | 缺失记 `MISSING_APPROVER`；不在 `parties` 中记 `UNKNOWN_APPROVER`（阻塞）。 |
| `dependencies[]` | 交付物编号列表。指向不存在编号记 `UNKNOWN_DEPENDENCY`（阻塞）。 |
| `out_of_scope[]` | 非空字符串列表；空或缺失记 `MISSING_OUT_OF_SCOPE`。 |

### 1.5 `approvals[]`

| 字段 | 说明 |
|---|---|
| `approval_id` | 编号（可选，用于输出标识）。 |
| `subject` | 审批对象描述。 |
| `owner_party_id` | 审批负责人；缺失记责任缺口，未知记 `UNKNOWN_PARTY`。 |
| `deliverable_id` | 关联交付物编号；用于把审批结论挂到交付物上。 |
| `status` | 封闭词表：`approved` / `pending` / `not_requested` / `rejected`；表外或缺失记 `UNKNOWN_APPROVAL_STATUS`。 |
| `due_at` | 截止时间；早于 `as_of` 且未完成者输出 `overdue`。 |
| `evidence_ref` | 单一文件名；含路径/链接被拒绝。 |

### 1.6 `client_inputs[]`

| 字段 | 说明 |
|---|---|
| `input_id` | 编号（可选）。 |
| `kind` | 类别，自由文本（素材/账号/文本/反馈等）。 |
| `description` | 描述。 |
| `owner_party_id` | 负责人；缺失记责任缺口。 |
| `deliverable_id` | 关联交付物编号（可选）。 |
| `status` | 封闭词表：`received` / `not_received` / `partial` / `waived`；表外或缺失记 `UNKNOWN_INPUT_STATUS` 且按未收到进入等待。 |
| `due_at` | 到期时间；早于 `as_of` 且未完成者 `overdue=true`。 |
| `evidence_ref` | 单一文件名；含路径/链接被拒绝。 |

### 1.7 `communication`（全部来自用户事实，无内置默认）

| 字段 | 缺失时 |
|---|---|
| `channel` | `MISSING_COMMUNICATION_CHANNEL` |
| `cadence` | `MISSING_COMMUNICATION_CADENCE` |
| `response_target` | `MISSING_COMMUNICATION_RESPONSE_TARGET` |
| `urgent_escalation` | `MISSING_COMMUNICATION_ESCALATION` |

整段 `communication` 缺失时记单条 `MISSING_COMMUNICATION`。

### 1.8 `change_process`

四个步骤 `propose` / `estimate` / `approve` / `schedule`。整段缺失记
`MISSING_CHANGE_PROCESS`；缺任一步骤记 `MISSING_CHANGE_PROCESS_STEP`。

---

## 2. 判定顺序

### 2.1 交付物

```
硬阻塞（任一命中 → BLOCKED）
  INVALID_DELIVERABLE_RECORD  MISSING_DELIVERABLE_ID  DUPLICATE_DELIVERABLE_ID
  UNKNOWN_APPROVER  UNKNOWN_DEPENDENCY  DEPENDENCY_CYCLE
  INVALID_DUE_AT  DUE_BEFORE_START

证据不足（任一命中 → INSUFFICIENT_EVIDENCE）
  MISSING_DESCRIPTION  MISSING_FORMAT  MISSING_QUANTITY
  MISSING_ACCEPTANCE_CRITERIA  MISSING_APPROVER
  MISSING_APPROVAL_RECORD  UNKNOWN_APPROVAL_STATUS  MISSING_OUT_OF_SCOPE
  MISSING_DUE_AT

等待客户（任一命中 → WAITING_ON_CLIENT）
  CLIENT_INPUT_PENDING  UNKNOWN_INPUT_STATUS

待推进（任一命中 → ACTION_NEEDED）
  APPROVAL_PENDING  APPROVAL_NOT_REQUESTED  APPROVAL_REJECTED  DUE_AFTER_TARGET
```

交付物状态 = 命中最严重的一档；都不命中 → `READY`。

### 2.2 项目

1. `project_blockers` 含 `MISSING_PROJECT_ID` / `MISSING_START_AT` /
   `MISSING_TIMEZONE` / `INVALID_START_AT` / `INVALID_TARGET_DELIVERY_AT`
   → 顶层 `status = INPUT_INCOMPLETE`。
2. 否则若项目级硬阻塞或任一交付物 `BLOCKED` → `BLOCKED`。
3. 否则若项目级证据缺口或任一交付物 `INSUFFICIENT_EVIDENCE` →
   `INSUFFICIENT_EVIDENCE`。
4. 否则若任一交付物 `WAITING_ON_CLIENT` 或存在 `waiting_on_client` 条目
   → `WAITING_ON_CLIENT`。
5. 否则若任一交付物 `ACTION_NEEDED` → `ACTION_NEEDED`。
6. 否则 → `READY`。

顶层 `status` 映射：`INPUT_INCOMPLETE` 保持；否则 `BLOCKED`→`BLOCKED`、
`READY`→`READY`、其余→`GAPS_FOUND`。凭据命中时直接输出 `REJECTED`。

### 2.3 依赖与根阻塞

- 依赖图节点 = 有编号的交付物；边 `from=依赖编号, to=本交付物`，仅保留指向存在编号的边。
- `dependency_cycles`：强连通分量中成员数 > 1，或自环。
- `root_blockers`：状态为 `BLOCKED` 且其依赖中没有任何 `BLOCKED` 节点的交付物。

### 2.4 等待客户

- 每条未完成（非 `received` / `waived`）的 `client_inputs` 记一条 `CLIENT_INPUT`。
- 每条状态为 `pending` / `not_requested` / 未知的 `approvals` 记一条 `APPROVAL`。
- `overdue = due_at < as_of`。

### 2.5 草稿与联系同意

- `contact_consent=true` → 该参与方名下有待确认事项时生成一条 `DRAFT_NOT_SENT` 草稿。
- `contact_consent=false` → 记录明确拒绝，**不生成草稿**。
- `contact_consent` 缺失 → 记 `CONTACT_CONSENT_UNKNOWN`，**不生成草稿**，列入人工检查表。

### 2.6 安全

- 凭据：字段名命中 `api_key`/`secret`/`password`/`token`/... 或值形如密钥 → 整体 `REJECTED`，只输出字段路径，不回显值。
- 文件名：`evidence_ref` / `evidence_refs` 只接受单一文件名；含 `/`、`\`、`:`、`..` 记 `PATH_REFERENCE`，含 scheme 记 `URL_REFERENCE`，只回流字段路径，不回显原引用。
- 注入：动作词 + 目标词同句共现才标记，`injection_flagged` 给出精确路径；`markdown_summary` 中该值替换为「已隐藏疑似提示注入文本」。
- 控制字符被剥离；Markdown 元字符加反斜杠转义，不可信文本无法伪造格式。

---

## 3. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | `REJECTED` / `INPUT_INCOMPLETE` / `BLOCKED` / `GAPS_FOUND` / `READY` |
| `project_state` / `project_status` | 项目级五类状态（同值） |
| `status_counts` | 五个状态的交付物计数 |
| `deliverables[]` | 每个交付物的状态、findings 与全部判定码 |
| `alignment_card` | 范围内外、验收标准、审批人、客户输入、沟通、升级路径、变更流程 |
| `dependency_graph` | 节点与边 |
| `root_blockers` | 根阻塞项 |
| `dependency_cycles` | 循环依赖成员 |
| `waiting_on_client` | 等待客户清单（含 `overdue`） |
| `pending_approvals` | 未完成的审批 |
| `missing_facts` | 缺失事实（路径 + 判定码） |
| `timeline_conflicts` | 时间线冲突 |
| `responsibility_gaps` | 责任缺口 |
| `duplicate_ids` | 重复编号 |
| `unknown_parties` | 未知责任方引用 |
| `refused_refs` | 被拒绝的引用 |
| `injection_flagged` | 注入命中路径 |
| `contact_consent` | granted / refused / unknown |
| `drafts[]` | 草稿，恒为 `DRAFT_NOT_SENT` |
| `clarification_questions` | 澄清问题 |
| `human_checklist` | 人工启动检查表 |
| `human_confirm_items` | 必须人工确认的事项 |
| `markdown_summary` | Markdown 对齐卡 |
| `disclaimer` / `automation_declaration` | 免责与边界声明 |
| `read_only` / `network` / `writes_files` | 恒为 `true` / `false` / `false` |

---

## 4. 边界

本工具不签合同、不接受范围/价格/工期变更、不承诺价格与交期、不自动联系客户、
不写任何 PM/CRM、不上传文件、不推断法律条款、不联网、无子进程、不写文件。
所有结论只由输入中的明示事实推导；缺失信息保持未知，必须由双方人工确认。
