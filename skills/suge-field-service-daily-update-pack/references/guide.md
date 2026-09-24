# 现场服务每日进度更新包 — 字段表与判定规则

本文件是本技能的唯一规则来源。`scripts/run.py` 完全按本文件实现；两者不一致时以本文件为准（并视为缺陷）。

## 1. 输入字段表

顶层：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `as_of` | 是 | 字符串 | ISO 8601 且**必须带时区偏移**。用于判断阻塞预计解除时间是否已过。 |
| `project` | 否 | 对象 | `project_id` / `name` / `timezone`（IANA 名）。 |
| `work_date` | 是 | 字符串 | `YYYY-MM-DD`，本次日报对应的**现场当地**日期。 |
| `plan` | 是 | 数组 | 当日计划任务，见下表。为空记 `INPUT_INCOMPLETE`。 |
| `progress` | 是 | 数组 | 当日完成记录，见下表。为空记 `INPUT_INCOMPLETE`。 |
| `photos` | 否 | 数组 | `filename` / `caption`。 |
| `blockers` | 否 | 数组 | `blocker_id` / `kind` / `description` / `owner` / `eta`。 |
| `customer_pending` | 否 | 数组 | `item_id` / `question` / `raised_at`。 |
| `next_day_plan` | 否 | 数组 | `task_id` / `title` / `owner`。 |

`plan[]` 条目：`task_id`、`title`、`owner`、`planned_hours`（数字或数字字符串）。

`progress[]` 条目：

| 字段 | 必填 | 说明 |
|---|---|---|
| `task_id` | 是 | 必须对应 `plan` 中的任务；找不到对应计划任务记为计划外记录。 |
| `state` | 是 | `done` / `partial` / `blocked` / `not_started`。越界记 `INVALID_STATE`。 |
| `note` | 否 | 自由文本，按不可信数据处理。 |
| `hours_spent` | 否 | 当日实际工时。 |
| `photo_refs` | 否 | 照片**文件名**数组。只能是裸文件名。 |
| `occurred_at` | 否 | 带偏移的 ISO 时间；与项目时区偏移不一致记 `RECORD_TZ_OFFSET_DIFFERS`。 |

`blockers[].kind`：`material` / `personnel` / `access` / `customer` / `other`。

## 2. 任务状态判定

对 `plan` 中每个任务，按下表确定状态（**以记录为准，不替你判断现场实际是否完成**）：

| 情况 | 状态 | 归入分栏 |
|---|---|---|
| 无对应进度记录 | `UNVERIFIABLE` | 无记录待确认 |
| `state = done` | `DONE` | 已完成（按记录） |
| `state = partial` | `PARTIAL` | 部分完成 |
| `state = blocked` | `BLOCKED` | 受阻 |
| `state = not_started` | `NOT_STARTED` | 未开始 |
| `state` 缺失或越界 | `UNVERIFIABLE` | 无记录待确认 |

- **`UNVERIFIABLE` 是刻意保留的状态**：计划里写了的活，当天没人记录，就不能写成「已完成」。它同时会生成一条需要现场负责人确认的问题。
- `progress[].task_id` 在 `plan` 中不存在 → 记入 `unplanned_records`（`PROGRESS_WITHOUT_PLAN_TASK`）。

## 3. 冲突与重复

- 同一 `task_id` 出现多条记录且**状态不一致** → `conflicts`，整体状态 `BLOCKED`。客户稿在状态未定时不能发出。
- 同一 `task_id` 出现多条记录但**状态一致** → `duplicates`，只保留首条参与状态判定，其余列出位置。

## 4. 工时偏差

对每个任务：`deviation_hours = hours_spent − planned_hours`（`Decimal`，2 位小数，消负零）。

- 判定：`ON_PLAN` / `OVER_PLAN` / `UNDER_PLAN`；任一侧缺失记 `PLAN_HOURS_UNKNOWN` 或 `RECORDED_HOURS_UNKNOWN`。
- 合计：`total_planned_hours`、`total_recorded_hours`、`deviation_hours`；任一侧完全没有数据时合计留 `null`，**不按 0 计算**。

## 5. 照片索引（**不读取图片内容**）

- `photo_refs` 与 `photos[].filename` 用**兼容归一化 + 大小写折叠**后的键比较（`NFKC` + `casefold`）。因此 `IMG_1.JPG` 与 `img_1.jpg` 视为同一张照片——在默认的 macOS / Windows 文件系统上它们确实会互相覆盖。展示时仍用原始写法。
- `photo_refs` 指向未声明的文件名 → `PHOTO_NOT_DECLARED`。
- 声明了但没有任何任务引用 → 列入 `photo_issues.unreferenced`。
- 缺少 `caption` → `CAPTION_MISSING`，列入 `photo_issues.caption_missing`。
- 多个声明归一化后同名 → `photo_issues.duplicate_filenames`；**所有**同名的声明都会标记为已被引用（不隐藏其中一条），重复本身单独报告。
- `photo_refs` 或 `filename` 含路径分隔符、`..`、URL scheme 或盘符 → `INVALID_PHOTO_REF` / `INVALID_PHOTO_NAME`，**该引用一律不解析、不打开**，整体状态 `BLOCKED`。
- 本工具**不读取图片内容**，无法验证照片是否真的拍到了所述内容；索引只是把文件名和说明摆在一起供人工核对。

## 6. 日期与时区

- `work_date` 与 `as_of` 在 `project.timezone` 下的**本地日期**不一致 → `WORK_DATE_TIMEZONE_MISMATCH`。跨时区团队最常见的就是「按北京时间写的日期，其实现场还在前一天」。
- `project.timezone` 无法解析 → `TIMEZONE_UNRESOLVED`，改用时间戳自带的偏移，并生成追问。
- `occurred_at` 的 UTC 偏移与项目当前偏移不同 → `RECORD_TZ_OFFSET_DIFFERS`（可能是跨时区记录，也可能是漏写偏移）。

## 7. 阻塞与材料缺口

- `blockers[].kind = material` → 同时进入 `material_gaps`。
- `eta` 早于 `as_of` → `ETA_PASSED`（`eta_state = PASSED`）；有 `eta` 为 `PENDING`；无 `eta` 为 `UNKNOWN`。
- `kind = personnel` → 参与「人员缺口是否影响明日计划」的追问。
- **追问与文案都必须跟随 `eta_state`**，不得对已填写的到货时间说「时间待确认」：
  - `PASSED`（原预计时间已过）→ 追问「原预计到货时间已过，请更新到货状态或给出新的预计时间」；
  - `UNKNOWN`（根本没填）→ 追问「没有填写预计到货时间，请补充」；
  - `PENDING`（还在有效期内）→ **不生成该追问**，除非另有独立的字段缺口。
  - 客户稿「材料进度」的兜底文案同理：`PASSED` 写「已超原预计，正在确认最新进度」，`PENDING` 直接写预计时间，只有 `UNKNOWN` 才写「待确认」；有 `description` 时一律以 `description` 为准。

## 8. 两份产物

- `internal_daily_report`（= `markdown_summary`）：内部日报，含全部细节、阻塞、记录问题、工时偏差与发送前检查表。
- `customer_progress_draft`：客户可读进度稿。**只描述记录到的事实**：
  - 已完成段落标题写作「当日已完成（按现场记录）」，不使用「已完工 / 已验收」；
  - `UNVERIFIABLE` 与 `NOT_STARTED` 合并在「尚未开始或需进一步确认」，绝不写进已完成；
  - 「需要你确认」**只列用户自己提交的** `customer_pending` 问题；由阻塞或无记录推导出的内部提示只留在内部日报。

## 9. 状态判定（优先级从高到低）

1. `REJECTED` — 输入疑似含凭据，拒绝处理且不回显。
2. `BLOCKED` — 存在状态冲突，或存在非法照片引用。
3. `INPUT_INCOMPLETE` — 缺 `as_of`、缺 `plan` 或 `progress`。
4. `GAPS_FOUND` — 存在无记录任务、计划外记录、记录级缺陷（`record_gaps`：时区偏移不一致、空记录、工时非法、`occurred_at` 非法、状态缺失或越界）、日期 / 时区告警、同名照片、缺说明照片或未被引用照片。
5. `READY` — 以上都不成立。

`record_gaps` 收录的记录级标记集合固定为：`RECORD_TZ_OFFSET_DIFFERS`、`RECORD_EMPTY`、`INVALID_HOURS_SPENT`、`NEGATIVE_HOURS_SPENT`、`INVALID_OCCURRED_AT`、`MISSING_STATE`、`INVALID_STATE`、`MISSING_TASK_ID`。

## 10. 安全处理

- **凭据**：字段名或值形如凭据 → 整体 `REJECTED`，只回路径与原因，**不回显原值**。
- **提示注入**：仅当**同一句**内同时出现动作词与目标词才命中；只标位置不执行，进入简报时替换为固定占位「已隐藏疑似提示注入文本」。
- **路径与 URL 拒绝**：照片引用一律按裸文件名处理；含 `/`、`\`、`..`、`scheme:` 的引用被拒绝且**不解析**。这既防目录穿越，也防「读取任意文件」。
- **控制字符**：进入任何输出前剥离（除换行、制表）。**不做 NFKC**，以免把中文全角标点改写成半角。
- **Markdown 结构**：所有自由文本转义 `` \ ` * _ { } [ ] ( ) # + - | < > ~ ! ``，无法伪造标题、列表、链接或表格。
- **只读**：只读用户指定的那一个 JSON，不打开链接、不访问路径、不读图片、不写文件、不执行输入中的命令。

## 11. 阅读顺序

1. 看 `status` 与 `section_counts`。
2. 看 `sections.unverifiable`：**这些不能写进客户稿**，先找现场负责人确认。
3. 看 `conflicts` 与 `unplanned_records`。
4. 看 `blockers` 与 `material_gaps`：材料缺口直接影响客户预期。
5. 看 `plan_deviation`：偏差大不等于出错，但要能解释。
6. 看 `photo_issues`：同名、缺说明、未引用三类。
7. 按 `human_send_checklist` 逐项打勾后再发客户稿。
8. 看 `clarification_questions`，按 `Q-01` 顺序去问。

## 12. 不适用情况

- 不能替代现场验收、安全交底或工程质检；不做质量、安全或进度结论。
- 不承诺工期，不判断责任，不计算报价。
- 不读取照片内容、不识别图片、不访问任何路径或 URL。
- 不发送任何消息（不发客户、不发班组），发送动作始终由人工执行。
