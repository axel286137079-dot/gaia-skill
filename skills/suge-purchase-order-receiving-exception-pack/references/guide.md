# 采购收货差异与待处理清单 — 字段表与判定顺序

本文件是 `suge-purchase-order-receiving-exception-pack` 的参考手册。脚本
`scripts/run.py` 只读一个本地 JSON，向 stdout 输出一个 JSON 文档。

- 常规样例：@references/sample.json（覆盖 MATCHED / PARTIALLY_RECEIVED /
  SHORTAGE / OVER_RECEIVED / DAMAGED / WRONG_ITEM / QUANTITY_CONFLICT，
  并生成一条未发送供应商沟通草稿）
- 阻塞样例：@references/sample-blocked.json（重复编号、非对象记录、未知
  line_id、负数/非数值数量、单位冲突、币种冲突、非法时间、个人信息、受保护
  属性、拒绝引用、注入、无同意）

---

## 1. 输入字段

### 1.1 顶层

| 字段 | 必需 | 说明 |
|---|---|---|
| `as_of` | 是 | 判断当前时点的基准，**必须带时区偏移**（如 `2026-10-05T19:00:00+08:00`）。缺失/无法解析时输出 `INPUT_INCOMPLETE`。 |
| `purchase_order` | 是 | 见 1.2；缺 `po_id` 时输出 `INPUT_INCOMPLETE`。 |
| `ordered_lines[]` | 是 | 见 1.4。**空列表**记 `MISSING_ORDERED_LINES`（证据不足）。 |
| `receipts[]` | 建议 | 见 1.5。可为空（表示尚未到货）。 |
| `owners[]` | 建议 | 见 1.3。缺失记 `MISSING_OWNERS`。 |
| `handling_rules` | 建议 | **必须由用户显式提供**；本工具**不内置任何平台或法律规则**。缺失记 `MISSING_HANDLING_RULES`。 |
| `communication_consent` | 建议 | 布尔。仅控制是否生成一条 `DRAFT_NOT_SENT` 供应商沟通草稿；本工具**绝不外发**。 |
| `notes` | 否 | 自由文本；参与注入扫描。 |
| `evidence_refs[]` | 否 | 单一文件名列表；含路径或链接者被拒绝。 |

### 1.2 `purchase_order`（全部来自用户事实，**不得内置默认**）

| 字段 | 必需 | 说明 |
|---|---|---|
| `po_id` | 是 | 缺失记 `MISSING_PO_ID`（`INPUT_INCOMPLETE`）。 |
| `supplier_id` | 否 | 匿名供应商编号。 |
| `destination_id` | 否 | 匿名收货点编号。 |
| `ordered_at` | 建议 | 带偏移时间。收货时间早于它记 `RECEIPT_BEFORE_ORDER`（阻塞）。 |
| `currency` | 否 | 标准三字母代码。非三字母记 `UNKNOWN_CURRENCY`（阻塞）。 |
| `order_status` | 建议 | 显式状态，见 §2.1。缺失记 `MISSING_ORDER_STATUS`；表外值记 `INVALID_ORDER_STATUS`（阻塞）；`CANCELLED` 记 `ORDER_CANCELLED`（阻塞）。 |

### 1.3 `owners[]`

| 字段 | 必需 | 说明 |
|---|---|---|
| `owner_id` | 是 | 匿名编号；缺失/重复记阻塞码。 |
| `responsibilities[]` | 否 | 职责，自由文本。 |

负责人待办按 `owner_id` 稳定排序。

### 1.4 `ordered_lines[]`（采购行）

| 字段 | 必需 | 说明 |
|---|---|---|
| `line_id` | 是 | 匿名编号。缺失记 `MISSING_LINE_ID`；重复让所有同号行记 `DUPLICATE_LINE_ID`（均阻塞）。 |
| `sku_id` | 建议 | 缺失记 `MISSING_SKU_ID`（证据不足）。 |
| `ordered_qty` | 是 | 非负数值（允许小数）。缺失记 `MISSING_ORDERED_QTY`；负数/非数值/布尔/NaN 记 `INVALID_ORDERED_QTY`（阻塞）。 |
| `unit` | 是 | 计量单位字符串。缺失记 `MISSING_UNIT`（证据不足）；非字符串记 `INVALID_UNIT`（阻塞）。 |
| `expected_unit_cost` | 否 | 非负数值；非法记阻塞（不参与数量桥）。 |
| `expected_by` | 否 | 带偏移时间；用于逾期未到与迟到判断。非法记 `INVALID_EXPECTED_BY`（阻塞）。 |
| `owner_id` | 是 | 必须出现在 `owners[]`；缺失记 `MISSING_OWNER`，不在清单记 `UNKNOWN_OWNER`。 |
| `currency` | 否 | 与采购单币种不同记 `ORDER_CURRENCY_CONFLICT`（阻塞）。 |
| `evidence_refs[]` | 建议 | 单一文件名；含路径/链接者被拒绝。 |

### 1.5 `receipts[]`（收货单）

| 字段 | 必需 | 说明 |
|---|---|---|
| `receipt_id` | 是 | 匿名编号。缺失记 `MISSING_RECEIPT_ID`；重复记 `DUPLICATE_RECEIPT_ID`（均阻塞）。 |
| `line_id` | 是 | 必须存在于 `ordered_lines[]`；否则记 `UNKNOWN_LINE_ID`（阻塞，进入未分配收货单）。 |
| `received_at` | 是 | 带偏移时间。缺失记 `MISSING_RECEIVED_AT`；非法记 `INVALID_RECEIVED_AT`（阻塞）；早于 `ordered_at` 记 `RECEIPT_BEFORE_ORDER`（阻塞）。 |
| `received_qty` | 是 | 非负数值。缺失记 `MISSING_RECEIPT_QTY`；非法记 `INVALID_RECEIPT_QTY`（阻塞）。 |
| `accepted_qty` `damaged_qty` `wrong_item_qty` `rejected_qty` `unaccounted_qty` | 是 | 全部非负数值；缺失记 `MISSING_RECEIPT_QTY`，非法记 `INVALID_RECEIPT_QTY`。**数量桥要求这五项之和等于 `received_qty`。** |
| `unit` | 否 | 与采购行单位不同记 `UNIT_CONFLICT`（阻塞）；本工具**不换算单位**。 |
| `currency` | 否 | 与采购单币种不同记 `CURRENCY_CONFLICT`（阻塞）；本工具**不跨币种合并**。 |
| `evidence_refs[]` | 建议 | 记录 `damaged_qty`/`wrong_item_qty`/`rejected_qty` > 0 时**必须**有证据，否则记 `MISSING_EVIDENCE`（证据不足）。 |

---

## 2. 判定顺序

### 2.1 订单状态词表

```
OPEN  PARTIALLY_RECEIVED  CLOSED  CANCELLED  UNKNOWN
```

接受少量同义词（`PENDING`/`IN_PROGRESS`→`OPEN`、`COMPLETED`/`DONE`/`RECEIVED`→`CLOSED`、
`CANCELED`/`VOID`→`CANCELLED`），其余表外值一律 `INVALID_ORDER_STATUS`。

### 2.2 数量桥（逐行，必须守恒）

```
已订 ordered_qty
累计实收 received_qty = Σ 各收货单 received_qty
接受 accepted_qty  = Σ accepted_qty
损坏 damaged_qty   = Σ damaged_qty
错货 wrong_item_qty= Σ wrong_item_qty
拒收 rejected_qty  = Σ rejected_qty
未解释 unaccounted_qty = Σ unaccounted_qty
仍未到 still_outstanding_qty = ordered_qty − received_qty
```

守恒约束（任一不满足 → `QUANTITY_CONFLICT`）：

1. `accepted + damaged + wrong_item + rejected + unaccounted == received`
   （不满足记 `DISPOSITION_MISMATCH`）；
2. `accepted <= received`（不满足记 `ACCEPTED_EXCEEDS_RECEIVED`）。

- **单位不一致不换算**：收货单单位与采购行不同即 `UNIT_CONFLICT`。
- **币种不跨币种合并**：不同币种即 `CURRENCY_CONFLICT`。
- 汇总 `quantity_bridge_totals` **只并入守恒的行**；不守恒或被阻塞的行记入
  `quantity_bridge_excluded_lines`，不静默混入总量。

### 2.3 逐行判定（状态取最严重者）

```
BLOCKED > INSUFFICIENT_EVIDENCE > QUANTITY_CONFLICT > WRONG_ITEM > DAMAGED
        > OVER_RECEIVED > SHORTAGE > PARTIALLY_RECEIVED > MATCHED
```

```
硬阻塞（任一命中 → BLOCKED；阻塞行不做数量桥）
  INVALID_LINE_RECORD  MISSING_LINE_ID  DUPLICATE_LINE_ID
  INVALID_ORDERED_QTY  INVALID_EXPECTED_UNIT_COST  INVALID_EXPECTED_BY
  INVALID_UNIT  UNIT_CONFLICT
  INVALID_RECEIPT_RECORD  MISSING_RECEIPT_ID  DUPLICATE_RECEIPT_ID
  UNKNOWN_LINE_ID  INVALID_RECEIVED_AT  RECEIPT_BEFORE_ORDER
  INVALID_RECEIPT_QTY  CURRENCY_CONFLICT  UNKNOWN_CURRENCY
  ORDER_CANCELLED  INVALID_ORDER_STATUS  ORDER_CURRENCY_CONFLICT
  PERSONAL_DATA_REFUSED  RESTRICTED_DATA_REFUSED  SENSITIVE_ATTRIBUTE_REFUSED

证据不足（任一命中 → INSUFFICIENT_EVIDENCE；未知不当作已完成）
  MISSING_SKU_ID  MISSING_UNIT  MISSING_ORDERED_QTY
  MISSING_RECEIVED_AT  MISSING_RECEIPT_QTY  MISSING_EVIDENCE
  MISSING_OWNER  UNKNOWN_OWNER

数量冲突（任一命中 → QUANTITY_CONFLICT）
  DISPOSITION_MISMATCH  ACCEPTED_EXCEEDS_RECEIVED

差异（按严重度，较低者）
  wrong_item_qty > 0 → WRONG_ITEM
  damaged_qty  > 0   → DAMAGED
  received_qty > ordered_qty → OVER_RECEIVED
  仍未到 > 0 且已逾期 → SHORTAGE
  仍未到 > 0          → PARTIALLY_RECEIVED
  其余               → MATCHED
```

逾期（`OVERDUE_NOT_ARRIVED`）= `仍未到 > 0` **且**（`expected_by` 早于 `as_of`，
或订单状态为 `CLOSED`/`CANCELLED`）。迟到（`LATE_DELIVERY`）= 某次 `received_at`
晚于 `expected_by`。二者为提醒，不改变上面的数量状态。

### 2.4 顶层（批）判定

- 缺 `as_of` / `purchase_order` / `po_id` → `INPUT_INCOMPLETE`（不进入五态）。
- 批级阻塞：任一 `ORDER_CANCELLED` / `INVALID_ORDER_STATUS` / `UNKNOWN_CURRENCY`
  / `ORDER_CURRENCY_CONFLICT` / 任一行的阻塞码 → `BLOCKED`。
- 批级证据不足：`MISSING_ORDER_STATUS` / `MISSING_HANDLING_RULES` /
  `MISSING_OWNERS` / `MISSING_ORDERED_LINES` / 任一行的证据不足码 →
  `INSUFFICIENT_EVIDENCE`（在无阻塞时）。
- 否则取**所有行状态的最严重者**。

### 2.5 供应商沟通草稿（同意门禁）

- **仅当** `communication_consent: true` **且** 顶层状态为
  `PARTIALLY_RECEIVED` / `SHORTAGE` / `OVER_RECEIVED` / `DAMAGED` /
  `WRONG_ITEM` / `QUANTITY_CONFLICT` 时，生成一条 `DRAFT_NOT_SENT` 草稿。
- `false` / 缺失 / 非布尔 → **不生成**；`MATCHED` / `BLOCKED` /
  `INSUFFICIENT_EVIDENCE` → **不生成**。
- 任何草稿**绝不外发**，收件人只列 `owner_id`。

### 2.6 安全

- **凭据**：字段名命中 `api_key`/`secret`/`password`/`token`/... 或值形如密钥 →
  整体 `REJECTED`，只输出字段路径，不回显值。
- **个人信息 / 受限业务内容 / 受保护属性**：字段名或值命中 → `refused_fields`
  （只含路径与原因 `PERSONAL_DATA` / `RESTRICTED_DATA` / `SENSITIVE_ATTRIBUTE`），
  该记录 `BLOCKED`，值在任何输出中都不出现。受限内容包括银行/发票原文/合同文本等。
- **文件名**：`evidence_refs` 只接受单一文件名；含 `/`、`\`、`:`、`..` 记
  `PATH_REFERENCE`，含 scheme 记 `URL_REFERENCE`，只回流字段路径，不回显原引用。
- **注入**：动作词 + 目标词同句共现才标记，`injection_flagged` 给出精确路径；
  `markdown_summary` 中该值替换为「已隐藏疑似提示注入文本」。
- 控制字符被剥离；Markdown 元字符加反斜杠转义，不可信文本无法伪造格式。

---

## 3. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | `REJECTED` / `INPUT_INCOMPLETE` / 九态之一 |
| `receiving_state` | 批级九态之一 |
| `purchase_order` | 规范化后的匿名订单事实 |
| `handling_rules` | 用户提供的处理规则（`source: USER_PROVIDED`），非本工具建议 |
| `lines[]` | 每行状态、状态码与 `quantity_bridge` |
| `receipts[]` / `unassigned_receipts[]` | 收货单（含引用未知 line_id 者） |
| `quantity_bridge_totals` | 按单位汇总（只并入守恒行） |
| `quantity_bridge_excluded_lines` | 未并入总量的行 |
| `line_state_counts` | 行九态计数 |
| `discrepancies` / `missing_evidence` / `overdue_or_not_arrived` | 差异、缺失证据、逾期未到与迟到 |
| `owner_todos` | 每位负责人的分工与未解决行 |
| `blockers` / `missing_facts` | 阻塞与缺失事实 |
| `refused_fields` / `refused_refs` / `accepted_refs` | 被拒绝字段与引用（只含路径与原因） |
| `injection_flagged` | 注入命中路径 |
| `communication_consent` / `supplier_draft` | 同意事实与草稿（恒 `DRAFT_NOT_SENT` 或 `null`） |
| `clarification_questions` / `human_checklist` / `human_confirm_items` | 澄清问题与人工复核清单 |
| `next_step` / `markdown_summary` | 下一步提示与 Markdown 收货准备单 |
| `disclaimer` / `automation_declaration` | 免责与边界声明 |
| `read_only` / `network` / `writes_files` | 恒为 `true` / `false` / `false` |

---

## 4. 边界

本工具不读取任何 WMS/ERP/库存/财务/邮箱/账号系统、不创建或修改采购单与收货单、
不更新任何库存、不判断商品质量或合同责任、不发起索赔或退货、不付款、不联系供应商
或承运商、不发送消息、不换算单位、不跨币种合并、不联网、无子进程、不写文件。
所有结论只由输入中的明示匿名事实推导；缺失信息保持未知，必须由人工确认。
