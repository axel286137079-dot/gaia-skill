#!/usr/bin/env python3
"""上门服务出发前工单准备包 — offline field-service dispatch readiness builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes and no dispatch/booking/contact action: attachments are handled as
file *basenames*.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import datetime

VERSION = "1.0.0"

# --------------------------------------------------------------------------
# fixed vocabulary (documented in references/guide.md)
# --------------------------------------------------------------------------
VISIT_TYPES = ("diagnosis", "installation", "followup", "maintenance")
# Parts gate planned execution only. A diagnosis visit never waits on repair
# parts: you cannot know the part until someone has been on site.
PART_GATING_VISIT_TYPES = ("installation", "maintenance")
PART_CHAIN = ("ordered", "received", "inspected", "reserved")
PART_STATES = ("ordered", "received", "inspected", "reserved", "unknown")

WO_STATES = ("READY_FOR_DIAGNOSIS", "READY_FOR_PLANNED_WORK",
             "BLOCKED", "INSUFFICIENT_EVIDENCE")

SKILL_STATES = ("COVERED", "GAP", "UNKNOWN")

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
# the SAME sentence, so ordinary service notes are not mislabelled.
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

# A basename must be a single path segment: no separators, no drive letters.
BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

DISCLAIMER = (
    "本输出是派工出发前的信息核对材料，不是技术安全、许可、资质或合规结论；"
    "现场准入、作业安全与资质要求以责任单位和主管部门的规定为准。"
)

# Everything the tool must never decide for the user.
HUMAN_CONFIRM_BASE = (
    "现场安全与作业许可须由责任单位人工确认（本工具不判断技术安全或许可）",
    "作业资质与持证要求须由人工确认（本工具不判断资质）",
    "改期、派工与联系客户须由人工决定（本工具不自动执行）",
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
        "work_order_count": 0,
        "status_counts": {name: 0 for name in WO_STATES},
        "work_orders": [],
        "coverage_matrix": [],
        "parts_evidence": [],
        "access_scope_approval_gaps": [],
        "departure_checklist": [],
        "human_confirm_items": [],
        "double_bookings": [],
        "responsibility": [],
        "clarification_questions": [],
        "injection_flagged": [],
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "markdown_summary": (
            "# 上门服务出发前工单准备包\n\n"
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
        "work_order_count": 0,
        "status_counts": {name: 0 for name in WO_STATES},
        "work_orders": [],
        "coverage_matrix": [],
        "parts_evidence": [],
        "access_scope_approval_gaps": [],
        "departure_checklist": [],
        "human_confirm_items": [],
        "double_bookings": [],
        "responsibility": [],
        "clarification_questions": [],
        "injection_flagged": list(injections),
        "input_warnings": list(warnings),
        "markdown_summary": (
            "# 上门服务出发前工单准备包\n\n"
            "- 状态：**%s**\n"
            "- 原因：%s\n\n"
            "> 请补齐必填输入后重新提交。\n" % (status, "; ".join(warnings) or "输入不完整")
        ),
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# record normalisation
# --------------------------------------------------------------------------
def normalise_window(raw, index, label):
    if not isinstance(raw, dict):
        return None
    start = parse_dt(raw.get("start"))
    end = parse_dt(raw.get("end"))
    if start is None or end is None:
        return None
    return {"start": start, "end": end, "label": label}


def normalise_parts(raw_parts):
    parts = []
    if not isinstance(raw_parts, list):
        return parts
    for index, raw in enumerate(raw_parts):
        if not isinstance(raw, dict):
            continue
        state = clean_text(raw.get("state")).lower()
        if state not in PART_STATES:
            state = "unknown"
        required = read_bool(raw.get("required"))
        if required is None:
            required = True
        part_id = clean_text(raw.get("part_id")) or None
        # The chain is monotone: reserved implies inspected implies received
        # implies ordered. `ordered` alone therefore never satisfies the gate.
        step = PART_CHAIN.index(state) if state in PART_CHAIN else -1
        parts.append({
            "part_id": part_id,
            "path": "parts[%d]" % index,
            "description": clean_text(raw.get("description")),
            "state": state,
            "required": required,
            "evidence": {
                "ordered": step >= 0,
                "received": step >= 1,
                "inspected": step >= 2,
                "reserved": step >= 3,
            },
        })
    return parts


def normalise_tools(raw_tools):
    tools = []
    if not isinstance(raw_tools, list):
        return tools
    for index, raw in enumerate(raw_tools):
        if not isinstance(raw, dict):
            continue
        required = read_bool(raw.get("required"))
        if required is None:
            required = True
        available = read_bool(raw.get("available"))
        tools.append({
            "tool_id": clean_text(raw.get("tool_id")) or None,
            "path": "tools[%d]" % index,
            "name": clean_text(raw.get("name")),
            "required": required,
            "available": available,
        })
    return tools


def normalise_documents(raw_docs):
    docs = []
    refused = []
    if not isinstance(raw_docs, list):
        return docs, refused
    for index, raw in enumerate(raw_docs):
        if not isinstance(raw, dict):
            continue
        required = read_bool(raw.get("required"))
        if required is None:
            required = True
        available = read_bool(raw.get("available"))
        ok, base, reason = safe_basename(raw.get("basename"))
        if not ok:
            refused.append({"path": "documents[%d]/basename" % index, "reason": reason})
        docs.append({
            "doc_id": clean_text(raw.get("doc_id")) or None,
            "path": "documents[%d]" % index,
            # `base` is already sanitised; the raw reference is never echoed.
            "basename": base,
            "refused": not ok,
            "required": required,
            "available": available,
        })
    return docs, refused


def normalise_wo(raw, index, as_of_dt):
    path = "work_orders[%d]" % index
    if not isinstance(raw, dict):
        return {
            "work_order_id": None, "index": index, "path": path, "invalid": True,
            "visit_type": None, "visit_type_valid": False,
            "status": "INSUFFICIENT_EVIDENCE",
            "flags": ["INVALID_WORK_ORDER_RECORD"],
            "blockers": ["INVALID_WORK_ORDER_RECORD"], "unknowns": [], "review_flags": [],
            "appointment": None, "access": None, "scope": None, "skills": None,
            "availability": None, "equipment": [], "parts": [], "tools": [], "documents": [],
            "refused_refs": [], "parts_blocking": False, "owner": None, "due_at": None,
            "due_state": "NOT_PROVIDED",
        }

    wo_id = clean_text(raw.get("work_order_id")) or None
    visit_type = clean_text(raw.get("visit_type")).lower()
    visit_type_valid = visit_type in VISIT_TYPES

    appt_raw = raw.get("appointment") if isinstance(raw.get("appointment"), dict) else {}
    appt_start = parse_dt(appt_raw.get("start"))
    appt_end = parse_dt(appt_raw.get("end"))
    timezone = clean_text(appt_raw.get("timezone")) or None
    appointment = {
        "start": clean_text(appt_raw.get("start")) or None,
        "end": clean_text(appt_raw.get("end")) or None,
        "timezone": timezone,
        "start_dt": appt_start,
        "end_dt": appt_end,
        "in_past": bool(appt_start is not None and as_of_dt is not None and appt_start < as_of_dt),
        "well_formed": appt_start is not None and appt_end is not None
        and (appt_end >= appt_start),
    }

    site = raw.get("site") if isinstance(raw.get("site"), dict) else {}
    access = {
        "contact": clean_text(site.get("contact")) or None,
        "access_notes": clean_text(site.get("access_notes")) or None,
        "confirmed": read_bool(site.get("access_confirmed")),
    }

    approval = raw.get("approval") if isinstance(raw.get("approval"), dict) else {}
    scope = {
        "approved": read_bool(approval.get("scope_approved")),
        "approved_by": clean_text(approval.get("approved_by")) or None,
        "approval_ref": clean_text(approval.get("approval_ref")) or None,
        "scope_text_present": has_text(raw.get("approved_scope")),
        "text": clean_text(raw.get("approved_scope")),
    }

    req_raw = raw.get("required_skills")
    required_skills = []
    if isinstance(req_raw, list):
        for item in req_raw:
            name = clean_text(item)
            if name and name not in required_skills:
                required_skills.append(name)

    tech = raw.get("assigned_technician") if isinstance(raw.get("assigned_technician"), dict) else {}
    tech_id = clean_text(tech.get("technician_id")) or None
    tech_skills_raw = tech.get("skills")
    tech_skills = None
    if isinstance(tech_skills_raw, list):
        tech_skills = []
        for item in tech_skills_raw:
            name = clean_text(item)
            if name and name not in tech_skills:
                tech_skills.append(name)

    covered = [s for s in required_skills if tech_skills is not None and s in tech_skills]
    missing = [s for s in required_skills if tech_skills is not None and s not in tech_skills]
    if tech_skills is None:
        skill_state = "UNKNOWN"
    elif missing:
        skill_state = "GAP"
    else:
        skill_state = "COVERED"

    windows_raw = tech.get("available_windows")
    windows_provided = isinstance(windows_raw, list)
    windows = []
    if windows_provided:
        for offset, item in enumerate(windows_raw):
            window = normalise_window(item, offset, "available_windows[%d]" % offset)
            if window is not None:
                windows.append(window)
    if not windows_provided or appt_start is None or appt_end is None or not appointment["well_formed"]:
        availability_state = "UNKNOWN"
    elif any(w["start"] <= appt_start and appt_end <= w["end"] for w in windows):
        availability_state = "AVAILABLE"
    else:
        availability_state = "UNAVAILABLE"
    availability = {"state": availability_state, "technician_id": tech_id,
                    "windows_provided": windows_provided}

    equipment = []
    eq_raw = raw.get("equipment")
    if isinstance(eq_raw, list):
        for offset, item in enumerate(eq_raw):
            if not isinstance(item, dict):
                continue
            known = read_bool(item.get("known"))
            equipment.append({
                "equipment_id": clean_text(item.get("equipment_id")) or None,
                "path": "equipment[%d]" % offset,
                "label": clean_text(item.get("label")),
                "known": known,
            })

    parts = normalise_parts(raw.get("parts"))
    tools = normalise_tools(raw.get("tools"))
    documents, refused_refs = normalise_documents(raw.get("documents"))

    gating = visit_type in PART_GATING_VISIT_TYPES
    parts_blocking = False
    for part in parts:
        part["blocks"] = False
        part["flag"] = None
        if gating and part["required"]:
            if part["state"] == "unknown":
                part["flag"] = "UNKNOWN_PART_STATE"
            elif part["state"] != "reserved":
                part["flag"] = "NOT_RESERVED"
                part["blocks"] = True
                parts_blocking = True

    blockers = []
    unknowns = []
    review_flags = []

    if not visit_type_valid:
        blockers.append("INVALID_VISIT_TYPE")
    if not appointment["well_formed"]:
        unknowns.append("UNKNOWN_APPOINTMENT")
    else:
        if appointment["in_past"]:
            blockers.append("APPOINTMENT_IN_PAST")
        if timezone is None:
            unknowns.append("UNKNOWN_TIMEZONE")
    if access["confirmed"] is False:
        blockers.append("ACCESS_NOT_CONFIRMED")
    elif access["confirmed"] is None:
        unknowns.append("UNKNOWN_ACCESS")
    if scope["approved"] is False:
        blockers.append("SCOPE_NOT_APPROVED")
    elif scope["approved"] is None:
        unknowns.append("UNKNOWN_SCOPE_APPROVAL")
    if not scope["scope_text_present"]:
        unknowns.append("SCOPE_TEXT_MISSING")
    if skill_state == "GAP":
        blockers.append("SKILL_GAP")
    elif skill_state == "UNKNOWN":
        unknowns.append("UNKNOWN_SKILLS")
    if availability_state == "UNAVAILABLE":
        blockers.append("TECHNICIAN_UNAVAILABLE")
    elif availability_state == "UNKNOWN":
        unknowns.append("UNKNOWN_AVAILABILITY")
    if not equipment:
        unknowns.append("EQUIPMENT_NOT_RECORDED")
    elif any(item["known"] is None for item in equipment):
        unknowns.append("UNKNOWN_EQUIPMENT_FACT")
    for part in parts:
        if part["flag"] == "NOT_RESERVED":
            review_flags.append("PART_NOT_RESERVED:%s" % (part["part_id"] or part["path"]))
            blockers.append("PART_NOT_RESERVED:%s" % (part["part_id"] or part["path"]))
        elif part["flag"] == "UNKNOWN_PART_STATE":
            unknowns.append("UNKNOWN_PART_STATE:%s" % (part["part_id"] or part["path"]))
    for tool in tools:
        if tool["required"] and tool["available"] is False:
            blockers.append("TOOL_UNAVAILABLE:%s" % (tool["tool_id"] or tool["path"]))
        elif tool["required"] and tool["available"] is None:
            unknowns.append("UNKNOWN_TOOL_AVAILABILITY:%s" % (tool["tool_id"] or tool["path"]))
    for doc in documents:
        if doc["required"] and doc["available"] is False:
            blockers.append("DOCUMENT_UNAVAILABLE:%s" % (doc["doc_id"] or doc["path"]))
        elif doc["required"] and doc["available"] is None:
            unknowns.append("UNKNOWN_DOCUMENT_AVAILABILITY:%s" % (doc["doc_id"] or doc["path"]))

    due_at_raw = raw.get("due_at")
    due_dt = parse_dt(due_at_raw)
    if due_at_raw is None:
        due_state = "NOT_PROVIDED"
    elif due_dt is None:
        due_state = "INVALID"
        review_flags.append("INVALID_DUE_AT")
    elif as_of_dt is not None and due_dt < as_of_dt:
        due_state = "OVERDUE"
        review_flags.append("OVERDUE")
    else:
        due_state = "ON_TRACK"

    if blockers:
        status = "BLOCKED"
    elif unknowns:
        status = "INSUFFICIENT_EVIDENCE"
    elif visit_type == "diagnosis":
        status = "READY_FOR_DIAGNOSIS"
    else:
        status = "READY_FOR_PLANNED_WORK"

    return {
        "work_order_id": wo_id, "index": index, "path": path, "invalid": False,
        "visit_type": visit_type if visit_type_valid else (visit_type or None),
        "visit_type_valid": visit_type_valid,
        "parts_gating": gating,
        "status": status,
        "appointment": appointment,
        "access": access,
        "scope": scope,
        "skills": {"required": required_skills, "covered": covered,
                   "missing": missing, "state": skill_state},
        "availability": availability,
        "equipment": equipment,
        "parts": parts,
        "parts_blocking": parts_blocking,
        "tools": tools,
        "documents": documents,
        "refused_refs": refused_refs,
        "blockers": blockers,
        "unknowns": unknowns,
        "review_flags": review_flags,
        "flags": [],
        "owner": clean_text(raw.get("owner")) or None,
        "due_at": clean_text(due_at_raw) if has_text(due_at_raw) else None,
        "due_state": due_state,
    }


# --------------------------------------------------------------------------
# cross-order work
# --------------------------------------------------------------------------
def find_double_bookings(work_orders):
    """Same technician, overlapping appointment windows: a definite conflict."""
    bookings = []
    for order in work_orders:
        appt = order.get("appointment") or {}
        tech_id = (order.get("availability") or {}).get("technician_id")
        if tech_id and appt.get("well_formed"):
            bookings.append((tech_id, appt["start_dt"], appt["end_dt"],
                             order.get("work_order_id") or order["path"]))
    conflicts = []
    for i in range(len(bookings)):
        for j in range(i + 1, len(bookings)):
            tech_a, start_a, end_a, id_a = bookings[i]
            tech_b, start_b, end_b, id_b = bookings[j]
            if tech_a != tech_b:
                continue
            if start_a < end_b and start_b < end_a:
                conflicts.append({
                    "technician_id": tech_a,
                    "work_order_ids": sorted([id_a, id_b]),
                })
    conflicts.sort(key=lambda c: (c["technician_id"], c["work_order_ids"]))
    return conflicts


def build_questions(work_orders, refused_refs):
    questions = []
    for order in work_orders:
        label = order.get("work_order_id") or order["path"]
        for unknown in order.get("unknowns", []):
            questions.append((label, unknown))
        for refused in order.get("refused_refs", []):
            questions.append((label, "REFUSED_REF:" + refused["path"]))
    for refused in refused_refs:
        questions.append(("全局", "REFUSED_REF:" + refused["path"]))
    out = []
    for index, (label, topic) in enumerate(questions, start=1):
        out.append({
            "id": "Q-%02d" % index,
            "work_order_id": label,
            "topic": topic,
            "question": _question_text(topic),
        })
    return out


def _question_text(topic):
    mapping = {
        "UNKNOWN_APPOINTMENT": "预约时窗缺失或时间格式无效（需带时区偏移），请补齐后再判断能否出发。",
        "UNKNOWN_TIMEZONE": "预约时窗未给时区，请确认按哪个时区到场。",
        "UNKNOWN_ACCESS": "现场准入状态未知，请确认是否需要门禁、陪同或提前报备。",
        "UNKNOWN_SCOPE_APPROVAL": "已批准范围状态未知，请确认本次作业范围是否已批准。",
        "SCOPE_TEXT_MISSING": "未记录已批准范围文本，请补齐后再判断派工交接内容。",
        "UNKNOWN_SKILLS": "技术人员技能清单未知，无法判断所需技能是否覆盖。",
        "UNKNOWN_AVAILABILITY": "技术人员可用时段未知，无法判断预约时窗内是否可到场。",
        "EQUIPMENT_NOT_RECORDED": "未记录设备事实，请补充设备编号与已知信息。",
        "UNKNOWN_EQUIPMENT_FACT": "设备事实存在未知项，请确认后再派工。",
    }
    if topic.startswith("PART_NOT_RESERVED"):
        return "所需零件尚未预留（ordered 不等于 received/reserved），请确认预留状态。"
    if topic.startswith("UNKNOWN_PART_STATE"):
        return "所需零件状态未知，请确认是否已预留。"
    if topic.startswith("REFUSED_REF"):
        return "附件引用只接受单一文件名；含路径或链接的引用已被拒绝且未回显，请改为文件名。"
    return mapping.get(topic, "存在待确认事实：" + topic)


def build_departure_checklist(work_orders):
    checklist = []
    for order in work_orders:
        label = order.get("work_order_id") or order["path"]
        appt = order.get("appointment") or {}
        access = order.get("access") or {}
        scope = order.get("scope") or {}
        skills = order.get("skills") or {}
        availability = order.get("availability") or {}
        items = [
            ("预约时窗与时区", "OK" if appt.get("well_formed") else "UNKNOWN"),
            ("现场联系人与准入", "OK" if access.get("confirmed") is True
             else ("GAP" if access.get("confirmed") is False else "UNKNOWN")),
            ("已批准范围", "OK" if scope.get("approved") is True
             else ("GAP" if scope.get("approved") is False else "UNKNOWN")),
            ("所需技能覆盖", {"COVERED": "OK", "GAP": "GAP"}.get(skills.get("state"), "UNKNOWN")),
            ("人员可用时段", {"AVAILABLE": "OK", "UNAVAILABLE": "GAP"}.get(
                availability.get("state"), "UNKNOWN")),
            ("零件证据链（仅安装/保养）", _parts_check(order)),
            ("工具", _tool_check(order)),
            ("文档", _doc_check(order)),
        ]
        for name, state in items:
            checklist.append({"work_order_id": label, "item": name, "state": state})
    return checklist


def _parts_check(order):
    if not order.get("parts_gating"):
        return "N/A"
    required = [p for p in order.get("parts", []) if p["required"]]
    if not required:
        return "N/A"
    if any(p["flag"] == "NOT_RESERVED" for p in required):
        return "GAP"
    if any(p["flag"] == "UNKNOWN_PART_STATE" for p in required):
        return "UNKNOWN"
    return "OK"


def _tool_check(order):
    required = [t for t in order.get("tools", []) if t["required"]]
    if not required:
        return "N/A"
    if any(t["available"] is False for t in required):
        return "GAP"
    if any(t["available"] is None for t in required):
        return "UNKNOWN"
    return "OK"


def _doc_check(order):
    required = [d for d in order.get("documents", []) if d["required"]]
    if not required:
        return "N/A"
    if any(d["available"] is False for d in required):
        return "GAP"
    if any(d["available"] is None for d in required):
        return "UNKNOWN"
    return "OK"


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def render_markdown(status, as_of, counts, work_orders, coverage_matrix, parts_evidence,
                    gaps, checklist, human_confirm, double_bookings, responsibility,
                    questions, warnings):
    lines = []
    lines.append("# 上门服务出发前工单准备包")
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 工单数：%d" % len(work_orders))
    lines.append("")
    lines.append("## 工单结论")
    lines.append("")
    lines.append("| 状态 | 数量 |")
    lines.append("|---|---:|")
    for name in WO_STATES:
        lines.append("| %s | %d |" % (name, counts[name]))
    lines.append("")
    lines.append("| 工单 | 访问类型 | 结论 | 技术员 | 预约 | 责任人 | 截止 |")
    lines.append("|---|---|---|---|---|---|---|")
    for order in work_orders:
        appt = order.get("appointment") or {}
        tech_id = (order.get("availability") or {}).get("technician_id")
        window = "%s → %s" % (
            esc(appt.get("start")) if appt.get("start") else "未提供",
            esc(appt.get("end")) if appt.get("end") else "未提供",
        )
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            esc(order.get("work_order_id")) if order.get("work_order_id") else esc(order["path"]),
            esc(order.get("visit_type")) if order.get("visit_type") else "未提供",
            order["status"],
            esc(tech_id) if tech_id else "未指派",
            window,
            esc(order.get("owner")) if order.get("owner") else "未指派",
            esc(order.get("due_at")) if order.get("due_at") else "未提供",
        ))
    lines.append("")

    lines.append("## 人员技能覆盖")
    lines.append("")
    lines.append("| 工单 | 所需技能 | 已覆盖 | 缺口 | 状态 |")
    lines.append("|---|---|---|---|---|")
    for row in coverage_matrix:
        lines.append("| %s | %s | %s | %s | %s |" % (
            esc(row["work_order_id"]) if row["work_order_id"] else esc(row["path"]),
            esc("、".join(row["required"])) if row["required"] else "无",
            esc("、".join(row["covered"])) if row["covered"] else "无",
            esc("、".join(row["missing"])) if row["missing"] else "无",
            row["state"],
        ))
    lines.append("")

    lines.append("## 零件证据链")
    lines.append("")
    if parts_evidence:
        lines.append("| 工单 | 零件 | 状态 | ordered | received | inspected | reserved | 阻塞 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for part in parts_evidence:
            evidence = part["evidence"]
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                esc(part["work_order_id"]) if part["work_order_id"] else esc(part["path"]),
                esc(part["part_id"]) if part["part_id"] else esc(part["path"]),
                esc(part["state"]),
                tri(evidence["ordered"]), tri(evidence["received"]),
                tri(evidence["inspected"]), tri(evidence["reserved"]),
                "是" if part["blocks"] else "否",
            ))
    else:
        lines.append("- 无零件记录")
    lines.append("")

    lines.append("## 准入 / 范围 / 批准缺口")
    lines.append("")
    if gaps:
        lines.append("| 工单 | 项目 | 状态 |")
        lines.append("|---|---|---|")
        for gap in gaps:
            lines.append("| %s | %s | %s |" % (
                esc(gap["work_order_id"]) if gap["work_order_id"] else esc(gap["path"]),
                esc(gap["item"]), gap["state"],
            ))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 范围与现场说明")
    lines.append("")
    if any((order.get("scope") or {}).get("text")
           or (order.get("access") or {}).get("access_notes") for order in work_orders):
        lines.append("| 工单 | 已批准范围 | 现场准入说明 |")
        lines.append("|---|---|---|")
        for order in work_orders:
            scope_text = (order.get("scope") or {}).get("text")
            access_notes = (order.get("access") or {}).get("access_notes")
            lines.append("| %s | %s | %s |" % (
                esc(order.get("work_order_id")) if order.get("work_order_id") else esc(order["path"]),
                hidden(scope_text) if scope_text else "未记录",
                hidden(access_notes) if access_notes else "未记录",
            ))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 出发前交接单")
    lines.append("")
    if checklist:
        lines.append("| 工单 | 检查项 | 状态 |")
        lines.append("|---|---|---|")
        for row in checklist:
            lines.append("| %s | %s | %s |" % (
                esc(row["work_order_id"]), esc(row["item"]), row["state"]))
    else:
        lines.append("- 无")
    lines.append("")

    if double_bookings:
        lines.append("## 人员重复派工")
        lines.append("")
        for conflict in double_bookings:
            lines.append("- 技术员 %s：工单 %s 时段重叠" % (
                esc(conflict["technician_id"]),
                esc("、".join(conflict["work_order_ids"]))))
        lines.append("")

    lines.append("## 人工确认项")
    lines.append("")
    for item in human_confirm:
        lines.append("- %s" % esc(item))
    lines.append("")

    lines.append("## 责任与截止")
    lines.append("")
    if responsibility:
        lines.append("| 工单 | 责任人 | 截止 | 截止状态 |")
        lines.append("|---|---|---|---|")
        for row in responsibility:
            lines.append("| %s | %s | %s | %s |" % (
                esc(row["work_order_id"]),
                esc(row["owner"]) if row["owner"] else "未指派",
                esc(row["due_at"]) if row["due_at"] else "未提供",
                esc(row["due_state"]),
            ))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 待确认问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- `%s` [%s] %s" % (
                question["id"], esc(question["work_order_id"]), esc(question["question"])))
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
    raw_orders = data.get("work_orders")
    if not isinstance(raw_orders, list):
        raw_orders = []

    warnings = []
    if as_of_dt is None:
        warnings.append("AS_OF_MISSING_OR_INVALID")
    if not raw_orders:
        warnings.append("NO_WORK_ORDERS")
    if warnings:
        return envelope("INPUT_INCOMPLETE", as_of_raw, injections, warnings)

    work_orders = [normalise_wo(raw, index, as_of_dt) for index, raw in enumerate(raw_orders)]

    double_bookings = find_double_bookings(work_orders)
    for conflict in double_bookings:
        for order in work_orders:
            label = order.get("work_order_id") or order["path"]
            if label in conflict["work_order_ids"]:
                order["blockers"].append("DOUBLE_BOOKED:%s" % conflict["technician_id"])
    for order in work_orders:
        if order["blockers"]:
            order["status"] = "BLOCKED"
        elif order["unknowns"]:
            order["status"] = "INSUFFICIENT_EVIDENCE"
        elif order["visit_type"] == "diagnosis":
            order["status"] = "READY_FOR_DIAGNOSIS"
        else:
            order["status"] = "READY_FOR_PLANNED_WORK"

    work_orders.sort(key=lambda o: (o.get("work_order_id") or "", o["path"]))

    # Parsed datetimes were only needed for the window and conflict maths above;
    # they must never leak into the serialisable output.
    for order in work_orders:
        appt = order.get("appointment")
        if isinstance(appt, dict):
            appt.pop("start_dt", None)
            appt.pop("end_dt", None)

    counts = {name: 0 for name in WO_STATES}
    for order in work_orders:
        counts[order["status"]] += 1

    coverage_matrix = [{
        "work_order_id": order.get("work_order_id"),
        "path": order["path"],
        "required": (order.get("skills") or {}).get("required", []),
        "covered": (order.get("skills") or {}).get("covered", []),
        "missing": (order.get("skills") or {}).get("missing", []),
        "state": (order.get("skills") or {}).get("state", "UNKNOWN"),
    } for order in work_orders]

    parts_evidence = []
    for order in work_orders:
        for part in order.get("parts", []):
            parts_evidence.append({
                "work_order_id": order.get("work_order_id"),
                "path": order["path"] + "/" + part["path"],
                "part_id": part["part_id"],
                "state": part["state"],
                "required": part["required"],
                "evidence": part["evidence"],
                "blocks": part["blocks"],
            })
    parts_evidence.sort(key=lambda p: (p["work_order_id"] or "", p["path"]))

    gaps = []
    for order in work_orders:
        label = order.get("work_order_id")
        path = order["path"]
        appt = order.get("appointment") or {}
        access = order.get("access") or {}
        scope = order.get("scope") or {}
        if access.get("confirmed") is not True:
            gaps.append({"work_order_id": label, "path": path, "item": "现场准入",
                         "state": "GAP" if access.get("confirmed") is False else "UNKNOWN"})
        if scope.get("approved") is not True:
            gaps.append({"work_order_id": label, "path": path, "item": "范围批准",
                         "state": "GAP" if scope.get("approved") is False else "UNKNOWN"})
        if not appt.get("well_formed"):
            gaps.append({"work_order_id": label, "path": path, "item": "预约时窗", "state": "UNKNOWN"})
        elif appt.get("timezone") is None:
            gaps.append({"work_order_id": label, "path": path, "item": "时区", "state": "UNKNOWN"})

    checklist = build_departure_checklist(work_orders)

    human_confirm = list(HUMAN_CONFIRM_BASE)
    if any(order.get("parts_gating") for order in work_orders):
        human_confirm.append("零件预留与到货以现场实际清点为准（工具只读取登记状态）")
    if any((order.get("availability") or {}).get("technician_id") for order in work_orders):
        human_confirm.append("最终派工与改期由人工决定（工具只记录可用时段）")

    responsibility = [{
        "work_order_id": order.get("work_order_id") or order["path"],
        "owner": order.get("owner"),
        "due_at": order.get("due_at"),
        "due_state": order.get("due_state"),
    } for order in work_orders]

    refused_all = []
    for order in work_orders:
        for refused in order.get("refused_refs", []):
            refused_all.append({"work_order_id": order.get("work_order_id"),
                                "path": order["path"] + "/" + refused["path"],
                                "reason": refused["reason"]})
    questions = build_questions(work_orders, [])

    if counts["BLOCKED"]:
        status = "BLOCKED"
    elif counts["INSUFFICIENT_EVIDENCE"]:
        status = "GAPS_FOUND"
    else:
        status = "READY"

    markdown = render_markdown(status, as_of_raw, counts, work_orders, coverage_matrix,
                               parts_evidence, gaps, checklist, human_confirm,
                               double_bookings, responsibility, questions, warnings)

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "as_of_date": (as_of_dt.date().isoformat() if as_of_dt else None),
        "work_order_count": len(work_orders),
        "status_counts": counts,
        "work_orders": work_orders,
        "coverage_matrix": coverage_matrix,
        "parts_evidence": parts_evidence,
        "access_scope_approval_gaps": gaps,
        "departure_checklist": checklist,
        "human_confirm_items": human_confirm,
        "double_bookings": double_bookings,
        "refused_refs": refused_all,
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
