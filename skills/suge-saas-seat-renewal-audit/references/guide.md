# 字段、公式与输出规范（SaaS 席位与续费审计）

## 1. 输入口径

顶层对象：`as_of_date`（ISO YYYY-MM-DD，必填，审计基准日）、`currency`（三位代码，默认 CNY）、`subscriptions`（1–500 项）。

每项订阅字段：

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| vendor | 是 | 1–120 文本 | 服务商名；注入内容只按文本 |
| plan | 否 | 1–120 文本 | 套餐名 |
| seats_purchased | 是 | 0–10^6 整数 | 已购席位 |
| active_users_30d | 否 | 0–10^6 整数 | 30 天活跃人数；**缺失→该行 UNKNOWN** |
| unit_price | 是 | 0–10^7 | 每席位单价（Decimal，2 位小数） |
| billing_cycle | 是 | monthly/yearly | 计费周期 |
| renewal_date | 是 | ISO 日期 | 续费日 |
| auto_renew | 是 | bool | 是否自动续费 |
| minimum_seats | 是 | 0–10^6 整数 | 合同最低承诺席位数 |
| cancellation_notice_days | 否 | 0–3650 整数 | 服务商要求的取消提前天数；缺失→截止日"未知" |

## 2. 公式

- 闲置席位 `idle = seats_purchased − active_users_30d`（活跃缺失则不计算）。
- 利用率 `utilization = active / seats × 100%`。
- 可削减候选 `candidate = min(idle, seats − minimum_seats)`（不能低于最低承诺；负数取 0）。
- 月度成本口径：`cost_monthly = unit_price × seats`；`cost_annualized = cost_monthly × 12`（monthly）或 `unit_price × seats`（yearly）。
- 理论年节省估算 `savings = candidate × unit_price × (12 if monthly else 1)`——**估算**，基于下次续费即降配假设，非保证节省。
- 取消通知最后日期 `last_cancel_date = renewal_date − cancellation_notice_days`（纯日历日运算）。
- 续费窗口：距基准日 ≤30 天 `due<=30`；≤60 `due<=60`；≤90 `due<=90`；否则 `ok`；早于基准日 `expired`。

## 3. 动作口径（不代执行）

| action | 触发 | 含义 |
|---|---|---|
| KEEP | 无闲置、未过期、无取消窗口风险 | 维持 |
| REDUCE_CANDIDATE | idle > 0 且 candidate > 0 | 存在超过最低承诺的闲置席位候选，未执行降配 |
| REVIEW | 续费日已过期 / 活跃 > 已购（数据可疑）/ 已进入或错过自动续费取消窗口 | 需人工处理 |
| UNKNOWN | active_users_30d 缺失 | 数据缺失，不把全部席位判闲置 |

优先级：校验失败拒绝计算；过期→REVIEW；活跃缺失→UNKNOWN；有候选→REDUCE_CANDIDATE；自动续费进入通知窗口→REVIEW；否则 KEEP。

## 4. 输出结构

- 顶层：`as_of_date`、`currency`、`subscription_count`、`total_monthly_cost`、`total_annualized_cost`、`total_idle_seats_known`、`total_reduction_candidates`、`potential_annual_savings_estimate_total`、`action_counts`、`subscriptions[]`、`markdown_summary`。
- 每个订阅 detail：见上公式项 + `action`、`reasons[]`、`note`。
- `markdown_summary`：可直接渲染的表格摘要，包含动作口径说明。

## 5. 限制

- 单币种，不自动换算；不含税；不考虑按人头阶梯价或用量浮动价。
- 不联网核对服务商当前政策；取消通知天数以用户输入的条款为准。
- 不执行取消/降配/发信；不读取订阅账号凭据。
- 样例（references/sample.json）为合成数据（示例云协作、示例文件存储），不代表真实服务商。
