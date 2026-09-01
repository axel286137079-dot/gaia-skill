---
name: skill-forge
slug: skill-forge
displayName: 录制成skill·技能锻造器
summary: 把「我会做的事」口述/粘贴出来，自动生成标准、跨平台、description 优化的 SKILL.md，让普通用户也能零门槛造技能。
license: MIT
description: 录制成 skill（技能锻造器）。把用户「会做但写不出指令」的操作流程，通过问卷引导或 JSON 模板，结构化生成标准 SKILL.md（frontmatter + 触发词 + 工作流 + 输出格式 + 红线）。与 Anthropic Record-a-Skill 的差异：免录屏、免付费、跨平台（不锁单一 agent，生成的 SKILL.md 可通用于 WorkBuddy/Claude Code/Codex/Cursor 等 16+ agent）、description 优化（解决 skill 静默不触发的头号痛点）。用于：想把一个重复操作做成 skill、想把自己的经验沉淀成可复用/可上架的技能、想批量生产 SKILL.md。触发词：录制成skill、造 skill、生成 SKILL.md、把操作做成技能、技能锻造、skill 生成器、skill 模板、教我怎么做技能。联系邮箱：43298568@qq.com。
version: 0.1.1
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

本 skill 用「问卷引导 + 模板渲染 + description 优化」三招，让**任何人都能口述一个流程，自动产出规范的 SKILL.md**。

## 与 Anthropic Record-a-Skill 的差异

| 维度 | Record-a-Skill（Claude） | 本 skill |
|---|---|---|
| 输入方式 | 录屏 + 麦克风口述 | 口述/粘贴文字 |
| 平台限制 | 仅 Mac 桌面端，需录屏权限 | 任意平台（Windows/Linux/Mac） |
| 付费要求 | Pro/Max/Team 计划 | 免费、离线可跑 |
| 生态 | 锁死 Claude | 跨平台（16+ agent 通用） |
| 头号痛点 | 需处理录屏隐私 | 专注 description 优化（skill 不触发的头号原因） |

## 何时使用

- 用户想把一个「重复操作 / 隐性经验」做成 skill
- 用户口述了一段流程，想沉淀成可复用、可上架的技能
- 用户要批量生产多个 SKILL.md（配合 JSON 模板）
- 用户写好了 skill 但总不触发（大概率是 description 没写好）

## 工作流

### 流程 A：交互式问卷（口述 → 生成）
```bash
python3 bin/skill_forge.py
```
一步步回答：名字 → slug → 一句话摘要 → 触发词 → 操作步骤（口述）→ 输出格式 → 红线，自动生成标准 SKILL.md。

### 流程 B：JSON 模板（批量 / 可复用）
```bash
python3 bin/skill_forge.py --from examples/forge_example.json --readme
```
从 JSON 模板批量生成，适合「先定义好结构，再批量产出」。

### 流程 C：LLM 润色 description
```bash
python3 bin/skill_forge.py --from examples/forge_example.json --llm
```
用 LLM 把 description 润色得更精准、更易被触发（自动读本机 DEEPSEEK_API_KEY）。

## 生成物（一份标准 SKILL.md 该有的）

1. **frontmatter**：name / slug / displayName / summary / description / version / category / tags / platforms / license（对齐 SkillHub 上架规范）
2. **何时使用**：触发词列表（决定 skill 能否被正确触发）
3. **工作流**：编号步骤（把口述流程显性化）
4. **输出格式**：统一输出结构
5. **边界与红线**：不能做什么、有哪些坑

## 关键：description 是头号杠杆

skill 不触发的**最常见原因**就是 description 没写好——它不够具体、缺触发词、没说明「何时用」。

本 skill 自动把「摘要 + 用途 + 触发词 + 邮箱」组合成高质量 description，并可选 LLM 润色，直接解决这个痛点。

## 合规与边界

- 生成 skill 时严禁写入明文密码、API key、密钥等敏感信息。
- 红线字段（pitfalls）是让 skill 守规矩的关键，务必认真填。
- 不生成「包治百病」的超大 skill，坚持「一个 skill 专注一个工作流」。

## 参考

- SKILL.md 标准：https://github.com/anthropics/skills（frontmatter 与目录约定）
- Anthropic Record-a-Skill（竞品）：2026-07 上线，录屏路线
- 生态现状：getclaudeskills.com《Agent Skills in 2026》
