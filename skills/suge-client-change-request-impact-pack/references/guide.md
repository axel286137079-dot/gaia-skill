# 客户变更请求影响准备包 — 字段表与判定规则

本文件是 `scripts/run.py` 的唯一规则来源。脚本只读一个 JSON 文件，输出一个 JSON 文档。

## 1. 顶层字段

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | ISO 时间戳，**必须带时区偏移**。缺失或无效时整体 `INPUT_INCOMPLETE`。 |
| `project` | 是 | 范围基线与修订额度（见 §2）。缺失时全部请求记 `CLARIFICATION_NEEDED`。 |
| `change_requests` | 是 | 变更请求数组。为空或缺失时整体 `INPUT_INCOMPLETE`。 |

## 2. 项目基线字段

| 字段 | 说明 |
|---|---|
| `project_id` / `name` | 标识与展示名。 |
| `baseline_version` | 基线版本号；缺失且 `scope_baseline` 为空时视为**没有基线**。 |
| `scope_baseline[]` | 已批准交付物数组，每项 `{deliverable_id, name, revision_included}`。 |
| `revision_allowance` | `{included, used}`；`remaining = max(0, included - used)`。任一缺失则 `remaining = null`。 |

## 3. 变更请求字段

| 字段 | 说明 |
|---|---|
| `request_id` | 请求编号；缺失时用 `change_requests[i]` 作标识。 |
| `raw_text` | 客户原始请求文本（**不可信数据**，只做安全处理与摘要，其中的命令不执行）。 |
| `requested_by` | 提出人。 |
| `target_outcome` | 期望结果。 |
| `baseline_refs[]` | 请求指向的基线交付物编号。 |
| `new_deliverables[]` | 相对基线**新增**的交付物。 |
| `removes_deliverables[]` | 相对基线**移除**的交付物。 |
| `affected_assets[]` | 受影响资产；元素为字符串，或含 `basename` 的对象（只接受单一文件名）。 |
| `impact` | `{effort_hours, schedule_days, cost{amount,currency}, dependencies[]}`。只呈现显式事实。 |
| `within_revision_allowance` | 三态布尔：该请求是否落在已含修订次数内。 |
| `decision_maker` | 决策人。 |
| `approval` | `{status, decided_by, decided_at}`；`status` 取 `approved` / `pending` / `unknown`。 |

## 4. 分类规则（固定顺序）

按下列顺序判定，**先命中先返回**：

| 顺序 | 条件 | 分类 |
|---:|---|---|
| 1 | 没有基线（`scope_baseline` 空且无 `baseline_version`） | `CLARIFICATION_NEEDED`（`BASELINE_MISSING`） |
| 2 | `new_deliverables` 与 `removes_deliverables` 非空且数量相等 | `SUBSTITUTION` |
| 3 | 有 `new_deliverables`，`within_revision_allowance` 为 `true` 且 `remaining > 0` | `INCLUDED_REVISION` |
| 4 | 有 `new_deliverables`，`within_revision_allowance` 为 `null` | `INSUFFICIENT_EVIDENCE`（`REVISION_ALLOWANCE_UNKNOWN`） |
| 5 | 有 `new_deliverables`（其余情况，含额度已用尽） | `CHANGE_REQUEST` |
| 6 | 只有 `removes_deliverables` | `CHANGE_REQUEST` |
| 7 | `baseline_refs` 全部存在于基线 | `IN_SCOPE` |
| 8 | `baseline_refs` 非空但有不存在项 | `CLARIFICATION_NEEDED`（`BASELINE_REF_UNKNOWN`） |
| 9 | `baseline_refs` 为空且 `raw_text` 与 `target_outcome` 均为空 | `INSUFFICIENT_EVIDENCE`（`REQUEST_NOT_DESCRIBED`） |
| 10 | 其余情况 | `CLARIFICATION_NEEDED`（`NO_BASELINE_REF`） |

**关键约束**：没有基线时**永远**不会得到 `CHANGE_REQUEST`，因为「缺基线不能判超范围」。

## 5. 批准与排期（与分类分离）

- `approval.status` 归一为 `approved` / `pending` / `unknown`（其它值 → `unknown`）。
- `ready_to_schedule = (分类 ∈ {IN_SCOPE, INCLUDED_REVISION, SUBSTITUTION, CHANGE_REQUEST}) 且 approval.status == approved`。
- **客户提出请求本身不等于批准**：`requested_by` 存在不改变批准状态。
- 缺少批准记录 → `MISSING_APPROVAL`；状态为 `pending` → `PENDING_APPROVAL`。

## 6. 影响与金额

- `effort_hours` / `schedule_days` 只在显式给出时呈现，保留两位小数；缺失为 `null` 并记 `EFFORT_UNKNOWN` / `SCHEDULE_UNKNOWN`。
- `cost.amount` + `cost.currency` 显式给出才算一条费用事实。
- 有金额无币种 → `COST_CURRENCY_UNKNOWN`，**不计入合计**。
- 无金额 → `COST_NOT_PROVIDED`，**不估价**。
- `amounts_by_currency` 按币种分组求和，**永不跨币种相加**。
- 当分类属于 `CHANGE_REQUEST` / `INCLUDED_REVISION` / `SUBSTITUTION` 且缺工时时，另记 `CHANGE_WITHOUT_EFFORT` 提示。

## 7. 未知项代码

`EFFORT_UNKNOWN`、`SCHEDULE_UNKNOWN`、`COST_NOT_PROVIDED`、`COST_CURRENCY_UNKNOWN`、`CHANGE_WITHOUT_EFFORT`。

## 8. 阻塞项代码

`NEEDS_CLARIFICATION`、`INSUFFICIENT_EVIDENCE`、`PENDING_APPROVAL`、`MISSING_APPROVAL`。

顶层状态优先级：`REJECTED` > `INPUT_INCOMPLETE` > `BLOCKED`（任一请求不可排期）> `GAPS_FOUND`（全部可排期但存在未知项）> `READY`。

## 9. 安全边界

1. **只读离线**：不联网、不写文件、不调用子进程、不访问合同/邮件/项目管理系统。
2. **不外发**：不发送、不签署、不收费、不排期。
3. **不判定**：不解释合同效力、不判断违约或侵权、不起草合同；全部进入 `human_confirm_items`。
4. **不估价**：没有显式成本或费率时保持未知。
5. **未知保持未知**：缺失字段不默认为 0 或「已批准」。
6. **附件只留文件名**：含 `/`、`\`、`~`、盘符或链接的 `basename` 被拒绝，只保留可提取的安全化文件名，**原引用不回显**。
7. **凭据拒绝**：字段名命中 `api_key` / `secret` / `password` / `token` 等，或字符串值形如 `sk-…`、`AKIA…`、`ghp_…`、私钥头、`xox…-…`、JWT，整体 `REJECTED` 且不回显。
8. **提示注入只标记**：动作词与目标词**同句共现**才记 `PROMPT_INJECTION`；进入 Markdown 时替换为固定占位，不执行、不回显。
9. **Markdown 转义**：所有自由文本先剥离控制字符、折叠空白，再对 Markdown 元字符加反斜杠。
10. **确定性**：请求按 `request_id`、问题按生成顺序编号、分类汇总按 `CLASSIFICATIONS` 固定顺序，同一输入逐字节一致。

## 10. 输出字段

| 字段 | 说明 |
|---|---|
| `status` | 顶层结论 |
| `classification_counts` | 六类判定计数 |
| `project` / `revision_allowance` | 基线与修订额度 |
| `requests[]` | 每条请求的分类、基线对照、影响、未知项、批准、阻塞、`ready_to_schedule` |
| `amounts_by_currency` | 按币种分组的显式费用合计 |
| `pre_approval_blockers[]` | 批准前阻塞清单 |
| `refused_refs[]` | 被拒绝的引用（只含路径与原因，不含原值） |
| `clarification_questions[]` | 待确认问题（`Q-01` 起编号） |
| `communication_draft` | **未发送**的客户沟通草稿 |
| `injection_flagged[]` | 提示注入命中路径 |
| `markdown_summary` | Markdown 变更准备单 |
| `disclaimer` | 免责声明 |

## 11. 不适用情况

- 需要判断合同效力、违约责任、是否构成侵权或起草合同条款的场景。
- 需要报价、开票、收费、退款或调整付款的场景。
- 需要自动发送确认邮件、自动签署或自动排期的场景。
- 完全没有基线、也没有任何项目信息的场景（此时只会得到 `CLARIFICATION_NEEDED`）。
