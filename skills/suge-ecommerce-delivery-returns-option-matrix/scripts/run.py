#!/usr/bin/env python3
"""电商配送与退货选项矩阵 — offline delivery & return option matrix builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes, no carrier lookup, no currency conversion, no checkout change, no
label generation and no refund approval: evidence references are handled as
file *basenames*.

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
# 上门 / 网点 / 自提 are the three delivery hand-over types. A value outside
# this list cannot be mapped onto any exact behaviour, so it is a structural
# error rather than an unknown.
PICKUP_TYPES = ("door", "pickup_point", "store_pickup")
DROP_OFF_TYPES = ("door_pickup", "dropoff_point", "mail_back")
FEE_PAYERS = ("seller", "buyer", "shared")
SURFACES = ("checkout", "product", "faq", "support")

RECORD_STATES = ("READY", "ACTION_NEEDED", "INSUFFICIENT_EVIDENCE", "BLOCKED")

DELIVERY_BLOCKER_ORDER = (
    "INVALID_DELIVERY_RECORD", "MISSING_REGION_SCOPE", "UNKNOWN_REGION",
    "CHANNEL_MISMATCH", "INVALID_PICKUP_TYPE", "INVALID_CUTOFF_TIME",
    "INVERTED_TIME_WINDOW", "NEGATIVE_TRANSIT_DAYS", "NEGATIVE_FEE",
    "CURRENCY_CONFLICT", "INVALID_EFFECTIVE_TIME", "INVALID_EFFECTIVE_WINDOW",
)
DELIVERY_UNKNOWN_ORDER = (
    "MISSING_FEE", "MISSING_CURRENCY", "MISSING_TRANSIT_TIME",
    "UNKNOWN_TRACKING", "MISSING_PROVIDER", "NO_EVIDENCE_REF",
)
DELIVERY_ACTION_ORDER = ("OPTION_EXPIRED", "OPTION_NOT_YET_EFFECTIVE")

RETURN_BLOCKER_ORDER = (
    "INVALID_RETURN_RECORD", "MISSING_REGION_SCOPE", "UNKNOWN_REGION",
    "CHANNEL_MISMATCH", "INVALID_DROP_OFF_TYPE", "NEGATIVE_RETURN_WINDOW",
    "INVALID_EFFECTIVE_TIME", "INVALID_EFFECTIVE_WINDOW",
)
RETURN_UNKNOWN_ORDER = (
    "MISSING_RETURN_WINDOW", "UNKNOWN_FEE_PAYER", "MISSING_PROVIDER",
    "NO_EVIDENCE_REF",
)
RETURN_ACTION_ORDER = ("OPTION_EXPIRED", "OPTION_NOT_YET_EFFECTIVE")

PROMISE_BLOCKER_ORDER = (
    "PROMISE_UNKNOWN_REGION", "PROMISE_CURRENCY_CONFLICT",
    "PROMISE_TIME_CONFLICT", "PROMISE_FEE_CONFLICT",
    "PROMISE_RETURN_WINDOW_CONFLICT",
)

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
# the SAME sentence, so ordinary promise copy is not mislabelled.
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
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

DISCLAIMER = (
    "本输出是电商配送与退货选项的人工上线准备材料，不是承运商报价、时效承诺、"
    "结账页配置修改或法律/税务结论；所有结论只由输入中明示的事实推导，"
    "真实费率、时效与可用性必须由人工向承运商核实。"
)

NO_AUTOMATION_DECLARATION = (
    "本工具不联网查询任何承运商费率或时效、不自动选择承运商、不修改结账页或商品页、"
    "不生成任何物流面单、不批准退款、不做币种换算、不建议免费配送、不承诺销量；"
    "它只输出一份准备与一致性材料。"
)

HUMAN_CONFIRM_BASE = (
    "真实运费与时效必须由人工向承运商逐项核实，本工具不联网、不报价、不代下单",
    "结账页 / 商品页 / FAQ / 客服稿的对外承诺必须由人工决定并为之负责",
    "承运商接入、面单与取件安排属于人工操作，本工具不生成标签、不联系承运商",
    "退货窗口、退货运费承担方与退货条件必须由人工确认（其中可能涉及消费者保护法规）",
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


def read_decimal(value, allow_negative=False):
    """Optional decimal; None means "not provided", never 0."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str)):
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if parsed.is_nan() or parsed.is_infinite():
            return None
        if parsed < 0 and not allow_negative:
            return None
        return parsed
    return None


def money(value):
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def days_str(value):
    if value is None:
        return None
    if value == value.to_integral_value():
        return str(int(value))
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


def ref_list(raw):
    """Normalise an evidence reference field (singular or list) to (ok, name, reason)."""
    if raw is None:
        return []
    entries = raw if isinstance(raw, list) else [raw]
    out = []
    for entry in entries:
        if not has_text(entry):
            continue
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# envelopes
# --------------------------------------------------------------------------
def _blank_boards():
    return {
        "matrix": [],
        "delivery_options": [],
        "return_options": [],
        "promise_checks": [],
        "no_delivery_coverage": [],
        "no_return_path": [],
        "currency_conflicts": [],
        "inverted_time_windows": [],
        "inactive_options": [],
        "evidence_gaps": [],
        "promise_conflicts": [],
        "duplicate_ids": [],
        "requirement_gaps": [],
        "responsibility_fields": [],
        "human_checklist": [],
        "clarification_questions": [],
        "refused_refs": [],
        "injection_flagged": [],
        "input_warnings": [],
    }


def reject(hits):
    out = {
        "version": VERSION,
        "status": "REJECTED",
        "reason": "CREDENTIAL_DETECTED",
        "credential_findings": [{"path": h["path"], "reason": h["reason"]} for h in hits],
        "as_of": None,
        "as_of_date": None,
        "store": None,
        "region_count": 0,
        "delivery_option_count": 0,
        "return_option_count": 0,
        "promise_count": 0,
        "status_counts": {name: 0 for name in RECORD_STATES},
        "requirements_provided": False,
        "requirement_note": "未提供 requirements：本工具不内置行业默认，也不据此宣称是否满足客户期望。",
        "no_automation_declaration": NO_AUTOMATION_DECLARATION,
        "markdown_summary": (
            "# 电商配送与退货选项矩阵\n\n"
            "- 状态：**REJECTED**\n"
            "- 原因：输入疑似包含凭据（密钥 / 令牌 / 密码）。\n"
            "- 处理：已拒绝处理，**未回显**任何疑似凭据内容。\n\n"
            "> 请移除凭据字段后重新提交。本工具不需要也不需要保存任何登录凭据。\n"
        ),
        "disclaimer": DISCLAIMER,
    }
    out.update(_blank_boards())
    out["input_warnings"] = ["CREDENTIAL_DETECTED"]
    return out


def envelope(status, as_of_raw, injections, warnings):
    out = {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "as_of_date": None,
        "store": None,
        "region_count": 0,
        "delivery_option_count": 0,
        "return_option_count": 0,
        "promise_count": 0,
        "status_counts": {name: 0 for name in RECORD_STATES},
        "requirements_provided": False,
        "requirement_note": "未提供 requirements：本工具不内置行业默认，也不据此宣称是否满足客户期望。",
        "no_automation_declaration": NO_AUTOMATION_DECLARATION,
        "markdown_summary": (
            "# 电商配送与退货选项矩阵\n\n"
            "- 状态：**%s**\n"
            "- 原因：%s\n\n"
            "> 请补齐必填输入后重新提交。\n" % (status, "; ".join(warnings) or "输入不完整")
        ),
        "disclaimer": DISCLAIMER,
    }
    out.update(_blank_boards())
    out["injection_flagged"] = list(injections)
    out["input_warnings"] = list(warnings)
    return out


# --------------------------------------------------------------------------
# record normalisation
# --------------------------------------------------------------------------
def _effective(raw_from, raw_to):
    start = parse_dt(raw_from)
    end = parse_dt(raw_to)
    bad = any(raw is not None and parse_dt(raw) is None
              for raw in (raw_from, raw_to))
    return {
        "from_raw": clean_text(raw_from) if has_text(raw_from) else None,
        "to_raw": clean_text(raw_to) if has_text(raw_to) else None,
        "from_dt": start,
        "to_dt": end,
        "invalid_time": bad,
        "invalid_window": (start is not None and end is not None and start > end),
        "provided": raw_from is not None or raw_to is not None,
    }


def norm_delivery(raw, index, store_currency):
    path = "delivery_options[%d]" % index
    empty = {
        "option_id": None, "index": index, "path": path, "invalid": True,
        "regions": [], "channels": [], "provider": "", "service_level": "",
        "fee": None, "fee_provided": False, "currency": None,
        "min_days": None, "max_days": None, "cutoff": None,
        "tracking": None, "pickup_type": None, "evidence_refs": [],
        "refused_refs": [], "notes": "",
        "state": "BLOCKED", "blockers": ["INVALID_DELIVERY_RECORD"],
        "unknowns": [], "review_flags": [], "reasons": [],
    }
    if not isinstance(raw, dict):
        return empty

    regions = [clean_text(r) for r in raw.get("regions", [])] \
        if isinstance(raw.get("regions"), list) else []
    regions = [r for r in regions if r]
    channels = [clean_text(c) for c in raw.get("channels", [])] \
        if isinstance(raw.get("channels"), list) else []
    channels = [c for c in channels if c]

    fee_raw = raw.get("fee")
    fee = read_decimal(fee_raw, allow_negative=True)
    fee_provided = fee is not None
    currency = clean_text(raw.get("currency")).upper() or None

    min_days = read_decimal(raw.get("min_days"), allow_negative=True)
    max_days = read_decimal(raw.get("max_days"), allow_negative=True)

    cutoff_raw = raw.get("cutoff_time")
    cutoff = clean_text(cutoff_raw) if has_text(cutoff_raw) else None

    pickup_raw = clean_text(raw.get("pickup_type")).lower()
    if not pickup_raw:
        pickup_type = None
        pickup_state = "NOT_PROVIDED"
    elif pickup_raw in PICKUP_TYPES:
        pickup_type = pickup_raw
        pickup_state = "KNOWN"
    else:
        pickup_type = pickup_raw
        pickup_state = "INVALID"

    refs_raw = ref_list(raw.get("evidence_refs") if "evidence_refs" in raw
                        else raw.get("evidence_ref"))
    evidence_refs = []
    refused = []
    for offset, entry in enumerate(refs_raw):
        ok, base, reason = safe_basename(entry)
        if ok:
            evidence_refs.append(base)
        else:
            refused.append({"path": "%s/evidence_refs[%d]" % (path, offset),
                            "reason": reason})
            if base:
                evidence_refs.append(base)

    eff = _effective(raw.get("effective_from"), raw.get("effective_to"))

    return {
        "option_id": clean_text(raw.get("option_id")) or None,
        "index": index, "path": path, "invalid": False,
        "regions": regions, "channels": channels,
        "provider": clean_text(raw.get("provider")),
        "service_level": clean_text(raw.get("service_level")),
        "fee": fee, "fee_provided": fee_provided, "currency": currency,
        "min_days": min_days, "max_days": max_days,
        "cutoff": cutoff,
        "tracking": read_bool(raw.get("tracking")),
        "pickup_type": pickup_type, "pickup_state": pickup_state,
        "evidence_refs": evidence_refs, "refused_refs": refused,
        "notes": clean_text(raw.get("notes")),
        "_effective": eff, "_store_currency": store_currency,
        "state": "READY", "blockers": [], "unknowns": [],
        "review_flags": [], "reasons": [],
    }


def norm_return(raw, index):
    path = "return_options[%d]" % index
    empty = {
        "return_id": None, "index": index, "path": path, "invalid": True,
        "regions": [], "channels": [], "provider": "", "service_level": "",
        "window_days": None, "fee_payer": None, "dropoff_type": None,
        "condition_scope": "", "evidence_refs": [], "refused_refs": [],
        "notes": "",
        "state": "BLOCKED", "blockers": ["INVALID_RETURN_RECORD"],
        "unknowns": [], "review_flags": [], "reasons": [],
    }
    if not isinstance(raw, dict):
        return empty

    regions = [clean_text(r) for r in raw.get("regions", [])] \
        if isinstance(raw.get("regions"), list) else []
    regions = [r for r in regions if r]
    channels = [clean_text(c) for c in raw.get("channels", [])] \
        if isinstance(raw.get("channels"), list) else []
    channels = [c for c in channels if c]

    window_days = read_decimal(raw.get("window_days"), allow_negative=True)

    payer_raw = clean_text(raw.get("fee_payer")).lower()
    if not payer_raw:
        payer_state = "NOT_PROVIDED"
    elif payer_raw in FEE_PAYERS:
        payer_state = "KNOWN"
    else:
        payer_state = "INVALID"
    # Only a recognised payer is exposed; an off-vocabulary value stays unknown.
    fee_payer = payer_raw if payer_state == "KNOWN" else None

    drop_raw = clean_text(raw.get("dropoff_type")).lower()
    if not drop_raw:
        dropoff_type = None
        drop_state = "NOT_PROVIDED"
    elif drop_raw in DROP_OFF_TYPES:
        dropoff_type = drop_raw
        drop_state = "KNOWN"
    else:
        dropoff_type = drop_raw
        drop_state = "INVALID"

    refs_raw = ref_list(raw.get("evidence_refs") if "evidence_refs" in raw
                        else raw.get("evidence_ref"))
    evidence_refs = []
    refused = []
    for offset, entry in enumerate(refs_raw):
        ok, base, reason = safe_basename(entry)
        if ok:
            evidence_refs.append(base)
        else:
            refused.append({"path": "%s/evidence_refs[%d]" % (path, offset),
                            "reason": reason})
            if base:
                evidence_refs.append(base)

    eff = _effective(raw.get("effective_from"), raw.get("effective_to"))

    return {
        "return_id": clean_text(raw.get("return_id")) or None,
        "index": index, "path": path, "invalid": False,
        "regions": regions, "channels": channels,
        "provider": clean_text(raw.get("provider")),
        "window_days": window_days, "fee_payer": fee_payer,
        "fee_payer_state": payer_state,
        "dropoff_type": dropoff_type, "dropoff_state": drop_state,
        "condition_scope": clean_text(raw.get("condition_scope")),
        "evidence_refs": evidence_refs, "refused_refs": refused,
        "notes": clean_text(raw.get("notes")),
        "_effective": eff,
        "state": "READY", "blockers": [], "unknowns": [],
        "review_flags": [], "reasons": [],
    }


# --------------------------------------------------------------------------
# classification — delivery options
# --------------------------------------------------------------------------
def classify_delivery(options, region_channels, as_of_dt):
    for item in options:
        if item["invalid"]:
            continue
        blockers = []
        if not item["regions"]:
            blockers.append("MISSING_REGION_SCOPE")
        else:
            for region_id in item["regions"]:
                if region_id not in region_channels:
                    blockers.append("UNKNOWN_REGION")
                    break
            else:
                if item["channels"]:
                    allowed = set()
                    for region_id in item["regions"]:
                        allowed |= set(region_channels[region_id])
                    if any(c not in allowed for c in item["channels"]):
                        blockers.append("CHANNEL_MISMATCH")
        if item["pickup_state"] == "INVALID":
            blockers.append("INVALID_PICKUP_TYPE")
        if item["cutoff"] is not None and not TIME_RE.match(item["cutoff"]):
            blockers.append("INVALID_CUTOFF_TIME")
        if item["min_days"] is not None and item["max_days"] is not None \
                and item["min_days"] > item["max_days"]:
            blockers.append("INVERTED_TIME_WINDOW")
        if (item["min_days"] is not None and item["min_days"] < 0) \
                or (item["max_days"] is not None and item["max_days"] < 0):
            blockers.append("NEGATIVE_TRANSIT_DAYS")
        if item["fee_provided"] and item["fee"] is not None and item["fee"] < 0:
            blockers.append("NEGATIVE_FEE")
        store_currency = item.get("_store_currency")
        if item["currency"] and store_currency and item["currency"] != store_currency:
            blockers.append("CURRENCY_CONFLICT")
        eff = item["_effective"]
        if eff["invalid_time"]:
            blockers.append("INVALID_EFFECTIVE_TIME")
        if eff["invalid_window"]:
            blockers.append("INVALID_EFFECTIVE_WINDOW")
        if blockers:
            item["blockers"] = [c for c in DELIVERY_BLOCKER_ORDER if c in blockers]
            item["state"] = "BLOCKED"
            item["reasons"].append("输入存在无法直接上线的硬矛盾，必须先人工修正")
            continue

        unknowns = []
        if not item["fee_provided"]:
            unknowns.append("MISSING_FEE")
        if item["fee_provided"] and not item["currency"]:
            unknowns.append("MISSING_CURRENCY")
        if item["min_days"] is None or item["max_days"] is None:
            unknowns.append("MISSING_TRANSIT_TIME")
        if item["tracking"] is None:
            unknowns.append("UNKNOWN_TRACKING")
        if not item["provider"]:
            unknowns.append("MISSING_PROVIDER")
        if not item["evidence_refs"]:
            unknowns.append("NO_EVIDENCE_REF")
        if unknowns:
            item["unknowns"] = [c for c in DELIVERY_UNKNOWN_ORDER if c in unknowns]
            item["state"] = "INSUFFICIENT_EVIDENCE"
            item["reasons"].append("缺少明示事实，必须先追问而不是替你补齐")
            continue

        actions = []
        eff = item["_effective"]
        if eff["to_dt"] is not None and as_of_dt is not None and eff["to_dt"] < as_of_dt:
            actions.append("OPTION_EXPIRED")
        if eff["from_dt"] is not None and as_of_dt is not None and eff["from_dt"] > as_of_dt:
            actions.append("OPTION_NOT_YET_EFFECTIVE")
        if not eff["provided"]:
            item["review_flags"].append("NO_EFFECTIVE_WINDOW")
        item["review_flags"] = [c for c in DELIVERY_ACTION_ORDER if c in actions] \
            + item["review_flags"]
        if actions:
            item["state"] = "ACTION_NEEDED"
            if "OPTION_EXPIRED" in actions:
                item["reasons"].append("选项生效期已过期：需人工决定续期或下架")
            if "OPTION_NOT_YET_EFFECTIVE" in actions:
                item["reasons"].append("选项尚未生效：上线前必须确认生效时间")
            continue
        item["state"] = "READY"


# --------------------------------------------------------------------------
# classification — return options
# --------------------------------------------------------------------------
def classify_return(options, region_channels, as_of_dt):
    for item in options:
        if item["invalid"]:
            continue
        blockers = []
        if not item["regions"]:
            blockers.append("MISSING_REGION_SCOPE")
        else:
            for region_id in item["regions"]:
                if region_id not in region_channels:
                    blockers.append("UNKNOWN_REGION")
                    break
            else:
                if item["channels"]:
                    allowed = set()
                    for region_id in item["regions"]:
                        allowed |= set(region_channels[region_id])
                    if any(c not in allowed for c in item["channels"]):
                        blockers.append("CHANNEL_MISMATCH")
        if item["dropoff_state"] == "INVALID":
            blockers.append("INVALID_DROP_OFF_TYPE")
        if item["window_days"] is not None and item["window_days"] < 0:
            blockers.append("NEGATIVE_RETURN_WINDOW")
        eff = item["_effective"]
        if eff["invalid_time"]:
            blockers.append("INVALID_EFFECTIVE_TIME")
        if eff["invalid_window"]:
            blockers.append("INVALID_EFFECTIVE_WINDOW")
        if blockers:
            item["blockers"] = [c for c in RETURN_BLOCKER_ORDER if c in blockers]
            item["state"] = "BLOCKED"
            item["reasons"].append("退货选项存在无法直接上线的硬矛盾，必须先人工修正")
            continue

        unknowns = []
        if item["window_days"] is None:
            unknowns.append("MISSING_RETURN_WINDOW")
        # A missing payer and an off-vocabulary payer are both "unknown": neither
        # may be silently read as "the seller pays".
        if item["fee_payer_state"] in ("NOT_PROVIDED", "INVALID"):
            unknowns.append("UNKNOWN_FEE_PAYER")
        if not item["provider"]:
            unknowns.append("MISSING_PROVIDER")
        if not item["evidence_refs"]:
            unknowns.append("NO_EVIDENCE_REF")
        if unknowns:
            item["unknowns"] = [c for c in RETURN_UNKNOWN_ORDER if c in unknowns]
            item["state"] = "INSUFFICIENT_EVIDENCE"
            item["reasons"].append("缺少明示事实，必须先追问而不是替你补齐")
            continue

        actions = []
        eff = item["_effective"]
        if eff["to_dt"] is not None and as_of_dt is not None and eff["to_dt"] < as_of_dt:
            actions.append("OPTION_EXPIRED")
        if eff["from_dt"] is not None and as_of_dt is not None and eff["from_dt"] > as_of_dt:
            actions.append("OPTION_NOT_YET_EFFECTIVE")
        if not eff["provided"]:
            item["review_flags"].append("NO_EFFECTIVE_WINDOW")
        item["review_flags"] = [c for c in RETURN_ACTION_ORDER if c in actions] \
            + item["review_flags"]
        if actions:
            item["state"] = "ACTION_NEEDED"
            if "OPTION_EXPIRED" in actions:
                item["reasons"].append("退货选项生效期已过期：需人工决定续期或下架")
            if "OPTION_NOT_YET_EFFECTIVE" in actions:
                item["reasons"].append("退货选项尚未生效：上线前必须确认生效时间")
            continue
        item["state"] = "READY"


# --------------------------------------------------------------------------
# matrix / promise / requirement analysis
# --------------------------------------------------------------------------
def _applies(record, region_id, channel):
    if region_id not in record["regions"]:
        return False
    if record["channels"] and channel not in record["channels"]:
        return False
    return True


def build_matrix(regions, delivery, returns, region_blocked):
    matrix = []
    status_counts = {name: 0 for name in RECORD_STATES}
    for region in regions:
        for channel in region["channels"]:
            deliveries = [d for d in delivery if _applies(d, region["region_id"], channel)]
            rets = [r for r in returns if _applies(r, region["region_id"], channel)]
            findings = []
            if region["region_id"] in region_blocked:
                findings.append("PROMISE_CONFLICT")
            state = "READY"
            if any(d["state"] == "BLOCKED" for d in deliveries) \
                    or any(r["state"] == "BLOCKED" for r in rets) or findings:
                state = "BLOCKED"
            elif not deliveries:
                findings.append("NO_DELIVERY_COVERAGE")
                state = "INSUFFICIENT_EVIDENCE"
            elif not rets:
                findings.append("NO_RETURN_PATH")
                state = "INSUFFICIENT_EVIDENCE"
            elif any(d["state"] == "INSUFFICIENT_EVIDENCE" for d in deliveries) \
                    or any(r["state"] == "INSUFFICIENT_EVIDENCE" for r in rets):
                state = "INSUFFICIENT_EVIDENCE"
            elif any(d["state"] == "ACTION_NEEDED" for d in deliveries) \
                    or any(r["state"] == "ACTION_NEEDED" for r in rets):
                state = "ACTION_NEEDED"
            status_counts[state] += 1
            matrix.append({
                "region_id": region["region_id"],
                "channel": channel,
                "delivery_option_ids": sorted(d["option_id"] or d["path"] for d in deliveries),
                "return_option_ids": sorted(r["return_id"] or r["path"] for r in rets),
                "state": state,
                "findings": findings,
            })
    matrix.sort(key=lambda row: (row["region_id"], row["channel"]))
    return matrix, status_counts


def build_promise_checks(promises, region_channels, delivery, returns, store_currency):
    checks = []
    region_blocked = set()
    for index, raw in enumerate(promises):
        path = "promises[%d]" % index
        if not isinstance(raw, dict):
            checks.append({
                "promise_id": None, "path": path, "surface": None,
                "region_id": None, "state": "BLOCKED",
                "conflicts": ["PROMISE_UNKNOWN_REGION"], "text": "",
            })
            continue
        promise_id = clean_text(raw.get("promise_id")) or None
        surface = clean_text(raw.get("surface")).lower() or None
        region_id = clean_text(raw.get("region_id")) or None
        text = clean_text(raw.get("text"))
        conflicts = []
        if region_id is None or region_id not in region_channels:
            conflicts.append("PROMISE_UNKNOWN_REGION")
        else:
            scoped_delivery = [d for d in delivery
                               if region_id in d["regions"] and not d["invalid"]]
            scoped_returns = [r for r in returns
                              if region_id in r["regions"] and not r["invalid"]]
            stated_min = read_decimal(raw.get("stated_min_days"), allow_negative=True)
            stated_max = read_decimal(raw.get("stated_max_days"), allow_negative=True)
            if stated_min is not None or stated_max is not None:
                pmin = stated_min if stated_min is not None else Decimal("0")
                pmax = stated_max if stated_max is not None else pmin
                known = [d for d in scoped_delivery
                         if d["min_days"] is not None and d["max_days"] is not None]
                if known and not any(d["max_days"] >= pmin and d["min_days"] <= pmax
                                     for d in known):
                    conflicts.append("PROMISE_TIME_CONFLICT")
            stated_fee = read_decimal(raw.get("stated_fee"), allow_negative=True)
            if stated_fee is not None:
                known_fee = [d for d in scoped_delivery if d["fee_provided"]
                             and d["fee"] is not None]
                if known_fee and all(d["fee"] > stated_fee for d in known_fee):
                    conflicts.append("PROMISE_FEE_CONFLICT")
            stated_currency = clean_text(raw.get("stated_currency")).upper() or None
            if stated_currency and store_currency and stated_currency != store_currency:
                conflicts.append("PROMISE_CURRENCY_CONFLICT")
            stated_window = read_decimal(raw.get("stated_return_window_days"),
                                         allow_negative=True)
            if stated_window is not None:
                known_window = [r for r in scoped_returns if r["window_days"] is not None]
                if known_window and all(r["window_days"] < stated_window
                                        for r in known_window):
                    conflicts.append("PROMISE_RETURN_WINDOW_CONFLICT")
        conflicts = [c for c in PROMISE_BLOCKER_ORDER if c in conflicts]
        state = "BLOCKED" if conflicts else "CONSISTENT"
        if conflicts and region_id:
            region_blocked.add(region_id)
        checks.append({
            "promise_id": promise_id, "path": path, "surface": surface,
            "region_id": region_id, "state": state,
            "conflicts": conflicts, "text": text,
        })
    checks.sort(key=lambda row: (row["region_id"] or "", row["promise_id"] or "",
                                 row["path"]))
    return checks, region_blocked


def build_requirement_gaps(requirements, has_requirements, delivery, returns,
                           region_channels, store_currency):
    gaps = []
    if not has_requirements:
        return gaps
    for index, raw in enumerate(requirements):
        if not isinstance(raw, dict):
            gaps.append({"path": "requirements[%d]" % index,
                         "region_id": None, "findings": ["REQ_INVALID_RECORD"]})
            continue
        requirement_id = clean_text(raw.get("requirement_id")) or None
        region_id = clean_text(raw.get("region_id")) or None
        channel = clean_text(raw.get("channel")) or None
        findings = []
        if region_id is None or region_id not in region_channels:
            findings.append("REQ_UNKNOWN_REGION")
        else:
            scoped_delivery = [d for d in delivery
                               if _applies(d, region_id, channel)
                               and d["state"] != "BLOCKED" and not d["invalid"]] \
                if channel else [d for d in delivery
                                 if region_id in d["regions"] and not d["invalid"]]
            scoped_returns = [r for r in returns
                              if _applies(r, region_id, channel)
                              and r["state"] != "BLOCKED" and not r["invalid"]] \
                if channel else [r for r in returns
                                 if region_id in r["regions"] and not r["invalid"]]
            max_days = read_decimal(raw.get("max_days"), allow_negative=True)
            if max_days is not None:
                known = [d for d in scoped_delivery if d["max_days"] is not None]
                if not known:
                    findings.append("REQ_DELIVERY_TIME_UNVERIFIABLE")
                elif all(d["max_days"] > max_days for d in known):
                    findings.append("REQ_DELIVERY_TIME_NOT_MET")
            max_fee = read_decimal(raw.get("max_fee"), allow_negative=True)
            if max_fee is not None:
                known_fee = [d for d in scoped_delivery if d["fee_provided"]
                             and d["fee"] is not None]
                if not known_fee:
                    findings.append("REQ_DELIVERY_FEE_UNVERIFIABLE")
                elif all(d["fee"] > max_fee for d in known_fee):
                    findings.append("REQ_DELIVERY_FEE_NOT_MET")
            min_window = read_decimal(raw.get("min_return_window_days"),
                                      allow_negative=True)
            if min_window is not None:
                known_window = [r for r in scoped_returns if r["window_days"] is not None]
                if not known_window:
                    findings.append("REQ_RETURN_WINDOW_UNVERIFIABLE")
                elif all(r["window_days"] < min_window for r in known_window):
                    findings.append("REQ_RETURN_WINDOW_NOT_MET")
            currency = clean_text(raw.get("currency")).upper() or None
            if currency and store_currency and currency != store_currency:
                findings.append("REQ_CURRENCY_MISMATCH")
        gaps.append({
            "requirement_id": requirement_id,
            "path": "requirements[%d]" % index,
            "region_id": region_id, "channel": channel,
            "findings": findings,
        })
    gaps.sort(key=lambda row: (row["region_id"] or "", row["requirement_id"] or "",
                               row["path"]))
    return gaps


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def render_markdown(status, as_of, store, matrix, delivery, returns, promise_checks,
                    counts, coverage_gaps, currency_conflicts, inverted,
                    inactive, evidence_gaps, promise_conflicts, requirements_note,
                    requirement_gaps, human_checklist, questions, warnings):
    lines = []
    lines.append("# 电商配送与退货选项矩阵")
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    if store:
        lines.append("- 店铺：%s" % (hidden(store.get("name")) or "未提供"))
        lines.append("- 默认币种：%s" % (esc(store.get("default_currency")) or "未提供"))
        lines.append("- 销售渠道：%s" % (
            esc("、".join(store.get("sales_channels", []))) or "未提供"))
    lines.append("- 区域数：%d ／ 配送选项：%d ／ 退货选项：%d ／ 承诺：%d"
                 % (len({row["region_id"] for row in matrix}), len(delivery),
                    len(returns), len(promise_checks)))
    lines.append("")
    lines.append("## 区域 × 渠道矩阵")
    lines.append("")
    lines.append("| 区域 | 渠道 | 配送选项 | 退货选项 | 结论 | 说明 |")
    lines.append("|---|---|---|---|---|---|")
    for row in matrix:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            esc(row["region_id"]), esc(row["channel"]),
            esc("、".join(row["delivery_option_ids"])) or "无",
            esc("、".join(row["return_option_ids"])) or "无",
            row["state"], esc("、".join(row["findings"])) or "—"))
    lines.append("")
    lines.append("| 结论 | 单元格数 |")
    lines.append("|---|---:|")
    for name in RECORD_STATES:
        lines.append("| %s | %d |" % (name, counts[name]))
    lines.append("")

    lines.append("## 配送选项")
    lines.append("")
    lines.append("| 选项 | 区域 | 渠道 | 服务 | 费用 | 时效 | 自提方式 | 追踪 | 结论 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for item in delivery:
        fee_display = ("%s %s" % (money(item["fee"]), item["currency"] or "（币种未提供）")
                       if item["fee_provided"] else "未提供")
        transit = "%s–%s 天" % (days_str(item["min_days"]) if item["min_days"] is not None
                                else "?", days_str(item["max_days"])
                                if item["max_days"] is not None else "?")
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            esc(item["option_id"] or item["path"]),
            esc("、".join(item["regions"])) or "未提供",
            esc("、".join(item["channels"])) or "全部",
            hidden(item["service_level"]) or "未提供",
            esc(fee_display), esc(transit),
            esc(item["pickup_type"]) or "未提供",
            {True: "有", False: "无", None: "未知"}[item["tracking"]],
            item["state"]))
    lines.append("")

    lines.append("## 退货选项")
    lines.append("")
    lines.append("| 选项 | 区域 | 渠道 | 退货窗 | 运费承担 | 退货方式 | 结论 |")
    lines.append("|---|---|---|---|---|---|---|")
    for item in returns:
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            esc(item["return_id"] or item["path"]),
            esc("、".join(item["regions"])) or "未提供",
            esc("、".join(item["channels"])) or "全部",
            ("%s 天" % days_str(item["window_days"])) if item["window_days"] is not None
            else "未提供",
            esc(item["fee_payer"]) or "未知",
            esc(item["dropoff_type"]) or "未提供",
            item["state"]))
    lines.append("")

    def _bullets(rows, fmt):
        if not rows:
            return ["- 无"]
        return ["- " + fmt(row) for row in rows]

    lines.append("## 无配送覆盖")
    lines.append("")
    lines.extend(_bullets(coverage_gaps, lambda r: "`%s` / `%s`：%s"
                          % (esc(r["region_id"]), esc(r["channel"]), r["kind"])))
    lines.append("")

    lines.append("## 无退货路径")
    lines.append("")
    lines.extend(_bullets([g for g in coverage_gaps if g["kind"] == "NO_RETURN_PATH"],
                          lambda r: "`%s` / `%s`" % (esc(r["region_id"]), esc(r["channel"]))))
    lines.append("")

    lines.append("## 币种不一致")
    lines.append("")
    lines.extend(_bullets(currency_conflicts, lambda r: "`%s`：%s ≠ 默认币种 %s"
                          % (esc(r["id"]), esc(r["currency"]), esc(r["store_currency"]))))
    lines.append("")

    lines.append("## 时效倒置")
    lines.append("")
    lines.extend(_bullets(inverted, lambda r: "`%s`：min %s > max %s"
                          % (esc(r["id"]), esc(r["min_days"]), esc(r["max_days"]))))
    lines.append("")

    lines.append("## 失效 / 未生效选项")
    lines.append("")
    lines.extend(_bullets(inactive, lambda r: "`%s`：%s" % (esc(r["id"]), r["kind"])))
    lines.append("")

    lines.append("## 缺证据")
    lines.append("")
    lines.extend(_bullets(evidence_gaps, lambda r: "`%s`：%s"
                          % (esc(r["id"]), esc("、".join(r["unknowns"])))))
    lines.append("")

    lines.append("## 承诺清单（对外文案，原样登记）")
    lines.append("")
    if promise_checks:
        lines.append("| 承诺 | 页面 | 区域 | 状态 | 文案 |")
        lines.append("|---|---|---|---|---|")
        for check in promise_checks:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(check["promise_id"] or check["path"]),
                esc(check["surface"]) if check["surface"] else "未标注",
                esc(check["region_id"]) if check["region_id"] else "未提供",
                esc(check["state"]),
                hidden(check["text"]) or "未提供"))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 承诺与结构化事实不一致")
    lines.append("")
    lines.extend(_bullets(promise_conflicts, lambda r: "`%s` [%s]：%s"
                          % (esc(r["promise_id"] or r["path"]),
                             esc(r["surface"]) if r["surface"] else "未标注页面",
                             esc("、".join(r["conflicts"])))))
    lines.append("")

    lines.append("## 目标要求缺口（只依据用户提供的 requirements）")
    lines.append("")
    lines.append("> %s" % esc(requirements_note))
    lines.append("")
    if requirement_gaps:
        for row in requirement_gaps:
            lines.append("- `%s`：%s" % (esc(row.get("requirement_id") or row["path"]),
                                        esc("、".join(row["findings"])) or "无缺口"))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 上线前人工检查表")
    lines.append("")
    for entry in human_checklist:
        lines.append("- [ ] %s" % esc(entry))
    lines.append("")

    lines.append("## 待确认问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- `%s` [%s] %s" % (
                esc(question["id"]), esc(question["subject"]), esc(question["question"])))
    else:
        lines.append("- 无")
    lines.append("")

    if warnings:
        lines.append("## 输入提示")
        lines.append("")
        for warning in warnings:
            lines.append("- %s" % esc(warning))
        lines.append("")

    lines.append("## 安全声明")
    lines.append("")
    lines.append("- %s" % esc(NO_AUTOMATION_DECLARATION))
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

    store_raw = data.get("store") if isinstance(data.get("store"), dict) else None
    default_currency = clean_text(store_raw.get("default_currency")).upper() \
        if store_raw else ""

    regions_raw = data.get("regions")
    if not isinstance(regions_raw, list):
        regions_raw = []

    warnings = []
    if as_of_dt is None:
        warnings.append("AS_OF_MISSING_OR_INVALID")
    if store_raw is None:
        warnings.append("MISSING_STORE")
    elif not default_currency:
        warnings.append("MISSING_DEFAULT_CURRENCY")
    if not regions_raw:
        warnings.append("NO_REGIONS")
    if warnings:
        return envelope("INPUT_INCOMPLETE", as_of_raw, injections, warnings)

    regions = []
    region_channels = {}
    for index, raw in enumerate(regions_raw):
        if not isinstance(raw, dict):
            continue
        region_id = clean_text(raw.get("region_id")) or None
        if region_id is None:
            continue
        channels = [clean_text(c) for c in raw.get("channels", [])] \
            if isinstance(raw.get("channels"), list) else []
        channels = [c for c in channels if c]
        if not channels:
            warnings.append("REGION_WITHOUT_CHANNELS:%s" % region_id)
        regions.append({
            "region_id": region_id,
            "country_or_area": clean_text(raw.get("country_or_area")),
            "postal_scope": clean_text(raw.get("postal_scope")),
            "channels": channels,
            "customer_segments": [clean_text(s) for s in raw.get("customer_segments", [])]
            if isinstance(raw.get("customer_segments"), list) else [],
        })
        region_channels[region_id] = channels

    if not regions:
        warnings.append("NO_VALID_REGIONS")
        return envelope("INPUT_INCOMPLETE", as_of_raw, injections, warnings)

    delivery_raw = data.get("delivery_options")
    if not isinstance(delivery_raw, list):
        delivery_raw = []
    returns_raw = data.get("return_options")
    if not isinstance(returns_raw, list):
        returns_raw = []
    promises_raw = data.get("promises")
    if not isinstance(promises_raw, list):
        promises_raw = []

    delivery = [norm_delivery(raw, i, default_currency)
                for i, raw in enumerate(delivery_raw)]
    returns = [norm_return(raw, i) for i, raw in enumerate(returns_raw)]

    classify_delivery(delivery, region_channels, as_of_dt)
    classify_return(returns, region_channels, as_of_dt)

    # Duplicate ids are a hard contradiction for every colliding record. This runs
    # after classification so a duplicated id can never be masked by a second,
    # otherwise-clean record.
    duplicates = []
    for key, code, collection in (("option_id", "DUPLICATE_OPTION_ID", delivery),
                                  ("return_id", "DUPLICATE_RETURN_ID", returns)):
        seen = {}
        for item in collection:
            if item["invalid"] or not item[key]:
                continue
            seen.setdefault(item[key], []).append(item)
        for value, items in seen.items():
            if len(items) > 1:
                duplicates.append({"id": value, "kind": code,
                                   "paths": [i["path"] for i in items]})
                for item in items:
                    if code not in item["blockers"]:
                        item["blockers"] = [code] + item["blockers"]
                    item["state"] = "BLOCKED"
                    if "重复编号导致该记录不可用" not in item["reasons"]:
                        item["reasons"].append("重复编号导致该记录不可用")
    duplicates.sort(key=lambda row: (row["kind"], row["id"]))

    promise_checks, region_blocked = build_promise_checks(
        promises_raw, region_channels, delivery, returns, default_currency)

    matrix, counts = build_matrix(regions, delivery, returns, region_blocked)

    coverage_gaps = []
    for row in matrix:
        if "NO_DELIVERY_COVERAGE" in row["findings"]:
            coverage_gaps.append({"region_id": row["region_id"],
                                  "channel": row["channel"],
                                  "kind": "NO_DELIVERY_COVERAGE"})
        if "NO_RETURN_PATH" in row["findings"]:
            coverage_gaps.append({"region_id": row["region_id"],
                                  "channel": row["channel"],
                                  "kind": "NO_RETURN_PATH"})
    coverage_gaps.sort(key=lambda r: (r["region_id"], r["channel"], r["kind"]))

    no_delivery_coverage = [g for g in coverage_gaps if g["kind"] == "NO_DELIVERY_COVERAGE"]
    no_return_path = [g for g in coverage_gaps if g["kind"] == "NO_RETURN_PATH"]

    currency_conflicts = []
    inverted = []
    inactive = []
    evidence_gaps = []
    for item in delivery:
        label = item["option_id"] or item["path"]
        if "CURRENCY_CONFLICT" in item["blockers"]:
            currency_conflicts.append({"id": label, "currency": item["currency"],
                                       "store_currency": default_currency,
                                       "path": item["path"]})
        if "INVERTED_TIME_WINDOW" in item["blockers"]:
            inverted.append({"id": label, "min_days": days_str(item["min_days"]),
                             "max_days": days_str(item["max_days"]),
                             "path": item["path"]})
        for code in item["review_flags"]:
            if code in ("OPTION_EXPIRED", "OPTION_NOT_YET_EFFECTIVE"):
                inactive.append({"id": label, "kind": code, "path": item["path"]})
        if item["unknowns"]:
            evidence_gaps.append({"id": label, "kind": "delivery",
                                  "unknowns": list(item["unknowns"]),
                                  "path": item["path"]})
    for item in returns:
        label = item["return_id"] or item["path"]
        for code in item["review_flags"]:
            if code in ("OPTION_EXPIRED", "OPTION_NOT_YET_EFFECTIVE"):
                inactive.append({"id": label, "kind": code, "path": item["path"]})
        if item["unknowns"]:
            evidence_gaps.append({"id": label, "kind": "return",
                                  "unknowns": list(item["unknowns"]),
                                  "path": item["path"]})
    for collection in (currency_conflicts, inverted, inactive, evidence_gaps):
        collection.sort(key=lambda r: (r["id"], r["path"]))

    promise_conflicts = [c for c in promise_checks if c["conflicts"]]
    promise_conflicts.sort(key=lambda r: (r["region_id"] or "", r["promise_id"] or "",
                                          r["path"]))

    requirements_raw = data.get("requirements")
    has_requirements = isinstance(requirements_raw, list) and len(requirements_raw) > 0
    requirement_gaps = build_requirement_gaps(
        requirements_raw if isinstance(requirements_raw, list) else [],
        has_requirements, delivery, returns, region_channels, default_currency)
    if has_requirements:
        requirement_note = ("缺口表只依据用户显式提供的 requirements（可选 max_days / max_fee / "
                            "min_return_window_days / currency）比对；本工具不内置任何行业默认。")
    else:
        requirement_note = ("未提供 requirements：本工具不内置行业默认，"
                            "也不据此宣称是否满足客户期望。")

    refused_all = []
    for item in delivery:
        for refused in item.get("refused_refs", []):
            refused_all.append({"id": item["option_id"] or item["path"],
                                "path": refused["path"], "reason": refused["reason"]})
    for item in returns:
        for refused in item.get("refused_refs", []):
            refused_all.append({"id": item["return_id"] or item["path"],
                                "path": refused["path"], "reason": refused["reason"]})
    refused_all.sort(key=lambda r: (r["id"], r["path"]))

    human_checklist = build_human_checklist(delivery, returns, coverage_gaps,
                                            promise_conflicts, has_requirements,
                                            inactive)
    responsibility = [
        {"area": "配送费用", "field": "delivery_options[].fee",
         "owner": "UNASSIGNED", "note": "谁向上架前的运费与币种负责，必须指定到人"},
        {"area": "配送时效", "field": "delivery_options[].min_days/max_days",
         "owner": "UNASSIGNED", "note": "谁核对承运商公布的时效区间，必须指定到人"},
        {"area": "退货窗口与运费承担", "field": "return_options[].window_days/fee_payer",
         "owner": "UNASSIGNED", "note": "谁对退货政策（含当地消费者保护要求）负责，必须指定到人"},
        {"area": "对外承诺", "field": "promises[].text",
         "owner": "UNASSIGNED", "note": "谁负责结账页 / 商品页 / FAQ / 客服稿的承诺文案"},
    ]

    questions = build_questions(delivery, returns, promise_checks, requirement_gaps)

    if any(c["state"] == "BLOCKED" for c in matrix) \
            or promise_conflicts or duplicates \
            or any(d["state"] == "BLOCKED" for d in delivery) \
            or any(r["state"] == "BLOCKED" for r in returns):
        status = "BLOCKED"
    elif any(c["state"] in ("ACTION_NEEDED", "INSUFFICIENT_EVIDENCE") for c in matrix) \
            or requirement_gaps:
        status = "GAPS_FOUND"
    else:
        status = "READY"

    store = {
        "name": clean_text(store_raw.get("name")),
        "default_currency": default_currency or None,
        "sales_channels": [clean_text(c) for c in store_raw.get("sales_channels", [])]
        if isinstance(store_raw.get("sales_channels"), list) else [],
    }

    markdown = render_markdown(status, as_of_raw, store, matrix, delivery, returns,
                              promise_checks, counts, coverage_gaps,
                              currency_conflicts, inverted, inactive, evidence_gaps,
                              promise_conflicts, requirement_note, requirement_gaps,
                              human_checklist, questions, warnings)

    for item in delivery:
        item["fee"] = money(item["fee"])
        item["min_days"] = days_str(item["min_days"])
        item["max_days"] = days_str(item["max_days"])
        item.pop("_effective", None)
        item.pop("_store_currency", None)
        item.pop("pickup_state", None)
    for item in returns:
        item["window_days"] = days_str(item["window_days"])
        item.pop("_effective", None)
        item.pop("drop_state", None)
        item.pop("fee_payer_state", None)

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "as_of_date": as_of_dt.date().isoformat() if as_of_dt else None,
        "store": store,
        "region_count": len(regions),
        "delivery_option_count": len(delivery),
        "return_option_count": len(returns),
        "promise_count": len(promise_checks),
        "status_counts": counts,
        "matrix": matrix,
        "delivery_options": delivery,
        "return_options": returns,
        "promise_checks": promise_checks,
        "no_delivery_coverage": no_delivery_coverage,
        "no_return_path": no_return_path,
        "coverage_gaps": coverage_gaps,
        "currency_conflicts": currency_conflicts,
        "inverted_time_windows": inverted,
        "inactive_options": inactive,
        "evidence_gaps": evidence_gaps,
        "promise_conflicts": promise_conflicts,
        "duplicate_ids": duplicates,
        "requirement_gaps": requirement_gaps,
        "requirements_provided": has_requirements,
        "requirement_note": requirement_note,
        "responsibility_fields": responsibility,
        "human_checklist": human_checklist,
        "clarification_questions": questions,
        "human_confirm_items": list(HUMAN_CONFIRM_BASE),
        "refused_refs": refused_all,
        "injection_flagged": list(injections),
        "input_warnings": warnings,
        "no_automation_declaration": NO_AUTOMATION_DECLARATION,
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
    }


def build_human_checklist(delivery, returns, coverage_gaps, promise_conflicts,
                          has_requirements, inactive):
    checks = [
        "逐项向承运商核实运费、时效、追踪与自提/网点可用性（本工具不联网、不代查）",
        "核对每个区域 × 渠道在结账页只暴露真实可用的配送选项",
        "核对结账页 / 商品页 / FAQ / 客服稿的承诺文案与实际配置一致",
        "确认退货窗口、退货运费承担方与退货条件符合当地消费者保护要求",
    ]
    if coverage_gaps:
        checks.append("为无配送覆盖或无退货路径的区域 × 渠道补上方案或明确不售卖")
    if promise_conflicts:
        checks.append("先修正承诺与结构化事实冲突的文案，再上线")
    if inactive:
        checks.append("确认失效 / 未生效选项的续期或下架安排")
    if not has_requirements:
        checks.append("如需按目标要求核对，请显式提供 requirements（本工具不内置行业默认）")
    return checks


def build_questions(delivery, returns, promise_checks, requirement_gaps):
    questions = []
    for item in delivery:
        label = item["option_id"] or item["path"]
        for unknown in item["unknowns"]:
            questions.append((label, unknown))
    for item in returns:
        label = item["return_id"] or item["path"]
        for unknown in item["unknowns"]:
            questions.append((label, unknown))
    for check in promise_checks:
        label = check["promise_id"] or check["path"]
        for conflict in check["conflicts"]:
            questions.append((label, conflict))
    for gap in requirement_gaps:
        label = gap.get("requirement_id") or gap["path"]
        for finding in gap["findings"]:
            questions.append((label, finding))
    out = []
    for index, (label, topic) in enumerate(questions, start=1):
        out.append({
            "id": "Q-%02d" % index,
            "subject": label,
            "topic": topic,
            "question": _question_text(topic),
        })
    return out


def _question_text(topic):
    mapping = {
        "MISSING_FEE": "配送选项没有写明费用，请补齐（这是本区域的实际运费，不是估计值）。",
        "MISSING_CURRENCY": "有费用但没有币种，请补齐；本工具不做币种换算。",
        "MISSING_TRANSIT_TIME": "缺少完整时效区间（min_days / max_days），请补齐或标注未知。",
        "UNKNOWN_TRACKING": "没有说明是否提供追踪，请补齐（未知不会被当成没有）。",
        "MISSING_PROVIDER": "没有写明承运商 / 服务商，请补齐。",
        "NO_EVIDENCE_REF": "没有提供任何证据引用，请补充可核对的单一文件名。",
        "MISSING_RETURN_WINDOW": "退货选项没有写明退货窗口天数，请补齐。",
        "UNKNOWN_FEE_PAYER": "退货选项没有写明退货运费由谁承担，请补齐。",
        "PROMISE_UNKNOWN_REGION": "承诺引用了一个不存在的区域编号，请修正。",
        "PROMISE_CURRENCY_CONFLICT": "承诺币种与店铺默认币种不一致，本工具不换算，请人工确认。",
        "PROMISE_TIME_CONFLICT": "承诺的时效没有任何配送选项能够满足，请修改承诺或补充选项。",
        "PROMISE_FEE_CONFLICT": "承诺的费用低于所有已知配送选项费用，请修改承诺或补充选项。",
        "PROMISE_RETURN_WINDOW_CONFLICT": "承诺的退货窗长于实际退货政策，请修改承诺或调整政策。",
        "REQ_DELIVERY_TIME_NOT_MET": "没有任何配送选项满足该时效要求，请确认要求或补充选项。",
        "REQ_DELIVERY_TIME_UNVERIFIABLE": "缺少时效数据，无法核对时效要求，请先补齐。",
        "REQ_DELIVERY_FEE_NOT_MET": "最低可见运费高于该费用要求，请确认要求或补充选项。",
        "REQ_DELIVERY_FEE_UNVERIFIABLE": "缺少费用数据，无法核对费用要求，请先补齐。",
        "REQ_RETURN_WINDOW_NOT_MET": "没有任何退货选项满足该退货窗要求，请确认要求或调整政策。",
        "REQ_RETURN_WINDOW_UNVERIFIABLE": "缺少退货窗数据，无法核对要求，请先补齐。",
        "REQ_CURRENCY_MISMATCH": "要求币种与店铺默认币种不一致，本工具不换算，请人工确认。",
        "REQ_UNKNOWN_REGION": "要求引用了一个不存在的区域编号，请修正。",
        "REQ_INVALID_RECORD": "目标要求记录不是对象，无法核对，请修正。",
    }
    return mapping.get(topic, "存在待确认事实：" + topic)


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
