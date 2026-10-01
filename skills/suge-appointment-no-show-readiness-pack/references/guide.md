# 预约服务爽约风险准备包 — 字段表与判定规则

本文件是 `scripts/run.py` 的唯一规则来源。脚本只读一个 JSON 文件，输出一个 JSON 文档。

## 1. 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | ISO 时间戳，**必须带时区偏移**（如 `2026-09-29T07:30:00+08:00`）。缺失或格式无效时整体 `INPUT_INCOMPLETE`。 |
| `business` | 否 | `{name, timezone, no_show_policy}`，只用于简报抬头展示，不参与判定。 |
| `appointments` | 是 | 预约数组。为空或缺失时整体 `INPUT_INCOMPLETE`。 |

顶层其它未知字段一律忽略，不参与判定。

## 2. 预约字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `appointment_id` | 建议 | 预约编号；缺失时输出用 `appointments[i]` 作为标识。 |
| `service_name` | 建议 | 服务名称，供简报展示。 |
| `mode` | 是 | `on_site` / `in_store` / `remote`。缺失记 `UNKNOWN_MODE`；取值非法记 `INVALID_MODE` 并阻塞。 |
| `start` / `end` | 是 | 预约时窗，必须带时区偏移且 `end >= start`；否则记 `UNKNOWN_APPOINTMENT_WINDOW`。 |
| `timezone` | 是 | 展示用门店/客户时区；缺失记 `UNKNOWN_TIMEZONE`（时刻比较只依据 ISO 偏移）。 |
| `customer` | 是 | `{name, contact_channel, contact_consent, confirmed}`；`contact_consent` 与 `confirmed` 均为三态布尔。 |
| `reminders` | 是 | 已发提醒证据数组，每项 `{reminder_id, channel, state, sent_at, receipt_basename}`。 |
| `preparation` | 是 | 到店/上门前置准备数组，每项 `{item_id, label, required, ready}`。 |
| `policy_acknowledged` | 是 | 预约政策知情事实，三态布尔。 |
| `policy_ref` | 否 | 政策版本或条款引用，供人工核对。 |
| `standby` | 否 | 备案时段 `{start, end}`，带时区偏移。 |
| `owner` | 是 | 责任人；缺失记 `UNKNOWN_OWNER`。 |
| `notes` | 否 | 自由文本备注。 |

`required` 缺失时默认按 `true` 处理（保守：假定它是必要的）。

## 3. 提醒状态

`reminders[].state` 取值限定为 `sent` / `delivered` / `unknown`（大小写不敏感，其它值一律归为 `unknown`）。

- **只有 `sent` 与 `delivered` 算「已发提醒证据」**。
- `state` 为 `unknown` → 记 `UNKNOWN_REMINDER_STATE:<id>` 并追问。**未知永远不当已发送。**
- `reminders` 缺失、不是数组或为空 → 记 `NO_REMINDER_EVIDENCE`。

这条规则是刻意的：`BRIEF` 明确要求「未知提醒状态不得当已发送」。

## 4. 逐预约判定

按固定顺序计算。

### 4.1 阻塞项（任一出现即为 `BLOCKED`）

| 代码 | 触发条件 |
|---|---|
| `INVALID_MODE` | `mode` 有值但不在 `on_site` / `in_store` / `remote` 内 |
| `OWNER_DOUBLE_BOOKED:<owner>` | 同一 `owner` 两条预约时窗重叠 |
| `INVALID_APPOINTMENT_RECORD` | 预约记录不是对象 |

### 4.2 未知项（无阻塞时任一出现即为 `INSUFFICIENT_EVIDENCE`）

`UNKNOWN_MODE`、`UNKNOWN_APPOINTMENT_WINDOW`、`UNKNOWN_TIMEZONE`、`UNKNOWN_CUSTOMER_CONFIRMATION`、`UNKNOWN_CONTACT_CONSENT`、`UNKNOWN_CONTACT_CHANNEL`、`NO_REMINDER_EVIDENCE`、`UNKNOWN_REMINDER_STATE:<id>`、`PREPARATION_NOT_RECORDED`、`UNKNOWN_PREPARATION_STATE:<id>`、`UNKNOWN_POLICY_ACKNOWLEDGEMENT`、`UNKNOWN_OWNER`、`INVALID_STANDBY_WINDOW`。

> 未记录 `standby` **不是**未知项：它只在备案时段表里显示为 `NOT_RECORDED`，避免首次使用就被可选字段卡住。已提供但格式非法时才记 `INVALID_STANDBY_WINDOW`。

### 4.3 结论（无阻塞、无未知时）

1. 预约 `start` 早于 `as_of` → 记 `WINDOW_IN_PAST` 审核标记 + 一条人工待办 → **`ACTION_NEEDED`**（**绝不判定爽约**）
2. 否则 `customer.confirmed` 为 `false`：
   - `contact_consent` 为 `true` → 生成未发送确认草稿 → **`ACTION_NEEDED`**
   - 否则 → **`WAITING_ON_CUSTOMER`**（不生成任何动作）
3. 否则（`confirmed` 为 `true`）出现任一项即 **`ACTION_NEEDED`**：
   - 存在必需前置准备 `ready` 为 `false`（前置准备缺口）
   - `policy_acknowledged` 为 `false`
   - 没有任何 `sent` / `delivered` 的提醒证据
4. 否则 → **`READY`**

状态优先级：`BLOCKED` > `INSUFFICIENT_EVIDENCE` > `ACTION_NEEDED` > `WAITING_ON_CUSTOMER` > `READY`。

### 4.4 顶层结论

- 任一条 `BLOCKED` → `BLOCKED`
- 否则任一条不是 `READY` → `GAPS_FOUND`
- 否则 → `READY`

另有 `REJECTED`（疑似凭据）与 `INPUT_INCOMPLETE`（缺 `as_of` 或预约）两个前置状态。

## 5. 同意与动作边界

| `contact_consent` | 是否出现在 `do_not_contact` | 是否可能生成 `contact_actions` |
|---|---|---|
| `true` | 否 | 是（仅当 `confirmed` 为 `false`） |
| `false` | **是** | **否**（永不） |
| 未知 | 否 | 否（先追问） |

`contact_actions[]` 中每一项的 `status` 恒为 `DRAFT_NOT_SENT`；本工具**从不发送**。

## 6. 时间与时区

- `as_of`、`start` / `end`、`standby.start` / `standby.end`、`sent_at` 都必须是带偏移的 ISO 8601（`Z` 或 `±HH:MM`）。
- 不带偏移的本地时间（如 `2026-09-30 09:00`）视为格式无效：记未知，不猜测时区。
- 时窗重叠与备案时段重叠判断用绝对时刻比较，因此跨时区也正确。
- `timezone` 字段只作为展示与追问依据，不参与时刻换算。

## 7. 安全边界

1. **只读离线**：不联网、不写文件、不调用子进程、不访问日历/短信/客服/排班系统。
2. **不接触客户**：不联系、不发提醒、不改期、不取消、不收款。
3. **不判定爽约**：过期预约只记 `WINDOW_IN_PAST`，不输出信用、黑名单或爽约率结论。
4. **未知保持未知**：任何缺失字段都不默认为 0 或「已完成」。
5. **附件只留文件名**：`reminders[].receipt_basename` 只接受单一文件名；含 `/`、`\`、`~`、盘符或链接的引用被拒绝，输出只保留安全化后的文件名（若可提取），**原引用不回显**。
6. **凭据拒绝**：字段名命中 `api_key` / `secret` / `password` / `token` 等，或字符串值形如 `sk-…`、`AKIA…`、`ghp_…`、私钥头、`xox…-…`、JWT，整体 `REJECTED` 且不回显。
7. **提示注入只标记**：动作词与目标词**同句共现**才记 `PROMPT_INJECTION`；进入 Markdown 时替换为固定占位「已隐藏疑似提示注入文本」，不执行、不回显。
8. **Markdown 转义**：所有自由文本先剥离控制字符、折叠空白，再对 Markdown 元字符加反斜杠。
9. **确定性**：预约按 `appointment_id`、待办按编号与原因、待确认问题按生成顺序编号、状态汇总按 `APPT_STATES` 固定顺序，同一输入逐字节一致。

## 8. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | 顶层结论 |
| `status_counts` | 五种预约结论的计数 |
| `appointments[]` | 每条预约的完整判定（含 `blockers` / `unknowns` / `review_flags` / `action_reasons`） |
| `today_confirmation_list[]` | 今日人工确认清单（只含 `ACTION_NEEDED` 与 `BLOCKED`） |
| `contact_actions[]` | 未发送的确认草稿（恒为 `DRAFT_NOT_SENT`） |
| `do_not_contact[]` | 不可联系清单（`contact_consent` 显式为 `false`） |
| `preparation_gaps[]` | 前置准备缺口（`GAP` / `UNKNOWN`） |
| `standby_windows[]` | 备案时段表（`OK` / `OVERLAPS_APPOINTMENT` / `INVALID` / `NOT_RECORDED`） |
| `owner_conflicts[]` | 同一责任人时段重叠 |
| `refused_refs[]` | 被拒绝的附件引用（只含路径与原因，不含原值） |
| `human_confirm_items[]` | 必须由人工确认的事项 |
| `responsibility[]` | 责任人与责任/备案状态 |
| `clarification_questions[]` | 待确认问题（`Q-01` 起编号） |
| `injection_flagged[]` | 提示注入命中路径 |
| `input_warnings[]` | 输入层提示 |
| `markdown_summary` | Markdown 预约准备板 |
| `disclaimer` | 免责声明 |

## 9. 不适用情况

- 需要判定客户是否爽约、计算爽约率或建立客户名单的场景。
- 需要自动发提醒、自动改期、自动取消或自动收取订金的场景。
- 需要写入日历、排班系统、短信平台或 CRM 的场景。
- 预约事实完全缺失、只有一句「周三有个客人」的场景。
