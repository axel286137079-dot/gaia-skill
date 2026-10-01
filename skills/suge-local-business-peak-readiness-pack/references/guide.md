# 本地商家客流高峰准备包 — 字段与判定规则

本文件是 `scripts/run.py` 的完整规则说明。脚本只读一个本地 JSON，向 stdout 输出一个 JSON；不联网、不写文件、**不登录 POS / 支付 / 排班 / 订货系统**，只做事实核对与固定规则推导。

## 1. 输入字段

### 1.1 顶层

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | 基准时间，**必须带时区偏移**。缺失或不可解析 → `INPUT_INCOMPLETE` |
| `business` | 建议 | `{ name, kind, timezone }`；`timezone` 为 IANA 名，无法解析 → `TIMEZONE_UNRESOLVED` |
| `peak` | 是 | `{ label, starts_at, ends_at }`，时间戳必须带时区偏移 |
| `inventory[]` | 建议 | 见 1.2 |
| `staffing` | 建议 | `{ staff[], shifts[], required_coverage[] }`，见 1.3 |
| `equipment[]` | 建议 | 见 1.4 |
| `payments[]` | 建议 | 见 1.5 |
| `signage[]` | 建议 | 见 1.6 |
| `flow` | 建议 | `{ entrance_plan, queue_plan, exit_plan }` |
| `contingency` | 建议 | `{ required_plans[], plans[] }`，见 1.7 |
| `tasks[]` | 建议 | 见 1.8 |

库存、班次、设备、任务**全部为空** → `INPUT_INCOMPLETE`。

### 1.2 `inventory[]`

| 字段 | 说明 |
|---|---|
| `item_id` | 品项编号。缺失 → `MISSING_ITEM_ID`，该记录判无效 |
| `name` / `unit` | 名称与单位（仅作展示） |
| `on_hand` | 现货数量。键缺失**或值为 `null`** → `INVENTORY_UNKNOWN`（计入 `inventory_unknown_count`，**不按 0**）；不可解析 → `INVALID_ON_HAND`（阻塞）；为负 → `NEGATIVE_ON_HAND`（阻塞） |
| `reorder_point` | 安全库存。给出但不可解析 → `INVALID_REORDER_POINT`（阻塞） |
| `supplier_lead_days` | 供货到货天数。给出但不可解析 → `INVALID_LEAD_DAYS`（阻塞） |

`state` 判定：`on_hand` 明确为 `0` → `OUT_OF_STOCK`（**阻塞**）；`on_hand ≤ reorder_point` → `BELOW_REORDER`，若 `supplier_lead_days > 距高峰天数` 则升级为 `LEAD_TIME_RISK`；`on_hand` 未知 → `UNKNOWN`；其余 `OK`。

### 1.3 `staffing`

`staff[]`：

| 字段 | 说明 |
|---|---|
| `staff_id` | 缺失 → `MISSING_STAFF_ID` |
| `role` | 岗位（与 `required_coverage[].role`、`shifts[].role` 精确匹配） |
| `available_from` / `available_to` | **员工自报可用时段**，必须带时区偏移并成对给出；缺失或不可解析 → `AVAILABILITY_UNKNOWN`；`available_to ≤ available_from` → `INVALID_AVAILABILITY_WINDOW` |
| `max_hours` | 自报当日最长工时；给出但不可解析或为负 → `INVALID_MAX_HOURS` |

`shifts[]`：

| 字段 | 说明 |
|---|---|
| `shift_id` | 班次编号 |
| `role` / `starts_at` / `ends_at` | 岗位与班次窗口；时间戳不可解析 → `INVALID_STARTS_AT` / `INVALID_ENDS_AT`（阻塞）；`ends_at ≤ starts_at` → `INVALID_SHIFT_WINDOW`（阻塞） |
| `assigned_staff_ids[]` | 指派员工。**空数组 → `UNASSIGNED_SHIFT`（阻塞）** |

`required_coverage[]`：`{ role, needed_count, starts_at, ends_at }`。统计**完全落在该窗口内**的班次指派人数，少于 `needed_count` → `COVERAGE_SHORTFALL`（阻塞）。

### 1.4 `equipment[]`

`kind` ∈ `pos` / `terminal` / `printer` / `network` / `fridge` / `display` / `kitchen` / `other`；`state` ∈ `ok` / `degraded` / `down` / `unknown`。

- `kind` 或 `state` 越界 / 缺失 → 该记录 `invalid` → `INVALID_EQUIPMENT_RECORD`（阻塞）。
- `state == unknown` → `EQUIPMENT_STATE_UNKNOWN`（缺口 + 提问）。
- **关键设备**（`pos` / `terminal` / `network`）`state ∈ {down, degraded}` 且 `spares_available` **不是** `true` → `SINGLE_POINT_OF_FAILURE`（阻塞）。
- 关键设备 `state == ok` 但 `spares_available` 不是 `true` → `SPARE_NOT_CONFIRMED`（缺口）。
- `spares_available` 键缺失 → `SPARES_UNKNOWN`（缺口）。

### 1.5 `payments[]`

`status` ∈ `active` / `issue` / `unknown`；越界 → `INVALID_PAYMENT_STATUS`（阻塞）。

- **完全没有收款方式** → `PAYMENT_METHOD_MISSING`（阻塞）。
- `status ∈ {issue, unknown}` 且 `fallback` 为空 → `PAYMENT_FALLBACK_MISSING`（阻塞）。
- `fallback` 缺失但 `status == active` → `FALLBACK_NOT_STATED`（缺口）。

### 1.6 `signage[]`

`{ sign_id, purpose, placement, text_ready }`。`text_ready` 键缺失**或为 `null`** → `SIGNAGE_TEXT_UNKNOWN`（缺口）；`false` → `SIGNAGE_NOT_READY`（缺口）；`purpose` 缺失 → `MISSING_SIGN_PURPOSE`（缺口）。

### 1.7 `contingency`

`required_plans[]` 为风险编号列表（或含 `risk_id` 的对象）；`plans[]` 为 `{ risk_id, plan, owner }`。`required_plans` 中没有任何 `plans[].risk_id` 匹配 → `contingency_gaps`（缺口）。`plans[]` 缺 `owner` → `OWNER_MISSING`（缺口）。

### 1.8 `tasks[]`

| 字段 | 说明 |
|---|---|
| `task_id` / `title` | 缺失 → 对应 `MISSING_*`；`task_id` 或 `title` 缺失 → 该记录 `invalid` |
| `phase` | `d_minus_7` / `d_minus_1` / `opening`。缺失或越界 → 该记录 `invalid`（阻塞） |
| `status` | `open` / `done` / `unknown`。缺失或越界 → 该记录 `invalid`（阻塞） |
| `owner` | 缺失 → `OWNER_MISSING`（缺口） |
| `due_at` | 不可解析 → `INVALID_DUE_AT`（缺口） |
| `evidence_refs[]` | 证据引用（仅记录，不打开） |

`due_at < as_of` 且 `status != done` → 计入 `overdue_tasks`。

## 2. 输出与阅读顺序

`status`、`business`、`peak`、`peak_window`、`readiness_by_area[]`、`area_matrix{}`、`blockers[]`、`single_points_of_failure[]`、`inventory[]`、`inventory_unknown_count`、`zero_stock_items[]`、`below_reorder_items[]`、`lead_time_risks[]`、`staffing{}`、`equipment[]`、`payments[]`、`signage[]`、`flow{}`、`contingency{}`、`contingency_gaps[]`、`responsibility_by_window[]`、`uncovered_windows[]`、`action_plan{}`、`overdue_tasks[]`、`human_confirmation_required[]`、`clarification_questions[]`、`injection_flagged[]`、`input_warnings[]`、`markdown_summary`、`disclaimer`。

阅读顺序：`status` → `blockers` → `readiness_by_area` → `single_points_of_failure` → `zero_stock_items` / `lead_time_risks` → `responsibility_by_window` / `uncovered_windows` → `action_plan` → `human_confirmation_required` → `clarification_questions`。

`peak_window.days_to_peak` 为 `as_of` 到 `peak.starts_at` 的天数（Decimal，2 位小数）；为负说明高峰已开始或已过，`peak_in_progress_or_past = true`。

## 3. 整体状态

按顺序判定，先命中先生效：

| 状态 | 触发条件 |
|---|---|
| `REJECTED` | 输入出现疑似凭据，**拒绝且不回显** |
| `BLOCKED` | 存在任一 `blockers[]` 条目（高峰窗口无效、现货为 0、库存数值非法、班次时间无效、排班超出员工可用时段或指派未登记员工、工时超自报上限、岗位人数不足、班次无人、设备记录非法、关键设备无备用、收款方式缺失或异常无备用、任务阶段或状态非法） |
| `INPUT_INCOMPLETE` | `as_of` 缺失、`peak.starts_at` 缺失/不可解析，或库存/班次/设备/任务全部为空 |
| `GAPS_FOUND` | 任一分区状态不是 `OK`，或存在逾期任务 |
| `READY` | 以上都没有 |

分区状态：`BLOCKED`（该分区有阻塞项）> `GAPS`（该分区有待补项）> `OK`。

- `库存`分区的 `GAPS` 覆盖：存在未盘点品项、低于安全库存品项，或到货周期不足品项。
- `人员`分区的 `GAPS` 覆盖：员工可用时段未知、岗位人数不足、班次无人指派、以及**相邻班次之间存在无人值守空档**（`uncovered_windows`）。空档本身不阻塞，但必须有人确认是否接受。

## 4. 行动清单的推导规则

`action_plan` 分 `d_minus_7` / `d_minus_1` / `opening` 三档，条目由**固定规则**生成（不是自由发挥）：

| 触发条件 | 阶段 | 动作 |
|---|---|---|
| 有品项现货为 0 | D-1 | 确认补货到店 |
| 低于安全库存或到货周期不足 | D-7 | 下单或改用替代品 |
| 有未盘点品项 | D-7 | 完成实盘登记（未盘点按未知处理） |
| 班次之间有空档 | D-1 | 补人 |
| 岗位人数不足 | D-1 | 补齐或确认缩减服务范围 |
| 排班与员工可用时段冲突 | D-1 | 由门店负责人裁决 |
| 关键设备无备用 | D-1 | 确认备用方案现场可用 |
| 设备状态未知 | D-1 | 现场复测确认 |
| 收款方式异常或缺备用 | D-1 | 备用收款方式现场试刷一次 |
| 标识未就绪或文案未知 | D-1 | 文案定稿并打印到店 |
| 缺少应急预案 | D-7 | 补齐预案 |
| 动线方案不完整 | D-7 | 画出布置并张贴 |
| 未完成的 `tasks[]` | `tasks[].phase` | 原样进入对应档 |
| 恒定 | 开门前 | 逐项核对作战单并写明当日负责人 |

## 5. 安全边界

- **不预测、不承诺**：不预测销量、客流、收入或利润；不保证促销效果。
- **合规只列人工确认**：支付费率与限额、食品安全、劳动法规、许可报备只出现在 `human_confirmation_required[]`，**不给结论**。
- **未知不按 0**：未盘点的现货保持未知并计入 `inventory_unknown_count`；只有用户**明确写 0** 才判 `OUT_OF_STOCK`。
- **不超可用时段排班**：班次必须完全落在员工自报的 `available_from`–`available_to` 内，否则 `STAFF_UNAVAILABLE` 且整体阻塞；工具**不会**替你改动或生成排班。
- **缺时区保持未知**：`as_of`、`peak`、班次与员工可用时段一律要求显式时区偏移；门店 `timezone` 无法解析时记 `TIMEZONE_UNRESOLVED` 并追问，不用本机时区冒充。
- **凭据门禁**：字段名命中 `password` / `token` / `api_key` 等，或字符串值具备真实凭据形态 → 直接 `REJECTED`，不输出命中内容。
- **提示注入**：只标记、不执行；要求「动作词 + 目标词」同句共现，命中记入 `injection_flagged[]` 并在 Markdown 中替换为固定占位「已隐藏疑似提示注入文本」。
- **Markdown 转义**：所有自由文本先去控制字符、折叠空白，再转义 Markdown 元字符，无法伪造标题、列表、链接或表格。
- **不改系统**：不改 POS、不排班、不下单、不登记工单、不发消息。

## 6. 诊断表

| 条目 | 含义 | 用户该做什么 |
|---|---|---|
| `blockers[].flag = ZERO_STOCK` | 用户明确填了 0 | 补货或确认停售 |
| `INVENTORY_UNKNOWN` | 未盘点 | 补录数量；不要填 0 |
| `BELOW_REORDER` / `LEAD_TIME_RISK` | 低于安全库存 / 到货赶不上 | 下单或找替代品 |
| `INVALID_ON_HAND` / `INVALID_REORDER_POINT` / `INVALID_LEAD_DAYS` | 数字不可解析 | 修正数值 |
| `UNASSIGNED_SHIFT` | 班次没人 | 指派员工 |
| `STAFF_UNAVAILABLE` | 排班超出员工自报可用时段，或指派了未登记员工 | 改时段或换人 |
| `AVAILABILITY_UNKNOWN` | 员工可用时段缺失 | 补齐后才能确认排班 |
| `HOURS_EXCEEDED` | 超出员工自报最长工时 | 减班或换人 |
| `COVERAGE_SHORTFALL` | 岗位人数不足 | 补人或缩减服务 |
| `SINGLE_POINT_OF_FAILURE` | 关键设备异常且无备用 | 确认备用方案 |
| `SPARE_NOT_CONFIRMED` / `SPARES_UNKNOWN` | 备用未确认 / 未说明 | 确认备用设备 |
| `PAYMENT_FALLBACK_MISSING` | 收款异常且无备用 | 定备用收款与人工兜底 |
| `INVALID_SHIFT_WINDOW` / `INVALID_PEAK_WINDOW` | 时间窗非法 | 修正时间戳（跨夜请写两个完整时间） |
| `INVALID_TASK_RECORD` | 任务阶段或状态非法 | 改为 `d_minus_7`/`d_minus_1`/`opening` 与 `open`/`done`/`unknown` |
