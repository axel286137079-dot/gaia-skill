#!/usr/bin/env python3
"""市集摊主申请准备包 — offline, deterministic, read-only.

Reads one JSON file, writes one JSON document to stdout. No network, no
third-party packages, no file writes, no child processes. Python 3.9+.
"""
import json
import math
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

try:  # stdlib on Python 3.9+; absent tzdata is handled as "timezone unknown".
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

SKILL = "suge-pop-up-market-vendor-application-pack"
VERSION = "1.0.2"

D = Decimal
CENT = D("0.01")
DUE_SOON_DAYS = 7

# ---- untrusted-input rules (deterministic, finite vocabularies) -------------

OVERRIDE_VERBS = [
    "忽略", "忽视", "无视", "不要理会", "不用理会", "忘记", "跳过",
    "ignore", "disregard", "override", "forget", "bypass",
]
INSTRUCTION_NOUNS = [
    "指令", "要求", "规则", "设定", "系统提示", "系统消息", "提示词", "提示",
    "上述", "以上", "之前", "所有",
    "instruction", "instructions", "rule", "rules", "prompt", "system",
    "previous", "above", "earlier",
]
CREDENTIAL_KEYS = (
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "access_key", "private_key", "client_secret", "cookie", "session_id",
    "auth_code", "授权码", "密码", "密钥", "令牌",
)
# Split so this source never contains a literal PEM header that a release
# scanner (or a reader) could mistake for a real key.
_PEM_HEAD = "-----BEGIN "
_PEM_TAIL = "PRIVATE KEY-----"
CREDENTIAL_VALUE_PATTERNS = [
    re.compile(_PEM_HEAD + r"[A-Z ]*" + _PEM_TAIL),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}"),
]
PLACEHOLDER = "已隐藏疑似提示注入文本"

FOOD = "FOOD"
NON_FOOD = "NON_FOOD"

ELIGIBLE_STATES = ("MET", "MET_EXPIRY_UNKNOWN", "NOT_APPLICABLE")
BLOCKING_STATES = ("MISSING", "EXPIRED", "NOT_AVAILABLE", "INVALID_EXPIRY_DATE", "UNKNOWN")


# ---- helpers ---------------------------------------------------------------


def dec(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return D(value)
    if isinstance(value, float):
        return D(str(value))
    if isinstance(value, str):
        raw = value.strip()
        if raw == "":
            return None
        try:
            return D(raw)
        except InvalidOperation:
            return None
    return None


def money(value):
    return None if value is None else str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def normalize_text(value):
    if not isinstance(value, str):
        return value
    text = unicodedata.normalize("NFKC", value)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())


def clause_flags_injection(text):
    if not isinstance(text, str):
        return False
    low = unicodedata.normalize("NFKC", text).casefold()
    for clause in re.split(r"[。！？；;!?\n\r]+", low):
        clause = clause[:200]
        if any(v in clause for v in OVERRIDE_VERBS) and any(n in clause for n in INSTRUCTION_NOUNS):
            return True
    return False


def safe(value):
    if isinstance(value, str) and clause_flags_injection(value):
        return PLACEHOLDER
    text = "" if value is None else str(value)
    text = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in text)
    text = " ".join(text.split())
    for ch in ("\\", "|", "`", "[", "]", "(", ")", "#", "!", "<", ">", "*", "_"):
        text = text.replace(ch, "\\" + ch)
    return text


def strings_in(node, path, out):
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            strings_in(v, path + "." + str(k) if path else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            strings_in(v, "%s[%d]" % (path, i), out)


def credential_paths(node):
    hits = []
    stack = [("", node)]
    while stack:
        path, cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                child = path + "." + str(k) if path else str(k)
                if isinstance(v, (dict, list)):
                    stack.append((child, v))
                    continue
                if any(t in str(k).casefold() for t in CREDENTIAL_KEYS):
                    hits.append(child)
                elif isinstance(v, str) and any(p.search(v) for p in CREDENTIAL_VALUE_PATTERNS):
                    hits.append(child)
        elif isinstance(cur, list):
            for i, v in enumerate(cur):
                child = "%s[%d]" % (path, i)
                if isinstance(v, (dict, list)):
                    stack.append((child, v))
                elif isinstance(v, str) and any(p.search(v) for p in CREDENTIAL_VALUE_PATTERNS):
                    hits.append(child)
    return sorted(set(hits))


def parse_iso(value):
    """Parse an ISO8601 string with an explicit offset. Naive input is rejected."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def offset_label(dt):
    if dt is None:
        return None
    total = int(dt.utcoffset().total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return "%s%02d:%02d" % (sign, total // 3600, (total % 3600) // 60)


def remaining_days(deadline, as_of):
    if deadline is None or as_of is None:
        return None
    return math.floor((deadline - as_of).total_seconds() / 86400.0)


def deadline_state(remaining):
    if remaining is None:
        return "UNKNOWN"
    if remaining < 0:
        return "EXPIRED"
    if remaining <= DUE_SOON_DAYS:
        return "DUE_SOON"
    return "OPEN"


# `expires_on` is a calendar date, not an instant: the leading YYYY-MM-DD of an
# ISO datetime is accepted, anything else is a data error rather than a guess.
_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[T\s].*)?$")
_OFFSET_TZ = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def parse_calendar_date(value):
    """Return a `date` for a date-only expiry, or None when it is not a date."""
    if not isinstance(value, str):
        return None
    match = _DATE_PREFIX.match(value.strip())
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def zone_of(label):
    """Resolve a declared timezone label; None when it cannot be resolved offline."""
    if not isinstance(label, str) or not label.strip():
        return None
    text = label.strip()
    offset = _OFFSET_TZ.match(text)
    if offset:
        hours = int(offset.group(2))
        minutes = int(offset.group(3))
        if hours > 23 or minutes > 59:
            return None
        sign = 1 if offset.group(1) == "+" else -1
        return timezone(sign * timedelta(hours=hours, minutes=minutes))
    if text.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(text)
    except Exception:
        return None


def local_calendar_date(as_of_dt, tz_label):
    """The calendar date of `as_of_dt` in the declared timezone.

    None means "cannot be determined": the engine never substitutes UTC for a
    declared timezone it could not resolve.
    """
    if as_of_dt is None:
        return None
    if isinstance(tz_label, str) and tz_label.strip():
        zone = zone_of(tz_label)
        if zone is None:
            return None
        return as_of_dt.astimezone(zone).date()
    # No usable timezone was declared: the only location information is the
    # offset carried by as_of itself, which is declared input, not a guess.
    return as_of_dt.date()


def normalize_products(raw):
    out = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        cat = normalize_text(p.get("category")) if isinstance(p.get("category"), str) else None
        if isinstance(cat, str):
            cat = cat.upper().replace("-", "_")
        out.append({
            "product_id": normalize_text(p.get("product_id")) if isinstance(p.get("product_id"), str) else None,
            "name": normalize_text(p.get("name")) if isinstance(p.get("name"), str) else None,
            "category": cat,
            "unit_price": money(dec(p.get("unit_price"))),
            "currency": normalize_text(p.get("currency")) if isinstance(p.get("currency"), str) else None,
        })
    return out


def normalize_equipment(raw):
    out = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        watts = dec(e.get("power_watts"))
        out.append({
            "equipment_id": normalize_text(e.get("equipment_id")) if isinstance(e.get("equipment_id"), str) else None,
            "label": normalize_text(e.get("label")) if isinstance(e.get("label"), str) else None,
            "power_watts": int(watts) if watts is not None and watts >= 0 and watts == watts.to_integral_value() else None,
        })
    return out


def normalize_materials(raw):
    out = []
    for m in raw:
        if not isinstance(m, dict):
            continue
        out.append({
            "material_id": normalize_text(m.get("material_id")) if isinstance(m.get("material_id"), str) else None,
            "type": (normalize_text(m.get("type")).upper().replace("-", "_") if isinstance(m.get("type"), str) else None),
            "label": normalize_text(m.get("label")) if isinstance(m.get("label"), str) else None,
            "status": (normalize_text(m.get("status")).upper() if isinstance(m.get("status"), str) else None),
            "expires_on": normalize_text(m.get("expires_on")) if isinstance(m.get("expires_on"), str) else None,
        })
    return out


def duplicates(values, key):
    counts = {}
    for v in values:
        v = v.get(key)
        if v:
            counts[v] = counts.get(v, 0) + 1
    return sorted([k for k, c in counts.items() if c > 1])


# ---- core ------------------------------------------------------------------


def build_output(doc):
    as_of_raw = doc.get("as_of") if isinstance(doc.get("as_of"), str) else None
    as_of = parse_iso(as_of_raw)

    leaves = []
    strings_in(doc, "", leaves)
    injection_flagged = sorted({p for p, v in leaves if clause_flags_injection(v)})

    creds = credential_paths(doc)
    if creds:
        return {
            "skill": SKILL,
            "version": VERSION,
            "as_of": as_of_raw,
            "status": "REJECTED",
            "rejected_reason": "CREDENTIAL_DETECTED",
            "rejected_fields": creds,
            "injection_flagged": [],
            "markdown_summary": (
                "# 输入被拒绝\n\n检测到疑似真实凭据字段（共 %d 处）。为保护隐私，**本工具不会回显这些内容**，"
                "也不会继续处理该文件。请移除凭据后重新提交摊主资料与招募规则。字段位置：%s。\n"
                % (len(creds), ", ".join(safe(p) for p in creds))
            ),
            "disclaimer": "本输出为市集申请准备材料，不构成法律、食品安全或资质结论；主办方规则以主办方原文为准。",
        }

    vendor = doc.get("vendor") if isinstance(doc.get("vendor"), dict) else {}
    materials = normalize_materials(doc.get("vendor_materials") if isinstance(doc.get("vendor_materials"), list) else [])
    products = normalize_products(doc.get("products") if isinstance(doc.get("products"), list) else [])
    equipment = normalize_equipment(doc.get("equipment") if isinstance(doc.get("equipment"), list) else [])
    markets = doc.get("markets") if isinstance(doc.get("markets"), list) else []

    # FOOD / NON_FOOD / unknown are three disjoint buckets. An unlabelled or
    # unrecognised category is NOT evidence of a non-food product: it is missing
    # input, counted separately and surfaced as such.
    food_products = [p for p in products if p["category"] == FOOD]
    non_food_products = [p for p in products if p["category"] == NON_FOOD]
    unknown_category_ids = [p["product_id"] for p in products if p["category"] not in (FOOD, NON_FOOD)]
    unknown_category_count = len(unknown_category_ids)

    vendor_has_food = len(food_products) > 0
    # Whether the food/non-food split can be asserted at all. Only a catalogue that
    # is fully labelled (or genuinely empty) lets a FOOD-scoped rule be called
    # NOT_APPLICABLE; one unlabelled product keeps the scope undecided.
    food_scope_known = unknown_category_count == 0

    # deterministic material lookup: latest expiry first, then input order
    def pick_material(req_type):
        candidates = [m for m in materials if m["type"] == req_type]
        if not candidates:
            return None

        def sort_key(item):
            idx, m = item
            expiry = m.get("expires_on") or ""
            return (expiry, -idx)

        best = sorted(enumerate(candidates), key=sort_key)[-1][1]
        return best

    equipment_known = [e["power_watts"] for e in equipment if e["power_watts"] is not None]
    equipment_unknown = [e["equipment_id"] or e["label"] for e in equipment if e["power_watts"] is None]
    power_total = sum(equipment_known) if equipment_known else 0

    market_rows = []
    questions = []
    qn = [0]

    def ask(topic, text, market_id=None):
        qn[0] += 1
        questions.append({"id": "Q-%02d" % qn[0], "topic": topic, "market_id": market_id, "question": text})

    fee_by_currency = {}
    refundable_by_currency = {}
    invalid_fees = []
    fee_count = 0

    for mi, market in enumerate(markets):
        if not isinstance(market, dict):
            continue
        mid = normalize_text(market.get("market_id")) if isinstance(market.get("market_id"), str) else None
        name = normalize_text(market.get("name")) if isinstance(market.get("name"), str) else None
        tz_label = normalize_text(market.get("timezone")) if isinstance(market.get("timezone"), str) else None
        app_tz = normalize_text(market.get("application_timezone")) if isinstance(market.get("application_timezone"), str) else None

        deadline = parse_iso(market.get("application_deadline"))
        event_start = parse_iso(market.get("event_start"))
        event_end = parse_iso(market.get("event_end"))
        remaining = remaining_days(deadline, as_of)
        state = deadline_state(remaining)

        event_days = None
        if event_start is not None and event_end is not None:
            event_days = (event_end.date() - event_start.date()).days + 1

        flags = []
        notices = []

        if isinstance(market.get("application_deadline"), str) and market.get("application_deadline") and deadline is None:
            flags.append("DEADLINE_NOT_OFFSET_AWARE")
        if deadline is None:
            flags.append("DEADLINE_UNKNOWN")
        if app_tz and tz_label and app_tz != tz_label:
            flags.append("TIMEZONE_MISMATCH")
        if deadline is not None and event_start is not None and offset_label(deadline) != offset_label(event_start):
            flags.append("TIMEZONE_INCONSISTENT")

        accepts_food = market.get("accepts_food")
        accepts_food_norm = accepts_food if isinstance(accepts_food, bool) else None
        if accepts_food_norm is False and vendor_has_food:
            notices.append("FOOD_PRODUCT_NOT_ACCEPTED")

        requirements = market.get("requirements") if isinstance(market.get("requirements"), list) else []
        if not requirements:
            flags.append("REQUIREMENTS_NOT_PROVIDED")
        if not food_scope_known:
            # The catalogue carries products with no FOOD / NON_FOOD label, so this
            # market's food scope cannot be decided. The flag is part of the market
            # state, not just a question: it forces at least INPUT_INCOMPLETE below.
            flags.append("PRODUCT_CATEGORY_UNKNOWN")

        eligibility = []
        missing_mandatory = []
        invalid_expiry = []
        tz_unknown_expiry = []
        for ri, req in enumerate(requirements):
            if not isinstance(req, dict):
                continue
            req_type = normalize_text(req.get("type")).upper().replace("-", "_") if isinstance(req.get("type"), str) else None
            applies_to = normalize_text(req.get("applies_to")).upper() if isinstance(req.get("applies_to"), str) else None
            mandatory = bool(req.get("mandatory"))
            entry = {
                "req_id": normalize_text(req.get("req_id")) if isinstance(req.get("req_id"), str) else None,
                "type": req_type,
                "label": normalize_text(req.get("label")) if isinstance(req.get("label"), str) else None,
                "mandatory": mandatory,
                "applies_to": applies_to,
            }
            if applies_to == FOOD and not vendor_has_food:
                if not food_scope_known:
                    # "No food products" cannot be asserted while products carry no
                    # category: the vendor may well be selling unlabelled food, so the
                    # permit stays undecided instead of being waved through as
                    # NOT_APPLICABLE. Mandatory ones fall into missing_mandatory below.
                    entry["state"] = "UNKNOWN"
                    entry["evidence"] = (
                        "存在 %d 个未标注类别的商品（%s），无法判断摊主是否有 FOOD 类商品，"
                        "该食品相关要求按 CATEGORY_UNKNOWN 处理（未放行）" % (
                            unknown_category_count, ", ".join(str(x) for x in unknown_category_ids)))
                    entry["material_id"] = None
                else:
                    entry["state"] = "NOT_APPLICABLE"
                    entry["evidence"] = "摊主当前没有 FOOD 类商品"
                    entry["material_id"] = None
                eligibility.append(entry)
                if mandatory and entry["state"] not in ELIGIBLE_STATES:
                    missing_mandatory.append({"req_id": entry["req_id"], "type": entry["type"],
                                              "label": entry["label"], "state": entry["state"]})
                continue
            material = pick_material(req_type) if req_type else None
            if material is None:
                entry["state"] = "MISSING"
                entry["evidence"] = "未提供类型为 %s 的材料" % (req_type or "未知")
            else:
                status = material.get("status")
                expires = material.get("expires_on")
                if status is not None and status != "AVAILABLE":
                    entry["state"] = "NOT_AVAILABLE"
                    entry["evidence"] = "材料 %s 当前状态为 %s" % (material.get("material_id"), status)
                elif not expires:
                    entry["state"] = "MET_EXPIRY_UNKNOWN"
                    entry["evidence"] = "材料 %s 未提供到期日" % material.get("material_id")
                    flags.append("MATERIAL_EXPIRY_UNKNOWN")
                else:
                    expiry_date = parse_calendar_date(expires)
                    if expiry_date is None:
                        # A malformed date is a data error: it is never read as
                        # "valid". The requirement stays unmet until corrected.
                        entry["state"] = "INVALID_EXPIRY_DATE"
                        entry["evidence"] = "材料 %s 的到期日「%s」不是合法日期（应为 YYYY-MM-DD），无法判断是否仍在有效期内" % (
                            material.get("material_id"), expires)
                        flags.append("MATERIAL_EXPIRY_INVALID")
                        invalid_expiry.append({
                            "req_id": entry["req_id"], "label": entry["label"],
                            "material_id": material.get("material_id"), "expires_on": expires,
                        })
                    elif as_of is None:
                        entry["state"] = "MET"
                        entry["evidence"] = "材料 %s 有效至 %s（未提供 as_of，未校验是否过期）" % (
                            material.get("material_id"), expires)
                    else:
                        today = local_calendar_date(as_of, tz_label)
                        if today is None:
                            # The declared timezone could not be resolved offline:
                            # stay unknown instead of assuming UTC or the host zone.
                            entry["state"] = "MET_EXPIRY_UNKNOWN"
                            entry["evidence"] = "材料 %s 到期日 %s，但无法离线解析时区「%s」的当地日历日，未判定是否过期" % (
                                material.get("material_id"), expires, tz_label)
                            flags.append("MATERIAL_EXPIRY_TZ_UNKNOWN")
                            tz_unknown_expiry.append({
                                "req_id": entry["req_id"], "label": entry["label"],
                                "material_id": material.get("material_id"), "expires_on": expires,
                            })
                        elif expiry_date < today:
                            entry["state"] = "EXPIRED"
                            entry["evidence"] = "材料 %s 于 %s 到期，早于当地日历日 %s（时区 %s），已过期" % (
                                material.get("material_id"), expires, today.isoformat(), tz_label)
                        else:
                            entry["state"] = "MET"
                            entry["evidence"] = "材料 %s 有效至 %s（含到期日当天；当地日历日 %s，时区 %s）" % (
                                material.get("material_id"), expires, today.isoformat(), tz_label)
            entry["material_id"] = material.get("material_id") if material else None
            eligibility.append(entry)
            if mandatory and entry["state"] not in ELIGIBLE_STATES:
                missing_mandatory.append({"req_id": entry["req_id"], "type": entry["type"],
                                          "label": entry["label"], "state": entry["state"]})

        eligibility_counts = {}
        for e in eligibility:
            eligibility_counts[e["state"]] = eligibility_counts.get(e["state"], 0) + 1
        optional_open = [e for e in eligibility
                         if not e["mandatory"] and e["state"] in ("MISSING", "EXPIRED", "NOT_AVAILABLE", "UNKNOWN")]
        if optional_open:
            notices.append("OPTIONAL_REQUIREMENTS_OPEN:%d" % len(optional_open))

        power_limit = dec(market.get("booth_power_watts_limit"))
        power_limit_i = int(power_limit) if power_limit is not None and power_limit >= 0 else None
        if power_limit_i is None:
            power_state = "UNKNOWN"
        elif equipment_unknown:
            power_state = "UNKNOWN"
        elif power_total > power_limit_i:
            power_state = "EXCEEDED"
        else:
            power_state = "OK"
        if power_state == "EXCEEDED":
            flags.append("POWER_EXCEEDED")

        booth_max = market.get("booth_max_count")
        booth_max_i = int(booth_max) if isinstance(booth_max, int) and not isinstance(booth_max, bool) else None
        booth_req = market.get("booth_requested_count")
        booth_req_i = int(booth_req) if isinstance(booth_req, int) and not isinstance(booth_req, bool) else None
        if booth_max_i is not None and booth_req_i is not None and booth_req_i > booth_max_i:
            flags.append("BOOTH_COUNT_EXCEEDED")

        fees_raw = market.get("fees") if isinstance(market.get("fees"), list) else []
        market_fees = []
        market_invalid_fees = []
        for fee in fees_raw:
            if not isinstance(fee, dict):
                continue
            raw_amount = fee.get("amount")
            amount = dec(raw_amount)
            cur = normalize_text(fee.get("currency")) if isinstance(fee.get("currency"), str) else None
            refundable = bool(fee.get("refundable"))
            # A negative or non-numeric amount is a data error, not a fee: it is
            # echoed for correction but never enters any currency total.
            amount_valid = amount is not None and amount >= 0
            row = {
                "fee_id": normalize_text(fee.get("fee_id")) if isinstance(fee.get("fee_id"), str) else None,
                "label": normalize_text(fee.get("label")) if isinstance(fee.get("label"), str) else None,
                "amount": money(amount) if amount_valid else None,
                "amount_raw": None if raw_amount is None else str(raw_amount),
                "currency": cur,
                "refundable": refundable,
                "included_in_totals": False,
            }
            if not amount_valid:
                row["state"] = "INVALID_AMOUNT"
                flags.append("FEE_AMOUNT_INVALID")
                market_invalid_fees.append(row)
            elif not cur:
                row["state"] = "CURRENCY_MISSING"
            else:
                row["state"] = "COUNTED"
                row["included_in_totals"] = True
                fee_count += 1
                bucket = fee_by_currency.setdefault(cur, {"total": D(0), "count": 0})
                bucket["total"] += amount
                bucket["count"] += 1
                if refundable:
                    refundable_by_currency[cur] = refundable_by_currency.setdefault(cur, D(0)) + amount
            market_fees.append(row)
        for row in market_invalid_fees:
            invalid_fees.append({"market_id": mid, "fee_id": row["fee_id"], "label": row["label"],
                                 "amount_raw": row["amount_raw"]})
            ask("FEE_AMOUNT_INVALID",
                "市集「%s」的费用「%s」（编号 %s）金额为 %s：缺失、非法或为负数，本工具未将其计入任何币种合计，"
                "请更正金额与币种后再核对预算。" % (
                    safe(name), safe(row["label"]), safe(row["fee_id"]), safe(row["amount_raw"])), mid)

        if state == "EXPIRED":
            market_status = "DEADLINE_PASSED"
        elif missing_mandatory:
            market_status = "NOT_ELIGIBLE"
        elif power_state == "EXCEEDED" or "BOOTH_COUNT_EXCEEDED" in flags:
            market_status = "BOOTH_REQUIREMENT_RISK"
        elif state == "UNKNOWN" or "REQUIREMENTS_NOT_PROVIDED" in flags or "MATERIAL_EXPIRY_INVALID" in flags \
                or "PRODUCT_CATEGORY_UNKNOWN" in flags:
            market_status = "INPUT_INCOMPLETE"
        elif flags:
            market_status = "REVIEW_REQUIRED"
        else:
            market_status = "READY_TO_APPLY"

        for item in missing_mandatory:
            ask("MANDATORY_NOT_MET",
                "市集「%s」的必填材料「%s」（%s）当前状态为 %s，报名前必须补齐或向主办方确认替代方案。"
                % (safe(name), safe(item["label"]), safe(item["type"]), item["state"]), mid)
        if power_state == "EXCEEDED":
            ask("POWER",
                "市集「%s」用电上限 %s W，按你提供的设备合计 %d W，需确认是否可增容或减少设备。"
                % (safe(name), power_limit_i, power_total), mid)
        elif power_state == "UNKNOWN" and equipment_unknown:
            ask("POWER_UNKNOWN",
                "以下设备未提供功率（%s），无法核算市集「%s」的用电是否超标。"
                % (safe(", ".join(str(x) for x in equipment_unknown)), safe(name)), mid)
        if "BOOTH_COUNT_EXCEEDED" in flags:
            ask("BOOTH_COUNT",
                "市集「%s」摊位上限 %s 个，你申请 %s 个，请确认是否超额或需要合并。"
                % (safe(name), booth_max_i, booth_req_i), mid)
        if "TIMEZONE_MISMATCH" in flags:
            ask("TIMEZONE",
                "市集「%s」的活动时区为 %s，但报名时区写的是 %s，请确认截止时间以哪个时区为准。"
                % (safe(name), safe(tz_label), safe(app_tz)), mid)
        if state == "EXPIRED":
            ask("DEADLINE_PASSED",
                "市集「%s」报名已于 %s 截止（剩余 %d 天），请确认是否仍接受补报。" % (safe(name), safe(market.get("application_deadline")), remaining), mid)
        if state == "UNKNOWN":
            ask("DEADLINE_UNKNOWN", "市集「%s」未提供报名截止时间，请向主办方确认后再排期。" % safe(name), mid)
        if "REQUIREMENTS_NOT_PROVIDED" in flags:
            ask("REQUIREMENTS_MISSING", "市集「%s」未提供任何材料要求，请补充主办方招募规则原文。" % safe(name), mid)
        if "FOOD_PRODUCT_NOT_ACCEPTED" in notices:
            ask("FOOD_PRODUCT",
                "市集「%s」不收食品类摊位，但你提供了 %d 个食品商品，请确认是否只带非食品商品。" % (safe(name), len(food_products)), mid)
        if "MATERIAL_EXPIRY_UNKNOWN" in flags:
            ask("MATERIAL_EXPIRY", "市集「%s」用到的材料未提供到期日，请补充以便判断报名时是否有效。" % safe(name), mid)
        if invalid_expiry:
            ask("MATERIAL_EXPIRY_INVALID",
                "市集「%s」以下材料的到期日不是合法日期（应为 YYYY-MM-DD），本工具无法判断是否已过期，"
                "已按未满足处理，请更正后重新核对：%s。" % (
                    safe(name),
                    safe("; ".join("%s（%s）→「%s」" % (e["label"], e["material_id"], e["expires_on"])
                                   for e in invalid_expiry))), mid)
        if tz_unknown_expiry:
            ask("MATERIAL_EXPIRY_TZ_UNKNOWN",
                "市集「%s」声明时区「%s」，本工具无法离线解析该时区的当地日历日，因此以下材料的到期日"
                "无法与报名当天比较，未判定是否过期：%s。请人工确认，或改用 ±HH:MM 形式的时区偏移。"
                % (safe(name), safe(tz_label),
                   safe("; ".join("%s（%s）" % (e["label"], e["material_id"]) for e in tz_unknown_expiry))), mid)

        timeline = []
        if market.get("application_deadline"):
            timeline.append({"stage": "APPLICATION_DEADLINE", "at": safe(market.get("application_deadline")),
                             "local_offset": offset_label(deadline), "remaining_days": remaining, "state": state})
        if market.get("event_start"):
            timeline.append({"stage": "EVENT_START", "at": safe(market.get("event_start")),
                             "local_offset": offset_label(event_start), "remaining_days": remaining_days(event_start, as_of)})
        if market.get("event_end"):
            timeline.append({"stage": "EVENT_END", "at": safe(market.get("event_end")),
                             "local_offset": offset_label(event_end), "remaining_days": remaining_days(event_end, as_of)})

        market_rows.append({
            "market_id": mid,
            "name": name,
            "timezone": tz_label,
            "application_timezone": app_tz,
            "deadline_state": state,
            "remaining_days": remaining,
            "deadline_offset": offset_label(deadline),
            "event_days": event_days,
            "accepts_food": accepts_food_norm,
            "status": market_status,
            "review_flags": sorted(set(flags)),
            "notices": sorted(set(notices)),
            "eligibility": eligibility,
            "eligibility_counts": {k: eligibility_counts[k] for k in sorted(eligibility_counts)},
            "missing_mandatory": missing_mandatory,
            "booth": {
                "requested_count": booth_req_i,
                "max_count": booth_max_i,
                "size": normalize_text(market.get("booth_size")) if isinstance(market.get("booth_size"), str) else None,
                "outdoor": market.get("outdoor") if isinstance(market.get("outdoor"), bool) else None,
                "power_required_watts": power_total,
                "power_limit_watts": power_limit_i,
                "power_state": power_state,
                "power_unknown_equipment": equipment_unknown,
            },
            "fees": market_fees,
            "timeline": timeline,
            "rules_provided": bool(isinstance(market.get("rules_text"), str) and normalize_text(market.get("rules_text"))),
            "notes": normalize_text(market.get("notes")) if isinstance(market.get("notes"), str) else None,
        })

    # ---- vendor-level duplicate ids ----------------------------------------
    market_id_values = [normalize_text(m.get("market_id")) if isinstance(m, dict) and isinstance(m.get("market_id"), str) else None
                        for m in markets]
    duplicate_ids = {
        "market_ids": sorted({v for v in market_id_values if v and market_id_values.count(v) > 1}),
        "material_ids": [],
        "requirement_ids": [],
        "fee_ids": [],
        "equipment_ids": [],
        "product_ids": [],
    }
    for mid_key, id_key, store in (
        ("vendor_materials", "material_id", "material_ids"),
        ("products", "product_id", "product_ids"),
        ("equipment", "equipment_id", "equipment_ids"),
    ):
        raw = doc.get(mid_key) if isinstance(doc.get(mid_key), list) else []
        dup = duplicates([r for r in raw if isinstance(r, dict)], id_key)
        duplicate_ids[store] = [{"id": d, "occurrences": sum(1 for r in raw if isinstance(r, dict) and r.get(id_key) == d)} for d in dup]
    for market in markets:
        if not isinstance(market, dict):
            continue
        mid = normalize_text(market.get("market_id")) if isinstance(market.get("market_id"), str) else None
        for src, id_key in (("requirements", "req_id"), ("fees", "fee_id")):
            raw = market.get(src) if isinstance(market.get(src), list) else []
            dup = duplicates([r for r in raw if isinstance(r, dict)], id_key)
            for d in dup:
                duplicate_ids["requirement_ids" if src == "requirements" else "fee_ids"].append(
                    {"market_id": mid, "id": d,
                     "occurrences": sum(1 for r in raw if isinstance(r, dict) and r.get(id_key) == d)})
    if duplicate_ids["material_ids"]:
        ask("DUPLICATE_MATERIAL_ID", "材料编号重复：%s，请确认以哪一条为准。"
            % safe(", ".join(d["id"] for d in duplicate_ids["material_ids"])))
    for d in duplicate_ids["requirement_ids"]:
        ask("DUPLICATE_REQUIREMENT_ID", "市集「%s」的材料要求编号 %s 重复出现 %d 次，请去重。"
            % (d["market_id"], safe(d["id"]), d["occurrences"]))
    for d in duplicate_ids["fee_ids"]:
        ask("DUPLICATE_FEE_ID", "市集「%s」的费用编号 %s 重复出现 %d 次，请去重。"
            % (d["market_id"], safe(d["id"]), d["occurrences"]))
    for d in duplicate_ids["market_ids"]:
        ask("DUPLICATE_MARKET_ID", "市集编号 %s 重复，请确认是否为同一场次。" % safe(d))
    if unknown_category_ids:
        ask("PRODUCT_CATEGORY",
            "商品 %s 未标注 FOOD / NON_FOOD（共 %d 个），食品相关要求无法判定：在补齐类别之前，"
            "所有 applies_to=FOOD 的要求都不会被当作「不适用」放行。" % (
                safe(", ".join(str(x) for x in unknown_category_ids)), unknown_category_count))
    if not questions:
        if not market_rows:
            ask("MARKETS_NOT_PROVIDED", "未提供任何市集场次，请至少提供一个主办方招募规则。")
        else:
            ask("NONE", "未发现必须澄清的问题，可按提交检查表准备。")

    # ---- fees summary -------------------------------------------------------
    fee_summary = {
        "by_currency": {k: {"total": money(v["total"]), "count": v["count"]} for k, v in sorted(fee_by_currency.items())},
        "refundable_by_currency": {k: money(v) for k, v in sorted(refundable_by_currency.items())},
        "fee_line_count": fee_count,
        "invalid_fee_count": len(invalid_fees),
        "invalid_fees": sorted(invalid_fees, key=lambda f: (f["market_id"] or "", f["fee_id"] or "")),
        "cross_currency_total": None,
        "note": "不同币种不合并、不折算；cross_currency_total 恒为 null；金额缺失、非法或为负数的费用只回显不计入，"
                "计入 invalid_fee_count，且不进入任何合计。",
    }
    non_refundable = {}
    for cur, bucket in fee_by_currency.items():
        refund = refundable_by_currency.get(cur, D(0))
        non_refundable[cur] = money(bucket["total"] - refund)
    fee_summary["non_refundable_by_currency"] = {k: non_refundable[k] for k in sorted(non_refundable)}

    # ---- status -------------------------------------------------------------
    status_counts = {}
    for row in market_rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    if any(s in ("DEADLINE_PASSED", "NOT_ELIGIBLE") for s in status_counts):
        status = "BLOCKED"
    elif status_counts and set(status_counts) == {"READY_TO_APPLY"}:
        status = "READY"
    else:
        status = "REVIEW_REQUIRED"

    # ---- checklist ----------------------------------------------------------
    checklist = [
        {"topic": "RULES", "action": "把主办方招募规则原文与本文的资格匹配逐条对照，缺失规则先向主办方确认。"},
        {"topic": "MATERIALS", "action": "按每个市集补齐必填材料，并确认报名当天材料仍在有效期内。"},
        {"topic": "FOOD", "action": "食品类摊位单独确认食品经营许可、健康证与现场卫生要求；非食品要求不要混用。"},
        {"topic": "POWER", "action": "按你提供的设备清单核对用电总量与摊位上限，未知功率设备补齐后再计算。"},
        {"topic": "FEES", "action": "分币种核对费用与押金，确认退款条件；本工具不代付款。"},
        {"topic": "DEADLINE", "action": "确认报名截止时间的时区，并在截止前留出材料补正时间。"},
        {"topic": "SUBMIT", "action": "本工具不代填、不代提交、不保证录取；请在主办方渠道人工提交。"},
    ]

    # ---- markdown -----------------------------------------------------------
    lines = []
    lines.append("# 市集摊主申请准备包")
    lines.append("")
    lines.append("> 基准时间 as_of：%s ｜ 状态：**%s** ｜ 场次 %d 个" % (safe(as_of_raw), status, len(market_rows)))
    lines.append("")
    lines.append("## 1. 摊主资料")
    lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("| --- | --- |")
    for key in ("vendor_ref", "brand_name", "category", "contact_ref", "city"):
        value = normalize_text(vendor.get(key)) if isinstance(vendor.get(key), str) else None
        lines.append("| %s | %s |" % (safe(key), safe(value)))
    lines.append("| 商品 | 食品 %d 个 / 非食品 %d 个 / 未标注类别 %d 个 |" % (
        len(food_products), len(non_food_products), unknown_category_count))
    lines.append("")
    lines.append("## 2. 场次资格与状态")
    lines.append("")
    lines.append("| 市集 | 截止状态 | 剩余天数 | 状态 | 缺口/提示 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in market_rows:
        extras = row["review_flags"] + row["notices"]
        lines.append("| %s | %s | %s | %s | %s |" % (
            safe(row["name"]), row["deadline_state"],
            "未知" if row["remaining_days"] is None else row["remaining_days"],
            row["status"], safe(", ".join(extras) or "无")))
    lines.append("")
    lines.append("## 3. 逐场资格匹配")
    for row in market_rows:
        lines.append("")
        lines.append("### %s（%s）" % (safe(row["name"]), safe(row["market_id"])))
        lines.append("")
        lines.append("| 要求 | 类型 | 必填 | 状态 | 证据 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for e in row["eligibility"]:
            lines.append("| %s | %s | %s | %s | %s |" % (
                safe(e["label"]), safe(e["type"]), "是" if e["mandatory"] else "否",
                e["state"], safe(e["evidence"])))
        lines.append("")
        b = row["booth"]
        lines.append("- 用电：需 %d W / 上限 %s W → **%s**（未知功率设备：%s）" % (
            b["power_required_watts"], b["power_limit_watts"] if b["power_limit_watts"] is not None else "未提供",
            b["power_state"], safe(", ".join(str(x) for x in b["power_unknown_equipment"]) or "无")))
        lines.append("- 摊位：申请 %s 个 / 上限 %s 个；尺寸 %s；户外 %s" % (
            b["requested_count"], b["max_count"] if b["max_count"] is not None else "未提供",
            safe(b["size"] or "未提供"), ("是" if b["outdoor"] else "否") if b["outdoor"] is not None else "未提供"))
        if row["fees"]:
            fee_text = "; ".join("%s %s %s%s" % (safe(f["label"]), f["amount"], safe(f["currency"]),
                                                 "（可退）" if f["refundable"] else "（不退）") for f in row["fees"])
            lines.append("- 费用：%s" % fee_text)
        if row["timeline"]:
            lines.append("- 时间线：")
            for t in row["timeline"]:
                lines.append("  - %s：%s（时区偏移 %s，剩余 %s 天）" % (
                    t["stage"], t["at"], t.get("local_offset") or "未提供",
                    t["remaining_days"] if t["remaining_days"] is not None else "未知"))
    lines.append("")
    lines.append("## 4. 分币种费用回显")
    lines.append("")
    lines.append("| 币种 | 合计 | 可退 | 不可退 | 条数 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for cur in fee_summary["by_currency"]:
        lines.append("| %s | %s | %s | %s | %d |" % (
            safe(cur), fee_summary["by_currency"][cur]["total"],
            fee_summary["refundable_by_currency"].get(cur, "0.00"),
            fee_summary["non_refundable_by_currency"].get(cur, "0.00"),
            fee_summary["by_currency"][cur]["count"]))
    lines.append("")
    lines.append("不同币种不合并、不折算；本表没有跨币种总额。")
    if fee_summary["invalid_fees"]:
        lines.append("")
        lines.append("以下费用金额缺失、非法或为负数，**未计入**上表任何合计，请更正后再核对预算：")
        for item in fee_summary["invalid_fees"]:
            lines.append("- %s / %s（%s）：原值 %s" % (
                safe(item["market_id"]), safe(item["label"]), safe(item["fee_id"]), safe(item["amount_raw"])))
    lines.append("")
    lines.append("## 5. 待主办方/自我确认问题")
    lines.append("")
    for q in questions:
        prefix = "（%s）" % safe(q["market_id"]) if q.get("market_id") else ""
        lines.append("- %s %s%s" % (q["id"], prefix, safe(q["question"])))
    lines.append("")
    lines.append("## 6. 人工提交检查表")
    lines.append("")
    for item in checklist:
        lines.append("- [ ] %s：%s" % (safe(item["topic"]), safe(item["action"])))
    lines.append("")
    lines.append("## 7. 边界")
    lines.append("")
    lines.append("本工具只读输入、离线计算：不代填或提交表单、不付款、不保证录取、不编造许可证/资质/销量，不提供食品安全或法律结论；规则缺失时保持未知。")
    if injection_flagged:
        lines.append("输入中检测到疑似提示注入文本，已按不可信数据处理并隐藏（位置：%s）。" % safe(", ".join(injection_flagged)))
    markdown = "\n".join(lines) + "\n"

    return {
        "skill": SKILL,
        "version": VERSION,
        "as_of": as_of_raw,
        "status": status,
        "status_counts": {k: status_counts[k] for k in sorted(status_counts)},
        "market_count": len(market_rows),
        "vendor_card": {
            "vendor_ref": normalize_text(vendor.get("vendor_ref")) if isinstance(vendor.get("vendor_ref"), str) else None,
            "brand_name": normalize_text(vendor.get("brand_name")) if isinstance(vendor.get("brand_name"), str) else None,
            "category": normalize_text(vendor.get("category")) if isinstance(vendor.get("category"), str) else None,
            "contact_ref": normalize_text(vendor.get("contact_ref")) if isinstance(vendor.get("contact_ref"), str) else None,
            "city": normalize_text(vendor.get("city")) if isinstance(vendor.get("city"), str) else None,
            "intro": normalize_text(vendor.get("intro")) if isinstance(vendor.get("intro"), str) else None,
            "intro_is_draft": True,
            "food_product_count": len(food_products),
            "non_food_product_count": len(non_food_products),
            "unknown_category_product_count": unknown_category_count,
            "unknown_category_product_ids": list(unknown_category_ids),
        },
        "markets": market_rows,
        "fee_summary": fee_summary,
        "duplicate_ids": duplicate_ids,
        "injection_flagged": injection_flagged,
        "clarification_questions": questions,
        "submission_checklist": checklist,
        "markdown_summary": markdown,
        "disclaimer": "本输出为市集申请准备材料，不构成法律、食品安全或资质结论；主办方规则、录取结果与费用以主办方原文与账户实况为准。",
    }


# Public engine entry point used by the batch tests.
analyse = build_output


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: run.py <input.json>\n")
        return 2
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as exc:
        json.dump({"skill": SKILL, "version": VERSION, "status": "REJECTED",
                   "rejected_reason": "INPUT_UNREADABLE", "detail": str(exc)}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if not isinstance(doc, dict):
        json.dump({"skill": SKILL, "version": VERSION, "status": "REJECTED",
                   "rejected_reason": "INPUT_NOT_OBJECT"}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    json.dump(build_output(doc), sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
