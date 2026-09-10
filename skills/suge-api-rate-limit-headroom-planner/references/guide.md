# 字段、口径与输出规范（API 限额余量与退避预演）

## 1. 输入口径

顶层对象：

| 字段 | 必填 | 说明 |
|---|---|---|
| as_of | 是 | 基准时间，ISO8601 **带时区** |
| quota_profiles | 是 | 限额档位，1–200 项 |
| request_buckets | 否 | 用量桶，0–20000 项（默认空） |
| backlog | 否 | 待处理积压，对象或 null |
| policy | 是 | 规划策略对象 |

policy 字段：`safety_utilization`（默认 0.8，安全利用率线）、`throttle_target_utilization`（默认 0.7，保守节流目标）、`remaining_tolerance_abs`（默认 0，响应头余量与自算余量允许的绝对偏差）、`timezone`（仅回显）。

quota_profile 字段：`profile_id`（唯一，重复直接拒绝）、`provider`、`dimension_keys[]`（1–4 个字段，不可重复）、`window_seconds`（1–86400）、`limit`（**必须 > 0**）、`remaining`（可空，来自响应头）、`reset_at`（可空）、`concurrency_limit`（可空）、`secondary_limit_observable`（可空，非 true 即视为不可观察）。

request_bucket 字段：`profile_id`（必须已声明）、`dimensions`（对象，必须覆盖该 profile 的全部 `dimension_keys`）、`window_start` / `window_end`（window_end 必须晚于 window_start）、`request_count`、`error_429_403_count`（默认 0）、`retry_after_seconds[]`（默认空）、`max_concurrency`（默认 0）、`latency_ms_p95`（可空）。同一 profile + 同一维度组合 + 同一窗口重复出现直接拒绝。

backlog 字段：`profile_id`、`pending_tasks`、`requests_per_task`、`deadline`。

## 2. 计算口径

- **不跨维度合并**：按 `dimension_keys` 的取值元组分组，每个维度组合单独核算（多地域/多子账号必须分开看）。
- **当前窗口**：包含 `as_of` 的桶；若没有，取 `window_end` 最晚的桶。
- **利用率** = 当前窗口 request_count ÷ limit；**峰值利用率** = 同组最大 request_count ÷ limit。
- **自算余量** = limit − 当前窗口 request_count。
- **响应头余量一致性**：`|remaining − 自算余量| ≤ remaining_tolerance_abs`，否则记 `REMAINING_INCONSISTENT`。**不跨窗口相加**。
- **窗口长度核对**：桶的 `window_end − window_start` 与 profile 声明不一致时记 `WINDOW_LENGTH_MISMATCH`。
- **reset 跨日**：`reset_at` 的日期与 `as_of` 日期不同则 `reset_crosses_day = true`。
- **建议速率** = limit ÷ window_seconds × throttle_target_utilization（仅为模拟参数）。
- **积压计划**：总请求 = pending_tasks × requests_per_task；最短理论完成时间按整窗上限估算；保守完成时间按建议速率折算。

## 3. 状态口径

| status | 触发 | 含义 |
|---|---|---|
| RETRY_AFTER_ACTIVE | 任一桶出现 retry-after | 必须优先遵从服务端退避，不得用本地退避覆盖 |
| CONCURRENCY_RISK | 观测并发峰值 > concurrency_limit | 并发超限，先降并发 |
| THROTTLE_RECOMMENDED | 出现 429/403；或峰值利用率 ≥ safety_utilization | 主限额有余量也不等于安全，需降速 |
| SECONDARY_LIMIT_UNKNOWN | 次级限额不可观察且主限额正常 | 不能因主限额有余量就声称安全 |
| HEADROOM_OK | 主限额余量充足、无 429/403、并发与退避正常 | 可维持当前速率 |
| UNKNOWN | 该 profile 没有任何用量桶 | 无数据不下结论 |
| INVALID | 结构非法（limit ≤ 0、重复 profile、维度缺失、naive 时间、NaN、凭据） | 整体拒绝 |

判定优先级：RETRY_AFTER_ACTIVE > CONCURRENCY_RISK > THROTTLE_RECOMMENDED > SECONDARY_LIMIT_UNKNOWN > HEADROOM_OK > UNKNOWN。

积压计划另有独立状态：`BACKLOG_DEADLINE_RISK`（最短理论完成时间已超截止）/ `THROTTLE_RECOMMENDED`（理论可达但保守计划超截止，风险 TIGHT）/ `HEADROOM_OK`（REACHABLE）。

## 4. 输出结构

- 顶层：as_of、profile_count、bucket_count、status_counts、profiles[]、backlog_plan、review_flags[]、markdown_summary、note。
- 每条 profile：profile_id、provider、dimension_keys、window_seconds、limit、header_remaining、concurrency_limit、observed_max_concurrency、secondary_limit_observable、dimensions[]（逐维度组合：dimension_values、bucket_count、used_in_current_window、peak_request_count、utilization、utilization_pct、peak_utilization、self_calculated_remaining、header_remaining、remaining_consistent、error_429_403_count、retry_after_max_seconds、max_concurrency、latency_ms_p95、window_start/end、window_seconds_declared/observed、reset_at、seconds_to_reset、reset_crosses_day）、status、reasons、review_flags、recommended_rate_per_second、earliest_recovery_at、assumptions[]。
- backlog_plan：profile_id、total_requests、limit、window_seconds、deadline、theoretical_windows_needed、theoretical_min_seconds、theoretical_completion_at、conservative_rate_per_second、conservative_min_seconds、conservative_completion_at、deadline_reachable、risk_level、status、assumptions[]。

## 5. 限制

- **只做模拟**：不实际请求、不修改网关、不改变限流配置、不联网。
- 不跨窗口相加、不跨维度合并；响应头 remaining/reset 只在用户提供时作为证据。
- 次级限额不可观察时不得声称安全。
- 时间必须带时区；缺失值不当作 0；未知保留 unknown。
- 样例（references/sample.json）为合成数据（PROFILE-A ~ PROFILE-G / key-a ~ key-f）。
