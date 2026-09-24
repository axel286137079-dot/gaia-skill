#!/usr/bin/env python3
"""门店交接班准备包 — offline shift-handover board builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file, writes one
JSON document to stdout. No network, no filesystem writes, no command execution.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = "1.0.1"

# --------------------------------------------------------------------------
# fixed vocabulary (documented in references/guide.md)
# --------------------------------------------------------------------------
VALID_ITEM_STATUS = ("open", "in_progress", "done", "unknown")
VALID_ITEM_CATEGORY = ("cash", "inventory", "equipment", "customer", "other")
VALID_EQUIPMENT_STATE = ("down", "degraded", "ok", "unknown")

REQUIRED_ITEM_FIELDS = ("summary", "category", "status")
RECOMMENDED_ITEM_FIELDS = ("owner", "due_at")

BOARD_BUCKETS = ("immediate", "next_shift", "confirm_with_owner", "record_only")

PLACEHOLDER = "已隐藏疑似提示注入文本"

CRED_KEY = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|auth[_-]?token)",
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
# the SAME sentence, so ordinary notes are not mislabelled.
INJ_ACTION = (
    "忽略", "无视", "跳过", "覆盖", "改写", "删除", "执行", "服从", "绕过",
    "ignore", "disregard", "override", "bypass", "forget",
)
INJ_TARGET = (
    "指令", "规则", "提示", "系统", "要求", "约束",
    "instruction", "rule", "prompt", "system", "constraint",
)

# Markdown metacharacters that could forge a heading, list, table, link or code span.
MD_ESCAPE = "\\`*_{}[]()#+-|<>~!"

DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

DISCLAIMER = (
    "本输出是交接班信息核对材料，不是责任判定、工资结算或合规结论；"
    "现金与库存差异的最终认定以门店制度、双方当面清点和票据为准。"
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    """Collapse control characters and whitespace; never raises.

    Compatibility normalisation (NFKC) is deliberately NOT applied here: it would
    rewrite Chinese full-width punctuation (，（）；) into ASCII and degrade the
    handover sheet the user prints. Only control characters are removed.
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
    """Escape Markdown structure characters so untrusted text cannot forge layout."""
    text = clean_text(value)
    return "".join("\\" + ch if ch in MD_ESCAPE else ch for ch in text)


def has_text(value):
    return isinstance(value, str) and value.strip() != ""


def quant(value, places=2):
    """Decimal quantize that never renders a negative zero."""
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
    """Render a free-text value for Markdown; injection hits become a placeholder."""
    if injection_hit(value):
        return PLACEHOLDER
    return esc(value)


# --------------------------------------------------------------------------
# credential gate (reject, never echo)
# --------------------------------------------------------------------------
def find_credentials(node, path="/", hits=None):
    if hits is None:
        hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = path + str(key)
            if CRED_KEY.search(str(key)):
                hits.append({"path": here, "reason": "CREDENTIAL_FIELD_NAME"})
                continue
            find_credentials(value, here + "/", hits)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            find_credentials(value, path + "[%d]/" % index, hits)
    elif isinstance(node, str):
        for pattern in CRED_VALUE:
            if pattern.search(node):
                hits.append({"path": path.rstrip("/") or "/", "reason": "CREDENTIAL_VALUE_SHAPE"})
                break
    return hits


def reject(hits):
    return {
        "version": VERSION,
        "status": "REJECTED",
        "reason": "CREDENTIAL_DETECTED",
        "credential_findings": [{"path": h["path"], "reason": h["reason"]} for h in hits],
        "board": {name: [] for name in BOARD_BUCKETS},
        "board_counts": {name: 0 for name in BOARD_BUCKETS},
        "items": [],
        "unfinished_items": [],
        "overdue_items": [],
        "duplicate_item_ids": [],
        "conflicts": [],
        "invalid_items": [],
        "missing_fields": [],
        "evidence_gaps": [],
        "totals_by_currency": {},
        "unknown_amount_count": 0,
        "unknown_quantity_count": 0,
        "equipment": [],
        "equipment_downtime": [],
        "opening_checklist": [],
        "clarification_questions": [],
        "injection_flagged": [],
        "markdown_summary": (
            "# 交接班准备包\n\n"
            "- 状态：**REJECTED**\n"
            "- 原因：输入疑似包含凭据（密钥 / 令牌 / 密码）。\n"
            "- 处理：已拒绝处理，**未回显**任何疑似凭据内容。\n\n"
            "> 请移除凭据字段后重新提交。本工具不需要也不需要保存任何登录凭据。\n"
        ),
        "disclaimer": DISCLAIMER,
        "input_warnings": ["CREDENTIAL_DETECTED"],
    }


# --------------------------------------------------------------------------
# item level work
# --------------------------------------------------------------------------
def first_present(mapping, key):
    if isinstance(mapping, dict) and key in mapping:
        return mapping[key]
    return None


def read_amount(raw):
    """Return (known, currency, decimal | None, flags)."""
    flags = []
    if not isinstance(raw, dict):
        return False, None, None, ["AMOUNT_UNKNOWN"]
    value = dec(raw.get("value"))
    currency = clean_text(raw.get("currency")).upper() if has_text(raw.get("currency")) else None
    if raw.get("value") is None:
        flags.append("AMOUNT_UNKNOWN")
        return False, currency, None, flags
    if value is None:
        flags.append("INVALID_AMOUNT")
        return False, currency, None, flags
    if value < 0:
        flags.append("NEGATIVE_AMOUNT")
    if not currency:
        flags.append("AMOUNT_CURRENCY_UNKNOWN")
        return False, None, value, flags
    return True, currency, value, flags


def read_quantity(raw):
    flags = []
    if not isinstance(raw, dict):
        return False, None, None, ["QUANTITY_UNKNOWN"]
    value = first_present(raw, "value")
    unit = clean_text(raw.get("unit")) if has_text(raw.get("unit")) else None
    if value is None:
        flags.append("QUANTITY_UNKNOWN")
        return False, unit, None, flags
    parsed = dec(value)
    if parsed is None:
        flags.append("INVALID_QUANTITY")
        return False, unit, None, flags
    if parsed < 0:
        flags.append("NEGATIVE_QUANTITY")
    if parsed == parsed.to_integral_value():
        parsed = parsed.to_integral_value()
    else:
        flags.append("FRACTIONAL_QUANTITY")
    return True, unit, parsed, flags


def read_refs(raw):
    refs = []
    if not isinstance(raw, list):
        return refs
    for entry in raw:
        if has_text(entry):
            refs.append(clean_text(entry))
    return refs


def normalise_item(raw, index, ctx):
    """Turn one raw record into a fully resolved, deterministic item record."""
    flags = []
    path = "items[%d]" % index
    if not isinstance(raw, dict):
        return {
            "item_id": None,
            "index": index,
            "path": path,
            "status": "invalid",
            "category": None,
            "invalid": True,
            "flags": ["INVALID_ITEM_RECORD"],
            "route": "confirm_with_owner",
            "summary": "", "owner": None, "due_at": None,
            "evidence_refs": [], "amount": None, "quantity": None,
            "is_overdue": False, "asset_id": None, "asset_down": False,
            "conflict": False, "injection": False,
        }

    raw_id = first_present(raw, "item_id")
    item_id = clean_text(raw_id) if has_text(raw_id) else None
    if item_id is None:
        flags.append("MISSING_ITEM_ID")

    status = clean_text(raw.get("status")).lower() if has_text(raw.get("status")) else None
    if status is None:
        flags.append("MISSING_STATUS")
    elif status not in VALID_ITEM_STATUS:
        flags.append("INVALID_STATUS")

    category = clean_text(raw.get("category")).lower() if has_text(raw.get("category")) else None
    if category is None:
        flags.append("MISSING_CATEGORY")
    elif category not in VALID_ITEM_CATEGORY:
        flags.append("INVALID_CATEGORY")

    summary = clean_text(raw.get("summary")) if has_text(raw.get("summary")) else ""
    if not summary:
        flags.append("MISSING_SUMMARY")

    owner = clean_text(raw.get("owner")) if has_text(raw.get("owner")) else None
    if owner is None:
        flags.append("OWNER_MISSING")

    due_raw = first_present(raw, "due_at")
    due_at = parse_dt(due_raw)
    if has_text(due_raw) and due_at is None:
        flags.append("INVALID_DUE_AT")

    is_overdue = bool(due_at is not None and ctx["as_of"] is not None and due_at < ctx["as_of"]
                      and status not in ("done",))

    asset_id = clean_text(raw.get("asset_id")) if has_text(raw.get("asset_id")) else None
    asset_down = bool(asset_id and asset_id in ctx["down_assets"])
    if category == "equipment" and asset_id and not asset_down and asset_id not in ctx["known_assets"]:
        flags.append("UNKNOWN_ASSET_REF")

    if "amount" in raw:
        amount_known, amount_currency, amount_value, amount_flags = read_amount(raw.get("amount"))
    else:
        # No money is involved in this item at all; that is not "unknown amount".
        amount_known, amount_currency, amount_value, amount_flags = False, None, None, []
    flags.extend(amount_flags)

    if "quantity" in raw:
        qty_known, qty_unit, qty_value, qty_flags = read_quantity(raw.get("quantity"))
    else:
        qty_known, qty_unit, qty_value, qty_flags = False, None, None, []
    flags.extend(qty_flags)

    refs = read_refs(first_present(raw, "evidence_refs"))

    notes = raw.get("notes")
    injected = injection_hit(notes)

    invalid = bool(
        status is None or status not in VALID_ITEM_STATUS
        or category is None or category not in VALID_ITEM_CATEGORY
        or not summary or item_id is None
    )

    return {
        "item_id": item_id,
        "index": index,
        "path": path,
        "status": status,
        "category": category,
        "invalid": invalid,
        "flags": flags,
        "route": None,
        "summary": summary,
        "owner": owner,
        "due_at": clean_text(due_raw) if has_text(due_raw) else None,
        "due_at_parsed": due_at,
        "evidence_refs": refs,
        "amount_known": amount_known,
        "amount_currency": amount_currency,
        "amount_value": amount_value,
        "quantity_known": qty_known,
        "quantity_unit": qty_unit,
        "quantity_value": qty_value,
        "is_overdue": is_overdue,
        "asset_id": asset_id,
        "asset_down": asset_down,
        "conflict": False,
        "injection": injected,
        "notes_hidden": notes,
    }


def route_item(item):
    if item["invalid"] or item["conflict"]:
        return "confirm_with_owner"
    if item["status"] == "done":
        return "record_only"
    if item["is_overdue"]:
        return "immediate"
    if item["category"] == "equipment" and item["asset_down"]:
        return "immediate"
    if item["category"] == "cash":
        return "immediate"
    if item["status"] == "unknown" or not item["owner"]:
        return "confirm_with_owner"
    return "next_shift"


# --------------------------------------------------------------------------
# main analysis
# --------------------------------------------------------------------------
def analyse(data):
    if not isinstance(data, dict):
        data = {}
    hits = find_credentials(data)
    if hits:
        return reject(hits)

    warnings = []
    as_of = parse_dt(data.get("as_of"))
    if as_of is None:
        warnings.append("AS_OF_MISSING_OR_INVALID")

    store_raw = data.get("store") if isinstance(data.get("store"), dict) else {}
    store_id = clean_text(store_raw.get("store_id")) if has_text(store_raw.get("store_id")) else None
    store_name = clean_text(store_raw.get("name")) if has_text(store_raw.get("name")) else None
    tz_name = clean_text(store_raw.get("timezone")) if has_text(store_raw.get("timezone")) else None
    tz = resolve_tz(tz_name)
    if tz_name and tz is None:
        warnings.append("TIMEZONE_UNRESOLVED")

    equipment_raw = data.get("equipment") if isinstance(data.get("equipment"), list) else []
    equipment = []
    down_assets = set()
    known_assets = set()
    for index, entry in enumerate(equipment_raw):
        if not isinstance(entry, dict):
            continue
        asset_id = clean_text(entry.get("asset_id")) if has_text(entry.get("asset_id")) else None
        state = clean_text(entry.get("state")).lower() if has_text(entry.get("state")) else "unknown"
        eflags = []
        if asset_id is None:
            eflags.append("MISSING_ASSET_ID")
        if state not in VALID_EQUIPMENT_STATE:
            eflags.append("INVALID_EQUIPMENT_STATE")
        if asset_id:
            known_assets.add(asset_id)
            if state == "down":
                down_assets.add(asset_id)
        equipment.append({
            "asset_id": asset_id,
            "name": clean_text(entry.get("name")) if has_text(entry.get("name")) else None,
            "state": state,
            "since": clean_text(entry.get("since")) if has_text(entry.get("since")) else None,
            "flags": eflags,
            "index": index,
        })

    if any("INVALID_EQUIPMENT_STATE" in entry["flags"] for entry in equipment):
        warnings.append("INVALID_EQUIPMENT_STATE")

    # ---- shift window ----
    shift_raw = data.get("shift") if isinstance(data.get("shift"), dict) else {}
    shift_flags = []
    shift_id = clean_text(shift_raw.get("shift_id")) if has_text(shift_raw.get("shift_id")) else None
    start_dt = parse_dt(shift_raw.get("starts_at"))
    end_dt = parse_dt(shift_raw.get("ends_at"))
    duration_hours = None
    crosses_midnight = False
    if has_text(shift_raw.get("starts_at")) and start_dt is None:
        shift_flags.append("INVALID_STARTS_AT")
    if has_text(shift_raw.get("ends_at")) and end_dt is None:
        shift_flags.append("INVALID_ENDS_AT")
    if start_dt is not None and end_dt is not None:
        if end_dt <= start_dt:
            shift_flags.append("INVALID_SHIFT_WINDOW")
        else:
            duration_hours = quant(Decimal((end_dt - start_dt).total_seconds()) / Decimal(3600), 2)
            if tz is not None:
                if end_dt.astimezone(tz).date() > start_dt.astimezone(tz).date():
                    crosses_midnight = True
            else:
                if end_dt.utcoffset() == start_dt.utcoffset() and (
                        end_dt.replace(tzinfo=None).date() > start_dt.replace(tzinfo=None).date()):
                    crosses_midnight = True

    ctx = {
        "as_of": as_of,
        "down_assets": down_assets,
        "known_assets": known_assets,
    }

    items_raw = data.get("items")
    items_missing = items_raw is None or not isinstance(items_raw, list) or len(items_raw) == 0
    raw_list = items_raw if isinstance(items_raw, list) else []
    resolved = [normalise_item(raw, index, ctx) for index, raw in enumerate(raw_list)]

    # ---- duplicate / conflict resolution ----
    grouped = {}
    order = []
    for item in resolved:
        key = item["item_id"]
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item)

    duplicate_item_ids = []
    conflicts = []
    survivors = []
    for key in order:
        group = grouped[key]
        if len(group) > 1:
            statuses = sorted({str(g["status"]) for g in group})
            if len(statuses) > 1:
                conflicts.append({
                    "item_id": key,
                    "statuses": statuses,
                    "occurrences": len(group),
                    "paths": sorted(g["path"] for g in group),
                })
                primary = group[0]
                primary["conflict"] = True
                primary["flags"].append("CONFLICTING_STATUS")
                survivors.append(primary)
            else:
                duplicate_item_ids.append({
                    "item_id": key,
                    "occurrences": len(group),
                    "statuses": statuses,
                    "paths": sorted(g["path"] for g in group),
                })
                primary = group[0]
                primary["flags"].append("DUPLICATE_ITEM_ID")
                survivors.append(primary)
        else:
            survivors.append(group[0])

    board = {name: [] for name in BOARD_BUCKETS}
    for item in survivors:
        item["route"] = route_item(item)
        board[item["route"]].append(item)

    # ---- aggregates ----
    invalid_items = []
    missing_fields = []
    evidence_gaps = []
    unfinished = []
    overdue = []
    totals = {}
    unknown_amount_count = 0
    unknown_quantity_count = 0

    for item in survivors:
        if item["invalid"]:
            invalid_items.append({
                "item_id": item["item_id"],
                "path": item["path"],
                "flags": sorted(f for f in item["flags"] if f.startswith(("INVALID", "MISSING"))),
            })
        for field in REQUIRED_ITEM_FIELDS:
            if field == "summary" and not item["summary"]:
                missing_fields.append({"item_id": item["item_id"], "field": field, "severity": "REQUIRED"})
            elif field == "category" and item["category"] is None:
                missing_fields.append({"item_id": item["item_id"], "field": field, "severity": "REQUIRED"})
            elif field == "status" and item["status"] is None:
                missing_fields.append({"item_id": item["item_id"], "field": field, "severity": "REQUIRED"})
        for field in RECOMMENDED_ITEM_FIELDS:
            if field == "owner" and item["owner"] is None:
                missing_fields.append({"item_id": item["item_id"], "field": field, "severity": "RECOMMENDED"})
            elif field == "due_at" and item["due_at"] is None:
                missing_fields.append({"item_id": item["item_id"], "field": field, "severity": "RECOMMENDED"})

        if item["status"] != "done":
            unfinished.append(item["item_id"])
            if not item["evidence_refs"]:
                evidence_gaps.append({"item_id": item["item_id"], "flag": "EVIDENCE_MISSING"})
        if item["is_overdue"]:
            overdue.append(item["item_id"])

        if item["amount_known"] and item["amount_currency"]:
            bucket = totals.setdefault(item["amount_currency"], Decimal("0"))
            totals[item["amount_currency"]] = bucket + item["amount_value"]
        elif not item["amount_known"] and "AMOUNT_UNKNOWN" in item["flags"]:
            unknown_amount_count += 1
        if not item["quantity_known"] and "QUANTITY_UNKNOWN" in item["flags"]:
            unknown_quantity_count += 1

    totals_by_currency = {code: str(quant(totals[code], 2)) for code in sorted(totals)}

    downtime = [{
        "asset_id": entry["asset_id"],
        "name": entry["name"],
        "state": entry["state"],
        "since": entry["since"],
    } for entry in equipment if entry["state"] == "down"]

    # ---- state ----
    if shift_flags:
        status = "BLOCKED"
    elif conflicts or invalid_items:
        status = "BLOCKED"
    elif as_of is None or items_missing:
        status = "INPUT_INCOMPLETE"
    elif missing_fields or unknown_amount_count or unknown_quantity_count or "TIMEZONE_UNRESOLVED" in warnings:
        status = "GAPS_FOUND"
    else:
        status = "READY"

    # ---- opening checklist (fixed order, conditional inclusion) ----
    checklist = []

    def add(topic, action):
        checklist.append({"step": len(checklist) + 1, "topic": topic, "action": action})

    if any(i["category"] == "cash" for i in survivors):
        add("CASH_RECONCILED", "现金与账面对照当面点清，双方在交接单上签字确认差异金额。")
    if downtime:
        add("EQUIPMENT_DOWNTIME", "设备停机状态已交接，并已在工单系统登记资产编号与停机起始时间。")
    if overdue:
        add("OVERDUE_ESCALATION", "逾期事项已完成或已升级给当班负责人，逾期原因记入交接单。")
    if any(i["owner"] is None for i in survivors):
        add("OWNER_ASSIGNED", "无责任人的事项已指派到具体班次成员。")
    if conflicts:
        add("CONFLICT_RESOLVED", "同一事项的状态冲突已由当班负责人裁决，仅保留一个有效状态。")
    if duplicate_item_ids:
        add("DUPLICATE_MERGED", "重复事项编号已合并，避免同一件事被两班重复跟进。")
    if unknown_amount_count or unknown_quantity_count:
        add("UNKNOWN_VALUES_FILLED", "金额或数量的空白格已补填或明确标注为未知，未按 0 处理。")
    if evidence_gaps:
        add("EVIDENCE_ATTACHED", "未完成事项已附票据、照片或聊天记录编号作为证据引用。")
    add("BOARD_DELIVERED", "下一班负责人已收到本交接板并确认开班。")

    # ---- clarification questions (fixed topic order) ----
    questions = []

    def ask(topic, text):
        questions.append({"id": "Q-%02d" % (len(questions) + 1), "topic": topic, "question": text})

    if as_of is None:
        ask("AS_OF", "请提供带时区偏移的 as_of（例如 2026-09-25T07:40:00+08:00），否则无法判断事项是否逾期。")
    if shift_flags:
        ask("SHIFT_WINDOW", "班次起止时间无效或结束早于开始，请确认跨午夜班次的两个时间戳及其时区偏移。")
    if items_missing:
        ask("ITEMS", "本班没有提交任何事项记录，请确认是否确实无待交接事项。")
    if conflicts:
        ask("CONFLICTING_STATUS", "以下事项编号出现了互相矛盾的状态，请确认哪一条为准：" +
            "、".join(c["item_id"] or "(无编号)" for c in conflicts))
    if duplicate_item_ids:
        ask("DUPLICATE_ITEM_ID", "以下事项编号重复出现，请确认是否同一件事：" +
            "、".join(d["item_id"] or "(无编号)" for d in duplicate_item_ids))
    if any(i["owner"] is None for i in survivors):
        ask("OWNER_MISSING", "部分事项没有责任人，请指派到具体班次成员，否则下一班无法跟进。")
    if overdue:
        ask("OVERDUE", "以下事项已过截止时间，请确认是已完成未更新还是需要升级：" +
            "、".join(str(x) for x in overdue))
    if unknown_amount_count or unknown_quantity_count:
        ask("UNKNOWN_VALUES", "部分金额或数量为空白或未知，请补齐；本工具不会把未知当作 0。")
    if "TIMEZONE_UNRESOLVED" in warnings:
        ask("TIMEZONE", "门店时区无法解析，请改用带明确偏移的时间戳，或提供有效的 IANA 时区名。")
    if downtime:
        ask("EQUIPMENT", "设备停机影响哪些事项，请确认是否需要下一班优先处理。")
    if any("INVALID_EQUIPMENT_STATE" in entry["flags"] for entry in equipment):
        ask("EQUIPMENT_STATE", "部分设备记录的状态不在允许取值内（down / degraded / ok / unknown），"
                               "请按字段表修正，否则无法判断该设备是否算停机。")
    if invalid_items:
        ask("INVALID_RECORD", "部分事项记录的类别或状态不在允许取值内，请按字段表填写。")
    if evidence_gaps:
        ask("EVIDENCE", "部分未完成事项没有证据引用，请补充票据、照片或记录的编号。")

    # ---- injection report ----
    injection_flagged = []
    for item in survivors:
        if item["injection"]:
            injection_flagged.append({"path": item["path"] + "/notes", "marker": "PROMPT_INJECTION"})

    markdown = render_markdown(
        status=status, store_name=store_name, store_id=store_id,
        shift_id=shift_id, start=shift_raw.get("starts_at"), end=shift_raw.get("ends_at"),
        duration_hours=duration_hours, crosses_midnight=crosses_midnight,
        as_of=data.get("as_of"), board=board, totals=totals_by_currency,
        unknown_amount_count=unknown_amount_count, unknown_quantity_count=unknown_quantity_count,
        checklist=checklist, questions=questions, downtime=downtime, warnings=warnings,
    )

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(data.get("as_of")) if has_text(data.get("as_of")) else None,
        "store": {"store_id": store_id, "name": store_name, "timezone": tz_name,
                  "timezone_resolved": tz is not None},
        "shift": {
            "shift_id": shift_id,
            "starts_at": clean_text(shift_raw.get("starts_at")) if has_text(shift_raw.get("starts_at")) else None,
            "ends_at": clean_text(shift_raw.get("ends_at")) if has_text(shift_raw.get("ends_at")) else None,
            "duration_hours": str(duration_hours) if duration_hours is not None else None,
            "crosses_midnight": crosses_midnight,
            "review_flags": sorted(set(shift_flags)),
        },
        "board": {
            name: [board_item_view(item) for item in board[name]]
            for name in BOARD_BUCKETS
        },
        "board_counts": {name: len(board[name]) for name in BOARD_BUCKETS},
        "items": [item_view(item) for item in survivors],
        "unfinished_items": unfinished,
        "overdue_items": overdue,
        "duplicate_item_ids": duplicate_item_ids,
        "conflicts": conflicts,
        "invalid_items": invalid_items,
        "missing_fields": missing_fields,
        "evidence_gaps": evidence_gaps,
        "totals_by_currency": totals_by_currency,
        "unknown_amount_count": unknown_amount_count,
        "unknown_quantity_count": unknown_quantity_count,
        "equipment": [equipment_view(entry) for entry in equipment],
        "equipment_downtime": downtime,
        "opening_checklist": checklist,
        "clarification_questions": questions,
        "injection_flagged": injection_flagged,
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
        "input_warnings": warnings,
    }


def board_item_view(item):
    return {
        "item_id": item["item_id"],
        "summary": item["summary"],
        "category": item["category"],
        "status": item["status"],
        "owner": item["owner"],
        "due_at": item["due_at"],
        "is_overdue": item["is_overdue"],
        "evidence_refs": list(item["evidence_refs"]),
        "flags": sorted(set(item["flags"])),
    }


def item_view(item):
    view = {
        "item_id": item["item_id"],
        "path": item["path"],
        "summary": item["summary"],
        "category": item["category"],
        "status": item["status"],
        "owner": item["owner"],
        "due_at": item["due_at"],
        "route": item["route"],
        "evidence_refs": list(item["evidence_refs"]),
        "amount": None,
        "quantity": None,
        "review_flags": sorted(set(item["flags"])),
    }
    if item["amount_known"] and item["amount_currency"]:
        view["amount"] = {"value": str(quant(item["amount_value"], 2)), "currency": item["amount_currency"]}
    elif item["amount_value"] is not None or item["amount_currency"]:
        # A blank value on a stated currency is "unknown amount in CNY", never 0 and
        # never silently dropped: the currency is part of what the record said.
        view["amount"] = {
            "value": str(quant(item["amount_value"], 2)) if item["amount_value"] is not None else None,
            "currency": item["amount_currency"],
        }
    if item["quantity_known"]:
        view["quantity"] = {"value": str(item["quantity_value"]), "unit": item["quantity_unit"]}
    elif item["quantity_unit"]:
        view["quantity"] = {"value": None, "unit": item["quantity_unit"]}
    return view


def equipment_view(entry):
    return {
        "asset_id": entry["asset_id"],
        "name": entry["name"],
        "state": entry["state"],
        "since": entry["since"],
        "flags": sorted(set(entry["flags"])),
    }


BUCKET_TITLES = (
    ("immediate", "立即处理"),
    ("next_shift", "下一班"),
    ("confirm_with_owner", "待负责人确认"),
    ("record_only", "仅记录"),
)


def render_markdown(status, store_name, store_id, shift_id, start, end,
                    duration_hours, crosses_midnight, as_of, board, totals,
                    unknown_amount_count, unknown_quantity_count, checklist,
                    questions, downtime, warnings):
    lines = []
    title = store_name or store_id or "门店"
    lines.append("# 交接班准备包 — %s" % esc(title))
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 生成基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 班次编号：%s" % (esc(shift_id) if shift_id else "未提供"))
    lines.append("- 班次区间：%s → %s" % (
        esc(start) if has_text(start) else "未提供",
        esc(end) if has_text(end) else "未提供",
    ))
    lines.append("- 时长：%s 小时" % (duration_hours if duration_hours is not None else "无法计算"))
    lines.append("- 跨午夜：%s" % ("是" if crosses_midnight else "否"))
    lines.append("")

    lines.append("## 交接板")
    lines.append("")
    for key, label in BUCKET_TITLES:
        entries = board[key]
        lines.append("### %s（%d）" % (label, len(entries)))
        lines.append("")
        if not entries:
            lines.append("- 无")
            lines.append("")
            continue
        lines.append("| 编号 | 事项 | 备注 | 类别 | 责任人 | 截止 |")
        lines.append("|---|---|---|---|---|---|")
        for item in entries:
            # The 事项 column is the item's own summary; notes are supplementary and
            # get their own column so neither one overwrites the other.
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                esc(item["item_id"]) if item["item_id"] else "未提供",
                hidden(item["summary"]),
                hidden(item["notes_hidden"]) if clean_text(item["notes_hidden"]) else "—",
                esc(item["category"]) if item["category"] else "未提供",
                esc(item["owner"]) if item["owner"] else "未指派",
                esc(item["due_at"]) if item["due_at"] else "未提供",
            ))
        lines.append("")

    lines.append("## 金额合计（按币种，不跨币种合并）")
    lines.append("")
    if totals:
        lines.append("| 币种 | 合计 |")
        lines.append("|---|---|")
        for code in sorted(totals):
            lines.append("| %s | %s |" % (esc(code), totals[code]))
    else:
        lines.append("- 无可用金额（已知币种的已填金额为空）")
    lines.append("")
    lines.append("- 金额未知的事项：%d" % unknown_amount_count)
    lines.append("- 数量未知的事项：%d" % unknown_quantity_count)
    lines.append("")

    if downtime:
        lines.append("## 设备停机")
        lines.append("")
        lines.append("| 资产编号 | 名称 | 状态 | 起始 |")
        lines.append("|---|---|---|---|")
        for entry in downtime:
            lines.append("| %s | %s | %s | %s |" % (
                esc(entry["asset_id"]) if entry["asset_id"] else "未提供",
                esc(entry["name"]) if entry["name"] else "未提供",
                esc(entry["state"]),
                esc(entry["since"]) if entry["since"] else "未提供",
            ))
        lines.append("")

    lines.append("## 开班确认清单")
    lines.append("")
    for step in checklist:
        lines.append("%d. [ ] %s" % (step["step"], esc(step["action"])))
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
