# OpenAPI 破坏性变更影响门禁 · 字段与规则

## 1. 输入结构

```json
{
  "as_of": "2026-09-12T09:00:00+08:00",
  "old_spec": {"openapi": "3.0.3", "paths": {"/pets": {"get": {"responses": {"200": {}}}}}},
  "new_spec": {"openapi": "3.0.3", "paths": {"/pets": {"get": {"responses": {"200": {}}}}}},
  "consumer_usage": [
    {"client_id": "CLIENT-WEB", "method": "post", "path": "/store/orders",
     "fields": ["order_id"], "status_codes": ["201"]}
  ],
  "waivers": [
    {"id": "WV-001", "reason": "signed sunset plan", "owner": "platform-team",
     "expires_at": "2026-12-31", "kind": "REMOVED_OPERATION",
     "path": "/store/orders", "method": "post"}
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `as_of` | 是 | ISO8601 **带 UTC 偏移**（不接受纯日期） |
| `old_spec` / `new_spec` | 是 | JSON 对象，需含 `openapi`（或 `swagger`）与 `paths` |
| `consumer_usage[].client_id` | 是 | 调用方标识 |
| `consumer_usage[].method` | 是 | 小写 HTTP 方法（get/put/post/delete/options/head/patch/trace） |
| `consumer_usage[].path` | 是 | 以 `/` 开头 |
| `consumer_usage[].fields` | 否 | 调用方读取的响应字段名 |
| `consumer_usage[].status_codes` | 否 | 调用方依赖的响应码 |
| `waivers[].id` | 是 | 全局唯一 |
| `waivers[].reason` / `owner` | 是 | 说明与责任人 |
| `waivers[].expires_at` | 是 | `YYYY-MM-DD`；早于 `as_of` 即过期 |
| `waivers[].change_id` 或 `kind`+`path`+`method` | 否 | 匹配方式；不提供则落到 `unmatched` |

## 2. 破坏性规则表

| 类型 | 级别 | 说明 |
| --- | --- | --- |
| `REMOVED_OPERATION` | BREAKING | 删除 path 或 method |
| `ADDED_OPERATION` | INFO | 新增 path 或 method |
| `ADDED_REQUIRED_PARAMETER` | BREAKING | 新增必填参数 |
| `ADDED_OPTIONAL_PARAMETER` | INFO | 新增可选参数 |
| `OPTIONAL_TO_REQUIRED` | BREAKING | 参数由可选改必填 |
| `REQUIRED_TO_OPTIONAL` | INFO | 参数由必填改可选 |
| `PARAMETER_REMOVED` | REVIEW | 删除参数（服务端可能拒收旧客户端参数） |
| `REQUEST_BODY_NOW_REQUIRED` | BREAKING | requestBody 由可选改必填 |
| `REQUEST_BODY_ADDED` / `REQUEST_BODY_REMOVED` | BREAKING / REVIEW | 新增或移除请求体 |
| `REQUEST_MEDIA_TYPE_REMOVED` | BREAKING | 删除请求媒体类型 |
| `REQUEST_MEDIA_TYPE_ADDED` | INFO | 新增请求媒体类型 |
| `REQUEST_REQUIRED_FIELD_ADDED` | BREAKING | 请求体新增必填字段 |
| `REQUEST_FIELD_REMOVED` | REVIEW | 请求体删除字段 |
| `SUCCESS_RESPONSE_REMOVED` | BREAKING | 删除 2xx 响应码 |
| `RESPONSE_CODE_REMOVED` | REVIEW | 删除非 2xx 响应码 |
| `RESPONSE_MEDIA_TYPE_REMOVED` | BREAKING | 删除响应媒体类型 |
| `RESPONSE_REQUIRED_FIELD_ADDED` | BREAKING | 响应 schema 新增必填字段 |
| `RESPONSE_FIELD_REMOVED` | REVIEW | 响应 schema 删除字段 |
| `TYPE_CHANGED` / `FORMAT_CHANGED` | BREAKING | schema 类型或格式变化 |
| `ENUM_NARROWED` | BREAKING | 枚举取值收窄 |
| `ENUM_WIDENED` | INFO | 枚举取值扩大 |
| `ENUM_CHANGED` | REVIEW | 枚举取值同时有增有减 |
| `COMPLEX_SCHEMA_REVIEW` | REVIEW | 出现 additionalProperties / oneOf / anyOf / allOf / not / nullable |
| `SECURITY_SCHEME_REMOVED` | BREAKING | 仍在使用的安全方案被删除 |
| `SECURITY_REQUIREMENT_REMOVED` | REVIEW | 操作不再要求认证 |
| `CONSUMER_OPERATION_REMOVED` | BREAKING | 调用方使用的接口在新版本中消失 |
| `CONSUMER_FIELD_REMOVED` | BREAKING | 调用方读取的响应字段消失 |
| `CONSUMER_STATUS_CODE_REMOVED` | BREAKING | 调用方依赖的响应码消失 |

## 3. 引用解析

- 只解析本地指针 `#/...`。远程引用（`http(s)://`、相对文件）**不取回**，计入 `unresolved_refs`。
- 本地指针指向不存在的节点 → 计入 `unresolved_refs`。
- 循环引用不递归展开，遇到即停止，不会挂死。
- 任一侧无法解析时跳过该处比对，**不用猜测代替解析**。

## 4. 豁免

- 匹配优先级：`change_id` 精确匹配 → `kind + path + method` 匹配。
- 未过期匹配 → 该变更 `status = WAIVED`（`severity` 保留原级别）。
- 已过期匹配 → 不抑制，加 `EXPIRED_WAIVER` 标记。
- 未匹配 → 进 `waivers.unmatched`，需要人工确认是否写错。

## 5. 总体状态

| 状态 | 条件 |
| --- | --- |
| `INVALID` | 输入不是 JSON 对象、缺 `openapi` 或 `paths` |
| `PARTIAL` | 存在未解析引用或无法定位的调用方目标 |
| `BREAKING` | 存在未豁免的破坏性变更 |
| `REVIEW` | 只存在需人工复核的变更 |
| `PASS` | 无破坏性且无待复核项 |

`PARTIAL` 优先于 `BREAKING`：引用解析不完整时，"没发现问题"本身不可信。

## 6. 输出要点

`changes[]`（`change_id`/`kind`/`status`/`severity`/`path`/`method`/`location`/`detail`/`evidence`/`affected_consumers`/`waiver_id`/`review_flags`）、`breaking_changes[]`、`review_changes[]`、`waivers`、`consumer_impact[]`、`migration_checklist[]`、`unresolved_refs[]`、`partial_reasons[]`、`markdown_summary`。

`change_id` 由 `kind|method|path|location|detail` 派生，同一输入多次运行完全一致；JSON 键顺序变化不影响结果集。

## 7. 不做什么

不运行代码生成、不访问远程 `$ref`、不修改规范、不部署、不宣称语义兼容性已被完全证明。约定式枚举、同名不同义字段、隐式兼容不在覆盖范围内，需要人工评审补位。
