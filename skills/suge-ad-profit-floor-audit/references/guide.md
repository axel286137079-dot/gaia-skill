# 字段、公式与输出规范（广告投放利润底线审计）

## 1. 输入口径

顶层对象：`as_of_date`（ISO YYYY-MM-DD，必填，审计基准日）、`currency`（三位代码，默认 CNY）、`campaigns`（1–500 项）。

每条活动字段：

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| campaign_id | 是 | 1–64 文本 | 活动标识；重复出现→后出现行强制 REVIEW 并列入 duplicate_campaign_ids |
| name | 否 | 1–120 文本 | 活动名；命令/URL/提示注入只按文本 |
| spend | 是 | 0–10^12 | 广告花费（Decimal，2 位小数） |
| attributed_revenue | 是 | 0–10^12 | 归因收入（Decimal） |
| refund_amount | 否 | 0–10^12 | 退款金额；缺省按 0 计并在 reasons 注明口径 |
| gross_margin_rate | 是 | 0–1 | 毛利率，0.35=35%；**缺失→该行 UNKNOWN，不输出保本 ROAS/最高可承受 CPA** |
| platform_fee | 否 | 0–10^12 | 平台手续费等；缺省按 0 计并注明 |
| fulfillment_cost | 否 | 0–10^12 | 履约/物流成本；缺省按 0 计并注明 |
| conversions | 否 | 0–10^9 整数 | 转化数；缺失→转化成本与最高可承受 CPA 不输出 |
| target_profit_margin | 否 | 0–1 | 目标利润率（对净收入口径） |
| attribution_lag_days | 否 | 0–3650 整数 | 归因窗口剩余天数；0=窗口已关闭，>0=收入可能未完全归因；缺省按 0 计并注明 |

金额可为 JSON 数字或字符串；比率必须是 [0,1] 数字（不支持 "35%" 写法）。负数、越界比率、非法日期直接拒绝并提示按规范检查。

## 2. 公式（口径在输出中逐项展开）

- 净收入 `net_revenue = attributed_revenue − refund_amount`（精确到分）。
- 毛利贡献 `gross_profit = net_revenue × gross_margin_rate`。
- 贡献利润 `contribution = gross_profit − spend − platform_fee − fulfillment_cost`。
- ROAS `= net_revenue / spend`（spend=0 时不输出，原因标 spend_zero）。
- 保本 ROAS（含平台费与履约费口径）`= (spend + platform_fee + fulfillment_cost) / (spend × gross_margin_rate)`；费率 0 时无意义不输出。
- 单转化成本 `CPA = spend / conversions`（无转化不输出）。
- 最高可承受 CPA（保本口径）`= 单转化净收入 × 毛利率 − (平台费+履约费)/转化数`；负值表示即使免费获客该活动按当前费用口径仍亏损。
- 目标利润 `required_profit = net_revenue × target_profit_margin`（仅当设置了 target_profit_margin）。

## 3. 状态口径（只读复核，不代执行）

| status | 触发 | 含义 |
|---|---|---|
| HEALTHY | 贡献利润 ≥ 目标利润（无目标时 ≥0） | 达标 |
| REVIEW | 正贡献但低于目标利润率 / 花费为 0 待核 / 重复 campaign_id | 需人工核对口径 |
| LOSS_CANDIDATE | 贡献利润 < 0 且归因窗口已关闭 | 亏损候选，仅提示复核 |
| DATA_DELAY | attribution_lag_days > 0（窗口未关闭） | 收入可能未完全归因，不下亏损结论 |
| UNKNOWN | 毛利率缺失 | 数据缺失，不输出保本 ROAS 类结论 |

优先级：校验失败拒绝计算 → 重复 ID REVIEW → 毛利率缺失 UNKNOWN → 归因未闭合 DATA_DELAY → 亏损/达标判断。任何状态都不暂停、不加预算、不改出价。

## 4. 输出结构

- 顶层：`as_of_date`、`currency`、`campaign_count`、`total_spend`、`total_net_revenue`、`total_contribution_profit_known`、`duplicate_campaign_ids`、`status_counts`、`campaigns[]`、`markdown_summary`。
- 每条活动 detail：上述公式项 + `formula_breakdown`（逐项中文公式回显，注明分子分母）+ `status`、`reasons[]`、`checklist[]`（止损复核清单）、`note`。
- `markdown_summary`：可直接渲染的表格摘要，含状态口径与比率口径说明。

## 5. 限制

- 单币种不换算、不含税；毛利率为用户输入口径，不替用户重算真实毛利率。
- 不联网拉取平台报表；归因口径以平台后台为准；attribution_lag_days 由用户按平台归因窗口填写。
- 不执行暂停/加预算/改出价/发消息；清单只提示人工动作。
- 样例（references/sample.json）为合成数据（C-101/102/103），不代表真实活动。
