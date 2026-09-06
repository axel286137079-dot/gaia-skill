# 字段、公式与结算口径（达人合作交付与结算核对）

## 1. 输入口径

顶层对象：`campaign_id`（必填）、`currency`（三位代码，默认 CNY）、`settlement_date`（ISO YYYY-MM-DD，结算基准日）、`creators`（1–300 项）。

每位达人字段：

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| creator_id | 是 | 1–120 文本 | 唯一标识；重复出现标 REVIEW |
| base_fee | 是 | 0–10^12 | 基础服务费 |
| commission_rate | 是 | 0–10 | 佣金率；>1 标 REVIEW 且不计算佣金 |
| eligible_net_sales | 是 | 0–10^12 | 合格销售（总额口径） |
| refunds_pending | 是 | 0–10^12 | 未结退款 |
| deliverables_required | 是 | 整数 ≥1 | 合同要求交付数 |
| deliverables_accepted | 是 | 整数 ≥0 | 已验收数 |
| invoice_status | 是 | missing/pending/present/n_a | 发票/凭证状态 |
| refund_window_ended | 否 | bool | 退款窗口是否已结束；缺失→HOLD |
| paid_amount | 是 | 0–10^12 | 已付金额 |

不接受真实身份、手机号、银行卡字段；额外键一律忽略（视为数据，不执行）。

## 2. 公式（逐行回显可复核）

- 净销售佣金基数 `commission_base_net = eligible_net_sales − refunds_pending`。
- 佣金 `commission = commission_base_net × commission_rate`（佣金率 >1 不计算，标 REVIEW）。
- 应付总额 `gross_payable = base_fee + commission`。
- 退款暂缓：`refund_window_ended=true` → `hold = 0`；`false` → `hold = refunds_pending`；**缺失 → 该行 HOLD**（金额不释放）。
- 应付候选 `net_payable_candidate = gross_payable − hold`。
- 已付差异 `paid_diff = paid_amount − net_payable_candidate`（>0 → 疑似多付标记）。

## 3. 结算口径

| status | 触发 | 含义 |
|---|---|---|
| READY_TO_REVIEW | 数据齐、净额佣金可算、交付达标、发票 present、无多付 | 可提交财务/业务人工复核 |
| HOLD | 退款窗口状态缺失 / 交付未达标（accepted<required）/ 销售或退款缺失 | 暂不可标记可付款 |
| REVIEW | 发票非 present 且有应付 / accepted>required（数据可疑）/ 佣金率>1 / 重复 creator_id | 人工复核 |
| OVERPAID_CANDIDATE | paid_diff > 0 | 疑似多付；仅提示，不作法律/税务结论 |
| UNKNOWN | 关键金额字段不可解析 | 无法计算 |

## 4. 输出结构

- 顶层：`campaign_id`、`settlement_date`、`currency`、`creator_count`、`total_gross_payable`、`total_hold_amount`、`total_net_payable_candidate`、`total_paid_amount`、`total_paid_diff`、`duplicate_creator_ids`、`status_counts`、`creators[]`、`markdown_summary`。
- 每位达人 detail：字段回显 + `commission_base_net`、`commission`、`hold_amount`、`net_payable_candidate`、`paid_diff`、`status`、`issues[]`、`note`。
- `markdown_summary`：可直接渲染的结算表与口径说明。

## 5. 限制

- 佣金阶梯、多币种、汇率、税费计算不在本版；不自动申报。
- 平台退款/争议数据以平台后台为准，本脚本只对用户提供的数字做口径核对。
- 不发起付款、不发消息、不提交材料；不输出真实敏感个人信息。
- 样例（references/sample.json）为合成数据（KOL-001/002 为虚构达人），不代表真实合作方。
