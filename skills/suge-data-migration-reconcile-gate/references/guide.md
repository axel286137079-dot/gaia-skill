# 数据迁移行数与结构一致性门禁 · 字段与判定规则

## 1. 输入结构

```json
{
  "as_of": "2026-09-13T09:00:00+08:00",
  "incremental_window": {"from": "2026-09-12T00:00:00+08:00", "to": "2026-09-12T23:59:59+08:00"},
  "tolerance": {"row_count_abs": 0, "row_count_rel_pct": "0.10"},
  "table_mappings": [{"source_table_id": "SRC-ORDERS", "target_table_id": "TGT-ORDERS"}],
  "exclusions": [
    {"exclusion_id": "EX-004", "table_id": "SRC-PAY", "check": "DISTINCT_COUNT",
     "column": "channel", "owner": "data-eng", "reason": "渠道映射调整期",
     "expires_at": "2026-09-30T00:00:00+08:00"}
  ],
  "source_tables": [
    {"table_id": "SRC-ORDERS", "captured_at": "2026-09-13T08:00:00+08:00", "row_count": 1200,
     "schema": [{"column": "order_id", "type": "bigint", "nullable": false, "key": true}],
     "null_counts": {"note": 30}, "distinct_counts": {"order_id": 1200},
     "min_max": {"amount": ["1.00", "9999.00"]},
     "hash": {"algorithm": "sha256", "salt_id": "SALT-1", "normalization": "trim_lower",
              "buckets": [{"bucket": "b0", "count": 600}, {"bucket": "b1", "count": 600}]}}
  ],
  "target_tables": ["…同上结构…"]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `as_of` | 是 | ISO8601 **带 UTC 偏移**（不接受纯日期） |
| `incremental_window.from` / `to` | 否 | 增量窗口；提供后所有表都要满足窗口约束 |
| `tolerance.row_count_abs` | 否 | 允许的绝对行数差，默认 0 |
| `tolerance.row_count_rel_pct` | 否 | 允许的相对行数差（百分比），默认 0 |
| `table_mappings[].source_table_id` / `target_table_id` | 是 | 各自在映射内唯一 |
| `*_tables[].table_id` | 是 | 各自集合内唯一 |
| `*_tables[].captured_at` | 建议 | 带时区；缺失时该映射判 `PARTIAL` |
| `*_tables[].watermark` | 否 | 增量水位；与 `incremental_window` 配合校验 |
| `*_tables[].row_count` | 是 | 非负整数 |
| `*_tables[].schema[]` | 否 | `column` / `type` / `nullable`（默认 true）/ `key` |
| `*_tables[].null_counts` / `distinct_counts` | 否 | 列名 → 计数；只比较两侧都提供的列 |
| `*_tables[].min_max` | 否 | 列名 → `[min, max]` 两元素数组 |
| `*_tables[].hash` | 否 | `algorithm` / `salt_id` / `normalization` / `buckets[]` |
| `exclusions[].exclusion_id` | 是 | 全局唯一 |
| `exclusions[].check` | 是 | 见下方允许值 |
| `exclusions[].owner` / `reason` / `expires_at` | 是 | 三者缺一即 `INVALID_EXCLUSION` |

### 豁免可作用的检查项

`COLUMN` · `TYPE` · `NULLABLE` · `ROW_COUNT` · `NULL_COUNT` · `DISTINCT_COUNT` · `MIN_MAX` · `HASH`

`exclusions[].column` 留空表示作用于该表该检查项的全部列；快照可比性（`SNAPSHOT`）**不可被豁免**。

## 2. 隐私门禁

命中任一条即**拒绝处理且不回显输入片段**：

- 字段名为 `dsn` / `connection_string` / `jdbc_url` / `database_url` / `password` / `passwd` /
  `passphrase` / `secret_value` / `raw_secret` / `private_key` / `private_key_pem` /
  `api_key` / `access_token` / `client_secret` / `credential_value`。
- 字符串匹配常见真实凭据样式（云厂商长令牌、访问密钥 ID、代码托管令牌、聊天平台令牌、
  JWT 三段式、PEM 私钥块、PEM 证书块）。
- 长度 ≥ 32 且只由 `A-Za-z0-9+/=_-` 组成、又没有 `sha256:` / `fp:` / `hash:` 等脱敏前缀的字符串。

## 3. 类型分组与兼容判定

| 分组 | 成员（按放宽顺序） |
| --- | --- |
| integer | tinyint < smallint < int / integer / serial < bigint < bigserial |
| decimal | real < float < double < decimal / numeric / money |
| text | char / character < varchar / nvarchar < string / text / clob |
| temporal | date / time < datetime / timestamp < timestamptz |
| boolean | boolean / bool |
| binary | binary < varbinary / blob / bytea |
| json | json < jsonb |
| uuid | uuid |

- 同组且等级不降 → `TYPE_WIDENED`（仅 INFO，不判漂移）。
- 同组但等级下降 → `TYPE_NARROWED`（HIGH，判漂移）。
- 跨组 → `TYPE_INCOMPATIBLE`（HIGH，判漂移）。
- text 组带长度参数时按长度比较；decimal 组带 `(p,s)` 时先比精度再比小数位。
- 类型字符串完全一致 → 直接 `OK`。

## 4. 逐表判定顺序

自上而下，先命中先判定：

| 顺序 | 状态 | 触发条件 |
| --- | --- | --- |
| 1 | `SCHEMA_DRIFT` | 映射指向的表缺少统计（`MISSING_TABLE`） |
| 2 | `SNAPSHOT_NOT_COMPARABLE` | watermark 落在窗口外，或快照早于窗口结束 |
| 3 | `SCHEMA_DRIFT` | 缺列/多列/类型不兼容或收窄/可空收窄 |
| 4 | `COUNT_MISMATCH` | 行数差超过允许值 |
| 5 | `CONTENT_MISMATCH` | 空值数/去重数/极值差，或哈希分桶差/缺失 |
| 6 | `PARTIAL` | 缺 `captured_at`，或哈希因口径不一致无法比较 |
| 7 | `MATCH` | 以上皆无 |

顶层 `status` 取所有映射中**最靠前**的一个；存在 orphan / 无效豁免时至少为 `PARTIAL`。

## 5. 不适用场景

- 需要连接数据库、拉取业务原始行或执行校验 SQL 的场景。
- 需要自动推断源到目标映射、自动修复或重跑迁移的场景。
- 需要在算法/盐/规范化不一致时仍强行比较哈希值的场景。
- 需要给出法律或财务合规结论的场景；输出仅为验收准备材料。
