#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skill_forge.py — 录制成 skill（口述/粘贴 → 结构化 SKILL.md）

把「我会做的事」转成一份标准、跨平台、description 优化的 SKILL.md。
与 Anthropic Record-a-Skill 的差异：免录屏、免付费、不锁单一 agent，
靠「问卷引导 + 模板渲染 + description 优化」三条路把隐性经验显性化。

用法：
    交互式问卷：  python3 skill_forge.py
    JSON 模板：   python3 skill_forge.py --from forge.json
    生成 README： python3 skill_forge.py --from forge.json --readme
    LLM 润色：    python3 skill_forge.py --from forge.json --llm --api-key sk-xxx

纯 Python 标准库，无第三方依赖，离线可跑。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_PLATFORMS = ["workbuddy", "claude-code", "cursor"]
DEFAULT_LICENSE = "MIT"


def slugify(text):
    """从中文名尝试推导 kebab-case slug：提取其中的英文/数字，否则返回空"""
    latin = re.sub(r'[^a-zA-Z0-9]+', '-', text).strip('-').lower()
    return latin if latin else ""


def ask(question, default=None):
    """交互式提问，返回用户输入或默认值"""
    prompt = f"{question}" + (f" [{default}]" if default else "") + ": "
    try:
        ans = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    return ans if ans else (default or "")


def ask_multiline(question):
    """多行输入（口述步骤），空行结束"""
    print(f"{question}（每行一条，输入空行结束）：")
    lines = []
    try:
        while True:
            line = input().strip()
            if not line:
                break
            lines.append(line)
    except (EOFError, KeyboardInterrupt):
        pass
    return lines


def ask_list(question, default=""):
    """逗号分隔输入，返回 list"""
    ans = ask(question, default)
    return [x.strip() for x in ans.split(",") if x.strip()] if ans else []


def build_description(meta):
    """
    生成高质量 description —— 这是 skill 能否被正确触发的头号杠杆。
    结构：displayName + summary + 用途 + 触发词 + 邮箱
    """
    parts = [meta.get("displayName", meta["name"])]
    summary = meta.get("summary", "").strip()
    if summary:
        parts.append(summary)
    # 用途（从 steps 提炼）
    if meta.get("steps"):
        parts.append("用于：" + "、".join(meta["steps"][:4]) + "。")
    # 触发词
    if meta.get("trigger"):
        parts.append("触发词：" + "、".join(meta["trigger"]) + "。")
    return "".join(parts).strip()


def yaml_string(value):
    """JSON strings are valid quoted YAML scalars and avoid YAML injection."""
    return json.dumps(str(value), ensure_ascii=False)


def validate_meta(meta):
    """Validate interactive and JSON input before using it in paths or YAML."""
    if not isinstance(meta, dict):
        raise ValueError("输入必须是 JSON 对象")
    required = ("name", "displayName", "summary")
    missing = [key for key in required if not isinstance(meta.get(key), str) or not meta[key].strip()]
    if missing:
        raise ValueError("缺少必填文本字段：" + ", ".join(missing))
    name = meta["name"].strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", name) or "--" in name:
        raise ValueError("name 必须是 1-64 位 kebab-case，不得包含路径字符")
    for key in ("trigger", "steps", "pitfalls", "tags", "platforms"):
        if key in meta and (not isinstance(meta[key], list) or not all(isinstance(x, str) for x in meta[key])):
            raise ValueError(f"{key} 必须是字符串数组")
    return meta


def render_frontmatter(meta, profile="skillhub"):
    """渲染 YAML frontmatter"""
    fm = [
        "---",
        f"name: {yaml_string(meta['name'])}",
    ]
    if profile == "codex":
        fm.append(f"description: {yaml_string(meta['description'])}")
        fm.append("---")
        return "\n".join(fm)
    fm.extend([
        f"slug: {yaml_string(meta['name'])}",
        f"displayName: {yaml_string(meta['displayName'])}",
        f"summary: {yaml_string(meta['summary'])}",
        f"license: {yaml_string(meta.get('license', DEFAULT_LICENSE))}",
    ])
    if meta.get("homepage"):
        fm.append(f"homepage: {yaml_string(meta['homepage'])}")
    fm.append(f"description: {yaml_string(meta['description'])}")
    fm.append(f"version: {yaml_string(meta.get('version', '0.1.0'))}")
    fm.append(f"category: {yaml_string(meta.get('category', '工具效率'))}")
    fm.append("tags: [" + ", ".join(yaml_string(x) for x in meta.get('tags', [])) + "]")
    fm.append("platforms: [" + ", ".join(yaml_string(x) for x in meta.get('platforms', DEFAULT_PLATFORMS)) + "]")
    fm.append("---")
    return "\n".join(fm)


def render_skill_md(meta, profile="skillhub"):
    """渲染完整 SKILL.md"""
    parts = [render_frontmatter(meta, profile), "", f"# {meta['displayName']}", ""]
    if meta.get("summary"):
        parts += [meta["summary"], ""]
    parts += ["## 何时使用", ""]
    if meta.get("trigger"):
        parts += ["出现以下任一情况即启用本 skill：", ""]
        for t in meta["trigger"]:
            parts.append(f"- {t}")
        parts.append("")
    else:
        parts += ["（在此补充触发场景）", ""]
    if meta.get("steps"):
        parts += ["## 工作流", ""]
        for i, s in enumerate(meta["steps"], 1):
            parts.append(f"{i}. {s}")
        parts.append("")
    if meta.get("output"):
        parts += ["## 输出格式", "", "```", meta["output"], "```", ""]
    if meta.get("pitfalls"):
        parts += ["## 边界与红线", ""]
        for p in meta["pitfalls"]:
            parts.append(f"- {p}")
        parts.append("")
    return "\n".join(parts)


def render_readme(meta):
    """渲染 README"""
    lines = [
        f"# {meta['displayName']}",
        "",
    ]
    if meta.get("summary"):
        lines += [meta["summary"], ""]
    lines += [
        "## 快速开始",
        "",
        "```bash",
        "# 交互式问卷生成 SKILL.md",
        "python3 bin/skill_forge.py",
        "```",
        "",
        f"## 生成结果",
        "",
        f"本 skill 可把「口述/粘贴的操作流程」结构化生成标准 `SKILL.md`（含 frontmatter、触发词、工作流、输出格式、红线）。",
        "",
        f"## License",
        "",
        meta.get("license", DEFAULT_LICENSE),
        "",
    ]
    return "\n".join(lines)


def collect_interactive():
    """交互式问卷收集元数据"""
    print("=" * 56)
    print("录制成 skill —— 问卷式引导（回答完自动生成 SKILL.md）")
    print("=" * 56)
    meta = {}
    meta["displayName"] = ask("这个 skill 叫什么名字（中文，如「小红书文案助手」）")
    if not meta["displayName"]:
        print("名字不能为空，已退出。")
        sys.exit(1)
    # slug
    auto_slug = slugify(meta["displayName"])
    meta["name"] = ask("slug（英文 kebab-case，如 xiaohongshu-writer）", auto_slug)
    while not meta["name"] or not re.match(r'^[a-z0-9][a-z0-9-]*$', meta["name"]):
        meta["name"] = ask("slug 不合法（小写字母/数字/连字符），重新输入", auto_slug)
    meta["summary"] = ask("一句话说清楚它是干什么的")
    meta["trigger"] = ask_list("什么时候用它？（触发词/场景，逗号分隔）")
    meta["steps"] = ask_multiline("操作步骤（口述你平时怎么做这件事）")
    meta["output"] = ask("最终输出什么格式？（一句话）")
    meta["pitfalls"] = ask_multiline("有哪些坑/红线？（没有可跳过）")
    meta["category"] = ask("分类", "工具效率")
    meta["tags"] = ask_list("标签（逗号分隔）", meta["name"])
    return meta


def finalize_meta(meta):
    """补全默认字段 + 生成 description"""
    meta.setdefault("license", DEFAULT_LICENSE)
    meta.setdefault("version", "0.1.0")
    meta.setdefault("category", "工具效率")
    if not meta.get("tags"):
        meta["tags"] = [meta["name"]]
    meta.setdefault("platforms", DEFAULT_PLATFORMS)
    if not meta.get("description"):
        meta["description"] = build_description(meta)
    validate_meta(meta)
    if len(meta["description"]) > 1024:
        raise ValueError("description 不得超过 1024 字符")
    return meta


def output_dir(base, name):
    root = Path(base).resolve()
    target = (root / name).resolve()
    if target.parent != root:
        raise ValueError("输出路径越界")
    return target


def render_openai_yaml(meta):
    short = meta["summary"].strip()
    if len(short) > 64:
        short = short[:63] + "…"
    return "\n".join([
        "interface:",
        f"  display_name: {yaml_string(meta['displayName'])}",
        f"  short_description: {yaml_string(short)}",
        f"  default_prompt: {yaml_string('Use $' + meta['name'] + ' to help with this task.')}",
        "",
    ])


def main():
    ap = argparse.ArgumentParser(description="录制成 skill：口述/粘贴 → 标准 SKILL.md")
    ap.add_argument("--from", dest="from_file", help="从 JSON 模板生成（而非交互式）")
    ap.add_argument("--out", default=".", help="输出目录（默认当前目录）")
    ap.add_argument("--readme", action="store_true", help="同时生成 README.md")
    ap.add_argument("--llm", action="store_true", help="用 LLM 润色 description（需 --api-key）")
    ap.add_argument("--api-key", default=None, help="OpenAI 兼容 API key")
    ap.add_argument("--base-url", default=None, help="API base url")
    ap.add_argument("--model", default=None, help="模型名")
    ap.add_argument("--profile", choices=["skillhub", "codex"], default="skillhub",
                    help="输出平台元数据（默认 skillhub）")
    args = ap.parse_args()

    # 收集元数据
    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
    else:
        meta = collect_interactive()

    meta = finalize_meta(meta)

    # 可选 LLM 润色 description
    if args.llm:
        api_key = args.api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if api_key:
            meta["description"] = llm_polish_description(meta, api_key,
                                                         args.base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.deepseek.com/v1",
                                                         args.model or "deepseek-v4-flash")
            if not meta["description"] or len(meta["description"]) > 1024:
                raise ValueError("LLM 返回的 description 必须为 1..1024 字符")

    # 输出目录：以 skill 名为子目录
    out_dir = output_dir(args.out, meta["name"])
    out_dir.mkdir(parents=True, exist_ok=True)

    skill_md = render_skill_md(meta, args.profile)
    with open(out_dir / "SKILL.md", "w", encoding="utf-8") as f:
        f.write(skill_md)
    agents_dir = out_dir / "agents"
    agents_dir.mkdir(exist_ok=True)
    with open(agents_dir / "openai.yaml", "w", encoding="utf-8") as f:
        f.write(render_openai_yaml(meta))

    files = ["SKILL.md", "agents/openai.yaml"]
    if args.readme:
        readme = render_readme(meta)
        with open(out_dir / "README.md", "w", encoding="utf-8") as f:
            f.write(readme)
        files.append("README.md")

    print("\n" + "=" * 56)
    print(f"✓ 已生成到 {out_dir}/")
    for fn in files:
        print(f"  - {fn}")
    print("=" * 56)
    print("下一步：")
    print("  1. 检查 SKILL.md 的 description 是否准确（它决定 skill 能否被触发）")
    print("  2. 如需配套脚本，在目录里补 bin/ 并回填 SKILL.md 的调用说明")
    if args.profile == "skillhub":
        print("  3. 经人工复核后，可上架 SkillHub：skillhub publish <目录>")
    else:
        print("  3. 用目标 Codex 环境的 skill 校验器复核，再安装生成目录")


def llm_polish_description(meta, api_key, base_url, model):
    """用 LLM 润色 description，让它更精准、更易被触发"""
    import urllib.request
    prompt = (
        f"请为这个 skill 写一段高质量的 description（中文，200 字以内）：\n"
        f"名称：{meta['displayName']}\n"
        f"摘要：{meta['summary']}\n"
        f"触发场景：{'、'.join(meta.get('trigger', []))}\n"
        f"工作步骤：{'、'.join(meta.get('steps', [])[:5])}\n"
        f"要求：必须包含「何时使用」和「触发词」，让 AI 能准确判断何时调用；"
        f"语言简洁专业。只输出 description 正文，不要任何前缀。"
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6,
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        desc = data["choices"][0]["message"]["content"].strip()
        print(f"\n[LLM 润色 description]：{desc}\n")
        return desc
    except Exception as e:
        print(f"⚠ LLM 润色失败：{e}，用默认 description。", file=sys.stderr)
        return meta["description"]


if __name__ == "__main__":
    main()
