# 短视频拍摄到剪辑交接包 — 字段与判定规则

本文件是 `scripts/run.py` 的完整规则说明。脚本只读一个本地 JSON，向 stdout 输出一个 JSON；不联网、不写文件、**不打开或解码任何媒体文件**，只处理用户写下的文字与文件名。

## 1. 输入字段

### 1.1 顶层

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | 基准时间，**必须带时区偏移**（`2026-09-27T20:00:00+08:00` 或 `...Z`）。缺失或不可解析 → `INPUT_INCOMPLETE` |
| `project` | 建议 | 项目信息，见 1.2 |
| `handoff` | 建议 | `{ "by": 拍摄方, "to": 剪辑方 }` |
| `shots[]` | 是 | 镜头记录，见 1.3。缺失或空数组 → `INPUT_INCOMPLETE` |
| `assets[]` | 建议 | 素材记录，见 1.4 |
| `brand_elements[]` | 可选 | 品牌元素，见 1.5 |

### 1.2 `project`

| 字段 | 说明 |
|---|---|
| `project_id` / `name` | 项目编号与名称 |
| `platform` | 目标平台（文字，不做平台规则判断） |
| `aspect_ratio` | 画幅（`9:16` / `16:9` / `1:1`）。缺失 → `ASPECT_RATIO_UNKNOWN` + 提问 |
| `version` | 项目版本（`v3`）。缺失 → `PROJECT_VERSION_UNKNOWN` + 提问 |
| `editor` | 剪辑负责人 |
| `due_at` | 项目截止时间（带时区）。不可解析 → `INVALID_PROJECT_DUE_AT` |

### 1.3 `shots[]`

| 字段 | 说明 |
|---|---|
| `shot_id` | 镜头编号。缺失 → `MISSING_SHOT_ID`，该记录判无效 |
| `description` | 画面内容。缺失 → `MISSING_DESCRIPTION` |
| `narrative_order` | 叙事顺序（非负整数）。缺失 → `ORDER_DERIVED_FROM_INPUT`（按输入顺序排）；非整数或负 → `INVALID_NARRATIVE_ORDER`，该记录判无效 |
| `status` | 见 §2.1。缺失 → `MISSING_STATUS`；不在白名单 → `INVALID_STATUS`，该记录判无效 |
| `take_ids[]` | 该镜头对应的素材编号 |
| `duration_sec` | 时长（秒，Decimal）。键缺失或值为 `null` → `DURATION_UNKNOWN`；不可解析或为负 → `INVALID_DURATION` / `NEGATIVE_DURATION`，该记录判无效 |
| `owner` | 责任人。缺失 → `OWNER_MISSING` |
| `due_at` | 镜头截止（带时区）。不可解析 → `INVALID_DUE_AT` |
| `subtitles_required` | `true` / `false`。键缺失**或值为 `null`** → `SUBTITLE_REQUIREMENT_UNKNOWN`（**不读作「不需要字幕」**） |
| `audio_note` | 音频说明（同期声 / 配音 / 无音频）。缺失 → `AUDIO_NOTE_MISSING` |
| `brand_elements[]` | 该镜头用到的品牌元素编号 |
| `notes` | 备注（自由文本，仅作数据） |

### 1.4 `assets[]`

| 字段 | 说明 |
|---|---|
| `asset_id` | 素材编号。缺失 → `MISSING_ASSET_ID`，该记录判无效 |
| `basename` | **只允许单一文件名**，见 §4.1。含路径或链接 → 拒绝并只保留文件名 |
| `kind` | `video` / `audio` / `image` / `graphics` / `other`。越界 → `INVALID_ASSET_KIND`，该记录判无效 |
| `version` | 素材版本。键缺失或 `null` → `ASSET_VERSION_UNKNOWN` |
| `status` | `available` / `missing` / `unknown`。缺失 → `MISSING_ASSET_STATUS`；`unknown` → `ASSET_STATUS_UNKNOWN` |
| `shot_refs[]` | 该素材服务的镜头编号 |
| `owner` | 责任人。缺失 → `OWNER_MISSING` |

### 1.5 `brand_elements[]`

| 字段 | 说明 |
|---|---|
| `element_id` / `name` | 元素编号与名称 |
| `required` | `true` / `false`。键缺失 → `REQUIRED_FLAG_UNKNOWN` |
| `available` | `true` / `false` / `null`。`null` → `AVAILABILITY_UNKNOWN` |
| `version` | 元素版本 |

规则：`required` 为 `true` 且 `available` **不是** `true` → `REQUIRED_ELEMENT_UNAVAILABLE`，进入 `brand_element_gaps`。没有任何镜头引用的元素 → `ELEMENT_NOT_REFERENCED`。

## 2. 判定规则

### 2.1 镜头状态与覆盖状态

| `status` | `take_ids` | 覆盖状态 |
|---|---|---|
| `shot` | 非空 | `COVERED` |
| `shot` | 空 | `COVERED_UNVERIFIED`（+ `TAKE_IDS_MISSING`） |
| `planned` | — | `NOT_SHOT` |
| `missing` | — | `MISSING` |
| `reshoot` | — | `RESHOOT` |
| `unknown` | — | `UNKNOWN` |

### 2.2 逾期

`shots[].due_at < as_of` 且 `status != "shot"` → `is_overdue = true`，该镜头编号同时进入 `missing_shots`。已拍妥的镜头不因截止时间过去而报逾期。

### 2.3 总时长

**只有全部镜头都给出时长时才合计**，否则 `total_duration_sec = null`，并给出 `duration_unknown_shots`。部分求和会让人误以为成片长度已确定。

### 2.4 素材版本冲突（阻塞）

按 `asset_id` 分组：

- 同一 `asset_id` 出现**多个不同 `version`** → `asset_conflicts`，整体 `BLOCKED`。工具不替你挑版本。
- 同一 `asset_id` 出现多次但**版本完全相同** → `duplicate_asset_ids`（只保留第一条），不阻塞，但会提问。

### 2.5 悬空引用（阻塞）

- `shots[].take_ids` 中的素材编号不存在于 `assets[]` → `DANGLING_TAKE_REF`。
- `assets[].shot_refs` 中的镜头编号不存在于 `shots[]` → `DANGLING_SHOT_REF`。

两者都使证据链断裂，整体 `BLOCKED`。

### 2.6 剪辑顺序建议

按 `(narrative_order 或 1_000_000 + 输入序号, 输入序号)` 升序排列，输出 `edit_order[]`：位次、镜头编号、覆盖状态、下一步动作。

| 覆盖状态 | 下一步动作 |
|---|---|
| `COVERED` | 可直接进入剪辑 |
| `COVERED_UNVERIFIED` | 剪辑前先确认素材编号 |
| `NOT_SHOT` | 待拍摄 |
| `MISSING` | 先补拍 |
| `RESHOOT` | 重拍后替换 |
| `UNKNOWN` | 与拍摄负责人确认镜头状态 |

### 2.7 整体状态

按顺序判定，先命中先生效：

| 状态 | 触发条件 |
|---|---|
| `REJECTED` | 输入出现疑似凭据（字段名或值形态），**拒绝且不回显** |
| `BLOCKED` | 素材版本冲突 / 被拒素材引用 / 悬空引用 / 存在无效镜头或素材记录 |
| `INPUT_INCOMPLETE` | `as_of` 缺失或不可解析，或 `shots[]` 缺失/为空 |
| `GAPS_FOUND` | 存在未知时长、缺责任人、品牌元素缺口、有镜头无素材、未知字幕或音频、未知画幅/版本、未给交接双方、素材版本或状态未知 |
| `READY` | 以上都没有 |

## 3. 输出字段

`version`、`status`、`as_of`、`project`、`handoff`、`shot_count`、`coverage_counts`、`shots[]`、`coverage_matrix[]`、`missing_shots`、`reshoot_shots`、`confirm_items[]`、`shots_without_asset`、`asset_count`、`assets[]`、`asset_conflicts[]`、`duplicate_asset_ids[]`、`refused_refs[]`、`dangling_refs[]`、`brand_elements[]`、`brand_element_gaps[]`、`edit_order[]`、`duration_known_shots`、`duration_unknown_shots`、`total_duration_sec`、`responsibility[]`、`clarification_questions[]`、`injection_flagged[]`、`input_warnings[]`、`markdown_summary`、`disclaimer`。

阅读顺序：`status` → `missing_shots` / `reshoot_shots` → `asset_conflicts` → `confirm_items` → `edit_order` → `clarification_questions` → `responsibility`。

## 4. 安全边界

### 4.1 只处理文件名

`assets[].basename` 只接受**单一文件名**（1–180 字符，不含 `/`、`\`、`:`、`*`、`?`、`"`、`<`、`>`、`|` 与控制字符）。

- 含路径（`../raw/A-201_final.mp4`、`C:\media\clip.mp4`）→ `REFUSED_BASENAME_PATH_REFERENCE`，只在 `assets[].basename` 输出**安全化后的文件名**，**原引用一字不回显**。
- 含链接（`https://…`）→ `REFUSED_BASENAME_URL_REFERENCE`，文件名输出为 `null`。
- 含非法字符 → `REFUSED_BASENAME_ILLEGAL_CHARACTERS`。

被拒引用一律使整体 `BLOCKED`。

### 4.2 凭据门禁

任一层级的**字段名**命中 `api_key` / `secret` / `password` / `token` / `credential` / `private_key` / `passphrase` 等，或**字符串值**呈现真实凭据形态（`sk-`、`AKIA`、`ghp_`、`xox*`、JWT、PEM 私钥头）→ 直接 `REJECTED`，`credential_findings[]` 只给出**路径与原因**，不输出命中内容。

### 4.3 提示注入

只标记、不执行。检测要求**同一句内同时出现动作词与指令目标词**（见 `scripts/run.py` 的 `INJ_ACTION` / `INJ_TARGET`），因此「请忽略上一版反光问题」这类正常备注不会被误标。命中记入 `injection_flagged[]`（精确到 `路径/字段`），进入 `markdown_summary` 时替换为固定占位「已隐藏疑似提示注入文本」。

### 4.4 Markdown 转义

`markdown_summary` 中所有自由文本先剥离控制字符、折叠空白，再对 Markdown 元字符加反斜杠；因此任何输入都不能伪造标题、列表、链接或表格列。

### 4.5 明确不做

不生成视频、不读取媒体、不计算成片时长以外的任何指标、不上传或发布到任何平台、不登录任何账号、不发送消息、不判断版权或肖像权、不评估平台违禁词。

## 5. 诊断表

| `review_flags` / 字段 | 含义 | 用户该做什么 |
|---|---|---|
| `MISSING_SHOT_ID` | 镜头没有编号 | 补编号，否则素材无法对应 |
| `INVALID_STATUS` | 镜头状态不在白名单 | 改为 `planned`/`shot`/`missing`/`reshoot`/`unknown` |
| `INVALID_NARRATIVE_ORDER` | 叙事顺序非非负整数 | 改成整数 |
| `DURATION_UNKNOWN` | 未给时长 | 补时长；未知不会按 0 计入总时长 |
| `INVALID_DURATION` / `NEGATIVE_DURATION` | 时长不可解析或为负 | 修正数值 |
| `TAKE_IDS_MISSING` | 标为已拍但没有素材编号 | 补素材编号，或改回 `unknown` |
| `ORDER_DERIVED_FROM_INPUT` | 未给叙事顺序，按输入顺序排列 | 如需固定顺序请显式填写 |
| `CONFLICTING_ASSET_VERSION` | 同一素材多个版本 | 决定以哪一版为准 |
| `DUPLICATE_ASSET_ID` | 同一素材重复登记 | 合并重复登记 |
| `DANGLING_TAKE_REF` / `DANGLING_SHOT_REF` | 互相引用的编号不存在 | 补齐编号 |
| `REFUSED_BASENAME_*` | 触发了文件名门禁 | 改为单一文件名 |
| `REQUIRED_ELEMENT_UNAVAILABLE` | 必需品牌元素未确认可用 | 确认素材来源与授权 |
| `SUBTITLE_REQUIREMENT_UNKNOWN` / `AUDIO_NOTE_MISSING` | 字幕或音频未说明 | 按平台要求补全 |
| `OWNER_MISSING` | 无责任人 | 指派到具体成员 |
