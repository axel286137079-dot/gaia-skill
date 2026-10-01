# 小团队行政积压优先级准备包 — 字段表与判定规则

本文件是 `scripts/run.py` 的唯一规则来源。脚本只读一个 JSON 文件，输出一个 JSON 文档。

## 1. 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | ISO 时间戳，**必须带时区偏移**（如 `2026-09-29T07:30:00+08:00`）。缺失或格式无效时整体 `INPUT_INCOMPLETE`。 |
| `team` | 否 | `{name, timezone}`，只用于简报抬头展示，不参与判定。 |
| `items` | 是 | 事项数组。为空或缺失时整体 `INPUT_INCOMPLETE`。 |

顶层其它未知字段一律忽略，不参与判定。

## 2. 事项字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `item_id` | 建议 | 事项编号；缺失时输出用 `items[i]` 作为标识。 |
| `category` | 建议 | 类别，供简报展示与分组。 |
| `status` | 是 | `open` / `in_progress` / `waiting` / `done`（大小写不敏感）。`in_progress` 等同 `open`。其它值或缺失记 `UNKNOWN_ITEM_STATUS`。 |
| `due_at` | 是 | 截止时间，带时区偏移。缺失记 `NO_DUE_DATE`；格式无效记 `INVALID_DUE_AT`。 |
| `impacts` | 否 | `{customer, cash, operations}`，三项均为三态布尔。**只有显式 `true` 才会升级优先级。** |
| `owner` | 是 | 责任人；缺失记 `UNKNOWN_OWNER`。 |
| `depends_on` | 否 | 前置事项编号数组。 |
| `decision_needed` | 否 | 待决策内容的文本。 |
| `decision_owner` | 否 | `internal` / `external`。为 `external` 且存在 `decision_needed` 时记 `WAITING`。 |
| `effort_hours` | 否 | 预计工作量（小时）。**缺失保持未知，永不按 0 计算。** |
| `external_commitment` | 否 | `{made_to, due_at}`，对外承诺与其到期时间。 |
| `evidence_ref` | 否 | 证据引用，**只接受单一文件名**。 |
| `notes` | 否 | 自由文本备注。 |

## 3. 判定顺序（固定）

对每条事项自上而下取第一个命中的分支：

| # | 条件 | 结果 |
|---:|---|---|
| 1 | 记录不是对象 | `INSUFFICIENT_EVIDENCE`（`INVALID_ITEM_RECORD`） |
| 2 | `status` 缺失或非法 | `INSUFFICIENT_EVIDENCE`（`UNKNOWN_ITEM_STATUS`） |
| 3 | `status` 为 `done` | **`DONE`** |
| 4 | 该事项处于依赖环中 | `INSUFFICIENT_EVIDENCE`（`DEPENDENCY_CYCLE`） |
| 5 | `depends_on` 指向不存在的事项 | `INSUFFICIENT_EVIDENCE`（`DANGLING_DEPENDENCY:<ref>`） |
| 6 | 仍有前置事项不是 `done` | **`WAITING`**（附未完成前置与根阻塞） |
| 7 | 事项自身 `status` 为 `waiting` | **`WAITING`** |
| 8 | 有 `decision_needed` 且 `decision_owner` 为 `external` | **`WAITING`** |
| 9 | `due_at` 缺失或无效，或 `owner` 缺失 | `INSUFFICIENT_EVIDENCE`（`NO_DUE_DATE` / `INVALID_DUE_AT` / `UNKNOWN_OWNER`） |
| 10 | 见下方「升级到今天」 | **`DO_TODAY`** |
| 11 | 其余 | **`PLAN_THIS_WEEK`** |

**关键点：第 6 步只看前置事项自身的 `status` 是否为 `done`，不用前置事项的推导结果反推**，避免循环定义。

## 4. 升级到今天（`DO_TODAY`）的明示条件

任一成立即升级；**没有明示事实就不升级**：

1. `due_at` 早于 `as_of`（**已逾期**）；
2. `due_at` 与 `as_of` 的日期相同（**今日到期**）；
3. `due_at` 在 `as_of` 后 3 天内，且 `impacts.customer` 或 `impacts.cash` 显式为 `true`；
4. `external_commitment.due_at` 在 `as_of` 后 24 小时内。

否则进本周板（`PLAN_THIS_WEEK`）。

### 截止状态（`due_bucket`）

| 值 | 含义 |
|---|---|
| `OVERDUE` | 截止时间早于 `as_of` |
| `DUE_TODAY` | 截止日期与 `as_of` 同日 |
| `DUE_SOON` | 截止时间在 `as_of` 后 3 天内 |
| `SCHEDULED` | 更晚 |
| `UNKNOWN` | 截止时间缺失或无效 |

### 状态优先级

`DONE` 与 `WAITING` / `INSUFFICIENT_EVIDENCE` 由上面的固定顺序决定，不存在互相覆盖；`DO_TODAY` 与 `PLAN_THIS_WEEK` 只在第 10 步产生。

## 5. 顶层结论

- 存在任一 `WAITING` 或 `INSUFFICIENT_EVIDENCE` → `GAPS_FOUND`
- 否则 → `READY`

另有 `REJECTED`（疑似凭据）与 `INPUT_INCOMPLETE`（缺 `as_of` 或事项）两个前置状态。

## 6. 工作量口径

```
today_effort.known_total_hours  今日板中「已知」工作量之和（两位小数，字符串）
today_effort.unknown_items      今日板中未提供工作量的事项条数
today_effort.complete           是否全部已知（且至少一条非空）
```

`known_total_hours` 在一条已知都没有时为 `null`，**不是 0**。

## 7. 时间与时区

- `as_of`、`due_at`、`external_commitment.due_at` 都必须是带偏移的 ISO 8601（`Z` 或 `±HH:MM`）。
- 不带偏移的本地时间（如 `2026/10/05 18:00`）视为格式无效：记未知，不猜测时区。
- 逾期判断用绝对时刻比较，因此跨时区也正确；「今日到期」用解析后的日期比较。
- `team.timezone` 只作为展示依据，不参与时刻换算。

## 8. 安全边界

1. **只读离线**：不联网、不写文件、不调用子进程、不访问工单/邮件/财务/协作系统。
2. **不外发**：不发送、不建任务、不派活、不审批、不付款、不删除原始材料。
3. **不评价人**：不输出绩效、劳资或法律结论；相关决定进 `human_confirm_items`。
4. **未知保持未知**：任何缺失字段都不默认为 0 或「已完成」。
5. **附件只留文件名**：`evidence_ref` 只接受单一文件名；含 `/`、`\`、`~`、盘符或链接的引用被拒绝，输出只保留安全化后的文件名（若可提取），**原引用不回显**。
6. **凭据拒绝**：字段名命中 `api_key` / `secret` / `password` / `token` 等，或字符串值形如 `sk-…`、`AKIA…`、`ghp_…`、私钥头、`xox…-…`、JWT，整体 `REJECTED` 且不回显。
7. **提示注入只标记**：动作词与目标词**同句共现**才记 `PROMPT_INJECTION`；进入 Markdown 时替换为固定占位「已隐藏疑似提示注入文本」，不执行、不回显。
8. **Markdown 转义**：所有自由文本先剥离控制字符、折叠空白，再对 Markdown 元字符加反斜杠。
9. **确定性**：事项按 `item_id`、待办板按截止状态等级（`OVERDUE` → `DUE_TODAY` → `DUE_SOON` → `SCHEDULED` → `UNKNOWN`）与编号、待确认问题按生成顺序编号、状态汇总按 `ITEM_STATES` 固定顺序，同一输入逐字节一致。

## 9. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | 顶层结论 |
| `status_counts` | 五种事项结论的计数 |
| `items[]` | 每条事项的完整判定（含 `unknowns` / `review_flags` / `reasons` / `due_bucket` / `days_to_due`） |
| `today_board[]` | 今日交付板（按紧急度固定排序） |
| `week_board[]` | 本周承诺板（含 `days_to_due`） |
| `waiting_board[]` | 等待中事项及其原因 |
| `overdue_items[]` | 已逾期（不含 `DONE`） |
| `due_soon_items[]` | 即将到期（3 天内，不含 `DONE`） |
| `no_owner_items[]` | 无责任人清单（不含 `DONE`；不是对象的记录已由 `INVALID_ITEM_RECORD` 覆盖，不重复列出） |
| `decision_gaps[]` | 决策缺口（含决策方） |
| `blocking_chains[]` | 阻塞链：`item_id` / `unresolved` / `root_blockers` |
| `dangling_dependencies[]` | 悬空依赖 |
| `dependency_cycles[]` | 依赖成环（按环内编号排序） |
| `today_effort` | 今日工作量口径（见 §6） |
| `refused_refs[]` | 被拒绝的引用（只含路径与原因，不含原值） |
| `human_confirm_items[]` | 必须由人工确认的事项 |
| `responsibility[]` | 责任人与责任/截止状态 |
| `clarification_questions[]` | 待确认问题（`Q-01` 起编号） |
| `injection_flagged[]` | 提示注入命中路径 |
| `input_warnings[]` | 输入层提示 |
| `markdown_summary` | Markdown 行政积压准备单 |
| `disclaimer` | 免责声明 |

## 10. 不适用情况

- 需要评价员工绩效、处理劳动关系或给出法律结论的场景。
- 需要自动派活、自动发送、自动审批或自动付款的场景。
- 需要写入项目管理系统、工单系统或财务系统的场景。
- 事项事实完全缺失、只有一句「行政的活太多了」的场景。
