# 字段、口径与输出规范（TLS 证书续期部署覆盖审计）

## 1. 输入口径

顶层对象：

| 字段 | 必填 | 说明 |
|---|---|---|
| as_of | 是 | 基准时间，ISO8601 **带时区** |
| certificates | 是 | 证书列表，1–500 项 |
| deployment_targets | 否 | 部署目标列表，0–1000 项（默认空） |
| observations | 否 | 观测记录列表，0–10000 项（默认空） |
| policy | 是 | 审计策略对象 |

policy 字段：`warn_days`（默认 30，到期预警窗口）、`max_observation_stale_hours`（默认 48，观测陈旧阈值）、`require_all_targets_deployed`（默认 true）。

certificate 字段：`certificate_id`（唯一，重复直接拒绝）、`serial_fingerprint`（可选；跨证书重复→线索）、`sans[]`、`issuer`（可空→ISSUER_UNKNOWN 线索）、`not_before`（可选）、`not_after`（必填）、`renewal_mode`、`renewal_status`、`validation_status`、`issued_replacement_id`（可空）。

deployment_target 字段：`target_id`（唯一）、`hostname`、`port`（默认 443）、`required_sans[]`（默认取 hostname）、`expected_certificate_id`（可空；为空时按 SAN 覆盖自动唯一匹配，匹配不到则不归属任何证书）。

observation 字段：`target_id`、`observed_at`、`observed_certificate_id`、`observed_fingerprint`、`hostname_match`、`chain_valid`。同一目标同一时间戳重复观测直接拒绝。

## 2. 计算口径

四个维度**分别评估**，互不掩盖：

1. **到期风险 expiry_risk**：`days_to_expiry = (not_after − as_of)/86400`。负数→EXPIRED；≤ warn_days→WARN；否则 OK。
2. **续期流程 renewal_process**：failed/blocked→BLOCKED；not_configured 在预警窗口内→BLOCKED、否则 NOT_STARTED；completed/renewed→COMPLETED；pending/paid→IN_PROGRESS；auto_enabled→SCHEDULED；其它→MANUAL。
3. **替代证书签发 replacement_issuance**：`issued_replacement_id` 非空→ISSUED；在预警窗口内但无替代证书→NOT_ISSUED；窗口外且无替代证书→NOT_REQUIRED。**已扣款/已续费不等于替代证书已签发**。
4. **部署覆盖 deployment_coverage**：`expected_deployed_id = issued_replacement_id 或 certificate_id`。逐目标取最新（且不晚于基准）的观测：
   - 无观测→NO_OBSERVATION（require_all_targets_deployed 时整证书降级 UNVERIFIED）
   - 观测年龄 > max_observation_stale_hours→STALE
   - chain_valid=false→CHAIN_INVALID
   - hostname_match=false→HOSTNAME_MISMATCH
   - 观测证书 ≠ expected_deployed_id→DRIFT
   - 否则 COVERED
   - SAN 覆盖：证书 `sans` 需覆盖目标 `required_sans`；支持单层通配 `*.example.com`（仅匹配同层数域名）。

## 3. 状态口径与优先级

| status | 触发 | 含义 |
|---|---|---|
| RENEWAL_BLOCKED | renewal_status 为 failed/blocked；或 not_configured 且已进预警窗口 | 续期链路断了，最需要人工介入 |
| REPLACEMENT_NOT_ISSUED | renewal_status 为已发起类（auto_enabled/pending/completed/renewed/paid）且在预警窗口内但无替代证书 | 以为续好了，其实没签发 |
| EXPIRING | 在预警窗口内（含已过期）且无替代证书签发记录 | 临期，需确认续期与部署时间窗 |
| DEPLOYMENT_DRIFT | 替代证书已签发但至少一个目标仍在观测旧证书 | 部署没铺开 |
| HOSTNAME_MISMATCH | SAN 未覆盖目标 required_sans，或观测 hostname_match=false | 域名不匹配 |
| CHAIN_INVALID | 观测 chain_valid=false，或证书 validation_status 非 valid 类 | 信任链有问题 |
| OBSERVATION_STALE | 最近观测超过陈旧阈值 | 数据太旧，结论不可信 |
| UNKNOWN | 缺观测、无部署目标、或 issuer 未知 | 证据不足 |
| INVALID | 结构非法（重复 ID、naive 时间、NaN、私钥内容） | 整体拒绝 |

判定优先级：RENEWAL_BLOCKED > REPLACEMENT_NOT_ISSUED > EXPIRING > DEPLOYMENT_DRIFT > HOSTNAME_MISMATCH > CHAIN_INVALID > OBSERVATION_STALE > UNKNOWN > PASS。

## 4. 输出结构

- 顶层：as_of、certificate_count、target_count、observation_count、status_counts、certificates[]、coverage_matrix[]、renewal_order[]（按最早到期排序）、review_flags[]、action_checklist[]、markdown_summary、note。
- 每条 certificate：certificate_id、serial_fingerprint、sans、issuer、issuer_known、not_before、not_after、days_to_expiry、expired、renewal_mode、renewal_status、validation_status、issued_replacement_id、expected_deployed_id、dimensions{expiry_risk, renewal_process, replacement_issuance, deployment_coverage}、targets[]、status、reasons、review_flags。
- `coverage_matrix` 是"证书—域名—部署目标覆盖矩阵"，`action_checklist` 是人工处置顺序。

## 5. 限制

- 只读审计：**不联网探测、不购买、不签发、不部署证书**，只依据用户提供的观测。
- 已续费/已扣款 ≠ 替代证书已签发；已签发 ≠ 已部署到所有目标。
- 时间必须带时区；缺失值不当作 0；未知保留 unknown。
- 疑似私钥/PEM 内容与含控制字符的字段一律拒绝，不写入输出。
- 样例（references/sample.json）为合成数据（CERT-A ~ CERT-I / T-A1 ~ T-I1）。
