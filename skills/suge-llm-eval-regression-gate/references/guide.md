# LLM 评测基线回归门禁 · 字段与判定规则

## 1. 输入结构

```json
{
  "as_of": "2026-09-13T09:00:00+08:00",
  "min_coverage": "1.0",
  "metric_definitions": [
    {"metric_id": "M-ACC", "direction": "higher", "scale": "0-1",
     "pass_threshold": "0.80", "max_allowed_regression": "0.02", "weight": "0.30"}
  ],
  "cases": [{"case_id": "C-003", "slice": "edge", "criticality": "critical"}],
  "baseline_results": [
    {"case_id": "C-003", "metric_id": "M-ACC", "score": "0.88", "judge_id": "judge-a",
     "scale": "0-1", "dataset_version": "ds-1", "evaluated_at": "2026-09-05T10:00:00+08:00"}
  ],
  "candidate_results": ["…同上结构…"],
  "human_labels": [
    {"case_id": "C-003", "metric_id": "M-SAFE", "label": false, "annotator_id": "h-1",
     "labeled_at": "2026-09-12T18:00:00+08:00"}
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `as_of` | 是 | ISO8601 **带 UTC 偏移**（不接受纯日期） |
| `min_coverage` | 否 | 0–1 的小数，默认 1.0 |
| `metric_definitions[].metric_id` | 是 | 全局唯一 |
| `metric_definitions[].direction` | 是 | `higher` / `lower` / `binary` |
| `metric_definitions[].scale` | 是 | 量表标识，如 `0-1` / `ms` / `bool` / `0-5` |
| `metric_definitions[].pass_threshold` | 数值指标必填 | `higher` 为下限，`lower` 为上限；`binary` 可省略 |
| `metric_definitions[].max_allowed_regression` | 是 | 非负；0 表示不允许任何反向变化 |
| `metric_definitions[].weight` | 是 | 非负；全部指标权重合计应为 1 或 100 |
| `metric_definitions[].cases` | 否 | 该指标适用的 case 列表；省略表示全部 case |
| `cases[].case_id` | 是 | 全局唯一 |
| `cases[].slice` | 否 | 切片名，用于分片统计 |
| `cases[].criticality` | 否 | `critical` / `normal`（默认）/ `low` |
| `results[].case_id` / `metric_id` | 是 | 必须指向已存在对象 |
| `results[].score` | 数值指标必填 | 有限数值；`NaN` / `Infinity` 直接报错 |
| `results[].label` | `binary` 指标必填 | 布尔值 |
| `results[].judge_id` | 是 | 裁判标识；基线/候选不一致即 `JUDGE_DRIFT` |
| `results[].scale` | 否 | 若提供且与指标 `scale` 不同即 `SCALE_DRIFT` |
| `results[].dataset_version` | 否 | 两侧都提供且不同即 `DATASET_DRIFT` |
| `results[].evaluated_at` | 否 | 带时区，且不得晚于 `as_of` |
| `human_labels[].annotator_id` | 是 | 标注人标识；同一 `(case_id, metric_id)` 只能有一条 |

## 2. 隐私门禁

命中任一条即**拒绝处理且不回显输入片段**：

- 字段名为 `api_key` / `access_token` / `client_secret` / `secret_value` / `raw_secret` /
  `password` / `passwd` / `passphrase` / `private_key` / `private_key_pem` / `credential_value`。
- 字符串匹配常见真实凭据样式（云厂商长令牌、访问密钥 ID、代码托管令牌、聊天平台令牌、
  JWT 三段式、PEM 私钥块、PEM 证书块）。
- 长度 ≥ 32 且只由 `A-Za-z0-9+/=_-` 组成、又没有 `sha256:` / `fp:` / `hash:` 等脱敏前缀的字符串。

## 3. 回归与通过口径

| 方向 | 通过条件 | 回归条件 |
| --- | --- | --- |
| `higher` | 候选 ≥ `pass_threshold` | 候选 − 基线 < −`max_allowed_regression` |
| `lower` | 候选 ≤ `pass_threshold` | 候选 − 基线 > `max_allowed_regression` |
| `binary` | `label = true` | 基线 `true` 且候选 `false` |

关键用例（`criticality = critical`）**回归或未通过**即记 `CRITICAL_CASE_FAIL`。

## 4. 逐指标判定顺序

自上而下，先命中先判定：

| 顺序 | 状态 | 触发条件 |
| --- | --- | --- |
| 1 | `NOT_COMPARABLE` | 该指标有可比样本，但全部样本因漂移被排除 |
| 2 | `CRITICAL_CASE_FAIL` | 至少一个关键用例回归或未达阈值 |
| 3 | `REGRESSION` | 至少一个样本回归 |
| 4 | `COVERAGE_GAP` | 覆盖率低于 `min_coverage` |
| 5 | `PARTIAL` | 部分样本因漂移被排除 |
| 6 | `INVALID` | 该指标没有任何可比样本 |
| 7 | `PASS` | 以上皆无 |

顶层 `status` 取所有指标中**最靠前**的一个；权重不闭合时至少降为 `PARTIAL`。

## 5. 聚合口径

- 聚合分**只用各指标通过率**（同一 0–100% 量纲）按权重归一化计算，`used_weight` 为实际参与归一化的权重合计。
- `NOT_COMPARABLE` 的指标**不参与**聚合。
- 不跨量表直接相加原始分（例如 `0-1` 的准确率与 `ms` 的延迟不混合）。
- `aggregate_masks_critical` 为 `true` 表示"聚合分没有变差、但存在关键用例失败"，此时聚合分不能作为放行依据。

## 6. 不适用场景

- 需要调用模型、重跑评测或重评分的场景。
- 需要在裁判模型、量表或数据集变化后仍直接比较分数的场景。
- 需要输出统计显著性结论但样本量不足的场景（本技能不输出无样本支撑的显著性）。
- 需要给出业务质量结论的场景；输出仅为发布评审准备材料。
