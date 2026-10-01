---
name: suge-creator-content-usage-scope-map
slug: suge-creator-content-usage-scope-map
displayName: 创作者内容使用范围地图
display_name: 创作者内容使用范围地图
display_name_en: "Creator Content Usage Scope Map"
summary: "把一次创作者合作里「哪份内容、谁能用、在哪些渠道和地域、做什么用途、从哪天到哪天、能不能改、要不要署名、到期怎么办」整理成资产 × 使用范围矩阵：已明确 / 未知 / 冲突 / 已到期状态、超范围候选、到期时间线、缺口清单与待澄清问题。只做事实整理与范围比对，不解释法律效力、不起草合同、不判断侵权；缺失范围不会被当作全渠道永久。"
description: "面向个人创作者、UGC 创作者、小型 MCN 和品牌方对接人：离线把一次共创合作的素材使用范围整理成可以逐条核对的矩阵。输入是带时区偏移的 as_of、collaboration（collab_id / brand / creator）、assets[]（asset_id / basename / kind / source_note）、grants[]（grant_id / asset_id / authorized_parties[] / channels[] / territory[] / purposes[] / modifications[] / starts_on / ends_on / attribution_required / attribution_text / renewal_note / source_evidence / notes）与可选 requests[]（request_id / asset_id / party / channel / territory / purpose / modifications[] / planned_on）。判定规则完全固定：日期一律 YYYY-MM-DD 且必须可解析、开始不得晚于结束，否则整体阻塞；结束日等于基准日仍算生效，早于基准日才是已到期；结束日缺失一律记为结束日未知并追问，绝不读作永久授权，也绝不把缺失的范围补成全渠道；同一资产在重叠时间段内两条记录的范围字段取值不一致记为冲突并提问，工具不替你选一条；新请求逐字段比对，得出已覆盖 / 覆盖但依据记录本身冲突 / 超范围 / 无生效窗口 / 信息不足无法比对五种结果，均声明为登记范围比对而非法律结论。安全上：附件只登记单一文件名，含路径或链接的引用被拒绝且只保留安全化文件名，原引用一字不回显；字段名或值形如凭据直接拒绝处理且不回显原值；提示注入只标记不执行并替换为固定占位；控制字符剥离、自由文本转义 Markdown 元字符。不解释条款效力、不起草合同、不判断侵权、不计算费用、不向任何平台提交授权、不发送通知。输出 JSON 另带 markdown_summary 范围确认单。只读、离线、不使用任何第三方库。触发词：使用范围、授权范围、内容授权、使用权、商单范围、素材使用权限、渠道范围、投放范围、授权期限、到期提醒、二创权限、署名要求、范围确认单。联系邮箱：43298568@qq.com。"
description_zh: "把创作者合作的内容使用范围整理成资产 × 使用范围矩阵：已明确 / 未知 / 冲突 / 已到期状态、超范围候选、到期时间线、缺口清单与待澄清问题。只做事实整理与范围比对，不解释法律效力、不起草合同、不判断侵权；缺失范围不会被当作全渠道永久，结束日缺失不会被当作永久授权。"
description_en: "An offline scope map for creator collaborations: it turns what a deal already says into an asset-by-scope matrix - which content, which parties, which channels, which territory, which purpose, from when to when, which changes, attribution and renewal - and checks new requests against that record. Missing scope is never read as all channels forever, a missing end date is never read as perpetual, overlapping records that disagree become conflicts for a human to settle, and date ranges that cannot be parsed block the run. It only compares records: it does not interpret legal effect, does not draft contract text, does not decide whether anything is infringing, never grants a permission on a platform and never sends a notice."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-creator-content-usage-scope-map
category: creator-rights-ops
tags: [使用范围, 授权范围, 内容授权, 渠道范围, 授权期限, 到期提醒, 二创权限, 范围确认单]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 创作者内容使用范围地图

创作者与合作方最容易发生分歧的地方不是钱，而是**范围**：品牌说要「用一下这条视频」，创作者以为是发一条小红书，对方想的是投三个月信息流加白名单；到期忘了停，下个月还在跑；同一份素材前后谈过两次，两个版本的对不上；花絮和原片没人提过，却被打包走了。**这些信息通常都已经写在邮件和聊天里，只是没有一张表把它们摆齐。**

本技能做这件事：读一份 JSON，输出**资产 × 使用范围矩阵**、冲突、到期时间线、超范围候选与缺口清单。**只做事实整理与范围比对——不解释法律效力、不起草合同、不判断侵权，也不向任何平台提交授权。**

## 输入与澄清

字段表、时间与冲突判定、请求比对与安全边界见 @references/guide.md（同目录 `references/sample.json` 是含冲突与超范围请求的样例，`references/sample-blocked.json` 是触发阻塞的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）、`assets[]` 与 `grants[]`（每条至少 `grant_id`、`asset_id`、`starts_on`、`ends_on`）。

**必须问清的关键点**：

1. **范围缺失就是未知**。渠道、地域、用途、使用主体没写，工具会标 `UNKNOWN_SCOPE_*` 并追问，**绝不会**补成「全渠道」「永久」。合作方口头说过的，请写进字段里。
2. **结束日期不能省**。`ends_on` 缺失一律记为「结束日未知」，**不等于永久授权**，也不会被当作「到期自动停」。这一条是本工具最重要的边界。
3. **日期必须可解析且开始不晚于结束**（`YYYY-MM-DD`）。这两条不满足会**整体阻塞**，因为时间窗错了，之后的每一项比对都没有意义。
4. **同一份素材被谈过两次是常态**。请两条都留着，工具会识别重叠时间段内的范围差异并列为冲突，由你决定以哪条为准，而不是替你选。
5. **允许的修改方式要写明确**。`modifications` 缺失不会被当作「禁止修改」，也不会被当作「随意修改」——两种默认都会误导。
6. **署名要写具体写法**。`attribution_required` 为 `true` 但没给文案时会标 `ATTRIBUTION_TEXT_MISSING`。
7. **只给文件名，不要给路径或链接，也不要填登录凭据**。含路径或链接的附件引用会被拒绝且只保留文件名；出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的合作。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED`（疑似凭据，已拒绝且未回显）、`BLOCKED`（编号重复、日期非法、引用断裂）、`INPUT_INCOMPLETE`（缺基准时间或没有范围记录）、`GAPS_FOUND`（可用但有字段待补）。
4. 看 `conflicts`：同一资产在同一时间段的范围差异必须先裁决，否则「能不能用」无从判断。
5. 看 `expiry_timeline` 与 `ends_today`：到期前先谈续期或安排停用，别等到被动发现。
6. 看 `over_scope_candidates`：这些请求与登记范围不一致或无法核对，**先确认范围再执行**。注意 `COVERED_BUT_CONFLICTED` 表示结论依据的记录本身有冲突。
7. 看 `scope_matrix` 的 `unknown_fields`：把「未知」补成「明确」，这张表才有用。
8. 看 `clarification_questions`：按 `Q-01` 顺序**原样**发给合作方确认。
9. 用 `markdown_summary` 作为范围确认单。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何平台账号。
- **附件只留文件名**：`basename` 只接受单一文件名；含路径或链接的引用被拒绝，且**只输出安全化文件名**，原引用在任何字段都不出现；不打开、不读取、不解析附件内容。
- **不授权、不外发**：不向任何平台提交或开启授权、不生成授权码、不发送通知或邮件、不代替任何一方同意条款。
- **不做法律判断**：不解释条款效力、不起草或改写合同、不判断是否侵权、不判断是否违反平台规则、不给出「能不能用」的最终结论、不计算费用或分成。
- **不猜测**：渠道、地域、用途、使用主体、修改方式、署名要求、续期与结束日期缺失一律保持未知并转成待确认问题，**绝不补成「全渠道」「永久」「允许随意修改」**。
- **确定性**：排序全部显式固定（汇总顺序、冲突与时间线排序、问题编号、字段名），同一输入输出逐字节一致。
- **冲突交给人工**：重叠时间段内的范围差异只列为冲突并提问；工具不替用户在两条记录里做选择，也不把冲突记录当作干净的「可以」。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注、来源说明、续期说明等自由文本，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `grants[2]/renewal_note`）；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：本输出只按登记内容整理事实与范围差异，不是法律意见，不解释条款效力、不起草合同、不判断是否侵权；权利范围以各方签署的书面文件为准。
