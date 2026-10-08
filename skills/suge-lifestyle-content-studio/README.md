# 生活方式内容工作室

一个不依赖任何特定内容平台的图文创作 Skill：选题 → 标题 → 正文 → 封面文字 → 标签 → 风险词自查。

## 主要能力

- 把产品事实、受众和场景整理成可编辑的图文草稿。
- 固定交付 3 个标题、1 版正文、2–3 个封面文字和 3–8 个标签。
- 使用纯标准库脚本做离线高风险措辞提示，不上传原文。
- 不直接发布，不承诺流量、转化或审核结果。

## 快速开始

```bash
python3 bin/content_risk_check.py examples/sample_note.md
python3 bin/content_risk_check.py examples/sample_note.md --json
```

## 目录

```text
suge-lifestyle-content-studio/
├── SKILL.md
├── README.md
├── agents/openai.yaml
├── bin/content_risk_check.py
└── examples/sample_note.md
```

## 说明

自查工具只做启发式检测，不构成法律意见，也不代表任何内容平台的审核结论。发布前应由用户核对事实、资质、目标平台规则与 AI 标识要求。

## License

MIT
