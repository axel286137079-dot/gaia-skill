# 泄露密钥轮换与撤销证据闭环 · 字段与状态机

## 1. 输入结构

```json
{
  "as_of": "2026-09-12T09:00:00+08:00",
  "sla": {"ack_hours": 24, "rotation_hours": 72},
  "incidents": [
    {"incident_id": "INC-001", "provider": "cloud-vendor", "secret_type": "api_key",
     "fingerprint": "sha256:...", "environment": "production",
     "exposed_at": "2026-09-01T00:00:00+08:00", "detected_at": "2026-09-01T01:00:00+08:00",
     "severity": "medium", "owner": "alice"}
  ],
  "dependencies": [
    {"service_id": "SVC-A", "incident_id": "INC-001", "owner": "alice", "criticality": "high"}
  ],
  "events": [
    {"event_id": "EV-004", "incident_id": "INC-001", "type": "dependency_updated",
     "occurred_at": "2026-09-01T04:00:00+08:00", "evidence_id": "EVD-004",
     "actor": "alice", "service_id": "SVC-A"}
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `as_of` | 是 | ISO8601 **带 UTC 偏移**（不接受纯日期） |
| `sla.ack_hours` / `rotation_hours` | 否 | 通知时限与轮换时限；缺失则只算实际用时，不做超时判定 |
| `incidents[].incident_id` | 是 | 全局唯一 |
| `incidents[].fingerprint` | 建议 | **必须脱敏**（如 `sha256:` 前缀）；缺失会降为 PARTIAL |
| `incidents[].owner` | 建议 | 缺失会降为 PARTIAL |
| `incidents[].exposed_at` / `detected_at` | 建议 | 带时区；缺失时暴露窗口为 `null` |
| `incidents[].severity` | 否 | `low` / `medium` / `high` / `critical` |
| `dependencies[].service_id` | 是 | 全局唯一 |
| `dependencies[].incident_id` | 是 | 必须指向已存在的 incident，否则进 `orphan_dependencies` |
| `events[].event_id` | 是 | 重复出现时保留首次，重复单列 |
| `events[].incident_id` | 是 | 必须指向已存在的 incident |
| `events[].type` | 是 | 见事件类型表 |
| `events[].occurred_at` | 是 | 带时区，且不得晚于 `as_of` |
| `events[].evidence_id` / `actor` | 否 | 证据编号与操作人 |
| `events[].service_id` | 否 | **依赖类事件建议提供**，用于精确归属 |

### 事件类型

`detected` · `owner_notified` · `new_secret_created` · `dependency_updated` · `deployment_verified` · `old_secret_revoked` · `alert_closed` · `postcheck_passed`

只有 `dependency_updated` 与 `deployment_verified` 属于依赖类事件，可带 `service_id`。

## 2. 隐私门禁

命中任一条即**拒绝处理且不回显输入片段**：

- 字段名为 `secret_value` / `raw_secret` / `secret_plaintext` / `plaintext_secret` / `private_key` / `private_key_pem` / `password` / `passphrase`。
- 字符串匹配常见真实凭据样式：云厂商长令牌、访问密钥 ID、代码托管令牌、聊天平台令牌、JWT 三段式、PEM 私钥块、PEM 证书块。
- 长度 ≥ 32 且只由 `A-Za-z0-9+/=_-` 组成、又没有 `sha256:` / `fp:` / `hash:` / `redacted:` 等脱敏前缀的不透明字符串。

指纹请写成 `sha256:<64 位十六进制>` 这类形式。

## 3. 依赖覆盖率

| 指标 | 含义 |
| --- | --- |
| `total` | 该 incident 下的依赖总数 |
| `updated` | 有 `dependency_updated`（带匹配 `service_id`）的依赖数 |
| `verified` | 更新之后有覆盖到它的 `deployment_verified` 的依赖数 |
| `updated_but_unverified` | 已更新但缺验证证据 |
| `never_updated` | 完全没更新证据 |

`deployment_verified` 若不带 `service_id`，则覆盖所有在它之前完成更新的依赖。

## 4. 状态机（每 incident）

优先级从高到低：

| 状态 | 条件 |
| --- | --- |
| `PARTIAL` | 缺 owner / 缺 fingerprint / 缺检测时间 |
| `SLA_BREACH` | 通知或轮换用时超过声明 SLA |
| `CLOSED_WITH_EVIDENCE` | 已撤销 + 有后检 + 全部依赖已验证 + 告警已关闭 |
| `REVOKED_PENDING_VERIFY` | 已撤销，但后检 / 依赖验证 / 告警关闭未齐 |
| `READY_TO_REVOKE` | 已创建新密钥 + 全部依赖已更新且已验证，尚未撤销 |
| `ROTATION_IN_PROGRESS` | 已创建新密钥或已有依赖更新，尚未满足撤销前置条件 |
| `OPEN` | 只有检测或通知，尚无新密钥 |
| `INVALID` | 没有任何 incident 可审计 |

总体状态取所有 incident 中优先级最高者；存在 `orphan_dependencies` 时至少为 `PARTIAL`。

## 5. 冲突规则

| 代码 | 含义 |
| --- | --- |
| `REVOKE_BEFORE_VERIFY` | 旧密钥在部署验证之前就被撤销 |
| `REVOKE_BEFORE_DEPENDENCY_UPDATE` | 旧密钥在依赖更新完成之前就被撤销 |
| `REVOKE_BEFORE_NEW_SECRET` | 旧密钥在新密钥创建之前就被撤销 |
| `POSTCHECK_BEFORE_REVOKE` | 后检时间早于撤销时间 |

冲突**不修改原始时间**，只作为并列事实记录。

## 6. 暴露窗口

- 已撤销：`exposed_at → old_secret_revoked`。
- 未撤销：`exposed_at → as_of`，且 `old_secret_valid_window_open = true`。
- `exposed_at` 缺失时为 `null`，**不用 0 代替**。

## 7. 不做什么

不验证或撤销真实密钥、不访问 GitHub 或云平台、不改历史、不自动关闭安全告警、不判断是否构成合规事件。从代码删除字符串或关闭告警都不等于供应商侧撤销；`p=none` 式的"先观察"取舍需要安全负责人决策。
