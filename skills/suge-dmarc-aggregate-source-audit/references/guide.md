# DMARC 聚合报告异常与发信源审计 · 字段与口径

## 1. 输入结构

```json
{
  "as_of": "2026-09-12T09:00:00+08:00",
  "org_domain": "example.com",
  "expected_sources": [
    {"source_id": "SRC-A", "ip": "203.0.113.10", "label": "Mailchimp"},
    {"source_id": "SRC-LEGACY", "ip": "203.0.113.10", "label": "Legacy Relay"}
  ],
  "thresholds": {
    "auth_failure_rate_pct": "10",
    "unknown_source_min_messages": 1,
    "min_sample_messages": 50
  },
  "xml_reports": [
    {"source_id": "REPORT-ALPHA", "xml": "<?xml version=\"1.0\"?><feedback>...</feedback>"}
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `as_of` | 是 | ISO8601 **带 UTC 偏移**的基准时间（不接受纯日期） |
| `org_domain` | 是 | 组织域，用于兜底与人工核对 |
| `expected_sources[]` | 是 | 允许的发信源；`source_id` 全局唯一，`ip` 必须为 IPv4；同一 `ip` 允许多条（多来源名） |
| `expected_sources[].label` | 否 | 展示名，缺省用 `source_id` |
| `thresholds` | 否 | 见下表；全部可选 |
| `xml_reports[].source_id` | 是 | **这份报告输入的标签**，用于错误定位；不是发信源 |
| `xml_reports[].xml` | 是 | DMARC 聚合报告 XML 字符串 |

### 阈值

| 字段 | 默认 | 作用 |
| --- | --- | --- |
| `auth_failure_rate_pct` | 无 | 认证失败率超过它时进 `attention` |
| `unknown_source_min_messages` | 1 | 未声明来源达到多少封才计入 `unknown_sources` |
| `min_sample_messages` | 无 | 有效报告覆盖邮件数低于它时标 `LOW_SAMPLE` |

## 2. 安全解析

- 单份 XML 上限 200000 字符，报告份数上限 500，单份记录数上限 20000。
- 拒绝 `DOCTYPE`、`ENTITY`、外部 `SYSTEM`/`PUBLIC` 标识与控制字符。
- 不解析文件路径、不打开 URL、不取回外部实体；`source_id` 里的 `../` 只当字符串。
- XML 结构不完整或截断时逐份标 `INVALID`，并尽量用 `<report_id>` 恢复标识；其余报告继续统计。

## 3. 去重与聚合口径

- 去重键：`report_id + org_name + date_range(begin,end)`。保留首份，重复份标 `DUPLICATE`，**不计入任何合计**。
- 消息数 = Σ`<count>`；记录数 = `<record>` 条数。两者分开输出，**不得互相替代**。
- 来源聚合按 `source_ip` 跨报告合并；同一 IP 的期望来源名全部保留。
- DMARC 通过 = `spf == pass` 且 `dkim == pass`。只一边 pass 记认证失败。
- 缺 `dkim` 或 `spf` 的记录记 `MISSING_ALIGNMENT`，计入 `unverifiable_message_total`，**排除在通过率分母之外**。
- 时间窗重叠：两份有效报告的窗口有交集时进 `window_overlaps`。

## 4. 状态口径

报告级：`VALID` / `DUPLICATE` / `INVALID`。

来源级：`OK` / `UNKNOWN_SOURCE` / `AUTH_FAILURE`。

总体（优先级从高到低）：

| 状态 | 触发条件 |
| --- | --- |
| `INVALID` | 没有一份有效报告 |
| `UNKNOWN_SOURCE` | 出现达到阈值封数的未声明来源 |
| `AUTH_FAILURE` | 存在认证失败邮件 |
| `POLICY_MISMATCH` | 策略声明与实际 disposition 不一致 |
| `PARTIAL` | 有报告解析失败，结论只覆盖部分报告 |
| `ATTENTION` | 命中声明的阈值（如失败率、样本量） |
| `PASS` | 以上均未触发 |

## 5. 策略不一致判定

- `policy_published.p` 为 `quarantine`/`reject` 且 `pct = 100`，但该记录 DMARC 失败而 `disposition = none` → `POLICY_MISMATCH`。
- `p = none` 但 `disposition` 为 `quarantine`/`reject` → `POLICY_MISMATCH`。
- 同一域的 `p` 与最早一份有效报告不同 → 该报告标 `POLICY_CHANGED`。

## 6. 常见误用

- 把 `record_count_total` 当邮件量 → 结论会差几个数量级。
- 只导出部分报告就下结论 → 应先看 `invalid_report_count` 与 `coverage`。
- 拿聚合统计当单封邮件证据 → 聚合只能说明趋势，定责需要原始日志。
- 看到失败就想直接跳到 `p=reject` → 先确认自有发信源是否漏登记。

## 7. 输出要点

`reports[]`、`sources[]`、`unknown_sources[]`、`auth_failures[]`、`policy_mismatches[]`、`coverage`、`window_overlaps[]`、`attention[]`、`manual_checklist[]`、`markdown_summary`。所有比例以 `xx.xx%` 字符串给出；无法计算时为 `null` 而不是 0。

## 8. 不做什么

不查询 IP 归属、不修改 DNS、不发送或拦截邮件、不打开路径或 URL、不解析外部实体、不建议直接从 `p=none` 跳到 `reject`、不把聚合统计当作单封邮件证据。
