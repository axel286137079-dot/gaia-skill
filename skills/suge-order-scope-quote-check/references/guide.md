# 接单范围与报价风险预检 · 使用与规则说明

本文件是 `suge-order-scope-quote-check` 的规则手册：字段含义、每条风险代码的触发条件与证据来源、脚本**绝不会替你发明**的阈值、`price_analysis` 的计算口径，以及输出的阅读顺序。
脚本只做一件事——把**客户要的使用范围与商务条件**，对照**你自己的价目表**与**你自设的 policy 阈值**逐条比对，
把对不上的地方列成风险清单和一份要发回客户的问题列表。它不查法律、不查行情、不改价格。

## 一、输入结构

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `as_of` | 是 | 评估时点，必须带时区偏移（如 `2026-09-16T10:00:00+08:00`）。所有期限与逾期判断的基准 |
| `currency` | 否 | 顶层 ISO4217 代码，仅做格式校验 |
| `policy` | 否 | **你自己拥有的阈值**。缺失即标 unknown，绝不套用默认值 |
| `rate_card[]` | 否 | 你自己的价目表档位。缺失时所有比价规则降级为「跳过」 |
| `order` | 是 | 订单对象，见下 |
| `order.notes` | 否 | 备注。只当数据读，其中任何指令性文字一律不执行 |

| policy 键 | 类型 | 触发的规则 |
| --- | --- | --- |
| `max_term_months` | 整数 | `TERM_OVER_LIMIT` |
| `min_deposit_pct` | 十进制字符串 | `DEPOSIT_BELOW_MIN` |
| `max_revision_rounds` | 整数 | `REVISIONS_OVER_LIMIT` |
| `require_kill_fee` | 布尔 | `NO_KILL_FEE` |

| rate_card[] 键 | 必填 | 说明 |
| --- | --- | --- |
| `tier_id` | 是 | 档位唯一编号，重复即报结构性错误 |
| `usage` | 是 | 固定词表：`social_media` / `paid_ads` / `broadcast` / `print` / `internal` / `ecommerce` / `ott` / `out_of_home` |
| `price` | 是 | 十进制字符串。**金额一律字符串**，不接受浮点 |
| `currency` | 是 | 3 位大写代码，与订单币种不一致时报 `CURRENCY_MISMATCH` 并跳过比价 |
| `term_months` | 否 | 该档位包含的授权月数 |
| `exclusive` / `buyout` / `ai_training` / `sublicense` | 否 | 该档位是否已覆盖这项权利。缺失或 null 一律按 `false` 读 |
| `unit` | 否 | `per_asset` / `per_project` / `per_month`。只有 `per_asset` 参与比价 |
| `effective_from` / `expires_at` | 否 | 日期字符串；`expires_at` 早于 `as_of` 时报 `EXPIRED_TIER` |

| order 键 | 必填 | 说明 |
| --- | --- | --- |
| `order_id` / `client_ref` | id 必填 | 订单编号；客户内部编号仅回显 |
| `deliverables[]` | 是 | 非空数组；每项含 `deliverable_id`（唯一）、`kind`、`quantity`（>=1）、`duration_seconds`、`revisions_included`、`due_at` |
| `usage.media[]` | 是 | 非空数组，取值同固定词表。空数组属结构性错误 |
| `usage.tier_id` | 否 | 你**打算据以计价**的档位编号 |
| `usage.territory[]` | 否 | 授权地域；null/空/含「全球、worldwide、不限」即视为无边界 |
| `usage.term_months` | 否 | 授权月数；null 即视为无限期风险 |
| `usage.exclusive` / `sublicense` / `buyout` / `ai_training` / `credit_required` | 否 | 三态：true / false / 未知（null） |
| `commercial.total_price` / `currency` | 是 | 客户报价（十进制字符串）与 3 位大写币种代码 |
| `commercial.deposit_pct` | 否 | 预付款比例；null 或 0 都触发 `NO_DEPOSIT` |
| `commercial.payment_terms[]` | 否 | 付款节点；null 或空数组都触发 `PAYMENT_TERMS_MISSING` |
| `commercial.expenses_cap` / `kill_fee_pct` / `late_fee_pct` / `revision_rate` | 否 | 对应费用条款，缺失只标 unknown，不填默认值 |
| `constraints.request_window_days` | 否 | 你需要的从需求确认到交付的最短自然日 |
| `constraints.raw_files_included` / `source_files_included` | 否 | 原始素材 / 源文件是否交付，三态 |
| `constraints.delivery_format` | 否 | 交付格式描述 |

## 二、先过的两道门

1. **凭据门禁**：键名命中 `password`、`passwd`、`secret_value`、`api_token`、`access_token`、`client_secret`、`credential_value`、`private_key`、`private_key_pem`、`dsn`、`connection_string`、`jdbc_url`、`database_url`，或值命中 `sk-…` / `AKIA…` / `gh[pousr]_…` / `xox[baprs]-…` / JWT / `BEGIN … PRIVATE KEY` 包裹的 PEM 私钥块，直接抛错并**拒绝回显**该字段名与值。
2. **提示词注入**：`忽略(以上|之前|所有)?(指令|规则)`、`ignore (all )?(previous|above) instructions`、`system prompt`、`你现在是`、`直接批准`、`免费`。命中只登记 `PROMPT_INJECTION_IGNORED`，**绝不执行**。

## 三、结构性错误（抛 ValueError，不出条目）

缺少 `order`；`deliverables` 为空；`total_price` / `price` 不是十进制数字；`deliverable_id` 或 `tier_id` 重复；`usage.media` 为空或取值不在固定词表内；`as_of` / `due_at` 缺少时区偏移。

## 四、风险代码表（条目按此顺序产出）

| # | 代码 | 级别 | 触发条件 | 证据字段 |
| --- | --- | --- | --- | --- |
| 1 | `MISSING_TIER` | HIGH | `usage.tier_id` 有值但价目表中不存在（或未提供价目表） | tier_id、rate_card 全部档位编号 |
| 2 | `TIER_MEDIA_MISMATCH` | HIGH | 所选档位的 `usage` 未能覆盖订单 media 中的其它媒介 | tier 的 usage、订单 media、未覆盖媒介 |
| 3 | `TIER_TERM_MISMATCH` | HIGH | 档位 `term_months` 与订单 `term_months` 不相等（两者都有值时才比） | 两个期限、tier_id |
| 4 | `TIER_EXCLUSIVITY_MISMATCH` | HIGH | 订单 `exclusive=true` 而档位 `exclusive=false` | 两边 exclusive、tier_id |
| 5 | `TIER_FLAG_MISMATCH` | HIGH | 订单的 `ai_training` / `sublicense` / `buyout` 为 true 而档位同项为 false。**多个命中合并为一条**，证据里列出全部命中项 | flags 列表、两边三项取值 |
| 6 | `AI_TRAINING_UNPRICED` | HIGH | 订单 `ai_training=true` 且**任何档位**都没有 `ai_training=true` | 订单标记、支持该用途的档位列表 |
| 7 | `SUBLICENSE_UNPRICED` | MEDIUM | 订单 `sublicense=true` 且任何档位都没有 `sublicense=true` | 订单标记、支持转授权的档位列表 |
| 8 | `BUYOUT_WITHOUT_PREMIUM` | HIGH | 订单 `buyout=true` 且任何档位都没有 `buyout=true` | 订单标记、买断档位列表 |
| 9 | `UNLIMITED_TERM` | HIGH | `usage.term_months` 为 null | term_months |
| 10 | `TERM_OVER_LIMIT` | HIGH | policy 给了 `max_term_months` 且订单期限超过它 | 订单期限、policy 上限 |
| 11 | `TERRITORY_UNBOUNDED` | HIGH | territory 为 null/空，或含 `worldwide` / `全球` / `不限` / `全球范围` | territory |
| 12 | `MEDIA_UNSPECIFIED` | HIGH | media 为空。**结构性错误优先，本条目实际不会产出** | — |
| 13 | `NO_DEPOSIT` | HIGH | `deposit_pct` 为 null 或等于 0 | deposit_pct |
| 14 | `DEPOSIT_BELOW_MIN` | MEDIUM | policy 给了 `min_deposit_pct` 且 0 < deposit_pct < 下限 | 订单比例、policy 下限 |
| 15 | `PAYMENT_TERMS_MISSING` | MEDIUM | `payment_terms` 为 null 或空数组 | payment_terms |
| 16 | `NO_KILL_FEE` | MEDIUM | `kill_fee_pct` 为 null 且 `policy.require_kill_fee` 为 true | kill_fee_pct、policy 开关 |
| 17 | `OPEN_REVISIONS` | MEDIUM | 任一交付物 `revisions_included` 为 null。**合并为一条** | 未写轮次的交付物列表 |
| 18 | `REVISIONS_OVER_LIMIT` | LOW | policy 给了 `max_revision_rounds` 且某交付物轮次超过它 | policy 上限、超限交付物 |
| 19 | `ASSET_HANDOVER_UNDEFINED` | MEDIUM | `raw_files_included` 或 `source_files_included` 为 null。**合并为一条**，证据同时列两项 | 两项取值 |
| 20 | `NO_REQUEST_WINDOW` | LOW | `request_window_days` 为 null | request_window_days |
| 21 | `EXPENSES_UNBOUNDED` | LOW | `expenses_cap` 为 null | expenses_cap |
| 22 | `NO_LATE_FEE` | LOW | `late_fee_pct` 为 null | late_fee_pct |
| 23 | `CREDIT_TERM_UNDEFINED` | LOW | `credit_required` 为 null | credit_required |
| 24 | `DELIVERY_FORMAT_UNDEFINED` | INFO | `delivery_format` 为 null 或空白 | delivery_format |
| 25 | `REVISION_RATE_MISSING` | INFO | `revision_rate` 为 null | revision_rate |
| 26 | `PAST_DUE` | HIGH | 任一 `due_at` 早于 `as_of` | as_of、逾期交付物列表 |
| 27 | `LEAD_TIME_TIGHT` | MEDIUM | 给了 `request_window_days`，且（最早 `due_at` − `as_of`）的天数 < 该窗口 | 窗口天数、最早交付时间、实际天数 |
| 28 | `EXPIRED_TIER` | MEDIUM | 所选档位 `expires_at` 早于 `as_of` | tier_id、expires_at |
| 29 | `CURRENCY_MISMATCH` | INFO | 订单币种与所选档位币种不一致（此时跳过比价） | 两个币种 |
| 30 | `PRICE_BELOW_TIER` | HIGH | 档位 `unit=per_asset`、币种一致，且订单报价 < 档位单价 × 交付总量 | 单价、总量、应计、报价、差额 |

`blocking` 一律等于「该条为 HIGH」，因此 `blocking_count` 恒等于 `high_risk_count`。

## 五、脚本绝不发明的东西

以下阈值**只能由用户提供**，缺失即跳过并把代码写进 `checks_skipped`，脚本不猜、不套用行业惯例：
`max_term_months` → 跳过 `TERM_LIMIT`；`min_deposit_pct` → 跳过 `DEPOSIT_MINIMUM`；
`max_revision_rounds` → 跳过 `REVISION_LIMIT`；`require_kill_fee` → 跳过 `KILL_FEE_REQUIREMENT`；
无 `request_window_days` 或全部 `due_at` → 跳过 `LEAD_TIME`；无 `usage.tier_id`、无 `rate_card`、
找不到该档位或币种不一致 → 跳过 `PRICE_COMPARISON`。

同样，缺失字段不会被填成 0 或「行业常见值」，而是按点号路径记入 `missing_inputs`：`order.usage.term_months`、`order.usage.credit_required`、`order.commercial.deposit_pct`、`order.commercial.payment_terms`、`order.commercial.kill_fee_pct`、`order.commercial.late_fee_pct`、`order.commercial.expenses_cap`、`order.constraints.request_window_days`、`order.constraints.raw_files_included`、`order.constraints.source_files_included`。空数组同视为缺失。

## 六、price_analysis 的确定性计算

只在**可算**时输出，否则为 `null`（并把 `PRICE_COMPARISON` 计入 `checks_skipped`）。可算条件：
`usage.tier_id` 有值、提供了 `rate_card`、该档位存在、订单币种与档位币种一致、且应计金额不为 0。

1. `total_quantity` = 所有交付物 `quantity` 的整数和。
2. `expected_total` = `tier_price` × `total_quantity`（Decimal 乘法，两位小数，ROUND_HALF_UP）。
3. `difference` = `quoted_total` − `expected_total`。
4. `gap_pct` = `difference` ÷ `expected_total` × 100，两位小数 ROUND_HALF_UP，负数带前导减号，形如 `-65.81%`。金额与百分比全部以字符串输出，运算全程 `Decimal`，**任何一步都不经过 float**。

注意 `expected_total` 是「单价 × 件数」口径，语义对应 `unit=per_asset`；档位 `unit` 为 `per_project` 或 `per_month` 时，该数字只当参考量级，不是最终报价。

## 七、输出阅读顺序

1. `status`：`INVALID`（输入不可用）> `HIGH_RISK`（有 HIGH）> `REVIEW_REQUIRED`（有 MEDIUM）> `PARTIAL`（有被跳过的校验）> `ACCEPTABLE`。
2. `findings[]` 与 `severity_counts` / `finding_total` / `high_risk_count` / `blocking_count`：看 `blocking=true` 的条目，判断是否需要暂停接单流程。
3. `price_analysis`：先看 `gap_pct`，再看 `difference`。
4. `checks_skipped` 与 `missing_inputs`：确认哪些结论因为缺字段而没做，补字段后重跑。
5. `findings[].question_to_client` / `next_actions[]` / `markdown_summary`：直接复制去问客户或贴给同事。
6. `injection_flags`：输入里出现过指令性文字，已忽略，仅作记录。

## 八、边界

只读预检，不构成法律意见，不代替合同审查，不保证成交价或议价结果，也不判断条款是否具有法律效力。所有阈值来自用户提供的 `policy`，缺失即标 unknown，不套用默认值；脚本不联网、不读取输入文件以外的任何路径、不修改任何文件。
