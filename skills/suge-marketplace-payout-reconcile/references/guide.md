# 字段、公式与输出规范（渠道回款拆分对账）

## 1. 输入口径

顶层对象：`currency`（三位代码，默认 CNY，**单币种一批**）、`as_of`（基准时间，ISO8601 带时区或纯日期，**必填**）、`payouts`（1–500 项）。

每条回款 payout 字段：

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| payout_id | 是 | 1–80 文本（字母数字 `._:-`） | 回款标识；重复直接拒绝 |
| status | 是 | settled / paid / completed / pending / processing / unassigned / failed / rejected / unknown | settled 系=已结算可核对；其余单独列出不混入已结算判定 |
| expected_platform_payout | 是 | 数字 | 平台声明应结算给银行的净额（本币） |
| bank_received_amount | 否 | 数字 ≥0 | 银行实收金额；缺失=未观察到银行实收（不判欠款，也不判 MATCH） |
| bank_received_at | 否 | ISO8601 带时区或纯日期 | 银行到账时间；晚于基准=未到账 |
| trace_reference | 否 | ≤120 文本 | 银行流水/参考号 |
| tolerance | 否 | 数字 ≥0（默认 0.00） | 本币绝对金额容差；差异 ≤ 容差视为一致 |
| net_formula | 否 | gross+fee / gross-fee | **net 计算公式，fee 正负方向必须显式声明**；fee 为带符号金额用 gross+fee，fee 为正费用值用 gross-fee |
| transactions | 是（已结算时） | 0–2000 项 | 构成该回款的交易明细 |

每条交易 transaction 字段：

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| transaction_id | 是 | 1–80 文本 | 跨整批唯一；重复→REVIEW 线索 |
| type | 是 | charge / refund / fee / adjustment / reserve / release / other | other 及未列类型→语义未确认线索 |
| gross | 是 | 数字（可负，refund 为负） | 交易总额 |
| fee | 否 | 数字 | 平台费；缺省按 0；**有 fee 时必须声明 net_formula，不猜正负方向** |
| net | 否 | 数字 | 交易净额；缺失时仅当声明 formula 才推导 |
| currency | 否 | 三位代码 | 缺省=顶层 currency；与本币不同→跨币种线索，不混入本币汇总 |
| payout_id | 否 | 文本 | 交易归属；缺省=所在回款；声明不一致→归属不匹配线索 |
| occurred_at | 否 | ISO8601 带时区 | 交易时间；晚于基准→线索 |

时间戳必须带偏移（如 `2026-09-20T00:00:00+08:00`）或 `YYYY-MM-DD`；naive 时间戳、负金额（gross 允许负，其余金额非负）、NaN/Infinity、重复 ID、命令/URL/提示词一律安全处理（命令只当文本，不执行）。

## 2. 计算口径（三层核对）

- **trusted net** = Σ(同币种、类型明确、归属一致、有 net 的交易 net)。other/未知类型/跨币种/归属不一致的交易从 trusted net 剔除并列入 `excluded_transactions`（不给 REVIEW 线索隐藏差异）。
- **平台层差异** `platform_difference = trusted_calculated_net − expected_platform_payout`。
- **银行层差异** `bank_difference = trusted_calculated_net − bank_received_amount`（仅当银行实收存在）。
- 每条交易的 net 校验：声明 formula 时 `net = gross ± fee`（按公式），不一致→`net_formula_mismatch`；**未声明 formula 且 fee 非零→`net_formula_missing`，不猜 fee 正负方向**；未声明且 fee 为 0→net 按 gross 处理（`net_derived_as_gross` 备注）。
- 银行未到账/无实收记录：给出线索（`bank_receipt_not_observed`），**不自动写成"平台欠款"**。

## 3. 状态口径

| status | 触发 | 含义 |
|---|---|---|
| MATCH | 已结算；trusted net=平台=银行，差异均在容差内 | 三层一致 |
| PLATFORM_LEDGER_DIFFERENCE | 已结算；平台层差超容差 | 流水合计与平台声明不一致 |
| BANK_RECEIPT_DIFFERENCE | 已结算；平台层一致、银行实收差超容差 | 银行到账金额不符 |
| PENDING | 原状态非 settled 系（pending/unassigned/failed/…） | 未结算，单独列出 |
| UNKNOWN | 已结算但：无交易明细可追溯 / 存在 REVIEW 线索（重复 ID、跨币种、未知类型、归属不一致、缺公式等）/ 银行实收未观察到 | 无法形成可信差异结论 |
| INVALID | 输入结构非法（重复 payout_id、非法币种、缺基准、NaN 等） | 整体拒绝 |

状态优先级：结构非法→INVALID 拒绝；未结算→PENDING；已结算→先查可追溯性/REVIEW 线索，再查平台层差异、银行层差异、最终 MATCH。

## 4. 输出结构

- 顶层：`as_of`、`currency`、`payout_count`、`status_counts`、`payouts[]`、`totals_settled`、`markdown_summary`。
- 每条 detail：payout_id、raw_status、status、currency、expected_platform_payout、bank_received_amount/at、trace_reference、tolerance、net_formula、transaction_count、trusted_calculated_net、platform_difference、bank_difference、type_totals（按类型 count/gross/fee/net）、excluded_transactions、review_flags、reasons、note。
- `markdown_summary` 可直接渲染，含三层口径与"只输出线索，不连接账户、不发起提现或争议"。

## 5. 限制

- 本技能只做**离线对账线索**：不连接支付/银行账户、不发起提现、不发起争议或申诉。
- 银行未到账不等于平台欠款；缺少银行实收时不给 MATCH 也不给差异结论。
- net 与 fee 正负方向必须由用户声明的 net_formula 确认；未声明时保留未知，不猜方向。
- 金额用 Decimal；时间带时区；缺失值不为零；未知值保留 unknown。
- 样例（references/sample.json）为合成数据（PO-001~003，TXN-100~300）。
