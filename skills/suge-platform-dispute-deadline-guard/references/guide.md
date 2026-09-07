# 字段、公式与输出规范（平台争议申诉时限守门）

## 1. 输入口径

顶层对象：`as_of_date`（ISO YYYY-MM-DD，必填，核对基准日）、`currency`（三位代码，默认 CNY）、`timezone`（可选，如 Asia/Shanghai，用于提示跨时区差异）、`cases`（1–500 项）。

每条案件字段：

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| case_id | 是 | 1–64 文本 | 案件标识；重复出现→该 case 标 UNKNOWN 并列入 duplicate_case_ids 人工去重 |
| platform | 否 | 1–120 文本 | 平台名；缺省"未命名平台"；含 PII 时脱敏 |
| case_type | 否 | 1–120 文本 | 案件类型；同上 |
| notice_date | 否 | ISO 日期 | 通知/立案日期；相对天数口径需要它做锚点 |
| explicit_deadline | 否 | ISO 日期 | 平台明确截止日；**存在时优先于 deadline_days** |
| deadline_days | 否 | 1–3650 整数 | 相对天数（自 notice_date 起）；explicit_deadline 缺失时才用它推算 |
| amount_at_risk | 否 | 0–10^12 | 风险金额；负数直接拒绝 |
| required_evidence | 否 | ≤100 项类型标识 | 必需证据类型（只比较标识文本，不打开 URL/文件） |
| available_evidence | 否 | ≤100 项类型标识 | 已有证据类型标识 |
| event_timeline | 否 | ≤200 项 | `{date, note}` 事实时间线；note 自由文本做 PII 脱敏 |

**没有 explicit_deadline 也没有 deadline_days → 该案件 UNKNOWN，不套用任何平台默认期限**（不同平台/案件类型期限差异大，不替用户猜）。

## 2. 计算口径

- 截止日：explicit_deadline 存在 → 直接采用（source=explicit，deadline_days 被忽略但保留展示）；否则 deadline = notice_date + deadline_days（source=relative）；再否则截止日=未知。
- 剩余天数 `remaining = deadline − as_of`（纯日历日差）。
- 缺证据 `missing_evidence = required_evidence − available_evidence`（按类型标识集合差，**不读取证据内容、不打开 URL/文件**）。
- 紧急度：剩余 <0 → expired；=0 → due_today；≤3 → critical；≤7 → high；其余 normal。
- 时间线按日期升序输出；自由文本（platform/case_type/timeline note）中的手机号（1[3-9] 开头 11 位）、身份证（17 数字+校验位）、银行卡（16–19 位连续数字）、邮箱候选值自动脱敏（如 138****5678），并记入 `pii_masked_fields` 提示人工复核。

## 3. 状态口径（只生成中性事实摘要）

| status | 触发 | 含义 |
|---|---|---|
| READY_FOR_HUMAN_REVIEW | 有截止日、期限充裕（>7 天）、证据齐 | 人工核对后按平台入口提交 |
| EVIDENCE_MISSING | 期限充裕但缺证据类型 | 先补证据再提交 |
| DEADLINE_RISK | 已过期/当天截止/剩余 ≤7 天 | 优先处理期限（含 urgency 细分） |
| EXPIRED | 剩余天数 <0 | 已过截止日，仅记录（是否可补救由平台规则决定） |
| UNKNOWN | 无截止规则 / 无 notice_date 锚点 / case_id 重复 | 不猜期限，人工确认 |

优先级：重复 ID UNKNOWN 优先；其次 EXPIRED > DEADLINE_RISK > EVIDENCE_MISSING > READY_FOR_HUMAN_REVIEW。

## 4. 输出结构

- 顶层：`as_of_date`、`currency`、`timezone`、`case_count`、`duplicate_case_ids`、`total_amount_at_risk_known`、`status_counts`、`cross_timezone_note`、`cases[]`、`markdown_summary`。
- 每条 case detail：上述字段 + `deadline_source`、`deadline`、`remaining_days`、`urgency`、`status`、`reasons[]`、`pii_masked_fields[]`、`note`（含"不代理提交/不预测胜诉/不构成法律意见"）。
- `markdown_summary` 可直接渲染。

## 5. 限制

- 不代理提交申诉、不自动回复平台、不预测胜诉、不构成法律意见；涉及诉讼等复杂情形转人工律师。
- 截止日与"今天"均为纯日历日运算；跨时区以平台本地日历日为准（输出含 cross_timezone_note 提示）。
- 平台期限规则只来自用户输入；样例（references/sample.json）为合成数据（D-001~004，平台均为"示例"命名）。
