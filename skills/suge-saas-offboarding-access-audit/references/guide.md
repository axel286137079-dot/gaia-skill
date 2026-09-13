# SaaS 离职账号与访问撤销覆盖审计 · 字段与判定规则

## 1. 输入结构

```json
{
  "as_of": "2026-09-13T09:00:00+08:00",
  "sla": {"offboarding_hours": 72},
  "systems": [
    {"system_id": "SYS-CRM",
     "required_actions": ["disable_login", "revoke_sessions", "remove_roles", "delete_account"],
     "session_semantics": "session_cookie"}
  ],
  "departures": [
    {"subject_id": "S-1001", "last_working_at": "2026-09-08T18:00:00+08:00",
     "owner": "hr-ops", "risk_level": "low"}
  ],
  "accounts": [
    {"account_id": "ACC-1001", "subject_id": "S-1001", "system_id": "SYS-CRM",
     "status": "deleted", "roles": [], "licenses": [], "last_seen_at": "2026-09-08T17:40:00+08:00"}
  ],
  "assets": [
    {"asset_id": "ASSET-1001", "subject_id": "S-1001", "asset_type": "repo_ownership",
     "owner_after": "S-2001", "handover_status": "transferred", "evidence_id": "EVD-1101"}
  ],
  "actions": [
    {"action_id": "ACT-1001", "account_id": "ACC-1001", "type": "disable_login",
     "status": "success", "occurred_at": "2026-09-08T18:30:00+08:00",
     "evidence_id": "EVD-1001", "actor": "it-ops"}
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `as_of` | 是 | ISO8601 **带 UTC 偏移**（不接受纯日期） |
| `sla.offboarding_hours` | 否 | 离职生效到完成撤销的时限；缺失则不做超时判定 |
| `systems[].system_id` | 是 | 全局唯一 |
| `systems[].required_actions` | 是 | 该系统要求的动作类型 |
| `systems[].not_applicable_actions` | 否 | 明确不适用的动作；与必需项重叠时记声明缺口 |
| `systems[].session_semantics` | 否 | `stateless` / `session_cookie` / `api_token` / `hybrid`，默认 `stateless` |
| `departures[].subject_id` | 是 | 全局唯一；用不透明脱敏 ID，不要写真实姓名或邮箱 |
| `departures[].last_working_at` | 是 | 带时区 |
| `departures[].owner` / `risk_level` | 建议 | 缺失会降为 `PARTIAL` |
| `accounts[].account_id` | 是 | 全局唯一 |
| `accounts[].subject_id` / `system_id` | 是 | 必须指向已存在对象，否则进 `orphan_accounts` |
| `accounts[].status` | 否 | `active` / `disabled` / `deleted` / `not_found` / `unknown` |
| `accounts[].roles` / `licenses` | 否 | 用于呈现角色与许可残留 |
| `assets[].handover_status` | 否 | `transferred` / `pending` / `missing` / `not_required`，默认 `pending` |
| `actions[].action_id` | 是 | 重复出现时保留首次，重复单列 |
| `actions[].type` | 是 | 见动作类型表 |
| `actions[].status` | 是 | `success` / `failed` / `pending` |
| `actions[].occurred_at` | 是 | 带时区，且不得晚于 `as_of` |
| `actions[].evidence_id` | 成功时必需 | 缺失的成功动作**不计入覆盖** |

### 动作类型

`disable_login` · `revoke_sessions` · `revoke_tokens` · `remove_groups` · `remove_roles` ·
`remove_licenses` · `transfer_assets` · `archive_data` · `delete_account` · `verify_access_denied`

### 会话语义 → 隐含必需动作

| `session_semantics` | 隐含必需动作 |
| --- | --- |
| `stateless` | 无 |
| `session_cookie` | `revoke_sessions` |
| `api_token` | `revoke_tokens` |
| `hybrid` | `revoke_sessions` + `revoke_tokens` |

隐含动作未出现在 `required_actions` 中时，记入 `system_declaration_gaps`（系统配置问题），
并按"隐含 ∪ 必需 − 不适用"计算该系统的实际必需动作集合。

## 2. 隐私门禁

命中任一条即**拒绝处理且不回显输入片段**：

- 字段名为 `password` / `passwd` / `passphrase` / `secret_value` / `raw_secret` /
  `secret_plaintext` / `private_key` / `private_key_pem` / `api_token` / `access_token` /
  `refresh_token` / `bearer_token` / `session_cookie` / `cookie` / `cookies` /
  `client_secret` / `credential_value`。
- 字符串匹配常见真实凭据样式：云厂商长令牌、访问密钥 ID、代码托管令牌、聊天平台令牌、
  JWT 三段式、PEM 私钥块、PEM 证书块。
- 长度 ≥ 32 且只由 `A-Za-z0-9+/=_-` 组成、又没有 `sha256:` / `fp:` / `hash:` / `redacted:`
  等脱敏前缀的不透明字符串。

## 3. 逐主体判定顺序

自上而下，先命中先判定：

| 顺序 | 状态 | 触发条件 |
| --- | --- | --- |
| 1 | `PARTIAL` | 缺 `owner`、缺 `risk_level`，或该主体没有任何账号记录 |
| 2 | `ASSET_TRANSFER_BLOCKED` | 存在未交接资产，且该主体已有账号被删除（或已执行 `delete_account`） |
| 3 | `ACCESS_RESIDUE` | 至少一个账号缺必需动作（登录/会话/令牌/角色/许可/交接类） |
| 4 | `SLA_BREACH` | 完成用时（或至今已过时间）超过 `sla.offboarding_hours` |
| 5 | `IN_PROGRESS` | 有进展但未全量覆盖 |
| 6 | `COMPLETE` | 全部账号覆盖且无残留、无交接阻塞 |

顶层 `status` 取所有主体状态中**最靠前**的一个；存在 orphan 引用时至少为 `PARTIAL`。

## 4. 覆盖与残留口径

| 字段 | 含义 |
| --- | --- |
| `coverage.total_accounts` | 该主体在已登记系统中的账号数（`not_found` 账号仍计入总数） |
| `coverage.covered_accounts` | 全部实际必需动作均有"成功 + 时间 + 证据"的账号数 |
| `residual.logins / sessions / tokens / roles / licenses / handover / deletion / verification` | 按动作类型归集的残留账号（`not_found` 账号不计入任何残留桶） |
| `handover_pending_assets` | `pending` 或 `missing` 的资产 |
| `evidence_gaps` | 成功但缺证据的动作、未交接且缺证据的资产 |

残留桶与动作类型的对应：`disable_login→logins`、`revoke_sessions→sessions`、
`revoke_tokens→tokens`、`remove_groups`/`remove_roles→roles`、
`remove_licenses→licenses`、`transfer_assets`/`archive_data→handover`、
`delete_account→deletion`、`verify_access_denied→verification`。

## 5. 不适用场景

- 需要真实禁用/删除账号、撤销令牌或会话的场景。
- 需要访问身份供应商 API、SSO 控制台或 HR 系统的场景。
- 需要按姓名/邮箱直接检索员工的场景（本技能只接受不透明脱敏 ID）。
- 需要法律合规结论的场景；输出仅为审计准备材料。
