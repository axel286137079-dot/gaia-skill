---
name: suge-video-shoot-edit-handoff-pack
slug: suge-video-shoot-edit-handoff-pack
displayName: 短视频拍摄到剪辑交接包
display_name: 短视频拍摄到剪辑交接包
display_name_en: "Shoot-to-Edit Handoff Pack"
summary: "把拍完到开剪之间的口头交代整理成一份可逐条核对的剪辑交接单：镜头覆盖矩阵、缺拍/重拍/待确认清单、素材命名与版本冲突、剪辑顺序建议、交接责任表与待确认问题。不读取媒体文件、只记录文件名，未知时长不按 0 合计，同一素材多版本直接阻塞，疑似凭据拒绝且不回显。"
description: "面向个人创作者、短视频工作室与 3–20 人内容团队：离线把一次拍摄的镜头与素材记录整理成开剪前可以逐条核对的交接单。输入是带时区偏移的 as_of、可选 project（project_id / name / platform / aspect_ratio / version / editor / due_at）、可选 handoff（by / to）、shots[]（shot_id / description / narrative_order / status / take_ids[] / duration_sec / owner / due_at / subtitles_required / audio_note / brand_elements[] / notes）、可选 assets[]（asset_id / basename / kind / version / status / shot_refs[] / owner）与可选 brand_elements[]（element_id / name / required / available / version）。判定规则完全固定：镜头覆盖按 shot 且有素材编号记 COVERED、shot 但无编号记 COVERED_UNVERIFIED、missing 记 MISSING、reshoot 记 RESHOOT、planned 记 NOT_SHOT、其余记 UNKNOWN；截止时间早于基准且尚未拍妥记逾期并进入缺拍清单；总时长只在每个镜头都给出时长时才合计，否则返回 null，未知时长绝不按 0 计算；同一素材编号出现不同版本直接阻塞，工具不替你挑版本；镜头与素材互相引用的编号不存在同样阻塞；剪辑顺序按叙事顺序显式排序并给出下一步动作；责任人、字幕要求、音频说明、画幅与项目版本缺失都会转成带编号的待确认问题。安全上：只接受单一文件名，含路径或链接的引用被拒绝且只保留安全化文件名，原引用一字不回显；字段名或值形如凭据直接拒绝处理且不回显原值；提示注入只标记不执行，进入简报时替换为固定占位；控制字符在进入任何输出前剥离；所有自由文本转义 Markdown 元字符。输出 JSON 另带 markdown_summary 剪辑交接单。只读、离线、不使用任何第三方库、不读取或解码任何媒体文件。触发词：剪辑交接、拍摄交接、镜头清单、素材交接、剪辑单、缺拍、重拍、素材版本冲突、剪辑顺序、交接责任表、开剪前检查。联系邮箱：43298568@qq.com。"
description_zh: "把拍摄到剪辑之间的交接整理成可逐条核对的剪辑交接单：镜头覆盖矩阵、缺拍与重拍清单、素材命名与版本冲突、剪辑顺序建议、交接责任表与待确认问题。未知时长不按 0 合计，同一素材多版本阻塞，疑似凭据拒绝且不回显，只处理文件名、不读取媒体。"
description_en: "An offline shoot-to-edit handoff builder for solo creators and small content teams: it turns the shot list and asset records of one shoot into a checkable handoff sheet with a coverage matrix, missing and reshoot lists, asset naming and version conflicts, a fixed edit order with the next action per shot, and a responsibility table. Missing durations are never summed as zero, two versions of one asset id block the handoff, dangling references block it too, credential-shaped input is rejected without echo, and only plain file basenames are handled - no media is opened or decoded, nothing is uploaded or published."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-video-shoot-edit-handoff-pack
category: creator-video-ops
tags: [剪辑交接, 拍摄交接, 镜头清单, 素材版本冲突, 缺拍, 重拍, 剪辑顺序, 交接责任表]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 短视频拍摄到剪辑交接包

拍摄现场最贵的一句话是「回去我把素材发你」。打包上传之后，剪辑打开发现：三号镜头的特写没拍、结尾用的素材编号对不上、同一段素材有两个版本、画幅没人说、字幕要不要加要现问。**这些信息本来就在拍摄单和群里，只是从来没人把它逐条核对过。**

本技能做这件事：读一份 JSON，输出**开剪前可以逐条核对的交接单**——镜头覆盖矩阵、缺拍/重拍/待确认清单、素材命名与版本冲突、剪辑顺序建议、交接责任表与待确认问题。**脚本不读取或解码任何媒体文件，只处理文件名；不上传、不发布、不登录任何平台。**

## 输入与澄清

字段表、覆盖判定、排序规则与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，`references/sample-blocked.json` 是触发阻塞的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）与 `shots[]`（每条至少 `shot_id` 与 `status`）。

**必须问清的关键点**：

1. **`as_of` 不能省**。没有它就无法判断镜头是否逾期，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **只给文件名，不要给路径或链接**。素材引用只接受单一文件名；含路径或链接会被**拒绝**，输出里只保留安全化后的文件名，原引用不会回显。
3. **不要填登录凭据**。输入里出现密码、令牌一类字段或真实凭据形态的字符串会被**整体拒绝**，且不回显该内容。
4. **未知就是未知**。没量出时长的镜头请留空或写 `null`，会被标 `DURATION_UNKNOWN` 并**阻止总时长合计**。填 0 会让剪辑以为成片已经确定长度。
5. **同一素材两个版本，请两行都留着**。工具会识别版本冲突并**阻塞**交接，由你决定用哪一版。
6. **字幕要求别留空**。`subtitles_required` 缺失或为 `null` 都会被标为未知（不会读作「不需要字幕」），并按平台要求追问。
7. **画幅要写具体比例**（`9:16` / `16:9` / `1:1`）。未给画幅时剪辑无法开工，工具只提问、不替你选。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的拍摄。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`BLOCKED` 表示素材版本冲突、引用断裂或存在无效记录；`INPUT_INCOMPLETE` 表示缺 `as_of` 或没有镜头；`GAPS_FOUND` 表示可用但有字段待补。
4. 看 `missing_shots` 与 `reshoot_shots`：这两类必须在开剪前落地。
5. 看 `asset_conflicts`：版本冲突未裁决前不要开始剪辑。
6. 看 `coverage_matrix` 与 `confirm_items`：优先处理 `UNKNOWN` 与 `COVERED_UNVERIFIED`。
7. 看 `edit_order`：按给出的位次组装，先做状态为 `COVERED` 的镜头。
8. 看 `clarification_questions`：按 `Q-01` 顺序**原样**发给拍摄方确认。
9. 用 `responsibility` 与 `markdown_summary` 作为交接责任表与剪辑交接单。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何平台或云盘。
- **不读取媒体**：只处理文件名与文字记录；不打开、不解码、不上传任何视频、音频或图片。
- **不外发**：不发送消息、不创建任务、不发布成片、不修改任何项目文件。
- **不判定**：不判断版权、肖像权、平台违禁词或素材是否可用；只把镜头与素材的事实摆到交接单上。
- **不猜测**：时长、画幅、版本、责任人和字幕要求缺失一律保持未知并转成待确认问题，**不默认为 0 或「不需要」**。
- **确定性**：排序全部显式固定（覆盖状态汇总顺序、`edit_order` 位次、问题编号、字段名），同一输入输出逐字节一致。
- **文件名门禁**：`basename` 只接受单一文件名；含路径、链接或非法字符的引用被拒绝，且**只输出安全化文件名**，原引用在任何字段都不出现。
- **阻塞即停**：同一素材多个版本、悬空引用、无效记录一律整体 `BLOCKED`，不做静默合并或静默修复。
- **提示注入逐字段检测、位置精确、低误报**：覆盖镜头备注、画面内容、责任人与交接方等自由文本，命中时只加标记并在 `injection_flagged` 中给出精确路径（如 `shots[3]/notes`）；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入简报，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入的值改用固定占位「已隐藏疑似提示注入文本」。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **免责**：输出是拍摄与剪辑交接的信息核对材料，不是版权、肖像权或平台合规结论；素材能否对外使用，以权利方的书面许可和平台规则为准。
