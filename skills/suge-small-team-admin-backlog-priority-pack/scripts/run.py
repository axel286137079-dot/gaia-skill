#!/usr/bin/env python3
"""小团队行政积压优先级准备包 — offline admin backlog priority builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes and no ticketing/mail/assignment action: attachment-style references
are handled as file *basenames*.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = "1.0.0"

# --------------------------------------------------------------------------
# fixed vocabulary (documented in references/guide.md)
# --------------------------------------------------------------------------
# `in_progress` is normalised to `open`: both mean "still on our plate".
STATUS_ALIASES = {
    "open": "open",
    "in_progress": "open",
    "waiting": "waiting",
    "done": "done",
}
STATUS_VALUES = ("open", "waiting", "done")

ITEM_STATES = ("DO_TODAY", "PLAN_THIS_WEEK", "WAITING", "DONE",
               "INSUFFICIENT_EVIDENCE")

DUE_SOON = timedelta(days=3)
COMMITMENT_IMMINENT = timedelta(hours=24)

# Fixed board ordering, most urgent first.
DUE_RANK = {"OVERDUE": 0, "DUE_TODAY": 1, "DUE_SOON": 2, "SCHEDULED": 3,
            "UNKNOWN": 4}

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

# Injection is flagged only when an action verb and an instruction word appear in
# the SAME sentence, so ordinary backlog notes are not mislabelled.
INJ_ACTION = (
    "忽略", "无视", "跳过", "覆盖", "改写", "删除", "执行", "服从", "绕过",
    "ignore", "disregard", "override", "bypass", "forget",
)
INJ_TARGET = (
    "指令", "规则", "提示", "系统", "要求", "约束",
    "instruction", "rule", "prompt", "system", "constraint",
)

MD_ESCAPE = "\\`*_{}[]()#+-|<>~!"

DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

DISCLAIMER = (
    "本输出是行政积压的人工优先级准备材料，不是绩效评价、劳动关系结论或财务审批；"
    "优先级只由输入中明示的事实推导，是否分派、对外承诺与付款由经营者人工决定。"
)

HUMAN_CONFIRM_BASE = (
    "优先级只由输入中的明示事实推导，工具不替代管理判断",
    "任务分派与对外承诺须由人工决定（本工具不自动分派、不自动发送）",
    "涉及合同、付款与劳动关系的决定须由人工确认（本工具不判断法律效力）",
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


def read_decimal(value):
    """Optional non-negative decimal; None means "not provided", never 0."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str)):
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if parsed.is_nan() or parsed.is_infinite() or parsed < 0:
            return None
        return parsed
    return None


def money(value):
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def injection_hit(value):
    if not isinstance(value, str) or not value:
        return False
    for sentence in re.split(r"[。！？!?;\n]", value):
        probe = sentence.lower()
        if any(a in probe for a in INJ_ACTION) and any(t in probe for t in INJ_TARGET):
            return True
    return False


def hidden(value):
    """Render a free-text value for Markdown; injection hits become a placeholder."""
    if injection_hit(value):
        return PLACEHOLDER
    return esc(value)


def safe_basename(raw):
    """Return (ok, safe_name, reason). The raw reference is never returned."""
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


def find_credentials(node, path="", hits=None):
    """Collect credential-shaped key names and values, with a readable path."""
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
    """Collect free-text leaves that look like a prompt injection."""
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


def tri(value):
    return "是" if value is True else ("否" if value is False else "未知")


# --------------------------------------------------------------------------
# empty / rejected envelopes
# --------------------------------------------------------------------------
def reject(hits):
    return {
        "version": VERSION,
        "status": "REJECTED",
        "reason": "CREDENTIAL_DETECTED",
        "credential_findings": [{"path": h["path"], "reason": h["reason"]} for h in hits],
        "as_of": None,
        "item_count": 0,
        "status_counts": {name: 0 for name in ITEM_STATES},
        "items": [],
        "today_board": [],
        "week_board": [],
        "waiting_board": [],
        "overdue_items": [],
        "due_soon_items": [],
        "no_owner_items": [],
        "decision_gaps": [],
        "blocking_chains": [],
        "dangling_dependencies": [],
        "dependency_cycles": [],
        "today_effort": {"known_total_hours": None, "unknown_items": 0,
                         "complete": False},
        "human_confirm_items": [],
        "responsibility": [],
        "clarification_questions": [],
        "refused_refs": [],
        "injection_flagged": [],
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "markdown_summary": (
            "# 小团队行政积压优先级准备包\n\n"
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
        "item_count": 0,
        "status_counts": {name: 0 for name in ITEM_STATES},
        "items": [],
        "today_board": [],
        "week_board": [],
        "waiting_board": [],
        "overdue_items": [],
        "due_soon_items": [],
        "no_owner_items": [],
        "decision_gaps": [],
        "blocking_chains": [],
        "dangling_dependencies": [],
        "dependency_cycles": [],
        "today_effort": {"known_total_hours": None, "unknown_items": 0,
                         "complete": False},
        "human_confirm_items": [],
        "responsibility": [],
        "clarification_questions": [],
        "refused_refs": [],
        "injection_flagged": list(injections),
        "input_warnings": list(warnings),
        "markdown_summary": (
            "# 小团队行政积压优先级准备包\n\n"
            "- 状态：**%s**\n"
            "- 原因：%s\n\n"
            "> 请补齐必填输入后重新提交。\n" % (status, "; ".join(warnings) or "输入不完整")
        ),
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# dependency graph
# --------------------------------------------------------------------------
def build_graph(items):
    """Return (known_ids, cycles) over item_id -> depends_on."""
    known = {item["item_id"] for item in items if item.get("item_id")}

    edges = {item["item_id"]: [dep for dep in item["depends_on"] if dep in known]
             for item in items if item.get("item_id")}

    # Iterative three-colour cycle detection, reported as sorted cycles.
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {node: WHITE for node in edges}
    cycles = []

    def walk(start):
        stack = [(start, iter(edges.get(start, [])))]
        path = [start]
        colour[start] = GREY
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if colour.get(child, WHITE) == GREY:
                    index = path.index(child)
                    cycles.append(sorted(set(path[index:])))
                    continue
                if colour.get(child, WHITE) == WHITE:
                    colour[child] = GREY
                    path.append(child)
                    stack.append((child, iter(edges.get(child, []))))
                    advanced = True
                    break
            if not advanced:
                colour[node] = BLACK
                stack.pop()
                if path:
                    path.pop()

    for node in sorted(edges):
        if colour.get(node, WHITE) == WHITE:
            walk(node)

    unique = []
    for cycle in sorted(cycles, key=lambda c: (len(c), c)):
        if cycle not in unique:
            unique.append(cycle)
    return known, unique


def unresolved_deps(item_id, done_ids, edges):
    """Direct dependencies that are not DONE yet (done is the only resolution)."""
    return sorted(dep for dep in edges.get(item_id, []) if dep not in done_ids)


def build_blocking_chains(waiting_ids, edges, done_ids):
    chains = []
    for item_id in waiting_ids:
        seen = set()
        frontier = [item_id]
        unresolved = set()
        while frontier:
            current = frontier.pop()
            for dep in edges.get(current, []):
                if dep in seen:
                    continue
                seen.add(dep)
                if dep not in done_ids:
                    unresolved.add(dep)
                    frontier.append(dep)
        roots = sorted(dep for dep in unresolved
                       if not unresolved_deps(dep, done_ids, edges))
        chains.append({"item_id": item_id, "unresolved": sorted(unresolved),
                       "root_blockers": roots})
    chains.sort(key=lambda c: c["item_id"])
    return chains


# --------------------------------------------------------------------------
# record normalisation
# --------------------------------------------------------------------------
def normalise_item(raw, index):
    path = "items[%d]" % index
    if not isinstance(raw, dict):
        return {
            "item_id": None, "index": index, "path": path, "invalid": True,
            "category": "", "status": None, "status_state": "UNKNOWN_ITEM_STATUS",
            "due_at": None, "due_state": "NOT_PROVIDED",
            "impacts": {"customer": None, "cash": None, "operations": None},
            "owner": None, "depends_on": [], "decision_needed": "",
            "decision_owner": None, "effort_hours": None, "effort_provided": False,
            "commitment": None, "evidence_ref": None, "evidence_refused": False,
            "notes": "",
            "state": "INSUFFICIENT_EVIDENCE",
            "unknowns": ["INVALID_ITEM_RECORD"],
            "review_flags": ["INVALID_ITEM_RECORD"],
            "reasons": [], "refused_refs": [],
        }

    item_id = clean_text(raw.get("item_id")) or None

    status_raw = clean_text(raw.get("status")).lower()
    status = STATUS_ALIASES.get(status_raw)
    status_state = status if status else "UNKNOWN_ITEM_STATUS"

    due_raw = raw.get("due_at")
    due_dt = parse_dt(due_raw)
    if not has_text(due_raw):
        due_state = "NOT_PROVIDED"
    elif due_dt is None:
        due_state = "INVALID"
    else:
        due_state = "PARSED"

    impacts_raw = raw.get("impacts") if isinstance(raw.get("impacts"), dict) else {}
    impacts = {
        "customer": read_bool(impacts_raw.get("customer")),
        "cash": read_bool(impacts_raw.get("cash")),
        "operations": read_bool(impacts_raw.get("operations")),
    }

    depends_raw = raw.get("depends_on")
    depends_on = []
    if isinstance(depends_raw, list):
        for entry in depends_raw:
            dep = clean_text(entry)
            if dep and dep not in depends_on:
                depends_on.append(dep)

    commitment_raw = raw.get("external_commitment")
    commitment = None
    if isinstance(commitment_raw, dict):
        commitment = {
            "made_to": clean_text(commitment_raw.get("made_to")),
            "due_at": clean_text(commitment_raw.get("due_at")) or None,
            "due_dt": parse_dt(commitment_raw.get("due_at")),
        }

    evidence_raw = raw.get("evidence_ref")
    if has_text(evidence_raw):
        ok, base, reason = safe_basename(evidence_raw)
    else:
        ok, base, reason = True, None, None

    effort = read_decimal(raw.get("effort_hours"))

    return {
        "item_id": item_id, "index": index, "path": path, "invalid": False,
        "category": clean_text(raw.get("category")),
        "status": status_raw or None,
        "status_state": status_state,
        "due_at": clean_text(due_raw) if has_text(due_raw) else None,
        "due_dt": due_dt,
        "due_state": due_state,
        "impacts": impacts,
        "owner": clean_text(raw.get("owner")) or None,
        "depends_on": depends_on,
        "decision_needed": clean_text(raw.get("decision_needed")),
        "decision_owner": clean_text(raw.get("decision_owner")).lower() or None,
        "effort_hours": effort,
        "effort_provided": effort is not None,
        "commitment": commitment,
        "evidence_ref": base,
        "evidence_refused": not ok,
        "evidence_reason": reason,
        "notes": clean_text(raw.get("notes")),
        "state": "INSUFFICIENT_EVIDENCE",
        "unknowns": [],
        "review_flags": [],
        "reasons": [],
        "refused_refs": ([{"path": path + "/evidence_ref", "reason": reason}]
                         if not ok else []),
    }


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------
def classify(items, edges, cycles, as_of_dt):
    """Fill in due/urgency facts, then assign every item a fixed state."""
    cycle_nodes = {node for cycle in cycles for node in cycle}

    for item in items:
        if item["invalid"]:
            continue
        due_dt = item.get("due_dt")
        if due_dt is None or as_of_dt is None:
            item["due_bucket"] = "UNKNOWN"
        elif due_dt < as_of_dt:
            item["due_bucket"] = "OVERDUE"
        elif due_dt.date() == as_of_dt.date():
            item["due_bucket"] = "DUE_TODAY"
        elif due_dt <= as_of_dt + DUE_SOON:
            item["due_bucket"] = "DUE_SOON"
        else:
            item["due_bucket"] = "SCHEDULED"

        item["days_to_due"] = None
        if due_dt is not None and as_of_dt is not None:
            item["days_to_due"] = str(
                (Decimal((due_dt - as_of_dt).total_seconds()) / Decimal(86400))
                .quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        commitment = item.get("commitment")
        item["commitment_imminent"] = bool(
            commitment and commitment.get("due_dt") is not None
            and as_of_dt is not None
            and commitment["due_dt"] <= as_of_dt + COMMITMENT_IMMINENT)

    done_ids = {item["item_id"] for item in items
                if item.get("item_id") and item["status_state"] == "done"}

    for item in items:
        if item["invalid"]:
            continue
        item_id = item.get("item_id")
        unknowns = item["unknowns"]
        reasons = item["reasons"]

        if item["status_state"] == "UNKNOWN_ITEM_STATUS":
            unknowns.append("UNKNOWN_ITEM_STATUS")
            item["state"] = "INSUFFICIENT_EVIDENCE"
            continue

        if item["status_state"] == "done":
            item["state"] = "DONE"
            continue

        if item_id and item_id in cycle_nodes:
            unknowns.append("DEPENDENCY_CYCLE")
            item["state"] = "INSUFFICIENT_EVIDENCE"
            continue

        pending = unresolved_deps(item_id, done_ids, edges)
        dangling = sorted(dep for dep in item["depends_on"] if dep not in edges)
        if dangling:
            for dep in dangling:
                unknowns.append("DANGLING_DEPENDENCY:%s" % dep)
            item["state"] = "INSUFFICIENT_EVIDENCE"
            continue
        if pending:
            item["pending_dependencies"] = pending
            reasons.append("被未完成的前置事项挡住：%s" % "、".join(pending))
            item["state"] = "WAITING"
            continue

        if item["status_state"] == "waiting":
            reasons.append("事项自身标记为等待中")
            item["state"] = "WAITING"
            continue

        if item["decision_needed"] and item["decision_owner"] == "external":
            reasons.append("等待外部决策：%s" % item["decision_needed"])
            item["state"] = "WAITING"
            continue

        if item["due_state"] == "NOT_PROVIDED":
            unknowns.append("NO_DUE_DATE")
        elif item["due_state"] == "INVALID":
            unknowns.append("INVALID_DUE_AT")
        if item["owner"] is None:
            unknowns.append("UNKNOWN_OWNER")
        if unknowns:
            # 无截止时间不得伪造紧急；无责任人不得自动分派。
            item["state"] = "INSUFFICIENT_EVIDENCE"
            continue

        escalate = item["due_bucket"] in ("OVERDUE", "DUE_TODAY")
        if item["due_bucket"] == "DUE_SOON" and (
                item["impacts"]["customer"] is True or item["impacts"]["cash"] is True):
            escalate = True
        if item["commitment_imminent"]:
            escalate = True

        if item["due_bucket"] == "OVERDUE":
            reasons.append("已逾期")
        elif item["due_bucket"] == "DUE_TODAY":
            reasons.append("今日到期")
        if item["commitment_imminent"]:
            reasons.append("对外承诺在 24 小时内到期")
        if item["due_bucket"] == "DUE_SOON" and (
                item["impacts"]["customer"] is True or item["impacts"]["cash"] is True):
            reasons.append("3 天内到期且存在明示的客户或现金影响")

        if item["decision_needed"]:
            item["review_flags"].append("DECISION_PENDING")

        item["state"] = "DO_TODAY" if escalate else "PLAN_THIS_WEEK"

    for item in items:
        item.pop("due_dt", None)
        if item.get("commitment"):
            item["commitment"].pop("due_dt", None)


def build_questions(items):
    questions = []
    for item in items:
        label = item.get("item_id") or item["path"]
        for unknown in item.get("unknowns", []):
            questions.append((label, unknown))
    out = []
    for index, (label, topic) in enumerate(questions, start=1):
        out.append({
            "id": "Q-%02d" % index,
            "item_id": label,
            "topic": topic,
            "question": _question_text(topic),
        })
    return out


def _question_text(topic):
    mapping = {
        "INVALID_ITEM_RECORD": "该事项记录不是对象，无法判断优先级，请按字段表重新提供。",
        "UNKNOWN_ITEM_STATUS": "事项状态缺失或取值非法（open / waiting / done），请补齐。",
        "NO_DUE_DATE": "没有截止时间，无法判断是否紧急；请给出截止时间（不猜紧急度）。",
        "INVALID_DUE_AT": "截止时间格式无效（需带时区偏移），请修正后重新提交。",
        "UNKNOWN_OWNER": "未指定责任人，无法排优先级；本工具不自动分派。",
        "DEPENDENCY_CYCLE": "前置依赖形成环，无法确定先后顺序，请人工拆解后再提交。",
    }
    if topic.startswith("DANGLING_DEPENDENCY"):
        return "前置依赖指向不存在的事项编号，请补充该事项或修正编号。"
    return mapping.get(topic, "存在待确认事实：" + topic)


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def render_markdown(status, as_of, team, counts, items, today_board, week_board,
                    waiting_board, overdue, due_soon, no_owner, decision_gaps,
                    chains, dangling, cycles, effort, human_confirm,
                    responsibility, questions, warnings):
    lines = []
    lines.append("# 小团队行政积压优先级准备包")
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    if team:
        lines.append("- 团队：%s" % (hidden(team.get("name")) or "未提供"))
        lines.append("- 团队时区：%s" % (esc(team.get("timezone")) or "未提供"))
    lines.append("- 事项数：%d" % len(items))
    lines.append("")
    lines.append("## 事项结论")
    lines.append("")
    lines.append("| 状态 | 数量 |")
    lines.append("|---|---:|")
    for name in ITEM_STATES:
        lines.append("| %s | %d |" % (name, counts[name]))
    lines.append("")
    lines.append("| 事项 | 类别 | 结论 | 状态值 | 截止 | 截止状态 | 责任人 |")
    lines.append("|---|---|---|---|---|---|---|")
    for item in items:
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            esc(item.get("item_id")) if item.get("item_id") else esc(item["path"]),
            esc(item.get("category")) or "未记录",
            item["state"],
            esc(item.get("status")) if item.get("status") else "未提供",
            esc(item.get("due_at")) if item.get("due_at") else "未提供",
            esc(item.get("due_bucket")) if item.get("due_bucket") else "未知",
            esc(item.get("owner")) if item.get("owner") else "未指派",
        ))
    lines.append("")

    lines.append("## 今日交付板")
    lines.append("")
    if today_board:
        lines.append("| 事项 | 类别 | 截止 | 责任人 | 原因 |")
        lines.append("|---|---|---|---|---|")
        for row in today_board:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(row["item_id"]), esc(row["category"]) or "未记录",
                esc(row["due_at"]) or "未提供",
                esc(row["owner"]) if row["owner"] else "未指派",
                hidden("；".join(row["reasons"])) or "明示事实触发"))
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("- 今日已知工作量合计：%s 小时" % (
        effort["known_total_hours"] if effort["known_total_hours"] is not None else "未提供"))
    lines.append("- 工作量缺失的事项：%d 条（**未按 0 计算**）" % effort["unknown_items"])
    lines.append("")

    lines.append("## 本周承诺板")
    lines.append("")
    if week_board:
        lines.append("| 事项 | 类别 | 截止 | 距截止（天） | 责任人 |")
        lines.append("|---|---|---|---:|---|")
        for row in week_board:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(row["item_id"]), esc(row["category"]) or "未记录",
                esc(row["due_at"]) or "未提供",
                esc(row["days_to_due"]) if row["days_to_due"] is not None else "未知",
                esc(row["owner"]) if row["owner"] else "未指派"))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 等待中")
    lines.append("")
    if waiting_board:
        lines.append("| 事项 | 等待原因 | 未完成前置 | 根阻塞 |")
        lines.append("|---|---|---|---|")
        chain_by_id = {chain["item_id"]: chain for chain in chains}
        for row in waiting_board:
            chain = chain_by_id.get(row["item_id"], {"unresolved": [], "root_blockers": []})
            lines.append("| %s | %s | %s | %s |" % (
                esc(row["item_id"]),
                hidden("；".join(row["reasons"])) or "未记录",
                esc("、".join(chain["unresolved"])) or "无",
                esc("、".join(chain["root_blockers"])) or "无"))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 已逾期 / 即将到期")
    lines.append("")
    if overdue or due_soon:
        lines.append("| 事项 | 截止 | 截止状态 | 责任人 |")
        lines.append("|---|---|---|---|")
        for row in overdue + due_soon:
            lines.append("| %s | %s | %s | %s |" % (
                esc(row["item_id"]), esc(row["due_at"]) or "未提供",
                esc(row["due_bucket"]),
                esc(row["owner"]) if row["owner"] else "未指派"))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 无责任人事项")
    lines.append("")
    if no_owner:
        for row in no_owner:
            lines.append("- `%s`（%s）" % (
                esc(row["item_id"]), esc(row["category"]) or "未记录"))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 决策缺口")
    lines.append("")
    if decision_gaps:
        lines.append("| 事项 | 待决策 | 决策方 | 责任人 |")
        lines.append("|---|---|---|---|")
        for row in decision_gaps:
            lines.append("| %s | %s | %s | %s |" % (
                esc(row["item_id"]), hidden(row["decision_needed"]),
                esc(row["decision_owner"]) if row["decision_owner"] else "未记录",
                esc(row["owner"]) if row["owner"] else "未指派"))
    else:
        lines.append("- 无")
    lines.append("")

    if dangling or cycles:
        lines.append("## 依赖异常")
        lines.append("")
        for dep in dangling:
            lines.append("- `%s` 引用了不存在的事项 `%s`" % (
                esc(dep["item_id"]), esc(dep["reference"])))
        for cycle in cycles:
            lines.append("- 依赖成环：%s" % esc(" → ".join(cycle)))
        lines.append("")

    lines.append("## 事项说明")
    lines.append("")
    if any(item.get("notes") for item in items):
        lines.append("| 事项 | 备注 |")
        lines.append("|---|---|")
        for item in items:
            if item.get("notes"):
                lines.append("| %s | %s |" % (
                    esc(item.get("item_id")) if item.get("item_id") else esc(item["path"]),
                    hidden(item.get("notes"))))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 人工确认项")
    lines.append("")
    for item in human_confirm:
        lines.append("- %s" % esc(item))
    lines.append("")

    lines.append("## 责任与截止")
    lines.append("")
    if responsibility:
        lines.append("| 事项 | 责任人 | 责任状态 | 截止状态 |")
        lines.append("|---|---|---|---|")
        for row in responsibility:
            lines.append("| %s | %s | %s | %s |" % (
                esc(row["item_id"]),
                esc(row["owner"]) if row["owner"] else "未指派",
                row["owner_state"], row["due_state"]))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 待确认问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- `%s` [%s] %s" % (
                question["id"], esc(question["item_id"]),
                esc(question["question"])))
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
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raw_items = []

    warnings = []
    if as_of_dt is None:
        warnings.append("AS_OF_MISSING_OR_INVALID")
    if not raw_items:
        warnings.append("NO_ITEMS")
    if warnings:
        return envelope("INPUT_INCOMPLETE", as_of_raw, injections, warnings)

    items = [normalise_item(raw, index) for index, raw in enumerate(raw_items)]
    known, cycles = build_graph(items)
    edges = {item["item_id"]: [dep for dep in item["depends_on"] if dep in known]
             for item in items if item.get("item_id")}

    classify(items, edges, cycles, as_of_dt)

    items.sort(key=lambda i: (i.get("item_id") or "", i["path"]))

    counts = {name: 0 for name in ITEM_STATES}
    for item in items:
        counts[item["state"]] += 1

    def sort_board(state):
        chosen = [item for item in items if item["state"] == state]
        chosen.sort(key=lambda i: (DUE_RANK.get(i.get("due_bucket"), 9),
                                   i.get("due_at") or "", i.get("item_id") or ""))
        return chosen

    def row(item):
        return {
            "item_id": item.get("item_id") or item["path"],
            "path": item["path"],
            "category": item.get("category"),
            "due_at": item.get("due_at"),
            "due_bucket": item.get("due_bucket"),
            "days_to_due": item.get("days_to_due"),
            "owner": item.get("owner"),
            "reasons": list(item.get("reasons", [])),
        }

    today_items = sort_board("DO_TODAY")
    week_items = sort_board("PLAN_THIS_WEEK")
    waiting_items = sort_board("WAITING")
    today_board = [row(item) for item in today_items]
    week_board = [row(item) for item in week_items]
    waiting_board = [row(item) for item in waiting_items]

    overdue = [row(item) for item in items if item.get("due_bucket") == "OVERDUE"
               and item["state"] != "DONE"]
    due_soon = [row(item) for item in items if item.get("due_bucket") == "DUE_SOON"
                and item["state"] != "DONE"]
    overdue.sort(key=lambda r: (r["due_at"] or "", r["item_id"]))
    due_soon.sort(key=lambda r: (r["due_at"] or "", r["item_id"]))

    no_owner = [{"item_id": item.get("item_id") or item["path"],
                 "path": item["path"], "category": item.get("category")}
                for item in items
                # A record that is not an object has no fields to assign at all,
                # so it is already covered by the invalid-record question.
                if item["state"] != "DONE" and not item["invalid"]
                and not item.get("owner")]
    no_owner.sort(key=lambda r: r["item_id"])

    decision_gaps = [{
        "item_id": item.get("item_id") or item["path"],
        "path": item["path"],
        "decision_needed": item["decision_needed"],
        "decision_owner": item.get("decision_owner"),
        "owner": item.get("owner"),
    } for item in items if item["decision_needed"]]
    decision_gaps.sort(key=lambda r: r["item_id"])

    waiting_ids = [item["item_id"] for item in waiting_items if item.get("item_id")]
    chains = build_blocking_chains(waiting_ids, edges, {
        item["item_id"] for item in items
        if item.get("item_id") and item["status_state"] == "done"})

    dangling = [{"item_id": item.get("item_id") or item["path"],
                 "path": item["path"], "reference": dep}
                for item in items for dep in item["depends_on"] if dep not in known]
    dangling.sort(key=lambda r: (r["item_id"], r["reference"]))

    known_effort = [item["effort_hours"] for item in today_items
                    if item.get("effort_provided")]
    unknown_effort = [item for item in today_items if not item.get("effort_provided")]
    total_effort = None
    if known_effort:
        total_effort = sum(known_effort, Decimal("0"))
    effort = {
        "known_total_hours": money(total_effort),
        "unknown_items": len(unknown_effort),
        # Never treat a missing estimate as zero: completeness is explicit.
        "complete": not unknown_effort and bool(known_effort),
    }

    # Decimals are internal only; the serialisable output carries exact strings.
    for item in items:
        item["effort_hours"] = money(item["effort_hours"])

    human_confirm = list(HUMAN_CONFIRM_BASE)
    if decision_gaps:
        human_confirm.append("决策缺口需要管理者拍板（工具不替你做决定）")
    if any(item.get("commitment") for item in items):
        human_confirm.append("对外承诺是否仍成立须由人工向对方确认（工具只读取登记值）")

    responsibility = [{
        "item_id": item.get("item_id") or item["path"],
        "owner": item.get("owner"),
        "owner_state": "ASSIGNED" if item.get("owner") else "UNASSIGNED",
        "due_state": item.get("due_bucket") or "UNKNOWN",
    } for item in items]

    refused_all = []
    for item in items:
        for refused in item.get("refused_refs", []):
            refused_all.append({"item_id": item.get("item_id"),
                                "path": refused["path"],
                                "reason": refused["reason"]})
    refused_all.sort(key=lambda r: (r["item_id"] or "", r["path"]))

    questions = build_questions(items)

    if counts["INSUFFICIENT_EVIDENCE"] or counts["WAITING"]:
        status = "GAPS_FOUND"
    else:
        status = "READY"

    team_raw = data.get("team") if isinstance(data.get("team"), dict) else None
    team = None
    if team_raw:
        team = {
            "name": clean_text(team_raw.get("name")),
            "timezone": clean_text(team_raw.get("timezone")) or None,
        }

    markdown = render_markdown(status, as_of_raw, team, counts, items, today_board,
                              week_board, waiting_board, overdue, due_soon, no_owner,
                              decision_gaps, chains, dangling, cycles, effort,
                              human_confirm, responsibility, questions, warnings)

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "as_of_date": (as_of_dt.date().isoformat() if as_of_dt else None),
        "team": team,
        "item_count": len(items),
        "status_counts": counts,
        "items": items,
        "today_board": today_board,
        "week_board": week_board,
        "waiting_board": waiting_board,
        "overdue_items": overdue,
        "due_soon_items": due_soon,
        "no_owner_items": no_owner,
        "decision_gaps": decision_gaps,
        "blocking_chains": chains,
        "dangling_dependencies": dangling,
        "dependency_cycles": cycles,
        "today_effort": effort,
        "refused_refs": refused_all,
        "human_confirm_items": human_confirm,
        "responsibility": responsibility,
        "clarification_questions": questions,
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
