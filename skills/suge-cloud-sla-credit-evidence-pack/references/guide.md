# 字段、公式与输出规范（云服务 SLA 补偿证据包）

## 1. 输入口径

顶层对象：

| 字段 | 必填 | 说明 |
|---|---|---|
| billing_cycle | 是 | 计费周期，`YYYY-MM`（如 2026-08） |
| provider / service | 是 | 服务商与服务名（≤80 文本） |
| eligible_fee | 是* | 该周期可申请补偿的费用金额（≥0）；**缺失→不估算赔付金额** |
| currency | 否 | 三位代码，默认 CNY |
| total_minutes | 是 | 周期总分钟数（如 8 月 31 天 = 44640） |
| timezone | 是* | `Asia/Shanghai` 或 `+08:00`；**缺失→无法裁切跨月故障、无法核算可用性，不估算赔付** |
| as_of_date / claim_deadline | 否 | ISO YYYY-MM-DD；两者都有时才提示申请期限风险 |
| credit_tiers | 是* | SLA 档位列表：`{min_availability: 99.95, credit_percent: 10}` 表示"可用性低于 99.95% 补偿 10%"；**缺失→不估算赔付比例** |
| incidents | 是 | 0–500 项故障记录 |

每条故障记录：`start` / `end`（ISO8601 时间，建议带时区偏移如 `2026-08-05T09:10:00+08:00`；无偏移时按顶层 timezone 解释）、`error_type`、`resource_id`（可选）、`log_evidence`（可选日志证据引用）、`excluded`（布尔，默认 false）、`exclusion_reason`（可选）。**结束时间早于开始时间直接拒绝**；命令/URL/提示注入只按文本处理，不回读任何日志或 URL。

## 2. 计算口径（确定性脚本）

- 每个故障先与计费周期边界裁切（跨月故障只保留落在本周期内的分钟数，周期边界按顶层 timezone 折算为 UTC 后比较）。
- **重叠故障合并**：按开始时间排序后合并重叠区间，候选不可用分钟 = 合并后区间长度之和（同一段时间只计一次）。
- 已标记 `excluded=true`（计划维护、客户配置错误、第三方原因等）的事件**不自动计入**候选分钟，单列 `excluded_records` 供人工复核。
- 可用性 =（周期总分钟 − 候选不可用分钟）/ 周期总分钟 ×100%，显示保留 4 位小数。
- 档位匹配：取所有满足"可用性 < min_availability"的档位中补偿比例最高者。
- 估算补偿金额 = eligible_fee × credit_percent / 100（精确到分）。
- 申请期限：claim_deadline 早于 as_of_date → 已过期；≤7 天 → 临近风险。

## 3. 状态口径

| status | 触发 | 含义 |
|---|---|---|
| ESTIMATE_READY | 无缺口、无排除、期限充裕 | 证据包可整理提交（仍需人工复核） |
| REVIEW_EXCLUSIONS | 存在已标记除外的事件 | 先人工确认除外原因是否符合 SLA |
| EVIDENCE_MISSING | 无时区 / 无档位 / 无费用 / 缺日志证据引用或资源 ID | 先补证据再申请 |
| DEADLINE_RISK | 申请期限已过期或 ≤7 天 | 优先处理期限 |
| UNKNOWN | 无任何故障记录 | 无故障不构成申请依据 |

优先级：DEADLINE_RISK > EVIDENCE_MISSING > REVIEW_EXCLUSIONS > UNKNOWN > ESTIMATE_READY。

## 4. 输出结构

- 顶层：`billing_cycle`、`provider`、`service`、`currency`、`eligible_fee`、`total_minutes`、`timezone`、`as_of_date`、`claim_deadline`、`candidate_downtime_minutes`、`merged_segments`、`availability_pct`、`matched_tier`、`estimated_credit`、`excluded_count`、`excluded_records[]`、`outside_cycle[]`（不在本周期内的故障，透明展示不静默丢弃）、`evidence_gaps[]`（含下标与缺失项）、`status`、`reasons[]`、`disclaimer`、`markdown_summary`。
- `merged_output` 段（segment 列表，UTC 起止）用于人工对照后台事件。
- `markdown_summary` 可直接渲染；`disclaimer` 必含"估算，以购买时生效的 SLA 和厂商审核为准"。

## 5. 限制

- 只做估算与证据整理；**不代提交补偿申请、不代联系客服、不打开日志/URL 内容**。
- 单币种；不含税；不自动换算；档位规则以用户输入为准，不替用户记厂商 SLA 文本。
- 排除是否成立、日志是否被厂商接受，均需人工复核；样例（references/sample.json）为合成数据（示例云、故障时间线均虚构）。
