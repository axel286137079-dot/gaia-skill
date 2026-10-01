# 上门服务出发前工单准备包 — 字段表与判定规则

本文件是 `scripts/run.py` 的唯一规则来源。脚本只读一个 JSON 文件，输出一个 JSON 文档。

## 1. 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | ISO 时间戳，**必须带时区偏移**（如 `2026-09-29T07:30:00+08:00`）。缺失或格式无效时整体 `INPUT_INCOMPLETE`。 |
| `work_orders` | 是 | 工单数组。为空或缺失时整体 `INPUT_INCOMPLETE`。 |

顶层其它未知字段一律忽略，不参与判定。

## 2. 工单字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `work_order_id` | 建议 | 工单编号；缺失时输出用 `work_orders[i]` 作为标识。 |
| `visit_type` | 是 | `diagnosis` / `installation` / `followup` / `maintenance`。其它值记 `INVALID_VISIT_TYPE` 并阻塞。 |
| `appointment` | 是 | `{start, end, timezone}`；`start`/`end` 必须带时区偏移且 `end >= start`。 |
| `site` | 是 | `{contact, access_notes, access_confirmed}`；`access_confirmed` 为三态布尔。 |
| `approved_scope` | 建议 | 已批准范围的文本，供交接单引用；缺失记 `SCOPE_TEXT_MISSING` 并追问。 |
| `approval` | 是 | `{scope_approved, approved_by, approval_ref}`；`scope_approved` 为三态布尔。 |
| `equipment` | 是 | 设备事实数组，每项 `{equipment_id, label, model, known}`；`known` 为三态布尔。 |
| `required_skills` | 建议 | 本次作业所需技能字符串数组。 |
| `assigned_technician` | 建议 | `{technician_id, skills[], available_windows[]}`。 |
| `parts` | 建议 | 零件数组，每项 `{part_id, description, state, required}`。 |
| `tools` | 建议 | 工具数组，每项 `{tool_id, name, required, available}`。 |
| `documents` | 建议 | 文档数组，每项 `{doc_id, basename, required, available}`；`basename` 只接受单一文件名。 |
| `owner` | 建议 | 责任人。 |
| `due_at` | 建议 | 截止时间，带时区偏移；早于 `as_of` 记 `OVERDUE`。 |

`required` 缺失时默认按 `true` 处理（保守：假定它是必要的）。

## 3. 零件状态与证据链

`state` 取值限定为 `ordered` / `received` / `inspected` / `reserved` / `unknown`（大小写不敏感，其它值一律归为 `unknown`）。

证据链是**单调**的：

| state | ordered | received | inspected | reserved |
|---|---|---|---|---|
| `ordered` | 是 | 否 | 否 | 否 |
| `received` | 是 | 是 | 否 | 否 |
| `inspected` | 是 | 是 | 是 | 否 |
| `reserved` | 是 | 是 | 是 | 是 |
| `unknown` | 否 | 否 | 否 | 否 |

**只有 `reserved` 满足门禁。** `ordered` 单独存在永远不满足。

### 零件门禁适用的工单类型

`PART_GATING_VISIT_TYPES = ("installation", "maintenance")`。

- 安装与保养：每个 `required` 零件必须是 `reserved`，否则记 `PART_NOT_RESERVED` 并**阻塞**；状态为 `unknown` 记 `UNKNOWN_PART_STATE` 并追问。
- 诊断与回访：零件**不参与门禁**（上门诊断时维修零件本就不确定），只在 `parts_evidence` 中如实列出。

这条规则是刻意的：`BRIEF` 明确要求「诊断工单不因未有维修零件被错误阻塞」。

## 4. 逐工单判定

按固定顺序计算：

### 4.1 阻塞项（任一出现即为 `BLOCKED`）

| 代码 | 触发条件 |
|---|---|
| `INVALID_VISIT_TYPE` | `visit_type` 不在四种取值内 |
| `APPOINTMENT_IN_PAST` | 预约 `start` 早于 `as_of` |
| `ACCESS_NOT_CONFIRMED` | `site.access_confirmed` 为 `false` |
| `SCOPE_NOT_APPROVED` | `approval.scope_approved` 为 `false` |
| `SKILL_GAP` | 技术员技能清单已知且未覆盖全部 `required_skills` |
| `TECHNICIAN_UNAVAILABLE` | 可用时段已知但预约时窗不在任何一段内 |
| `PART_NOT_RESERVED:<part>` | 安装/保养的必需零件未 `reserved` |
| `TOOL_UNAVAILABLE:<tool>` | 必需工具 `available` 为 `false` |
| `DOCUMENT_UNAVAILABLE:<doc>` | 必需文档 `available` 为 `false` |
| `DOUBLE_BOOKED:<tech>` | 同一技术员两张工单预约时窗重叠 |
| `INVALID_WORK_ORDER_RECORD` | 工单记录不是对象 |

### 4.2 未知项（无阻塞时任一出现即为 `INSUFFICIENT_EVIDENCE`）

`UNKNOWN_APPOINTMENT`、`UNKNOWN_TIMEZONE`、`UNKNOWN_ACCESS`、`UNKNOWN_SCOPE_APPROVAL`、`SCOPE_TEXT_MISSING`、`UNKNOWN_SKILLS`、`UNKNOWN_AVAILABILITY`、`EQUIPMENT_NOT_RECORDED`、`UNKNOWN_EQUIPMENT_FACT`、`UNKNOWN_PART_STATE:<part>`、`UNKNOWN_TOOL_AVAILABILITY:<tool>`、`UNKNOWN_DOCUMENT_AVAILABILITY:<doc>`。

### 4.3 结论

1. 有阻塞项 → `BLOCKED`
2. 否则有未知项 → `INSUFFICIENT_EVIDENCE`
3. 否则 `visit_type == diagnosis` → `READY_FOR_DIAGNOSIS`；其它 → `READY_FOR_PLANNED_WORK`

状态优先级：`BLOCKED` > `INSUFFICIENT_EVIDENCE` > `READY_*`。

### 4.4 顶层结论

- 任一张工单 `BLOCKED` → `BLOCKED`
- 否则任一张 `INSUFFICIENT_EVIDENCE` → `GAPS_FOUND`
- 否则 → `READY`

另有 `REJECTED`（疑似凭据）与 `INPUT_INCOMPLETE`（缺 `as_of` 或工单）两个前置状态。

## 5. 时间与时区

- `as_of`、`appointment.start/end`、`available_windows[].start/end`、`due_at` 都必须是带偏移的 ISO 8601（`Z` 或 `±HH:MM`）。
- 不带偏移的本地时间（如 `2026-09-30 09:00`）视为格式无效：预约记未知，不猜测时区。
- 可用时段判断用绝对时刻比较，因此跨时区也正确。
- `timezone` 字段只作为展示与追问依据，不参与时刻换算。

## 6. 安全边界

1. **只读离线**：不联网、不写文件、不调用子进程、不访问调度/客服/采购系统。
2. **不派工**：不派工、不改期、不联系客户、不下单。
3. **不判定**：不判断技术安全、作业许可、资质或合规；全部进入 `human_confirm_items`。
4. **未知保持未知**：任何缺失字段都不默认为 0 或「已完成」。
5. **附件只留文件名**：`documents[].basename` 只接受单一文件名；含 `/`、`\`、`~`、盘符或链接的引用被拒绝，输出只保留安全化后的文件名（若可提取），**原引用不回显**。
6. **凭据拒绝**：字段名命中 `api_key` / `secret` / `password` / `token` 等，或字符串值形如 `sk-…`、`AKIA…`、`ghp_…`、私钥头、`xox…-…`、JWT，整体 `REJECTED` 且不回显。
7. **提示注入只标记**：动作词与目标词**同句共现**才记 `PROMPT_INJECTION`；进入 Markdown 时替换为固定占位「已隐藏疑似提示注入文本」，不执行、不回显。
8. **Markdown 转义**：所有自由文本先剥离控制字符、折叠空白，再对 Markdown 元字符加反斜杠。
9. **确定性**：工单按 `work_order_id`、待确认问题按生成顺序编号、状态汇总按 `WO_STATES` 固定顺序，同一输入逐字节一致。

## 7. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | 顶层结论 |
| `status_counts` | 四种工单结论的计数 |
| `work_orders[]` | 每张工单的完整判定（含 `blockers` / `unknowns` / `review_flags`） |
| `coverage_matrix[]` | 人员技能覆盖（`required` / `covered` / `missing` / `state`） |
| `parts_evidence[]` | 零件证据链（四列独立 + `blocks`） |
| `access_scope_approval_gaps[]` | 准入/范围/批准/预约/时区缺口 |
| `departure_checklist[]` | 出发前交接单（逐项状态） |
| `human_confirm_items[]` | 必须由人工确认的事项 |
| `double_bookings[]` | 重复派工 |
| `refused_refs[]` | 被拒绝的附件引用（只含路径与原因，不含原值） |
| `responsibility[]` | 责任人与截止（含截止状态） |
| `clarification_questions[]` | 待确认问题（`Q-01` 起编号） |
| `injection_flagged[]` | 提示注入命中路径 |
| `input_warnings[]` | 输入层提示 |
| `markdown_summary` | Markdown 派工准备板 |
| `disclaimer` | 免责声明 |

## 8. 不适用情况

- 需要真实技术安全判断、作业许可、资质核验或合规结论的场景。
- 需要自动派工、自动改期、自动联系客户或自动下单的场景。
- 需要读取地图、定位、库存写入或采购系统的场景。
- 工单事实完全缺失、只有一句「明天上门」的场景。
