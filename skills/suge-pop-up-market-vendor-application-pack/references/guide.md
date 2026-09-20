# 字段表与阅读步骤

本文件是 `suge-pop-up-market-vendor-application-pack` 的字段说明与输出阅读指南。所有规则都是确定性的：同一输入必然得到逐字节一致的输出。

两个随包样例：`@references/sample.json` 是**全部商品都已标注类别**的主样例（含 5 个场次与各类边界）；`@references/sample-unknown-category.json` 是**商品类别漏填**的边界样例，用来看清"未标注类别"如何把食品要求卡在未判定、并把场次挡在 `READY` 之外。

## 1. 输入字段表

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `as_of` | **是** | string | 基准时间，**必须带时区偏移**，如 `2026-09-19T10:00:00+08:00`。缺失时所有日期比较退化为"未校验"并给出提示。 |
| `vendor` | 建议 | object | 摊主资料：`vendor_ref`、`brand_name`、`category`、`contact_ref`、`city`、`intro`。`intro` 只作为草稿使用。 |
| `vendor_materials` | 是 | array | 摊主已有材料，每项 `material_id` / `type` / `label` / `status` / `expires_on`。`expires_on` 是**日期**（`YYYY-MM-DD`，或以其开头的 ISO 日期时间，只取日期部分）；格式不合法的日期会被标 `MATERIAL_EXPIRY_INVALID`，**绝不当作有效**。 |
| `products` | 建议 | array | 商品清单，每项 `product_id` / `name` / `category`（必须是 `FOOD` 或 `NON_FOOD`）/ `unit_price` / `currency`。漏填或写成其他值的类别**既不算食品也不算非食品**，单独计入 `unknown_category_product_count`。 |
| `equipment` | 建议 | array | 设备清单，每项 `equipment_id` / `label` / `power_watts`。 |
| `markets` | 是 | array | 要申请的场次，每项见下表。 |

`markets[]` 每项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `market_id` / `name` | string | 场次标识与名称。重复 `market_id` 会被登记。 |
| `timezone` | string | 活动时区标识（如 `Asia/Shanghai`），也接受 `±HH:MM` 偏移。无法离线解析的时区会让材料到期日的"是否过期"判定保持未定并追问，**不按 UTC 猜测**。 |
| `application_timezone` | string | 主办方声明的报名时区。与 `timezone` 不一致时标 `TIMEZONE_MISMATCH`。 |
| `application_deadline` | string | 报名截止时间，**必须带时区偏移**。无偏移会标 `DEADLINE_NOT_OFFSET_AWARE`。 |
| `event_start` / `event_end` | string | 活动起止时间，带时区偏移。 |
| `accepts_food` | bool/null | 是否接受食品摊位。为 `false` 且摊主有食品商品时给提示（不降级状态）。 |
| `booth_max_count` / `booth_requested_count` | int/null | 摊位上限与申请数量。申请数超过上限时标 `BOOTH_COUNT_EXCEEDED`。 |
| `booth_size` / `outdoor` | string/bool | 摊位尺寸与是否户外。 |
| `booth_power_watts_limit` | number/null | 摊位用电上限（瓦）。缺失或设备功率未知时用电判定为 `UNKNOWN`。 |
| `requirements` | array | 主办方材料要求，每项 `req_id` / `type` / `label` / `mandatory` / `applies_to`。`applies_to` 为 `FOOD` 时，仅在摊主**明确**有食品商品时适用；只要清单里存在未标注类别的商品，该要求判 `UNKNOWN` 而不是 `NOT_APPLICABLE`。 |
| `fees` | array | 费用，每项 `fee_id` / `label` / `amount` / `currency` / `refundable`。`amount` 必须是**非负数值**；缺失、非数值或为负数的费用只回显并标 `FEE_AMOUNT_INVALID`，不计入任何合计。 |
| `rules_text` | string | 主办方规则原文，按不可信文本处理。 |
| `notes` | string | 备注。 |

## 2. 时间与时区

- `as_of` 与各时间字段必须带时区偏移；不带偏移的时间会被**拒绝解析**并标记。
- `remaining_days` = `floor((deadline − as_of) / 86400)`，即按绝对时间计算后再向下取整。
- `deadline_state`：`EXPIRED`（<0）、`DUE_SOON`（≤7）、`OPEN`（>7）、`UNKNOWN`（无法解析）。
- 时区不一致会显式标记：报名时区与活动时区不同 → `TIMEZONE_MISMATCH`；截止时间与活动起点的 UTC 偏移不同 → `TIMEZONE_INCONSISTENT`。
- **材料到期日按"日历日"比较**：`expires_on` 是日期而不是时刻，比较对象是 `as_of` 在**场次声明时区**里的当地日历日。当地日期等于 `expires_on` 时视为**仍然有效**（含到期日当天）；只有 `expires_on` **早于**当地日历日才判 `EXPIRED`。声明时区无法离线解析时，判定保持未定（`MET_EXPIRY_UNKNOWN` + `MATERIAL_EXPIRY_TZ_UNKNOWN`）并追问，绝不假定 UTC。

## 3. 资格匹配（逐条要求）

对每条 `requirements`，按 `type` 在 `vendor_materials` 中查材料（同一类型有多条时，取**到期日最晚**的一条）：

| 状态 | 含义 |
|---|---|
| `MET` | 材料状态为 `AVAILABLE` 且到期日不早于 `as_of` 的当地日历日（含到期日当天） |
| `MET_EXPIRY_UNKNOWN` | 材料可用但未提供到期日，或声明时区无法解析而无法判定 |
| `NOT_APPLICABLE` | `applies_to=FOOD`，摊主**明确**没有食品商品（清单全部标注为 `NON_FOOD`，或清单为空） |
| `UNKNOWN` | `applies_to=FOOD`，但清单里存在未标注类别的商品（`CATEGORY_UNKNOWN`）：无法判断该要求是否适用，按未判定处理 |
| `NOT_AVAILABLE` | 材料存在但状态不是 `AVAILABLE`（如 `PENDING`） |
| `EXPIRED` | 到期日早于 `as_of` 的当地日历日 |
| `INVALID_EXPIRY_DATE` | `expires_on` 不是合法日期（如 `2026-9-3`、`not-a-date`），**无法判断**是否仍有效，按未满足处理 |
| `MISSING` | 没有任何该类型的材料 |

必填（`mandatory=true`）且状态不属于 `MET` / `MET_EXPIRY_UNKNOWN` / `NOT_APPLICABLE` 的，进入 `missing_mandatory`。因此"日期格式写错"的必填材料，以及"类别未标注导致无法判定"的食品要求，都会进入硬缺口，直到补齐后重新核对。

### 3.1 商品类别三桶

`products[].category` 被分成三个**互斥**的桶，绝不互相补集：

| 桶 | 计数字段 | 含义 |
|---|---|---|
| 食品 | `vendor_card.food_product_count` | 只有显式 `FOOD`（NFKC + 去空白 + 大写 + `-`→`_` 后等于 `FOOD`） |
| 非食品 | `vendor_card.non_food_product_count` | 只有显式 `NON_FOOD` |
| 未标注 | `vendor_card.unknown_category_product_count` / `unknown_category_product_ids` | 缺字段、空串、或其他任何值 |

只要未标注桶非空，就说明食品范围**无法判定**：该场次标 `PRODUCT_CATEGORY_UNKNOWN`，`applies_to=FOOD` 的要求判 `UNKNOWN`，场次状态至少 `INPUT_INCOMPLETE`，顶层不会是 `READY`。未标注类别**不会**被算作非食品（1.0.1 的错误行为），也**不会**把食品要求放行为"不适用"。只有未标注桶为空（清单全部标注，或清单为空）时，食品要求才可能判 `NOT_APPLICABLE`。可运行 `@references/sample-unknown-category.json` 观察这条规则：`MKT-U01` 的食品许可证判 `UNKNOWN` 并进入 `missing_mandatory`，连没有食品要求的 `MKT-U02` 也停在 `INPUT_INCOMPLETE`。

## 4. 用电核算

- `power_required_watts` = **仅**用户提供的 `equipment[].power_watts` 求和。
- 任一设备功率缺失，或 `booth_power_watts_limit` 缺失 → `power_state = UNKNOWN`（**不把未知功率当 0**）。
- 超过上限 → `EXCEEDED`；否则 `OK`。

## 5. 费用

- 只按币种分组输出 `fee_summary.by_currency`（合计、条数）与可退/不可退拆分。
- **跨币种不合并、不折算**：`cross_currency_total` 恒为 `null`。
- **无效金额不参与任何合计**：`amount` 缺失、非数值或为负数时，该条费用在 `markets[].fees` 中保留原值（`amount_raw`）便于改正，`amount` 为 `null`、`state` 为 `INVALID_AMOUNT`、`included_in_totals` 为 `false`，并计入 `fee_summary.invalid_fee_count` / `invalid_fees`，同时生成 `FEE_AMOUNT_INVALID` 待确认问题。没有币种的费用（`state = CURRENCY_MISSING`）单独回显但同样不计入任何币种桶。
- 每条费用都有 `state`：`COUNTED`（已计入）、`INVALID_AMOUNT`、`CURRENCY_MISSING`。

## 6. 场次状态

`DEADLINE_PASSED`（截止已过）> `NOT_ELIGIBLE`（必填材料未满足）> `BOOTH_REQUIREMENT_RISK`（用电超标或摊位超额）> `INPUT_INCOMPLETE`（截止时间未知、未提供要求、有材料的到期日格式非法，或商品清单存在未标注类别）> `REVIEW_REQUIRED` > `READY_TO_APPLY`。

顶层 `status`：任一场次为 `DEADLINE_PASSED` 或 `NOT_ELIGIBLE` → `BLOCKED`；全部 `READY_TO_APPLY` → `READY`；否则 `REVIEW_REQUIRED`。检测到疑似真实凭据时直接 `REJECTED`。

## 7. 输出阅读步骤

1. 看顶层 `status`。`REJECTED` 表示输入含疑似凭据（已拒绝且未回显）。
2. 看 `markets[].deadline_state` 与 `remaining_days`：先排除已截止的场次。
3. 看 `markets[].missing_mandatory`：这是"报了也过不了"的硬缺口。
4. 看 `markets[].eligibility`：逐条核对状态与证据。`INVALID_EXPIRY_DATE` 表示到期日格式写错、无法判断，**不是**"有效"；`UNKNOWN` + `CATEGORY_UNKNOWN` 表示商品类别未标注，食品要求**不能**当作"不适用"。
5. 看 `vendor_card.unknown_category_product_count`：大于 0 时先把 `products[].category` 补成 `FOOD` / `NON_FOOD` 再重跑，否则食品范围一直是未判定。
6. 看 `markets[].booth.power_state`：`UNKNOWN` 表示有设备功率没给，**不是 0**。
7. 看 `fee_summary`：**分币种**核对，确认押金是否可退；`invalid_fee_count > 0` 时先改正 `invalid_fees` 里的金额，再重跑。
8. 看 `clarification_questions`：按 `Q-01` 顺序**原样**发给主办方或自己确认。
9. 用 `submission_checklist` 与 `markdown_summary` 作为提交前检查表；**提交仍需人工完成**。

## 8. 不可信输入处理

- **提示注入只标记、不执行**：命中值在 `markdown_summary` 中替换为固定占位「已隐藏疑似提示注入文本」，结构化 JSON 保留原始值并在 `injection_flagged` 中给出精确路径。
- 检测规则是「覆盖动作 + 指令/规则/系统/提示」**同句共现**，单个动词或名词不触发。
- **Markdown 安全转义**：自由文本入简报前压掉控制字符与换行，并对 Markdown 元字符加反斜杠。
- **凭据拒绝**：字段名或值形似账号密码、令牌、私钥时直接 `REJECTED` 且不回显。

## 9. 明确不做的事

不代填或提交任何表单、不付款、不保证录取、不编造许可证/资质/销量/摊位号，不提供食品安全或法律结论。主办方规则缺失时保持 `UNKNOWN`，由用户补齐原文；商品类别未标注时同样保持 `UNKNOWN`，由用户补齐 `FOOD` / `NON_FOOD`，不替用户猜成非食品。
