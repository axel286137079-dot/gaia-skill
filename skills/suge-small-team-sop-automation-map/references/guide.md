# 小团队 SOP 自动化机会地图 — 字段与判定说明

本文件是 `SKILL.md` 的展开，也是你用来核对输出的依据。**所有判定都在这里写死**，脚本只做算术和排序。

## 1. 输入字段

### 1.1 顶层

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | **必须带时区偏移**，如 `2026-09-26T21:30:00+08:00`。缺失或非法 → `INPUT_INCOMPLETE` |
| `team` | 否 | 见 1.2 |
| `processes` | 是 | 见 1.3 |

### 1.2 `team`

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 否 | 展示名 |
| `size` | 否 | 人数（整数） |
| `default_hourly_cost` | 否 | `{value, currency}`。只在步骤自身 `hourly_cost` 缺失时用于回退；**不换汇** |

### 1.3 `processes[]` 与 `steps[]`

| 字段 | 层级 | 必填 | 说明 |
|---|---|---|---|
| `process_id` | 流程 | 是 | 缺失记 `MISSING_PROCESS_ID` |
| `name` | 流程 | 否 | 展示名 |
| `owner` | 流程 | 否 | 责任人 |
| `steps[]` | 流程 | 是 | 为空记 `NO_STEPS_PROVIDED` |
| `step_id` | 步骤 | 是 | 缺失记 `MISSING_STEP_ID` |
| `name` | 步骤 | 是 | 缺失记 `NAME_MISSING`（自由文本，走注入检测） |
| `frequency` | 步骤 | **视为必填** | 取值 `daily` / `weekly` / `monthly` / `per_delivery` / `ad_hoc`；缺失记 `FREQUENCY_MISSING`，其他值记 `FREQUENCY_UNKNOWN` |
| `minutes_per_run` | 步骤 | **视为必填** | 单次耗时（分钟），必须 ≥ 0；缺失/非法/负数记 `MINUTES_MISSING` / `MINUTES_INVALID` / `MINUTES_NEGATIVE` |
| `monthly_runs` | 步骤 | 否（但金额需要） | 月均运行次数，**必须显式提供**；工具不按频率推算 |
| `error_rate_pct` | 步骤 | **视为必填** | 当前错误率（%）；缺失/非法记 `ERROR_RATE_MISSING` / `ERROR_RATE_INVALID` |
| `error_consequence` | 步骤 | 否 | `low` / `medium` / `high`；缺失记 `ERROR_CONSEQUENCE_MISSING` |
| `data_sensitivity` | 步骤 | 否 | `public` / `internal` / `customer_pii` / `financial` / `regulated`；缺失记 `DATA_SENSITIVITY_MISSING` |
| `rule_based` | 步骤 | 否 | 布尔。该步骤是否按固定规则执行；缺失记 `RULE_BASED_MISSING` |
| `touches[]` | 步骤 | 否 | 动作标签，见 §1.4 |
| `external_dependency` | 步骤 | 否 | 布尔，是否存在外部对端依赖 |
| `manual_only` | 步骤 | 否 | 布尔，你是否已决定该步必须人工 |
| `human_approval_required` | 步骤 | 否 | 布尔，你自己声明的强制审批 |
| `hourly_cost` | 步骤 | 否 | `{value, currency}`，优先于团队默认值 |
| `assumed_saving_ratio` | 步骤 | 否 | 0–1 的**你的假设**节省比例；不填就没有金额 |
| `shadow_runs` | 步骤 | 否 | 并行人工复核次数；不填则回退默认 10 次（仅出现在文字建议里） |
| `inputs` / `outputs` / `systems` | 步骤 | 否 | 字符串数组，用于展示 |
| `notes` | 步骤 | 否 | 自由文本（走注入检测与转义） |

### 1.4 `touches[]` 动作标签

| 分类 | 取值 | 效果 |
|---|---|---|
| 高风险 | `payment`、`deletion`、`outbound_message`、`external_publish`、`account_permission`、`legal`、`medical`、`financial` | 强制人工审批，最多 `assist` |
| 一般 | `read_only`、`data_entry`、`internal_note`、`scheduling`、`reporting` | 不影响判定 |
| **未识别** | 其他任意值 | 记 `UNKNOWN_TOUCH_TAG`，**按风险未知处理**：最多 `assist` + 强制人工确认 |

## 2. 判定顺序（**先命中先定**）

1. **证据不完整** → `insufficient_evidence`
   条件：`frequency` 不是合法取值，**或** `minutes_per_run` 缺失/非法，**或** `error_rate_pct` 缺失/非法。
2. `manual_only == true` → `keep_manual`
3. `frequency == "ad_hoc"` → `keep_manual`
   （原因写入理由：频率不稳定，流程自身还没定型，先保持人工。）
4. 命中任一强制项 → `assist`（强制人工审批）
   强制项：`touches` 含高风险标签 / 存在未识别标签 / `data_sensitivity` 为 `financial` 或 `regulated` / `error_consequence == "high"` / `human_approval_required == true`。
5. `external_dependency == true` → `assist`
6. `rule_based == true` 且 `data_sensitivity ∈ {public, internal, customer_pii}` → `automate_candidate`
7. 其他（证据完整但没有声明固定规则）→ `assist`

每一步都会写入 `verdict_reason`，说明**为什么**是这个结论，而不是只给一个标签。

## 3. 优先级

### 3.1 月耗时

```
月耗时（小时）= minutes_per_run × monthly_runs ÷ 60
```

**两个值都必须显式提供**才能计算；否则 `monthly_hours` 为 `null`。

### 3.2 分值

```
分值 = 月耗时（小时，保留两位）× 10 + error_rate_pct
```

只有同时满足以下两条才参与排序：

- `verdict ∈ {automate_candidate, assist}`
- `monthly_hours` 与 `error_rate_pct` 都可计算

其余进入 `unranked_steps`，带 `reason` 列表：

| reason | 含义 |
|---|---|
| `MONTHLY_HOURS_NOT_COMPUTABLE` | 缺 `minutes_per_run` 或 `monthly_runs` |
| `ERROR_RATE_MISSING` | 缺错误率 |
| `VERDICT_NOT_RANKABLE` | 判定为 `keep_manual`，不参与自动化优先级 |

**排序规则**：分值降序 → `step_id` 升序 → `process_id` 升序（同分时可复现）。

### 3.3 实施顺序 `implementation_order`

分四档，档内各按自己的规则：

| 档 | 内容 | 档内顺序 |
|---:|---|---|
| 1 | `automate_candidate` | 分值降序（即优先级矩阵顺序） |
| 2 | `assist` | 分值降序 |
| 3 | `keep_manual` | `process_id` → `step_id` 升序 |
| 4 | `insufficient_evidence` | `process_id` → `step_id` 升序 |

## 4. 金额假设（只用显式输入）

```
月省小时（假设） = 月耗时 × assumed_saving_ratio
月省金额（假设） = 月省小时 × 小时成本
```

- 小时成本取步骤 `hourly_cost`；缺失才回退 `team.default_hourly_cost`；都没有则 `null`。输出里 `hourly_cost_source` 会写明来源是 `STEP` 还是 `TEAM_DEFAULT`。
- `assumed_saving_ratio` 缺失 → 只有 `monthly_hours`，没有金额。
- 金额按 `currency` 分组写入 `time_saving_hypothesis.by_currency`，**没有跨币种总额字段，也不做汇率换算**。
- `time_saving_hypothesis.assumptions` 逐条写明这套算法依赖哪些显式输入。

## 5. 人工检查点

| mode | 触发 | 要求 |
|---|---|---|
| `APPROVE_BEFORE_USE` | `assist` 且命中强制项 | 可以辅助生成草稿，但产出必须人工确认后才能对外使用或进入下一步 |
| `FULLY_MANUAL` | `keep_manual` 且命中强制项 | 整步保持人工，不建议自动化 |
| `WAIT_FOR_EVIDENCE` | `insufficient_evidence` 且命中强制项 | 先补齐耗时、频率与错误率；补齐前不进入任何自动化通道 |

`reasons` 用固定前缀写明触发原因：`HIGH_RISK_TOUCH:<tag>` / `UNKNOWN_TOUCH_TAG` / `PROTECTED_DATA:<level>` / `HIGH_ERROR_CONSEQUENCE` / `USER_DECLARED`。

## 6. 状态 `status`

| 值 | 条件 |
|---|---|
| `REJECTED` | 命中凭据门禁 |
| `INPUT_INCOMPLETE` | `as_of` 非法，或没有流程，或没有任何步骤 |
| `NO_CANDIDATE` | 没有任何步骤判为 `assist` 或 `automate_candidate` |
| `MAPPED` | 其他 |

## 7. 输出结构要点

| 键 | 内容 |
|---|---|
| `counts` | 流程数、步骤数、已排序数与未排序数、`verdict_counts` |
| `steps[]` | 全部步骤及其判定、理由、月耗时、分值、排名 |
| `priority_matrix[]` | 仅已排序步骤 |
| `unranked_steps[]` | 未排序步骤 + 原因 + 说明 |
| `implementation_order[]` | 四档顺序 |
| `manual_only_steps[]` / `insufficient_evidence_steps[]` | 两类步骤单独列出 |
| `human_checkpoints[]` | 人工检查点及模式 |
| `data_permission_prerequisites[]` | 数据与权限前置项 |
| `failure_fallbacks[]` | 逐步骤的失败回退 |
| `time_saving_hypothesis` | `by_step` / `by_currency` / `missing_inputs` / `assumptions` |
| `tool_limits[]` | 本工具明确不做的事 |
| `markdown_summary` | Markdown 决策包，与 JSON 同源 |

## 8. 不适用与已知边界

1. **不生成自动化实现**。不输出 n8n、Zapier、Make、脚本或任何可执行定义；从「判定」到「实现」这一步必须由人来做。
2. **不代替流程访谈**。工具只能判断你交上来的这份清单；如果清单本身就漏了步骤，结论也会跟着漏。
3. **不判断合规性**。不评估你的处理方式是否满足个保法、劳动法、行业监管或合同要求。
4. **不提供法律、医疗、金融或人力资源结论**。这类步骤一律进强制人工审批。
5. **金额是假设不是实测**。`monthly_saving_amount_hypothesis` 完全依赖你提供的节省比例；它不是承诺，也不是收益预测。
6. **一次盘点不是永久结论**。流程变了、人手变了、工具变了，都应重新盘点；`ad_hoc` 的流程尤其应等它稳定后再来。
7. **不评估你团队成员的绩效**，也不做排班或产能建议。
