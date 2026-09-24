# 门店交接班准备包 — 字段表与判定规则

本文件是本技能的唯一规则来源。`scripts/run.py` 完全按本文件实现；两者不一致时以本文件为准（并视为缺陷）。

## 1. 输入字段表

顶层：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `as_of` | 是 | 字符串 | ISO 8601 且**必须带时区偏移**，如 `2026-09-25T07:40:00+08:00`。用于判断事项是否逾期。缺省或格式不合法记 `AS_OF_MISSING_OR_INVALID`。 |
| `store` | 否 | 对象 | `store_id` / `name` / `timezone`。`timezone` 为 IANA 名（如 `Asia/Shanghai`）；无法解析记 `TIMEZONE_UNRESOLVED`，改用时间戳自带的偏移。 |
| `shift` | 否 | 对象 | `shift_id` / `starts_at` / `ends_at`，两者都必须带时区偏移。 |
| `equipment` | 否 | 数组 | `asset_id` / `name` / `state`（`down` / `degraded` / `ok` / `unknown`）/ `since`。`state` 越界记 `INVALID_EQUIPMENT_STATE`（进入 `input_warnings`、`equipment[].flags`，并生成 `EQUIPMENT_STATE` 待确认问题）；缺 `asset_id` 记 `MISSING_ASSET_ID`。 |
| `items` | 是 | 数组 | 事项记录，见下表。为空或缺失记 `INPUT_INCOMPLETE`。 |

`items[]` 条目：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `item_id` | 建议 | 字符串 | 事项编号。缺失记 `MISSING_ITEM_ID`，该条判为无效记录。 |
| `summary` | 是 | 字符串 | 事项摘要。缺失记 `MISSING_SUMMARY`，该条判为无效记录。 |
| `category` | 是 | 枚举 | `cash` / `inventory` / `equipment` / `customer` / `other`。越界记 `INVALID_CATEGORY`。 |
| `status` | 是 | 枚举 | `open` / `in_progress` / `done` / `unknown`。越界记 `INVALID_STATUS`。 |
| `owner` | 建议 | 字符串 | 责任人代号（只用脱敏工号，不要写姓名、电话）。缺失记 `OWNER_MISSING`。 |
| `due_at` | 建议 | 字符串 | 带偏移的 ISO 时间；无偏移或不可解析记 `INVALID_DUE_AT`。 |
| `asset_id` | 否 | 字符串 | 关联设备，用于判断是否处于停机状态。 |
| `evidence_refs` | 否 | 字符串数组 | 证据引用编号（票据号、工单号、照片编号）。 |
| `amount` | 否 | 对象 | `value` 与 `currency`。**键不存在**表示该事项不涉及金额；键存在但 `value` 为 `null` 才记 `AMOUNT_UNKNOWN`。 |
| `quantity` | 否 | 对象 | `value` 与 `unit`。规则同上：键不存在 ≠ 数量未知。 |
| `notes` | 否 | 字符串 | 自由文本。按不可信数据处理。 |

## 2. 班次窗口

- `ends_at <= starts_at`（按绝对时刻比较）→ `INVALID_SHIFT_WINDOW`，整体状态 `BLOCKED`。
- `duration_hours = (ends_at − starts_at) / 3600`，保留 2 位小数。
- **跨午夜**：把两个时刻换算到 `store.timezone` 后比较**本地日历日期**，结束日期晚于开始日期即为跨午夜。时区无法解析时退化为「偏移相同且结束的本地日期更晚」。
- 跨午夜班次不会造成 `INVALID_SHIFT_WINDOW`——`22:00 → 次日 06:00` 是合法班次。

## 3. 分板路由（按顺序，首个命中生效）

| 顺序 | 条件 | 归入 |
|---:|---|---|
| 1 | 记录无效（缺编号/摘要，或类别、状态越界） | `confirm_with_owner` |
| 2 | 同一 `item_id` 出现互相矛盾的状态 | `confirm_with_owner` |
| 3 | `status == done` | `record_only` |
| 4 | 已逾期（`due_at < as_of` 且状态不是 `done`） | `immediate` |
| 5 | `category == equipment` 且关联设备 `state == down` | `immediate` |
| 6 | `category == cash` | `immediate` |
| 7 | `status == unknown`，或责任人缺失 | `confirm_with_owner` |
| 8 | 其余 | `next_shift` |

**为什么现金一律进「立即处理」**：现金与备用金差异必须在交班当面无双方确认，跨班追认几乎无法定责。工具只把事项推到最前，**不判定谁的责任、也不算工资**。

## 4. 重复与冲突

- 同一 `item_id` 出现多次，**状态全部相同** → 记 `duplicate_item_ids`，只有**首次出现**进入分板，其余列出位置供合并。
- 同一 `item_id` 出现多次，**状态不一致** → 记 `conflicts`，该事项带 `CONFLICTING_STATUS` 归入「待负责人确认」，整体状态升为 `BLOCKED`。**工具不替你挑一个状态**。

## 5. 金额、数量与币种

- 金额用 `Decimal` 计算，输出保留 2 位小数；`-0.00` 一律消成 `0.00`。
- 金额允许为负（退款、冲销场景），不做绝对值化。
- `value` 非数字记 `INVALID_AMOUNT`；为负记 `NEGATIVE_AMOUNT`（仅提示，不阻断）。
- 币种缺失记 `AMOUNT_CURRENCY_UNKNOWN`，该笔**不进入任何合计**。
- `totals_by_currency` **只按币种分组**，没有也不会有跨币种总额。
- 空值不做 0 处理：`unknown_amount_count` / `unknown_quantity_count` 单独计数。
- 输出里的 `items[].amount` / `items[].quantity` 保留记录**已经说明过**的币种 / 单位，只把数值置空：
  - `{"value": null, "currency": "CNY"}` — 说了币种但没填金额；不是这笔金额为 0。
  - `{"value": null, "unit": "箱"}` — 说了单位但没填数量。
  - 键不存在（该事项根本不涉及金额）时为 `null`，与「未知」区分开。
  - 已填数值一律按 `Decimal` 输出为字符串（如 `"0.00"`、`"120.50"`）。
- `equipment` 原样回显每条设备记录（`asset_id` / `name` / `state` / `since` / `flags`，按输入顺序），`equipment_downtime` 只列 `state == down` 的设备。

## 6. 状态判定（优先级从高到低）

1. `REJECTED` — 输入疑似含凭据（密钥 / 令牌 / 密码）。**拒绝处理且不回显**。
2. `BLOCKED` — 班次窗口非法，或存在状态冲突，或存在无效事项记录。
3. `INPUT_INCOMPLETE` — 缺 `as_of`，或 `items` 缺失 / 为空。
4. `GAPS_FOUND` — 缺失必填 / 建议字段、金额或数量未知、时区无法解析。
5. `READY` — 以上都不成立。

`GAPS_FOUND` 不等于「有问题不能用」：它表示交接板可用，但有几处需要人工补齐。

## 7. 安全处理

- **凭据**：字段名命中 `api_key` / `secret` / `password` / `token` / `credential` 等，或字符串值形如 `sk-…`、`AKIA…`、`ghp_…`、`eyJ…`（JWT）、PEM 私钥头 → 整体 `REJECTED`，只回 `路径 + 原因`，**不回显原值**。
- **提示注入**：仅当**同一句**内同时出现动作词（忽略 / 无视 / 覆盖 / 改写 / 删除 / 执行 / 绕过 / ignore / override …）与目标词（指令 / 规则 / 提示 / 系统 / 要求 / 约束 / instruction / rule / prompt …）才判定命中。命中只在 `injection_flagged` 标位置，**不执行**；该值进入 Markdown 时替换为固定占位「已隐藏疑似提示注入文本」。
  - 因此「请忽略小额尾差，财务已核销。」这类普通备注不会被误标。
  - 命中记录同时在 `markdown_summary` 的「备注」列显示为占位文本，原文不回显、也不作为任何判定依据。
- **控制字符**：输入中的所有 C0/C1 控制字符（除换行、制表）在进入任何输出前剥离；`\u0007` 之类的字符不会出现在 JSON 或 Markdown 中。
- **Markdown 结构**：所有自由文本转义 `\ \` * _ { } [ ] ( ) # + - | < > ~ !`，无法伪造标题、列表、链接或表格。
- **只读**：只读用户指定的那一个 JSON，不打开链接、不访问路径、不写文件、不执行输入中的任何命令。

## 8. 阅读顺序

1. 看 `status`。
2. 看 `board.immediate`：这三五件事是交班前必须落地的。
3. 看 `conflicts` 与 `duplicate_item_ids`：先合并，再谈跟进。
4. 看 `items[].review_flags`：处理 `OWNER_MISSING`、`AMOUNT_UNKNOWN`、`INVALID_DUE_AT`。
5. 看 `equipment` 与 `equipment_downtime`：状态越界的设备记录先修正，否则无法判断是否算停机。
6. 看 `totals_by_currency`：**分币种**核对，不要把不同币种相加。
7. 看 `clarification_questions`：按 `Q-01` 顺序原样去问。
8. 打印 `opening_checklist` 与 `markdown_summary` 作为交接单。`markdown_summary` 的「交接板」表把 `summary` 放在「事项」列、`notes` 放在「备注」列，两者互不覆盖。

## 9. 不适用情况

- 不能替代 POS、排班或库存系统；不读取也不写入任何系统。
- 不做责任判定、不做工资或绩效核算、不做现金责任归因。
- 不向外发消息（不给员工、不给顾客）。
- 不识别照片内容；`evidence_refs` 只是编号。
- 输出不是合规结论；门店制度与票据才是最终依据。
