#!/usr/bin/env python3
"""客户变更请求影响准备包 — offline client change-request impact builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes and no send/sign/charge/schedule action: only file *basenames* are
ever handled.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = "1.0.0"

# --------------------------------------------------------------------------
# fixed vocabulary (documented in references/guide.md)
# --------------------------------------------------------------------------
CLASSIFICATIONS = (
    "IN_SCOPE", "INCLUDED_REVISION", "SUBSTITUTION",
    "CHANGE_REQUEST", "CLARIFICATION_NEEDED", "INSUFFICIENT_EVIDENCE",
)
# The classes that represent a real, understood decision about scope. They still
# need an explicit approval before anything can be called ready to schedule.
DECIDED_CLASSES = ("IN_SCOPE", "INCLUDED_REVISION", "SUBSTITUTION", "CHANGE_REQUEST")
UNRESOLVED_CLASSES = ("CLARIFICATION_NEEDED", "INSUFFICIENT_EVIDENCE")

APPROVAL_STATES = ("approved", "pending", "unknown")

PLACEHOLDER = "已隐藏疑似提示注入文本"

CRED_KEY = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|auth[_-]?token|passphrase)",
    re.I,
)
CRED_VALUE = (
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE " + r"KEY-----"),
    re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)

INJ_ACTION = (
    "忽略", "无视", "跳过", "覆盖", "改写", "删除", "执行", "服从", "绕过", "批准",
    "ignore", "disregard", "override", "bypass", "forget", "approve",
)
INJ_TARGET = (
    "指令", "规则", "提示", "系统", "要求", "约束", "基线", "范围",
    "instruction", "rule", "prompt", "system", "constraint", "baseline", "scope",
)

MD_ESCAPE = "\\`*_{}[]()#+-|<>~!"

DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

DISCLAIMER = (
    "本输出是变更请求影响的信息核对材料，不是合同效力、法律责任或定价结论；"
    "是否构成合同范围内的变更、如何计费与是否批准，由双方授权人员按合同约定决定。"
)

HUMAN_CONFIRM_BASE = (
    "变更是否构成合同约定的范围变更须由双方授权人员判断（本工具不解释合同效力）",
    "是否批准、由谁批准、何时生效须由人工决定（客户提出请求不等于批准）",
    "是否计费、计费金额与开票方式须由人工决定（本工具不报价、不收费）",
    "是否调整排期、发送确认或签署文件须由人工执行（本工具不自动发送、签署或排期）",
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    """Collapse control characters and whitespace; never raises."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = "".join(
        ch for ch in text
        if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C"
    )
    return re.sub(r"\s+", " ", text).strip()


def esc(value):
    """Escape Markdown structure characters so untrusted text cannot forge layout."""
    text = clean_text(value)
    return "".join("\\" + ch if ch in MD_ESCAPE else ch for ch in text)


def has_text(value):
    return isinstance(value, str) and value.strip() != ""


def parse_dt(value):
    """Parse an ISO timestamp that MUST carry an explicit UTC offset."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not DT_RE.match(text):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_bool(value):
    """Tri-state boolean: True / False / None (unknown)."""
    if isinstance(value, bool):
        return value
    return None


def dec(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal, str)):
        text = str(value).strip()
        if text == "":
            return None
        try:
            parsed = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
        if not parsed.is_finite():
            return None
        return parsed
    return None


def quant(value, places=2):
    """Decimal quantize that never renders a negative zero."""
    exponent = Decimal(1).scaleb(-places)
    out = Decimal(value).quantize(exponent, rounding=ROUND_HALF_UP)
    return abs(out) if out == 0 else out


def money(value):
    return None if value is None else str(quant(value))


def injection_hit(value):
    if not isinstance(value, str) or not value:
        return False
    for sentence in re.split(r"[。！？!?;\n]", value):
        probe = sentence.lower()
        if any(a in probe for a in INJ_ACTION) and any(t in probe for t in INJ_TARGET):
            return True
    return False


def hidden(value):
    if injection_hit(value):
        return PLACEHOLDER
    return esc(value)


def find_credentials(node, path="", hits=None):
    if hits is None:
        hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + "/" + str(key) if path else str(key)
            if CRED_KEY.search(str(key)):
                hits.append({"path": child, "reason": "CREDENTIAL_FIELD_NAME"})
                continue
            find_credentials(value, child, hits)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = (path + "[%d]") % index if path else "[%d]" % index
            find_credentials(value, child, hits)
    elif isinstance(node, str):
        for pattern in CRED_VALUE:
            if pattern.search(node):
                hits.append({"path": path or "/", "reason": "CREDENTIAL_VALUE_SHAPE"})
                break
    return hits


def find_injections(node, path="", hits=None):
    if hits is None:
        hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + "/" + str(key) if path else str(key)
            find_injections(value, child, hits)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = (path + "[%d]") % index if path else "[%d]" % index
            find_injections(value, child, hits)
    elif isinstance(node, str) and injection_hit(node):
        hits.append({"path": path or "/", "marker": "PROMPT_INJECTION"})
    return hits


def safe_basename(raw):
    if not has_text(raw):
        return False, None, "EMPTY"
    text = clean_text(raw)
    if URL_RE.match(text):
        return False, None, "URL_REFERENCE"
    if "\\" in text or "/" in text:
        tail = re.split(r"[\\/]+", text)[-1]
        if tail in ("", ".", ".."):
            return False, None, "PATH_REFERENCE"
        return False, clean_text(tail), "PATH_REFERENCE"
    if text in (".", "..") or text.startswith("~"):
        return False, None, "PATH_REFERENCE"
    if re.match(r"^[A-Za-z]:", text):
        return False, clean_text(re.split(r"[:/\\]+", text)[-1]), "PATH_REFERENCE"
    if not BASENAME_RE.match(text):
        return False, None, "ILLEGAL_CHARACTERS"
    return True, text, None


# --------------------------------------------------------------------------
# envelope
# --------------------------------------------------------------------------
def reject(hits):
    return {
        "version": VERSION,
        "status": "REJECTED",
        "reason": "CREDENTIAL_DETECTED",
        "credential_findings": [{"path": h["path"], "reason": h["reason"]} for h in hits],
        "as_of": None,
        "project": {},
        "request_count": 0,
        "classification_counts": {name: 0 for name in CLASSIFICATIONS},
        "requests": [],
        "amounts_by_currency": {},
        "revision_allowance": {"included": 0, "used": 0, "remaining": 0},
        "pre_approval_blockers": [],
        "clarification_questions": [],
        "injection_flagged": [],
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "communication_draft": "",
        "markdown_summary": (
            "# 客户变更请求影响准备包\n\n"
            "- 状态：**REJECTED**\n"
            "- 原因：输入疑似包含凭据（密钥 / 令牌 / 密码）。\n"
            "- 处理：已拒绝处理，**未回显**任何疑似凭据内容。\n\n"
            "> 请移除凭据字段后重新提交。本工具不需要也不需要保存任何登录凭据。\n"
        ),
        "disclaimer": DISCLAIMER,
    }


def envelope(status, as_of_raw, injections, warnings):
    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "project": {},
        "request_count": 0,
        "classification_counts": {name: 0 for name in CLASSIFICATIONS},
        "requests": [],
        "amounts_by_currency": {},
        "revision_allowance": {"included": 0, "used": 0, "remaining": 0},
        "pre_approval_blockers": [],
        "clarification_questions": [],
        "injection_flagged": list(injections),
        "input_warnings": list(warnings),
        "communication_draft": "",
        "markdown_summary": (
            "# 客户变更请求影响准备包\n\n"
            "- 状态：**%s**\n"
            "- 原因：%s\n\n"
            "> 请补齐必填输入后重新提交。\n" % (status, "; ".join(warnings) or "输入不完整")
        ),
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# project baseline
# --------------------------------------------------------------------------
def normalise_project(raw):
    if not isinstance(raw, dict):
        raw = {}
    baseline = []
    raw_baseline = raw.get("scope_baseline")
    if isinstance(raw_baseline, list):
        for item in raw_baseline:
            if not isinstance(item, dict):
                continue
            deliverable_id = clean_text(item.get("deliverable_id")) or None
            baseline.append({
                "deliverable_id": deliverable_id,
                "name": clean_text(item.get("name")),
                "revision_included": read_bool(item.get("revision_included")),
            })
    allowance = raw.get("revision_allowance") if isinstance(raw.get("revision_allowance"), dict) else {}
    included = dec(allowance.get("included"))
    used = dec(allowance.get("used"))
    included_i = int(included) if included is not None else None
    used_i = int(used) if used is not None else None
    remaining = None
    if included_i is not None and used_i is not None:
        remaining = included_i - used_i
        if remaining < 0:
            remaining = 0
    return {
        "project_id": clean_text(raw.get("project_id")) or None,
        "name": clean_text(raw.get("name")),
        "baseline_version": clean_text(raw.get("baseline_version")) or None,
        "scope_baseline": baseline,
        "baseline_ids": [d["deliverable_id"] for d in baseline if d["deliverable_id"]],
        "revision_allowance": {"included": included_i, "used": used_i, "remaining": remaining},
    }


def has_baseline(project):
    return bool(project["scope_baseline"]) or project["baseline_version"] is not None


# --------------------------------------------------------------------------
# change requests
# --------------------------------------------------------------------------
def classify(request, project):
    """Fixed classification; documented in references/guide.md section 4."""
    new = request["new_deliverables"]
    removed = request["removes_deliverables"]
    refs = request["baseline_refs"]

    if not has_baseline(project):
        return "CLARIFICATION_NEEDED", "BASELINE_MISSING"
    if new and removed and len(new) == len(removed):
        return "SUBSTITUTION", None
    if new:
        remaining = project["revision_allowance"]["remaining"]
        if request["within_revision_allowance"] is True and remaining is not None and remaining > 0:
            return "INCLUDED_REVISION", None
        if request["within_revision_allowance"] is None:
            return "INSUFFICIENT_EVIDENCE", "REVISION_ALLOWANCE_UNKNOWN"
        return "CHANGE_REQUEST", None
    if removed:
        return "CHANGE_REQUEST", None
    if refs:
        known = project["baseline_ids"]
        if all(ref in known for ref in refs):
            return "IN_SCOPE", None
        return "CLARIFICATION_NEEDED", "BASELINE_REF_UNKNOWN"
    if not has_text(request["raw_text"]) and not request["target_outcome"]:
        return "INSUFFICIENT_EVIDENCE", "REQUEST_NOT_DESCRIBED"
    return "CLARIFICATION_NEEDED", "NO_BASELINE_REF"


def normalise_request(raw, index, project):
    path = "change_requests[%d]" % index
    if not isinstance(raw, dict):
        request = {
            "request_id": None, "index": index, "path": path, "invalid": True,
            "raw_text": "", "requested_by": None, "target_outcome": "",
            "baseline_refs": [], "new_deliverables": [], "removes_deliverables": [],
            "affected_assets": [], "dependencies": [],
            "effort_hours": None, "schedule_days": None,
            "cost_amount": None, "cost_currency": None,
            "within_revision_allowance": None, "decision_maker": None,
            "approval_status": "unknown", "approved_by": None, "approved_at": None,
            "refused_refs": [],
        }
        request["classification"], request["class_reason"] = "INSUFFICIENT_EVIDENCE", "INVALID_REQUEST_RECORD"
        return request

    def str_list(value):
        out = []
        if isinstance(value, list):
            for item in value:
                name = clean_text(item)
                if name and name not in out:
                    out.append(name)
        return out

    impact = raw.get("impact") if isinstance(raw.get("impact"), dict) else {}
    cost = impact.get("cost") if isinstance(impact.get("cost"), dict) else {}
    cost_amount = dec(cost.get("amount"))
    cost_currency = clean_text(cost.get("currency")).upper() or None

    approval = raw.get("approval") if isinstance(raw.get("approval"), dict) else {}
    status = clean_text(approval.get("status")).lower()
    if status not in APPROVAL_STATES:
        status = "unknown"

    refused_refs = []
    assets = []
    raw_assets = raw.get("affected_assets")
    if isinstance(raw_assets, list):
        for offset, item in enumerate(raw_assets):
            if isinstance(item, dict) and "basename" in item:
                ok, base, reason = safe_basename(item.get("basename"))
                if not ok:
                    refused_refs.append({
                        "path": "%s/affected_assets[%d]/basename" % (path, offset),
                        "reason": reason,
                    })
                if base and base not in assets:
                    assets.append(base)
            else:
                name = clean_text(item)
                if name and name not in assets:
                    assets.append(name)

    request = {
        "request_id": clean_text(raw.get("request_id")) or None,
        "index": index,
        "path": path,
        "invalid": False,
        "raw_text": clean_text(raw.get("raw_text")),
        "requested_by": clean_text(raw.get("requested_by")) or None,
        "target_outcome": clean_text(raw.get("target_outcome")),
        "baseline_refs": str_list(raw.get("baseline_refs")),
        "new_deliverables": str_list(raw.get("new_deliverables")),
        "removes_deliverables": str_list(raw.get("removes_deliverables")),
        "affected_assets": assets,
        "dependencies": str_list(impact.get("dependencies")),
        "effort_hours": dec(impact.get("effort_hours")),
        "schedule_days": dec(impact.get("schedule_days")),
        "cost_amount": cost_amount,
        "cost_currency": cost_currency,
        "within_revision_allowance": read_bool(raw.get("within_revision_allowance")),
        "decision_maker": clean_text(raw.get("decision_maker")) or None,
        "approval_status": status,
        "approved_by": clean_text(approval.get("decided_by")) or None,
        "approved_at": clean_text(approval.get("decided_at")) or None,
        "refused_refs": refused_refs,
    }
    request["classification"], request["class_reason"] = classify(request, project)
    return request


def request_impact(request):
    cost_by_currency = {}
    if request["cost_amount"] is not None and request["cost_currency"]:
        cost_by_currency[request["cost_currency"]] = money(request["cost_amount"])
    return {
        "effort_hours": money(request["effort_hours"]),
        "schedule_days": money(request["schedule_days"]),
        "cost_by_currency": cost_by_currency,
        "dependencies": request["dependencies"],
    }


def request_unknowns(request):
    unknowns = []
    if request["effort_hours"] is None:
        unknowns.append("EFFORT_UNKNOWN")
    if request["schedule_days"] is None:
        unknowns.append("SCHEDULE_UNKNOWN")
    if request["cost_amount"] is None:
        unknowns.append("COST_NOT_PROVIDED")
    elif not request["cost_currency"]:
        unknowns.append("COST_CURRENCY_UNKNOWN")
    if request["classification"] in ("CHANGE_REQUEST", "INCLUDED_REVISION", "SUBSTITUTION") \
            and request["effort_hours"] is None:
        unknowns.append("CHANGE_WITHOUT_EFFORT")
    return unknowns


def request_blockers(request):
    blockers = []
    classification = request["classification"]
    if classification == "CLARIFICATION_NEEDED":
        blockers.append("NEEDS_CLARIFICATION")
    elif classification == "INSUFFICIENT_EVIDENCE":
        blockers.append("INSUFFICIENT_EVIDENCE")
    if request["approval_status"] == "pending":
        blockers.append("PENDING_APPROVAL")
    elif request["approval_status"] == "unknown":
        blockers.append("MISSING_APPROVAL")
    return blockers


def ready_to_schedule(request):
    return (request["classification"] in DECIDED_CLASSES
            and request["approval_status"] == "approved")


# --------------------------------------------------------------------------
# questions and draft
# --------------------------------------------------------------------------
def build_questions(requests, project):
    questions = []
    for request in requests:
        label = request["request_id"] or request["path"]
        classification = request["classification"]
        reason = request.get("class_reason")
        if classification == "CLARIFICATION_NEEDED":
            if reason == "BASELINE_MISSING":
                questions.append((label, "BASELINE_MISSING",
                                  "缺少范围基线（scope_baseline / baseline_version），无法判断是否超范围。"))
            elif reason == "BASELINE_REF_UNKNOWN":
                questions.append((label, "BASELINE_REF_UNKNOWN",
                                  "请求引用的基线交付物不在已批准基线中，请确认基准版本。"))
            else:
                questions.append((label, "NO_BASELINE_REF",
                                  "请求未指向任何基线交付物，也未说明新增交付物，请明确对应范围。"))
        elif classification == "INSUFFICIENT_EVIDENCE":
            if reason == "REVISION_ALLOWANCE_UNKNOWN":
                questions.append((label, "REVISION_ALLOWANCE_UNKNOWN",
                                  "无法判断该请求是否落在已含修订次数内，请确认修订额度。"))
            else:
                questions.append((label, "REQUEST_NOT_DESCRIBED",
                                  "请求内容为空，无法分类，请补齐原始请求与目标结果。"))
        if request["approval_status"] != "approved":
            questions.append((label, "APPROVAL_%s" % request["approval_status"].upper(),
                              "尚未记录明确批准（客户提出请求不等于批准），请确认决策人与批准状态。"))
        if request["cost_amount"] is None:
            questions.append((label, "COST_NOT_PROVIDED",
                              "没有显式费用事实，本工具不估价；如需计费请提供金额与币种。"))
        elif not request["cost_currency"]:
            questions.append((label, "COST_CURRENCY_UNKNOWN",
                              "提供了金额但没有币种，无法归入币种分组，请补充币种。"))
        if request["effort_hours"] is None:
            questions.append((label, "EFFORT_UNKNOWN",
                              "没有显式工时影响，取值保持未知，请补充或确认。"))
    out = []
    for index, (label, topic, text) in enumerate(questions, start=1):
        out.append({"id": "Q-%02d" % index, "request_id": label,
                    "topic": topic, "question": text})
    return out


def render_draft(project, requests):
    lines = []
    title = project["name"] or project["project_id"] or "本项目"
    lines.append("## 客户沟通草稿（未发送）")
    lines.append("")
    lines.append("> 本段只是**草稿**，工具不会发送、签署、收费或排期；请人工确认后再决定是否对外发出。")
    lines.append("")
    lines.append("关于 %s 的变更请求，当前处理如下：" % esc(title))
    lines.append("")
    for request in requests:
        label = request["request_id"] or request["path"]
        impact = request_impact(request)
        costs = impact["cost_by_currency"]
        cost_text = "、".join("%s %s" % (esc(k), esc(v)) for k, v in sorted(costs.items())) or "未提供"
        lines.append("- **%s**：分类为 %s；工时影响 %s，排期影响 %s 天，费用影响 %s。" % (
            esc(label),
            esc(request["classification"]),
            esc(impact["effort_hours"]) if impact["effort_hours"] is not None else "未知",
            esc(impact["schedule_days"]) if impact["schedule_days"] is not None else "未知",
            cost_text,
        ))
        if request["approval_status"] != "approved":
            lines.append("  - 待办：%s 尚未取得明确批准，需由 %s 确认。" % (
                esc(label), esc(request["decision_maker"]) if request["decision_maker"] else "指定决策人"))
    lines.append("")
    lines.append("以上为待确认清单，**不代表我方已接受范围变更，也不构成报价**。")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def render_markdown(status, as_of, project, counts, requests, amounts, blockers,
                    questions, warnings):
    lines = []
    title = project.get("name") or project.get("project_id") or "未命名项目"
    lines.append("# 客户变更请求影响准备包 — %s" % esc(title))
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 范围基线版本：%s" % (esc(project.get("baseline_version"))
                                      if project.get("baseline_version") else "未提供"))
    allowance = project.get("revision_allowance") or {}
    lines.append("- 已含修订：%s / 已用 %s / 剩余 %s" % (
        allowance.get("included") if allowance.get("included") is not None else "未知",
        allowance.get("used") if allowance.get("used") is not None else "未知",
        allowance.get("remaining") if allowance.get("remaining") is not None else "未知",
    ))
    lines.append("- 请求数：%d" % len(requests))
    lines.append("")

    lines.append("## 请求分类汇总")
    lines.append("")
    lines.append("| 分类 | 数量 |")
    lines.append("|---|---:|")
    for name in CLASSIFICATIONS:
        lines.append("| %s | %d |" % (name, counts[name]))
    lines.append("")

    lines.append("## 请求明细")
    lines.append("")
    lines.append("| 请求 | 分类 | 请求人 | 决策人 | 批准状态 | 可排期 |")
    lines.append("|---|---|---|---|---|---|")
    for request in requests:
        label = request["request_id"] or request["path"]
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            esc(label),
            request["classification"],
            esc(request["requested_by"]) if request["requested_by"] else "未提供",
            esc(request["decision_maker"]) if request["decision_maker"] else "未提供",
            esc(request["approval_status"]),
            "是" if ready_to_schedule(request) else "否",
        ))
    lines.append("")

    lines.append("## 基线对照")
    lines.append("")
    if requests:
        lines.append("| 请求 | 命中基线交付物 | 新增交付物 | 移除交付物 |")
        lines.append("|---|---|---|---|")
        for request in requests:
            lines.append("| %s | %s | %s | %s |" % (
                esc(request["request_id"] or request["path"]),
                esc("、".join(request["baseline_refs"])) if request["baseline_refs"] else "无",
                esc("、".join(request["new_deliverables"])) if request["new_deliverables"] else "无",
                esc("、".join(request["removes_deliverables"])) if request["removes_deliverables"] else "无",
            ))
    else:
        lines.append("- 无请求记录")
    lines.append("")

    lines.append("## 影响维度")
    lines.append("")
    lines.append("| 请求 | 工时影响 | 排期影响(天) | 费用影响（按币种） | 依赖 |")
    lines.append("|---|---|---|---|---|")
    for request in requests:
        impact = request_impact(request)
        costs = impact["cost_by_currency"]
        cost_text = "、".join("%s %s" % (esc(k), esc(v)) for k, v in sorted(costs.items())) or "未提供"
        lines.append("| %s | %s | %s | %s | %s |" % (
            esc(request["request_id"] or request["path"]),
            esc(impact["effort_hours"]) if impact["effort_hours"] is not None else "未知",
            esc(impact["schedule_days"]) if impact["schedule_days"] is not None else "未知",
            cost_text,
            esc("、".join(impact["dependencies"])) if impact["dependencies"] else "无",
        ))
    lines.append("")

    lines.append("## 金额按币种分组")
    lines.append("")
    if amounts:
        lines.append("| 币种 | 合计（显式费用，未跨币种汇总） |")
        lines.append("|---|---:|")
        for currency in sorted(amounts):
            lines.append("| %s | %s |" % (esc(currency), esc(amounts[currency])))
    else:
        lines.append("- 无显式费用事实（本工具不估价）")
    lines.append("")

    lines.append("## 受影响资产与原始请求摘要")
    lines.append("")
    lines.append("| 请求 | 受影响资产 | 原始请求摘要（不可信文本已转义） |")
    lines.append("|---|---|---|")
    for request in requests:
        lines.append("| %s | %s | %s |" % (
            esc(request["request_id"] or request["path"]),
            esc("、".join(request["affected_assets"])) if request["affected_assets"] else "无",
            hidden(request["raw_text"]) if request["raw_text"] else "无",
        ))
    lines.append("")

    refused_rows = []
    for request in requests:
        for refused in request["refused_refs"]:
            refused_rows.append((request["request_id"] or request["path"],
                                 refused["path"], refused["reason"]))
    if refused_rows:
        lines.append("## 被拒绝的引用")
        lines.append("")
        for label, refused_path, reason in refused_rows:
            lines.append("- %s：%s（%s）—— 已拒绝且未回显原引用" % (
                esc(label), esc(refused_path), esc(reason)))
        lines.append("")

    lines.append("## 批准前阻塞项")
    lines.append("")
    if blockers:
        lines.append("| 请求 | 阻塞项 |")
        lines.append("|---|---|")
        for item in blockers:
            lines.append("| %s | %s |" % (esc(item["request_id"]), esc(item["blocker"])))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 待确认问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- `%s` [%s] %s" % (
                question["id"], esc(question["request_id"]), esc(question["question"])))
    else:
        lines.append("- 无")
    lines.append("")

    if warnings:
        lines.append("## 输入提示")
        lines.append("")
        for warning in warnings:
            lines.append("- %s" % esc(warning))
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> %s" % esc(DISCLAIMER))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------
def analyse(data):
    if not isinstance(data, dict):
        data = {}
    creds = find_credentials(data)
    if creds:
        return reject(creds)
    injections = find_injections(data)

    as_of_raw = data.get("as_of")
    as_of_dt = parse_dt(as_of_raw)
    raw_requests = data.get("change_requests")
    if not isinstance(raw_requests, list):
        raw_requests = []

    warnings = []
    if as_of_dt is None:
        warnings.append("AS_OF_MISSING_OR_INVALID")
    if not raw_requests:
        warnings.append("NO_CHANGE_REQUESTS")
    if warnings:
        return envelope("INPUT_INCOMPLETE", as_of_raw, injections, warnings)

    project = normalise_project(data.get("project"))
    requests = [normalise_request(raw, index, project) for index, raw in enumerate(raw_requests)]
    requests.sort(key=lambda r: (r["request_id"] or "", r["path"]))

    counts = {name: 0 for name in CLASSIFICATIONS}
    for request in requests:
        counts[request["classification"]] += 1

    amounts = {}
    for request in requests:
        if request["cost_amount"] is not None and request["cost_currency"]:
            current = amounts.get(request["cost_currency"], Decimal(0))
            amounts[request["cost_currency"]] = current + request["cost_amount"]
    amounts = {currency: money(total) for currency, total in amounts.items()}

    blockers = []
    for request in requests:
        for blocker in request_blockers(request):
            blockers.append({"request_id": request["request_id"] or request["path"],
                             "blocker": blocker})

    ready_count = sum(1 for request in requests if ready_to_schedule(request))
    if any(not ready_to_schedule(request) for request in requests):
        status = "BLOCKED"
    elif any(request_unknowns(request) for request in requests):
        status = "GAPS_FOUND"
    else:
        status = "READY"

    questions = build_questions(requests, project)
    draft = render_draft(project, requests)

    request_output = []
    for request in requests:
        request_output.append({
            "request_id": request["request_id"], "path": request["path"],
            "invalid": request["invalid"],
            "classification": request["classification"],
            "class_reason": request.get("class_reason"),
            "requested_by": request["requested_by"],
            "target_outcome": request["target_outcome"],
            "raw_text_present": has_text(request["raw_text"]),
            "baseline_refs": request["baseline_refs"],
            "new_deliverables": request["new_deliverables"],
            "removes_deliverables": request["removes_deliverables"],
            "affected_assets": request["affected_assets"],
            "impact": request_impact(request),
            "unknowns": request_unknowns(request),
            "approval": {
                "status": request["approval_status"],
                "decision_maker": request["decision_maker"],
                "decided_by": request["approved_by"],
                "decided_at": request["approved_at"],
            },
            "ready_to_schedule": ready_to_schedule(request),
            "blockers": request_blockers(request),
            "refused_refs": request["refused_refs"],
        })

    refused_all = []
    for request in requests:
        for refused in request["refused_refs"]:
            refused_all.append({"request_id": request["request_id"],
                                "path": refused["path"], "reason": refused["reason"]})

    markdown = render_markdown(status, as_of_raw, project, counts, requests, amounts,
                               blockers, questions, warnings)

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "as_of_date": (as_of_dt.date().isoformat() if as_of_dt else None),
        "project": {
            "project_id": project["project_id"],
            "name": project["name"],
            "baseline_version": project["baseline_version"],
            "scope_baseline": project["scope_baseline"],
            "baseline_ids": project["baseline_ids"],
        },
        "revision_allowance": project["revision_allowance"],
        "request_count": len(requests),
        "classification_counts": counts,
        "requests": request_output,
        "ready_to_schedule_count": ready_count,
        "amounts_by_currency": amounts,
        "pre_approval_blockers": blockers,
        "refused_refs": refused_all,
        "clarification_questions": questions,
        "communication_draft": draft,
        "injection_flagged": list(injections),
        "input_warnings": warnings,
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
    }


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: run.py <input.json>\n")
        return 2
    try:
        with open(argv[1], "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        sys.stderr.write("cannot read input: %s\n" % error)
        return 2
    sys.stdout.write(json.dumps(analyse(data), ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
