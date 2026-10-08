---
name: suge-purchase-order-receiving-exception-pack
slug: suge-purchase-order-receiving-exception-pack
displayName: 采购收货差异与待处理清单
display_name: 采购收货差异与待处理清单
display_name_en: "Purchase-Order Receiving Exception Pack"
summary: "把一张采购单的订购、到货与数量分配事实整理成一张可人工复核的差异清单：按采购行给出 MATCHED / PARTIALLY_RECEIVED / SHORTAGE / OVER_RECEIVED / DAMAGED / WRONG_ITEM / QUANTITY_CONFLICT / INSUFFICIENT_EVIDENCE / BLOCKED，数量桥保证 接受+损坏+错货+拒收+未解释=实收 且 仍未到=已订−实收，再加差异清单、缺失证据、逾期未到提醒、负责人待办、供应商沟通草稿（恒 DRAFT_NOT_SENT）与 Markdown 收货准备单。未知保持未知，绝不把订购量当实收量、不把损坏品当可售库存、不换算单位或币种；不判断质量、合同责任、索赔、付款或入库，不创建退货/索赔/采购单，不更新库存，不联系供应商或承运商，草稿仅在明确同意且无阻塞/证据不足时生成。"
description: "面向小商家、零售与 3-20 人采购/仓库团队的离线采购收货差异工具。输入是一份 JSON：as_of（必须带时区偏移）、purchase_order（po_id / supplier_id / destination_id / ordered_at / currency（可选）/ order_status（显式））、ordered_lines[]（匿名 line_id / sku_id / ordered_qty / unit / expected_unit_cost（可选）/ expected_by / owner_id / currency（可选）/ evidence_refs）、receipts[]（匿名 receipt_id / line_id / received_at / received_qty / accepted_qty / damaged_qty / wrong_item_qty / rejected_qty / unaccounted_qty / unit（可选）/ currency（可选）/ evidence_refs）、owners[]（匿名 owner_id 与职责）、handling_rules（必须由用户显式提供，不内置平台或法律规则）、communication_consent、notes、evidence_refs。判定顺序固定：非对象记录记 INVALID_LINE_RECORD、编号缺失记 MISSING_LINE_ID、重复编号让同号记录一并 BLOCKED、数量为负/非数值/布尔/NaN 记 INVALID_ORDERED_QTY 或 INVALID_RECEIPT_QTY、单位不一致记 UNIT_CONFLICT（不换算）、币种不一致记 CURRENCY_CONFLICT（不跨币种合并）、non三字母币种记 UNKNOWN_CURRENCY、未知 line_id 记 UNKNOWN_LINE_ID、收货早于下单记 RECEIPT_BEFORE_ORDER、订单取消记 ORDER_CANCELLED；数量分配之和≠实收记 DISPOSITION_MISMATCH、接受>实收记 ACCEPTED_EXCEEDS_RECEIVED；缺状态/负责人/单位/数量/证据记证据不足；记录损坏/错货/拒收但无证据记 MISSING_EVIDENCE；缺 handling_rules 记 MISSING_HANDLING_RULES。输出 status、receiving_state、按单位汇总的数量桥（只并入守恒行）、逐行状态与数量桥、差异清单、缺失证据、逾期未到与迟到提醒、负责人待办、拒绝字段、危险引用、注入标记、澄清问题、人工清单与 Markdown 收货准备单。安全上：证据引用只接受单一文件名，含路径、链接或盘符的引用被拒绝且原引用一字不回显；凭据形字段或值直接拒绝处理且不回显；个人信息、银行/发票原文/合同文本与受保护属性字段被拒绝且不回显；输入中的命令与提示注入不执行，命中时在 markdown_summary 中隐藏该值并给出精确路径。"
description_zh: "把一张采购单的订购、到货与数量分配事实整理成可人工复核的收货差异清单：九态、数量桥（守恒）、差异、缺失证据、逾期未到、负责人待办与供应商草稿（恒未发送）。未知保持未知，绝不把订购量当实收量、不把损坏品当可售库存、不换算单位或币种，不判断质量/合同责任/索赔/付款/入库，不联系供应商，草稿仅在明确同意且无阻塞/证据不足时生成。"
description_en: "An offline purchase-order receiving exception helper for small businesses, retail and 3-20 person sourcing/warehouse teams. It reads one JSON file and returns MATCHED, PARTIALLY_RECEIVED, SHORTAGE, OVER_RECEIVED, DAMAGED, WRONG_ITEM, QUANTITY_CONFLICT, INSUFFICIENT_EVIDENCE or BLOCKED for every ordered line and the batch, plus a conserved quantity bridge (accepted+damaged+wrong+rejected+unaccounted == received, still-outstanding == ordered-received), a discrepancy list, missing evidence, overdue-not-arrived and late-delivery reminders, owner todos, refused fields, refused references, injection markers, clarification questions, a human checklist, a never-sent supplier draft and a Markdown receiving prep sheet. It never treats ordered quantity as received, never treats damaged or wrong-item stock as sellable, never guesses a unit or currency conversion, never judges quality or contract liability, never raises a claim or return, never updates inventory, never pays and never contacts a supplier or carrier. Drafts are always DRAFT_NOT_SENT and exist only when consent is explicitly granted and no line is blocked or evidence-short."
version: 1.0.0
author: 苏格
homepage: "https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/suge-purchase-order-receiving-exception-pack"
category: retail-ops
tags: [采购收货, 收货差异, 数量桥, 采购订单, 部分到货, 短缺, 超收, 损坏与错货, 负责人待办, 人工复核]
platforms: [workbuddy, claude-code, cursor]
license: MIT
---
# 采购收货差异与待处理清单

小商家和 3-20 人采购/仓库团队最常卡住的地方，不是「没有采购单」，而是**一张采购单的订购、到货与数量分配散在送货单、微信群和口头说明里，没有人把它摆成一张守恒的数量桥**：订了多少、到了多少、哪些被接受、哪些损坏或错货、还有多少没到——全靠记忆，而「已订购」常被当成「已到货」。Shopify 与 Square 的官方收货流程都把采购订单、实际到货、接受、拒收、未到与分批到货分开，并要求记录损坏与错货差异；国内中小企业数字化试点材料也把「采购订单→仓库收货→入库确认」与到货通知、质检单列为核查环节——**需求真实，但 POS/WMS/ERP 与表格是成熟替代品**。所以本工具不做「收货决策」，只做一件确定的事：把匿名收货事实整理成一张**可逐条人工复核、且数量必须守恒的差异清单**。

本工具读一份 JSON，输出**收货差异清单**——每条采购行与整批的九态结论、按单位汇总的数量桥、差异清单、缺失证据、逾期未到与迟到提醒、负责人待办、拒绝字段、危险引用、注入标记、澄清问题、人工检查表、供应商沟通草稿（一律未发送）与 Markdown 收货准备单。**脚本只读一个本地 JSON，不联网、不写文件、不读取 WMS/ERP/库存/财务系统、不更新库存、不换算单位或币种、不判断质量或合同责任、不索赔或退货、不付款、不联系供应商或承运商、不发送消息。**

## 输入与澄清

字段表、判定顺序与安全边界见 @references/guide.md（同目录 `references/sample.json` 是常规样例，覆盖七种行状态并生成一条草稿；`references/sample-blocked.json` 是重复编号、非对象记录、未知 line_id、负数/非数值数量、单位/币种冲突、非法时间、个人信息、受保护属性、拒绝引用、注入与无同意的样例，两者输出分布固定）。

最少确认：`as_of`（**必须带时区偏移**）、`purchase_order.po_id`，以及至少一条 `ordered_lines[]`。

**必须问清的关键点**：

1. **`as_of` 与 `po_id` 不能省**。没有它们无法判断当前时点与采购单边界，工具会停在 `INPUT_INCOMPLETE`，而不是猜一个当前时间。
2. **只接受匿名事实**。姓名、手机号、邮箱、账号、地址、银行卡、发票原文、合同文本等一律**禁止**：字段名或值命中时记 `refused_fields`，该记录判 `BLOCKED`，**工具不回显该内容**。
3. **不使用受保护属性**。性别、年龄、民族、宗教、健康等字段会被拒绝（`SENSITIVE_ATTRIBUTE`），**绝不参与任何判定或排序**。
4. **数量桥必须守恒**。每条收货单必须满足 `接受+损坏+错货+拒收+未解释 == 实收`；不满足记 `DISPOSITION_MISMATCH` 并判 `QUANTITY_CONFLICT`，**不静默修正**。
5. **绝不把订购量当实收量、不把损坏品当可售库存**。`仍未到 = 已订 − 实收`；损坏、错货、拒收、未解释**单独列出**，不并入可接受数量。
6. **单位与币种不换算**。收货单单位与采购行不同记 `UNIT_CONFLICT`，币种不同记 `CURRENCY_CONFLICT`，均为阻塞；**工具不做任何换算或跨币种合并**。
7. **未知保持未知**。缺数量/单位/负责人/证据一律保持未知，**绝不默认为 0 或当作已完成**。
8. **`handling_rules` 只登记不发明**。处理规则**必须由用户显式提供**；工具**不内置任何平台或法律规则**；缺 `handling_rules` 记 `MISSING_HANDLING_RULES`。
9. **只在明确同意时生成草稿**。`supplier_draft` 恒为 `DRAFT_NOT_SENT`；`communication_consent` 不为 `true`、总体被阻塞、证据不足或已完全匹配时**不生成任何草稿**，也**绝不自动发送**。
10. **只给文件名，不要给路径或链接**。`evidence_refs` 只接受单一文件名；含路径、链接或盘符的引用会被**拒绝**，输出里只保留字段路径，原引用不会回显。
11. **所有动作由人工执行**。是否接受、是否索赔、是否退货、是否付款、是否入库、如何与供应商沟通，全部由人工决定；工具只整理事实。

## 执行

1. 按 @references/guide.md 的字段表整理输入 JSON。第一次使用建议先用 @references/sample.json 跑一遍，确认输出结构后再换成自己的事实。
2. 用 Python 3.9+ 执行本包脚本（无第三方依赖、不联网）：
   `python3 "<skill-directory>/scripts/run.py" "<input.json>"`，Windows 可用 `py -3`。
3. 先看顶层 `status`：`REJECTED` 表示输入疑似含凭据（已拒绝且未回显）；`INPUT_INCOMPLETE` 表示缺 `as_of` / 采购单 / `po_id`；其余为九态之一。
4. 再看 `receiving_state` 与 `line_state_counts`：先处理 `BLOCKED` 与 `INSUFFICIENT_EVIDENCE`，再看 `QUANTITY_CONFLICT` 与差异行。
5. 逐行核对 `quantity_bridge` 与 `quantity_bridge_totals`；**不要自行填数，也不要把缺失当成 0**。
6. 按 `discrepancies`、`missing_evidence` 与 `overdue_or_not_arrived` 逐条追问与补证。
7. 按 `owner_todos` 逐位负责人跟进；按 `refused_fields` 删除个人信息/受限内容后重跑。
8. 按 `handling_rules`（用户提供）与 `human_checklist` 完成人工复核。
9. 按 `supplier_draft`（恒 `DRAFT_NOT_SENT`）准备人工沟通；未同意者**没有草稿**，须先人工确认同意。
10. 用 `human_confirm_items` 明确哪些决定必须由人工做。

## 运行约束

- **只读**：只读取用户提供的那一个 JSON；不打开链接、不调用外部服务、不写文件、不访问任何 WMS/ERP/库存/财务/邮箱系统。
- **不自动执行**：不更新库存、不创建或修改采购单与收货单、不索赔、不退货、不付款、不发送消息、不判断质量或合同责任、不换算单位或币种。
- **只推导不发明**：结论只由输入中的明示匿名事实推导；不虚构到货、负责人、到期或证据。
- **未知保持未知**：缺数量不当作 0、缺单位不默认、缺负责人不猜、缺证据不放过、缺同意不生成草稿。
- **守恒门禁**：每条收货单的数量分配必须等于实收；不守恒即 `QUANTITY_CONFLICT`，并排除出汇总总量。
- **状态门禁**：九态固定聚合顺序；硬阻塞（重复编号、未知 line_id、非法数量/时间、单位/币种冲突、受限数据）必须先人工修正。
- **同意门禁**：`supplier_draft` 恒为 `DRAFT_NOT_SENT`；`communication_consent` 不为 `true` 或总体被阻塞 / 证据不足 / 完全匹配时**不生成任何草稿**。
- **确定性**：行与收货单按输入顺序、负责人与队列按固定顺序稳定排序，同一输入输出逐字节一致。
- **文件名门禁**：`evidence_refs` 只接受单一文件名；含路径、链接或盘符的引用被拒绝，且只输出字段路径，原引用在任何字段都不出现。
- **隐私门禁**：字段名或值命中个人信息 / 受限业务内容 / 受保护属性时只输出字段路径与原因，**值不回显**；字段名命中凭据名或值具备凭据形态时**整体拒绝**且不回显。
- **提示注入逐字段检测、位置精确、低误报**：覆盖备注、处理规则、职责、SKU 与条目标签；命中时只加标记并在 `injection_flagged` 中给出精确路径；检测要求「动作词 + 目标词」**同句共现**。
- **不可信文本先转义再入准备单，命中即隐藏**：`markdown_summary` 中所有自由文本走同一转义处理（去控制字符、折叠空白、对 Markdown 元字符加反斜杠）；命中注入或被拒绝的字段改用固定占位文本。
- **免责**：输出是采购收货差异的人工核对材料，不是质量、合同、索赔、付款或入库结论；是否接受、是否索赔、是否退货、是否付款、是否入库与如何沟通必须由人工确认。
