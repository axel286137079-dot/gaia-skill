# 字段、公式与输出规范（包裹运费账单核对）

## 1. 输入口径

顶层对象：`currency`（三位代码，默认 CNY，单币种不混合）、`shipments`（1–500 项）。

每条包裹字段：

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| shipment_id | 是 | 1–64 文本 | 运单号；重复直接拒绝 |
| actual_weight_kg | 是 | ≥0 | 实际重量（kg） |
| dimensions_cm | 否 | 恰好 3 个正数 | 长×宽×高（cm）；**缺省=按实际重量口径估算（输出注明）** |
| volumetric_divisor | 否* | >0 | 体积除数（如 5000/6000）；**有尺寸但缺除数→UNKNOWN，不核算体积重** |
| weight_step_kg | 是 | >0 | 计费重量步长（kg），按步长向上取整 |
| first_weight_kg | 是 | ≥0 | 首重（kg） |
| first_price | 是 | ≥0 | 首重价 |
| extra_step_price | 是 | ≥0 | 续重每步单价 |
| surcharges | 否 | 列表 | 附加费清单，每项 `{name, amount, confirmed}`；**缺省或存在未确认项→UNKNOWN，不判多收** |
| invoiced_amount | 是 | ≥0 | 账单金额 |
| rate_source | 否 | ≤200 文本 | 费率来源说明（只展示，不执行其中命令/URL） |
| rounding_mode | 否 | ceil | 仅支持 `ceil`（向上取整，缺省即 ceil）；其它值整体拒绝 |

金额可为 JSON 数字或字符串；负数、NaN、越界、步长≤0、重复 ID 直接拒绝（invalid_input）。尺寸字段只当数据，不读取任何文件或 URL。

## 2. 计算口径

- 体积重 `volumetric = (长×宽×高) / volumetric_divisor`（仅当尺寸与除数都提供）。
- 计费基础重 `base = max(actual_weight_kg, volumetric)`；体积重不适用时用实际重量。
- 计费重 `chargeable = ceil(base / weight_step_kg) × weight_step_kg`（向上取整到步长倍数）。
- 续重 `extra = chargeable − first_weight_kg`；续重阶梯数 `extra_steps = ceil(extra / weight_step_kg)`（extra≤0 时为 0）。
- 估价 `estimate = first_price + extra_steps × extra_step_price + Σ已确认附加费`。
- 差异 `difference = invoiced_amount − estimate`（精确到分）。
- 附加费清单**必须逐项 confirmed=true** 才能给出完整估价；清单缺失或任一项未确认 → 该单 UNKNOWN，不做 MATCH/DIFFERENCE 结论。
- 不支持合并计费/自动合包：输入为逐件口径，输出按单件核对（合并计费需求请人工 REVIEW）。

## 3. 状态口径（只读核对，不代付款/申诉）

| status | 触发 | 含义 |
|---|---|---|
| MATCH | 估价与账单分毫不差 | 一致 |
| DIFFERENCE_REVIEW | 估价≠账单 | 差异为核对提示，**不自动认定多收** |
| UNKNOWN | 附加费缺失/未确认、有尺寸缺体积除数 | 规则不完整，不判多收费 |
| INVALID | 结构非法（负值/NaN/步长≤0/重复 ID/rounding 不支持） | 整体拒绝（invalid_input） |

## 4. 输出结构

- 顶层：`currency`、`shipment_count`、`status_counts`、`shipments[]`、`markdown_summary`。
- 每条 detail：实际重/尺寸/除数/体积重/计费基础依据/计费重/首重/续重阶梯数/首价/续价/附加费(确认状态与合计)/估价/账单/差异/费率来源/rounding/status/reasons/note。
- `markdown_summary` 可直接渲染，含口径说明。

## 5. 限制

- 体积重口径与续重阶梯规则来自用户输入（rate_source 仅记录来源），不替用户联网查价目表。
- 单价不含税、不含报关/清关费；合并多件计费不支持。
- 不付款、不代申诉、不改账单；样例（references/sample.json）为合成数据（P-001~003）。
