#!/usr/bin/env python3
"""Read-only Skill release security audit. Never executes scanned content."""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    STDLIB = set(sys.stdlib_module_names)
except AttributeError:  # Python < 3.10 fallback
    STDLIB = {"os", "sys", "json", "re", "math", "decimal", "pathlib", "subprocess",
              "argparse", "hashlib", "shutil", "tempfile", "zipfile", "stat", "glob",
              "csv", "datetime", "collections", "itertools", "functools", "typing",
              "urllib", "http", "email", "socket", "ssl", "time", "random", "string",
              "io", "base64", "logging", "warnings", "copy", "unittest", "contextlib",
              "dataclasses", "enum", "uuid", "calendar", "zoneinfo", "html", "xml"}

DEFAULTS = {"max_zip_mb": 20, "max_entries": 500, "max_extract_mb": 100, "max_file_mb": 20}

SECRET_FILENAME = re.compile(
    r"(^|/)(\.env(\.|$)|id_rsa|id_ed25519|id_dsa|.*\.pem$|.*\.key$|.*\.p12$|.*\.pfx$|"
    r".*\.jks$|credentials.*|secret.*|secrets.*|.*token.*\.(json|txt|yaml|yml)$)", re.I)
SECRET_CONTENT = [
    re.compile(r"-----BEGIN [A-Z0-9 ]*P[R]IVATE KEY-----"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{36}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"(?im)^(api[_-]?key|secret|passwd|password|access[_-]?token)\s*[:=]\s*[\"']?[A-Za-z0-9_\-.]{16,}"),
]
LOCAL_PATH = re.compile(r"(/U[s]ers/[A-Za-z0-9_.-]+|/h[o]me/[A-Za-z0-9_.-]+|C:\\U[s]ers\\[A-Za-z0-9_.-]+)")
RM_ROOT = re.compile(r"\brm\s+-(?:[a-z]*r[a-z]*f[a-z]*|[a-z]*f[a-z]*r[a-z]*)\s+/(?:\s|$|--)")
RMTREE_ROOT = re.compile(r"shutil\.rmtree\s*\(\s*[\"']/[\"']\s*\)")
OS_SYSTEM = re.compile(r"\bos\.system\s*\(")
SHELL_TRUE = re.compile(r"(?m)^\s*shell\s*=\s*True|subprocess\.(?:Popen|run|call|check_output|check_call)\s*\([^)]*shell\s*=\s*True")
PIPE_SH = re.compile(r"\b(?:curl|wget)\b[^|;\n]{0,200}\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b")
EXTERNAL_FETCH = re.compile(r"\b(?:requests|urllib\.request|httpx|aiohttp|curl|wget)\b[^\n]{0,120}(?:https?://|\.(?:get|post|request)\s*\()")
IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z0-9_\.]+)", re.M)
HIDDEN_NAME = re.compile(r"(^|/)(__pycache__|\.DS_Store|\.git)(/|$)|\.pyc$", re.I)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SCRIPT_SUFFIX = (".py", ".sh", ".js", ".rb", ".ps1", ".bat", ".php")


def finding(rule, severity, path, summary, fix, evidence=None, value=None):
    item = {"rule": rule, "severity": severity, "path": path, "summary": summary, "fix": fix}
    if evidence is not None:
        item["evidence"] = evidence
    if value is not None:
        item["matched_sha256"] = value
    return item


def sha256_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


class Audit:
    def __init__(self):
        self.findings = []
        self.files = []
        self.declared_pool = ""

    def add(self, item):
        self.findings.append(item)

    def scan_bytes(self, relpath, raw, is_script_text):
        text = raw.decode("utf-8", "replace")
        for pattern in SECRET_CONTENT:
            m = pattern.search(text)
            if m:
                self.add(finding("S002", "critical", relpath,
                                 "内容疑似包含密钥/口令（模式 " + pattern.pattern[:20] + "…）",
                                 "删除该凭据，改用环境变量或托管密钥；重新扫描确认无命中。",
                                 evidence="secret_content_marker", value=sha256_bytes(raw)))
                break
        if not is_script_text:
            return
        if LOCAL_PATH.search(text):
            self.add(finding("S003", "medium", relpath,
                             "包含本机/用户绝对路径，他人环境会失效且泄露目录结构",
                             "改为相对路径或 <skill-dir> 占位符。", evidence="absolute_user_path"))
        if RM_ROOT.search(text) or RMTREE_ROOT.search(text):
            self.add(finding("C001", "critical", relpath, "包含删除根目录的危险命令",
                             "移除；清理仅限临时目录并加存在性校验。", evidence="destructive_root_delete"))
        if OS_SYSTEM.search(text):
            self.add(finding("C002", "high", relpath, "使用 os.system 调用外部命令",
                             "改用 subprocess 参数数组形式，不经过 shell 解释。", evidence="os_system_call"))
        if SHELL_TRUE.search(text):
            self.add(finding("C002", "high", relpath, "调用点启用了 shell 解释，有命令注入面",
                             "关闭 shell 解释，用参数列表传命令。", evidence="shell_true"))
        if PIPE_SH.search(text):
            self.add(finding("C003", "critical", relpath, "下载内容直接管道给 shell 执行",
                             "移除；下载物先人工审查并固定 hash。", evidence="pipe_to_shell_download"))
        elif EXTERNAL_FETCH.search(text):
            self.add(finding("C004", "high", relpath, "脚本会发起外部网络下载/请求，需人工确认来源可信",
                             "确认下载源为可复核 URL 并在文档声明；不确定时移除。", evidence="external_fetch_visible"))
        imports = sorted({m.split(".")[0] for m in IMPORT_RE.findall(text)})
        third_party = [name for name in imports
                       if name not in STDLIB and name not in {"run", "__future__"}]
        if third_party:
            missing = [name for name in third_party if name not in self.declared_pool]
            if missing:
                self.add(finding("D001", "medium", relpath,
                                 "引用第三方包但未见声明：" + ",".join(missing),
                                 "在 SKILL.md 依赖清单或 requirements/package.json 中显式声明。",
                                 evidence="third_party_import:" + ",".join(missing)))


def scan_tree(root, audit, opts):
    """Scan one fully materialized directory tree (already safety-checked)."""
    root = Path(root).resolve()
    # Build declared-pool from manifests + markdown before scanning scripts.
    pool_parts = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in {".txt", ".json", ".toml", ".md", ".yaml", ".yml"} \
                and (path.name in {"requirements.txt", "package.json", "pyproject.toml", "SKILL.md"}
                     or path.suffix == ".md"):
            try:
                pool_parts.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    audit.declared_pool = "\n".join(pool_parts)

    for path in sorted(root.rglob("*")):
        try:
            if path.is_symlink():
                audit.add(finding("T002", "high", str(path.relative_to(root)),
                                  "符号链接：打包后可能指向外部文件", "移除链接，复制实际文件。"))
                continue
            if not path.is_file():
                continue
        except OSError:
            continue
        rel = str(path.relative_to(root))
        try:
            raw = path.read_bytes()
        except OSError as e:
            audit.add(finding("F099", "medium", rel, "无法读取文件：" + str(e), "检查权限。"))
            continue
        audit.files.append({"path": rel, "size": len(raw), "sha256": sha256_bytes(raw)})
        if SECRET_FILENAME.search(rel):
            audit.add(finding("S001", "critical", rel, "文件名命中敏感凭据特征（.env/私钥/证书等）",
                              "从发布包删除，密钥走环境变量。", evidence="secret_filename",
                              value=sha256_bytes(raw)))
        if len(raw) > opts["max_file_mb"] * 1024 * 1024:
            audit.add(finding("M001", "medium", rel,
                              "单文件超过 {}MB 上限".format(opts["max_file_mb"]),
                              "拆包或确认是否为必要资源。"))
        if HIDDEN_NAME.search(rel):
            audit.add(finding("M002", "low", rel, "包含缓存/隐藏文件，不应进入发布包",
                              "打包前排除 __pycache__/.DS_Store/.pyc 等。"))
        audit.scan_bytes(rel, raw, rel.endswith(SCRIPT_SUFFIX))
    audit_frontmatter(root, audit)
    return audit


def audit_frontmatter(root, audit):
    skill_files = sorted(Path(root).rglob("SKILL.md"))
    if not skill_files:
        audit.add(finding("F001", "high", "SKILL.md", "扫描树中未找到 SKILL.md，无法作为技能包发布",
                          "补充带 frontmatter 的 SKILL.md（放在技能根目录）。"))
        return
    for skill_md in skill_files:
        try:
            body = skill_md.read_text(encoding="utf-8")
        except OSError:
            audit.add(finding("F001", "high", str(skill_md.relative_to(root)), "SKILL.md 不可读", "检查文件。"))
            continue
        if not body.startswith("---\n") or body.count("\n---\n") < 1:
            audit.add(finding("F001", "high", str(skill_md.relative_to(root)),
                              "frontmatter 缺失或不完整", "补齐 name/version/description/license 等字段。"))
            continue
        front = body.split("---", 2)[1]
        keys = set()
        for line in front.strip().splitlines():
            key, sep, _ = line.partition(":")
            if sep:
                keys.add(key.strip())
        for required in ("name", "version", "description", "license"):
            if required not in keys:
                audit.add(finding("F001", "high", str(skill_md.relative_to(root)),
                                  "frontmatter 缺少字段：" + required, "补齐该字段。"))
    for md_path in sorted(Path(root).rglob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ref in re.findall(r"@((?:references|scripts|templates)/[A-Za-z0-9_.-]+)", text):
            if not (md_path.parent / ref).is_file():
                audit.add(finding("F002", "high", str(md_path.relative_to(root)),
                                  "Markdown 引用文件不存在：" + ref, "补上文件或删除引用。"))


def scan_zip(zip_path, audit, opts):
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            normalized_names = [name.replace("\\", "/") for name in names]
            duplicates = sorted({name for name in normalized_names if normalized_names.count(name) > 1})
            if duplicates:
                audit.add(finding("T003", "critical", "<zip>",
                                  "ZIP 含重复条目：" + ",".join(duplicates[:5]),
                                  "重建压缩包并确保每个规范化路径只出现一次。"))
                return audit
            total = sum(i.file_size for i in archive.infolist())
            if len(names) > opts["max_entries"]:
                audit.add(finding("X001", "critical", "<zip>",
                                  "ZIP 条目数 {} 超过上限 {}".format(len(names), opts["max_entries"]),
                                  "压缩包疑似 zip bomb 或打包错误。"))
                return audit
            if total > opts["max_extract_mb"] * 1024 * 1024:
                audit.add(finding("X001", "critical", "<zip>",
                                  "ZIP 解压总量约 {}MB 超过上限 {}MB，拒绝解压".format(
                                      round(total / 1048576, 1), opts["max_extract_mb"]),
                                  "压缩包疑似 zip bomb。"))
                return audit
            unsafe = False
            for name, normalized in zip(names, normalized_names):
                if ".." in Path(normalized).parts or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
                    audit.add(finding("T001", "critical", name, "ZIP 条目含路径穿越/绝对路径",
                                      "重建压缩包，条目必须为相对扁平路径。", evidence="path_traversal_entry"))
                    unsafe = True
                info = archive.getinfo(name)
                if stat.S_ISLNK(info.external_attr >> 16):
                    audit.add(finding("T002", "high", name, "ZIP 条目是符号链接", "移除符号链接条目。"))
                    unsafe = True
            if unsafe:
                return audit  # do not extract archives with unsafe entries
            tmp = Path(tempfile.mkdtemp(prefix="skill-audit-"))
            try:
                archive.extractall(tmp)
                scan_tree(tmp, audit, opts)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    except (zipfile.BadZipFile, OSError) as e:
        audit.add(finding("F099", "critical", "<zip>", "ZIP 无法打开或损坏：" + str(e),
                          "确认文件是有效 ZIP。"))
    return audit


def analyze(data):
    if not isinstance(data, dict) or "target" not in data:
        raise ValueError("input must contain a target path (directory or .zip)")
    target_raw = data["target"]
    if not isinstance(target_raw, str) or not target_raw.strip():
        raise ValueError("target must be a nonempty path")
    target = Path(target_raw)
    if not target.is_absolute():
        target = Path.cwd() / target
    target = target.resolve()
    opts = dict(DEFAULTS)
    for key in opts:
        if key in data:
            v = data[key]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(key + " must be numeric")
            opts[key] = float(v)
    audit = Audit()
    if not target.exists():
        audit.add(finding("F099", "critical", target.name or "<target>", "target 路径不存在", "检查输入路径。"))
    elif target.is_dir():
        scan_tree(target, audit, opts)
    elif target.suffix.lower() == ".zip":
        if target.stat().st_size > opts["max_zip_mb"] * 1024 * 1024:
            audit.add(finding("X001", "critical", target.name,
                              "ZIP 超过 {}MB 上限".format(opts["max_zip_mb"]), "确认包大小。"))
        else:
            scan_zip(target, audit, opts)
    else:
        audit.add(finding("F099", "critical", target.name or "<target>", "target 不是目录也不是 ZIP", "检查输入路径。"))
    verdict = "FAIL" if any(f["severity"] == "critical" for f in audit.findings) else \
              "REVIEW" if audit.findings else "PASS"
    return {
        "skill": "suge-skill-release-security-audit",
        "version": "1.0.1",
        "target": target.name or ".",
        "verdict": verdict,
        "summary": {"total": len(audit.findings),
                    "by_severity": {s: sum(1 for f in audit.findings if f["severity"] == s)
                                    for s in ("critical", "high", "medium", "low")}},
        "findings": sorted(audit.findings, key=lambda f: (SEVERITY_ORDER[f["severity"]], f["rule"])),
        "files": audit.files,
        "config_echo": {k: opts[k] for k in DEFAULTS},
        "note": "只读静态审计；未执行被扫描内容、未联网。PASS/FAIL/REVIEW 是发布门禁判定，不保证零漏洞，不替代人工代码审查。"
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="UTF-8 JSON with target path; maximum 2 MB")
    args = parser.parse_args()
    try:
        path = Path(args.input)
        raw = path.read_bytes() if path.exists() else open(args.input, "rb").read()
        if len(raw) > 2_000_000:
            raise ValueError("input exceeds 2 MB")
        data = json.loads(raw.decode("utf-8-sig"))
        result = analyze(data)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
        print(json.dumps({"error": "invalid_input",
                          "message": "请对照 references/guide.md 提供含 target（目录或 zip）的 JSON。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
