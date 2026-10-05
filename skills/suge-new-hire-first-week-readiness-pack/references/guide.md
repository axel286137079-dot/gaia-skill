# 新员工首周入职准备包 — 字段表与判定顺序

本文件是 `suge-new-hire-first-week-readiness-pack` 的参考手册。脚本
`scripts/run.py` 只读一个本地 JSON，向 stdout 输出一个 JSON 文档。

- 常规样例：@references/sample.json（覆盖 READY / ACTION_NEEDED /
  WAITING_ON_OWNER 三种分区状态，并生成一条未发送内部提醒草稿）
- 阻塞样例：@references/sample-blocked.json（重复编号、非对象记录、未识别状态、
  非法会议区间、个人信息、受保护属性、拒绝引用、注入、无同意）

---

## 1. 输入字段

### 1.1 顶层

| 字段 | 必需 | 说明 |
|---|---|---|
| `as_of` | 是 | 判断当前时点的基准，**必须带时区偏移**（如 `2026-10-04T19:00:00+08:00`）。缺失或无法解析时输出 `INPUT_INCOMPLETE`。 |
| `organization` | 是 | 见 1.2；缺 `organization_id` 或 `timezone` 时输出 `INPUT_INCOMPLETE`。 |
| `new_hire` | 是 | 见 1.3；只接受匿名事实。 |
| `owners[]` | 建议 | 见 1.4。缺失时记 `MISSING_OWNERS`。 |
| `equipment[]` `access_items[]` `training_items[]` | 建议 | 见 1.5。**空列表**记 `MISSING_*_ITEMS`（准备度保持未知）。 |
| `meetings[]` `first_week_outcomes[]` `policy_acknowledgements[]` | 可选 | 见 1.5。空列表不报缺失。 |
| `communication_consent` | 建议 | 布尔。仅控制是否生成一条 `DRAFT_NOT_SENT` 内部提醒草稿；本工具**绝不外发**。 |
| `notes` | 否 | 自由文本；参与注入扫描。 |
| `evidence_refs[]` | 否 | 单一文件名列表；含路径或链接者被拒绝。 |

### 1.2 `organization`（全部来自用户事实，**不得内置默认**）

| 字段 | 必需 | 说明 |
|---|---|---|
| `organization_id` | 是 | 缺失记 `MISSING_ORGANIZATION_ID`（`INPUT_INCOMPLETE`）。 |
| `timezone` | 是 | 缺失记 `MISSING_TIMEZONE`（`INPUT_INCOMPLETE`）。 |
| `work_mode` | 否 | 如「混合办公」；仅作展示，不参与判定。 |
| `working_days[]` | 建议 | 整数 1–7（周一=1）。缺失记 `MISSING_WORKING_DAYS`；取值非法记 `INVALID_WORKING_DAYS`（阻塞）。**工具不假设默认工作日**。 |
| `first_week_availability[]` | 建议 | 可用时段，自由文本；缺失记 `MISSING_FIRST_WEEK_AVAILABILITY`。 |

### 1.3 `new_hire`（仅匿名事实）

| 字段 | 必需 | 说明 |
|---|---|---|
| `role_instance_id` | 是 | **匿名**岗位实例编号。缺失记 `MISSING_ROLE_INSTANCE_ID`（阻塞）。 |
| `role_title` | 否 | 岗位名，自由文本。 |
| `start_date` | 是 | `YYYY-MM-DD`。缺失记 `MISSING_START_DATE`；无法解析记 `INVALID_START_DATE`（阻塞）；早于 `as_of` 记 `START_DATE_IN_PAST`（阻塞）。 |
| `timezone` | 建议 | 缺失记 `MISSING_HIRE_TIMEZONE`；与组织时区不同记 `TIMEZONE_MISMATCH`（跨时区提示）。 |
| `work_location_mode` | 否 | 如「现场办公」，自由文本。 |

**禁止字段**（字段名或值形如个人信息）：姓名、`phone` / `mobile` / `tel`、
`email` / `mail`、`account`、`address`、`cookie`、`token`、证件号、**薪资/工资**等
→ 记 `refused_fields`（reason `PERSONAL_DATA`），该条目/输入 `BLOCKED`，**不回显该值**。
**受保护属性**（`gender` / `sex` / `age` / `race` / `ethnicity` / `religion` /
`health` / `disability` / `marital_status` / `nationality` / `politics` 等）→ reason
`SENSITIVE_ATTRIBUTE`，**工具绝不使用这些属性**。

### 1.4 `owners[]`

| 字段 | 必需 | 说明 |
|---|---|---|
| `owner_id` | 是 | 匿名编号；缺失记 `MISSING_ITEM_ID`，重复记 `DUPLICATE_ITEM_ID`（均阻塞）。 |
| `responsibilities[]` | 否 | 职责，自由文本。 |
| `availability[]` | 否 | 可用时段，自由文本。 |

负责人矩阵按 `owner_id` 稳定排序（缺失编号以路径兜底）。

### 1.5 条目（`equipment` / `access_items` / `training_items` / `meetings` / `first_week_outcomes` / `policy_acknowledgements`）

通用字段：

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | 是 | 匿名编号。缺失记 `MISSING_ITEM_ID`；重复让所有同号条目记 `DUPLICATE_ITEM_ID`（均阻塞）。 |
| `status` | 是 | 显式状态，封闭词表见 §2.1。缺失记 `MISSING_ITEM_STATUS`（未知）；表外值记 `INVALID_ITEM_STATUS`（阻塞）。 |
| `owner_id` | 是 | 必须出现在 `owners[]`；缺失记 `MISSING_OWNER`，不在清单记 `UNKNOWN_OWNER`（均未知）。 |
| `due_at` | 建议 | 带偏移时间。无法解析记 `INVALID_DUE_AT`（阻塞）；未完成且早于 `as_of` 记 `OVERDUE_ITEM`；`equipment`/`access_items` 到期晚于入职日记 `DUE_AFTER_START`。 |
| `label` | 否 | 自由文本，参与注入扫描。 |
| `evidence_refs[]` | 建议 | 单一文件名；含路径/链接者被拒绝。`first_week_outcomes` 的必需条目**必须**有证据，否则记 `MISSING_EVIDENCE`。 |
| `required` | 否 | 布尔。显式 `false`（或 `status: NOT_APPLICABLE`）的条目为**可选项**，不参与阻塞聚合，但会照常列出。 |

`meetings` 额外使用 `start_at` / `end_at`（带偏移）。缺失记 `MISSING_MEETING_TIME`；
无法解析记 `INVALID_MEETING_TIME`；结束不晚于开始记 `INVALID_MEETING_RANGE`（均阻塞）；
两个会议时间重叠记 `MEETING_OVERLAP`。

---

## 2. 判定顺序

### 2.1 条目状态词表

```
DONE  SCHEDULED  PENDING  WAITING_ON_OWNER  BLOCKED  NOT_APPLICABLE  UNKNOWN
```

`DONE` / `SCHEDULED` / `NOT_APPLICABLE` 视为就绪；`PENDING` 为待处理；`WAITING_ON_OWNER`
为等待负责人；`BLOCKED` 为显式阻塞；缺失或 `UNKNOWN` 保持未知。接受少量同义词
（`READY`→`DONE`、`PLANNED`→`SCHEDULED`、`TODO`→`PENDING`、`WAITING`→`WAITING_ON_OWNER`、
`NA`→`NOT_APPLICABLE`），其余表外值一律 `INVALID_ITEM_STATUS`。

### 2.2 条目判定（逐条）

```
硬阻塞（任一命中 → BLOCKED）
  INVALID_ITEM_RECORD  MISSING_ITEM_ID  DUPLICATE_ITEM_ID
  INVALID_ITEM_STATUS  BLOCKED_ITEM  INVALID_DUE_AT
  INVALID_MEETING_TIME  INVALID_MEETING_RANGE
  PERSONAL_DATA_REFUSED  SENSITIVE_ATTRIBUTE_REFUSED

证据不足（任一命中 → INSUFFICIENT_EVIDENCE；未知不当作已完成）
  MISSING_ITEM_STATUS  MISSING_OWNER  UNKNOWN_OWNER  MISSING_DUE_AT
  MISSING_MEETING_TIME  MISSING_EVIDENCE

等待负责人（任一命中 → WAITING_ON_OWNER）
  WAITING_ON_OWNER（条目显式状态）

待处理（任一命中 → ACTION_NEEDED）
  OVERDUE_ITEM  PENDING_ITEM  DUE_AFTER_START  MEETING_OVERLAP
```

`required: false` 的条目**不参与**以上聚合，但仍在 `items[]` 中列出其状态。

### 2.3 组织 / 新员工级

- 缺 `as_of` / `organization_id` / `timezone` → 顶层 `INPUT_INCOMPLETE`（不进入五态）。
- `INVALID_START_DATE`、`START_DATE_IN_PAST`、`MISSING_ROLE_INSTANCE_ID`、
  `INVALID_WORKING_DAYS` → 阻塞。
- `MISSING_START_DATE`、`MISSING_HIRE_TIMEZONE`、`MISSING_WORKING_DAYS`、
  `MISSING_FIRST_WEEK_AVAILABILITY`、`MISSING_OWNERS` → 未知。
- `TIMEZONE_MISMATCH`、`START_ON_NON_WORKING_DAY` → 待处理。

### 2.4 五态聚合（状态取最严重者）

```
BLOCKED > INSUFFICIENT_EVIDENCE > WAITING_ON_OWNER > ACTION_NEEDED > READY_FOR_DAY_ONE
```

`READY_FOR_DAY_ONE` **仅在**必需项事实齐全、无未知、无等待、无待处理、无阻塞时出现；
任何未知或缺失都保留为 `INSUFFICIENT_EVIDENCE`，**未知绝不当作已完成**。

### 2.5 各分区（area）

按固定顺序 `equipment / access / training / meetings / outcomes / policy` 输出，
每个分区取其**必需条目**里最严重的状态；空必需列表记为未知。

### 2.6 首日清单与首周日程

- `day_one_checklist`：到期时间早于或等于入职日的条目（按 `due_at` 稳定排序）。
- `first_week_schedule`：从入职日起 7 个自然日，标注是否工作日与当日会议、到期事项；
  缺工作日时 `is_working_day` 为 `null`（不猜）。

### 2.7 内部提醒草稿（同意门禁）

- **仅当** `communication_consent: true` **且**总体状态为 `ACTION_NEEDED` 或
  `WAITING_ON_OWNER` 时，生成一条 `DRAFT_NOT_SENT` 内部提醒草稿。
- `false` / 缺失 / 非布尔 → **不生成**；`BLOCKED` / `INSUFFICIENT_EVIDENCE` → **不生成**。
- 任何草稿**绝不外发**，收件人只列 `owner_id`。

### 2.8 安全

- **凭据**：字段名命中 `api_key`/`secret`/`password`/`token`/... 或值形如密钥 →
  整体 `REJECTED`，只输出字段路径，不回显值。
- **个人信息 / 受保护属性**：字段名或值命中 → `refused_fields`（只含路径与原因），
  该条目 `BLOCKED`，值在任何输出中都不出现。
- **文件名**：`evidence_refs` 只接受单一文件名；含 `/`、`\`、`:`、`..` 记
  `PATH_REFERENCE`，含 scheme 记 `URL_REFERENCE`，只回流字段路径，不回显原引用。
- **注入**：动作词 + 目标词同句共现才标记，`injection_flagged` 给出精确路径；
  `markdown_summary` 中该值替换为「已隐藏疑似提示注入文本」。
- 控制字符被剥离；Markdown 元字符加反斜杠转义，不可信文本无法伪造格式。

---

## 3. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | `REJECTED` / `INPUT_INCOMPLETE` / 五态之一 |
| `readiness_state` | 五态之一（`READY_FOR_DAY_ONE` / `ACTION_NEEDED` / `WAITING_ON_OWNER` / `INSUFFICIENT_EVIDENCE` / `BLOCKED`） |
| `organization` / `new_hire` | 规范化后的匿名事实 |
| `readiness_areas` / `area_states` | 六个分区的状态与未解决条目 |
| `item_state_counts` | 条目五态计数 |
| `items[]` | 每条目的状态、状态码与证据引用 |
| `day_one_checklist` | 首日到期事项 |
| `first_week_schedule` | 首周 7 天日程（工作日/会议/到期） |
| `owner_matrix` | 每位负责人的分工、可用时段、负责与未解决条目 |
| `blockers` / `missing_facts` / `time_conflicts` / `overdue_items` | 阻塞、缺失事实、时间冲突、逾期 |
| `policy_confirmations` | 政策确认清单（**仅登记确认，不作合规判断**） |
| `refused_fields` / `refused_refs` | 被拒绝的个人/受保护属性字段与引用（只含路径与原因） |
| `injection_flagged` | 注入命中路径 |
| `communication_consent` / `reminder_draft` | 同意事实与草稿（恒 `DRAFT_NOT_SENT` 或 `null`） |
| `clarification_questions` / `human_checklist` / `human_confirm_items` | 澄清问题与人工复核清单 |
| `next_step` | 下一步提示 |
| `markdown_summary` | Markdown 准备卡 |
| `disclaimer` / `automation_declaration` | 免责与边界声明 |
| `read_only` / `network` / `writes_files` | 恒为 `true` / `false` / `false` |

---

## 4. 边界

本工具不读取任何 HRIS/邮箱/日历/账号系统、不自动开通或回收账号、不采购设备、
不发送消息、不作出录用或绩效判断、不使用受保护属性、不处理薪资税务、不生成劳动合同、
不给出法律结论、不联网、无子进程、不写文件。所有结论只由输入中的明示匿名事实推导；
缺失信息保持未知，必须由人工确认。
