---
name: skill-forge
slug: skill-forge
displayName: 录制成skill·技能锻造器
summary: 把口述或 JSON 流程整理为可验证的 SKILL.md，支持 SkillHub 扩展字段与 Codex 严格 frontmatter 两种输出。
license: MIT
description: 用问卷或 JSON 将重复流程整理成 SKILL.md，校验安全的 kebab-case 名称、必填字段和列表类型，并可生成 SkillHub 扩展元数据或 Codex 兼容的 name/description frontmatter 与 agents/openai.yaml。适用于创建 skill、生成 SKILL.md、沉淀工作流和批量制作用例。
version: 0.1.2
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/skill-forge
category: 工具效率
tags: [skill生成, 技能锻造, 元工具, SKILL.md模板, 知识沉淀]
platforms: [workbuddy, claude-code, cursor]
---

# 录制成 skill · 技能锻造器

把「我知道怎么做」转成「AI 能照着做的 SKILL.md」——靠口述/粘贴，不靠写代码、不靠录屏。

## 核心洞察：为什么手写 skill 这么难

「知道怎么做一件事」和「写出 AI 能遵循的指令」之间，隔着一条大多数人跨不过去的鸿沟：

- 领域专家：懂流程、懂边界，但写不出规范的 YAML frontmatter 和 Markdown 指令；
- 工程师：会写 Markdown，但不了解业务里的隐性判断和边界情况。

本 skill 用「问卷引导 + 模板渲染 + 字段校验」把流程整理成可继续人工打磨的 skill 初稿。

## 何时使用

- 用户想把一个「重复操作 / 隐性经验」做成 skill
- 用户口述了一段流程，想沉淀成可复用、可上架的技能
- 用户要批量生产多个 SKILL.md（配合 JSON 模板）
- 用户写好了 skill 但触发不稳定，需要检查 description 与触发场景

## 工作流

### 流程 A：交互式问卷（口述 → 生成）
```bash
python3 bin/skill_forge.py
```
一步步回答：名字 → slug → 一句话摘要 → 触发词 → 操作步骤（口述）→ 输出格式 → 红线，自动生成标准 SKILL.md。

### 流程 B：JSON 模板（批量 / 可复用）
```bash
python3 bin/skill_forge.py --from examples/forge_example.json --readme --profile skillhub
```
从 JSON 模板批量生成，适合「先定义好结构，再批量产出」。

### 流程 C：LLM 润色 description
```bash
python3 bin/skill_forge.py --from examples/forge_example.json --llm
```
用 LLM 把 description 润色得更精准、更易被触发（自动读本机 DEEPSEEK_API_KEY）。

### 流程 D：生成 Codex 兼容包
```bash
python3 bin/skill_forge.py --from examples/forge_example.json --profile codex
```
该模式只在 `SKILL.md` frontmatter 保留 `name` 与 `description`，并生成 `agents/openai.yaml`。

## 生成物（一份标准 SKILL.md 该有的）

1. **frontmatter**：SkillHub profile 使用扩展字段；Codex profile 仅使用 `name` 与 `description`
2. **何时使用**：触发词列表（决定 skill 能否被正确触发）
3. **工作流**：编号步骤（把口述流程显性化）
4. **输出格式**：统一输出结构
5. **边界与红线**：不能做什么、有哪些坑

## 关键：description 是头号杠杆

skill 不触发的**最常见原因**就是 description 没写好——它不够具体、缺触发词、没说明「何时用」。

本 skill 自动把「摘要 + 用途 + 触发词」组合成 description，并可选 LLM 润色；最终触发效果仍需在目标 agent 中实测。

## 合规与边界

- 生成 skill 时严禁写入明文密码、API key、密钥等敏感信息。
- 红线字段（pitfalls）是让 skill 守规矩的关键，务必认真填。
- 不生成「包治百病」的超大 skill，坚持「一个 skill 专注一个工作流」。

## 参考

- SKILL.md 标准：https://github.com/anthropics/skills（frontmatter 与目录约定）
- Codex skills：以当前客户端和官方文档的校验规则为准。
