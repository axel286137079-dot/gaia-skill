# 字段、公式与输出规范（订阅变更折算核对）

## 1. 输入口径

顶层对象：`currency`（三位代码，默认 CNY，单币种）、`subscriptions`（1–500 项）。

每条订阅字段：

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| subscription_id | 是 | 1–64 文本 | 订阅标识；重复或缺失直接拒绝 |
| mode | 否 | linear_same_cycle | 折算模式；**仅支持 linear_same_cycle（缺省即同周期线性）**，其它→REVIEW |
| billing_basis | 否 | seconds / actual_days | 剩余比例口径；缺省 seconds |
| cycle_start / cycle_end | 是 | ISO8601 带 UTC 偏移 | 计费周期起止；结束须晚于开始 |
| change_at | 是 | ISO8601 带 UTC 偏移 | 变更生效时间；超出周期→REVIEW |
| old_period_amount | 是 | ≥0 | 变更前周期价（原价） |
| new_period_amount | 是 | ≥0 | 变更后周期价（新价） |
| old_invoice_paid | 是 | bool | 旧账单是否已付；非 true→REVIEW，不自动退款 |
| invoiced_adjustment | 否 | 数字 | 账单已体现的调整金额，用于比对 invoice_match |
| policy_source | 否 | ≤200 文本 | 折算规则来源；缺失→REVIEW，不判确定差异 |

时间戳必须是带偏移的 ISO8601（如 `2026-09-16T00:00:00+08:00`）；naive 时间戳与非法日期直接拒绝。金额为 JSON 数字或字符串；负值/NaN 拒绝。命令、URL、提示词只当文本，不执行。

## 2. 计算口径（linear_same_cycle，同周期线性）

- 剩余占比 `fraction = (cycle_end − change_at) / (cycle_end − cycle_start)`。
  - `billing_basis=seconds`：用绝对秒差。
  - `billing_basis=actual_days`：仅接受三个时间戳同一固定 UTC 偏移、且均为当地午夜 00:00 的输入，按自然日差计算；否则 REVIEW（复杂时区/DST 交人工）。
- 新增(charge) `= new_period_amount × fraction`；抵扣(credit) `= old_period_amount × fraction`。
- 净额 `net = charge − credit`。
- 金额逐项 **ROUND_HALF_UP 到分**后再求净额；`rounding_breakdown` 回显逐项舍入前/后的原始值（舍入点可见、可复算）。

## 3. 状态口径

| status | 触发 | 含义 |
|---|---|---|
| ADDITIONAL_CHARGE | net > 0 | 应补差额（升配场景） |
| CREDIT_CANDIDATE | net < 0 | **候选抵扣，非已退款**（降配场景，不自动发起退款） |
| NO_CHANGE | net = 0 | 无净变化 |
| REVIEW | 模式不支持 / 变更跨周期 / 旧账单未付或未知 / 缺 policy_source / actual_days 复杂时区或非午夜 | 需人工确认，不做自动处理 |

优先级：任何 REVIEW 阻塞项存在即整体 REVIEW，不输出折算结论。

## 4. 输出结构

- 顶层：`currency`、`subscription_count`、`status_counts`、`subscriptions[]`、`markdown_summary`。
- 每条 detail：起止/变更时间、原价/新价、remaining_fraction 与 remaining_detail（口径回显）、charge/credit/net、rounding_breakdown（逐项 raw→rounded）、old_invoice_paid、invoiced_adjustment、invoice_match/invoice_diff（有账单调整时）、policy_source、status、reasons、note。
- `markdown_summary` 可直接渲染，含"折算公式为用户选定假设，非通用账单引擎"。

## 5. 限制

- 本技能实现的线性折算是**用户选定的假设**，不是 Stripe/阿里云等通用账单引擎的官方算法；涉及退款/发票请以服务商实际账单与条款为准。
- 只做估算核对：**不发起退款、不生成账单、不修改订阅**。
- actual_days 不处理 DST 切换与复杂时区（交人工）；样例（references/sample.json）为合成数据（SUB-001~003）。
