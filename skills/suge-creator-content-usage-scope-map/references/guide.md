# 创作者内容使用范围地图 — 字段与判定规则

本文件是 `scripts/run.py` 的完整规则说明。脚本只读一个本地 JSON，向 stdout 输出一个 JSON；不联网、不写文件、**不打开任何附件**，只处理用户写下的文字与文件名。

> **定位**：本工具只把「哪份资产、谁可以用、在哪些渠道与地域、做什么用途、从哪天到哪天、能不能改、要不要署名、到期怎么办」这些**已经被写下来的事实**整理成矩阵并做一致的比对。它**不解释法律效力、不起草合同、不判断是否侵权**，也不会替你向任何平台授权。

## 1. 输入字段

### 1.1 顶层

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | 基准时间，**必须带时区偏移**。缺失或不可解析 → `INPUT_INCOMPLETE`。基准日取其本地日期 |
| `collaboration` | 建议 | `{ collab_id, brand, creator }` |
| `assets[]` | 建议 | 见 1.2 |
| `grants[]` | 是 | 使用范围记录，见 1.3。缺失或空数组 → `INPUT_INCOMPLETE` |
| `requests[]` | 可选 | 新的使用请求，见 1.4 |

### 1.2 `assets[]`

| 字段 | 说明 |
|---|---|
| `asset_id` | 资产编号。缺失 → `MISSING_ASSET_ID`，该记录判无效 |
| `basename` | **只允许单一文件名**，见 §4.1。含路径或链接 → 拒绝并只保留文件名 |
| `kind` | 资产类型（`video` / `image` / `audio` / `graphics` / `other`，仅作展示） |
| `source_note` | 来源说明（仅作展示） |

### 1.3 `grants[]`

| 字段 | 说明 |
|---|---|
| `grant_id` | 范围编号。缺失 → `MISSING_GRANT_ID`；**重复出现 → 整体 `BLOCKED`** |
| `asset_id` | 必须存在于 `assets[]`，否则 `DANGLING_ASSET_ID`（整体 `BLOCKED`） |
| `authorized_parties[]` | **允许使用主体**（品牌方自有账号、指定代理商、合作伙伴等）。空 → `UNKNOWN_SCOPE_AUTHORIZED_PARTIES` |
| `channels[]` | **允许渠道**。空 → `UNKNOWN_SCOPE_CHANNELS` |
| `territory[]` | **允许地域**。空 → `UNKNOWN_SCOPE_TERRITORY` |
| `purposes[]` | **允许用途**。常用值 `organic`（自然发布）/ `paid_ads`（付费投放）/ `website` / `email` / `retail_screen` / `whitelisting`，也接受合作双方自己的写法。空 → `UNKNOWN_SCOPE_PURPOSES` |
| `modifications[]` | **允许的修改方式**。常用值 `crop`（裁剪）/ `caption`（加字幕）/ `translate` / `re_edit`（二次剪辑）/ `raw_footage`（原始素材）/ `altered_audio`（改音）。键缺失或空 → `MODIFICATIONS_UNKNOWN` |
| `starts_on` / `ends_on` | `YYYY-MM-DD`；不可解析 → `INVALID_START_DATE` / `INVALID_END_DATE`（`BLOCKED`）；`starts_on > ends_on` → `INVALID_DATE_RANGE`（`BLOCKED`）；`starts_on` 缺失 → `MISSING_START_DATE`；`ends_on` 缺失 → `END_DATE_UNKNOWN` |
| `attribution_required` / `attribution_text` | `true` 但没有署名文案 → `ATTRIBUTION_TEXT_MISSING`；键缺失或 `null` → `ATTRIBUTION_REQUIREMENT_UNKNOWN` |
| `renewal_note` | 到期后如何处理。缺失 → `RENEWAL_NOT_STATED` |
| `source_evidence` | 依据来自哪份沟通或文件（**只写名称**）。缺失 → `SOURCE_EVIDENCE_NOT_STATED` |
| `notes` | 备注（自由文本，仅作数据） |

### 1.4 `requests[]`（新请求）

`{ request_id, asset_id, party, channel, territory, purpose, modifications[], planned_on, note }`。
`request_id` 缺失，或 `planned_on` 给出但不可解析 → 该记录判无效（`BLOCKED`）。

## 2. 判定规则

### 2.1 时间状态（每条范围记录一个）

基准日 = `as_of` 的本地日期。

| 时间状态 | 条件 |
|---|---|
| `NOT_YET_STARTED` | `starts_on > 基准日` |
| `EXPIRED` | `ends_on < 基准日` |
| `ACTIVE_ENDS_TODAY` | `ends_on == 基准日`（**当天仍算生效**，只提示） |
| `EXPIRING_SOON` | 剩余天数 ≤ 30 |
| `ACTIVE` | 剩余天数 > 30 |
| `ACTIVE_END_UNKNOWN` | 给了开始日期但没有结束日期 |

**`ends_on` 缺失一律记为「结束日未知」，绝不读作永久授权，也绝不读作「全渠道」。**

### 2.2 记录状态（每条范围记录一个）

按优先级取第一个命中：`CONFLICT` > `INVALID` > `EXPIRED` > `INCOMPLETE` > `DEFINED`。

- `CONFLICT`：该记录与**同一资产的另一条记录**在重叠时间段内存在范围差异（见 2.3）。
- `INVALID`：编号/资产缺失、日期不可解析或起晚于止。
- `EXPIRED`：窗口已过。
- `INCOMPLETE`：任一范围字段未知，或修改方式 / 署名要求 / 开始日期 / 结束日期未知。
- `DEFINED`：以上都没有。

### 2.3 冲突检测

对**同一 `asset_id`** 的任意两条记录：若两者日期区间**有重叠**，且 `authorized_parties` / `channels` / `territory` / `purposes` / `modifications` 中任一字段**双方都有值且取值不同**，记一条 `conflicts[]`，两条记录状态均为 `CONFLICT`。

- 只有一方有值、另一方为空 → 不算冲突（那是「未知」，走澄清问题）。
- 任一方的日期不完整（缺开始或结束）→ 无法比较，记入 `unknown_overlap_pairs[]` 并追问，**不**据此判冲突。

**范围冲突不阻塞整体**：冲突正是需要人来裁决的事，工具把它摆出来并提问，而不是替用户选一条。

### 2.4 新请求核对

对每个请求，取该资产**所有可读记录**，先按 `planned_on` 筛选出覆盖该日期的记录；然后逐字段比对：

| 单字段结果 | 条件 |
|---|---|
| `IN_SCOPE` | 请求值在该记录的登记值列表内 |
| `OUT_OF_SCOPE` | 记录有登记值，但请求值不在其中 |
| `CANNOT_CHECK_UNKNOWN_SCOPE` | 该记录此字段为空（未登记） |

请求整体结果：

| 结果 | 条件 |
|---|---|
| `COVERED` | 至少一条记录所有请求字段都 `IN_SCOPE`，且该记录不处于冲突 |
| `COVERED_BUT_CONFLICTED` | 结论为覆盖，但所依据的记录本身与他条冲突——**必须先裁决** |
| `OUT_OF_SCOPE` | 没有覆盖记录，且至少一条记录出现 `OUT_OF_SCOPE` |
| `UNKNOWN_CANNOT_CHECK` | 无法比对（登记信息不足，或该资产没有任何范围记录） |
| `NO_ACTIVE_WINDOW` | 该资产有范围记录，但没有任何窗口覆盖 `planned_on` |

**五种结果都不是法律结论**，只是「与登记内容是否一致」。

### 2.5 到期时间线

按 `ends_on` 升序（无结束日的排在最后，同值按 `grant_id`）分桶：`expired`（已到期）、`within_30_days`、`within_60_days`、`within_90_days`、`unknown_end`（结束日未知）、`not_yet_started`（尚未生效）。

### 2.6 整体状态

按顺序判定，先命中先生效：

| 状态 | 触发条件 |
|---|---|
| `REJECTED` | 输入出现疑似凭据，**拒绝且不回显** |
| `BLOCKED` | 重复范围编号 / 指向未登记资产 / 被拒附件引用 / 无效资产记录 / 无效请求记录 / 日期不可解析 / 起晚于止 |
| `INPUT_INCOMPLETE` | `as_of` 缺失，或 `grants[]` 缺失为空 |
| `GAPS_FOUND` | 存在未知范围字段、冲突、无法比较的重叠、署名缺口、未写续期、未写来源、无范围记录的资产、结束日未知、尚未生效记录，或存在需人工处理的新请求 |
| `READY` | 以上都没有 |

## 3. 输出字段

`version`、`status`、`as_of`、`as_of_date`、`collaboration`、`grant_count`、`asset_count`、`assets[]`、`status_counts`、`scope_matrix[]`、`conflicts[]`、`unknown_overlap_pairs[]`、`expired_grants`、`expiring_soon`、`ends_today`、`end_date_unknown`、`not_yet_started`、`expiry_timeline{}`、`over_scope_candidates[]`、`all_requests[]`、`attribution_gaps`、`renewal_not_stated`、`source_evidence_not_stated`、`orphan_assets`、`dangling_grants`、`duplicate_grant_ids[]`、`refused_refs[]`、`clarification_questions[]`、`injection_flagged[]`、`input_warnings[]`、`markdown_summary`、`disclaimer`。

阅读顺序：`status` → `conflicts` → `expiry_timeline` → `over_scope_candidates` → `scope_matrix` → `clarification_questions`。

## 4. 安全边界

### 4.1 只处理文件名

`assets[].basename` 只接受**单一文件名**（1–180 字符，不含 `/`、`\`、`:`、`*`、`?`、`"`、`<`、`>`、`|` 与控制字符）。

- 含路径（`../exports/clip.mp4`、`C:\media\clip.mp4`）→ `REFUSED_BASENAME_PATH_REFERENCE`，只输出**安全化后的文件名**，**原引用一字不回显**。
- 含链接（`https://…`）→ `REFUSED_BASENAME_URL_REFERENCE`，文件名输出为 `null`。
- 含非法字符 → `REFUSED_BASENAME_ILLEGAL_CHARACTERS`。

被拒引用一律使整体 `BLOCKED`。附件只登记文件名，**不打开、不读取、不解析**。

### 4.2 凭据门禁

任一层级的**字段名**命中 `api_key` / `secret` / `password` / `token` / `credential` / `private_key` / `passphrase` 等，或**字符串值**呈现真实凭据形态（`sk-`、`AKIA`、`ghp_`、`xox*`、JWT、PEM 私钥头）→ 直接 `REJECTED`，`credential_findings[]` 只给出**路径与原因**，不输出命中内容。

### 4.3 提示注入

只标记、不执行。检测要求**同一句内同时出现动作词与指令目标词**，命中记入 `injection_flagged[]`（精确到 `路径/字段`），进入 `markdown_summary` 时替换为固定占位「已隐藏疑似提示注入文本」。备注、来源说明、续期说明等自由文本都在检测范围内。

### 4.4 Markdown 转义

`markdown_summary` 中所有自由文本先剥离控制字符、折叠空白，再对 Markdown 元字符加反斜杠；任何输入都不能伪造标题、列表、链接或表格列。

### 4.5 明确不做

**不**解释条款法律效力、**不**起草或改写合同与授权函、**不**判断是否侵权、**不**判断是否违反平台规则、**不**给出「能不能用」的最终结论、**不**计算费用或分成、**不**向任何平台提交授权、**不**发送任何通知或邮件、**不**上传或发布内容。

## 5. 诊断表

| `review_flags` | 含义 | 用户该做什么 |
|---|---|---|
| `UNKNOWN_SCOPE_CHANNELS` 等 | 该维度没登记 | 补齐渠道 / 地域 / 用途 / 使用主体 |
| `MODIFICATIONS_UNKNOWN` | 没说明允许哪些修改 | 补齐；缺失不会被当作「禁止修改」 |
| `INVALID_DATE_RANGE` | 开始晚于结束 | 修正日期 |
| `INVALID_START_DATE` / `INVALID_END_DATE` | 日期不可解析 | 改为 `YYYY-MM-DD` |
| `MISSING_START_DATE` | 没有开始日期 | 补齐才能判断生效窗口 |
| `END_DATE_UNKNOWN` | 没有结束日期 | 补齐；**不等于永久授权** |
| `SCOPE_CONFLICT` | 与同资产另一条记录在同一时间段冲突 | 裁决以哪条为准 |
| `DUPLICATE_GRANT_ID` | 范围编号重复 | 合并或重新编号 |
| `DANGLING_ASSET_ID` | 指向未登记资产 | 补资产或改编号 |
| `ATTRIBUTION_TEXT_MISSING` | 要求署名但没给署名写法 | 补署名文案 |
| `ATTRIBUTION_REQUIREMENT_UNKNOWN` | 没说要不要署名 | 确认 |
| `RENEWAL_NOT_STATED` | 没说到期后怎么办 | 确认续期 / 停用 / 再议 |
| `SOURCE_EVIDENCE_NOT_STATED` | 没写依据来源 | 写名称（不要粘贴沟通原文） |
| `REFUSED_BASENAME_*` | 触发了文件名门禁 | 改为单一文件名 |
