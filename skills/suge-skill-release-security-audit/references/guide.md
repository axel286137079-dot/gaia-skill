# 规则、判定与报告规范（Skill 发布安全审计）

## 1. 输入格式

单个 JSON 对象：

```json
{
  "target": "/path/to/skill-dir 或 /path/to/skill.zip 或 .",
  "max_zip_mb": 20,
  "max_entries": 500,
  "max_extract_mb": 100,
  "max_file_mb": 20
}
```

`target` 必填；四个限制可选，均有默认值。目录会就地扫描（不修改）；ZIP 在满足大小/条目/总量上限后才解压到系统临时目录，扫描后清理。

## 2. 规则表

| 规则 | 级别 | 检查内容 |
|---|---|---|
| F001 | high | SKILL.md 缺失，或 frontmatter 缺失 name/version/description/license |
| F002 | high | Markdown 中 `@references/…`、`@scripts/…`、`@templates/…` 引用不存在 |
| S001 | critical | 文件名命中敏感凭据特征：`.env*`、`id_rsa`、`*.pem`、`*.key`、`*.p12`、`*.pfx`、`credentials*`、`secret*` 等 |
| S002 | critical | 内容含私钥头、`sk-…`、`AKIA…`、`ghp_…`、`xox…`、`AIza…`、`api_key=…` 等模式；只报 hash 与位置 |
| S003 | medium | 内容含本机/用户主目录绝对路径（macOS、Linux 或 Windows 用户目录前缀） |
| T001 | critical | ZIP 条目含 `../` 或绝对路径（路径穿越），拒绝解压 |
| T002 | high | 目录或 ZIP 内含符号链接 |
| T003 | critical | ZIP 含重复或规范化后重名的条目，拒绝解压 |
| C001 | critical | `rm -rf /`、`shutil.rmtree('/')` 等删根命令 |
| C002 | high | `os.system(...)`、`shell=True` |
| C003 | critical | `curl/wget … | sh` 下载后直接执行 |
| C004 | high | 脚本存在外部网络下载/请求（来源可见时标 REVIEW 而非一律 FAIL/通过） |
| D001 | medium | 脚本 import 第三方包但目录内 manifests/SKILL.md 未声明 |
| M001 | medium | 单文件超过 `max_file_mb` |
| M002 | low | 含 `__pycache__`、`.DS_Store`、`.pyc`、`.git` 等不应入包的内容 |
| X001 | critical | ZIP 条目数/文件体积/解压总量超限（疑似 zip bomb），拒绝解压 |
| F099 | critical/medium | target 不存在/损坏/不可读 |

## 3. 判定规则

- 任一 `critical` → **FAIL**（不要上传）。
- 无 critical、但有任何 `high/medium/low` → **REVIEW**（人工确认）。
- 没有任何发现 → **PASS**（可复跑门禁通过；不等于零漏洞，仍需人工审查）。

## 4. 输出结构（run.py 打印 JSON）

- `verdict`：PASS / FAIL / REVIEW。
- `summary`：findings 总数与按级别分布。
- `findings[]`：`rule`、`severity`、`path`（相对/脱敏）、`summary`、`fix`、`evidence`（类型/位置）、`matched_sha256`（凭据文件只给 hash）。
- `files[]`：`path`、`size`、`sha256`（作为版本指纹）。
- `config_echo`、`note`。

## 5. 中文报告结构（由代理依据 stdout JSON 生成 audit.md）

1. 扫描对象、时间、限制参数回显。
2. 结论框：PASS / FAIL / REVIEW 及一句理由。
3. 发现清单表：规则 ID / 级别 / 路径 / 问题 / 修复建议（按 critical→low 排序）。
4. 文件指纹表（path / size / sha256）。
5. 说明：本报告为静态门禁，不替代人工代码审查；凭据不回显。

## 6. 边界与限制

- 只扫本地文件；ZIP 仅临时解压；不执行、不联网、不写用户目录。
- 正则匹配是启发式：可能漏报（混淆/加密内容）也可能误报；凭据类发现一律按 critical 处理更安全。
- 不自动删除、不改写任何文件；修复建议由用户执行后重扫。
- 样例（references/sample.json）以 "." 指向解压后的技能自身，用于演示 PASS 路径；真实发布前请指向实际待发布包。
