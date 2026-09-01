# 录制成 skill · 技能锻造器

把「我会做的事」口述/粘贴出来，自动生成标准、跨平台、description 优化的 **SKILL.md**。

## 一句话定位

Anthropic 的 Record-a-Skill 要录屏、要 Mac、要付费计划；本工具**免录屏、免付费、跨平台**，靠问卷引导 + 模板渲染，让普通用户也能零门槛造技能。

## 为什么值得做

- 手写 SKILL.md 的最大摩擦：「知道怎么做」≠「能写出 AI 遵循的指令」。
- 领域专家懂流程但不会写 YAML，工程师会写但不懂业务边界——本工具把这条鸿沟填平。
- description 是 skill 能否被触发的**头号杠杆**，本工具自动优化它。

## 快速开始

### 交互式问卷
```bash
python3 bin/skill_forge.py
```

### JSON 模板批量生成
```bash
python3 bin/skill_forge.py --from examples/forge_example.json --readme
```

### LLM 润色 description（自动读本机 key）
```bash
python3 bin/skill_forge.py --from examples/forge_example.json --llm
```

## 生成物

一份标准 SKILL.md：frontmatter（对齐 SkillHub 规范）+ 触发词 + 工作流 + 输出格式 + 红线。

## 目录结构

```
skill-forge/
├── SKILL.md                    # 本 skill 的路由逻辑
├── README.md
├── bin/skill_forge.py          # 核心脚本（纯标准库，离线可跑）
└── examples/forge_example.json # JSON 模板示例
```

## License

MIT
