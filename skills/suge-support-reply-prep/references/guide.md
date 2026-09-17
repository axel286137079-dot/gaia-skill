# 客服应答准备包 · 参考手册

所有规则都是固定的：同一份输入必然得到同一份输出，不含随机性、不含联网、不含任何"按经验补充"的默认值。

## 1. 输入字段表

顶层：`as_of`（必填，ISO8601 且**必须带时区偏移**，如 `2026-09-16T10:00:00+08:00`，缺偏移或缺时间直接拒绝处理）、`sla`（选填对象 `{"first_reply_minutes": 30}`，须为大于 0 的整数；缺失时所有记录首响状态记 `UNKNOWN`、`coverage.sla_source` 为 `absent`）、`policies`（选填数组）、`messages`（必填，至少一条）、`drafts`（选填数组）。

| 对象 | 字段 | 必填 | 说明 |
| --- | --- | --- | --- |
| `policies[]` | `policy_id` | 是 | 政策编号，全批唯一（重复即拒绝处理） |
| `policies[]` | `covers` | 是 | 非空主题码数组，只允许 `REFUND`、`RETURN`、`DAMAGE`、`INVOICE`、`WARRANTY`、`LOGISTICS` |
| `policies[]` | `title` / `text` | 否 | 供人阅读；脚本只引用编号，不解析政策内容 |
| `policies[]` | `effective_from` | 否 | `YYYY-MM-DD`；晚于基准日则该政策未生效 |
| `policies[]` | `expires_at` | 否 | `YYYY-MM-DD` 或 `null`；早于或等于基准日则该政策已失效 |
| `messages[]` | `message_id` | 是 | 工单编号；重复出现时后一条记 `DUPLICATE_MESSAGE_ID` |
| `messages[]` | `received_at` | 是 | ISO8601 带偏移；缺失记 `MISSING_RECEIVED_AT`、无法解析或缺偏移记 `INVALID_RECEIVED_AT`、晚于 `as_of` 记 `FUTURE_RECEIVED_AT` |
| `messages[]` | `text` | 是 | 客户原文；空或全空白记 `EMPTY_TEXT` |
| `messages[]` | `channel` / `customer_ref` / `order_ref` | 否 | 渠道、客户编号、订单号；空白按缺失处理 |
| `messages[]` | `intake` | 否 | `{"has_attachment": true}` 是 `damage_evidence` 是否满足的唯一口径 |
| `drafts[]` | `message_id` / `text` | 是 | 必须指向已存在的工单（否则拒绝处理）；`text` 非空 |

## 2. 输出字段表

顶层：

| 字段 | 说明 |
| --- | --- |
| `status` / `as_of` | 全批结论：`INVALID` > `ESCALATION_REQUIRED` > `SLA_BREACH` > `INFO_MISSING` > `POLICY_GAP` > `READY`；同时回显基准时间 |
| `message_count` / `unique_message_id_count` | 记录条数与唯一 `message_id` 数（存在重复编号时两者不同） |
| `status_counts` / `priority_counts` | 五个状态与 P0–P3 的计数，未出现的键补 0 |
| `escalation_count` / `info_missing_count` / `policy_gap_count` / `sla_breach_count` / `sla_at_risk_count` | 分别为 `ESCALATE`、`NEEDS_INFO`、`POLICY_MISSING` 的记录数与首响超时、临界的记录数 |
| `injection_flagged` / `coverage` | 命中提示注入的编号；`sla_source` 取 `user` 或 `absent` |
| `policy_coverage` | `required_topics`、`covered_topics`、`gaps`、`expired_policy_ids`、`inactive_policy_ids` |
| `messages` | 逐条结果，见下表 |
| `draft_checks` / `draft_status_counts` / `next_actions` | 草稿检查明细、`PASS`/`REVISE`/`BLOCK` 计数与逐条 `{action, priority, message_id}` |
| `markdown_summary` / `disclaimer` | 中文摘要表与使用边界 |

`messages[]` 单条：`message_id`、`row_index`（从 1 开始的输入序号）、`channel`、`customer_ref`、`order_ref`、`intent`、`priority`（P0–P3）、`status`（五种状态）、`received_at`、`elapsed_minutes`（字符串，2 位小数，无效记录为 `null`）、`first_reply_due`、`sla_state`（`OK`/`AT_RISK`/`BREACH`/`UNKNOWN`）、`required_topics`、`policy_refs`、`policy_gaps`、`blocking_fields`、`escalation_reasons`、`injection_flags`、`review_flags`、`must_not_say`、`suggested_reply_skeleton`。

## 3. 意图关键词表与固定优先级

按下列顺序逐条匹配归一化文本（全角转半角 + 转小写）的**子串**，首个命中即判定，不再往下看。

| 顺序 | 意图 | 关键词 |
| --- | --- | --- |
| 1 | `ACCOUNT_SECURITY` | 账号、登录、被盗、验证码、密码、冻结、解封 |
| 2 | `REFUND_RETURN` | 退款、退货、退钱、七天无理由、退一赔 |
| 3 | `DAMAGE_QUALITY` | 破损、坏了、质量问题、少件、漏发、发错、瑕疵、与描述不符 |
| 4 | `INVOICE_BILLING` | 发票、开票、收据、账单、抬头、税号 |
| 5 | `WARRANTY` | 保修、保期、质保、维修、售后维修 |
| 6 | `PRICE_PROMO` | 价格、优惠、差价、活动价、补差价、降价 |
| 7 | `LOGISTICS` | 物流、快递、什么时候到、没收到、发货、运费、签收 |
| 8 | `OTHER` | 兜底，无关键词 |

## 4. 升级原因表

命中任意一条即判 `ESCALATE`（P0），必须由人接手后才对外表达。

| 原因码 | 触发条件 |
| --- | --- |
| `MONEY_COMMITMENT_RISK` | 意图为 `REFUND_RETURN` 或 `PRICE_PROMO`，或文本含 赔偿、赔付、补钱、差价、退一赔 |
| `ACCOUNT_SECURITY` | 意图为 `ACCOUNT_SECURITY` |
| `LEGAL_OR_REGULATOR` | 文本含 律师、起诉、法院、仲裁、12315、工商、消协、投诉到 |
| `PUBLIC_RELATION_RISK` | 文本含 曝光、发小红书、差评、微博、维权 |
| `ABUSE_OR_THREAT` | 文本含 骗子、垃圾、傻、滚、死、威胁、举报你们 |

## 5. 必需主题表与阻塞字段表

| 意图 | 必需政策主题（覆盖检查用） | 阻塞字段（缺失即必须先追问） |
| --- | --- | --- |
| `REFUND_RETURN` | `REFUND`、`RETURN` | `order_ref` |
| `DAMAGE_QUALITY` | `DAMAGE` | `order_ref`、`damage_evidence`（仅当 `intake.has_attachment` 为真才算满足） |
| `INVOICE_BILLING` | `INVOICE` | `order_ref` |
| `WARRANTY` | `WARRANTY` | 无 |
| `LOGISTICS` | `LOGISTICS` | `order_ref` |
| `PRICE_PROMO` | 无 | `order_ref` |
| `ACCOUNT_SECURITY` | 无 | `customer_ref` |
| `OTHER` | 无 | 无 |

`blocking_fields` 按本表列出的固定顺序输出（`order_ref` 在前、`damage_evidence` 在后），不是字符串排序。

## 6. 状态判定顺序与优先级

| 顺序 | 状态 | 条件 |
| --- | --- | --- |
| 1 | `INVALID` | 重复 `message_id`、`received_at` 晚于基准时间、缺失/无法解析/naive 的时间、空文本、含控制字符 |
| 2 | `ESCALATE` | 第 4 节任一升级原因命中 |
| 3 | `POLICY_MISSING` | 存在未被任何生效政策覆盖的必需主题 |
| 4 | `NEEDS_INFO` | 存在缺失的阻塞字段 |
| 5 | `READY` | 以上都不成立，可按依据回复 |

优先级：状态为 `INVALID`/`ESCALATE`，或首响已超时 → P0；状态为 `NEEDS_INFO`/`POLICY_MISSING`，或首响临界 → P1；首响状态 `UNKNOWN` → P2；其余 → P3。

`priority_counts` 按唯一 `message_id` 的**首次记录**统计（重复记录仍计入 `status_counts` 与 `messages[]`）。`messages[]` 排序为：优先级升序 → 收到时间升序 → `message_id` 升序；其中 `INVALID` 记录排在 P0 组末尾并按 `message_id` 升序。

## 7. 首响 SLA 判定

- 用时 = （`as_of` − `received_at`）的分钟数，用 `Decimal` 计算并四舍五入保留 2 位小数；临界线 = `first_reply_minutes × 80 ÷ 100`（同样保留 2 位）。
- `elapsed >= first_reply_minutes` → `BREACH`；否则 `elapsed >= 临界线` → `AT_RISK`；否则 `OK`；`first_reply_due` = `received_at` + `first_reply_minutes`，输出与原记录相同时区偏移的 ISO8601。
- `sla` 缺失或该记录 `INVALID` 时不计算用时（`elapsed_minutes` 与 `first_reply_due` 为 `null`，状态记 `UNKNOWN`）；SLA 30 分钟时临界线为 24.00，用时 25.00 分钟判 `AT_RISK`、30.00 分钟判 `BREACH`（等号算超时）。

## 8. 政策覆盖与缺口

- 生效判定：`effective_from <= as_of 日期`（缺省视为一直有效），且 `expires_at` 为 `null` 或 `> as_of 日期`；已失效政策进 `policy_coverage.expired_policy_ids`，尚未生效政策进 `inactive_policy_ids`，两者都不覆盖任何主题。
- 一条记录的 `policy_refs` = **同时覆盖它全部必需主题**的生效政策编号（升序）；必需主题为空时 `policy_refs` 为空数组；`policy_gaps` = 没有任何生效政策覆盖的必需主题（升序），`policy_coverage.gaps` 是全批并集，而 `policy_gap_count` 统计的是状态为 `POLICY_MISSING` 的记录数（已被升级等更高优先级状态覆盖的记录只体现在主题并集里）。

## 9. 草稿检查判定表

| 检查项 | 判定方式 |
| --- | --- |
| `banned_terms` | 草稿归一化文本中命中的禁用词：保证、一定、绝对、100%、免费、全额退款、马上赔、立刻退款、永久、无条件 |
| `unresolved_placeholders` | 命中的 `【待补:…】`、`{{…}}`、`[待确认]` |
| `missing_policy_basis` | 该工单必需主题非空，且草稿中没有出现覆盖这些主题的生效政策编号的 `[依据:P-…]` |

| 草稿状态 | 条件 |
| --- | --- |
| `BLOCK` | 所引用工单状态为 `ESCALATE`，或含禁用词 |
| `REVISE` | 存在未完成占位符，或缺少政策依据 |
| `PASS` | 以上都不成立 |

## 10. 输出的阅读顺序

1. 顶层 `status`：`INVALID` 表示这批数据没有一条能用；`ESCALATION_REQUIRED` 表示有人必须先接手。
2. `status_counts` / `priority_counts` / `sla_breach_count` / `sla_at_risk_count`：先看 P0 有多少条，超时的先回、临界的先问。
3. `policy_coverage.gaps`：缺口是流程问题，会反复制造同类工单，值得单独修。
4. `messages[]`：按输出顺序自上而下处理；每条看 `status` → `escalation_reasons` → `blocking_fields` → `policy_refs` → `must_not_say` → `suggested_reply_skeleton`。
5. `draft_checks` / `next_actions` / `disclaimer`：`BLOCK` 直接重写、`REVISE` 补齐后再审；动作分派出去；把准备件当成结论之前先读一遍免责说明。

## 11. 隐私、安全与边界

- 输入对象里出现密码、口令、令牌、私钥、连接串等字段名，或出现 `sk-`、`AKIA`、`ghp_`/`gho_`、`xox`、JWT、私钥/证书样式字符串时，脚本**立即拒绝处理且不回显该内容**。
- 文本含控制字符的记录判 `INVALID`，不中断整批；提示注入类措辞只加标记，规则与结论都不因此改变；输出不含金额、赔付、时限与责任表述；本技能**不发送消息、不生成政策、不批准退款**，所有对外表达由有权限的人员确认后发出。
