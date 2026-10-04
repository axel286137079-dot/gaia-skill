# 销售线索接入完整度与下一步优先包 — 字段表与判定顺序

本文件是 `suge-lead-intake-readiness-priority-pack` 的参考手册。脚本
`scripts/run.py` 只读一个本地 JSON，向 stdout 输出一个 JSON 文档。

- 常规样例：@references/sample.json（八条线索覆盖六种状态）
- 阻塞样例：@references/sample-blocked.json（重复编号、非对象记录、个人信息、
  受保护属性、拒绝引用、注入、无同意）

---

## 1. 输入字段

### 1.1 顶层

| 字段 | 必需 | 说明 |
|---|---|---|
| `as_of` | 是 | 判断当前时点的基准，**必须带时区偏移**（如 `2026-10-03T19:00:00+08:00`）。缺失或无法解析时输出 `INPUT_INCOMPLETE`。 |
| `business` | 是 | 见 1.2；缺 `business_id` 或 `timezone` 时输出 `INPUT_INCOMPLETE`。 |
| `leads[]` | 建议 | 见 1.3。只接受匿名事实，**禁止**姓名、手机号、邮箱、账号、地址、Cookie、令牌等。 |
| `qualification_rules` | 建议 | 见 1.4。缺失时记 `MISSING_QUALIFICATION_RULES`，**不代用户发明销售策略**。 |
| `capacity` | 建议 | 见 1.5。只用于形成排队建议，**不作承诺**。 |
| `notes` | 否 | 自由文本；参与注入扫描。 |
| `evidence_refs[]` | 否 | 单一文件名列表；含路径或链接者被拒绝。 |

### 1.2 `business`（全部来自用户事实，**不得内置默认**）

| 字段 | 必需 | 说明 |
|---|---|---|
| `business_id` | 是 | 缺失记 `MISSING_BUSINESS_ID`（`INPUT_INCOMPLETE`）。 |
| `timezone` | 是 | 缺失记 `MISSING_TIMEZONE`（`INPUT_INCOMPLETE`）。 |
| `product_scope[]` | 建议 | 提供的产品/服务范围；缺失记 `MISSING_PRODUCT_SCOPE`，产品适配保持未知。 |
| `service_area[]` | 建议 | 服务区域；缺失记 `MISSING_SERVICE_AREA_SCOPE`，区域适配保持未知。 |
| `out_of_scope[]` | 否 | **明确不服务**的范围；命中者记 `PRODUCT_IN_EXCLUDED_SCOPE`。 |
| `channels[]` | 建议 | 可用渠道；缺失记 `MISSING_BUSINESS_CHANNELS`。 |

### 1.3 `leads[]`（仅匿名事实）

| 字段 | 必需 | 说明 |
|---|---|---|
| `lead_id` | 是 | **匿名**编号。缺失记 `MISSING_LEAD_ID`；重复让所有同号记录记 `DUPLICATE_LEAD_ID`，一并 `BLOCKED`。 |
| `source` | 是 | 来源渠道；缺失记 `MISSING_SOURCE`。 |
| `received_at` | 是 | 带偏移时间。缺失记 `MISSING_RECEIVED_AT`；无法解析记 `INVALID_RECEIVED_AT`；晚于 `as_of` 记 `RECEIVED_IN_FUTURE`（均为阻塞）。 |
| `requested_outcome` | 是 | 期望结果；缺失记 `MISSING_REQUESTED_OUTCOME`。 |
| `product_or_service` | 是 | 咨询的产品/服务；缺失记 `MISSING_PRODUCT_OR_SERVICE`。 |
| `service_area` | 建议 | 服务区域；**缺失时不猜**，记 `MISSING_SERVICE_AREA`，区域适配保持未知。 |
| `deadline` | 建议 | 带偏移时间。缺失记 `MISSING_DEADLINE`；无法解析记 `INVALID_DEADLINE`；早于 `received_at` 记 `DEADLINE_BEFORE_RECEIVED`（阻塞）；早于 `as_of` 记 `DEADLINE_EXPIRED`。 |
| `budget_status` | 建议 | 封闭词表：`provided` / `not_provided` / `not_applicable`。`not_provided` 记 `BUDGET_NOT_PROVIDED`；缺失或表外记 `BUDGET_STATUS_UNKNOWN`。**未提供预算不等于低价值**。 |
| `decision_timing` | 建议 | 决策时间点；缺失记 `MISSING_DECISION_TIMING`。 |
| `evidence_refs[]` | 否 | 单一文件名列表；含路径/链接者被拒绝。 |
| `contact_consent` | 建议 | 布尔。`true` 才允许生成草稿；`false` 记 `CONSENT_NOT_GRANTED`；缺失或非布尔记 `CONSENT_UNKNOWN`。 |

**禁止字段**（字段名或值形如个人信息）：姓名、`phone` / `mobile` / `tel`、
`email` / `mail`、`account`、`address`、`cookie`、`session_id`、`token`、
`password`、证件号等 → 记 `refused_fields`（reason `PERSONAL_DATA`），该线索
`BLOCKED`，**不回显该值**。**受保护属性**（`gender` / `sex` / `age` / `race` /
`ethnicity` / `religion` / `health` / `disability` / `marital_status` /
`nationality` / `politics` 等）→ reason `SENSITIVE_ATTRIBUTE`，该线索 `BLOCKED`，
**工具绝不使用这些属性**。

### 1.4 `qualification_rules`（没有规则就保持未知）

| 字段 | 说明 |
|---|---|
| `product_service_terms[]` | 附加的在范围内产品/服务词；与 `business.product_scope` 取并集。 |
| `service_area_terms[]` | 附加的在范围内区域词；与 `business.service_area` 取并集。 |
| `minimum_required_facts[]` | 额外必需事实；缺失记 `MISSING_REQUIRED_FACT`。核心事实（需求/产品/来源/接收时间）始终检查。 |
| `timeliness.urgent_within_hours` | 数字；`deadline` 落在该窗口内进入 `BAND_URGENT`。缺失时不做紧急判定，**不猜**。 |
| `priority_order[]` | 次级排序键，取值 `deadline` / `received_at` / `lead_id`；缺省为三者。 |

### 1.5 `capacity`

| 字段 | 说明 |
|---|---|
| `available_slots` | 非负整数；人工可处理名额。缺失记 `MISSING_CAPACITY`，排队建议不计算。 |
| `windows[]` | 可用时段，自由文本。 |

---

## 2. 判定顺序

### 2.1 单条线索

```
硬阻塞（任一命中 → BLOCKED）
  INVALID_LEAD_RECORD  MISSING_LEAD_ID  DUPLICATE_LEAD_ID
  PERSONAL_DATA_REFUSED  SENSITIVE_ATTRIBUTE_REFUSED
  INVALID_RECEIVED_AT  INVALID_DEADLINE  RECEIVED_IN_FUTURE
  DEADLINE_BEFORE_RECEIVED

证据不足（任一命中 → INSUFFICIENT_EVIDENCE）
  MISSING_REQUESTED_OUTCOME  MISSING_PRODUCT_OR_SERVICE  MISSING_SOURCE
  MISSING_RECEIVED_AT  MISSING_REQUIRED_FACT

超出声明范围（任一命中 → OUT_OF_DECLARED_SCOPE）
  PRODUCT_IN_EXCLUDED_SCOPE  PRODUCT_OUT_OF_DECLARED_SCOPE
  AREA_OUT_OF_DECLARED_SCOPE  AREA_BOUNDARY_REVIEW

等待同意（任一命中 → WAITING_FOR_CONSENT）
  CONSENT_NOT_GRANTED  CONSENT_UNKNOWN

需澄清（任一命中 → NEEDS_CLARIFICATION）
  DEADLINE_EXPIRED  MISSING_DEADLINE  MISSING_SERVICE_AREA
  BUDGET_NOT_PROVIDED  BUDGET_STATUS_UNKNOWN  MISSING_DECISION_TIMING
```

状态取命中最严重的一档（`BLOCKED` > `INSUFFICIENT_EVIDENCE` >
`OUT_OF_DECLARED_SCOPE` > `WAITING_FOR_CONSENT` > `NEEDS_CLARIFICATION` >
`READY_FOR_HUMAN_FOLLOWUP`）；都不命中 → `READY_FOR_HUMAN_FOLLOWUP`。

### 2.2 范围适配

- 产品：规范化（去空白、大小写折叠）后，命中 `out_of_scope` 记
  `PRODUCT_IN_EXCLUDED_SCOPE`；命中在范围内词记 `IN`；声明了范围但不匹配记
  `PRODUCT_OUT_OF_DECLARED_SCOPE`；未声明任何产品范围则 `UNKNOWN`。
- 区域：精确匹配记 `IN`；与某声明区域互为子串（如「徐汇区」与「上海市徐汇区」）
  记 `BOUNDARY` + `AREA_BOUNDARY_REVIEW`；不匹配记 `AREA_OUT_OF_DECLARED_SCOPE`；
  区域缺失记 `UNKNOWN` + `MISSING_SERVICE_AREA`，**不猜**。
- `OUT_OF_DECLARED_SCOPE` 只是依据用户显式范围的**人工复核提示**，不是拒客决定。

### 2.3 优先带（不输出客户价值分）

优先带只由状态、时效与容量推导，**不推断价值、意愿、信用或受保护属性**：

```
BAND_BLOCKED_INPUT        BLOCKED
BAND_MISSING_FACTS        INSUFFICIENT_EVIDENCE
BAND_SCOPE_REVIEW         OUT_OF_DECLARED_SCOPE
BAND_CONSENT_REVIEW       WAITING_FOR_CONSENT
BAND_NEEDS_CLARIFICATION  NEEDS_CLARIFICATION
BAND_URGENT               READY 且 deadline 落在 timeliness 窗口内
BAND_STANDARD             其余 READY
```

`queue` 按 `priority_band` → `priority_order`（缺省 `deadline`、`received_at`、
`lead_id`）稳定排序；同键以 `lead_id`/路径兜底，保证**逐字节可复现**。

### 2.4 容量

- `capacity_summary`：`available_slots`、`windows`、`followup_candidates`
  （`READY` 条数）、`within_capacity`、`overflow`。
- `READY` 线索按队列顺序给出 `followup_rank` 与 `within_capacity`。
- 容量缺失时 `available_slots` 为 `null`，**不猜名额**。
- 容量**只用于排队建议，不作任何交付或时效承诺**。

### 2.5 草稿与联系同意（门禁）

- 仅当 `contact_consent: true` **且**线索状态为 `NEEDS_CLARIFICATION` 或
  `INSUFFICIENT_EVIDENCE`（确有待澄清事实）时，生成一条 `DRAFT_NOT_SENT` 澄清草稿。
- `false` → 记录明确拒绝，**不生成草稿**；缺失/非布尔 → 未知，**不生成草稿**并列入检查表。
- 任何草稿**绝不外发**。

### 2.6 安全

- **凭据**：字段名命中 `api_key`/`secret`/`password`/`token`/... 或值形如密钥 →
  整体 `REJECTED`，只输出字段路径，不回显值。
- **个人信息 / 受保护属性**：字段名或值命中 → `refused_fields`（只含路径与原因），
  该线索 `BLOCKED`，值在任何输出中都不出现（渲染为占位或省略）。
- **文件名**：`evidence_refs` 只接受单一文件名；含 `/`、`\`、`:`、`..` 记
  `PATH_REFERENCE`，含 scheme 记 `URL_REFERENCE`，只回流字段路径，不回显原引用。
- **注入**：动作词 + 目标词同句共现才标记，`injection_flagged` 给出精确路径；
  `markdown_summary` 中该值替换为「已隐藏疑似提示注入文本」。
- 控制字符被剥离；Markdown 元字符加反斜杠转义，不可信文本无法伪造格式。

---

## 3. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | `REJECTED` / `INPUT_INCOMPLETE` / `BLOCKED` / `GAPS_FOUND` / `READY` |
| `project_state` / `project_status` | 六类状态之一（同值） |
| `status_counts` | 六个状态的线索计数 |
| `business` | 规范化后的业务范围与渠道 |
| `leads[]` | 每条线索的状态、优先带、findings、范围适配与容量位次 |
| `queue` | 稳定排序的人工复核队列 |
| `priority_order_used` | 实际使用的次级排序键 |
| `capacity_summary` | 容量与排队建议（不作承诺） |
| `intake_completeness` | 接入完整度清单（逐字段是否提供） |
| `scope_evidence` | 范围适配证据（产品/区域及判定码） |
| `next_step_cards` | 每条线索的下一步准备卡 |
| `missing_facts` | 缺失事实（路径 + 判定码） |
| `timeline_conflicts` | 时间冲突 |
| `refused_fields` | 被拒绝的个人信息 / 受保护属性字段（只含路径与原因） |
| `refused_refs` | 被拒绝的引用（路径 + 原因） |
| `injection_flagged` | 注入命中路径 |
| `contact_consent` | granted / refused / unknown |
| `drafts[]` | 澄清草稿，恒为 `DRAFT_NOT_SENT` |
| `clarification_questions` | 澄清问题 |
| `human_checklist` | 人工复核检查表 |
| `human_confirm_items` | 必须人工确认的事项 |
| `markdown_summary` | Markdown 优先卡 |
| `disclaimer` / `automation_declaration` | 免责与边界声明 |
| `read_only` / `network` / `writes_files` | 恒为 `true` / `false` / `false` |

---

## 4. 边界

本工具不抓取线索、不读取 CRM/邮箱/私信、不自动联系、不自动淘汰客户、不承诺
价格/折扣/交期、不使用受保护属性、不推断购买意愿、不联网、无子进程、不写文件。
所有结论只由输入中的明示事实推导；缺失信息保持未知，必须由人工确认。
