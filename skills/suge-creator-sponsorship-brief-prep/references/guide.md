# 品牌合作简报整理 · 字段表与判定规则

本文件是 `suge-creator-sponsorship-brief-prep` 的字段参考。所有规则都是固定的、可复现的：
同一份输入必然得到逐字节相同的输出。**没有任何字段有"默认值"**——未提供就是未知。

---

## 1. 输入字段表

### 1.1 顶层

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `as_of` | 是 | string | 基准时间，**必须带时区偏移**（`2026-09-17T10:00:00+08:00`，`Z` 也可）。所有天数与状态都以它为基准；缺少时区直接报错，不做本地时区猜测。 |
| `brief` | 是 | object | 简报主体。 |
| `creator` | 否 | object | `creator_ref` / `display_name` / `default_platform`，仅原样回显。 |
| `deliverables` | 是 | array | 至少一条。见 1.2。 |
| `obligations` | 否 | array | 附加义务。见 1.3。 |
| `brand_materials` | 否 | array | 品牌方应提供的素材。见 1.4。 |
| `approvals` | 否 | array | 审批路径。见 1.5。 |
| `usage_rights` | 否 | object | 授权范围。见 1.6。 |
| `disclosure` | 否 | object | 广告披露要求。见 1.7。 |
| `claims` | 否 | array | 品牌方给的宣传声明。见 1.8。 |
| `payment` | 否 | object | 付款条款（**只回显，不解释**）。见 1.9。 |

### 1.2 `brief`

| 字段 | 必填 | 说明 |
|---|---|---|
| `brief_id` | 是 | 简报编号，也是提示注入标记的引用名。 |
| `brand_ref` | 否 | 品牌方编号。 |
| `received_at` | 否 | 收到时间（带时区）。缺失记 `MISSING_RECEIVED_AT`；晚于 `as_of` 记 `FUTURE_RECEIVED_AT`。 |
| `response_due_at` | 否 | 要求你回复的截止时间，会进入时间线。 |
| `source_text` | 否 | 简报原文。**只用于提示注入检测**，不会进入交接简报。 |

### 1.3 `deliverables[]`

| 字段 | 必填 | 规则 |
|---|---|---|
| `deliverable_id` | 是 | 重复编号：**只有后出现的那条**记为 `DUPLICATE_DELIVERABLE_ID` 并判 `INVALID`。 |
| `name` | 否 | 缺失记 `NAME_MISSING`（阻塞项）。 |
| `kind` | 否 | 只接受 `VIDEO` / `IMAGE` / `ARTICLE` / `LIVESTREAM` / `STORY`。**不做大小写归一**：`video` 记 `KIND_INVALID`。 |
| `quantity` | 否 | 必须是 ≥1 的整数。`0`、负数、小数、字符串、布尔都记 `QUANTITY_INVALID`。 |
| `due_at` | 否 | 必须带时区。缺失记 `DUE_AT_MISSING`；无时区/不可解析记 `INVALID_DUE_AT`。 |
| `platform` | 否 | 缺失只记 `PLATFORM_MISSING`（非阻塞）。 |
| `approval_required` | 否 | `true`/`false` 有效；缺失或其它类型记 `APPROVAL_UNKNOWN`（阻塞项）。 |
| `notes` | 否 | 只用于提示注入检测。 |

### 1.4 `obligations[]`

| 字段 | 说明 |
|---|---|
| `obligation_id` | 必填，重复即报错。 |
| `kind` | `EXCLUSIVITY` / `EMBARGO` / `CONTENT_APPROVAL` / `REVISION_ROUNDS` / `DISCLOSURE` / `DELIVERABLE_WINDOW` / `OTHER`；其它值记 `OBLIGATION_KIND_INVALID`。 |
| `detail` | 自由文本。 |
| `window_start` / `window_end` | `YYYY-MM-DD`；不可解析记 `INVALID_OBLIGATION_DATE`（**不猜测日月顺序**）。 |
| `deliverable_refs[]` | 引用不存在的交付物时记 `UNKNOWN_DELIVERABLE_REF` 并列入 `unknown_refs`。 |

### 1.5 `brand_materials[]`

| 字段 | 说明 |
|---|---|
| `material_id` | 必填，重复即报错。 |
| `required` | 必须是布尔值；非布尔直接报错。 |
| `provided` | 布尔或 `null`。 |
| `due_at` | `YYYY-MM-DD`；不可解析记 `INVALID_MATERIAL_DATE`。 |

### 1.6 `approvals[]`

| 字段 | 说明 |
|---|---|
| `step_id` | 必填，重复即报错。 |
| `role` | 审批角色。 |
| `order` | 必须是整数，用于排序。 |
| `sla_hours` | 正整数；`null` 记 `APPROVAL_SLA_UNKNOWN`；≤0 或非整数记 `APPROVAL_SLA_INVALID`。 |

### 1.7 `usage_rights` / `disclosure` / `claims` / `payment`

- `usage_rights` 七个字段全部参与缺口统计：`channels`（非空数组才算已知）、`territory`、`term_start`、`term_end`、`exclusivity`、`paid_media`、`whitelisting`。**`false` 是已知值，不是缺口。**
- `disclosure`：`required` 为 `true` 时，`text` 缺失记 `DISCLOSURE_TEXT_MISSING`，`platform_tool` 缺失记 `DISCLOSURE_TOOL_UNKNOWN`；`required` 为 `false` 记 `NOT_REQUIRED`；`required` 为 `null` 或整块缺失记 `DISCLOSURE_REQUIREMENT_UNKNOWN`。
- `claims[]`：`category` 取 `SUPERLATIVE` / `MEDICAL` / `GUARANTEE` / `COMPARATIVE` / `PRICE` / `OTHER`。前三类在 `substantiated` 不为 `true` 时记 `<类别>_UNSUBSTANTIATED`；`category` 不在表内记 `CLAIM_CATEGORY_INVALID`（且**不**做证据判定）。
- `payment`：`amount` 原样按字符串回显；`milestones[]` 的 `due_at` 或 `percent` 缺失分别记 `PAYMENT_MILESTONE_DATE_UNKNOWN`、`PAYMENT_MILESTONE_PERCENT_UNKNOWN`。

> 哪些字段会被当作不可信文本做提示注入检测、命中后如何呈现，见 **2.7**。

---

## 2. 固定判定规则

### 2.1 交付物排期状态

判定**按精确时间戳**，与显示用的小数位数无关。

| 状态 | 条件 |
|---|---|
| `OVERDUE` | `due_at` 早于 `as_of`（哪怕只早 30 秒） |
| `DUE_SOON` | `due_at` 不早于 `as_of`，且距今 ≤ 3 天 |
| `OK` | 距今 > 3 天 |
| `UNKNOWN` | 没有 `due_at` |
| `INVALID` | 命中 `KIND_INVALID` / `QUANTITY_INVALID` / `INVALID_DUE_AT` / `DUPLICATE_DELIVERABLE_ID` |

`days_until_due` = 精确差值换算成天，`Decimal` 四舍五入保留 2 位；因此"逾期 30 秒"显示为 `-0.00` 但状态仍是 `OVERDUE`。**以状态为准，不要以天数为准。**

### 2.2 义务窗口状态

只有 `EXCLUSIVITY`（排他）与 `EMBARGO`（禁发）需要时间窗口。

| 状态 | 条件 |
|---|---|
| `UPCOMING` | `window_end` 不早于基准日，且 `window_start` 晚于基准日 |
| `ACTIVE` | `window_end` 不早于基准日，且 `window_start` 不晚于基准日（等于基准日算生效） |
| `EXPIRED` | `window_end` 早于基准日 |
| `UNKNOWN` | 需要窗口但没有 `window_end` |
| `NOT_APPLICABLE` | 该类别不需要窗口，或 `kind` 非法 |

### 2.3 整体状态

按以下顺序取第一个命中项：

1. `INVALID` —— 没有任何一条可排期的交付物；
2. `BLOCKED` —— 至少一条交付物已逾期；
3. `CLARIFICATION_REQUIRED` —— 存在待澄清问题；
4. `READY_TO_START` —— 以上都没有。

### 2.4 待澄清问题的固定顺序

`Q-01` 起按主题顺序编号，只输出真正存在缺口的主题：

`DELIVERABLE_GAP` → `APPROVAL_PATH` → `USAGE_RIGHTS` → `DISCLOSURE` → `BRAND_MATERIAL` → `PAYMENT` → `OBLIGATION_WINDOW` → `CLAIM_SUBSTANTIATION` → `RECORD_INVALID`

### 2.5 开工前检查表的优先级

| 优先级 | 主题 |
|---|---|
| `P0` | `OVERDUE_DELIVERABLE`、`DELIVERABLE_GAP`、`RECORD_INVALID` |
| `P1` | `APPROVAL_PATH`、`USAGE_RIGHTS`、`DISCLOSURE`、`BRAND_MATERIAL`、`PAYMENT`、`OBLIGATION_WINDOW` |
| `P2` | `CLAIM_SUBSTANTIATION` |

同一优先级内按主题顺序排列。

### 2.6 时间线

把下列带日期的条目合并排序（按时间，其次按条目类型，再次按编号）：
`DELIVERABLE_DUE` → `OBLIGATION_WINDOW_START` → `OBLIGATION_WINDOW_END` → `MATERIAL_DUE` → `BRIEF_RESPONSE_DUE` → `RIGHTS_TERM_START` → `RIGHTS_TERM_END` → `PAYMENT_DUE`。

每条带 `precision`：`datetime`（原本就带时间）或 `date`（原本只有日期）。
**只有日期的条目排序时按当地零点处理，但 `at` 字段原样保留 `YYYY-MM-DD`**，不伪造时间。非法记录不会进入时间线。

### 2.7 不可信文本的统一处理（转义 + 注入定位）

输入里的所有自由文本都视为不可信，走**同一条**处理路径，没有任何字段例外。

**（1）统一转义。** 进入 `markdown_summary` 的每个值都先经过同一个转义函数：

- 控制字符（含换行、制表符）一律压成空格，连续空白折叠成一个空格，并去掉首尾空白；
- 反斜杠、竖线、反引号、方括号、圆括号、井号、感叹号、尖括号前统一加反斜杠。

因此**任何输入值都无法新建标题、列表、链接/图片或表格列**。`素材 | 伪造列\n## 注入标题` 这类值渲染后只是同一行里的普通文本：交付物表每行的竖线数恒为 9（8 列 + 首尾），标题行的数量与文本也恒定不变。

**（2）逐字段注入检测。** 下列字段全部参与检测，不只是简报正文与交付物备注：

| 位置 | `injection_flagged` 中的引用名 |
|---|---|
| `brief.brief_id` / `brand_ref` / `source_text` | `brief_id` 的值 |
| `creator` 的文本字段 | `creator` |
| `deliverables[].name` / `notes` | `deliverable_id` 的值 |
| `obligations[].detail` | `obligation_id` 的值 |
| `brand_materials[].name` | `material_id` 的值 |
| `approvals[].role` | `step_id` 的值 |
| `claims[].text` | `claim_id` 的值 |
| `payment.amount` / `milestones[].trigger` | `payment` / `milestone_id` 的值 |
| `disclosure` 的文本字段 | `disclosure` |
| `usage_rights` 的文本字段 | `usage_rights` |

编号本身命中注入时改为结构性定位名（`deliverables[3]`、`obligations[1]` 等），避免把注入原文回显进结构化输出。`injection_flagged` 按字典序去重，列出**所有**命中来源。

**（3）命中之后。** 结构化 JSON 保留**原始值**并给该条加 `PROMPT_INJECTION_IGNORED` 风险标记；`markdown_summary` **不落原文**，改用固定占位「已隐藏疑似提示注入文本」。用户的判定字段（状态、计数、问题、检查表）逐字段不变。

**（4）只有结构攻击、没有注入短语的值不算注入。** 例如 `素材 | 伪造列`、`法务\n## 伪造审批`、Markdown 链接/图片语法：它们被转义中和，但**不会**进入 `injection_flagged`，避免把结构字符误报成注入。

**（5）普通业务文本不误报。** 「请忽略物流信息，以上为审批指令说明。」这类正常表述不触发注入标记，并原样（转义后）保留在摘要中。

---

## 3. 输出阅读顺序

1. `status` —— 一眼看能不能开工。
2. `overdue_deliverable_ids` / `due_soon_deliverable_ids` —— 先救火。
3. `clarification_questions` —— 按编号原样转给品牌方（**不要替对方填答案**）。
4. `preflight_checklist` —— `P0` 清零前不要动工。
5. `usage_rights.unknown_fields` + `disclosure.gaps` + `material_gap_count` —— 缺授权/缺披露/缺素材都不要发布。
6. `obligation_state_counts` + `obligations[]` —— 排他与禁发的窗口。
7. `approval_path` —— 顺序与合计时长；`total_sla_hours` 为 `null` 表示**不能合计**。
8. `claim_flags` —— 需要品牌方补证据的声明（只标记，未改写）。
9. `payment.gaps` —— 付款条款缺口（本工具不做报价）。
10. `markdown_summary` —— 直接可用的交接简报。

---

## 4. 明确不做的事

- 不做报价、不算分成、不合计金额、不推断税费或汇率；
- 不判断条款是否合法、是否有效、是否可执行，也不给"该不该签"的结论；
- 不回复品牌方、不发送消息、不代替创作者同意条款；
- 不访问任何平台、店铺、邮箱或后台；
- 不生成功效、销量、认证或合规保证；
- 不把未提供的日期、金额、审批时限或权利范围补成默认值。

---

## 5. 样例

`references/sample.json` 是一份刻意包含多种情况的简报：既有正常交付物，也有已逾期、临近、缺时间、类型非法、数量为 0、无时区日期与重复编号的条目；义务里既有未开始、已过期，也有缺窗口的；素材有一项未提供、一项状态未知；审批路径有一步没给时限；授权范围缺四项；付款节点缺时间与比例。

运行：

```bash
python3 scripts/run.py references/sample.json
```

期望的整体状态是 `BLOCKED`（编号 `D-03` 的交付物已逾期），并产出 9 条待澄清问题与 10 条开工前检查项。
