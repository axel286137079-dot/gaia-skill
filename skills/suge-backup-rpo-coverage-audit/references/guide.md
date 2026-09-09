# 字段、公式与输出规范（备份 RPO 恢复点覆盖审计）

## 1. 输入口径

顶层对象：`as_of`（基准时间，ISO8601 **带时区**，必填；纯日期按 UTC 午夜处理）、`assets`（1–500 项）。

每条资产 asset 字段：

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| asset_id | 是 | 1–80 文本（字母数字 `._:-`） | 资产标识；重复直接拒绝 |
| criticality | 否 | critical / high / normal / low / unknown（默认 normal） | 缺口排序用 |
| target_rpo_hours | 是 | 数字 >0 | 目标恢复点目标（小时） |
| target_retention_days | 否 | 整数 ≥1 | 目标保留期（天）；缺失→不核对保留窗口 |
| policy_enabled | 否 | 布尔 | 备份策略是否启用；**非 true→POLICY_NOT_BOUND** |
| policy_interval_hours | 否 | 数字 >0 | 策略计划间隔，仅作为参考输出 |
| resource_bound | 否 | 布尔 | 策略是否绑定到资源；**非 true→POLICY_NOT_BOUND** |
| keep_at_least_one | 否 | 布尔（默认 false） | 仅"至少保留一份"不能替代长期保留证据 |
| cross_region_required / observed | 否 | 布尔 | required=true 而 observed 非 true→RESTORE_UNVERIFIED（合规线索） |
| immutable_required / observed | 否 | 布尔 | 同上 |
| last_restore_test_at | 否 | ISO8601 带时区 | 最近恢复测试时间 |
| last_restore_test_result | 否 | ≤20 文本 | success/passed/ok/succeeded 之外均视为未通过 |
| last_restore_test_evidence | 否 | ≤200 文本 | 恢复测试证据；success 但缺证据→不可称可恢复 |
| restore_points | 是 | 0–5000 项 | 恢复点列表 |

每条恢复点 restore_point 字段：

| 字段 | 必填 | 范围 | 说明 |
|---|---|---|---|
| rp_id / restore_point_id / id | 是 | 1–120 文本 | 同一资产内唯一；重复直接拒绝 |
| status | 是 | ≤20 文本 | success/succeeded/completed 才算有效点；failed/skipped 不算覆盖 |
| completed_at | 是 | ISO8601 带时区 | 完成时间；**晚于基准→忽略并标线索** |
| expiry_at | 否 | ISO8601 带时区 | 过期时间；≤基准视为已过期，不能用于恢复 |
| region | 否 | ≤60 文本 | 区域（跨区核对参考） |
| source_job_id | 否 | ≤120 文本 | 来源任务 |

时间戳必须带偏移（如 `2026-09-20T00:00:00+08:00`）；naive 时间戳拒绝（纯日期按 UTC 午夜）。布尔字段接受 true/false/1/0/yes/no。NaN/Infinity、重复 ID、命令/URL/提示词一律安全处理（命令只当文本不执行）。

## 2. 计算口径

- **有效恢复点** = status 为成功类 **且** 未过期（expiry_at 为空或 > 基准）**且** completed_at ≤ 基准。failed/skipped/过期/未来点不计入覆盖。
- **最新有效点年龄** = as_of − 最新有效点 completed_at（小时）。
- **相邻点最大间隔** = 相邻有效点 completed_at 的最大小时差。
- **RPO 覆盖**：最新点年龄 ≤ target_rpo_hours 且最大间隔 ≤ target_rpo_hours。
- **连续覆盖**：≥2 个有效点才能证明连续；**仅 1 个点：当前年龄可判，但连续覆盖标 UNKNOWN**（当前年龄也超 RPO 时仍判 RPO_GAP）。
- **保留窗口**：最早有效点年龄 ≥ target_retention_days 才认为覆盖保留期；keep_at_least_one 不替代长期保留证据；无有效点时保留不可判→缺口。
- **恢复测试单独评价**：备份成功 ≠ 可恢复。未测试、测试结果非 success 类、success 但缺测试日期/证据 → RESTORE_UNVERIFIED。
- **合规**：cross_region/immutable required 与 observed 分别核对，任一不满足→RESTORE_UNVERIFIED 线索。
- 备份策略未启用或资源未绑定 → POLICY_NOT_BOUND（不再往下判 RPO）。

## 3. 状态口径

| status | 触发 | 含义 |
|---|---|---|
| PASS | 策略在位、RPO 覆盖、保留覆盖、恢复测试有证据、合规满足 | 该资产审计通过 |
| RPO_GAP | 无有效点 / 最新点年龄超 RPO / 相邻最大间隔超 RPO | 数据可能丢失超过目标窗口 |
| RETENTION_GAP | 最早有效点覆盖不到保留期（keep_at_least_one 不豁免） | 长期保留目标未达 |
| POLICY_NOT_BOUND | policy_enabled 非 true 或 resource_bound 非 true | 策略未启用/未绑定资源，备份可能根本没执行 |
| RESTORE_UNVERIFIED | 恢复测试未跑/未过/缺证据；或跨区/不可变要求未满足 | 备份成功≠可恢复，恢复能力未证实 |
| UNKNOWN | 缺目标 RPO；或仅 1 个有效点（当前可判、连续性无法证实） | 数据不足 |
| INVALID | 结构非法（重复 ID、naive 时间、NaN 等） | 整体拒绝 |

判定优先级：POLICY_NOT_BOUND > RPO_GAP > UNKNOWN（连续性）> RETENTION_GAP > RESTORE_UNVERIFIED > PASS。
说明：仅 1 点且当前年龄 OK 时标 UNKNOWN（可判但连续性不足）；当前年龄超 RPO 时标 RPO_GAP。

## 4. 输出结构

- 顶层：as_of、asset_count、status_counts、assets[]、priority_gaps（按关键级排序的缺口清单）、markdown_summary。
- 每条 asset：asset_id、criticality、target_rpo_hours、target_retention_days、policy_enabled、policy_interval_hours、resource_bound、keep_at_least_one、status、restore_point_total、valid_restore_points、latest_valid_point_age_hours、max_adjacent_gap_hours、coverage_continuity（OK/GAP/UNKNOWN/NO_POINTS）、retention_ok、restore_verified、cross_region_met、immutable_met、review_flags、reasons、note。
- `markdown_summary` 可直接渲染，含"只有成功且未过期恢复点计入覆盖；备份成功≠可恢复"。

## 5. 限制

- 本技能只做**只读审计**：不执行备份、恢复、删除或策略修改。
- 备份成功与可恢复是不同字段：恢复测试未验证时不得宣称可恢复。
- 时间带时区；缺失值不为零；未知值保留 unknown。
- 样例（references/sample.json）为合成数据（DB-PROD-01 / FS-REPORTS-02 / VM-APP-03 / ARCHIVE-04）。
