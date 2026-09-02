#!/usr/bin/env python3
"""Validate repository skill structure without third-party dependencies."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PATH_RE = re.compile(r"`((?:bin|examples|references|assets)/[^`\s]+)`")


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("frontmatter 缺失或未闭合")
    raw = text[4 : text.find("\n---\n", 4)]
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        meta[key.strip()] = value
    return meta


def validate_skill(folder: Path) -> list[str]:
    errors: list[str] = []
    skill_md = folder / "SKILL.md"
    try:
        meta = parse_frontmatter(skill_md)
    except (OSError, ValueError) as exc:
        return [f"{folder.name}: {exc}"]
    name = meta.get("name", "")
    description = meta.get("description", "")
    if name != folder.name or not NAME_RE.fullmatch(name):
        errors.append(f"{folder.name}: name 必须与目录一致且为安全 kebab-case")
    if not description or len(description) > 1024:
        errors.append(f"{folder.name}: description 必须为 1..1024 字符")
    if not meta.get("version"):
        errors.append(f"{folder.name}: 缺少 version")
    if not (folder / "agents" / "openai.yaml").is_file():
        errors.append(f"{folder.name}: 缺少 agents/openai.yaml")
    text = skill_md.read_text(encoding="utf-8")
    for relative in PATH_RE.findall(text):
        cleaned = relative.rstrip(".,，。；;：:")
        if "|" in cleaned or "<" in cleaned:
            continue
        if not (folder / cleaned).exists():
            errors.append(f"{folder.name}: 引用了不存在的路径 {cleaned}")
    return errors


def main() -> int:
    folders = sorted(path for path in SKILLS.iterdir() if path.is_dir() and (path / "SKILL.md").is_file())
    errors = [error for folder in folders for error in validate_skill(folder)]
    if len(folders) != 12:
        errors.append(f"期望 12 个 skills，实际 {len(folders)}")
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"校验通过：{len(folders)} 个 skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
