# 成本压力应对情景包 — 字段表、判定规则与情景口径

本文件是 `scripts/run.py` 的唯一规则来源。脚本只读一个 JSON 文件，输出一个 JSON 文档。

一切数字都是对输入事实做的算术。**没有任何一行是建议、预测或推荐。**

## 1. 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | ISO 时间戳，**必须带时区偏移**（如 `2026-09-30T07:30:00+08:00`）。缺失或格式无效时整体 `INPUT_INCOMPLETE`。 |
| `business` | 否 | `{name, timezone}`，只用于简报抬头展示，不参与判定。 |
| `items` | 是 | 成本项数组。为空或缺失时整体 `INPUT_INCOMPLETE`。 |
| `scenarios` | 否 | 响应情景数组。为空时只输出逐项与月度口径，并加输入提示 `NO_SCENARIOS`。 |

## 2. 成本项字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `item_id` | 建议 | 成本项编号；缺失时输出用 `items[i]` 作为标识。同一编号出现多次 → `DUPLICATE_ITEM_ID`。 |
| `supplier` | 否 | 供应商名称，只用于展示。 |
| `unit_cost_before` | 是 | 变动前单位成本。缺失记 `NO_UNIT_COST_BEFORE`；不可解析记 `INVALID_UNIT_COST_BEFORE`；为负记 `NEGATIVE_COST`。 |
| `unit_cost_after` | 是 | 变动后单位成本。缺失记 `NO_UNIT_COST_AFTER`；不可解析记 `INVALID_UNIT_COST_AFTER`；为负记 `NEGATIVE_COST`。 |
| `currency` | 是 | 币种代码（如 `CNY`、`USD`，大小写不敏感）。缺失记 `UNKNOWN_CURRENCY`，**不做任何汇率换算**。 |
| `monthly_volume` | 建议 | 月用量。缺失记 `NO_MONTHLY_VOLUME`（只给单位口径）；不可解析记 `INVALID_MONTHLY_VOLUME`；为负记 `NEGATIVE_VOLUME`；为 0 只记 `ZERO_VOLUME` 提示。 |
| `current_price` | 建议 | 当前售价。缺失记 `NO_CURRENT_PRICE`（不给毛利情景）；不可解析记 `INVALID_CURRENT_PRICE`；为负记 `NEGATIVE_PRICE`；为 0 记 `DIVISION_BY_ZERO_PRICE`。 |
| `gross_margin_before` | 否 | 你登记的毛利率基线。`0–1` 视为比例，`>1` 视为百分数（如 `35` 即 35%）。仅在能与售价、单位成本对比时才核对。 |
| `effective_at` | 是 | 生效时间，带时区偏移。缺失记 `NO_EFFECTIVE_AT`；不可解析（含缺偏移）记 `INVALID_EFFECTIVE_AT`。 |
| `evidence_refs` | 否 | 证据引用数组，**只接受单一文件名**。一条都没提供时记提示 `NO_EVIDENCE_REF`（进 `evidence_gaps`）；提供了但被拒绝的引用只进 `refused_refs`，**不重复算作证据缺口**。 |
| `notes` | 否 | 自由文本备注。 |

## 3. 成本项判定顺序（固定）

| # | 条件 | 结果 |
|---:|---|---|
| 1 | 记录不是对象 | `INSUFFICIENT_EVIDENCE`（`INVALID_ITEM_RECORD`） |
| 2 | 同一 `item_id` 出现多次 | **`BLOCKED`**（`DUPLICATE_ITEM_ID`） |
| 3 | 单位成本或售价为负 / 月用量为负 | **`BLOCKED`**（`NEGATIVE_COST` / `NEGATIVE_PRICE` / `NEGATIVE_VOLUME`） |
| 4 | 存在必要事实缺失（见 §4） | `INSUFFICIENT_EVIDENCE` |
| 5 | 存在非必要事实缺失（见 §5） | `PARTIAL` |
| 6 | 其余 | `COMPUTED` |

阻塞代码按固定顺序输出：`INVALID_ITEM_RECORD` → `DUPLICATE_ITEM_ID` → `NEGATIVE_COST` → `NEGATIVE_VOLUME` → `NEGATIVE_PRICE`。

## 4. 必要事实缺失（`INSUFFICIENT_EVIDENCE`）

按固定顺序输出到 `unknowns`：

```
NO_UNIT_COST_BEFORE / INVALID_UNIT_COST_BEFORE /
NO_UNIT_COST_AFTER  / INVALID_UNIT_COST_AFTER  /
UNKNOWN_CURRENCY    / NO_EFFECTIVE_AT          / INVALID_EFFECTIVE_AT
```

这些事实缺一就无法给出单位增量或无法给金额标注币种，因此**不往下算**。

## 5. 非必要事实缺失（`PARTIAL`）

按固定顺序输出到 `soft_unknowns`：

| 代码 | 触发条件 | 影响 |
|---|---|---|
| `NO_MONTHLY_VOLUME` | 缺月用量 | 只给单位增量；不参与月度合计 |
| `INVALID_MONTHLY_VOLUME` | 月用量不可解析 | 同上 |
| `NO_CURRENT_PRICE` | 缺当前售价 | 不给毛利情景 |
| `INVALID_CURRENT_PRICE` | 售价不可解析 | 不给毛利情景 |
| `DIVISION_BY_ZERO_PRICE` | 基准为 0 而增量非 0（成本基准），或售价为 0 | 百分比变化**无定义**，不按 0 |
| `INVALID_MARGIN_BASELINE` | `gross_margin_before` 不是有效的比例或百分数 | 不做基线核对 |

## 6. 事实提示（`review_flags`，不影响结论）

按固定顺序输出：`ZERO_VOLUME` → `MARGIN_BASELINE_MISMATCH` → `NO_EVIDENCE_REF` → `EFFECTIVE_IN_FUTURE`。

`NO_EVIDENCE_REF` 只在**一条引用都没提供**时出现；提供了但被拒绝的引用已由 `refused_refs` 记录，不再重复计入证据缺口。

`effective_state` 单独给出：`EFFECTIVE_IN_FUTURE`（生效时间晚于 `as_of`）/ `EFFECTIVE_ALREADY` / `UNKNOWN`。**工具不推断生效日期，只比较你登记的时间。**

## 7. 计算口径

```
unit_delta        = unit_cost_after - unit_cost_before
direction         = UP / DOWN / FLAT
unit_delta_pct    = unit_delta / unit_cost_before * 100        （基准为 0 且增量非 0 时无定义）
monthly_cost_before = unit_cost_before * monthly_volume
monthly_cost_after  = unit_cost_after  * monthly_volume
monthly_delta       = unit_delta * monthly_volume
unit_margin_before  = current_price - unit_cost_before
unit_margin_after   = current_price - unit_cost_after
margin_before_pct   = unit_margin_before / current_price * 100 （售价为 0 时无定义）
margin_after_pct    = unit_margin_after  / current_price * 100
margin_delta_pp     = margin_after_pct - margin_before_pct
```

- 所有金额用 `Decimal`，**只在输出时**按 `ROUND_HALF_UP` 量化到两位小数；百分比两位小数加 `%`；百分点差输出两位小数字符串。
- `monthly_impact.by_currency` 只汇总 `monthly_delta` 可算的成本项；`complete` 为「没有缺月用量的成本项且至少有一条可算」。
- 毛利变化**未考虑**销量、税、退货、运费与其它成本。

## 8. 响应情景

固定类型词表：`absorb`（吸收成本）、`price_adjust`（调整价格）、`substitute_supplier`（替代供应商）、`reduce_volume`（减少用量）、`discontinue`（停售）。表外类型记 `UNKNOWN_SCENARIO_TYPE`，**工具不发明情景**。

情景字段：`scenario_id`、`type`、`applies_to`（成本项编号数组；缺失或为空表示全部）、`constraints`（可选），以及按类型所需的参数：

| 类型 | 必需参数 | 计算 |
|---|---|---|
| `absorb` | `absorb_ratio`（0–1） | 抵消金额 = 成本增量 × 比例 |
| `price_adjust` | `price_change_pct` | 新售价 = 当前售价 ×（1 + 幅度/100）；抵消金额 =（新售价 − 当前售价）× 月用量 |
| `substitute_supplier` | `new_unit_cost` | 情景月度成本 = 新单位成本 × 月用量；抵消金额 =（变动后成本 − 新成本）× 月用量 |
| `reduce_volume` | `volume_change_pct` | 新用量 = 月用量 ×（1 + 幅度/100）；情景月度成本 = 变动后成本 × 新用量；抵消金额 =（月用量 − 新用量）× 变动后成本 |
| `discontinue` | 无 | 情景月度成本 = 0；抵消金额 = 变动后成本 × 月用量 |

五个类型共用同一套可比字段，定义如下（`×` 表示逐成本项求和）：

```
baseline_monthly_cost = unit_cost_after × monthly_volume
cost_delta_vs_baseline = (unit_cost_after − unit_cost_before) × monthly_volume
mitigation_amount      = 按上表
residual_exposure      = cost_delta_vs_baseline − mitigation_amount
scenario_monthly_cost  = 按上表（absorb 与 price_adjust 不改变采购成本）
```

所有汇总**按币种分别进行**，不同币种**不可直接比较**。

### 约束（可选）

| 约束 | 触发条件 | 违反代码 |
|---|---|---|
| `max_price_change_pct` | 仅 `price_adjust`：调价幅度绝对值超过上限 | `MAX_PRICE_CHANGE` |
| `min_gross_margin_pct` | 仅 `price_adjust`：新售价下毛利率低于下限 | `MIN_GROSS_MARGIN` |
| `max_residual_exposure` | 任一币种残余敞口超过上限 | `MAX_RESIDUAL_EXPOSURE` |

其它违反代码：`SCENARIO_PRICE_NOT_POSITIVE`（新售价 ≤ 0）、`ABSORB_RATIO_OUT_OF_RANGE`（比例超出 0–1，按边界截断后仍报违规）、`NEW_COST_NEGATIVE`（替代成本为负）、`VOLUME_NEGATIVE`（新用量为负，按 0 计算）。

### 可行性（`feasibility`）

按以下优先级取第一个命中：

| 值 | 含义 |
|---|---|
| `INSUFFICIENT_EVIDENCE` | 情景本身不可用（表外类型、编号重复、参数缺失或非法、引用不存在的成本项）或没有任何可算的成本项 |
| `VIOLATES_CONSTRAINTS` | 存在违反代码 |
| `PARTIAL_INPUT` | 有成本项因缺输入被跳过（见 `skipped_items`） |
| `MEETS_CONSTRAINTS` | 全部可算且未违反约束（**不代表该情景更好**） |

情景未知代码按固定顺序输出：`UNKNOWN_SCENARIO_TYPE` → `DUPLICATE_SCENARIO_ID` → `SCENARIO_PARAM_MISSING` → `SCENARIO_PARAM_INVALID` → `UNKNOWN_ITEM_REFERENCE` → `ITEM_NOT_READY`。情景不可用时**不输出任何部分合计**。

`reduce_volume` 与 `discontinue` 恒带提示 `REVENUE_EFFECT_NOT_MODELLED`：**收入影响本工具不建模**。

## 9. 顶层结论

- 存在任一 `BLOCKED` 成本项 → `BLOCKED`
- 否则存在任一 `PARTIAL` / `INSUFFICIENT_EVIDENCE` 成本项，或任一情景可行性不是 `MEETS_CONSTRAINTS` → `GAPS_FOUND`
- 否则 → `READY`

另有 `REJECTED`（疑似凭据）与 `INPUT_INCOMPLETE`（缺 `as_of` 或成本项）两个前置状态。

## 10. 安全边界

1. **只读离线**：不联网、不写文件、不调用子进程、不访问 ERP / 财务 / 采购 / 供应商系统。
2. **不动作**：不改价、不下单、不采购、不联系供应商、不签合同、不删除原始材料。
3. **不给建议**：不排序、不推荐、不输出「应该调价 / 应该换供应商」一类结论，也不承诺任何节省或利润。
4. **未知保持未知**：任何缺失字段都不默认为 0、「没影响」或「已生效」。
5. **附件只留文件名**：`evidence_refs` 只接受单一文件名；含 `/`、`\`、`~`、盘符或链接的引用被拒绝，输出只保留安全化后的文件名（若可提取），**原引用不回显**。
6. **凭据拒绝**：字段名命中 `api_key` / `secret` / `password` / `token` 等，或字符串值形如 `sk-…`、`AKIA…`、`ghp_…`、私钥头、`xox…-…`、JWT，整体 `REJECTED` 且不回显。
7. **提示注入只标记**：动作词与目标词**同句共现**才记 `PROMPT_INJECTION`；进入 Markdown 时替换为固定占位「已隐藏疑似提示注入文本」，不执行、不回显。
8. **Markdown 转义**：所有自由文本先剥离控制字符、折叠空白，再对 Markdown 元字符加反斜杠。
9. **确定性**：成本项按 `item_id`、情景按 `scenario_id`、可比表按情景与币种、问题按生成顺序编号、状态汇总按 `ITEM_STATES` 固定顺序，同一输入逐字节一致。

## 11. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | 顶层结论 |
| `status_counts` | 四种成本项结论的计数 |
| `items[]` | 每个成本项的完整判定（含 `blockers` / `unknowns` / `soft_unknowns` / `review_flags` / `reasons` / `effective_state` / 数值字段的 `state` 与 `value`） |
| `cost_changes[]` | 逐项单位增量、增量百分比与月度增量 |
| `monthly_impact` | 按币种的月度影响、可算条数、缺用量清单与 `complete` |
| `margin_scenarios[]` | 当前售价下的毛利变化（含登记的与推算的毛利率基线） |
| `scenario_count` / `scenarios[]` | 情景明细（可行性、违反、未知、跳过清单、提示、逐币种合计） |
| `scenario_table[]` | 可比表：每个情景每个币种一行 |
| `evidence_gaps[]` | 无证据引用的成本项 |
| `unknowns_summary[]` | 未知项汇总（必要 + 非必要） |
| `duplicate_items[]` | 重复编号与出现位置 |
| `refused_refs[]` | 被拒绝的引用（只含路径与原因，不含原值） |
| `human_confirm_items[]` | 必须由人工确认的事项 |
| `clarification_questions[]` | 待确认问题（`Q-01` 起编号） |
| `injection_flagged[]` | 提示注入命中路径 |
| `input_warnings[]` | 输入层提示 |
| `scenario_type_vocabulary` | 情景类型固定词表 |
| `markdown_summary` | Markdown 决策准备板 |
| `disclaimer` | 免责声明 |

## 12. 不适用情况

- 需要自动调价、自动下单、自动采购或自动联系供应商的场景。
- 需要会计、税务、法律或合规结论的场景（账期、税率、合同条款由人工或专业人士确认）。
- 需要预测需求对价格的反应、承诺节省金额或利润的场景。
- 需要写入 ERP、财务或采购系统的场景。
- 事实完全缺失、只有一句「成本涨了怎么办」的场景。
