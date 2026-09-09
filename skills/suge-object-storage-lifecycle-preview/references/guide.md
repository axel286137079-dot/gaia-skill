# 字段、公式与输出规范（对象存储生命周期变更预演）

## 1. 输入口径

顶层对象：`provider`（必填，如 AWS/ALIYUN/GCP/TENCENT）、`as_of`（基准日，ISO8601 带时区或纯日期，**必填**）、`currency`（三位代码，默认 CNY）、`object_cohorts`（1–500 项）、`rules`（1–500 项）、`policy_profile`（**必填**）、`price_table`（可选）。

### policy_profile（用户提供，不跨云套用默认值）

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| min_billable_bytes | 否 | 整数 ≥0（默认 0） | 最小计量空间（字节/对象）。avg < 该值时 billable=对象数×最小计量 |
| minimum_storage_days | 否 | 整数 ≥0（默认 0） | 目标存储类最低存储天数。转换/到期后过早删除→费用/不可行提示 |
| small_object_threshold_bytes | 否 | 整数 ≥0（默认 0=不启用） | 小对象阈值。avg < 阈值→该规则对该批不适用（默认不转） |
| conflict_priority | 否 | review / earliest / latest / expiration_first / transition_first（默认 review） | 规则重叠时的裁决；review=不裁决标冲突 |
| time_rounding | 否 | day（当前仅支持按天取整） | 时间取整口径 |

### rules[]

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| rule_id | 是 | 1–80 文本（字母数字 `._:-`） | 重复直接拒绝 |
| action | 是 | transition / expiration | transition=转换到 target_class；expiration=到期删除 |
| days | 是 | 整数 ≥0 | 相对 last_modified_at 的天数，触发日=last_modified_at+days |
| target_class | transition 必填 | ≤60 文本 | 目标存储类名（须与 price_table.classes 键一致才能估费） |
| filters | 否 | 对象 | 可选匹配条件（见下）；**缺省=匹配全部** |

filters 支持：`prefix`（cohort 未提供 key 前缀信息→无法核对→UNKNOWN）、`tags`（cohort 未提供标签→无法核对→UNKNOWN）、`min_bytes`/`max_bytes`（与 cohort 的 avg_object_bytes 比较；avg 不在范围→不适用）。

### object_cohorts[]

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| cohort_id | 是 | 1–80 文本 | 重复直接拒绝 |
| object_count | 是 | 整数 ≥1 | 该批对象数 |
| total_bytes | 是 | 整数 ≥0 | 该批总字节 |
| avg_object_bytes | 否 | 整数 ≥0 | 平均对象字节；缺省由 total/object_count 推导 |
| current_class | 是 | ≤60 文本 | 当前存储类 |
| last_modified_at | 否 | ISO8601/日期 | 触发日期基准；**缺失→无法判定触发→UNKNOWN** |
| versioning_state | 否 | versioned / unversioned / suspended / unknown（默认 unknown） | 决定 expiration 语义：versioned=删除标记（历史版本保留）；其余=永久删除提示 |
| expected_delete_or_overwrite_at | 否 | ISO8601/日期 | 预计删除/覆盖日，用于最低时长暴露检查 |

### price_table

`{"effective_at": "...", "source": "...", "classes": {"STANDARD": {"storage_gb_month": "...", "transition_per_1000_objects": "..."}, ...}}`。费率全部由用户提供并带来源时间；**目标类缺费率→只给用量不报金额**（`price_missing_for_*`）。

时间戳必须带偏移或 `YYYY-MM-DD`；naive 时间戳、负数、NaN/Infinity、重复 cohort_id/rule_id、命令/URL/提示词一律安全处理（命令只当文本不执行）。provider 缺省/未知不做任何内置费率假设。

## 2. 计算口径（情景预演，非精确账单）

- **不调用云 API、不修改生命周期、不删除对象**。cohort 是聚合批次：命中按"整批上界"估算（对象数/容量），缺对象年龄/大小分布时不假装精确到单个对象。
- 触发日 = cohort.last_modified_at + rule.days；`already_triggered` 标记触发日是否已到基准。
- billable 容量：默认=total_bytes；若 min_billable_bytes>0 且 avg<min_billable_bytes → billable=object_count×min_billable_bytes（最小计量空间）。
- 候选月度存储费用 = billable/2^30 × storage_gb_month（目标类 transition / 现类 expiration）；transition 请求费用 = ceil(object_count/1000) × transition_per_1000_objects。**缺费率只给用量不报金额**。
- 最低时长暴露：若 expected_delete_or_overwrite_at 早于触发日→"规则不会产生作用"；晚于触发日但不足 minimum_storage_days→"提前变更费用/不可行"。
- 规则重叠：同批命中多个目标类或 transition 与 expiration 重叠 → conflict_priority=review 时标 CONFLICT_REVIEW（不猜优先级）；earliest/latest 等为"如何取舍"的说明性裁决（当前 review 之外值留待人工或产品策略）。

## 3. 状态口径

| status | 触发 | 含义 |
|---|---|---|
| SAFE_PREVIEW | 有适用规则但无永久删除、无费用风险 | 预演显示可安全执行（仍是预演） |
| COST_RISK | 最小计量放大 / 最低存储时长不足 / 规则触发前即删除 | 有费用或无效变更风险提示 |
| DELETE_RISK | expiration 命中且非 versioned（含 unversioned/suspended/unknown） | 到期对象将被永久删除（不可逆） |
| CONFLICT_REVIEW | 同批多目标类或转换+到期重叠且未声明优先级 | 冲突规则需人工裁决 |
| UNKNOWN | 缺 last_modified_at / prefix/tag 过滤无法核对 | 数据不足，不做单对象精确预估 |
| INVALID | 结构非法（重复 ID、缺 provider、负数、NaN 等） | 整体拒绝 |

优先级：CONFLICT_REVIEW > DELETE_RISK > COST_RISK > UNKNOWN > SAFE_PREVIEW（versioned 的 expiration=软删除提示，不升级为 DELETE_RISK）。

## 4. 输出结构

- 顶层：provider、as_of、currency、policy_profile（回显）、cohort_count、status_counts、cohorts[]、markdown_summary。
- 每条 cohort：cohort_id、current_class、versioning_state、object_count、total_bytes、avg_object_bytes、last_modified_at、expected_delete_or_overwrite_at、rule_hits、status、hit_billable_bytes_estimate、rule_evaluations[]（每条含 rule_id/action/days/target_class/applicable/trigger_date/already_triggered/billable 估算/候选费用/reasons）、reasons、note。
- `markdown_summary` 可直接渲染，含"只做情景预演、不修改生命周期、不删除对象、不代表云端已变更"。

## 5. 限制

- 情景预演 = 帮助判断变更影响，**不是实际执行**；任何结果都不能声称云端已发生变更。
- 规则、阈值、费率全部来自用户输入；不读取云账号、不跨云套用最低时长/小对象阈值/版本删除语义。
- 聚合粒度限制：只按批次上界估算，不假装精确到单对象；缺价格只给用量不报金额。
- 样例（references/sample.json）为合成数据（ALIYUN profile，COH-* 五批）。
