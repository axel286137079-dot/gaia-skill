# 字段、口径与输出规范（Webhook 投递与幂等核对）

## 1. 输入口径

顶层对象：

| 字段 | 必填 | 说明 |
|---|---|---|
| as_of | 是 | 基准时间，ISO8601 **带时区**；纯日期按 UTC 午夜处理 |
| expected_event_ids | 否 | 期望事件清单（数组或 null）。**缺失时只能审计已观察集合，不能声称完整无漏投** |
| deliveries | 是 | 投递尝试列表，0–5000 项 |
| processing_records | 是 | 处理记录列表，0–5000 项 |
| policy | 是 | 核对策略对象 |

policy 字段：

| 字段 | 默认 | 说明 |
|---|---|---|
| success_status_min / max | 200 / 299 | 视为成功的 HTTP 状态码闭区间 |
| max_response_seconds | 10 | 响应耗时上限；超过记为 timeout |
| dedup_key | ["event_id"] | 去重键字段，可选 event_id / event_type / object_id 的组合（最多 3 个，不可重复） |
| allow_out_of_order | true | 是否允许同一 object_id 下处理顺序与投递顺序不一致 |
| observation_window_seconds | 空 | 观察窗口（仅回显参考，不用于裁剪） |
| timezone | 空 | 期望时区（仅回显参考） |

delivery 字段：`delivery_id`（全局唯一，重复直接拒绝）、`event_id`、`event_type`、`object_id`、`attempt_no`（≥1）、`sent_at`、`http_status`（可空）、`response_at`（可空）、`manual_resend`、`signature_verified`（true/false/null）。

processing_record 字段：`event_id`、`object_id`、`event_type`、`processing_status`、`processed_at`、`side_effect_reference`（可空）。完全相同的五元组重复出现直接拒绝。

## 2. 计算口径

- **成功投递** = http_status 落在成功区间 **且** response_at 存在 **且** response_at ≥ sent_at **且** 响应耗时 ≤ max_response_seconds **且** sent_at ≤ as_of。
- 非成功投递按原因分类：`failed`（状态码不在区间）、`timeout`（响应过慢）、`no_response`（缺 response_at 或早于 sent_at）、`no_status`（缺 http_status）、`future`（sent_at 晚于基准）。
- **重试候选数** = 同一 event_id 的投递数 − 1。多次投递本身**不构成**业务重复执行的证据。
- **去重键值** = 按 policy.dedup_key 拼接的字符串（缺字段用 `-`）。
- **副作用引用集合** = 处理成功记录中非空 side_effect_reference 的去重集合。
- **乱序**：同一 object_id 下，按首次成功投递时间排序后，若处理时间不是单调不减，则后处理的事件标 `OUT_OF_ORDER`；`allow_out_of_order=false` 时该事件由 PASS 降级为 UNKNOWN。
- 疑似凭据（sk- 前缀长串、AKIA 访问键、PEM 私钥标记）与含控制字符的字段一律拒绝，不进入输出。

## 3. 状态口径

| status | 触发 | 含义 |
|---|---|---|
| PASS | 有成功投递、恰有一组副作用引用、签名已验证 | 重试后成功且幂等成立 |
| MISSING_DELIVERY | 期望清单中的事件没有任何投递 | 无法证明已送达 |
| DUPLICATE_EVENT_CANDIDATE | 同一去重键值出现在多个不同 event_id 上 | 疑似同一业务事件被重复投递 |
| DUPLICATE_EFFECT_CANDIDATE | 同一事件有 ≥2 个不同 side_effect_reference | 疑似业务副作用被执行了两次 |
| ACK_WITHOUT_PROCESSING_EVIDENCE | 有成功投递但没有处理成功记录 | 2xx 只证明收到，不证明执行 |
| RETRY_PENDING | 有投递尝试但无一次成功 | 重试仍在进行 |
| SIGNATURE_UNKNOWN | 成功投递的签名校验为 null/false/部分缺失 | 来源可信度未证实 |
| UNKNOWN | 证据不足（如仅处理记录无投递；或乱序降级） | 需人工补充 |
| INVALID | 结构非法（重复 delivery_id、naive 时间、NaN、凭据） | 整体拒绝 |

判定优先级：MISSING_DELIVERY > DUPLICATE_EVENT_CANDIDATE > DUPLICATE_EFFECT_CANDIDATE > ACK_WITHOUT_PROCESSING_EVIDENCE > RETRY_PENDING > SIGNATURE_UNKNOWN > PASS > UNKNOWN。

## 4. 输出结构

- 顶层：as_of、expected_set_provided、expected_event_total、event_count、status_counts、events[]、duplicate_dedup_groups[]、missing_deliveries[]、review_flags[]、replay_checklist[]、markdown_summary、note。
- 每条 event：event_id、object_id、event_type、dedup_key_value、in_expected_list、delivery_total、delivery_success、retry_candidates、processing_total、processing_success、side_effect_references、signature_state、status、reasons、review_flags、timeline[]（逐次尝试的 outcome 与说明）。
- `replay_checklist` 是"重放前人工核对清单"：只列待确认项，**本技能不发起重放**。

## 5. 限制

- 只读核对：不发起 webhook 重放、不写队列/数据库、不修改生产数据、不联网。
- 同一 event_id 多次投递 ≠ 业务重复执行；反之 2xx ≠ 业务已执行。
- 未提供期望清单时不得声称完整无漏投。
- 时间必须带时区；缺失值不当作 0；未知保留 unknown。
- 样例（references/sample.json）为合成数据（OBJ-1001 ~ OBJ-1007 / EV-100 ~ EV-200）。
