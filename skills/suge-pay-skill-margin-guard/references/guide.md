# 字段、公式与报告规范（Pay Skill 盈亏与定价守门）

## 1. 数据口径与字段

顶层为单个 JSON 对象，币种单一（默认 CNY，不自动换算）。所有数字允许十进制字符串；拒绝布尔、None、非有限数及超出上限的值。

### 必填字段

| 字段 | 含义 | 范围 | 说明 |
|---|---|---|---|
| display_price | 展示价（对用户标价） | 0–10^9 | 与注册价、服务端价不一致时红灯 |
| register_price | 注册价（平台登记/目录价） | 0–10^9 | 同上 |
| server_price | 服务端价（实际扣款/账单价） | 0–10^9 | 利润与手续费以此为基数 |
| avg_input_tokens | 单次成功交付平均输入 token | 0–10^10 整数 | 样例 8000 |
| avg_output_tokens | 单次成功交付平均输出 token | 0–10^10 整数 | 样例 1500 |
| input_price_per_mtok | 输入模型单价（每百万 token） | 0–10^6 | 与结算币种同币种 |
| output_price_per_mtok | 输出模型单价（每百万 token） | 0–10^6 | 同上 |
| monthly_fixed_cost | 月固定成本（服务器/订阅/人工底薪分摊等） | 0–10^12 | 按月摊销进盈亏平衡 |
| expected_monthly_success_calls | 预计月成功调用量 | 0–10^12 整数 | 0 时盈亏平衡与月利润不可算 |

### 可选但必须显式（缺失 → unknown_assumptions）

| 字段 | 含义 | 范围 | 缺失后果 |
|---|---|---|---|
| currency | 币种代码 | [A-Z]{3} | 默认 CNY |
| payment_fee_rate | 支付/平台费率（按服务端价） | 0–0.99 | 未计入，报告标注 |
| refund_rate | 退款率（按成功收费笔数） | 0–1 | 未计入，报告标注 |
| failure_retry_rate | 失败重试率（额外尝试比例） | 0–0.99 | 未计入，报告标注 |
| tax_rate | 税费测算率（按服务端价） | 0–0.99 | 未计入，报告标注 |
| manual_review_cost_per_call | 单次人工复核成本 | 0–10^9 | 未计入，报告标注 |
| target_margin_rate | 目标毛利率 | 0–0.99 | 不输出目标售价项 |

## 2. 公式（全部回显可审计）

记 `in/out` 为 token 数，`p_in/p_out` 为每百万 token 单价，`P` 为服务端价，`F/T/R` 分别为支付费率、税费测算率、退款率，`r` 为失败重试率，`M` 为单次人工复核成本，`C_fix` 为月固定成本，`N` 为预计月成功调用量。

- 单次基础模型成本：`model = (in·p_in + out·p_out) / 1e6`
- 含失败重试：`model_retry = model · (1 + r)`
- 单次含退款放大（退款也消耗成本）：`cost_var = (model_retry + M) · (1 + R) + P·F + P·T`
- 期望单次收入：`revenue = P · (1 − R)`
- 毛利率：`margin = (revenue − cost_var) / revenue`
- 月利润：`(revenue − cost_var) · N − C_fix`
- 盈亏平衡调用量：`ceil(C_fix / (revenue − cost_var))`；`revenue − cost_var ≤ 0` 时不存在平衡点
- 目标毛利率 `G` 最低售价：`P_min = cost_var / ((1−R) − F − T − G·(1−R))`；分母 ≤ 0 表示不可达

三价一致性：展示/注册/服务端价取两位小数后任意不同即 `RED`。

## 3. 三情景（确定性乘数，非随机）

| 情景 | 模型价 | 预计调用量 | 退款率 | 失败重试率 |
|---|---|---|---|---|
| base | ×1 | ×1 | ×1 | ×1 |
| pessimistic | ×1.5 | ×0.5 | ×2（封顶1） | ×2（封顶0.99） |
| optimistic | ×0.9 | ×1.5 | ×0.5 | ×0.5 |

可选字段缺失时，该乘数不生效且该字段仍在 `unknown_assumptions`，情景结果不因缺失项而"假装更赚"。

## 4. 输出结构（run.py 打印 JSON）

- `currency`、`input_echo`：原值回显，便于复核。
- `price_consistency`：GREEN/RED 与三个价。
- `unknown_assumptions`：缺失的可选成本字段清单。
- `completeness` / `profitability_claim_allowed`：有未知成本时分别为 INCOMPLETE / false。
- `scenarios.{base,pessimistic,optimistic}`：每个含 `multipliers`（回显乘数）与 `result`。
- `result` 关键字段：`model_cost`、`model_cost_with_retry`、`variable_cost_expected`、`revenue_expected`、`gross_margin_pct`、`monthly_profit`、`break_even.{monthly_calls,state}`、`minimum_server_price_to_target`、`state`、`estimate_scope`、`profitability_claim_allowed`。未知项存在时数字只是 `KNOWN_COSTS_ONLY_LOWER_BOUND`。
  - `state`: `ok` / `unprofitable_revenue_zero`（退款率使收入≤0）/ `unprofitable_contribution`（单次贡献≤0）。
  - `break_even.state`: `reachable` / `unreachable_contribution_not_positive` / `cannot_compute_calls_zero` / `no_fixed_cost`。
- `warnings`、`formula_note`、`note`。

## 5. 中文报告结构（由代理依据 stdout JSON 生成 Markdown）

1. 输入与来源（哪些数字用户提供、哪些是样例假设，分开标注）。
2. 三价核对结论（RED 则优先处理）。
3. 三情景表：模型成本 / 可变成本 / 收入 / 毛利率 / 月利润 / 盈亏平衡。
4. 敏感性一句话：退款与失败重试对毛利率的影响（对比 base 与悲观）。
5. `unknown_assumptions` 清单与"补齐哪个字段能消除最大不确定性"。
6. 建议（定价调整、补字段、先用真实客户验证），并声明不保证盈利。

## 6. 限制

- 单币种、单 SKU；不处理阶梯价、订阅与按次混合、汇率换算。
- 不读取密钥、不改平台价格、不下单、不发信。
- 不含税法定口径判断；税率是用户测算输入。
- 样例（references/sample.json）是合成数据，不是真实客户数据。
