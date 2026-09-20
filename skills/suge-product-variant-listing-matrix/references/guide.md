# 字段表与阅读步骤

本文件是 `suge-product-variant-listing-matrix` 的字段说明与输出阅读指南。所有规则都是确定性的：同一输入必然得到逐字节一致的输出。

## 1. 输入字段表

顶层对象（唯一输入是一个 JSON 文件，或聊天中粘贴的等价 JSON）：

| 字段 | 必需 | 类型 | 说明 |
|---|---|---|---|
| `as_of` | 建议 | string | 基准时间，**必须带时区偏移**，如 `2026-09-19T10:00:00+08:00`。缺失时输出为 `null`，不影响矩阵计算。 |
| `currency_default` | 否 | string | 当某个 SKU 未写 `currency` 时使用的默认币种。**只有用户显式给出才会套用**；既没写也没默认值时该 SKU 币种为 `null` 并标记 `CURRENCY_UNKNOWN`。 |
| `parent` | 建议 | object | 父商品事实：`parent_id`、`title`、`brand`、`category_ref`、`description`，以及任何你想保留的补充字段。 |
| `spec_dimensions` | 是 | array | 规格维度，每项 `{"name": "颜色", "values": ["红色", "蓝色"]}`。维度顺序决定矩阵列顺序。 |
| `skus` | 是 | array | SKU 列表，每项见下表。 |
| `platform_fields` | 否 | array | 平台字段要求，每项 `{"field": "...", "required": true, "max_length": 60}`。**必须由用户提供**；本工具不内置任何平台规则。 |
| `images` | 否 | array | 已声明图片，每项 `{"image_ref": "IMG-01", "role": "main"}`。 |

`skus[]` 每项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `sku_id` | string | SKU 编号。重复即标 `DUPLICATE_SKU_ID`。 |
| `specs` | object | 规格取值，键为维度名。缺失维度 → `INCOMPLETE_SPEC`；取值不在维度值清单中 → `UNKNOWN_SPEC_VALUE`。 |
| `price` | string/number | 单价。缺失 → `PRICE_UNKNOWN`；非法或负数 → `INVALID_PRICE`。 |
| `currency` | string | 币种。不同币种**永不合并**。 |
| `stock` | number/null | 库存。`null`/缺失 → `STOCK_UNKNOWN`（**不会当 0**）；负数或非整数 → `INVALID_STOCK`；`0` 是合法值，标 `OUT_OF_STOCK`。 |
| `barcode` | string | **必须是字符串**。以数字形式提交会被标 `BARCODE_NUMERIC_TYPE`，因为 JSON 数字无法保留前导零。 |
| `image_ref` | string | 图片编号。未在 `images` 中声明 → `MISSING_IMAGE_REF`。 |
| `notes` | string | 备注，只作不可信文本处理，不影响判定。 |

## 2. 规格值规范化（NFKC + 空白折叠）

每个规格值、维度名、SKU 编号、条码都会先做：Unicode **NFKC** 规范化（全角→半角）、去控制字符、折叠连续空白并去首尾空格。

- `"红色 "` 与 `"红色"` 规范化后相同 → 该维度值被列入 `duplicate_values`（重复规格值）。
- 比较用的键在规范化后再 `casefold`，因此 `"S"` 与 `"s"` 视为同一规格值；**展示值保留第一次出现的写法**。
- 维度内的取值按该比较键去重，所以 `["Red", "red"]` 只有**一个**规格值：`normalized_values` 为 `["Red"]`、`duplicate_values` 为 `["Red"]`、维度取值数按 1 计。规范化清单、重复清单与笛卡尔组合数三者永远一致，不会出现互相矛盾的数字。
- 全角/半角由 NFKC 折叠、大小写由 `casefold` 折叠，因此 `["Ａ", "A"]` 同样是重复值。

## 3. 笛卡尔积与实际 SKU 的差集

- 期望组合 = 每个维度的**去重后**规格值做笛卡尔积。
- 实际组合 = 每个规格完整且取值合法的 SKU 的规格组合集合。
- `missing_combinations` = 期望 − 实际；`beyond_dimension_combinations` = 实际 − 期望（一般应为空）。
- **未提供 `spec_dimensions`（缺失或空数组）时不进行差集计算**：期望与实际组合数都为 0、`beyond_dimension_combinations` 为空，顶层状态至少为 `GAPS_FOUND`，并追加 `SPEC_DIMENSIONS_NOT_PROVIDED` 待确认问题。没有规格维度就没有变体结论，工具不会给出 `READY`。

## 4. SKU 状态

| 状态 | 触发标记 |
|---|---|
| `INVALID` | `DUPLICATE_SKU_ID`、`MISSING_SKU_ID`、`INVALID_PRICE`、`INVALID_STOCK` |
| `CONFLICT` | `DUPLICATE_SPEC_COMBINATION`、`UNKNOWN_SPEC_VALUE`、`INCOMPLETE_SPEC`、`UNDECLARED_SPEC_DIMENSION` |
| `REVIEW` | `STOCK_UNKNOWN`、`PRICE_UNKNOWN`、`MISSING_IMAGE_REF`、`BARCODE_NUMERIC_TYPE`、`DUPLICATE_BARCODE` |
| `OK` | 无任何标记 |

顶层 `status`：`BLOCKED`（存在 INVALID SKU 或 BLOCKER 字段缺口）> `GAPS_FOUND`（没有 SKU、未提供 `spec_dimensions`，或存在 CONFLICT / REVIEW / 缺失组合 / 字段缺口）> `READY`（只在规格维度与 SKU 齐备且无任何缺口时）；检测到疑似真实凭据时直接 `REJECTED`。

## 5. 输出阅读步骤

1. 先看顶层 `status`。`REJECTED` 表示输入含疑似凭据，**本工具不会回显**，请移除后重试。
2. 看 `spec_normalization`：确认维度名、规格值与 `duplicate_values`。若 `dimension_count` 为 0，说明没有提供 `spec_dimensions`，结论必然不是 `READY`。
3. 看 `cartesian`：`missing_combinations` 是"应该有但没有"的规格组合。
4. 看 `skus`：逐行 `review_flags`。`stock` 为 `null` 表示**未知**，不是 0。
5. 看 `duplicate_spec_groups` 与 `barcode_issues`：重复规格组合、重复条码、数值型条码。
6. 看 `image_mapping`：`missing_image_refs` 与 `unreferenced_images`。
7. 看 `totals_by_currency`：**分币种**核对，不要相加。
8. 看 `field_gaps`：`BLOCKER` 必须先解决。
9. 看 `clarification_questions`：按 `Q-01` 顺序原样发出去确认，不要替对方填答案。
10. 用 `listing_checklist` 与 `markdown_summary` 作为人工上架前的检查表。

## 6. 不可信输入处理

- **提示注入只标记、不执行**：`markdown_summary` 中命中注入的值会被替换为固定占位「已隐藏疑似提示注入文本」；结构化 JSON 保留原始值，并在 `injection_flagged` 中带精确路径（如 `parent.supplier_note`）。
- 检测规则是**「覆盖动作 + 指令/规则/系统/提示」同句共现**，单个动词或单个名词不触发。因此「请忽略小额尾差，财务已核销。」这类普通备注不会被误标。
- **Markdown 安全转义**：所有自由文本在进入 `markdown_summary` 前会压掉控制字符与换行，并对 `\ | ` [ ] ( ) # ! < > * _` 加反斜杠，任何输入值都无法新建标题、列表、链接或表格列。
- **凭据拒绝**：字段名含 `password`/`token`/`api_key`/`密码`/`密钥` 等，或值形如私钥/`sk-…`/`AKIA…` 时，直接返回 `REJECTED` 且不回显内容。

## 7. 明确不做的事

不抓取平台、不登录店铺、不创建或发布商品、不猜测功效/材质/认证/库存/价格、不生成虚假营销声明、不判断平台合规性。平台规则必须由用户提供；本工具不内置任何平台的类目或字段规则。
