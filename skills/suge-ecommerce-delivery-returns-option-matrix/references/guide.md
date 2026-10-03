# 电商配送与退货选项矩阵 — 字段表与判定规则

本文件是 `scripts/run.py` 的唯一规则来源。脚本只读一个 JSON 文件，输出一个 JSON 文档。

## 1. 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | ISO 时间戳，**必须带时区偏移**（如 `2026-10-01T19:05:00+08:00`）。缺失或格式无效时整体 `INPUT_INCOMPLETE`。 |
| `store` | 是 | `{name, default_currency, sales_channels}`。缺 `default_currency` 时整体 `INPUT_INCOMPLETE`（币种冲突判定需要基准币种）。 |
| `regions[]` | 是 | 区域数组。为空或缺失时整体 `INPUT_INCOMPLETE`。 |
| `delivery_options[]` | 否 | 配送选项数组。 |
| `return_options[]` | 否 | 退货选项数组。 |
| `promises[]` | 否 | 用户在不同页面写下的配送 / 退货承诺。 |
| `requirements[]` | 否 | **用户自己提供的目标要求**；本工具不内置任何行业默认。 |

顶层其它未知字段一律忽略，不参与判定。

## 2. 区域字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `region_id` | 是 | 区域编号；缺失的记录被跳过。 |
| `country_or_area` | 建议 | 国家 / 地区描述。 |
| `postal_scope` | 建议 | 邮编范围描述。 |
| `channels[]` | 是 | 该区域的销售渠道。为空时记输入提示 `REGION_WITHOUT_CHANNELS:<id>`，该区域不生成矩阵单元格。 |
| `customer_segments[]` | 否 | 客群描述。 |

矩阵单元格 = 每个区域 × 该区域声明的每个渠道。

## 3. 配送选项字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `option_id` | 建议 | 选项编号；缺失时输出用 `delivery_options[i]` 作为标识。 |
| `regions[]` | 是 | 适用区域编号。为空记 `MISSING_REGION_SCOPE`；引用了不存在区域记 `UNKNOWN_REGION`。 |
| `channels[]` | 否 | 适用渠道；为空表示该区域的**全部渠道**。写了该区域未声明的渠道记 `CHANNEL_MISMATCH`。 |
| `provider` | 是 | 承运商 / 服务商。缺失记 `MISSING_PROVIDER`。 |
| `service_level` | 否 | 服务等级，仅展示。 |
| `fee` | 是 | 运费（`Decimal`）。**缺失记 `MISSING_FEE`，与 `0` 明确区分**；负数记 `NEGATIVE_FEE`。 |
| `currency` | 是 | 币种。有费用无币种记 `MISSING_CURRENCY`；与 `store.default_currency` 不一致记 `CURRENCY_CONFLICT`（**不换算**）。 |
| `min_days` / `max_days` | 是 | 时效区间（天）。有任一缺失记 `MISSING_TRANSIT_TIME`；`min_days > max_days` 记 `INVERTED_TIME_WINDOW`；负数记 `NEGATIVE_TRANSIT_DAYS`。 |
| `cutoff_time` | 否 | 截单时间，格式 `HH:MM`；格式错误记 `INVALID_CUTOFF_TIME`。 |
| `tracking` | 是 | 三态布尔：是否提供追踪。非布尔记 `UNKNOWN_TRACKING`。 |
| `pickup_type` | 是 | 自提方式，必须来自固定词表（见 §7）。表外值记 `INVALID_PICKUP_TYPE`。 |
| `evidence_ref` / `evidence_refs` | 是 | 证据引用，**只接受单一文件名**（支持字符串或数组）。一条有效引用都没有记 `NO_EVIDENCE_REF`。 |
| `effective_from` / `effective_to` | 否 | 生效窗口，带时区偏移。不可解析记 `INVALID_EFFECTIVE_TIME`；`from > to` 记 `INVALID_EFFECTIVE_WINDOW`；`to < as_of` 记 `OPTION_EXPIRED`；`from > as_of` 记 `OPTION_NOT_YET_EFFECTIVE`；两者都不填只加提示 `NO_EFFECTIVE_WINDOW`（视为无有效期上界）。 |
| `notes` | 否 | 自由文本备注。 |

## 4. 退货选项字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `return_id` | 建议 | 退货编号。 |
| `regions[]` / `channels[]` | 是 / 否 | 同配送选项。 |
| `window_days` | 是 | 退货窗口天数。缺失记 `MISSING_RETURN_WINDOW`；负数记 `NEGATIVE_RETURN_WINDOW`。 |
| `fee_payer` | 是 | 退货运费承担方，必须来自固定词表（见 §7）。缺失记 `UNKNOWN_FEE_PAYER`；表外值记 `UNKNOWN_FEE_PAYER`（两者是不同事实，缺失不会被当成商家承担）。 |
| `dropoff_type` | 是 | 退货方式，必须来自固定词表（见 §7）。表外值记 `INVALID_DROP_OFF_TYPE`。 |
| `provider` | 是 | 服务商。缺失记 `MISSING_PROVIDER`。 |
| `condition_scope` | 否 | 退货条件描述。 |
| `evidence_ref` / `evidence_refs` | 是 | 同配送选项。 |
| `effective_from` / `effective_to` | 否 | 同配送选项，动作码为 `OPTION_EXPIRED` / `OPTION_NOT_YET_EFFECTIVE`。 |

## 5. 承诺字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `promise_id` | 建议 | 承诺编号。 |
| `surface` | 建议 | 出现位置：`checkout` / `product` / `faq` / `support`（结账页 / 商品页 / FAQ / 客服稿）。 |
| `region_id` | 是 | 承诺对应的区域；引用不存在区域记 `PROMISE_UNKNOWN_REGION`。 |
| `text` | 是 | 用户提供的原文，原样登记（进入简报前转义；命中注入替换为占位）。 |
| `stated_min_days` / `stated_max_days` | 否 | 承诺的时效区间。 |
| `stated_fee` | 否 | 承诺的运费。 |
| `stated_currency` | 否 | 承诺的币种。 |
| `stated_return_window_days` | 否 | 承诺的退货窗天数。 |

**冲突判定（只在该区域的结构化选项上比对）**：

- `PROMISE_TIME_CONFLICT`：给出了时效承诺，且该区域**没有任何**已知时效的配送选项满足承诺区间 `[pmin, pmax]`（要求 `option.max_days >= pmin` 且 `option.min_days <= pmax`）。
- `PROMISE_FEE_CONFLICT`：给出了运费承诺，且该区域**所有**已知运费的配送选项费用都**高于**承诺值。
- `PROMISE_CURRENCY_CONFLICT`：承诺币种与店铺默认币种不一致（不换算）。
- `PROMISE_RETURN_WINDOW_CONFLICT`：给出了退货窗承诺，且该区域**所有**已知退货窗都**短于**承诺天数。

无数据可比时不下冲突结论（标 `CONSISTENT`，不臆测）。

## 6. 目标要求字段（`requirements[]`）

**只比对用户显式提供的要求；未提供时缺口表为空，且明确声明不据此判断是否满足客户期望。**

| 字段 | 说明 |
|---|---|
| `requirement_id` | 要求编号。 |
| `region_id` / `channel` | 作用范围；`region_id` 不存在记 `REQ_UNKNOWN_REGION`。 |
| `max_days` | 要求的最大时效。无已知时效记 `REQ_DELIVERY_TIME_UNVERIFIABLE`；所有选项都超时记 `REQ_DELIVERY_TIME_NOT_MET`。 |
| `max_fee` | 要求的最大运费。无已知费用记 `REQ_DELIVERY_FEE_UNVERIFIABLE`；最低运费仍超记 `REQ_DELIVERY_FEE_NOT_MET`。 |
| `min_return_window_days` | 要求的最小退货窗。无已知退货窗记 `REQ_RETURN_WINDOW_UNVERIFIABLE`；所有退货窗都短记 `REQ_RETURN_WINDOW_NOT_MET`。 |
| `currency` | 要求币种与店铺默认币种不一致记 `REQ_CURRENCY_MISMATCH`（不换算）。 |

## 7. 固定词表

大小写不敏感；表外值按上表记录：

```
配送自提方式 pickup_type  ：door / pickup_point / store_pickup   （上门 / 网点 / 自提）
退货方式       dropoff_type：door_pickup / dropoff_point / mail_back
退货运费承担   fee_payer   ：seller / buyer / shared
承诺页面       surface     ：checkout / product / faq / support
```

## 8. 判定顺序（固定）

**配送选项 / 退货选项** 各自自上而下取第一个命中的分支：

| # | 条件 | 结果 |
|---:|---|---|
| 1 | 记录不是对象 | **`BLOCKED`**（`INVALID_DELIVERY_RECORD` / `INVALID_RETURN_RECORD`） |
| 2 | 任一硬矛盾（见 §9） | **`BLOCKED`** |
| 3 | 存在任一缺失事实 | **`INSUFFICIENT_EVIDENCE`** |
| 4 | 存在任一失效 / 未生效 | **`ACTION_NEEDED`** |
| 5 | 其余 | **`READY`** |

**区域 × 渠道单元格**：

| # | 条件 | 结果 |
|---:|---|---|
| 1 | 该单元格有 `BLOCKED` 的配送或退货选项，或该区域存在承诺冲突 | **`BLOCKED`** |
| 2 | 该单元格没有任何配送选项 | **`INSUFFICIENT_EVIDENCE`**（`NO_DELIVERY_COVERAGE`） |
| 3 | 该单元格没有任何退货选项 | **`INSUFFICIENT_EVIDENCE`**（`NO_RETURN_PATH`） |
| 4 | 该单元格有 `INSUFFICIENT_EVIDENCE` 的选项 | **`INSUFFICIENT_EVIDENCE`** |
| 5 | 该单元格有 `ACTION_NEEDED` 的选项 | **`ACTION_NEEDED`** |
| 6 | 其余 | **`READY`** |

## 9. 硬矛盾（`BLOCKED`）

配送：`INVALID_DELIVERY_RECORD`、`MISSING_REGION_SCOPE`、`UNKNOWN_REGION`、`CHANNEL_MISMATCH`、`INVALID_PICKUP_TYPE`、`INVALID_CUTOFF_TIME`、`INVERTED_TIME_WINDOW`、`NEGATIVE_TRANSIT_DAYS`、`NEGATIVE_FEE`、`CURRENCY_CONFLICT`、`INVALID_EFFECTIVE_TIME`、`INVALID_EFFECTIVE_WINDOW`、`DUPLICATE_OPTION_ID`。

退货：`INVALID_RETURN_RECORD`、`MISSING_REGION_SCOPE`、`UNKNOWN_REGION`、`CHANNEL_MISMATCH`、`INVALID_DROP_OFF_TYPE`、`NEGATIVE_RETURN_WINDOW`、`INVALID_EFFECTIVE_TIME`、`INVALID_EFFECTIVE_WINDOW`、`DUPLICATE_RETURN_ID`。

承诺：`PROMISE_UNKNOWN_REGION`、`PROMISE_CURRENCY_CONFLICT`、`PROMISE_TIME_CONFLICT`、`PROMISE_FEE_CONFLICT`、`PROMISE_RETURN_WINDOW_CONFLICT`。

**重复编号会让所有同号记录一并不可用**（不是保留一条、丢弃另一条）。

## 10. 顶层结论

- 存在凭据形态内容 → `REJECTED`（不回显）
- 缺 `as_of` / `store.default_currency` / 有效区域 → `INPUT_INCOMPLETE`
- 存在任一硬矛盾（选项、单元格或承诺冲突） → `BLOCKED`
- 否则存在任一 `ACTION_NEEDED` / `INSUFFICIENT_EVIDENCE` 单元格，或存在要求缺口 → `GAPS_FOUND`
- 否则 → `READY`

## 11. 金额与时间口径

- `fee`、`window_days`、`min_days`、`max_days` 使用 `Decimal`；费用输出两位小数 `ROUND_HALF_UP`，天数为整数或两位小数。
- **币种不换算、不跨币种合计**；与默认币种不一致只报告为冲突。
- 时间必须是带偏移的 ISO 8601（`Z` 或 `±HH:MM`）；不带偏移的本地时间视为无效。
- `as_of` 用绝对时刻比较；时区偏移只在解析时使用。

## 12. 安全边界

1. **只读离线**：不联网、不写文件、不调用子进程、不访问承运商 / 平台 / 店铺后台。
2. **不自动执行**：不选择承运商、不改结账页或商品页、不生成面单、不批准退款、不建议免费配送、不承诺销量。
3. **不换算币种**：币种冲突只报告，不折算、不跨币种合计。
4. **未知保持未知**：任何缺失字段都不默认为 0、`seller` 或「无影响」；**0 费用与缺失费用是两种事实**。
5. **附件只留文件名**：`evidence_ref` / `evidence_refs` 只接受单一文件名；含 `/`、`\`、`~`、盘符或链接的引用被拒绝，输出只保留安全化后的文件名（若可提取），**原引用不回显**。
6. **凭据拒绝**：字段名命中 `api_key` / `secret` / `password` / `token` 等，或字符串值形如 `sk-…`、`AKIA…`、`ghp_…`、私钥头、`xox…-…`、JWT，整体 `REJECTED` 且不回显。
7. **提示注入只标记**：动作词与目标词**同句共现**才记 `PROMPT_INJECTION`；进入 Markdown 时替换为固定占位「已隐藏疑似提示注入文本」，不执行、不回显。
8. **Markdown 转义**：所有自由文本先剥离控制字符、折叠空白，再对 Markdown 元字符加反斜杠。
9. **确定性**：选项按编号、单元格按 `(region_id, channel)`、问题按生成顺序、状态汇总按固定顺序，同一输入逐字节一致。

## 13. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | 顶层结论 |
| `status_counts` | 四种单元格结论的计数 |
| `matrix[]` | 区域 × 渠道单元格：适用配送 / 退货选项、结论、findings |
| `delivery_options[]` / `return_options[]` | 每个选项的完整判定（`state` / `blockers` / `unknowns` / `review_flags` / `reasons`） |
| `promise_checks[]` | 每条承诺的冲突判定 |
| `no_delivery_coverage[]` / `no_return_path[]` / `coverage_gaps[]` | 覆盖缺口 |
| `currency_conflicts[]` | 币种不一致 |
| `inverted_time_windows[]` | 时效倒置 |
| `inactive_options[]` | 失效 / 未生效选项 |
| `evidence_gaps[]` | 缺证据选项 |
| `promise_conflicts[]` | 承诺与结构化事实不一致 |
| `duplicate_ids[]` | 重复编号 |
| `requirement_gaps[]` / `requirements_provided` / `requirement_note` | 目标要求缺口与口径说明 |
| `responsibility_fields[]` | 需要指定责任人的字段 |
| `human_checklist[]` | 上线前人工检查表 |
| `clarification_questions[]` | 待确认问题（`Q-01` 起编号） |
| `human_confirm_items[]` | 必须由人工确认的事项 |
| `refused_refs[]` | 被拒绝的引用（只含路径与原因，不含原值） |
| `injection_flagged[]` | 提示注入命中路径 |
| `input_warnings[]` | 输入层提示 |
| `no_automation_declaration` / `disclaimer` | 不可自动化声明与免责 |
| `markdown_summary` | Markdown 矩阵与检查表 |

## 14. 不适用情况

- 需要自动选择承运商、自动改价、自动改结账页、自动生成面单、自动批准退款的场景。
- 需要实时承运商费率 / 时效查询或换算币种的场景。
- 需要输出「包邮更好」「这样能提升销量」一类建议或效果承诺的场景。
- 事实完全缺失、只有一句「帮我配好配送」的场景。
