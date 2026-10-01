#!/usr/bin/env python3
"""预约服务爽约风险准备包 — offline appointment no-show readiness builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes and no booking/contact/calendar action: attachment-style references
are handled as file *basenames*.

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
MODES = ("on_site", "in_store", "remote")

# A reminder only counts as *evidence of having been sent* in these states.
REMINDER_SENT_STATES = ("sent", "delivered")
REMINDER_STATES = ("sent", "delivered", "unknown")

APPT_STATES = ("READY", "ACTION_NEEDED", "WAITING_ON_CUSTOMER",
               "INSUFFICIENT_EVIDENCE", "BLOCKED")

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
# the SAME sentence, so ordinary scheduling notes are not mislabelled.
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
    "本输出是预约到期前的人工准备材料，不是爽约判定、客户信用评价或收入结论；"
    "是否改期、取消、收费或终止服务由经营者依预约政策与当地规定人工决定。"
)

HUMAN_CONFIRM_BASE = (
    "爽约判定与后续处理须由人工决定（本工具不判定客户是否爽约）",
    "改期、取消、收取订金与终止服务须由人工决定（本工具不自动执行）",
    "客户沟通须在取得联系同意后由人工发送（本工具只生成未发送的草稿）",
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
        "appointment_count": 0,
        "status_counts": {name: 0 for name in APPT_STATES},
        "appointments": [],
        "today_confirmation_list": [],
        "do_not_contact": [],
        "contact_actions": [],
        "preparation_gaps": [],
        "standby_windows": [],
        "owner_conflicts": [],
        "human_confirm_items": [],
        "responsibility": [],
        "clarification_questions": [],
        "refused_refs": [],
        "injection_flagged": [],
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "markdown_summary": (
            "# 预约服务爽约风险准备包\n\n"
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
        "appointment_count": 0,
        "status_counts": {name: 0 for name in APPT_STATES},
        "appointments": [],
        "today_confirmation_list": [],
        "do_not_contact": [],
        "contact_actions": [],
        "preparation_gaps": [],
        "standby_windows": [],
        "owner_conflicts": [],
        "human_confirm_items": [],
        "responsibility": [],
        "clarification_questions": [],
        "refused_refs": [],
        "injection_flagged": list(injections),
        "input_warnings": list(warnings),
        "markdown_summary": (
            "# 预约服务爽约风险准备包\n\n"
            "- 状态：**%s**\n"
            "- 原因：%s\n\n"
            "> 请补齐必填输入后重新提交。\n" % (status, "; ".join(warnings) or "输入不完整")
        ),
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# record normalisation
# --------------------------------------------------------------------------
def normalise_reminders(raw_reminders):
    reminders = []
    if not isinstance(raw_reminders, list):
        return reminders, False
    for index, raw in enumerate(raw_reminders):
        if not isinstance(raw, dict):
            continue
        state = clean_text(raw.get("state")).lower()
        if state not in REMINDER_STATES:
            state = "unknown"
        receipt_raw = raw.get("receipt_basename")
        if has_text(receipt_raw):
            ok, base, reason = safe_basename(receipt_raw)
        else:
            # Absent receipt reference is simply not recorded, not a refusal.
            ok, base, reason = True, None, None
        reminders.append({
            "reminder_id": clean_text(raw.get("reminder_id")) or None,
            "path": "reminders[%d]" % index,
            "channel": clean_text(raw.get("channel")) or None,
            "state": state,
            "sent_at": clean_text(raw.get("sent_at")) or None,
            # A receipt reference is only ever exposed as a safe basename.
            "receipt_basename": base,
            "receipt_refused": not ok,
            "receipt_reason": reason,
        })
    return reminders, True


def normalise_preparation(raw_items):
    items = []
    if not isinstance(raw_items, list):
        return items, False
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            continue
        required = read_bool(raw.get("required"))
        if required is None:
            required = True
        ready = read_bool(raw.get("ready"))
        items.append({
            "item_id": clean_text(raw.get("item_id")) or None,
            "path": "preparation[%d]" % index,
            "label": clean_text(raw.get("label")),
            "required": required,
            "ready": ready,
        })
    return items, True


def normalise_standby(raw):
    if not isinstance(raw, dict):
        return None
    start = parse_dt(raw.get("start"))
    end = parse_dt(raw.get("end"))
    if start is None or end is None or end < start:
        return {"recorded": True, "valid": False,
                "start": clean_text(raw.get("start")) or None,
                "end": clean_text(raw.get("end")) or None,
                "start_dt": None, "end_dt": None}
    return {"recorded": True, "valid": True,
            "start": clean_text(raw.get("start")) or None,
            "end": clean_text(raw.get("end")) or None,
            "start_dt": start, "end_dt": end}


def normalise_appointment(raw, index, as_of_dt):
    path = "appointments[%d]" % index
    if not isinstance(raw, dict):
        return {
            "appointment_id": None, "index": index, "path": path, "invalid": True,
            "service_name": "", "mode": None, "mode_state": "UNKNOWN",
            "window": None, "customer": None, "reminders": [], "reminders_provided": False,
            "preparation": [], "preparation_provided": False,
            "policy": {"acknowledged": None, "ref": None}, "standby": None,
            "standby_state": "NOT_RECORDED",
            "owner": None, "notes": "",
            # A record we cannot read cannot be prepared at all: it blocks.
            "status": "BLOCKED",
            "blockers": ["INVALID_APPOINTMENT_RECORD"],
            "unknowns": [], "review_flags": ["INVALID_APPOINTMENT_RECORD"],
            "action_reasons": [], "prep_gaps": [], "refused_refs": [],
        }

    appt_id = clean_text(raw.get("appointment_id")) or None

    mode_raw = clean_text(raw.get("mode")).lower()
    if not mode_raw:
        mode_state = "UNKNOWN"
    elif mode_raw in MODES:
        mode_state = "OK"
    else:
        mode_state = "INVALID"

    start = parse_dt(raw.get("start"))
    end = parse_dt(raw.get("end"))
    timezone = clean_text(raw.get("timezone")) or None
    well_formed = start is not None and end is not None and end >= start
    window = {
        "start": clean_text(raw.get("start")) or None,
        "end": clean_text(raw.get("end")) or None,
        "timezone": timezone,
        "well_formed": well_formed,
        # 预约已经过去不等于客户爽约：only a review flag, never a verdict.
        "in_past": bool(start is not None and as_of_dt is not None and start < as_of_dt),
        "start_dt": start,
        "end_dt": end,
    }

    customer_raw = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
    customer = {
        "name": clean_text(customer_raw.get("name")) or None,
        "contact_channel": clean_text(customer_raw.get("contact_channel")) or None,
        "contact_consent": read_bool(customer_raw.get("contact_consent")),
        "confirmed": read_bool(customer_raw.get("confirmed")),
    }

    reminders, reminders_provided = normalise_reminders(raw.get("reminders"))
    preparation, preparation_provided = normalise_preparation(raw.get("preparation"))
    policy = {
        "acknowledged": read_bool(raw.get("policy_acknowledged")),
        "ref": clean_text(raw.get("policy_ref")) or None,
    }
    standby = normalise_standby(raw.get("standby"))
    if standby is None or not standby["recorded"]:
        standby_state = "NOT_RECORDED"
    elif not standby["valid"]:
        standby_state = "INVALID"
    elif well_formed and standby["start_dt"] < end and start < standby["end_dt"]:
        standby_state = "OVERLAPS_APPOINTMENT"
    else:
        standby_state = "OK"
    owner = clean_text(raw.get("owner")) or None
    notes = clean_text(raw.get("notes"))

    blockers = []
    unknowns = []
    review_flags = []
    action_reasons = []
    refused_refs = []

    if mode_state == "INVALID":
        blockers.append("INVALID_MODE")
    elif mode_state == "UNKNOWN":
        unknowns.append("UNKNOWN_MODE")
    if not well_formed:
        unknowns.append("UNKNOWN_APPOINTMENT_WINDOW")
    elif timezone is None:
        unknowns.append("UNKNOWN_TIMEZONE")
    if customer["confirmed"] is None:
        unknowns.append("UNKNOWN_CUSTOMER_CONFIRMATION")
    if customer["contact_consent"] is None:
        unknowns.append("UNKNOWN_CONTACT_CONSENT")
    if not customer["contact_channel"]:
        unknowns.append("UNKNOWN_CONTACT_CHANNEL")
    if not reminders_provided or not reminders:
        unknowns.append("NO_REMINDER_EVIDENCE")
    else:
        for reminder in reminders:
            if reminder["state"] == "unknown":
                unknowns.append("UNKNOWN_REMINDER_STATE:%s"
                                % (reminder["reminder_id"] or reminder["path"]))
    if not preparation_provided:
        unknowns.append("PREPARATION_NOT_RECORDED")
    else:
        for item in preparation:
            if item["required"] and item["ready"] is None:
                unknowns.append("UNKNOWN_PREPARATION_STATE:%s"
                                % (item["item_id"] or item["path"]))
    if policy["acknowledged"] is None:
        unknowns.append("UNKNOWN_POLICY_ACKNOWLEDGEMENT")
    if owner is None:
        unknowns.append("UNKNOWN_OWNER")
    if standby is not None and standby["recorded"] and not standby["valid"]:
        unknowns.append("INVALID_STANDBY_WINDOW")

    # A missing standby window is *not* an unknown verdict: it is reported as
    # `NOT_RECORDED` in the standby table only, so the first run is not blocked
    # on an optional scheduling convenience.

    for reminder in reminders:
        if reminder["receipt_refused"]:
            refused_refs.append({"path": path + "/" + reminder["path"] + "/receipt_basename",
                                 "reason": reminder["receipt_reason"]})

    prep_gaps = []
    if preparation_provided:
        for item in preparation:
            if item["required"] and item["ready"] is False:
                prep_gaps.append({
                    "appointment_id": appt_id, "path": path,
                    "item_id": item["item_id"], "item_path": item["path"],
                    "label": item["label"],
                })

    reminder_sent = any(r["state"] in REMINDER_SENT_STATES for r in reminders)

    if blockers:
        status = "BLOCKED"
    elif unknowns:
        status = "INSUFFICIENT_EVIDENCE"
    elif window["in_past"]:
        # Deliberately not a no-show: the tool only asks for a human decision.
        review_flags.append("WINDOW_IN_PAST")
        action_reasons.append("过期预约需人工决定处理方式（不判定爽约）")
        status = "ACTION_NEEDED"
    elif customer["confirmed"] is False:
        if customer["contact_consent"] is True:
            action_reasons.append("客户尚未确认，已获联系同意，可人工发出确认")
            status = "ACTION_NEEDED"
        else:
            # Nothing can be done today by us; the customer owes the next move.
            status = "WAITING_ON_CUSTOMER"
    else:
        if prep_gaps:
            action_reasons.append("前置准备未就绪，需补齐：%s" % "、".join(
                (g["item_id"] or g["label"] or g["item_path"]) for g in prep_gaps))
        if policy["acknowledged"] is False:
            action_reasons.append("预约政策知情尚未确认")
        if not reminder_sent:
            action_reasons.append("尚无已发提醒证据，需人工决定是否补发提醒")
        status = "ACTION_NEEDED" if action_reasons else "READY"

    return {
        "appointment_id": appt_id,
        "index": index,
        "path": path,
        "invalid": False,
        "service_name": clean_text(raw.get("service_name")),
        "mode": mode_raw or None,
        "mode_state": mode_state,
        "status": status,
        "window": window,
        "customer": customer,
        "reminders": reminders,
        "reminders_provided": reminders_provided,
        "reminder_sent": reminder_sent,
        "preparation": preparation,
        "preparation_provided": preparation_provided,
        "policy": policy,
        "standby": standby,
        "standby_state": standby_state,
        "owner": owner,
        "notes": notes,
        "blockers": blockers,
        "unknowns": unknowns,
        "review_flags": review_flags,
        "action_reasons": action_reasons,
        "prep_gaps": prep_gaps,
        "refused_refs": refused_refs,
    }


# --------------------------------------------------------------------------
# cross-appointment work
# --------------------------------------------------------------------------
def find_owner_conflicts(appointments):
    """Same owner, overlapping appointment windows: a definite conflict."""
    bookings = []
    for appt in appointments:
        window = appt.get("window") or {}
        if appt.get("owner") and window.get("well_formed"):
            bookings.append((appt["owner"], window["start_dt"], window["end_dt"],
                             appt.get("appointment_id") or appt["path"]))
    conflicts = []
    for i in range(len(bookings)):
        for j in range(i + 1, len(bookings)):
            owner_a, start_a, end_a, id_a = bookings[i]
            owner_b, start_b, end_b, id_b = bookings[j]
            if owner_a != owner_b:
                continue
            if start_a < end_b and start_b < end_a:
                conflicts.append({"owner": owner_a,
                                  "appointment_ids": sorted([id_a, id_b])})
    conflicts.sort(key=lambda c: (c["owner"], c["appointment_ids"]))
    return conflicts


def build_questions(appointments):
    questions = []
    for appt in appointments:
        label = appt.get("appointment_id") or appt["path"]
        for unknown in appt.get("unknowns", []):
            questions.append((label, unknown))
        for refused in appt.get("refused_refs", []):
            questions.append((label, "REFUSED_REF:" + refused["path"]))
    out = []
    for index, (label, topic) in enumerate(questions, start=1):
        out.append({
            "id": "Q-%02d" % index,
            "appointment_id": label,
            "topic": topic,
            "question": _question_text(topic),
        })
    return out


def _question_text(topic):
    mapping = {
        "UNKNOWN_MODE": "预约方式（上门 / 到店 / 远程）未知，请补充后再判断准备依赖。",
        "UNKNOWN_APPOINTMENT_WINDOW": "预约开始/结束时间缺失或格式无效（需带时区偏移），请补齐。",
        "UNKNOWN_TIMEZONE": "预约时窗未给时区，请确认按哪个时区到场。",
        "UNKNOWN_CUSTOMER_CONFIRMATION": "客户是否已确认到店未知，预约存在不等于已确认。",
        "UNKNOWN_CONTACT_CONSENT": "是否已获得联系同意未知；未取得同意前不会生成任何可发送动作。",
        "UNKNOWN_CONTACT_CHANNEL": "联系渠道未知，请补充客户可用的联系渠道。",
        "NO_REMINDER_EVIDENCE": "没有任何已发提醒证据；未知提醒状态不会被当成已发送。",
        "PREPARATION_NOT_RECORDED": "未记录到店/上门前置准备，请补充后再判断准备缺口。",
        "UNKNOWN_POLICY_ACKNOWLEDGEMENT": "预约政策知情状态未知，请确认客户是否已知悉政策。",
        "UNKNOWN_OWNER": "该预约未指定责任人，请指定后再排准备。",
        "INVALID_STANDBY_WINDOW": "备案时段格式无效或结束早于开始，请修正。",
    }
    if topic.startswith("UNKNOWN_REMINDER_STATE"):
        return "存在提醒的发送状态未知；未知状态不会被当作已发送。"
    if topic.startswith("UNKNOWN_PREPARATION_STATE"):
        return "存在必需前置准备的完成状态未知，请确认后就绪与否。"
    if topic.startswith("REFUSED_REF"):
        return "回执引用只接受单一文件名；含路径或链接的引用已被拒绝且未回显，请改为文件名。"
    return mapping.get(topic, "存在待确认事实：" + topic)


def build_today_list(appointments):
    """Today's human checklist: only appointments that actually need an action.

    `WAITING_ON_CUSTOMER` is deliberately excluded - nothing here is ours to do.
    """
    rows = []
    for appt in appointments:
        if appt["status"] not in ("ACTION_NEEDED", "BLOCKED"):
            continue
        label = appt.get("appointment_id") or appt["path"]
        owner = appt.get("owner")
        for reason in appt.get("action_reasons", []):
            rows.append({"appointment_id": label, "path": appt["path"],
                         "owner": owner, "reason": reason})
        for code in appt.get("blockers", []):
            rows.append({"appointment_id": label, "path": appt["path"], "owner": owner,
                         "reason": "确定阻塞，需人工解决：%s" % code})
    rows.sort(key=lambda r: (r["appointment_id"], r["reason"]))
    return rows


def build_contact_actions(appointments):
    """Only appointments with *explicit* contact consent get a draft action."""
    actions = []
    for appt in appointments:
        customer = appt.get("customer") or {}
        if customer.get("confirmed") is not False:
            continue
        if customer.get("contact_consent") is not True:
            continue
        window = appt.get("window") or {}
        actions.append({
            "appointment_id": appt.get("appointment_id") or appt["path"],
            "path": appt["path"],
            "channel": customer.get("contact_channel"),
            "window": "%s → %s" % (window.get("start") or "未提供",
                                   window.get("end") or "未提供"),
            "purpose": "人工确认客户是否能按时到场",
            "status": "DRAFT_NOT_SENT",
        })
    actions.sort(key=lambda a: (a["appointment_id"], a["channel"] or ""))
    return actions


def build_do_not_contact(appointments):
    rows = []
    for appt in appointments:
        customer = appt.get("customer") or {}
        if customer.get("contact_consent") is False:
            rows.append({
                "appointment_id": appt.get("appointment_id") or appt["path"],
                "path": appt["path"],
                "reason": "未获得联系同意，不生成任何可发送动作",
            })
    rows.sort(key=lambda r: r["appointment_id"])
    return rows


def build_standby_table(appointments):
    rows = []
    for appt in appointments:
        standby = appt.get("standby") or {}
        rows.append({
            "appointment_id": appt.get("appointment_id") or appt["path"],
            "path": appt["path"],
            "start": standby.get("start") if standby.get("recorded") else None,
            "end": standby.get("end") if standby.get("recorded") else None,
            "state": appt.get("standby_state", "NOT_RECORDED"),
        })
    rows.sort(key=lambda r: r["appointment_id"])
    return rows


def build_preparation_gaps(appointments):
    rows = []
    for appt in appointments:
        label = appt.get("appointment_id") or appt["path"]
        for gap in appt.get("prep_gaps", []):
            rows.append({"appointment_id": label, "path": appt["path"],
                         "item_id": gap["item_id"], "item_path": gap["item_path"],
                         "label": gap["label"], "state": "GAP"})
        if appt.get("preparation_provided"):
            for item in appt.get("preparation", []):
                if item["required"] and item["ready"] is None:
                    rows.append({"appointment_id": label, "path": appt["path"],
                                 "item_id": item["item_id"], "item_path": item["path"],
                                 "label": item["label"], "state": "UNKNOWN"})
    rows.sort(key=lambda r: (r["appointment_id"], r["item_path"]))
    return rows


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def render_markdown(status, as_of, business, counts, appointments, today_rows,
                    contact_actions, do_not_contact, prep_gaps, standby, conflicts,
                    human_confirm, responsibility, questions, warnings):
    lines = []
    lines.append("# 预约服务爽约风险准备包")
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    if business:
        lines.append("- 门店 / 服务：%s" % (hidden(business.get("name")) or "未提供"))
        lines.append("- 门店时区：%s" % (esc(business.get("timezone")) or "未提供"))
    lines.append("- 预约数：%d" % len(appointments))
    lines.append("")
    lines.append("## 预约结论")
    lines.append("")
    lines.append("| 状态 | 数量 |")
    lines.append("|---|---:|")
    for name in APPT_STATES:
        lines.append("| %s | %d |" % (name, counts[name]))
    lines.append("")
    lines.append("| 预约 | 服务 | 方式 | 结论 | 时窗 | 客户确认 | 联系同意 | 责任人 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for appt in appointments:
        window = appt.get("window") or {}
        customer = appt.get("customer") or {}
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            esc(appt.get("appointment_id")) if appt.get("appointment_id") else esc(appt["path"]),
            esc(appt.get("service_name")) or "未记录",
            esc(appt.get("mode")) if appt.get("mode") else "未记录",
            appt["status"],
            "%s → %s" % (esc(window.get("start")) if window.get("start") else "未提供",
                         esc(window.get("end")) if window.get("end") else "未提供"),
            tri(customer.get("confirmed")),
            tri(customer.get("contact_consent")),
            esc(appt.get("owner")) if appt.get("owner") else "未指派",
        ))
    lines.append("")

    lines.append("## 今日人工确认清单")
    lines.append("")
    if today_rows:
        lines.append("| 预约 | 责任人 | 待办 |")
        lines.append("|---|---|---|")
        for row in today_rows:
            lines.append("| %s | %s | %s |" % (
                esc(row["appointment_id"]),
                esc(row["owner"]) if row["owner"] else "未指派",
                hidden(row["reason"])))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 未发送的确认草稿")
    lines.append("")
    if contact_actions:
        lines.append("| 预约 | 渠道 | 时窗 | 用途 | 状态 |")
        lines.append("|---|---|---|---|---|")
        for action in contact_actions:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(action["appointment_id"]),
                esc(action["channel"]) if action["channel"] else "未记录",
                esc(action["window"]), esc(action["purpose"]), action["status"]))
        lines.append("")
        lines.append("> 以上全部为**草稿**，本工具不会发送。只有已获联系同意的预约才会出现在这里。")
    else:
        lines.append("- 无（未取得联系同意时不会生成任何可发送动作）")
    lines.append("")

    lines.append("## 不可联系清单")
    lines.append("")
    if do_not_contact:
        for row in do_not_contact:
            lines.append("- `%s` — %s" % (esc(row["appointment_id"]), esc(row["reason"])))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 前置准备缺口")
    lines.append("")
    if prep_gaps:
        lines.append("| 预约 | 项目 | 说明 | 状态 |")
        lines.append("|---|---|---|---|")
        for gap in prep_gaps:
            lines.append("| %s | %s | %s | %s |" % (
                esc(gap["appointment_id"]),
                esc(gap["item_id"]) if gap["item_id"] else esc(gap["item_path"]),
                hidden(gap["label"]) or "未记录", gap["state"]))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 备案时段表")
    lines.append("")
    if standby:
        lines.append("| 预约 | 备案开始 | 备案结束 | 状态 |")
        lines.append("|---|---|---|---|")
        for row in standby:
            lines.append("| %s | %s | %s | %s |" % (
                esc(row["appointment_id"]),
                esc(row["start"]) if row["start"] else "未备案",
                esc(row["end"]) if row["end"] else "未备案",
                row["state"]))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 服务与备注")
    lines.append("")
    if any(appt.get("service_name") or appt.get("notes") for appt in appointments):
        lines.append("| 预约 | 服务 | 备注 |")
        lines.append("|---|---|---|")
        for appt in appointments:
            lines.append("| %s | %s | %s |" % (
                esc(appt.get("appointment_id")) if appt.get("appointment_id") else esc(appt["path"]),
                hidden(appt.get("service_name")) or "未记录",
                hidden(appt.get("notes")) or "未记录"))
    else:
        lines.append("- 无")
    lines.append("")

    if conflicts:
        lines.append("## 责任人时段冲突")
        lines.append("")
        for conflict in conflicts:
            lines.append("- 责任人 %s：预约 %s 时段重叠" % (
                esc(conflict["owner"]),
                esc("、".join(conflict["appointment_ids"]))))
        lines.append("")

    lines.append("## 人工确认项")
    lines.append("")
    for item in human_confirm:
        lines.append("- %s" % esc(item))
    lines.append("")

    lines.append("## 责任与截止")
    lines.append("")
    if responsibility:
        lines.append("| 预约 | 责任人 | 责任状态 | 备案状态 |")
        lines.append("|---|---|---|---|")
        for row in responsibility:
            lines.append("| %s | %s | %s | %s |" % (
                esc(row["appointment_id"]),
                esc(row["owner"]) if row["owner"] else "未指派",
                row["owner_state"], row["standby_state"]))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 待确认问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- `%s` [%s] %s" % (
                question["id"], esc(question["appointment_id"]),
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


def tri(value):
    return "是" if value is True else ("否" if value is False else "未知")


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
    raw_appointments = data.get("appointments")
    if not isinstance(raw_appointments, list):
        raw_appointments = []

    warnings = []
    if as_of_dt is None:
        warnings.append("AS_OF_MISSING_OR_INVALID")
    if not raw_appointments:
        warnings.append("NO_APPOINTMENTS")
    if warnings:
        return envelope("INPUT_INCOMPLETE", as_of_raw, injections, warnings)

    appointments = [normalise_appointment(raw, index, as_of_dt)
                    for index, raw in enumerate(raw_appointments)]

    conflicts = find_owner_conflicts(appointments)
    for conflict in conflicts:
        for appt in appointments:
            label = appt.get("appointment_id") or appt["path"]
            if label in conflict["appointment_ids"]:
                code = "OWNER_DOUBLE_BOOKED:%s" % conflict["owner"]
                if code not in appt["blockers"]:
                    appt["blockers"].append(code)
    for appt in appointments:
        if appt["blockers"]:
            appt["status"] = "BLOCKED"
        elif appt["unknowns"]:
            appt["status"] = "INSUFFICIENT_EVIDENCE"
        elif appt["status"] not in APPT_STATES:
            appt["status"] = "INSUFFICIENT_EVIDENCE"

    appointments.sort(key=lambda a: (a.get("appointment_id") or "", a["path"]))

    # Parsed datetimes were only needed for the overlap and standby maths above;
    # they must never leak into the serialisable output.
    for appt in appointments:
        window = appt.get("window")
        if isinstance(window, dict):
            window.pop("start_dt", None)
            window.pop("end_dt", None)
        standby = appt.get("standby")
        if isinstance(standby, dict):
            standby.pop("start_dt", None)
            standby.pop("end_dt", None)

    counts = {name: 0 for name in APPT_STATES}
    for appt in appointments:
        counts[appt["status"]] += 1

    today_rows = build_today_list(appointments)
    contact_actions = build_contact_actions(appointments)
    do_not_contact = build_do_not_contact(appointments)
    prep_gaps = build_preparation_gaps(appointments)
    standby = build_standby_table(appointments)

    human_confirm = list(HUMAN_CONFIRM_BASE)
    if any(appt.get("reminders") for appt in appointments):
        human_confirm.append("提醒是否真正送达以运营商或客户回复为准（工具只读取登记状态）")
    if any((appt.get("customer") or {}).get("contact_consent") is True for appt in appointments):
        human_confirm.append("即使已获联系同意，最终是否联系仍由人工决定并留痕")

    responsibility = []
    for appt in appointments:
        standby_row = next((row for row in standby
                            if row["appointment_id"] == (appt.get("appointment_id")
                                                         or appt["path"])), None)
        responsibility.append({
            "appointment_id": appt.get("appointment_id") or appt["path"],
            "owner": appt.get("owner"),
            "owner_state": "ASSIGNED" if appt.get("owner") else "UNASSIGNED",
            "standby_state": standby_row["state"] if standby_row else "NOT_RECORDED",
        })

    refused_all = []
    for appt in appointments:
        for refused in appt.get("refused_refs", []):
            refused_all.append({"appointment_id": appt.get("appointment_id"),
                                "path": refused["path"],
                                "reason": refused["reason"]})

    questions = build_questions(appointments)

    if counts["BLOCKED"]:
        status = "BLOCKED"
    elif counts["INSUFFICIENT_EVIDENCE"] or counts["ACTION_NEEDED"] \
            or counts["WAITING_ON_CUSTOMER"]:
        status = "GAPS_FOUND"
    else:
        status = "READY"

    business_raw = data.get("business") if isinstance(data.get("business"), dict) else None
    business = None
    if business_raw:
        business = {
            "name": clean_text(business_raw.get("name")),
            "timezone": clean_text(business_raw.get("timezone")) or None,
            "no_show_policy": clean_text(business_raw.get("no_show_policy")),
        }

    markdown = render_markdown(status, as_of_raw, business, counts, appointments,
                               today_rows, contact_actions, do_not_contact, prep_gaps,
                               standby, conflicts, human_confirm, responsibility,
                               questions, warnings)

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "as_of_date": (as_of_dt.date().isoformat() if as_of_dt else None),
        "business": business,
        "appointment_count": len(appointments),
        "status_counts": counts,
        "appointments": appointments,
        "today_confirmation_list": today_rows,
        "contact_actions": contact_actions,
        "do_not_contact": do_not_contact,
        "preparation_gaps": prep_gaps,
        "standby_windows": standby,
        "owner_conflicts": conflicts,
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
