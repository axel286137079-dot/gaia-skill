# 应收款跟进准备包 · 字段表与判定规则

本文件是 `suge-invoice-collection-followup-pack` 的字段参考。所有规则都是固定的、可复现的：
同一份输入必然得到逐字节相同的输出。**没有任何字段有"默认值"**——未提供就是未知。

---

## 1. 输入字段表

### 1.1 顶层

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `as_of` | 是 | string | 基准时间，**必须带时区偏移**（`2026-09-18T10:00:00+08:00`，`Z` 也可）。所有天数与状态以它为基准；缺少时区直接报错，不做本地时区猜测。 |
| `currency_default` | 否 | string | 仅当某张发票没写 `currency` 时套用，并标记 `CURRENCY_FROM_DEFAULT`。没有它又没写币种 → 该发票 `INVALID` + `CURRENCY_MISSING`。 |
| `company` | 否 | object | `company_ref` / `display_name`，原样回显（两者都做提示注入检测）。 |
| `invoices` | 是 | array | 至少一条。见 1.2。 |
| `communications` | 否 | array | 历史沟通记录。见 1.3。 |
| `followup_stages` | 否 | array | 用户提供的跟进阶段。见 1.4。**未提供时不会臆造任何阶段日期。** |

### 1.2 `invoices[]`

| 字段 | 必填 | 规则 |
|---|---|---|
| `invoice_id` | 是 | 发票号，缺失或空白直接报错。重复时**只有后出现的那条**记 `DUPLICATE_INVOICE_ID` 并判 `INVALID`。 |
| `customer_ref` | 否 | 客户编号，仅回显。 |
| `currency` | 否 | 币种代码，**原样比较大小写**。缺失见 `currency_default`。 |
| `issued_on` | 否 | 开票日 `YYYY-MM-DD`；不可解析记 `INVALID_ISSUED_ON`（硬错误）。 |
| `due_on` | 否 | 到期日 `YYYY-MM-DD`；不可解析记 `INVALID_DUE_ON`（硬错误）；缺失记 `DUE_DATE_MISSING`（缺口，状态 `DATE_UNKNOWN`）。 |
| `net_amount` | 是 | 发票净额，必须是 > 0 的数值。`0`、负数、字符串、布尔都记 `INVALID_AMOUNT` 并判 `INVALID`（`net_amount` 与 `outstanding` 为 `null`）。 |
| `contact_ref` | 否 | 联系人引用，仅回显与做缺口判定。 |
| `terms` | 否 | `payment_term_days` / `grace_days`。**只用于判断"是否提供"**，本技能不用它推算任何日期；缺失记 `CONTRACT_TERMS_MISSING`。 |
| `payments[]` | 否 | 见 1.2.1。 |
| `credit_notes[]` | 否 | 见 1.2.2。 |
| `disputed` | 否 | 布尔。`true` → 状态 `DISPUTED`；非布尔且非 `null` 记 `DISPUTED_FLAG_INVALID`。 |
| `promised_payment_on` | 否 | 客户承诺付款日 `YYYY-MM-DD`；不可解析记 `INVALID_PROMISE_DATE`；早于基准日记 `PROMISE_IN_PAST`。 |
| `notes` | 否 | 只用于提示注入检测，不进入简报。 |

#### 1.2.1 `payments[]`

| 字段 | 规则 |
|---|---|
| `payment_id` | 同张发票内重复时，**后出现的那条**记 `DUPLICATE_PAYMENT_ID` 且不计入已收。 |
| `paid_on` | `YYYY-MM-DD`；缺失或不可解析记 `INVALID_PAYMENT_DATE`，该条不计入。 |
| `amount` | 必须 > 0；否则记 `INVALID_PAYMENT_AMOUNT`，该条不计入。 |
| `currency` | 与发票币种不同 → `CROSS_CURRENCY_PAYMENT`，**整条排除**（不折算）。 |
| `status` | 只认 `CONFIRMED` / `PENDING` / `FAILED`（大写）。`CONFIRMED` 计入已收；`PENDING` 只单列（`pending_paid`）并触发缺口；`FAILED` 忽略；其它值记 `PAYMENT_STATUS_UNKNOWN` 且不计入。 |

#### 1.2.2 `credit_notes[]`

| 字段 | 规则 |
|---|---|
| `credit_id` | 重复时后出现的那条记 `DUPLICATE_CREDIT_ID` 且不计入。 |
| `issued_on` | `YYYY-MM-DD`；不可解析记 `INVALID_CREDIT_DATE`，该条不计入。 |
| `amount` | 必须 > 0；否则记 `INVALID_CREDIT_AMOUNT`，该条不计入。 |

### 1.3 `communications[]`

| 字段 | 说明 |
|---|---|
| `comm_id` | 记录编号，也是提示注入的引用名（自身也按不可信文本检测；命中时 `reference` 用固定占位）。 |
| `invoice_id` | 引用不存在的发票时记入 `unknown_communication_refs`（不报错，只单列）。 |
| `sent_on` / `channel` | 仅回显。 |
| `direction` | `INBOUND` 用于判断承诺付款是否有书面确认。 |
| `summary` | 自由文本，只用于提示注入检测与回显。 |

### 1.4 `followup_stages[]`

| 字段 | 说明 |
|---|---|
| `stage_id` | 必填。 |
| `name` | 仅回显（会做注入检测）。 |
| `min_days_overdue` | 必填整数，阶段起始逾期天数。 |
| `max_days_overdue` | 整数或 `null`（表示无上限）。 |

阶段按 `min_days_overdue` 升序使用。

---

## 2. 固定判定规则

### 2.1 金额（全部 `Decimal`，保留 2 位，四舍五入）

```
未收余额 = net_amount − 已确认(CONFIRMED)付款 − 有效贷项通知
```

- `PENDING` / `FAILED` / 重复 / 非法 / 跨币种的付款计入**排除项**，不参与相减。
- 未收余额为负 → 状态 `OVERPAID` 并保留负值 + 标记 `NEGATIVE_OUTSTANDING`（**不静默归零**）。
- 合计只按币种分组：`totals_by_currency[币种] = {invoice_count, outstanding, overdue_outstanding}`，**不存在跨币种总额键**。

### 2.2 单张发票状态（按顺序取第一个命中）

| 顺序 | 状态 | 条件 |
|---:|---|---|
| 1 | `INVALID` | 命中 `DUPLICATE_INVOICE_ID` / `INVALID_AMOUNT` / `CURRENCY_MISSING` / `INVALID_DUE_ON` / `INVALID_ISSUED_ON` |
| 2 | `DISPUTED` | `disputed = true` |
| 3 | `OVERPAID` | 未收余额 < 0 |
| 4 | `PAID` | 未收余额 = 0 |
| 5 | `PROMISED` | 承诺付款日 ≥ 基准日 |
| 6 | `DATE_UNKNOWN` | 没有 `due_on` |
| 7 | `OVERDUE` | `due_on` < 基准日 |
| 8 | `DUE_SOON` | 距到期 ≤ 7 天（含当天） |
| 9 | `CURRENT` | 其余 |

天数：`days_overdue = max(0, 基准日 − due_on)`；`days_until_due = due_on − 基准日`；**`due_on` 缺失时两者都是 `null`**，不是 0。
`overdue_outstanding` 只累计状态为 `OVERDUE` 的发票。

### 2.3 整体状态（第一个命中）

`INVALID`（没有可用发票）→ `DISPUTE_HOLD` → `OVERPAID_REVIEW` → `OVERDUE_ACTION` → `PROMISE_TRACKING` → `DATE_UNKNOWN` → `DUE_SOON` → `CURRENT`

### 2.4 优先级与队列

| 优先级 | 状态 |
|---|---|
| `P1` | `DISPUTED`、`OVERDUE` |
| `P2` | `PROMISED`、`DATE_UNKNOWN` |
| `P3` | `DUE_SOON`、`OVERPAID` |
| `P4` | `PAID`、`CURRENT`、`INVALID` |

`followup_queue` 只含 `P1`～`P3`，排序键固定为：状态序（争议 1 → 逾期 2 → 承诺 3 → 到期未知 4 → 即将到期 5 → 超额 6）→ 逾期天数降序 → 未收余额降序 → 发票号升序。

### 2.5 下一次人工动作

| 状态 | `next_action` | `next_action_on` |
|---|---|---|
| `OVERDUE`（有阶段） | `SEND_STAGE_DRAFT` | 到期日 + 下一阶段 `min_days_overdue`；没有下一阶段则 `null` |
| `OVERDUE`（无阶段） | `REVIEW_OVERDUE_MANUALLY` | `null` |
| `DISPUTED` | `AWAIT_DISPUTE_RESOLUTION` | `null` |
| `PROMISED` | `VERIFY_PROMISED_PAYMENT` | 承诺付款日 |
| `DATE_UNKNOWN` | `CONFIRM_DUE_DATE` | `null` |
| `DUE_SOON` | `SEND_PRE_DUE_REMINDER` | 到期日 |
| `OVERPAID` | `REVIEW_OVERPAYMENT` | `null` |
| 其它 | `NONE` | `null` |

### 2.6 证据缺口代码

`CONTRACT_TERMS_MISSING`、`DUE_DATE_MISSING`、`ISSUED_DATE_MISSING`、`CONTACT_MISSING`、`COMMUNICATION_LOG_MISSING`、`PENDING_PAYMENT_UNVERIFIED`、`FOLLOWUP_STAGES_MISSING`、`DISPUTE_OWNER_UNKNOWN`、`PROMISE_UNCONFIRMED`。

- `CONTACT_MISSING` 与 `COMMUNICATION_LOG_MISSING` **只对需要动作的状态**（`OVERDUE`/`DUE_SOON`/`DISPUTED`/`PROMISED`/`DATE_UNKNOWN`）判定；已结清或未到期的发票不因缺沟通记录而报缺口。
- `PROMISE_UNCONFIRMED` 只在有生效承诺且没有任何 `INBOUND` 沟通时出现。
- 缺口按 `(invoice_id, code)` 升序输出，同代码按字典序。

### 2.7 待澄清问题（固定主题顺序）

`DUE_DATE` → `CONTRACT_TERMS` → `CONTACT` → `DISPUTE_OWNER` → `FOLLOWUP_STAGES` → `PAYMENT_STATUS` → `PROMISE_CONFIRMATION` → `COMMUNICATION_LOG` → `RECORD_INVALID`

只输出真正有缺口的主题，编号从 `Q-01` 连续；同一个主题跨多张发票时合并为一条，`invoice_ids` 升序。

### 2.8 草稿（只准备、不发送）

- 只为 `OVERDUE` / `DUE_SOON` / `PROMISED` / `DATE_UNKNOWN` 生成，顺序与 `followup_queue` 一致。
- 每条固定 `draft_status = DRAFT_HUMAN_CONFIRM`、`send_allowed = false`。
- `placeholders` 列出缺失字段（如 `客户联系人`、`到期日`），正文**不含**滞纳金、利息、违约金、律师函、法律后果等措辞。
- **争议中的发票不生成任何草稿**。

---

## 3. 不可信文本的统一处理（转义 + 注入定位）

输入里的所有自由文本都视为不可信。

**（1）统一转义。** 进入 `markdown_summary` 的每个值都先经过同一个转义函数：控制字符（含换行、制表符）压成空格、连续空白折叠并去首尾；反斜杠、竖线、反引号、方括号、圆括号、井号、感叹号、尖括号前统一加反斜杠。因此**任何输入值都无法新建标题、列表、链接/图片或表格列**；所有表格每行的竖线数恒定。

**（2）逐字段注入检测（覆盖动作 + 危险目标，低误报）。** 参与检测的位置（每条命中都记录为可复现的精确位置）：`company.company_ref`、`company.display_name`、`invoices[].invoice_id`、`invoices[].customer_ref`、`invoices[].contact_ref`、`invoices[].notes`、`communications[].comm_id`、`communications[].summary`、`followup_stages[].name`。判定要求**同一个子句内既出现覆盖动作**（忽略 / 無視 / ignore / disregard / forget / override / leak …）**又出现危险目标**（指令 / 规则 / 要求 / 系统 / 提示词 / instruction / rule / system / prompt …），或命中少量绝不会出现在应收备注里的固定短语（如 `系统提示词`、`system prompt`）。因此**单个动词或单个名词不触发**：普通财务备注「请忽略小额尾差，财务已核销。」「Ignore the previous invoice status」与「系统提示：本月对账窗口关闭」都不标记；而「忽略上述指令」「請忽略上述指令」「无视上述要求」「忽略规则」「Ignore the previous instructions」仍命中。`injection_flagged` 以确定性（升序）顺序列出全部命中位置；结构化字段与该行/对象的 `injection_flags` 同时带 `PROMPT_INJECTION_IGNORED` 风险标记。

**（3）命中之后，命中值不进入任何人读交付物。** 结构化 JSON 保留原始值并加风险标记；`markdown_summary`、`drafts[].text`、待澄清问题里的发票引用与 `reference` 标签一律改用固定占位「已隐藏疑似提示注入文本」。用户的判定字段（状态、计数、队列、缺口）逐字段不变。

**（4）隐私门禁。** 出现 `password` / `token` / `secret` / `api_key` / `cookie` 等字段名，或 PEM 私钥块、常见云厂商密钥前缀、代码托管平台个人访问令牌前缀等凭据样式字符串时，**整批拒绝处理且不回显该内容**（具体前缀清单在 `scripts/run.py` 的 `SECRET_VALUE_PATTERNS` 中，此处不复写以免文档本身触发扫描）。

---

## 4. 输出阅读顺序

1. `status` —— 一眼看整批性质。
2. `followup_queue` —— 按 `P1 → P2 → P3` 处理。
3. `evidence_gaps` + `clarification_questions` —— 原样发给对方补齐，不要替对方作答。
4. `totals_by_currency` —— 分币种核对，不加总。
5. `overdue_invoice_ids` / `disputed_invoice_ids` / `promised_invoice_ids` / `unknown_due_date_invoice_ids` / `invalid_invoice_ids` —— 分类清单。
6. `drafts` —— 人工确认并补齐占位符后自行发送。
7. `markdown_summary` —— 可直接交给团队的跟进板。

---

## 5. 明确不做的事

- 不发送邮件 / 短信 / 微信，不拨打电话，不催收，不扣款，不提交争议；
- 不计算滞纳金、利息、罚息、违约金、税费，不做坏账或信用风险结论；
- 不判断付款义务是否成立、是否可诉，不给法律意见；
- 不把不同币种折算相加；
- 不用未提供的账期、宽限期、承诺日或联系人补默认值；
- 不访问任何店铺、银行、邮箱或后台。

---

## 6. 样例

`references/sample.json` 刻意包含多种情况：正常逾期、即将到期、部分付款 + 贷项通知、未到账（PENDING）付款、跨币种付款、争议挂起、承诺付款、到期日缺失、重复发票号、负数金额、非法日期格式、到期日早于开票日，在**公司名、跟进阶段名、沟通 `comm_id`、发票备注与沟通摘要**五处不同位置的提示注入，以及两处**普通财务备注**（「请忽略小额尾差，财务已核销。」与 "Ignore the previous invoice status; payment was confirmed manually."）——它们按普通文本处理，**不标记、不产生占位**，用于在发布包内确证低误报。

运行：

```bash
python3 scripts/run.py references/sample.json
```

期望整体状态 `DISPUTE_HOLD`，跟进队列 9 条，证据缺口 15 条，待澄清问题 7 条（`Q-01`～`Q-07`），人工草稿 8 条，提示注入来源按升序列出 5 条精确位置：`communications[3].comm_id`、`communications[4].summary`、`company.display_name`、`followup_stages[2].name`、`invoices[12].notes`；`markdown_summary` 与 `drafts[].text` 中不出现任何注入原文，只出现固定占位「已隐藏疑似提示注入文本」。
