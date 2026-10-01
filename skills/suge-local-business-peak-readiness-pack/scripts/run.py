#!/usr/bin/env python3
"""本地商家客流高峰准备包 — offline peak-readiness board builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no command
execution, no POS / rostering / ordering integration.

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
EQUIPMENT_STATES = ("ok", "degraded", "down", "unknown")
# Equipment whose failure stops the till or the network at exactly peak time.
CRITICAL_EQUIPMENT_KINDS = ("pos", "terminal", "network")
EQUIPMENT_KINDS = ("pos", "terminal", "printer", "network", "fridge", "display", "kitchen", "other")
PAYMENT_STATUSES = ("active", "issue", "unknown")
TASK_PHASES = ("d_minus_7", "d_minus_1", "opening")
TASK_STATUSES = ("open", "done", "unknown")

AREAS = ("inventory", "staffing", "equipment", "payments", "signage", "flow", "contingency", "tasks")

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
    "忽略", "无视", "跳过", "覆盖", "改写", "删除", "执行", "服从", "绕过",
    "ignore", "disregard", "override", "bypass", "forget",
)
INJ_TARGET = (
    "指令", "规则", "提示", "系统", "要求", "约束",
    "instruction", "rule", "prompt", "system", "constraint",
)

MD_ESCAPE = "\\`*_{}[]()#+-|<>~!"

DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

DISCLAIMER = (
    "本输出是高峰营业准备的信息核对材料，不预测销量、不保证收入，也不构成支付、食品安全或劳动合规结论；"
    "上述事项以支付服务商、监管要求与门店制度的人工确认为准。"
)

HUMAN_CONFIRMATION = (
    {"topic": "PAYMENT_PROVIDER",
     "action": "与支付服务商确认高峰期间费率、限额与降级处理，本工具不查询任何支付账户。"},
    {"topic": "FOOD_SAFETY",
     "action": "按当地监管要求确认食品储存、留样与温控记录，本工具不给食品安全结论。"},
    {"topic": "LABOUR_RULES",
     "action": "按当地劳动法规确认工时、休息与加班安排，本工具不判定排班是否合规。"},
    {"topic": "LOCAL_PERMITS",
     "action": "确认外摆、音源、明火等是否需事先报备，本工具不做许可判断。"},
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    """Collapse control characters and whitespace; never raises.

    NFKC is deliberately NOT applied: it would rewrite Chinese full-width
    punctuation and degrade the sheet the user prints for the floor.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = "".join(
        ch for ch in text
        if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C"
    )
    return re.sub(r"\s+", " ", text).strip()


def esc(value):
    text = clean_text(value)
    return "".join("\\" + ch if ch in MD_ESCAPE else ch for ch in text)


def has_text(value):
    return isinstance(value, str) and value.strip() != ""


def quant(value, places=2):
    exponent = Decimal(1).scaleb(-places)
    out = Decimal(value).quantize(exponent, rounding=ROUND_HALF_UP)
    return abs(out) if out == 0 else out


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


def read_bool(value):
    return value if isinstance(value, bool) else None


def parse_dt(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not DT_RE.match(text):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def resolve_tz(name):
    if not has_text(name):
        return None
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover - 3.9+ always has zoneinfo
        return None
    try:
        return ZoneInfo(name.strip())
    except Exception:
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
    if injection_hit(value):
        return PLACEHOLDER
    return esc(value)


def find_credentials(node, path="", hits=None):
    """Collect credential-shaped key names and values, with a readable path.

    The path matches what the user can find in their own JSON: `api_key` at the
    root, `shots[0]/notes` further down. The matched value is never returned.
    """
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
    """Collect free-text leaves that look like a prompt injection.

    Paths use the same readable shape as `find_credentials`, e.g. `shots[0]/notes`.
    """
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


def window_contains(outer_start, outer_end, inner_start, inner_end):
    return outer_start <= inner_start and inner_end <= outer_end


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def reject(hits):
    return {
        "version": VERSION,
        "status": "REJECTED",
        "reason": "CREDENTIAL_DETECTED",
        "credential_findings": [{"path": h["path"], "reason": h["reason"]} for h in hits],
        "business": {}, "peak": {}, "peak_window": {},
        "readiness_by_area": [], "area_matrix": {name: [] for name in AREAS},
        "blockers": [], "single_points_of_failure": [],
        "inventory": [], "inventory_unknown_count": 0, "zero_stock_items": [],
        "below_reorder_items": [], "lead_time_risks": [],
        "staffing": {"staff": [], "shifts": [], "coverage_gaps": [],
                     "availability_issues": [], "hours": [], "unassigned_shifts": []},
        "equipment": [], "payments": [], "signage": [], "flow": {},
        "contingency": [], "contingency_gaps": [],
        "responsibility_by_window": [], "uncovered_windows": [],
        "action_plan": {"d_minus_7": [], "d_minus_1": [], "opening": []},
        "overdue_tasks": [], "human_confirmation_required": [],
        "clarification_questions": [], "injection_flagged": [],
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "markdown_summary": (
            "# 本地商家客流高峰准备包\n\n"
            "- 状态：**REJECTED**\n"
            "- 原因：输入疑似包含凭据（密钥 / 令牌 / 密码）。\n"
            "- 处理：已拒绝处理，**未回显**任何疑似凭据内容。\n\n"
            "> 请移除凭据字段后重新提交。本工具不需要也不需要保存任何登录凭据，"
            "也不会登录 POS、支付或排班系统。\n"
        ),
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# area readers
# --------------------------------------------------------------------------
def read_inventory(raw_list, ctx):
    items = []
    unknown_count = 0
    zero_stock = []
    below_reorder = []
    lead_time_risks = []
    for index, raw in enumerate(raw_list):
        path = "inventory[%d]" % index
        if not isinstance(raw, dict):
            items.append({"item_id": None, "path": path, "invalid": True,
                          "flags": ["INVALID_INVENTORY_RECORD"], "state": "UNKNOWN"})
            continue
        flags = []
        item_id = clean_text(raw.get("item_id")) if has_text(raw.get("item_id")) else None
        if item_id is None:
            flags.append("MISSING_ITEM_ID")

        on_hand = None
        on_hand_state = "unknown"
        if "on_hand" in raw and raw.get("on_hand") is not None:
            parsed = dec(raw.get("on_hand"))
            if parsed is None:
                flags.append("INVALID_ON_HAND")
                on_hand_state = "invalid"
            elif parsed < 0:
                flags.append("NEGATIVE_ON_HAND")
                on_hand_state = "invalid"
            else:
                on_hand = parsed
                on_hand_state = "known"
        else:
            flags.append("INVENTORY_UNKNOWN")

        reorder_point = dec(raw.get("reorder_point")) if "reorder_point" in raw else None
        if "reorder_point" in raw and raw.get("reorder_point") is not None and reorder_point is None:
            flags.append("INVALID_REORDER_POINT")

        lead_days = dec(raw.get("supplier_lead_days")) if "supplier_lead_days" in raw else None
        if "supplier_lead_days" in raw and raw.get("supplier_lead_days") is not None and lead_days is None:
            flags.append("INVALID_LEAD_DAYS")

        state = "UNKNOWN"
        if on_hand_state == "invalid":
            state = "INVALID"
        elif on_hand_state == "known":
            if on_hand == 0:
                state = "OUT_OF_STOCK"
                zero_stock.append(item_id)
            elif reorder_point is not None and on_hand <= reorder_point:
                state = "BELOW_REORDER"
                below_reorder.append(item_id)
            else:
                state = "OK"
        if state == "BELOW_REORDER":
            flags.append("BELOW_REORDER")
            if lead_days is not None and ctx["days_to_peak"] is not None and lead_days > ctx["days_to_peak"]:
                state = "LEAD_TIME_RISK"
                lead_time_risks.append(item_id)
                flags.append("LEAD_TIME_RISK")
        if state == "OUT_OF_STOCK":
            flags.append("ZERO_STOCK")
        if on_hand_state == "unknown":
            unknown_count += 1

        items.append({
            "item_id": item_id, "path": path, "invalid": bool(item_id is None or on_hand_state == "invalid"),
            "name": clean_text(raw.get("name")) if has_text(raw.get("name")) else None,
            "unit": clean_text(raw.get("unit")) if has_text(raw.get("unit")) else None,
            "on_hand": str(quant(on_hand, 2)) if on_hand is not None else None,
            "on_hand_state": on_hand_state,
            "reorder_point": str(quant(reorder_point, 2)) if reorder_point is not None else None,
            "supplier_lead_days": str(quant(lead_days, 2)) if lead_days is not None else None,
            "state": state, "flags": sorted(set(flags)),
            "note": raw.get("note"),
        })
    return items, unknown_count, zero_stock, below_reorder, lead_time_risks


def read_staffing(staffing_raw):
    """Return (staff, shifts, coverage_gaps, availability_issues, hours, unassigned, blockers)."""
    staff_raw = staffing_raw.get("staff") if isinstance(staffing_raw.get("staff"), list) else []
    shifts_raw = staffing_raw.get("shifts") if isinstance(staffing_raw.get("shifts"), list) else []
    coverage_raw = staffing_raw.get("required_coverage") if isinstance(staffing_raw.get("required_coverage"), list) else []

    staff = []
    availability = {}
    for index, raw in enumerate(staff_raw):
        if not isinstance(raw, dict):
            continue
        staff_id = clean_text(raw.get("staff_id")) if has_text(raw.get("staff_id")) else None
        flags = []
        if staff_id is None:
            flags.append("MISSING_STAFF_ID")
        available_from = parse_dt(raw.get("available_from"))
        available_to = parse_dt(raw.get("available_to"))
        if has_text(raw.get("available_from")) and available_from is None:
            flags.append("INVALID_AVAILABLE_FROM")
        if has_text(raw.get("available_to")) and available_to is None:
            flags.append("INVALID_AVAILABLE_TO")
        if available_from is None or available_to is None:
            flags.append("AVAILABILITY_UNKNOWN")
            if staff_id:
                availability[staff_id] = None
        else:
            if available_to <= available_from:
                flags.append("INVALID_AVAILABILITY_WINDOW")
                availability[staff_id] = None
            else:
                availability[staff_id] = (available_from, available_to)
        max_hours = dec(raw.get("max_hours")) if "max_hours" in raw else None
        if "max_hours" in raw and raw.get("max_hours") is not None and (max_hours is None or max_hours < 0):
            flags.append("INVALID_MAX_HOURS")
        staff.append({
            "staff_id": staff_id, "index": index,
            "role": clean_text(raw.get("role")) if has_text(raw.get("role")) else None,
            "available_from": clean_text(raw.get("available_from")) if has_text(raw.get("available_from")) else None,
            "available_to": clean_text(raw.get("available_to")) if has_text(raw.get("available_to")) else None,
            "max_hours": str(quant(max_hours, 2)) if max_hours is not None else None,
            "flags": sorted(set(flags)),
        })

    shifts = []
    availability_issues = []
    unassigned = []
    for index, raw in enumerate(shifts_raw):
        if not isinstance(raw, dict):
            continue
        shift_id = clean_text(raw.get("shift_id")) if has_text(raw.get("shift_id")) else None
        flags = []
        start = parse_dt(raw.get("starts_at"))
        end = parse_dt(raw.get("ends_at"))
        if start is None:
            flags.append("INVALID_STARTS_AT")
        if end is None:
            flags.append("INVALID_ENDS_AT")
        duration_hours = None
        if start is not None and end is not None:
            if end <= start:
                flags.append("INVALID_SHIFT_WINDOW")
            else:
                duration_hours = quant(Decimal((end - start).total_seconds()) / Decimal(3600), 2)
        assigned = []
        raw_assigned = raw.get("assigned_staff_ids")
        if isinstance(raw_assigned, list):
            for entry in raw_assigned:
                if has_text(entry):
                    assigned.append(clean_text(entry))
        if not assigned:
            flags.append("UNASSIGNED_SHIFT")
            unassigned.append(shift_id)
        for staff_id in assigned:
            if staff_id not in availability:
                flags.append("UNKNOWN_STAFF_ID")
                availability_issues.append({
                    "shift_id": shift_id, "staff_id": staff_id, "reason": "UNKNOWN_STAFF_ID"})
                continue
            window = availability[staff_id]
            if window is None:
                availability_issues.append({
                    "shift_id": shift_id, "staff_id": staff_id, "reason": "AVAILABILITY_UNKNOWN"})
                flags.append("AVAILABILITY_UNKNOWN")
                continue
            if start is None or end is None:
                continue
            if not window_contains(window[0], window[1], start, end):
                availability_issues.append({
                    "shift_id": shift_id, "staff_id": staff_id, "reason": "OUTSIDE_AVAILABILITY"})
                flags.append("STAFF_UNAVAILABLE")
        shifts.append({
            "shift_id": shift_id, "index": index,
            "role": clean_text(raw.get("role")) if has_text(raw.get("role")) else None,
            "starts_at": clean_text(raw.get("starts_at")) if has_text(raw.get("starts_at")) else None,
            "ends_at": clean_text(raw.get("ends_at")) if has_text(raw.get("ends_at")) else None,
            "duration_hours": str(duration_hours) if duration_hours is not None else None,
            "start_dt": start, "end_dt": end,
            "assigned_staff_ids": assigned,
            "flags": sorted(set(flags)),
        })

    # ---- per-staff hours ----
    hours = {}
    for shift in shifts:
        if shift["duration_hours"] is None:
            continue
        for staff_id in shift["assigned_staff_ids"]:
            hours[staff_id] = hours.get(staff_id, Decimal("0")) + Decimal(shift["duration_hours"])
    hours_rows = []
    for entry in staff:
        staff_id = entry["staff_id"]
        total = hours.get(staff_id)
        exceeded = False
        if total is not None and entry["max_hours"] is not None:
            exceeded = total > Decimal(entry["max_hours"])
            if exceeded:
                entry["flags"] = sorted(set(entry["flags"] + ["HOURS_EXCEEDED"]))
        hours_rows.append({
            "staff_id": staff_id,
            "scheduled_hours": str(quant(total, 2)) if total is not None else None,
            "max_hours": entry["max_hours"],
            "exceeded": exceeded,
        })

    # ---- required coverage ----
    coverage_gaps = []
    for index, raw in enumerate(coverage_raw):
        if not isinstance(raw, dict):
            continue
        role = clean_text(raw.get("role")) if has_text(raw.get("role")) else None
        needed = dec(raw.get("needed_count"))
        start = parse_dt(raw.get("starts_at"))
        end = parse_dt(raw.get("ends_at"))
        if role is None or needed is None:
            coverage_gaps.append({
                "index": index, "role": role, "needed_count": None,
                "assigned_count": 0, "reason": "COVERAGE_REQUIREMENT_INCOMPLETE"})
            continue
        needed_int = int(needed) if needed == needed.to_integral_value() else None
        if needed_int is None or needed_int < 0:
            coverage_gaps.append({
                "index": index, "role": role, "needed_count": None,
                "assigned_count": 0, "reason": "INVALID_NEEDED_COUNT"})
            continue
        if start is None or end is None or end <= start:
            coverage_gaps.append({
                "index": index, "role": role, "needed_count": needed_int,
                "assigned_count": 0, "reason": "INVALID_COVERAGE_WINDOW"})
            continue
        assigned_count = 0
        for shift in shifts:
            if shift["role"] != role or shift["start_dt"] is None or shift["end_dt"] is None:
                continue
            if window_contains(start, end, shift["start_dt"], shift["end_dt"]):
                assigned_count += len(shift["assigned_staff_ids"])
        if assigned_count < needed_int:
            coverage_gaps.append({
                "index": index, "role": role, "needed_count": needed_int,
                "assigned_count": assigned_count, "reason": "COVERAGE_SHORTFALL"})

    return staff, shifts, coverage_gaps, availability_issues, hours_rows, unassigned


def read_equipment(raw_list):
    equipment = []
    spof = []
    for index, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            continue
        flags = []
        asset_id = clean_text(raw.get("asset_id")) if has_text(raw.get("asset_id")) else None
        if asset_id is None:
            flags.append("MISSING_ASSET_ID")
        kind = clean_text(raw.get("kind")).lower() if has_text(raw.get("kind")) else None
        if kind is None:
            flags.append("MISSING_EQUIPMENT_KIND")
        elif kind not in EQUIPMENT_KINDS:
            flags.append("INVALID_EQUIPMENT_KIND")
        state = clean_text(raw.get("state")).lower() if has_text(raw.get("state")) else None
        if state is None:
            flags.append("MISSING_EQUIPMENT_STATE")
        elif state not in EQUIPMENT_STATES:
            flags.append("INVALID_EQUIPMENT_STATE")
        if state == "unknown":
            flags.append("EQUIPMENT_STATE_UNKNOWN")
        spares = read_bool(raw.get("spares_available"))
        if "spares_available" not in raw:
            flags.append("SPARES_UNKNOWN")
        critical = kind in CRITICAL_EQUIPMENT_KINDS
        if critical and state in ("down", "degraded") and spares is not True:
            flags.append("SINGLE_POINT_OF_FAILURE")
            spof.append({"asset_id": asset_id, "kind": kind, "state": state,
                         "reason": "CRITICAL_EQUIPMENT_NOT_OK_WITHOUT_SPARE"})
        if critical and state == "ok" and spares is not True:
            flags.append("SPARE_NOT_CONFIRMED")
        equipment.append({
            "asset_id": asset_id, "index": index,
            "name": clean_text(raw.get("name")) if has_text(raw.get("name")) else None,
            "kind": kind, "state": state, "critical": critical,
            "spares_available": spares, "flags": sorted(set(flags)),
            "invalid": bool(asset_id is None or kind is None or kind not in EQUIPMENT_KINDS
                            or state is None or state not in EQUIPMENT_STATES),
        })
    return equipment, spof


def read_payments(raw_list):
    payments = []
    blockers = []
    if not raw_list:
        blockers.append({"area": "payments", "flag": "PAYMENT_METHOD_MISSING",
                         "detail": "未登记任何收款方式。"})
        return payments, blockers
    for index, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            continue
        flags = []
        method = clean_text(raw.get("method")) if has_text(raw.get("method")) else None
        if method is None:
            flags.append("MISSING_PAYMENT_METHOD")
        status = clean_text(raw.get("status")).lower() if has_text(raw.get("status")) else None
        invalid_status = False
        if status is None:
            flags.append("MISSING_PAYMENT_STATUS")
        elif status not in PAYMENT_STATUSES:
            flags.append("INVALID_PAYMENT_STATUS")
            invalid_status = True
        fallback = clean_text(raw.get("fallback")) if has_text(raw.get("fallback")) else None
        if fallback is None:
            flags.append("FALLBACK_NOT_STATED")
        if status in ("issue", "unknown") and fallback is None:
            blockers.append({"area": "payments", "flag": "PAYMENT_FALLBACK_MISSING",
                             "detail": "%s 的备用收款方式未登记。" % (method or "(未命名方式)")})
        payments.append({
            "method": method, "index": index, "status": status,
            "fallback": fallback, "flags": sorted(set(flags)),
            "invalid": bool(method is None or invalid_status),
        })
    return payments, blockers


def read_signage(raw_list):
    signage = []
    for index, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            continue
        flags = []
        sign_id = clean_text(raw.get("sign_id")) if has_text(raw.get("sign_id")) else None
        if sign_id is None:
            flags.append("MISSING_SIGN_ID")
        purpose = clean_text(raw.get("purpose")) if has_text(raw.get("purpose")) else None
        if purpose is None:
            flags.append("MISSING_SIGN_PURPOSE")
        text_ready = read_bool(raw.get("text_ready"))
        if "text_ready" not in raw or text_ready is None:
            flags.append("SIGNAGE_TEXT_UNKNOWN")
        elif text_ready is False:
            flags.append("SIGNAGE_NOT_READY")
        signage.append({
            "sign_id": sign_id, "index": index, "purpose": purpose,
            "placement": clean_text(raw.get("placement")) if has_text(raw.get("placement")) else None,
            "text_ready": text_ready, "flags": sorted(set(flags)),
        })
    return signage


def read_flow(raw):
    flow = {}
    flags = []
    if not isinstance(raw, dict):
        raw = {}
    for key in ("entrance_plan", "queue_plan", "exit_plan"):
        value = clean_text(raw.get(key)) if has_text(raw.get(key)) else None
        flow[key] = value
        if value is None:
            flags.append("FLOW_PLAN_MISSING_" + key.upper())
    flow["flags"] = sorted(set(flags))
    return flow


def read_contingency(raw):
    required = []
    plans = []
    if isinstance(raw, dict):
        raw_required = raw.get("required_plans")
        raw_plans = raw.get("plans")
        if isinstance(raw_required, list):
            for entry in raw_required:
                if has_text(entry):
                    required.append(clean_text(entry))
                elif isinstance(entry, dict) and has_text(entry.get("risk_id")):
                    required.append(clean_text(entry.get("risk_id")))
        if isinstance(raw_plans, list):
            for index, entry in enumerate(raw_plans):
                if not isinstance(entry, dict):
                    continue
                risk_id = clean_text(entry.get("risk_id")) if has_text(entry.get("risk_id")) else None
                plan = clean_text(entry.get("plan")) if has_text(entry.get("plan")) else None
                owner = clean_text(entry.get("owner")) if has_text(entry.get("owner")) else None
                flags = []
                if risk_id is None:
                    flags.append("MISSING_RISK_ID")
                if plan is None:
                    flags.append("MISSING_PLAN")
                if owner is None:
                    flags.append("OWNER_MISSING")
                plans.append({"risk_id": risk_id, "index": index, "plan": plan,
                              "owner": owner, "flags": sorted(set(flags))})
    covered = {p["risk_id"] for p in plans if p["risk_id"] is not None}
    gaps = [risk for risk in required if risk not in covered]
    return required, plans, gaps


def read_tasks(raw_list, as_of):
    tasks = []
    overdue = []
    for index, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            continue
        flags = []
        task_id = clean_text(raw.get("task_id")) if has_text(raw.get("task_id")) else None
        if task_id is None:
            flags.append("MISSING_TASK_ID")
        title = clean_text(raw.get("title")) if has_text(raw.get("title")) else None
        if title is None:
            flags.append("MISSING_TASK_TITLE")
        phase = clean_text(raw.get("phase")).lower() if has_text(raw.get("phase")) else None
        if phase is None:
            flags.append("MISSING_TASK_PHASE")
        elif phase not in TASK_PHASES:
            flags.append("INVALID_TASK_PHASE")
        status = clean_text(raw.get("status")).lower() if has_text(raw.get("status")) else None
        if status is None:
            flags.append("MISSING_TASK_STATUS")
        elif status not in TASK_STATUSES:
            flags.append("INVALID_TASK_STATUS")
        owner = clean_text(raw.get("owner")) if has_text(raw.get("owner")) else None
        if owner is None:
            flags.append("OWNER_MISSING")
        due_raw = raw.get("due_at")
        due_at = parse_dt(due_raw)
        if has_text(due_raw) and due_at is None:
            flags.append("INVALID_DUE_AT")
        is_overdue = bool(due_at is not None and as_of is not None and due_at < as_of
                          and status != "done")
        if is_overdue:
            overdue.append(task_id)
        evidence = []
        raw_evidence = raw.get("evidence_refs")
        if isinstance(raw_evidence, list):
            for entry in raw_evidence:
                if has_text(entry):
                    evidence.append(clean_text(entry))
        tasks.append({
            "task_id": task_id, "index": index, "title": title, "phase": phase,
            "status": status, "owner": owner,
            "due_at": clean_text(due_raw) if has_text(due_raw) else None,
            "due_at_parsed": due_at, "is_overdue": is_overdue,
            "evidence_refs": evidence, "flags": sorted(set(flags)),
            "invalid": bool(task_id is None or title is None or phase is None
                            or phase not in TASK_PHASES or status is None or status not in TASK_STATUSES),
        })
    return tasks, overdue


# --------------------------------------------------------------------------
# main analysis
# --------------------------------------------------------------------------
def analyse(data):
    if not isinstance(data, dict):
        data = {}

    hits = find_credentials(data)
    if hits:
        return reject(hits)

    injection_flagged = find_injections(data)

    warnings = []
    as_of = parse_dt(data.get("as_of"))
    if as_of is None:
        warnings.append("AS_OF_MISSING_OR_INVALID")

    business_raw = data.get("business") if isinstance(data.get("business"), dict) else {}
    business = {
        "name": clean_text(business_raw.get("name")) if has_text(business_raw.get("name")) else None,
        "kind": clean_text(business_raw.get("kind")) if has_text(business_raw.get("kind")) else None,
        "timezone": clean_text(business_raw.get("timezone")) if has_text(business_raw.get("timezone")) else None,
    }
    tz = resolve_tz(business["timezone"])
    if business["timezone"] and tz is None:
        warnings.append("TIMEZONE_UNRESOLVED")
    business["timezone_resolved"] = tz is not None

    peak_raw = data.get("peak") if isinstance(data.get("peak"), dict) else {}
    peak_start = parse_dt(peak_raw.get("starts_at"))
    peak_end = parse_dt(peak_raw.get("ends_at"))
    peak_flags = []
    if has_text(peak_raw.get("starts_at")) and peak_start is None:
        peak_flags.append("INVALID_PEAK_START")
    if has_text(peak_raw.get("ends_at")) and peak_end is None:
        peak_flags.append("INVALID_PEAK_END")
    if peak_start is not None and peak_end is not None and peak_end <= peak_start:
        peak_flags.append("INVALID_PEAK_WINDOW")

    days_to_peak = None
    duration_hours = None
    crosses_midnight = False
    if peak_start is not None and as_of is not None:
        days_to_peak = quant(Decimal((peak_start - as_of).total_seconds()) / Decimal(86400), 2)
    if peak_start is not None and peak_end is not None and peak_end > peak_start:
        duration_hours = quant(Decimal((peak_end - peak_start).total_seconds()) / Decimal(3600), 2)
        if tz is not None:
            crosses_midnight = peak_end.astimezone(tz).date() > peak_start.astimezone(tz).date()
        elif peak_end.utcoffset() == peak_start.utcoffset():
            crosses_midnight = peak_end.replace(tzinfo=None).date() > peak_start.replace(tzinfo=None).date()

    ctx = {"days_to_peak": days_to_peak, "as_of": as_of}

    inventory, inventory_unknown, zero_stock, below_reorder, lead_time_risks = read_inventory(
        data.get("inventory") if isinstance(data.get("inventory"), list) else [], ctx)

    staffing_raw = data.get("staffing") if isinstance(data.get("staffing"), dict) else {}
    staff, shifts, coverage_gaps, availability_issues, hours_rows, unassigned = read_staffing(staffing_raw)

    equipment, spof = read_equipment(data.get("equipment") if isinstance(data.get("equipment"), list) else [])
    payments, payment_blockers = read_payments(data.get("payments") if isinstance(data.get("payments"), list) else [])
    signage = read_signage(data.get("signage") if isinstance(data.get("signage"), list) else [])
    flow = read_flow(data.get("flow"))
    contingency_required, contingency, contingency_gaps = read_contingency(data.get("contingency"))
    tasks, overdue_tasks = read_tasks(data.get("tasks") if isinstance(data.get("tasks"), list) else [], as_of)

    # ---- blockers ----
    blockers = []
    if peak_flags:
        for flag in peak_flags:
            blockers.append({"area": "peak", "flag": flag, "detail": "高峰时间窗无效。"})
    for item in inventory:
        if "ZERO_STOCK" in item["flags"]:
            blockers.append({"area": "inventory", "flag": "ZERO_STOCK",
                             "detail": "%s 现货为 0（用户明确填写 0）。" % (item["item_id"] or item["path"])})
        if "INVALID_ON_HAND" in item["flags"] or "NEGATIVE_ON_HAND" in item["flags"]:
            blockers.append({"area": "inventory", "flag": "INVALID_ON_HAND",
                             "detail": "%s 的现货数量不可解析或为负。" % (item["item_id"] or item["path"])})
        if "INVALID_REORDER_POINT" in item["flags"] or "INVALID_LEAD_DAYS" in item["flags"]:
            blockers.append({"area": "inventory", "flag": "INVALID_INVENTORY_PARAMETER",
                             "detail": "%s 的安全库存或到货周期不可解析。" % (item["item_id"] or item["path"])})
    for shift in shifts:
        if "INVALID_SHIFT_WINDOW" in shift["flags"] or "INVALID_STARTS_AT" in shift["flags"] or "INVALID_ENDS_AT" in shift["flags"]:
            blockers.append({"area": "staffing", "flag": "INVALID_SHIFT_WINDOW",
                             "detail": "%s 的班次时间无效。" % (shift["shift_id"] or "未编号班次")})
        if "UNASSIGNED_SHIFT" in shift["flags"]:
            blockers.append({"area": "staffing", "flag": "UNASSIGNED_SHIFT",
                             "detail": "%s 没有任何指派员工，高峰时段会无人值守。" % (
                                 shift["shift_id"] or "未编号班次")})
    for issue in availability_issues:
        if issue["reason"] == "OUTSIDE_AVAILABILITY":
            blockers.append({"area": "staffing", "flag": "STAFF_UNAVAILABLE",
                             "detail": "%s 安排了超出该员工自报可用时段的班次 %s。" % (
                                 issue["staff_id"], issue["shift_id"] or "未编号班次")})
        elif issue["reason"] == "UNKNOWN_STAFF_ID":
            blockers.append({"area": "staffing", "flag": "UNKNOWN_STAFF_ID",
                             "detail": "%s 指派了未登记的员工编号 %s。" % (
                                 issue["shift_id"] or "未编号班次", issue["staff_id"])})
    for row in hours_rows:
        if row["exceeded"]:
            blockers.append({"area": "staffing", "flag": "HOURS_EXCEEDED",
                             "detail": "%s 排班 %.2f 小时，超过自报上限 %s 小时。" % (
                                 row["staff_id"], Decimal(row["scheduled_hours"]), row["max_hours"])})
    for gap in coverage_gaps:
        if gap["reason"] == "COVERAGE_SHORTFALL":
            blockers.append({"area": "staffing", "flag": "COVERAGE_SHORTFALL",
                             "detail": "%s 需求 %d 人，实际覆盖 %d 人。" % (
                                 gap["role"], gap["needed_count"], gap["assigned_count"])})
    for bad in equipment:
        if bad["invalid"]:
            blockers.append({"area": "equipment", "flag": "INVALID_EQUIPMENT_RECORD",
                             "detail": "%s 的设备类型或状态无效。" % (bad["asset_id"] or "未编号设备")})
    for failure in spof:
        blockers.append({"area": "equipment", "flag": "SINGLE_POINT_OF_FAILURE",
                         "detail": "%s（%s）状态为 %s 且未确认备用。" % (
                             failure["asset_id"] or "未编号设备", failure["kind"], failure["state"])})
    blockers.extend(payment_blockers)
    for bad in payments:
        if bad["invalid"]:
            blockers.append({"area": "payments", "flag": "INVALID_PAYMENT_RECORD",
                             "detail": "%s 的收款方式的字段无效。" % (bad["method"] or "未命名方式")})
    for bad in tasks:
        if bad["invalid"]:
            blockers.append({"area": "tasks", "flag": "INVALID_TASK_RECORD",
                             "detail": "%s 的阶段或状态无效。" % (bad["task_id"] or "未编号任务")})

    blockers.sort(key=lambda b: (b["area"], b["flag"], b["detail"]))

    # ---- 时段责任表 ----
    ordered_shifts = sorted(
        [s for s in shifts if s["start_dt"] is not None and s["end_dt"] is not None],
        key=lambda s: (s["start_dt"], s["end_dt"], s["shift_id"] or "", s["index"]),
    )
    responsibility_by_window = [{
        "shift_id": s["shift_id"],
        "role": s["role"],
        "window_start": s["starts_at"],
        "window_end": s["ends_at"],
        "duration_hours": s["duration_hours"],
        "staff_ids": list(s["assigned_staff_ids"]),
        "flags": sorted(set(s["flags"])),
    } for s in ordered_shifts]

    uncovered = []
    for previous, current in zip(ordered_shifts, ordered_shifts[1:]):
        if current["start_dt"] > previous["end_dt"]:
            uncovered.append({
                "gap_start": previous["ends_at"],
                "gap_end": current["starts_at"],
                "hours": str(quant(Decimal((current["start_dt"] - previous["end_dt"]).total_seconds())
                                   / Decimal(3600), 2)),
            })

    # ---- per-area readiness ----
    def area_state(area):
        if any(b["area"] == area for b in blockers):
            return "BLOCKED"
        if area == "inventory":
            if inventory_unknown or below_reorder or lead_time_risks:
                return "GAPS"
            return "OK"
        if area == "staffing":
            if availability_issues or coverage_gaps or unassigned or uncovered:
                return "GAPS"
            return "OK"
        if area == "equipment":
            if any("SPARE_NOT_CONFIRMED" in e["flags"] or "EQUIPMENT_STATE_UNKNOWN" in e["flags"]
                   or "SPARES_UNKNOWN" in e["flags"] for e in equipment):
                return "GAPS"
            return "OK"
        if area == "payments":
            if any("FALLBACK_NOT_STATED" in p["flags"] for p in payments):
                return "GAPS"
            return "OK"
        if area == "signage":
            if any("SIGNAGE_TEXT_UNKNOWN" in s["flags"] or "SIGNAGE_NOT_READY" in s["flags"]
                   or "MISSING_SIGN_PURPOSE" in s["flags"] for s in signage):
                return "GAPS"
            return "OK"
        if area == "flow":
            return "GAPS" if flow.get("flags") else "OK"
        if area == "contingency":
            if contingency_gaps or any("OWNER_MISSING" in p["flags"] for p in contingency):
                return "GAPS"
            return "OK"
        if area == "tasks":
            if overdue_tasks or any("OWNER_MISSING" in t["flags"] for t in tasks) \
                    or any(t["status"] == "unknown" for t in tasks):
                return "GAPS"
            return "OK"
        return "OK"

    readiness_by_area = [{"area": area, "state": area_state(area),
                          "records": len(_area_records(area, inventory, staff, shifts, equipment,
                                                       payments, signage, contingency, tasks))}
                         for area in AREAS]

    # ---- action plan (fixed derivation rules) ----
    plan = {"d_minus_7": [], "d_minus_1": [], "opening": []}

    def add_action(phase, area, action):
        plan[phase].append({"area": area, "action": action})

    if zero_stock:
        add_action("d_minus_1", "inventory", "现货为 0 的品项已确认补货到店：" + "、".join(str(x) for x in zero_stock))
    if below_reorder or lead_time_risks:
        add_action("d_minus_7", "inventory", "低于安全库存或到货周期不足的品项已下单或改用替代品："
                   + "、".join(str(x) for x in sorted(set(below_reorder + lead_time_risks))))
    if inventory_unknown:
        add_action("d_minus_7", "inventory", "未盘点的品项已完成实盘登记（未盘点按未知处理，未计入 0）。")
    if uncovered:
        add_action("d_minus_1", "staffing", "班次之间的空档已补人：%s" %
                   "、".join("%s → %s" % (u["gap_start"], u["gap_end"]) for u in uncovered))
    if coverage_gaps:
        add_action("d_minus_1", "staffing", "未达人数的岗位已补齐或已确认缩减服务范围。")
    if availability_issues:
        add_action("d_minus_1", "staffing", "排班与员工可用时段的冲突已由门店负责人裁决。")
    if spof:
        add_action("d_minus_1", "equipment", "关键设备（收银 / 终端 / 网络）已确认备用方案并现场可用。")
    if any("EQUIPMENT_STATE_UNKNOWN" in e["flags"] for e in equipment):
        add_action("d_minus_1", "equipment", "设备状态已现场复测确认。")
    if payment_blockers or any(p["status"] in ("issue", "unknown") for p in payments):
        add_action("d_minus_1", "payments", "备用收款方式已现场试刷一次并确认可用。")
    if any("SIGNAGE_NOT_READY" in s["flags"] or "SIGNAGE_TEXT_UNKNOWN" in s["flags"] for s in signage):
        add_action("d_minus_1", "signage", "标识文案已定稿并打印到店。")
    if contingency_gaps:
        add_action("d_minus_7", "contingency", "缺失的应急预案已补齐：" + "、".join(contingency_gaps))
    if flow.get("flags"):
        add_action("d_minus_7", "flow", "入口 / 排队 / 出口动线方案已画出现场布置并张贴。")
    for task in tasks:
        if task["status"] == "done" or task["phase"] not in plan:
            continue
        add_action(task["phase"], "tasks", "%s（%s）" % (task["title"], task["owner"] or "未指派"))
    add_action("opening", "all", "开门前逐项核对本作战单，并把当日负责人写在收银台可见处。")

    # ---- state ----
    everything_empty = not inventory and not shifts and not equipment and not tasks
    if blockers:
        status = "BLOCKED"
    elif as_of is None or peak_start is None or everything_empty:
        status = "INPUT_INCOMPLETE"
    elif any(row["state"] != "OK" for row in readiness_by_area) or overdue_tasks:
        status = "GAPS_FOUND"
    else:
        status = "READY"

    # ---- clarification questions ----
    questions = []

    def ask(topic, text):
        questions.append({"id": "Q-%02d" % (len(questions) + 1), "topic": topic, "question": text})

    if as_of is None:
        ask("AS_OF", "请提供带时区偏移的 as_of（例如 2026-09-27T20:00:00+08:00），否则无法计算距高峰的天数。")
    if peak_start is None:
        ask("PEAK_START", "请提供带时区偏移的高峰开始时间（starts_at），这是本作战单的时间基准。")
    if peak_flags:
        ask("PEAK_WINDOW", "高峰起止时间无效或结束早于开始，请确认跨夜高峰的两个时间戳及其时区偏移。")
    if everything_empty:
        ask("SCOPE", "没有登记库存、班次、设备或任务，请确认本次是否确实不需要准备。")
    if zero_stock:
        ask("ZERO_STOCK", "以下品项现货为 0，请确认补货或停售：" + "、".join(str(x) for x in zero_stock))
    if inventory_unknown:
        ask("INVENTORY_UNKNOWN", "有 %d 个品项没有盘点数量，请补齐；本工具不会把未知当作 0。" % inventory_unknown)
    if lead_time_risks:
        ask("LEAD_TIME", "以下品项即使现在下单也可能赶不上高峰，请确认替代方案："
            + "、".join(str(x) for x in lead_time_risks))
    if unassigned:
        ask("UNASSIGNED_SHIFT", "以下班次没有安排任何人，请指派：" + "、".join(str(x) for x in unassigned))
    if any(issue["reason"] == "AVAILABILITY_UNKNOWN" for issue in availability_issues):
        ask("AVAILABILITY", "部分员工的可用时段缺失，无法确认排班是否超出可上班时间，请补齐。")
    if uncovered:
        ask("UNCOVERED_WINDOW", "班次之间存在无人值守的空档，请确认是否接受或补人。")
    if spof:
        ask("SINGLE_POINT_OF_FAILURE", "以下关键设备状态不佳且没有备用，请确认备用方案："
            + "、".join(str(s["asset_id"]) for s in spof))
    if payment_blockers:
        ask("PAYMENT_FALLBACK", "收款方式异常或缺备用，请确认高峰期间的备用收款与人工兜底流程。")
    if "TIMEZONE_UNRESOLVED" in warnings:
        ask("TIMEZONE", "门店时区无法解析，请改用带明确偏移的时间戳，或提供有效的 IANA 时区名。")
    if flow.get("flags"):
        ask("FLOW", "入口 / 排队 / 出口动线方案不完整，请补齐后再印刷现场标识。")
    if contingency_gaps:
        ask("CONTINGENCY", "以下风险没有对应预案，请补齐责任人与动作："
            + "、".join(str(x) for x in contingency_gaps))
    if overdue_tasks:
        ask("OVERDUE_TASK", "以下任务已过截止时间，请确认是已完成未更新还是需要升级："
            + "、".join(str(x) for x in overdue_tasks))
    if any("OWNER_MISSING" in t["flags"] for t in tasks):
        ask("TASK_OWNER", "部分任务没有责任人，请指派到具体成员。")

    markdown = render_markdown(
        status=status, business=business, peak_raw=peak_raw, as_of=data.get("as_of"),
        days_to_peak=days_to_peak, duration_hours=duration_hours,
        crosses_midnight=crosses_midnight, readiness_by_area=readiness_by_area,
        blockers=blockers, inventory=inventory, inventory_unknown=inventory_unknown,
        zero_stock=zero_stock, below_reorder=below_reorder, lead_time_risks=lead_time_risks,
        responsibility=responsibility_by_window, uncovered=uncovered,
        equipment=equipment, spof=spof, payments=payments, signage=signage,
        flow=flow, contingency=contingency, contingency_gaps=contingency_gaps,
        plan=plan, human=HUMAN_CONFIRMATION, questions=questions, warnings=warnings,
        overdue_tasks=overdue_tasks,
    )

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(data.get("as_of")) if has_text(data.get("as_of")) else None,
        "business": business,
        "peak": {
            "label": clean_text(peak_raw.get("label")) if has_text(peak_raw.get("label")) else None,
            "starts_at": clean_text(peak_raw.get("starts_at")) if has_text(peak_raw.get("starts_at")) else None,
            "ends_at": clean_text(peak_raw.get("ends_at")) if has_text(peak_raw.get("ends_at")) else None,
            "review_flags": sorted(set(peak_flags)),
        },
        "peak_window": {
            "days_to_peak": str(days_to_peak) if days_to_peak is not None else None,
            "duration_hours": str(duration_hours) if duration_hours is not None else None,
            "crosses_midnight": crosses_midnight,
            "peak_in_progress_or_past": bool(days_to_peak is not None and days_to_peak < 0),
        },
        "readiness_by_area": readiness_by_area,
        "area_matrix": {
            "inventory": [_inventory_view(i) for i in inventory],
            "staffing": [_staffing_view(s) for s in staff] + [_shift_view(s) for s in shifts],
            "equipment": [_equipment_view(e) for e in equipment],
            "payments": [_payment_view(p) for p in payments],
            "signage": [_signage_view(s) for s in signage],
            "flow": [{"key": key, "plan": flow.get(key)}
                     for key in ("entrance_plan", "queue_plan", "exit_plan")],
            "contingency": [_contingency_view(c) for c in contingency],
            "tasks": [_task_view(t) for t in tasks],
        },
        "blockers": blockers,
        "single_points_of_failure": spof,
        "inventory": [_inventory_view(i) for i in inventory],
        "inventory_unknown_count": inventory_unknown,
        "zero_stock_items": [x for x in zero_stock if x is not None],
        "below_reorder_items": [x for x in below_reorder if x is not None],
        "lead_time_risks": [x for x in lead_time_risks if x is not None],
        "staffing": {
            "staff": [_staff_view(s) for s in staff],
            "shifts": [_shift_view(s) for s in shifts],
            "coverage_gaps": coverage_gaps,
            "availability_issues": availability_issues,
            "hours": hours_rows,
            "unassigned_shifts": [x if x is not None else "(未编号班次)" for x in unassigned],
        },
        "equipment": [_equipment_view(e) for e in equipment],
        "payments": [_payment_view(p) for p in payments],
        "signage": [_signage_view(s) for s in signage],
        "flow": {k: flow.get(k) for k in ("entrance_plan", "queue_plan", "exit_plan")},
        "contingency": {
            "required_plans": contingency_required,
            "plans": [_contingency_view(c) for c in contingency],
            "gaps": contingency_gaps,
        },
        "contingency_gaps": contingency_gaps,
        "responsibility_by_window": responsibility_by_window,
        "uncovered_windows": uncovered,
        "action_plan": plan,
        "overdue_tasks": [x for x in overdue_tasks if x is not None],
        "human_confirmation_required": [dict(item) for item in HUMAN_CONFIRMATION],
        "clarification_questions": questions,
        "injection_flagged": injection_flagged,
        "input_warnings": warnings,
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
    }


def _area_records(area, inventory, staff, shifts, equipment, payments, signage, contingency, tasks):
    return {
        "inventory": inventory, "staffing": staff + shifts, "equipment": equipment,
        "payments": payments, "signage": signage, "flow": [],
        "contingency": contingency, "tasks": tasks,
    }[area]


def _inventory_view(item):
    return {
        "item_id": item.get("item_id"), "path": item.get("path"), "name": item.get("name"),
        "unit": item.get("unit"), "on_hand": item.get("on_hand"),
        "reorder_point": item.get("reorder_point"),
        "supplier_lead_days": item.get("supplier_lead_days"),
        "state": item.get("state"), "review_flags": item.get("flags", []),
    }


def _staff_view(entry):
    return {"staff_id": entry.get("staff_id"), "role": entry.get("role"),
            "available_from": entry.get("available_from"), "available_to": entry.get("available_to"),
            "max_hours": entry.get("max_hours"), "review_flags": entry.get("flags", [])}


def _staffing_view(entry):
    return _staff_view(entry)


def _shift_view(entry):
    return {"shift_id": entry.get("shift_id"), "role": entry.get("role"),
            "starts_at": entry.get("starts_at"), "ends_at": entry.get("ends_at"),
            "duration_hours": entry.get("duration_hours"),
            "assigned_staff_ids": entry.get("assigned_staff_ids", []),
            "review_flags": entry.get("flags", [])}


def _equipment_view(entry):
    return {"asset_id": entry.get("asset_id"), "name": entry.get("name"), "kind": entry.get("kind"),
            "state": entry.get("state"), "critical": entry.get("critical"),
            "spares_available": entry.get("spares_available"), "review_flags": entry.get("flags", [])}


def _payment_view(entry):
    return {"method": entry.get("method"), "status": entry.get("status"),
            "fallback": entry.get("fallback"), "review_flags": entry.get("flags", [])}


def _signage_view(entry):
    return {"sign_id": entry.get("sign_id"), "purpose": entry.get("purpose"),
            "placement": entry.get("placement"), "text_ready": entry.get("text_ready"),
            "review_flags": entry.get("flags", [])}


def _contingency_view(entry):
    return {"risk_id": entry.get("risk_id"), "plan": entry.get("plan"),
            "owner": entry.get("owner"), "review_flags": entry.get("flags", [])}


def _task_view(entry):
    return {"task_id": entry.get("task_id"), "title": entry.get("title"), "phase": entry.get("phase"),
            "status": entry.get("status"), "owner": entry.get("owner"), "due_at": entry.get("due_at"),
            "is_overdue": entry.get("is_overdue"), "evidence_refs": entry.get("evidence_refs", []),
            "review_flags": entry.get("flags", [])}


AREA_LABELS = (
    ("inventory", "库存"), ("staffing", "人员"), ("equipment", "设备"),
    ("payments", "收款"), ("signage", "标识"), ("flow", "动线"),
    ("contingency", "应急"), ("tasks", "任务"),
)


def render_markdown(status, business, peak_raw, as_of, days_to_peak, duration_hours,
                    crosses_midnight, readiness_by_area, blockers, inventory,
                    inventory_unknown, zero_stock, below_reorder, lead_time_risks,
                    responsibility, uncovered, equipment, spof, payments, signage,
                    flow, contingency, contingency_gaps, plan, human, questions,
                    warnings, overdue_tasks):
    lines = []
    title = business["name"] or "门店"
    lines.append("# 客流高峰准备包 — %s" % esc(title))
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 高峰：%s → %s" % (
        esc(peak_raw.get("starts_at")) if has_text(peak_raw.get("starts_at")) else "未提供",
        esc(peak_raw.get("ends_at")) if has_text(peak_raw.get("ends_at")) else "未提供",
    ))
    lines.append("- 距高峰：%s 天" % (days_to_peak if days_to_peak is not None else "无法计算"))
    lines.append("- 时长：%s 小时" % (duration_hours if duration_hours is not None else "无法计算"))
    lines.append("- 跨夜：%s" % ("是" if crosses_midnight else "否"))
    lines.append("")

    lines.append("## 分区准备度")
    lines.append("")
    lines.append("| 分区 | 状态 | 记录数 |")
    lines.append("|---|---|---:|")
    for row in readiness_by_area:
        label = dict(AREA_LABELS)[row["area"]]
        lines.append("| %s | %s | %d |" % (esc(label), row["state"], row["records"]))
    lines.append("")

    lines.append("## 阻塞项")
    lines.append("")
    if blockers:
        lines.append("| 分区 | 代码 | 说明 |")
        lines.append("|---|---|---|")
        for row in blockers:
            lines.append("| %s | %s | %s |" % (esc(row["area"]), esc(row["flag"]), esc(row["detail"])))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 库存")
    lines.append("")
    if inventory:
        lines.append("| 品项 | 现货 | 单位 | 安全库存 | 状态 |")
        lines.append("|---|---|---|---|---|")
        for item in inventory:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(item.get("item_id")) if item.get("item_id") else "未提供",
                item.get("on_hand") if item.get("on_hand") is not None else "未知",
                esc(item.get("unit")) if item.get("unit") else "未提供",
                item.get("reorder_point") if item.get("reorder_point") is not None else "未提供",
                esc(item.get("state")),
            ))
    else:
        lines.append("- 无库存记录")
    lines.append("")
    lines.append("- 未盘点品项：%d（未盘点按未知处理，未计入 0）" % inventory_unknown)
    lines.append("- 现货为 0：%s" % ("、".join(esc(str(x)) for x in zero_stock) if zero_stock else "无"))
    lines.append("- 低于安全库存：%s" % ("、".join(esc(str(x)) for x in below_reorder) if below_reorder else "无"))
    lines.append("- 到货周期不足：%s" % ("、".join(esc(str(x)) for x in lead_time_risks) if lead_time_risks else "无"))
    lines.append("")

    lines.append("## 时段责任表")
    lines.append("")
    if responsibility:
        lines.append("| 班次 | 岗位 | 时段 | 时长(小时) | 人员 |")
        lines.append("|---|---|---|---|---|")
        for row in responsibility:
            lines.append("| %s | %s | %s → %s | %s | %s |" % (
                esc(row["shift_id"]) if row["shift_id"] else "未编号",
                esc(row["role"]) if row["role"] else "未提供",
                esc(row["window_start"]) if row["window_start"] else "未提供",
                esc(row["window_end"]) if row["window_end"] else "未提供",
                row["duration_hours"] if row["duration_hours"] is not None else "未知",
                esc("、".join(row["staff_ids"])) if row["staff_ids"] else "未指派",
            ))
    else:
        lines.append("- 无排班记录")
    lines.append("")
    if uncovered:
        lines.append("**无人值守空档**")
        lines.append("")
        for gap in uncovered:
            lines.append("- %s → %s（%s 小时）" % (esc(gap["gap_start"]), esc(gap["gap_end"]), gap["hours"]))
        lines.append("")

    lines.append("## 设备与备用")
    lines.append("")
    if equipment:
        lines.append("| 设备 | 类型 | 状态 | 关键 | 备用 |")
        lines.append("|---|---|---|---|---|")
        for entry in equipment:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(entry.get("asset_id")) if entry.get("asset_id") else "未提供",
                esc(entry.get("kind")) if entry.get("kind") else "未提供",
                esc(entry.get("state")) if entry.get("state") else "未提供",
                "是" if entry.get("critical") else "否",
                "已确认" if entry.get("spares_available") is True
                else ("未确认" if entry.get("spares_available") is False else "未知"),
            ))
    else:
        lines.append("- 无设备记录")
    lines.append("")
    if spof:
        lines.append("**单点故障**")
        lines.append("")
        for failure in spof:
            lines.append("- %s（%s）状态 %s 且未确认备用" % (
                esc(failure["asset_id"]), esc(failure["kind"]), esc(failure["state"])))
        lines.append("")

    lines.append("## 收款与标识")
    lines.append("")
    if payments:
        lines.append("| 收款方式 | 状态 | 备用 |")
        lines.append("|---|---|---|")
        for entry in payments:
            lines.append("| %s | %s | %s |" % (
                esc(entry.get("method")) if entry.get("method") else "未提供",
                esc(entry.get("status")) if entry.get("status") else "未提供",
                esc(entry.get("fallback")) if entry.get("fallback") else "未登记",
            ))
    else:
        lines.append("- 无收款方式记录")
    lines.append("")
    if signage:
        lines.append("| 标识 | 用途 | 文案就绪 |")
        lines.append("|---|---|---|")
        for entry in signage:
            lines.append("| %s | %s | %s |" % (
                esc(entry.get("sign_id")) if entry.get("sign_id") else "未提供",
                esc(entry.get("purpose")) if entry.get("purpose") else "未提供",
                "就绪" if entry.get("text_ready") is True
                else ("未就绪" if entry.get("text_ready") is False else "未知"),
            ))
    lines.append("")

    lines.append("## 动线与应急")
    lines.append("")
    for key, label in (("entrance_plan", "入口"), ("queue_plan", "排队"), ("exit_plan", "出口")):
        lines.append("- %s：%s" % (label, hidden(flow.get(key)) if flow.get(key) else "未提供"))
    lines.append("")
    if contingency:
        lines.append("| 风险 | 预案 | 责任人 |")
        lines.append("|---|---|---|")
        for entry in contingency:
            lines.append("| %s | %s | %s |" % (
                esc(entry.get("risk_id")) if entry.get("risk_id") else "未提供",
                hidden(entry.get("plan")) if entry.get("plan") else "未提供",
                esc(entry.get("owner")) if entry.get("owner") else "未指派",
            ))
    if contingency_gaps:
        lines.append("")
        lines.append("**缺少预案**：" + "、".join(esc(str(x)) for x in contingency_gaps))
    lines.append("")

    lines.append("## 行动清单")
    lines.append("")
    for phase, label in (("d_minus_7", "D-7（一周前）"), ("d_minus_1", "D-1（前一天）"), ("opening", "开门前")):
        lines.append("### %s" % label)
        lines.append("")
        if plan[phase]:
            for step_index, step in enumerate(plan[phase], start=1):
                lines.append("%d. [ ] %s" % (step_index, hidden(step["action"])))
        else:
            lines.append("- 无")
        lines.append("")

    lines.append("## 必须人工确认（本工具不下结论）")
    lines.append("")
    for item in human:
        lines.append("- **%s**：%s" % (item["topic"], esc(item["action"])))
    lines.append("")

    if overdue_tasks:
        lines.append("## 逾期任务")
        lines.append("")
        lines.append("- %s" % "、".join(esc(str(x)) for x in overdue_tasks))
        lines.append("")

    lines.append("## 待确认问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- `%s` %s" % (question["id"], esc(question["question"])))
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
