# 电商活动上线准备包 — 字段与判定说明

本文件是 `SKILL.md` 的展开，也是你能用来逐条核对输出的依据。**所有判定都在这里写死**，脚本只做算术和排序，不做判断。

## 1. 输入字段

### 1.1 顶层

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `as_of` | 是 | string | 生成基准时间，**必须带时区偏移**，如 `2026-09-26T21:00:00+08:00`。缺失或无法解析 → `INPUT_INCOMPLETE`（若渠道/商品也有缺失）或 `NOT_READY`。 |
| `campaign` | 是 | object | 见 1.2 |
| `channels` | 是 | array | 见 1.3 |
| `products` | 是 | array | 见 1.4 |
| `promotions` | 否 | array | 见 1.5 |
| `assets` | 否 | array | 见 1.6 |
| `prep_items` | 否 | array | 见 1.7 |

### 1.2 `campaign`

| 字段 | 必填 | 说明 |
|---|---|---|
| `campaign_id` | 否 | 活动的内部编号 |
| `name` | 否 | 活动名，用于 Markdown 标题 |
| `timezone` | 否 | IANA 时区名（如 `Asia/Shanghai`），**只用于显示**；不参与任何时间换算或缺失时区填充 |
| `starts_at` / `ends_at` | 否 | 活动窗口，**必须带时区偏移** |

`window_state` 取值：

| 值 | 条件 |
|---|---|
| `VALID` | 两个时间都可解析且 `ends_at > starts_at` |
| `INVALID` | 两个时间都可解析但 `ends_at <= starts_at` → 追加 `INVALID_CAMPAIGN_WINDOW`（BLOCKER） |
| `UNKNOWN` | 任一时间缺失、缺时区或无法解析 → 追加输入提示 `CAMPAIGN_START/END_TIMEZONE_MISSING_OR_INVALID` 并生成追问 |

### 1.3 `channels[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `channel_id` | 是 | 唯一标识；缺失记 `MISSING_CHANNEL_ID`，重复记 `DUPLICATE_CHANNEL_ID` |
| `name` | 否 | 展示名 |
| `owner` | **视为必填** | 缺失 → `MISSING_OWNER`（BLOCKER），不按「团队共同负责」处理 |
| `launch_deadline` | 否 | 该渠道上线截止时间，**必须带时区偏移**；缺时区 → `DUE_AT_TIMEZONE_UNKNOWN`（REVIEW） |
| `required_asset_kinds[]` | 否 | 该渠道要求的素材类型，取值 `main_image` / `detail_page` / `banner`；其他值记 `UNKNOWN_REQUIRED_ASSET_KIND` |

### 1.4 `products[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `product_id` | 是 | 缺失记 `MISSING_PRODUCT_ID` |
| `channel_id` | 是 | 必须存在于 `channels[]`，否则记 `UNKNOWN_CHANNEL` |
| `name` | 否 | 展示名（自由文本，走注入检测与 Markdown 转义） |
| `stock.value` / `stock.unit` | **视为必填** | 见 §2.2 |
| `price.value` / `price.currency` | **视为必填** | 见 §2.3 |
| `min_stock` | 否 | **由你自己声明**的最低库存；不填就不做任何临界判断 |

### 1.5 `promotions[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `promo_id` | 是 | 缺失记 `MISSING_PROMO_ID` |
| `channel_id` | 是 | 不在 `channels[]` → `PROMO_UNKNOWN_CHANNEL`（BLOCKER） |
| `type` | 是 | 取值 `threshold_discount` / `percent_discount` / `coupon` / `gift` / `bundle`；其他值记 `UNKNOWN_PROMO_TYPE` |
| `product_ids[]` | 否 | 不在 `products[]` 的商品 → `PROMO_UNKNOWN_PRODUCT`（BLOCKER） |
| `value` | 否 | 优惠力度；缺失或非数值记 `PROMO_VALUE_UNKNOWN` |
| `window.starts_at` / `window.ends_at` | 否 | 生效区间；缺失记 `PROMO_WINDOW_UNKNOWN`；超出活动窗口记 `PROMO_WINDOW_OUTSIDE_CAMPAIGN`（REVIEW，可能是预热或返场） |

### 1.6 `assets[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `asset_id` | 是 | 缺失记 `MISSING_ASSET_ID` |
| `channel_id` | 是 | 不在 `channels[]` 记 `UNKNOWN_CHANNEL` |
| `kind` | 是 | 与渠道 `required_asset_kinds` 配对；缺失记 `MISSING_ASSET_KIND` |
| `filename` | **视为必填** | **必须裸文件名**；含 `/`、`\`、`..`、`scheme:`、盘符或超 200 字符 → `INVALID_ASSET_REF`（BLOCKER）且**不解析**。被拒引用的原文本**不会出现在输出里**，`asset_gaps.invalid_refs[].basename` 只保留安全化后的最后一段文件名 |
| `due_at` | 否 | 缺时区 → `DUE_AT_TIMEZONE_UNKNOWN`（REVIEW）；早于 `as_of` → `ASSET_OVERDUE`（BLOCKER） |
| `approved` | 否 | `true` = 已通过；`false` → `ASSET_NOT_APPROVED`（BLOCKER）；缺失 → `ASSET_APPROVAL_UNKNOWN`（REVIEW） |

### 1.7 `prep_items[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `item_id` | 是 | 缺失记 `MISSING_ITEM_ID` |
| `channel_id` | 是 | 不在 `channels[]` 记 `UNKNOWN_CHANNEL` |
| `area` | 否 | 建议取值 `page` / `customer_service` / `logistics` / `stock` / `pricing` / `platform_review`；其他值原样保留 |
| `title` | 是 | 自由文本（走注入检测与 Markdown 转义） |
| `owner` | **视为必填** | 缺失 → `MISSING_OWNER`（BLOCKER） |
| `due_at` | 否 | 缺时区 → `DUE_AT_TIMEZONE_UNKNOWN`（REVIEW）；晚于该渠道 `launch_deadline` → `DEADLINE_AFTER_LAUNCH`（BLOCKER） |
| `done` | 否 | `true` / `false` / 缺失。缺失 → `DONE_UNKNOWN`（REVIEW），**不按未完成或已完成**处理 |

## 2. 数值规则

### 2.1 通用

- 金额与数量一律用 `Decimal` 解析，绝不用浮点。
- `price` 输出固定两位小数（`599.00`）；`stock` / `min_stock` 去掉多余尾零（`420`、`18`）。
- 布尔值不会被当成数字：`true` 不是 1。

### 2.2 库存 `stock.value`

| 情况 | `stock_state` | finding |
|---|---|---|
| 字段缺失（`null` / 不存在） | `UNKNOWN` | `MISSING_STOCK`（BLOCKER） |
| 非数字（如 `"待确认"`） | `INVALID` | `INVALID_STOCK`（BLOCKER） |
| 负数 | `INVALID` | `INVALID_STOCK`（BLOCKER） |
| 有效且 ≥ 0 | `KNOWN` | 若 < `min_stock` → `STOCK_BELOW_MIN`（REVIEW） |

**只有 `KNOWN` 参与 `stock_by_unit.known_total`。** 缺失与非法分别进 `unknown_count` / `invalid_count`，都**不进合计**。

### 2.3 价格 `price.value`

| 情况 | `price_state` | finding |
|---|---|---|
| 字段缺失 | `UNKNOWN` | `MISSING_PRICE`（BLOCKER） |
| 非数字 | `INVALID` | `INVALID_PRICE`（BLOCKER） |
| 负数 | `INVALID` | `INVALID_PRICE`（BLOCKER） |
| 有效但缺 `currency` | `KNOWN` + 标记 `PRICE_CURRENCY_UNKNOWN` | 不进任何币种区间 |
| 有效且有币种 | `KNOWN` | 进 `price_range_by_currency` |

**不存在任何跨币种合计字段。** `price_range_by_currency` 只给每个币种自己的 `min` / `max` / `count`，不给总额，不做换算。

### 2.4 单位

`stock.unit` 不同的库存**不合计**。`件` 与 `pcs` 是两个桶。缺 `unit` 记入 `未标注单位` 桶。

## 3. 冲突判定（BLOCKER）

### 3.1 价格冲突 `PRICE_CONFLICT`

条件：同一 `(product_id, channel_id)` 上有多条记录，且归一化后的价格（`值 + 币种`）不止一种。
细节：非法/缺失价格按其状态字符串（`INVALID` / `UNKNOWN`）参与比较，因此「一条 599.00，另一条缺失」同样算冲突。

### 3.2 库存冲突 `STOCK_CONFLICT`

条件：同一 `(product_id, channel_id)` 上有多条记录，且归一化后的库存（`数值 + 单位`）不止一种。
输出：`stock_conflicts[].stock_values` 为去重排序后的字符串列表。

### 3.3 优惠冲突 `PROMOTION_CONFLICT`

以下全部成立才算冲突：

1. 两条优惠的 `channel_id` 相同；
2. `type` 相同；
3. `product_ids` 有交集；
4. 两条 `window` 的时间区间**重叠**（含首尾相接）；
5. 两条 `value` 都能解析且**不相等**。

任一条件不成立就不算冲突（例如不同渠道的同名活动是允许的）。命中后整体 `BLOCKED`，**工具不替你选哪条留下来**。

### 3.4 优惠引用错误

- `PROMO_UNKNOWN_CHANNEL`：`channel_id` 不在 `channels[]`。
- `PROMO_UNKNOWN_PRODUCT`：`product_ids` 里有商品不在 `products[]`。

两者都为 BLOCKER —— 无法确定适用范围时不能上线。

### 3.5 跨渠道价差 `CROSS_CHANNEL_PRICE_DIFF`（REVIEW，**不是**错误）

同一商品在不同渠道价格不同时记录下来，并明确写出「这是经营事实而不是错误」。工具**不做**比价、不做定价建议、不判断哪个渠道该改。

## 4. 素材与就绪度

### 4.1 必填素材缺失 `MISSING_ASSET`（BLOCKER）

对每个渠道的每个 `required_asset_kinds` 值，若 `assets[]` 中**没有任何**记录同时匹配该渠道与类型，即为缺失。注意：

- 「有记录但没通过审核」**不算** `MISSING_ASSET`，而是各自记 `ASSET_NOT_APPROVED` / `ASSET_APPROVAL_UNKNOWN`；
- 「有记录但文件名非法」也不算缺失，而是记 `INVALID_ASSET_REF`。

### 4.2 文件名安全规则

一律按**裸文件名**处理。以下一项命中即记 `INVALID_ASSET_REF`（BLOCKER）并**拒绝解析该路径**：

| 命中 | 例子 |
|---|---|
| 路径分隔符 | `img/main.jpg`、`img\main.jpg` |
| 上级目录 | `../main.jpg`、`..\\main.jpg` |
| URL scheme | `https://cdn.example.com/main.jpg` |
| 盘符 / 冒号开头的 scheme 形态 | `C:main.jpg`、`file:main.jpg` |
| 超长（> 200 字符） | — |

`file:///etc/passwd` 一类**不会被打开、不会被读取**。被拒引用也**不会**以路径形态出现在 JSON 或 Markdown 里：`assets[].filename` 置为 `null`、`filename_refused` 置为 `true`，`asset_gaps.invalid_refs[].basename` 只保留安全化后的最后一段文件名（例如 `../../share/a.jpg` → `a.jpg`）。

## 5. 时间与时区

- 可解析格式：`YYYY-MM-DDTHH:MM`、`YYYY-MM-DDTHH:MM:SS`，可带小数秒，**必须**以 `Z` 或 `±HH:MM` 结尾。
- 形如 `2026-09-29 18:00`、`2026-09-29T18:00`（无偏移）→ 记 `DUE_AT_TIMEZONE_UNKNOWN`（REVIEW），**保持时间未知**，并生成追问。工具**不会**用 `campaign.timezone` 去补。
- 比较一律在带偏移的绝对时间上进行，跨时区（如 `-07:00`）正确比较。
- `launch_deadline` 缺时区时，`DEADLINE_AFTER_LAUNCH` 检查**跳过**（无法比较），并保留追问。

## 6. 状态与优先级

### 6.1 顶层 `status`

按顺序判定，先命中先定：

| 顺序 | 条件 | `status` |
|---:|---|---|
| 1 | 命中凭据门禁 | `REJECTED` |
| 2 | `as_of` 缺失/非法，或 `channels` 为空，或 `products` 为空 | `INPUT_INCOMPLETE` |
| 3 | `blocker_count > 0` | `BLOCKED` |
| 4 | `review_count > 0` 或存在输入提示 | `NOT_READY` |
| 5 | 其他 | `READY` |

> `READY` 只代表**本次核查范围内**没有发现问题，不代表活动一定能做成或一定赚钱。

### 6.2 渠道状态 `channels[].state`

| 值 | 条件 |
|---|---|
| `BLOCKED` | 该渠道有 ≥1 条 BLOCKER |
| `AT_RISK` | 无 BLOCKER 但有 ≥1 条 REVIEW |
| `READY` | 两者都没有 |

### 6.3 处理顺序

1. `status = REJECTED` → 从输入中移除凭据后重跑。
2. `INPUT_INCOMPLETE` → 补 `as_of` / 渠道 / 商品。
3. `BLOCKED` → **不要上线**。先解决所有 `severity = BLOCKER`，顺序建议：冲突类（价格/库存/优惠）→ 引用错误 → 素材缺失与非法 → 负责人与截止时间。
4. `NOT_READY` → 逐条确认 `REVIEW`，补齐信息后重跑。
5. `READY` → 执行 `pre_send_checklist` 并人工上线。

## 7. 输出结构要点

| 键 | 内容 |
|---|---|
| `launch_readiness` | `state`、`blocker_count`、`review_count`、`channel_status_counts`、`unknown_counts`（stock / price / owner / asset_approval / due_at_timezone / done_state） |
| `channels[]` | 逐渠道矩阵，含 `state`、`blocker_codes`、`review_codes` |
| `stock_by_unit` | 按 `unit` 分组的 `known_total` / `known_count` / `unknown_count` / `invalid_count` |
| `price_range_by_currency` | 按 `currency` 分组的 `min` / `max` / `count`，**无总额** |
| `asset_gaps` | `overdue` / `not_approved` / `approval_unknown` / `invalid_refs` / `timezone_unknown` / `missing_by_channel` |
| `owner_todos[]` | 按负责人汇总的准备事项与未完成数 |
| `findings[]` | 全部发现项，按「严重度 → 渠道 → 代码 → 对象」排序 |
| `finding_counts` | 每个代码的出现次数（只列出现过的代码） |
| `pre_send_checklist[]` | 发送前确认单，按条件动态生成 |
| `clarification_questions[]` | `Q-01` 起编号的追问，按固定主题顺序 |
| `markdown_summary` | Markdown 上线作战单，与 JSON 同源 |

## 8. 不适用与已知边界

1. **不核查商品详情页正文、图文内容质量或违禁词**。素材只按文件名与审核状态处理。
2. **不替代平台后台**。如果后台配置与你的 JSON 不一致，以后台为准；本工具只能核查你交给它的那份快照。
3. **不做财务判断**。不计算利润、毛利、ROAS、投产比、销量预测，也不给定价建议。
4. **不做选品或渠道策略建议**。
5. **不是上线效果保证**。`READY` 只说明资料层面没发现矛盾。
6. **不处理需要登录的数据**。如果数据只能从后台在线查看，请先导出为 JSON。
7. **不判断「这个活动该不该做」**。
