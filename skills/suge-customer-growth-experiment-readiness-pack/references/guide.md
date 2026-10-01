# 客户增长实验准备包 — 字段表与判定规则

本文件是 `scripts/run.py` 的唯一规则来源。脚本只读一个 JSON 文件，输出一个 JSON 文档。

## 1. 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | ISO 时间戳，**必须带时区偏移**（如 `2026-09-30T07:30:00+08:00`）。缺失或格式无效时整体 `INPUT_INCOMPLETE`。 |
| `business` | 否 | `{name, timezone}`，只用于简报抬头展示，不参与判定。 |
| `experiments` | 是 | 实验数组。为空或缺失时整体 `INPUT_INCOMPLETE`。 |

顶层其它未知字段一律忽略，不参与判定。

## 2. 实验字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `experiment_id` | 建议 | 实验编号；缺失时输出用 `experiments[i]` 作为标识。 |
| `goal` | 是 | 想改变什么。缺失记 `NO_GOAL`。 |
| `audience` | 是 | 面向谁。缺失记 `NO_AUDIENCE`。 |
| `channel` | 是 | 渠道，必须来自固定词表（见 §6）。缺失记 `UNKNOWN_CHANNEL`；表外值记 `INVALID_CHANNEL`。 |
| `offer` | 是 | 给对方的钩子。缺失记 `NO_OFFER`。 |
| `baseline` | 是 | 基线数字。**缺失不推算**，记 `NO_BASELINE`；给了但不是数值记 `INVALID_BASELINE`。 |
| `target` | 是 | 目标数字。缺失记 `NO_TARGET`；给了但不是数值记 `INVALID_TARGET`。 |
| `metric` | 是 | 指标。可以是字符串（只给指标名）或 `{name, definition}`。缺指标名记 `UNKNOWN_METRIC`；缺 `definition` 只记提示 `METRIC_DEFINITION_MISSING`。 |
| `budget_cap` | 是 | `{amount, currency}`。缺 `amount` 记 `NO_BUDGET`（**不按 0**）；有金额无币种记 `UNKNOWN_BUDGET_CURRENCY`；金额为负数记 `NEGATIVE_BUDGET`。 |
| `start_at` / `end_at` | 是 | 起止时间，带时区偏移。两者都缺记 `NO_WINDOW`；有一项存在但不可解析（含缺偏移）记 `INVALID_WINDOW`；都能解析且 `end <= start` 记 `INVALID_EXPERIMENT_WINDOW`。 |
| `owner` | 是 | 责任人；缺失记 `UNKNOWN_OWNER`。 |
| `outreach_required` | 是 | 三态布尔：是否需要对外触达。缺失记 `UNKNOWN_OUTREACH_REQUIREMENT`。 |
| `consent_basis` | 视情况 | 同意依据文本。`outreach_required` 为 `true` 且本字段为空时记 `NO_CONSENT_BASIS`，并**不产生任何外发动作**。 |
| `evidence_refs` | 否 | 证据引用数组，**只接受单一文件名**。 |
| `stop_conditions` | 是 | 停止条件数组。为空记 `NO_STOP_CONDITIONS`。 |
| `notes` | 否 | 自由文本备注。 |

## 3. 判定顺序（固定）

对每个实验自上而下取第一个命中的分支：

| # | 条件 | 结果 |
|---:|---|---|
| 1 | 记录不是对象 | **`BLOCKED`**（`INVALID_EXPERIMENT_RECORD`） |
| 2 | `channel` 写了表外值 | **`BLOCKED`**（`INVALID_CHANNEL`） |
| 3 | 起止都能解析且 `end <= start` | **`BLOCKED`**（`INVALID_EXPERIMENT_WINDOW`） |
| 4 | `budget_cap.amount < 0` | **`BLOCKED`**（`NEGATIVE_BUDGET`） |
| 5 | `baseline` 与 `target` 都能解析且 `target <= baseline` | **`BLOCKED`**（`TARGET_NOT_ABOVE_BASELINE`） |
| 6 | 存在任一缺失事实（见 §4） | **`INSUFFICIENT_EVIDENCE`** |
| 7 | 存在任一准备门禁（见 §5） | **`ACTION_NEEDED`** |
| 8 | 其余 | **`READY`** |

**关键点**：第 2–5 步是**硬矛盾**，工具不替你选一个数字；第 6 步是**缺事实**，必须先追问；第 7 步是**开始前就能补齐的准备项**。

## 4. 缺失事实（`INSUFFICIENT_EVIDENCE`）

按下表固定顺序输出到 `unknowns`：

| 代码 | 触发条件 |
|---|---|
| `NO_GOAL` | `goal` 缺失或为空 |
| `NO_AUDIENCE` | `audience` 缺失或为空 |
| `NO_OFFER` | `offer` 缺失或为空 |
| `NO_BASELINE` | `baseline` 未提供 |
| `INVALID_BASELINE` | 提供了 `baseline` 但不是可比较数值 |
| `NO_TARGET` | `target` 未提供 |
| `INVALID_TARGET` | 提供了 `target` 但不是可比较数值 |
| `UNKNOWN_METRIC` | 没有指标名 |
| `UNKNOWN_CHANNEL` | `channel` 未提供 |
| `UNKNOWN_OWNER` | `owner` 缺失 |
| `NO_WINDOW` | `start_at` 与 `end_at` 都未提供 |
| `INVALID_WINDOW` | 有时间值但不可解析或缺少时区偏移 |
| `NO_BUDGET` | `budget_cap.amount` 未提供 |
| `UNKNOWN_BUDGET_CURRENCY` | 有金额但没有币种 |
| `UNKNOWN_OUTREACH_REQUIREMENT` | `outreach_required` 不是布尔值 |

## 5. 准备门禁（`ACTION_NEEDED`）

事实齐全后检查；按下表固定顺序输出到 `review_flags`：

| 代码 | 触发条件 | 是否驱动 `ACTION_NEEDED` |
|---|---|---|
| `NO_STOP_CONDITIONS` | `stop_conditions` 为空 | 是 |
| `NO_CONSENT_BASIS` | `outreach_required` 为 `true` 且 `consent_basis` 为空 | 是 |
| `WINDOW_IN_PAST` | `end_at` 早于 `as_of` | 是 |
| `METRIC_DEFINITION_MISSING` | 指标没有 `definition`，只有名字 | **否**（只提示，不影响结论） |

## 6. 固定渠道词表

大小写不敏感；表外值记 `INVALID_CHANNEL`：

```
content / email / sms / paid_ads / social / referral / in_store / phone / event / other
```

## 7. 顶层结论

- 存在任一 `BLOCKED` → `BLOCKED`
- 否则存在任一 `ACTION_NEEDED` 或 `INSUFFICIENT_EVIDENCE` → `GAPS_FOUND`
- 否则 → `READY`

另有 `REJECTED`（疑似凭据）与 `INPUT_INCOMPLETE`（缺 `as_of` 或实验）两个前置状态。

## 8. 外发与投放口径

```
outreach_plan[]     仅当 outreach_required 为 true、consent_basis 非空、
                    窗口未结束、且结论为 READY 或 ACTION_NEEDED 时生成；
                    status 恒为 DRAFT_NOT_SENT（永不发送）
do_not_outreach[]   其余 outreach_required 为 true 的实验，附原因
                    NO_CONSENT_BASIS / WINDOW_IN_PAST / NOT_PREPARED
```

工具在任何情况下都**不生成可发送、可投放、可导出的动作**；`no_send_declaration` 与 `no_spend_declaration` 是两个固定声明字段。

## 9. 金额与时间口径

- `baseline` / `target` / `budget_cap.amount` 使用 `Decimal`，输出统一为两位小数字符串；`target_delta` 为 `target - baseline`，仅在两者都可解析时给出。
- `budgets_by_currency` 按币种分别合计，**不换算、不跨币种合计**；缺金额或缺币种的实验不进入任何币种合计。
- 时间必须是带偏移的 ISO 8601（`Z` 或 `±HH:MM`）；不带偏移的本地时间视为无效。
- `window_in_past` 用绝对时刻比较：`end_at < as_of`。`business.timezone` 只作为展示依据。

## 10. 安全边界

1. **只读离线**：不联网、不写文件、不调用子进程、不访问广告平台 / CRM / 名单 / 店铺后台。
2. **不外发、不花钱**：不发送、不投放、不建广告、不导名单、不下单、不绑定账号、不删除原始材料。
3. **不承诺效果**：不输出增长预测、成功概率、收益或「提高销量」一类结论。
4. **未知保持未知**：任何缺失字段都不默认为 0、「已完成」或「没有影响」。
5. **附件只留文件名**：`evidence_refs` 只接受单一文件名；含 `/`、`\`、`~`、盘符或链接的引用被拒绝，输出只保留安全化后的文件名（若可提取），**原引用不回显**。
6. **凭据拒绝**：字段名命中 `api_key` / `secret` / `password` / `token` 等，或字符串值形如 `sk-…`、`AKIA…`、`ghp_…`、私钥头、`xox…-…`、JWT，整体 `REJECTED` 且不回显。
7. **提示注入只标记**：动作词与目标词**同句共现**才记 `PROMPT_INJECTION`；进入 Markdown 时替换为固定占位「已隐藏疑似提示注入文本」，不执行、不回显。
8. **Markdown 转义**：所有自由文本先剥离控制字符、折叠空白，再对 Markdown 元字符加反斜杠。
9. **确定性**：实验按 `experiment_id`、看板按编号、问题按生成顺序编号、状态汇总按 `EXPERIMENT_STATES` 固定顺序，同一输入逐字节一致。

## 11. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | 顶层结论 |
| `status_counts` | 四种实验结论的计数 |
| `experiments[]` | 每个实验的完整判定（含 `blockers` / `unknowns` / `review_flags` / `reasons` / `window` / `budget`） |
| `experiment_cards[]` | 实验卡：目标、受众、渠道、钩子、指标与口径、基线→目标、`target_delta`、预算上限、窗口与天数、责任人、停止条件 |
| `ready_board[]` / `action_needed_board[]` / `blocked_board[]` / `insufficient_board[]` | 四类结论的独立视角（均按编号排序） |
| `missing_facts[]` | 缺失事实汇总 |
| `budgets_by_currency` | 按币种的上限合计（不换算、不跨币种合计） |
| `budget_currency_note` | 币种口径说明 |
| `outreach_plan[]` | 外发草稿（恒 `DRAFT_NOT_SENT`） |
| `do_not_outreach[]` | 不会外发的实验与原因 |
| `refused_refs[]` | 被拒绝的引用（只含路径与原因，不含原值） |
| `human_confirm_items[]` | 必须由人工确认的事项 |
| `clarification_questions[]` | 待确认问题（`Q-01` 起编号） |
| `injection_flagged[]` | 提示注入命中路径 |
| `input_warnings[]` | 输入层提示 |
| `no_send_declaration` / `no_spend_declaration` | 不可外发 / 不可投放声明 |
| `markdown_summary` | Markdown 客户增长实验准备板 |
| `disclaimer` | 免责声明 |

## 12. 不适用情况

- 需要自动投放、自动发消息、自动导名单、自动改价或自动收款阈值的场景。
- 需要输出增长预测、ROI 承诺、成功概率或「保证提高销量」的场景。
- 需要写入广告平台、CRM、名单或店铺后台的场景。
- 事实完全缺失、只有一句「帮我搞点客户增长」的场景。
