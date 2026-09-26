---
name: suge-supplier-sample-evaluation-pack
slug: suge-supplier-sample-evaluation-pack
display_name: 供应商样品评估准备包
display_name_en: "Supplier Sample Evaluation Evidence Pack"
summary: "把「同一个需求、多家供应商、多个样品批次」的样品观察结果，整理成一张逐规格的证据矩阵：每一格是 PASS / FAIL / UNKNOWN / NOT_TESTED，按必填与可选分开，并给出样品间差异、复测与补证清单、不可比较项、供应商追问清单和人工决策包。硬规则：未测试、证据缺失或单位不可比较一律不判通过；单位换算只用显式白名单（质量、长度、面积、面密度、百分比、级、件数各自成组，跨组绝不换算），白名单之外的单位按未知处理并保持 UNKNOWN；报价按币种分组，非数值或负数价格排除在区间之外，不做跨币种合计、不做汇率换算；只记录安全化后的附件文件名，含路径分隔符、上级目录、URL scheme 或盘符的引用一律拒绝且不解析；明确不给质量、安全、认证、合规、法律或最终采购结论。"
description: "面向小品牌、电商商家、采购负责人与轻量制造团队的离线样品评估准备工具，纯标准库、只读、不联网、不读文件内容。触发词：供应商样品、样品评估、样品对比、打样对比、样品报告、规格核对、复测清单、供应商追问、面料规格、检测报告整理、样品差异。输入是一份用户自己整理的 JSON：带时区偏移的 as_of、requirement（requirement_id / name / specs[]，每条规格含 spec_id、name、kind（numeric 或 text）、required、min、max、unit、target）、suppliers[]（supplier_id / name / quote{price{value,currency},moq,lead_time_days} / samples[]，每个样品含 sample_id、batch、attachments[]、observations[]，每条观察含 spec_id、tested、value、unit、method、note）。判定完全固定且可复核：tested 不为 true 判 NOT_TESTED（tested 字段本身缺失时另记 TESTED_FLAG_MISSING）；tested 为 true 但没有可用数值或结论判 UNKNOWN 并记 EVIDENCE_MISSING；数值无法解析记 VALUE_INVALID；文本规格按 NFKC 加 casefold 加去空白归一化后逐字比较，相等判 PASS、否则 FAIL；数值规格先做单位检查再比较区间。单位检查顺序：规格无单位时只比较数值本身并记录 SPEC_UNIT_UNSPECIFIED；观察缺单位但规格有单位记 UNIT_MISMATCH；观察单位不在白名单内记 UNKNOWN_UNIT；观察单位与规格单位不在同一量纲分组记 UNIT_INCOMPATIBLE；以上任一情况一律 UNKNOWN、绝不判通过。同一 (sample_id, spec_id) 多条观察只取第一条参与判定并标记 DUPLICATE_OBSERVATION。样品结论：存在必填规格 FAIL 判 FAIL；否则存在必填规格 NOT_TESTED 或 UNKNOWN 判 INCONCLUSIVE；否则判 REQUIRED_SPECS_PASS；若该样品含被拒附件等使记录本身失效的问题则判 INVALID。附件引用含 / 、\\ 、.. 、URL scheme 或盘符直接判阻塞且不解析；观察记录引用需求里不存在的规格编号、或规格编号重复、或供应商或样品编号重复同样判阻塞。输出含 status、counts、specs、suppliers、samples、evidence_matrix、spec_summary、sample_differences（对可比较的数值规格给出极差与极差占均值比例，只做算术、不给合格判定）、non_comparable_items、retest_items、follow_up_questions、excluded_quotes、quote_summary_by_currency、attachment_index、observation_notes、decision_pack、findings 与 finding_counts、not_concluded、markdown_summary（Markdown 样品评估准备单）。安全上：凭据字段名或凭据形态的值直接 REJECTED 且不回显；自由文本只在「动作词 + 目标词同句共现」时判提示注入，只标精确路径、只替换为固定占位、绝不执行；控制字符剥离；Markdown 元字符转义；排序全部显式固定，同一输入输出逐字节一致。联系邮箱：43298568@qq.com。"
description_zh: "把多家供应商多个样品批次的观察结果，整理成逐规格证据矩阵（PASS/FAIL/UNKNOWN/NOT_TESTED），并给出样品间差异、复测补证清单、不可比较项、供应商追问清单与人工决策包。未测试、证据缺失或单位不可比较一律不判通过；单位换算只用白名单、跨量纲不换算；报价按币种分组、非数值或负数排除，不跨币种合计；附件只记安全化文件名、含路径或链接的一律拒绝且不解析；不给质量、认证、合规、法律或最终采购结论。"
description_en: "An offline evidence-matrix builder for supplier sample evaluation, aimed at small brands, e-commerce merchants, procurement leads and light manufacturing teams. It turns one user-assembled JSON (requirement specs plus per-supplier sample observations and quotes) into a spec-by-sample matrix where every cell is PASS, FAIL, UNKNOWN or NOT_TESTED, plus sample differences, a retest and evidence-collection list, non-comparable items, a supplier follow-up list and a human decision pack. A cell is never PASS unless the evidence supports it: untested, evidence-missing and unit-incomparable cells all stay unverified. Unit conversion uses a closed whitelist (mass, length, area, area density, percent, grade, count) and never crosses dimension groups; units outside the whitelist are unknown and cannot be judged. Text specs compare after NFKC, case folding and whitespace removal. Quotes are grouped by currency with invalid or negative prices excluded from the range and no cross-currency total or FX conversion. Attachments are recorded as sanitised bare file names only; any reference containing a path separator, parent segment, URL scheme or drive letter is refused without resolution and blocks the run. The tool never issues quality, safety, certification, compliance, legal or final purchasing conclusions. Credential-shaped input makes the whole run REJECTED without echoing the content. Deterministic: identical input yields byte-identical output."
version: 1.0.0
author: 苏格
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-supplier-sample-evaluation-pack
category: procurement-qc
tags: [供应商样品, 样品评估, 样品对比, 规格核对, 复测清单, 供应商追问, 单位换算, 证据矩阵]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 供应商样品评估准备包

打样阶段最贵的一类错误，是**把「没测」当成「没问题」**。样品到手，业务同事拍了照片，供应商口头说「和样品一样」，然后表格里就这么填了「合格」。等到批量到货才发现填充物比例、克重或者 pH 值从头到尾没人真正测过。

这个工具只做一件事：把「这个样品到底满足哪些要求」变成**一张逐规格、可复核的证据矩阵**，并且**在证据不足时明确拒绝下结论**。

它的三条硬线：

1. **未测试、证据缺失、单位不可比较，一律不判通过。** 不是「打问号」，是明确判 `UNKNOWN` / `NOT_TESTED` 并进入复测清单。
2. **单位换算只用白名单。** `g/m²` 与 `kg/m2` 是同一个单位，可以换算；`g` 与 `件` 不是同一个量纲，工具会判「不可比较」而不是硬凑。白名单之外的单位一律按未知处理。
3. **不给结论。** 不做质量、安全、认证、合规、法律和最终采购结论——这些需要资质，不是这个工具能替代的。

## 输入与澄清

字段表、单位白名单与逐格判定顺序见 @references/guide.md（同目录 `references/sample.json` 是可复现的「多供应商多批次」样例，`references/sample-ready.json` 是全部满足的样例，两份都用同一套规则）。

最少确认：`as_of`（**必须带时区偏移**）、`requirement.specs[]`、`suppliers[].samples[].observations[]`。

**必须问清的关键点**：

1. **`tested` 要如实填。** 只有 `tested: true` 且给出可用数值或结论才可能判通过；`tested: false` 判 `NOT_TESTED`；`tested` 这个字段本身忘了填，会另外记 `TESTED_FLAG_MISSING`——因为「没写」和「明确没测」不是一回事。
2. **`value` 为空不要留占位符。** 写成 `""`、`"待确认"`、`null` 都会被判 `UNKNOWN` 并进入补证清单，而不是被当成通过。
3. **单位请写原始报告里的单位。** 工具只做白名单内的换算（如 `kg` ↔ `g`、`kg/m2` ↔ `g/m2`）。写 `gram`、`丝`、`丹尼尔` 之类不在白名单里的，会被判「单位未知」并保持 `UNKNOWN`——**这是故意的**，猜错单位比不判断更危险。
4. **附件只写文件名。** 含 `/`、`\`、`..`、`https:` 的引用会被**拒绝且不解析**，并让整体变 `BLOCKED`；原引用不会被回显，输出里只保留**安全化后的文件名**（最后一段路径）。工具**不会打开**任何附件，也不会读取 PDF、图片或表格里的内容。
5. **同一规格在同一份样品上请只填一条观察。** 重复时只有第一条参与判定，并标记 `DUPLICATE_OBSERVATION`——工具不会替你合并两个互相矛盾的读数。
6. **不要把供应商账号、密码、Token 填进来。** 命中凭据会被**整体拒绝**且不回显。
7. **报价币种要写清楚。** 缺币种或为负数的报价会被排除在区间统计之外，且不做汇率换算。
8. **样品结论只是「证据是否齐全」的结论，不是「能不能买」的结论。** 请勿把 `REQUIRED_SPECS_PASS` 读成「这家可以下单」。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。先用 @references/sample.json 跑一遍，再用 @references/sample-ready.json 看全通过时的样子。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED`（疑似凭据，已拒绝且未回显）；`INPUT_INCOMPLETE`（缺 `as_of`、规格或供应商）；`BLOCKED`（存在阻塞项：附件引用非法、编号重复、引用了不存在的规格）；`GAPS_FOUND`（有未达标或证据不足，需人工处理）；`READY`（本次证据范围内无问题）。
4. 看 `evidence_matrix`：**横着读一家供应商的所有规格，竖着读一个规格在所有样品上的表现**。竖着读是这张表最主要的价值。
5. 看 `samples[].verdict` 与 `required_fail_specs` / `required_unverified_specs`：必填项失败与必填项缺证据是两件事，处理方式不同。
6. 看 `retest_items`：这就是你的复测与补证清单，只含必填规格。
7. 看 `non_comparable_items`：**先统一单位再看结论**，不要在单位不一致时下判断。
8. 看 `sample_differences`：极差与极差占均值比例只是算术，代不代表「批次不稳定」要你自己判断。
9. 看 `follow_up_questions`：按供应商分出的话题，直接拿去做追问清单。
10. 看 `attachment_index` 与 `observation_notes`：附件只记文件名（未打开），备注按不可信文本处理。
11. 按 `decision_pack` 逐条走完，再交给有资质的人判断。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开附件、不读取 PDF/图片/表格内容、不访问路径或链接、不写文件。
- **不结论**：不给质量、安全、健康、环保、认证、合规、法律、合同或最终采购结论；不做价格合理性、性价比或供应商选择建议。见输出中的 `not_concluded`。
- **不猜测单位**：换算只限白名单内的同量纲换算；白名单外一律 `UNKNOWN_UNIT` 并保持未判定。
- **不把缺失当通过**：未测试、字段缺失、数值无法解析、单位不可比较，全部不判通过。
- **不跨币种**：报价按 `currency` 分组，非数值或负数报价排除在区间外；**没有跨币种总额，也不做汇率换算**。
- **附件只记文件名**：含 `/`、`\`、`..`、`scheme:`、盘符或超长的一律拒绝且**不解析**，并按阻塞处理。
- **提示注入逐字段检测、位置精确、低误报**：只在「动作词 + 目标词」**同句共现**时命中（`忽略上述要求` 命中，`请忽略轻微的印刷重影` 不命中），命中后只标精确路径并替换为固定占位，绝不执行其中指令。
- **隐私门禁**：字段名命中 `password`、`token`、`api_key`、`secret` 一类凭据名，或字符串值具备真实凭据形态，**直接拒绝处理且不回显该内容**。
- **确定性**：排序全部显式固定（规格表顺序、样品输入顺序、样品差异按规格顺序、发现项按严重度/供应商/样品/代码/对象、币种字典序），同一输入输出逐字节一致。
- **免责**：输出是按你提交的观察整理的证据矩阵，不是检测报告；是否可用由你与有资质的人员决定。
