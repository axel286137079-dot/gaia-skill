# 供应商样品评估准备包 — 字段、单位与判定说明

本文件是 `SKILL.md` 的展开，也是你用来核对输出的依据。**所有判定都在这里写死**，脚本只做算术和排序。

## 1. 输入字段

### 1.1 顶层

| 字段 | 必填 | 说明 |
|---|---|---|
| `as_of` | 是 | **必须带时区偏移**，如 `2026-09-26T22:00:00+08:00`。缺失或非法 → `INPUT_INCOMPLETE` |
| `requirement` | 是 | 见 1.2 |
| `suppliers` | 是 | 见 1.3 |

### 1.2 `requirement`

| 字段 | 必填 | 说明 |
|---|---|---|
| `requirement_id` | 否 | 需求编号 |
| `name` | 否 | 需求名（Markdown 标题） |
| `specs[]` | 是 | 规格列表，见 1.3 |

### 1.3 `specs[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `spec_id` | 是 | 规格编号。缺失记 `MISSING_SPEC_ID`；重复记 `DUPLICATE_SPEC_ID`（**阻塞**） |
| `name` | 是 | 规格名（自由文本，走注入检测） |
| `kind` | 是 | `numeric` 或 `text`；其他值记 `SPEC_KIND_UNKNOWN` |
| `required` | 否 | `true` 为必填。**只有 `true` 才算必填**，缺失按可选处理并影响结论强度 |
| `min` / `max` | 数值规格需要 | 上下限。两者都缺失记 `SPEC_RANGE_MISSING`（待确认），该规格只能判 `UNKNOWN` |
| `unit` | 数值规格建议填 | 缺失记 `SPEC_UNIT_UNSPECIFIED`（待确认），此时只比较数值本身、不做单位检查 |
| `target` | 文本规格需要 | 期望值。缺失记 `SPEC_TARGET_MISSING` |

### 1.4 `suppliers[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `supplier_id` | 是 | 缺失记 `MISSING_SUPPLIER_ID`；重复记 `DUPLICATE_SUPPLIER_ID`（**阻塞**） |
| `name` | 否 | 供应商名（自由文本，走注入检测） |
| `quote.price.value` / `quote.price.currency` | 否 | 见 §4 |
| `quote.moq` / `quote.lead_time_days` | 否 | 原样展示 |
| `samples[]` | 是 | 见 1.5 |

### 1.5 `samples[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `sample_id` | 是 | 缺失记 `MISSING_SAMPLE_ID`；重复记 `DUPLICATE_SAMPLE_ID`（**阻塞**） |
| `batch` | 否 | 批次号 |
| `attachments[]` | 否 | **只写裸文件名**。含 `/`、`\`、`..`、`scheme:`、盘符或超 200 字符 → `INVALID_ATTACHMENT_REF`（**阻塞**，且**不解析**）。被拒引用的原文本**不会出现在输出里**：`filename` 置为 `null`，`basename` 只保留安全化后的最后一段文件名 |
| `observations[]` | 是 | 见 1.6 |

### 1.6 `observations[]`

| 字段 | 必填 | 说明 |
|---|---|---|
| `spec_id` | 是 | 必须是 `requirement.specs[]` 里的编号；否则记 `UNKNOWN_SPEC_ID`（**阻塞**） |
| `tested` | **视为必填** | 布尔。`true` 才可能判通过；`false` → `NOT_TESTED`；缺失 → `NOT_TESTED` + `TESTED_FLAG_MISSING` |
| `value` | `tested: true` 时必需 | 数值或文本。缺失/空串/`null` → `UNKNOWN` + `EVIDENCE_MISSING` |
| `unit` | 数值规格在规格有单位时必需 | 见 §3 |
| `method` | 否 | 测试或观察方法（自由文本，走注入检测） |
| `note` | 否 | 备注（自由文本，走注入检测，命中即隐藏） |

## 2. 逐格判定顺序（`specs` × `samples` 的每一格）

按顺序判定，**先命中先定**：

| 顺序 | 条件 | 结果 | 标记 |
|---:|---|---|---|
| 1 | `tested` 缺失 | `NOT_TESTED` | `TESTED_FLAG_MISSING` |
| 2 | `tested` 不为 `true` | `NOT_TESTED` | `NOT_TESTED` |
| 3 | `tested: true` 但 `value` 缺失/空 | `UNKNOWN` | `EVIDENCE_MISSING` |
| 4 | 文本规格且无 `target` | `UNKNOWN` | `SPEC_TARGET_MISSING` |
| 5 | 文本规格 | 归一化后相等 → `PASS`；否则 → `FAIL` | — |
| 6 | `kind` 不是 `numeric` | `UNKNOWN` | `SPEC_KIND_UNKNOWN` |
| 7 | 数值无法解析 | `UNKNOWN` | `VALUE_INVALID` |
| 8 | 单位检查（见 §3）不通过 | `UNKNOWN` | `UNIT_MISMATCH` / `UNKNOWN_UNIT` / `UNIT_INCOMPATIBLE` / `SPEC_UNIT_UNKNOWN` |
| 9 | 数值规格没有 `min` 也没有 `max` | `UNKNOWN` | `SPEC_RANGE_MISSING` |
| 10 | 落在区间内 | `PASS` | — |
| 11 | 超出区间 | `FAIL` | — |

> 同一 `(sample_id, spec_id)` 出现多条观察时，**只有第一条参与判定**，其余忽略，并给该格加 `DUPLICATE_OBSERVATION`。

### 2.1 文本规格的归一化

```
NFKC 归一化 → casefold → 去掉所有空白
```

因此 `90% 白鸭绒`、`90%白鸭绒`、`９０%白鸭绒` 视为同一结论。**不做同义词或近似匹配**——`80% 白鸭绒` 就是 `FAIL`。

## 3. 单位白名单（唯一允许换算的范围）

| 量纲 | 成员 | 内部换算 |
|---|---|---|
| `mass` | `mg` / `g` / `kg` | 以 `g` 为基准 |
| `length` | `mm` / `cm` / `m` | 以 `cm` 为基准 |
| `area` | `cm2` / `m2` | 以 `cm2` 为基准 |
| `area_density` | `g/m2` / `kg/m2` | 以 `g/m2` 为基准 |
| `percent` | `%` / `percent` | 同一单位 |
| `grade` | `级` | 同一单位 |
| `count` | `件` / `个` / `只` / `条` / `pcs` / `pc` | 同一单位 |

**归一化规则**：NFKC → 小写 → 去除空白。因此 `g/m²`、`G/M2`、`g / m2` 都是 `g/m2`。

**检查顺序**：

| 顺序 | 条件 | 结果 | 标记 |
|---:|---|---|---|
| 1 | 规格无 `unit` | 只比较数值本身（仍记录 `SPEC_UNIT_UNSPECIFIED`） | — |
| 2 | 观察无 `unit` 但规格有 | `UNKNOWN` | `UNIT_MISMATCH` |
| 3 | 观察单位不在白名单 | `UNKNOWN` | `UNKNOWN_UNIT`（并产生一条待确认发现项） |
| 4 | 观察与规格单位不同量纲 | `UNKNOWN` | `UNIT_INCOMPATIBLE`（并产生一条待确认发现项） |
| 5 | 规格单位本身不在白名单 | `UNKNOWN` | `SPEC_UNIT_UNKNOWN` |
| 6 | 同量纲 | 换算后进入区间比较 | — |

**跨量纲绝不换算。** `60 件` 不会变成克重；它被判 `UNIT_INCOMPATIBLE`，计入 `non_comparable_items`。

## 4. 报价规则

| 情况 | `price_state` | 处理 |
|---|---|---|
| 缺失 | `UNKNOWN` | 记 `PRICE_MISSING`（待确认），不进任何币种区间 |
| 非数值 | `INVALID` | 记 `INVALID_PRICE`（待确认），进 `excluded_quotes` |
| 负数 | `INVALID` | 记 `INVALID_PRICE`（待确认），进 `excluded_quotes` |
| 缺币种 | `NO_CURRENCY` | 记 `PRICE_CURRENCY_UNKNOWN`（待确认），进 `excluded_quotes`，**不做汇率猜测** |
| 有数值有币种 | `KNOWN` | 进 `quote_summary_by_currency` |

**只有 `KNOWN` 参与区间统计。** 输出中**不存在**任何跨币种总额字段。

## 5. 结论层级

### 5.1 样品结论 `samples[].verdict`

| 值 | 条件 |
|---|---|
| `INVALID` | 该样品有记录级问题（如附件引用被拒） |
| `FAIL` | 存在必填规格 `FAIL` |
| `INCONCLUSIVE` | 无必填 `FAIL`，但存在必填规格 `NOT_TESTED` 或 `UNKNOWN` |
| `REQUIRED_SPECS_PASS` | 必填规格全部 `PASS` |

> `REQUIRED_SPECS_PASS` **只说明必填规格的证据齐全且落在区间内**，不说明这家供应商可以下单。

### 5.2 整体 `status`

| 顺序 | 条件 | `status` |
|---:|---|---|
| 1 | 命中凭据门禁 | `REJECTED` |
| 2 | `as_of` 非法，或无规格，或无供应商 | `INPUT_INCOMPLETE` |
| 3 | 有阻塞项 | `BLOCKED` |
| 4 | 有必填未达标 / 有复测项 / 有不可比较项 / 有输入提示 | `GAPS_FOUND` |
| 5 | 其他 | `READY` |

阻塞项只有五类：`INVALID_ATTACHMENT_REF`、`DUPLICATE_SPEC_ID`、`UNKNOWN_SPEC_ID`、`DUPLICATE_SUPPLIER_ID`、`DUPLICATE_SAMPLE_ID`。理由：这五类会让**证据与要求的对应关系本身失效**，继续分析只会给出看似精确的错误答案。

## 6. 输出结构要点

| 键 | 内容 |
|---|---|
| `counts` | 规格/供应商/样品/观察数，`cell_counts`，`sample_verdict_counts`，必填未达标数、复测数、不可比较数 |
| `evidence_matrix[]` | 逐规格一行，`cells[]` 按样品顺序给出 `status` / `display_value` / `flags` |
| `spec_summary[]` | 逐规格的 PASS/FAIL/NOT_TESTED/UNKNOWN 计数 |
| `sample_differences[]` | 可比较数值规格的 `min` / `max` / `spread` / `spread_pct_of_mean`（**只做算术**） |
| `non_comparable_items[]` | 单位不可比较的格子及原因 |
| `retest_items[]` | 必填规格中 `NOT_TESTED` / `UNKNOWN` 的补证清单 |
| `follow_up_questions[]` | 按供应商分组的话题清单 |
| `quote_summary_by_currency` | 按币种的报价区间 |
| `excluded_quotes[]` | 未进入统计的报价及原因 |
| `attachment_index[]` | 附件文件名索引（**未打开、未读取、未解析**）；被拒引用只给 `basename`，不给原路径 |
| `observation_notes[]` | 观察备注（命中注入即隐藏） |
| `decision_pack[]` | 人工决策步骤 |
| `findings[]` / `finding_counts` | 全部发现项，按严重度/供应商/样品/代码/对象排序 |
| `not_concluded[]` | 本工具明确不结论的事项 |
| `markdown_summary` | Markdown 样品评估准备单，与 JSON 同源 |

## 7. 不适用与已知边界

1. **不替代检测报告。** 工具不读附件内容、不做任何实验或测量；它只整理你自己填的观察。
2. **不给认证、合规、法律或安全结论。** 见输出中的 `not_concluded`。
3. **不做价格合理性、性价比或供应商选择建议。** 只按币种给出报价区间。
4. **不预测批量表现。** 样品结果能否代表量产水平不在本工具判断范围内。
5. **不做统计显著性判断。** 样品间差异只给极差与极差占均值比例，不给「是否稳定」的结论。
6. **单位白名单是有限的。** 未收录的单位会被判未知而不是被猜；如果你的行业常用单位不在表内，请先自行统一成白名单单位再录入。
7. **不做供应商沟通。** `follow_up_questions` 是给你复制使用的草稿，工具不会联系任何人。
