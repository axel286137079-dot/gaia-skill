# 客户名单清洗与去重 · 使用指南

本指南描述 `scripts/run.py` 的输入结构、输出结构、问题代码表、规范化规则、去重规则与阅读顺序。
引擎是**离线、只读、确定性**的：不写回源数据、不删除记录、不调用网络、相同输入必然产生逐字节一致的输出。

## 零、凭据门禁（最先执行）

名单里经常夹带导出时误带进来的账号字段。引擎在解析任何业务字段**之前**递归扫描整份输入：

- **字段名命中**即拒绝：`password`、`passwd`、`passphrase`、`secret_value`、`api_token`、`access_token`、
  `client_secret`、`credential_value`、`private_key`、`private_key_pem`、`dsn`、`connection_string`、
  `jdbc_url`、`database_url`、`cookie`、`session_token`、`refresh_token`（大小写与首尾空白不敏感）。
- **字段值命中**即拒绝：`sk-` 开头的长串、`AKIA…`、`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` 开头的令牌、
  `xoxb-`/`xoxp-` 等 Slack 令牌、`eyJ…` 三段式 JWT、以及 `BEGIN … PRIVATE KEY` / `BEGIN … CERTIFICATE` 包裹的 PEM 块。

命中时抛 `ValueError`，异常信息**不回显**命中的字段名与字符串内容，避免二次泄漏。
说明：手机号、邮箱、姓名、公司名是本职任务的正常输入，**不会被门禁拦截**；门禁只针对凭据类内容。

## 一、输入结构（input.json）

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| as_of | 是 | 文本 | 数据基准时间，必须是**带时区偏移**的 ISO8601，如 `2026-09-16T10:00:00+08:00` |
| normalization | 否 | 对象 | `default_region`（CN / INTL）、`phone_format`（E164 / NATIONAL） |
| records | 是 | 数组 | 非空记录数组，顺序即 `row_index`（从 1 开始） |
| dedup | 否 | 对象 | `exact_keys`、`fuzzy_name`、`fuzzy_threshold`、`blocking_field` |
| email_domain_corrections | 否 | 数组 | `[{"from": 错域名, "to": 正确域名}]` |
| company_aliases | 否 | 数组 | `[{"alias": 别名, "canonical": 正式名称}]` |

`records[]` 字段：

| 字段 | 必填 | 类型 | 说明 |
| --- | --- | --- | --- |
| record_id | 是（缺失即报错） | 文本 | 记录编号，用于引用与人工核对 |
| name | 否 | 文本 | 姓名 |
| company | 否 | 文本 | 公司名称 |
| phone | 否 | 文本 | 手机号/电话 |
| email | 否 | 文本 | 邮箱 |
| city | 否 | 文本 | 城市 |
| tags | 否 | 文本数组 | 标签 |
| source | 否 | 文本 | 来源渠道（仅参与注入扫描） |
| updated_at | 否 | 文本 | 带时区偏移的 ISO8601，用于平局判定 |

输入不合法（如 `as_of` 无时区、`records` 为空、`fuzzy_name` 为 true 却未给 `fuzzy_threshold`）时，引擎抛出中文 `ValueError` 并退出码非 0。

## 二、输出结构（stdout JSON）

| 字段 | 说明 |
| --- | --- |
| status | 总状态：INVALID / DUPLICATES_FOUND / NEEDS_REVIEW / CLEAN |
| as_of | 回显输入基准时间 |
| record_count | 记录条数 |
| unique_record_id_count / duplicate_record_id_count | 唯一编号数 / 编号重复的记录条数 |
| status_counts | 四种记录状态的数量，四种键始终存在 |
| issue_counts | 问题代码 → 出现次数（键已排序） |
| severity_counts | HIGH / MEDIUM / LOW / INFO 的问题条数 |
| records[] | 逐条结果，见下 |
| duplicate_groups[] | 重复候选组，精确组在前、弱组在后 |
| duplicate_group_count / exact_group_count / weak_group_count | 组数统计 |
| suggested_removal_count | 建议移出（= 精确组中非保留成员）的条数 |
| summary | 汇总指标，含 `completeness_avg_pct`（两位小数字符串） |
| normalization_applied / dedup_applied | 本次实际生效的配置 |
| next_actions[] | `{action, priority, target}`，按固定顺序输出 |
| markdown_summary | 中文 Markdown 报告，可直接贴进工作文档 |
| disclaimer | 中文免责说明 |

`records[]` 字段：`record_id`、`row_index`、`status`、`normalized{name, company, phone, phone_e164, email, city, tags}`、
`completeness_pct`（两位小数字符串）、`issues[]{code, field, severity, detail, suggested_value}`、
`issue_codes`（排序去重）、`injection_flags`、`in_duplicate_group`。

`duplicate_groups[]` 字段：`group_id`（G-001 起）、`confidence`（EXACT / WEAK）、`matched_on`、
`blocking_field`、`record_ids`、`conflicts`、`suggested_keep`、`suggested_remove`、`needs_confirmation`、`reason`。

## 三、问题代码表

| 代码 | 严重度 | 触发条件 | field |
| --- | --- | --- | --- |
| MISSING_RECORD_ID | HIGH | 缺少 record_id 或规范化后为空 | record_id |
| DUPLICATE_RECORD_ID | HIGH | 编号与更早的记录相同（首次出现处保留编号，之后各条报错） | record_id |
| NO_CONTACT_KEY | HIGH | 手机号与邮箱同时为空 | contact_key |
| INVALID_PHONE | HIGH | 不符合当前 region/格式要求（CN 需 11 位大陆手机号） | phone |
| INVALID_EMAIL | HIGH | 不满足 `^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$` | email |
| NON_TEXT_FIELD | HIGH | 期望文本的字段拿到对象/数组/数字 | 该字段 |
| EMAIL_DOMAIN_TYPO | MEDIUM | 邮箱域名命中 `email_domain_corrections` | email |
| FUTURE_UPDATED_AT | MEDIUM | updated_at 晚于 as_of | updated_at |
| INJECTION_TEXT | MEDIUM | 任意文本字段命中提示注入特征 | 首个命中字段 |
| CONTROL_CHARS_REMOVED | MEDIUM | 清理了控制字符（Cc/Cf/Cs/Co） | 首个命中字段 |
| SUSPICIOUS_NAME | LOW | 姓名含数字或仅由符号组成 | name |
| COMPANY_ALIAS | INFO | 公司名与别名表条目完全一致（忽略大小写） | company |
| FULLWIDTH_NORMALIZED | INFO | 做过全角转半角 | 首个命中字段 |
| WHITESPACE_NORMALIZED | INFO | 折叠或去掉了首尾空白 | 首个命中字段 |
| MISSING_FIELD | INFO | name / company / city 缺失或为空 | 首个缺失的字段 |

同一记录的 `issues` 按「严重度 → 代码 → 字段」排序；`FULLWIDTH/WHITESPACE/CONTROL` 三个代码每条记录最多各出现一次，
`detail` 里列出所有受影响字段。`MISSING_FIELD` 每条记录也**最多一次**：`field` 记首个缺失的关键字段（name 优先于 company 优先于 city），
其余缺失字段体现在 `normalized` 的空值与 `completeness_pct` 上，phone/email 的缺失单独由 `NO_CONTACT_KEY` 覆盖。

记录状态优先级：INVALID（编号缺失/重复或非文本字段）> DUPLICATE（精确组中非保留项）> NEEDS_REVIEW（存在 HIGH/MEDIUM 问题，或属于弱分组）
> VALID（仅 INFO/LOW 问题）。**弱分组成员一律 NEEDS_REVIEW，包括被建议保留的那一条。**

## 四、规范化规则

1. **全角转半角**：U+FF01–U+FF5E 映射到对应半角字符（含全角数字、全角括号、全角逗号等），U+3000 表意空格转半角空格。
   该步骤始终执行。
2. **控制字符**：制表符、换行等空白类控制符转为空格；其余 Cc/Cf/Cs/Co 字符直接移除（如 U+200B、U+FEFF）。
3. **空白折叠**：连续空白折叠为一个半角空格，并去掉首尾空白。姓名内部的单词间空格不会被删除（`张 三` 与 `张三` 视为不同写法）。
4. **邮箱**：规范化后转小写；仅做格式校验，不做真实可达性验证。
5. **手机号**：去掉空格与 `- ( ) + .` 后取数字串。region=CN 时，13 位且以 `86` 开头视为携带国家码并去掉前两位；
   随后必须满足 `1[3-9]\d{9}`。`phone_format=E164` 时输出 `phone_e164="+86"+11位`；`NATIONAL` 时不加 `+86`，`phone` 保持 11 位国内形态。
   region 为 INTL 或未配置时不做国家推断：6–15 位数字视为结构有效，`phone_e164` 仅当原值以 `+` 开头时才有值。
6. **公司别名**：规范化后与别名表**完全一致**（忽略大小写）时给出建议值；仅用于弱分组的阻断字段归一，不改变 `normalized.company`。
7. **完整度**：`completeness_pct = 有效项数 / 4 × 100`，四项为 name、company、phone（须有效）、email（须有效），缺失值不得猜测。

## 五、去重规则

- **精确组**：以「规范化邮箱」「规范化手机号（优先 E.164）」为键，键为空的值永不参与分组（空不等于空）；
  共用任一键的记录通过并查集合并（传递闭包）。`matched_on` 为组内真正命中的键类型（排序后）。
- **弱分组**：仅当 `dedup.fuzzy_name=true`。姓名相似度用 `difflib.SequenceMatcher(None, a, b).ratio()`，
  并要求阻断字段（默认 `company`）双方非空且归一后相等；相似度 ≥ `fuzzy_threshold` 才成组，置信度为 WEAK、`needs_confirmation=true`。
  弱分组的 `matched_on` 固定为 `["fuzzy_name"]`（精确键没有命中，命中的是姓名相似度），`blocking_field` 记为实际使用的阻断字段。
- 已经进入精确组的记录不参与弱分组（分组互斥），避免一条记录同时落在两个组里。
- 建议修正（邮箱域名纠错、公司别名）**绝不参与去重键**，因此「建议修正」不是合并证据：R-0003 的 `gmial.com` 与 R-0004 的 `gmail.com` 不会被合并。
- `conflicts`：name/company/phone/email/city 五个字段在组内出现多于一个规范化取值时给出排序列表，否则为空数组。
- 组顺序：精确组在前（按最小行号），弱组在后（按最小行号），编号 G-001 起连续。

## 六、suggested_keep 平局规则（确定性）

依次比较：① `completeness_pct` 高者优先；② `updated_at` 晚者优先（缺失视为最旧，排最后）；③ `row_index` 小者优先。
精确组的 `suggested_remove` = 其余成员（按行号升序）；弱组不给出 `suggested_remove`，且 `needs_confirmation=true`。

## 七、输出阅读顺序

1. `status` 与 `status_counts` —— 先判断名单整体质量。
2. `records` 中 `status=INVALID` 的记录 —— 修复编号或字段类型后重新导出。
3. `duplicate_groups` —— 逐组看 `conflicts` 再决定是否合并，精确组优先处理。
4. 其余 `NEEDS_REVIEW` 记录的 `issues` —— 按严重度从高到低处理。
5. `next_actions` 与 `markdown_summary` —— 直接作为当天的工作清单。

## 八、硬性边界

- 引擎只读：不修改源文件、不删除记录、不自动合并、不写数据库。
- 输入中没有的值一律保持未知，`normalized` 中为 `null`，绝不推测补全。
- `suggested_value` 全部是待人工确认的建议，不是事实。
- 输出只是清洗工作清单，不是经过核实的客户名单；触达前需人工复核。
- 任意文本字段中的「忽略以上指令」「delete all」等字样只被记录（`INJECTION_TEXT` + `injection_flags`），引擎不会执行。
