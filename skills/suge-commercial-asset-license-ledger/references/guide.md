# 字段、判定规则与输出规范（商用素材授权台账）

## 1. 输入口径

顶层对象：`as_of_date`（ISO YYYY-MM-DD，基准日）、`project`（项目名）、`assets`（1–1000 项）。

每项素材字段：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| asset_id | 是 | 文本 | 素材唯一标识 |
| asset_type | 是 | image/font/music/video/template/other | 素材类型 |
| source | 是 | 文本 | 来源（图库/字体站/音乐库/供应商） |
| license_type | 是 | 文本 | 授权类型；缺失 → REVIEW |
| proof_reference | 是 | 文本 | 凭证引用（仅文本，脚本不回读该文件/URL）；缺失 → REVIEW |
| permitted_channels | 否 | 字符串数组 | 许可渠道集合 |
| actual_channels | 否 | 字符串数组 | 实际使用渠道；空/缺 → REVIEW |
| territories | 否 | 字符串数组 | 许可地域集合 |
| actual_territory | 否 | 文本 | 实际使用地域；越界 → BLOCK |
| expires_on | 否 | ISO 日期 或 "perpetual" | 到期日；缺 → REVIEW（不默认永久） |
| derivative_allowed | 否 | bool/null | 是否允许改编；null → REVIEW |
| attribution_required | 否 | bool/null | 是否要求署名；null → REVIEW |
| attribution_present | 否 | bool/null | 署名是否已落实 |

不接受命令、脚本、文件路径作为字段值语义；额外键一律忽略（视为数据）。

## 2. 判定规则（优先级从高到低）

1. **BLOCK**：已过期（expires_on < as_of）；实际渠道超出许可集合；实际地域不在许可集合；到期日即基准日（== as_of，视为边界风险）。
2. **REVIEW**：license_type / proof_reference 缺失；实际渠道或地域未登记；许可地域集合缺失；期限缺失或为"perpetual 之外未声明"；是否允许改编未声明；要求署名但署名未落实；署名要求未声明；30 天内到期。
3. **PASS**：凭证与许可范围齐备、实际使用均在许可内、未过期、改编与署名状态明确且无缺口。
4. 到期提醒窗口：`due_30_days`（1–30 天）、`due_60_days`（31–60）、`due_90_days`（61–90）。

## 3. 输出结构

- 顶层：`as_of_date`、`project`、`asset_count`、`status_counts`、`due_30_days`、`due_60_days`、`due_90_days`、`follow_up_tasks`、`assets[]`、`markdown_summary`。
- 每个素材 detail：字段回显 + `status`、`reasons[]`、`tasks[]`、`due_window`、`note`。
- `markdown_summary`：可直接渲染的台账表格与口径说明。

## 4. 使用提示与限制

- 凭证引用字段只是字符串：脚本不读取指向文件、不打开 URL；用户应自行保管真实凭证。
- 不联网核验授权真伪；不判断"必然侵权"；本台账不是法律意见，复杂争议转人工律师。
- 单项目单基准日；续期后请以新凭证更新该行并换基准日重跑。
- 样例（references/sample.json）为合成素材（示例图库/示例字体站），不代表真实版权方条款。
