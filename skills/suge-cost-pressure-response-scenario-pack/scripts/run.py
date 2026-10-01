#!/usr/bin/env python3
"""成本压力应对情景包 — offline cost-pressure response scenario builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes, no repricing, no ordering and no supplier contact: evidence
references are handled as file *basenames*.

Everything produced here is arithmetic on the facts the user supplied. The
scenarios are labelled as scenarios, never as advice, and no accounting, legal
or tax conclusion is produced.

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
ITEM_STATES = ("COMPUTED", "PARTIAL", "INSUFFICIENT_EVIDENCE", "BLOCKED")

SCENARIO_TYPES = ("absorb", "price_adjust", "substitute_supplier",
                  "reduce_volume", "discontinue")

BLOCKER_ORDER = ("INVALID_ITEM_RECORD", "DUPLICATE_ITEM_ID", "NEGATIVE_COST",
                 "NEGATIVE_VOLUME", "NEGATIVE_PRICE")

FATAL_UNKNOWN_ORDER = ("NO_UNIT_COST_BEFORE", "INVALID_UNIT_COST_BEFORE",
                       "NO_UNIT_COST_AFTER", "INVALID_UNIT_COST_AFTER",
                       "UNKNOWN_CURRENCY", "NO_EFFECTIVE_AT",
                       "INVALID_EFFECTIVE_AT")

SOFT_UNKNOWN_ORDER = ("NO_MONTHLY_VOLUME", "INVALID_MONTHLY_VOLUME",
                      "NO_CURRENT_PRICE", "INVALID_CURRENT_PRICE",
                      "DIVISION_BY_ZERO_PRICE", "INVALID_MARGIN_BASELINE")

REVIEW_ORDER = ("ZERO_VOLUME", "MARGIN_BASELINE_MISMATCH", "NO_EVIDENCE_REF",
                "EFFECTIVE_IN_FUTURE")

VIOLATION_ORDER = ("SCENARIO_PRICE_NOT_POSITIVE", "ABSORB_RATIO_OUT_OF_RANGE",
                   "NEW_COST_NEGATIVE", "VOLUME_NEGATIVE", "MAX_PRICE_CHANGE",
                   "MIN_GROSS_MARGIN", "MAX_RESIDUAL_EXPOSURE")

SCENARIO_UNKNOWN_ORDER = ("UNKNOWN_SCENARIO_TYPE", "DUPLICATE_SCENARIO_ID",
                          "SCENARIO_PARAM_MISSING", "SCENARIO_PARAM_INVALID",
                          "UNKNOWN_ITEM_REFERENCE", "ITEM_NOT_READY")

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

BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

DISCLAIMER = (
    "本输出是成本压力的人工决策准备材料，只是对输入事实做的数学情景，"
    "不是定价建议、不是会计、税务或法律结论；是否调价、换供应商、减少用量或停售由经营者人工决定。"
)

HUMAN_CONFIRM_BASE = (
    "采用哪一种响应属于经营决策，本工具只做数学情景，不给建议（本工具不排序、不推荐）",
    "调价、换供应商、减少用量与停售都必须由人工另行决定（本工具不改价、不下单、不联系供应商）",
    "涉及会计、税务与合同条款的结论须由人工或专业人士确认（本工具不判断合规性）",
    "单位成本、销量与售价只按输入登记值使用，本工具不核实、不推算、不补齐",
)

SCENARIO_NOTE = "本行只是对所给假设的算术结果，不是建议、不是预测，也不代表任何选项更优。"


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


def number_state(raw):
    """Return (state, Decimal|None) with state in ABSENT / INVALID / PARSED.

    Negative values ARE returned: they are contradictions the caller must be
    able to report, not values to silently drop.
    """
    if raw is None:
        return "ABSENT", None
    if isinstance(raw, bool):
        return "INVALID", None
    if isinstance(raw, str) and not raw.strip():
        return "ABSENT", None
    if isinstance(raw, (int, float, str)):
        try:
            parsed = Decimal(str(raw).strip())
        except (InvalidOperation, ValueError):
            return "INVALID", None
        if parsed.is_nan() or parsed.is_infinite():
            return "INVALID", None
        return "PARSED", parsed
    return "INVALID", None


def money(value):
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def pct(value):
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + "%"


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


# --------------------------------------------------------------------------
# empty / rejected envelopes
# --------------------------------------------------------------------------
EMPTY_SECTIONS = {
    "items": [],
    "cost_changes": [],
    "monthly_impact": {"by_currency": {}, "items_with_impact": 0,
                       "items_without_volume": [], "complete": False},
    "margin_scenarios": [],
    "scenarios": [],
    "scenario_table": [],
    "evidence_gaps": [],
    "unknowns_summary": [],
    "duplicate_items": [],
    "refused_refs": [],
    "human_confirm_items": [],
    "clarification_questions": [],
    "injection_flagged": [],
}


def reject(hits):
    payload = {
        "version": VERSION,
        "status": "REJECTED",
        "reason": "CREDENTIAL_DETECTED",
        "credential_findings": [{"path": h["path"], "reason": h["reason"]} for h in hits],
        "as_of": None,
        "item_count": 0,
        "status_counts": {name: 0 for name in ITEM_STATES},
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "markdown_summary": (
            "# 成本压力应对情景包\n\n"
            "- 状态：**REJECTED**\n"
            "- 原因：输入疑似包含凭据（密钥 / 令牌 / 密码）。\n"
            "- 处理：已拒绝处理，**未回显**任何疑似凭据内容。\n\n"
            "> 请移除凭据字段后重新提交。本工具不需要也不需要保存任何登录凭据。\n"
        ),
        "disclaimer": DISCLAIMER,
    }
    payload.update({key: (list(value) if isinstance(value, list) else value)
                    for key, value in EMPTY_SECTIONS.items()})
    return payload


def envelope(status, as_of_raw, injections, warnings):
    payload = {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "item_count": 0,
        "status_counts": {name: 0 for name in ITEM_STATES},
        "input_warnings": list(warnings),
        "injection_flagged": list(injections),
        "markdown_summary": (
            "# 成本压力应对情景包\n\n"
            "- 状态：**%s**\n"
            "- 原因：%s\n\n"
            "> 请补齐必填输入后重新提交。\n" % (status, "; ".join(warnings) or "输入不完整")
        ),
        "disclaimer": DISCLAIMER,
    }
    payload.update({key: (list(value) if isinstance(value, list) else value)
                    for key, value in EMPTY_SECTIONS.items()})
    return payload


# --------------------------------------------------------------------------
# item normalisation and classification
# --------------------------------------------------------------------------
def normalise_item(raw, index):
    path = "items[%d]" % index
    if not isinstance(raw, dict):
        return {
            "item_id": None, "index": index, "path": path, "invalid": True,
            "supplier": "", "currency": None,
            "cost_before": ("ABSENT", None), "cost_after": ("ABSENT", None),
            "volume": ("ABSENT", None), "price": ("ABSENT", None),
            "margin": ("ABSENT", None), "effective_at": None,
            "effective_state": "UNKNOWN", "evidence_refs": [], "refused_refs": [],
            "refs_provided": False, "notes": "",
            "state": "INSUFFICIENT_EVIDENCE", "blockers": [],
            "unknowns": ["INVALID_ITEM_RECORD"], "soft_unknowns": [],
            "review_flags": [], "reasons": [],
            "metrics": {},
        }

    currency = clean_text(raw.get("currency")).upper() or None
    effective_raw = raw.get("effective_at")
    effective_dt = parse_dt(effective_raw)
    if not has_text(effective_raw):
        effective_code = "NO_EFFECTIVE_AT"
    elif effective_dt is None:
        effective_code = "INVALID_EFFECTIVE_AT"
    else:
        effective_code = None

    refs_raw = raw.get("evidence_refs")
    evidence_refs = []
    refused = []
    refs_provided = False
    if isinstance(refs_raw, list):
        for offset, entry in enumerate(refs_raw):
            if not has_text(entry):
                continue
            refs_provided = True
            ok, base, reason = safe_basename(entry)
            if ok:
                evidence_refs.append(base)
            else:
                refused.append({
                    "path": "%s/evidence_refs[%d]" % (path, offset),
                    "reason": reason,
                })
                if base:
                    evidence_refs.append(base)

    return {
        "item_id": clean_text(raw.get("item_id")) or None,
        "index": index, "path": path, "invalid": False,
        "supplier": clean_text(raw.get("supplier")),
        "currency": currency,
        "cost_before": number_state(raw.get("unit_cost_before")),
        "cost_after": number_state(raw.get("unit_cost_after")),
        "volume": number_state(raw.get("monthly_volume")),
        "price": number_state(raw.get("current_price")),
        "margin": number_state(raw.get("gross_margin_before")),
        "effective_at": clean_text(effective_raw) if has_text(effective_raw) else None,
        "effective_dt": effective_dt,
        "effective_code": effective_code,
        "effective_state": "UNKNOWN",
        "evidence_refs": evidence_refs, "refused_refs": refused,
        "refs_provided": refs_provided,
        "notes": clean_text(raw.get("notes")),
        "state": "INSUFFICIENT_EVIDENCE", "blockers": [],
        "unknowns": [], "soft_unknowns": [],
        "review_flags": [], "reasons": [],
        "metrics": {},
    }


def classify_items(items, duplicate_ids, as_of_dt):
    for item in items:
        if item["invalid"]:
            continue
        label = item.get("item_id") or item["path"]

        if item.get("item_id") and item["item_id"] in duplicate_ids:
            item["blockers"].append("DUPLICATE_ITEM_ID")

        before_state, before = item["cost_before"]
        after_state, after = item["cost_after"]
        volume_state, volume = item["volume"]
        price_state, price = item["price"]

        if (before_state == "PARSED" and before < 0) or \
                (after_state == "PARSED" and after < 0):
            item["blockers"].append("NEGATIVE_COST")
        if volume_state == "PARSED" and volume < 0:
            item["blockers"].append("NEGATIVE_VOLUME")
        if price_state == "PARSED" and price < 0:
            item["blockers"].append("NEGATIVE_PRICE")

        if item["blockers"]:
            item["blockers"] = [code for code in BLOCKER_ORDER
                                if code in item["blockers"]]
            item["state"] = "BLOCKED"
            item["reasons"].append("输入存在不可能的数值或重复编号，必须先人工修正")
            continue

        fatal = []
        if before_state == "ABSENT":
            fatal.append("NO_UNIT_COST_BEFORE")
        elif before_state == "INVALID":
            fatal.append("INVALID_UNIT_COST_BEFORE")
        if after_state == "ABSENT":
            fatal.append("NO_UNIT_COST_AFTER")
        elif after_state == "INVALID":
            fatal.append("INVALID_UNIT_COST_AFTER")
        if not item["currency"]:
            fatal.append("UNKNOWN_CURRENCY")
        if item["effective_code"]:
            fatal.append(item["effective_code"])

        if fatal:
            item["unknowns"] = [code for code in FATAL_UNKNOWN_ORDER
                                if code in fatal]
            item["state"] = "INSUFFICIENT_EVIDENCE"
            item["reasons"].append("缺少成本或币种等必要事实，必须先追问")
            continue

        soft = []
        if volume_state == "ABSENT":
            soft.append("NO_MONTHLY_VOLUME")
        elif volume_state == "INVALID":
            soft.append("INVALID_MONTHLY_VOLUME")
        if price_state == "ABSENT":
            soft.append("NO_CURRENT_PRICE")
        elif price_state == "INVALID":
            soft.append("INVALID_CURRENT_PRICE")

        margin_state, margin = item["margin"]
        if margin_state == "INVALID" or (margin_state == "PARSED" and margin < 0):
            soft.append("INVALID_MARGIN_BASELINE")
            item["margin"] = ("INVALID", None)

        delta = after - before
        direction = "UP" if delta > 0 else ("DOWN" if delta < 0 else "FLAT")
        delta_pct = None
        if before != 0:
            delta_pct = delta / before * Decimal(100)
        elif delta != 0:
            # A percentage change from a zero base is undefined, never zero.
            soft.append("DIVISION_BY_ZERO_PRICE")
            item["soft_unknowns"].append("DIVISION_BY_ZERO_PRICE")

        monthly_delta = None
        monthly_before = None
        monthly_after = None
        monthly_volume = None
        if volume_state == "PARSED" and volume >= 0:
            monthly_volume = volume
            monthly_before = before * volume
            monthly_after = after * volume
            monthly_delta = delta * volume
            if volume == 0:
                item["review_flags"].append("ZERO_VOLUME")

        margin_before_pct = None
        margin_after_pct = None
        margin_delta_pp = None
        unit_margin_before = None
        unit_margin_after = None
        if price_state == "PARSED" and price > 0:
            unit_margin_before = price - before
            unit_margin_after = price - after
            margin_before_pct = unit_margin_before / price * Decimal(100)
            margin_after_pct = unit_margin_after / price * Decimal(100)
            margin_delta_pp = margin_after_pct - margin_before_pct
        elif price_state == "PARSED" and price == 0:
            soft.append("DIVISION_BY_ZERO_PRICE")
            item["soft_unknowns"].append("DIVISION_BY_ZERO_PRICE")

        margin_given = None
        margin_implied = None
        margin_check = None
        if margin_state == "PARSED" and margin >= 0:
            margin_given = margin / Decimal(100) if margin > 1 else margin
            if unit_margin_before is not None and price > 0:
                margin_implied = unit_margin_before / price
                if abs(margin_given - margin_implied) > Decimal("0.02"):
                    item["review_flags"].append("MARGIN_BASELINE_MISMATCH")
                    margin_check = "MISMATCH"
                else:
                    margin_check = "MATCH"
            else:
                margin_check = "NOT_CHECKABLE"

        if not item["evidence_refs"] and not item["refs_provided"]:
            # A refused reference is already reported in refused_refs; only a
            # record that never offered one is an evidence gap.
            item["review_flags"].append("NO_EVIDENCE_REF")

        if item["effective_dt"] is not None and as_of_dt is not None:
            if item["effective_dt"] > as_of_dt:
                item["effective_state"] = "EFFECTIVE_IN_FUTURE"
            else:
                item["effective_state"] = "EFFECTIVE_ALREADY"

        item["soft_unknowns"] = [code for code in SOFT_UNKNOWN_ORDER
                                 if code in soft]
        item["review_flags"] = [code for code in REVIEW_ORDER
                                if code in item["review_flags"]]
        item["metrics"] = {
            "currency": item["currency"],
            "unit_cost_before": before,
            "unit_cost_after": after,
            "unit_delta": delta,
            "direction": direction,
            "unit_delta_pct": delta_pct,
            "monthly_volume": monthly_volume,
            "monthly_cost_before": monthly_before,
            "monthly_cost_after": monthly_after,
            "monthly_delta": monthly_delta,
            "current_price": price if price_state == "PARSED" else None,
            "unit_margin_before": unit_margin_before,
            "unit_margin_after": unit_margin_after,
            "margin_before_pct": margin_before_pct,
            "margin_after_pct": margin_after_pct,
            "margin_delta_pp": margin_delta_pp,
            "gross_margin_before_given": margin_given,
            "gross_margin_before_implied": margin_implied,
            "margin_check": margin_check,
            "label": label,
        }

        if item["soft_unknowns"]:
            item["state"] = "PARTIAL"
            item["reasons"].append("月度影响或毛利情景缺少输入，仅给出单位口径")
        else:
            item["state"] = "COMPUTED"


# --------------------------------------------------------------------------
# scenario engine
# --------------------------------------------------------------------------
def _scenario_applicable(raw, all_items, duplicate_scenarios):
    scenario_id = clean_text(raw.get("scenario_id")) or None
    scenario_type = clean_text(raw.get("type")).lower()
    unknown_codes = []
    if not scenario_type or scenario_type not in SCENARIO_TYPES:
        unknown_codes.append("UNKNOWN_SCENARIO_TYPE")
    if scenario_id and scenario_id in duplicate_scenarios:
        unknown_codes.append("DUPLICATE_SCENARIO_ID")

    applies_raw = raw.get("applies_to")
    refs = []
    if isinstance(applies_raw, list):
        for entry in applies_raw:
            text = clean_text(entry)
            if text and text not in refs:
                refs.append(text)
    if not refs:
        refs = [item["item_id"] for item in all_items if item.get("item_id")]

    known = {item["item_id"]: item for item in all_items if item.get("item_id")}
    for ref in refs:
        if ref not in known:
            unknown_codes.append("UNKNOWN_ITEM_REFERENCE")

    return scenario_id, scenario_type, refs, known, unknown_codes


def build_scenarios(raw_scenarios, all_items, duplicate_scenarios):
    scenarios = []
    table = []
    for index, raw in enumerate(raw_scenarios):
        path = "scenarios[%d]" % index
        if not isinstance(raw, dict):
            scenarios.append({
                "scenario_id": None, "path": path, "type": None,
                "applies_to": [], "applied_items": [], "skipped_items": [],
                "totals_by_currency": {}, "feasibility": "INSUFFICIENT_EVIDENCE",
                "violations": [], "unknowns": ["UNKNOWN_SCENARIO_TYPE"],
                "flags": [], "notes": [SCENARIO_NOTE],
            })
            continue

        scenario_id, scenario_type, refs, known, unknown_codes = \
            _scenario_applicable(raw, all_items, duplicate_scenarios)
        label = scenario_id or path

        params = {
            "absorb_ratio": number_state(raw.get("absorb_ratio")),
            "price_change_pct": number_state(raw.get("price_change_pct")),
            "new_unit_cost": number_state(raw.get("new_unit_cost")),
            "volume_change_pct": number_state(raw.get("volume_change_pct")),
        }
        constraints_raw = raw.get("constraints") if isinstance(
            raw.get("constraints"), dict) else {}
        constraints = {
            "max_price_change_pct": number_state(constraints_raw.get("max_price_change_pct")),
            "min_gross_margin_pct": number_state(constraints_raw.get("min_gross_margin_pct")),
            "max_residual_exposure": number_state(constraints_raw.get("max_residual_exposure")),
        }

        required_param = {
            "absorb": "absorb_ratio",
            "price_adjust": "price_change_pct",
            "substitute_supplier": "new_unit_cost",
            "reduce_volume": "volume_change_pct",
            "discontinue": None,
        }.get(scenario_type)
        if required_param:
            state, _ = params[required_param]
            if state == "ABSENT":
                unknown_codes.append("SCENARIO_PARAM_MISSING")
            elif state == "INVALID":
                unknown_codes.append("SCENARIO_PARAM_INVALID")

        violations = []
        flags = []
        notes = [SCENARIO_NOTE]
        contributes = {}

        applied = []
        skipped = []
        for ref in refs:
            item = known.get(ref)
            if item is None:
                continue
            if item["state"] in ("BLOCKED", "INSUFFICIENT_EVIDENCE"):
                skipped.append({"item_id": ref, "reason": "ITEM_NOT_READY"})
                continue
            metrics = item["metrics"]
            if metrics.get("monthly_volume") is None:
                skipped.append({"item_id": ref, "reason": "NO_MONTHLY_VOLUME"})
                continue
            if scenario_type in ("price_adjust",) and metrics.get("current_price") is None:
                skipped.append({"item_id": ref, "reason": "NO_CURRENT_PRICE"})
                continue
            applied.append(item)
            contributes[ref] = item

        totals = {}
        if not unknown_codes and applied:
            for item in applied:
                m = item["metrics"]
                currency = m["currency"]
                volume = m["monthly_volume"]
                before = m["unit_cost_before"]
                after = m["unit_cost_after"]
                cost_delta = (after - before) * volume
                mitigation = Decimal("0")
                scenario_cost = after * volume

                if scenario_type == "absorb":
                    ratio = params["absorb_ratio"][1]
                    if ratio is None:
                        unknown_codes.append("SCENARIO_PARAM_MISSING")
                        break
                    if ratio < 0 or ratio > 1:
                        violations.append("ABSORB_RATIO_OUT_OF_RANGE")
                        ratio = min(max(ratio, Decimal("0")), Decimal("1"))
                    mitigation = cost_delta * ratio
                elif scenario_type == "price_adjust":
                    change = params["price_change_pct"][1]
                    if change is None:
                        unknown_codes.append("SCENARIO_PARAM_MISSING")
                        break
                    base_price = m["current_price"]
                    new_price = base_price * (Decimal("1") + change / Decimal(100))
                    if new_price <= 0:
                        violations.append("SCENARIO_PRICE_NOT_POSITIVE")
                    mitigation = (new_price - base_price) * volume
                    limit_state, limit = constraints["max_price_change_pct"]
                    if limit_state == "PARSED" and abs(change) > limit:
                        violations.append("MAX_PRICE_CHANGE")
                    margin_state, margin_limit = constraints["min_gross_margin_pct"]
                    if margin_state == "PARSED" and new_price > 0:
                        new_margin_pct = (new_price - after) / new_price * Decimal(100)
                        if new_margin_pct < margin_limit:
                            violations.append("MIN_GROSS_MARGIN")
                elif scenario_type == "substitute_supplier":
                    new_cost = params["new_unit_cost"][1]
                    if new_cost is None:
                        unknown_codes.append("SCENARIO_PARAM_MISSING")
                        break
                    if new_cost < 0:
                        violations.append("NEW_COST_NEGATIVE")
                    scenario_cost = new_cost * volume
                    mitigation = (after - new_cost) * volume
                elif scenario_type == "reduce_volume":
                    change = params["volume_change_pct"][1]
                    if change is None:
                        unknown_codes.append("SCENARIO_PARAM_MISSING")
                        break
                    new_volume = volume * (Decimal("1") + change / Decimal(100))
                    if new_volume < 0:
                        violations.append("VOLUME_NEGATIVE")
                        new_volume = Decimal("0")
                    if new_volume == 0:
                        flags.append("SCENARIO_VOLUME_ZERO")
                    scenario_cost = after * new_volume
                    mitigation = (volume - new_volume) * after
                    flags.append("REVENUE_EFFECT_NOT_MODELLED")
                elif scenario_type == "discontinue":
                    scenario_cost = Decimal("0")
                    mitigation = after * volume
                    flags.append("REVENUE_EFFECT_NOT_MODELLED")

                residual = cost_delta - mitigation
                bucket = totals.setdefault(currency, {
                    "scenario_monthly_cost": Decimal("0"),
                    "baseline_monthly_cost": Decimal("0"),
                    "cost_delta_vs_baseline": Decimal("0"),
                    "mitigation_amount": Decimal("0"),
                    "residual_exposure": Decimal("0"),
                })
                bucket["scenario_monthly_cost"] += scenario_cost
                bucket["baseline_monthly_cost"] += after * volume
                bucket["cost_delta_vs_baseline"] += cost_delta
                bucket["mitigation_amount"] += mitigation
                bucket["residual_exposure"] += residual

            limit_state, limit = constraints["max_residual_exposure"]
            if limit_state == "PARSED":
                for currency, bucket in totals.items():
                    if bucket["residual_exposure"] > limit:
                        violations.append("MAX_RESIDUAL_EXPOSURE")
                        break

        if unknown_codes or not totals:
            # A scenario with an unusable input must not leak a half-computed
            # total: clear the partial buckets before reporting.
            totals = {}
            if not unknown_codes:
                unknown_codes.append("ITEM_NOT_READY")
            feasibility = "INSUFFICIENT_EVIDENCE"
        elif violations:
            feasibility = "VIOLATES_CONSTRAINTS"
        elif skipped:
            feasibility = "PARTIAL_INPUT"
        else:
            feasibility = "MEETS_CONSTRAINTS"

        serial_totals = {}
        for currency in sorted(totals):
            bucket = totals[currency]
            serial_totals[currency] = {
                "scenario_monthly_cost": money(bucket["scenario_monthly_cost"]),
                "baseline_monthly_cost": money(bucket["baseline_monthly_cost"]),
                "cost_delta_vs_baseline": money(bucket["cost_delta_vs_baseline"]),
                "mitigation_amount": money(bucket["mitigation_amount"]),
                "residual_exposure": money(bucket["residual_exposure"]),
            }

        violations = [code for code in VIOLATION_ORDER if code in violations]
        unknown_codes = [code for code in SCENARIO_UNKNOWN_ORDER
                         if code in unknown_codes]
        flags = [code for code in ("REVENUE_EFFECT_NOT_MODELLED",
                                   "SCENARIO_VOLUME_ZERO") if code in flags]
        if scenario_type in ("discontinue", "reduce_volume"):
            notes.append("减少用量或停售会改变销量，收入影响本工具不建模，必须由人工评估。")
        if scenario_type == "price_adjust":
            notes.append("调价只做算术，未考虑需求对价格的反应。")

        scenarios.append({
            "scenario_id": scenario_id, "path": path, "type": scenario_type,
            "applies_to": refs,
            "applied_items": [item["item_id"] for item in applied],
            "skipped_items": skipped,
            "totals_by_currency": serial_totals,
            "feasibility": feasibility,
            "violations": violations,
            "unknowns": unknown_codes,
            "flags": flags,
            "notes": notes,
        })

        for currency in sorted(serial_totals):
            table.append({
                "scenario_id": label,
                "type": scenario_type,
                "currency": currency,
                "scenario_monthly_cost": serial_totals[currency]["scenario_monthly_cost"],
                "cost_delta_vs_baseline": serial_totals[currency]["cost_delta_vs_baseline"],
                "mitigation_amount": serial_totals[currency]["mitigation_amount"],
                "residual_exposure": serial_totals[currency]["residual_exposure"],
                "feasibility": feasibility,
                "violations": list(violations),
            })

    scenarios.sort(key=lambda s: s["scenario_id"] or s["path"])
    table.sort(key=lambda r: (r["scenario_id"], r["currency"]))
    return scenarios, table


# --------------------------------------------------------------------------
# questions
# --------------------------------------------------------------------------
def build_questions(items, scenarios):
    pairs = []
    for item in items:
        label = item.get("item_id") or item["path"]
        for unknown in list(item.get("unknowns", [])) + list(item.get("soft_unknowns", [])):
            pairs.append((label, unknown))
    for scenario in scenarios:
        label = scenario["scenario_id"] or scenario["path"]
        for unknown in scenario["unknowns"]:
            pairs.append((label, unknown))
    out = []
    for index, (label, topic) in enumerate(pairs, start=1):
        out.append({
            "id": "Q-%02d" % index,
            "subject": label,
            "topic": topic,
            "question": _question_text(topic),
        })
    return out


def _question_text(topic):
    mapping = {
        "INVALID_ITEM_RECORD": "该成本项记录不是对象，无法计算，请按字段表重新提供。",
        "NO_UNIT_COST_BEFORE": "没有变动前的单位成本，无法算增量，请补齐。",
        "INVALID_UNIT_COST_BEFORE": "变动前单位成本不是可比较的数值，请修正。",
        "NO_UNIT_COST_AFTER": "没有变动后的单位成本，无法算增量，请补齐。",
        "INVALID_UNIT_COST_AFTER": "变动后单位成本不是可比较的数值，请修正。",
        "UNKNOWN_CURRENCY": "没有币种，金额无法登记；本工具不做任何汇率换算。",
        "NO_EFFECTIVE_AT": "没有生效时间，无法判断是否已生效，请补齐带时区的时间。",
        "INVALID_EFFECTIVE_AT": "生效时间格式无效或缺少时区偏移，请修正。",
        "NO_MONTHLY_VOLUME": "没有月度用量，只能给出单位口径，无法给出月度影响。",
        "INVALID_MONTHLY_VOLUME": "月度用量不是可比较的数值，请修正。",
        "NO_CURRENT_PRICE": "没有当前售价，无法给出毛利变化情景。",
        "INVALID_CURRENT_PRICE": "当前售价不是可比较的数值，请修正。",
        "DIVISION_BY_ZERO_PRICE": "基准为 0，百分比变化无法定义，本工具不按 0 处理。",
        "INVALID_MARGIN_BASELINE": "填写的毛利率基线不是 0–1 的比例也不是百分比，请修正。",
        "UNKNOWN_SCENARIO_TYPE": "情景类型不在固定词表内，本工具不发明情景，请改用支持的类型。",
        "DUPLICATE_SCENARIO_ID": "情景编号重复，无法区分，请改成唯一编号。",
        "SCENARIO_PARAM_MISSING": "情景缺少必需参数，请补齐后再计算。",
        "SCENARIO_PARAM_INVALID": "情景参数不是可比较的数值，请修正。",
        "UNKNOWN_ITEM_REFERENCE": "情景引用了不存在的成本项编号，请补充该成本项或修正编号。",
        "ITEM_NOT_READY": "情景引用的成本项本身还没算出来，请先补齐该成本项的事实。",
    }
    return mapping.get(topic, "存在待确认事实：" + topic)


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def render_markdown(status, as_of, business, counts, items, cost_changes,
                    monthly, margins, scenarios, table, evidence_gaps,
                    duplicates, human_confirm, questions, warnings,
                    unknown_summary):
    lines = []
    lines.append("# 成本压力应对情景包")
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    if business:
        lines.append("- 业务：%s" % (hidden(business.get("name")) or "未提供"))
        lines.append("- 时区：%s" % (esc(business.get("timezone")) or "未提供"))
    lines.append("- 成本项数：%d" % len(items))
    lines.append("")
    lines.append("## 成本项结论")
    lines.append("")
    lines.append("| 结论 | 数量 |")
    lines.append("|---|---:|")
    for name in ITEM_STATES:
        lines.append("| %s | %d |" % (name, counts[name]))
    lines.append("")
    lines.append("| 成本项 | 供应商 | 币种 | 变动前 | 变动后 | 单位增量 | 方向 | 生效状态 | 结论 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for item in items:
        m = item.get("metrics", {})
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            esc(item.get("item_id")) if item.get("item_id") else esc(item["path"]),
            hidden(item.get("supplier")) or "未记录",
            esc(item.get("currency")) if item.get("currency") else "未提供",
            money(m.get("unit_cost_before")) if m.get("unit_cost_before") is not None else "未提供",
            money(m.get("unit_cost_after")) if m.get("unit_cost_after") is not None else "未提供",
            money(m.get("unit_delta")) if m.get("unit_delta") is not None else "未知",
            esc(m.get("direction")) if m.get("direction") else "未知",
            esc(item.get("effective_state")),
            item["state"],
        ))
    lines.append("")

    lines.append("## 逐项成本增量")
    lines.append("")
    if cost_changes:
        lines.append("| 成本项 | 币种 | 单位增量 | 增量百分比 | 月度增量 |")
        lines.append("|---|---|---|---:|---:|")
        for row in cost_changes:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(row["item_id"]), esc(row["currency"]), esc(row["unit_delta"]),
                esc(row["unit_delta_pct"]) if row["unit_delta_pct"] is not None else "未定义",
                esc(row["monthly_delta"]) if row["monthly_delta"] is not None else "未提供"))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 月度影响（仅输入齐全时）")
    lines.append("")
    if monthly["by_currency"]:
        lines.append("| 币种 | 变动前月度成本 | 变动后月度成本 | 月度增量 |")
        lines.append("|---|---:|---:|---:|")
        for currency in sorted(monthly["by_currency"]):
            row = monthly["by_currency"][currency]
            lines.append("| %s | %s | %s | %s |" % (
                esc(currency), esc(row["cost_before"]), esc(row["cost_after"]),
                esc(row["delta"])))
    else:
        lines.append("- 无可计算的月度影响（缺失值不按 0 计算）")
    lines.append("")
    lines.append("- 可计算月度影响的成本项：%d 条" % monthly["items_with_impact"])
    lines.append("- 缺月度用量的成本项：%d 条" % len(monthly["items_without_volume"]))
    lines.append("- 是否全部齐全：%s" % ("是" if monthly["complete"] else "否"))
    lines.append("")

    lines.append("## 当前价格下的毛利变化情景")
    lines.append("")
    if margins:
        lines.append("| 成本项 | 币种 | 售价 | 单位毛利（前） | 单位毛利（后） | 毛利率（前） | 毛利率（后） | 变化 |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
        for row in margins:
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                esc(row["item_id"]), esc(row["currency"]), esc(row["current_price"]),
                esc(row["unit_margin_before"]), esc(row["unit_margin_after"]),
                esc(row["margin_before_pct"]), esc(row["margin_after_pct"]),
                esc(row["margin_delta_pp"])))
    else:
        lines.append("- 无（缺售价或成本不完整）")
    lines.append("")
    lines.append("> 毛利变化只是售价与单位成本的算术差；未考虑销量、税、退货与其它成本。")
    lines.append("")

    lines.append("## 响应情景可比表")
    lines.append("")
    if table:
        lines.append("| 情景 | 类型 | 币种 | 情景月度成本 | 成本增量 | 抵消金额 | 残余敞口 | 可行性 |")
        lines.append("|---|---|---|---:|---:|---:|---:|---|")
        for row in table:
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                esc(row["scenario_id"]), esc(row["type"]), esc(row["currency"]),
                esc(row["scenario_monthly_cost"]), esc(row["cost_delta_vs_baseline"]),
                esc(row["mitigation_amount"]), esc(row["residual_exposure"]),
                esc(row["feasibility"])))
    else:
        lines.append("- 无可用情景")
    lines.append("")
    lines.append("> %s" % esc(SCENARIO_NOTE))
    lines.append("")

    lines.append("## 情景明细")
    lines.append("")
    if scenarios:
        for scenario in scenarios:
            lines.append("### %s（%s）" % (
                esc(scenario["scenario_id"] or scenario["path"]),
                esc(scenario["type"]) if scenario["type"] else "未识别"))
            lines.append("")
            lines.append("- 可行性：**%s**" % esc(scenario["feasibility"]))
            lines.append("- 适用成本项：%s" % (
                esc("、".join(scenario["applied_items"])) or "无"))
            if scenario["skipped_items"]:
                lines.append("- 跳过：%s" % esc("、".join(
                    "%s（%s）" % (s["item_id"], s["reason"])
                    for s in scenario["skipped_items"])))
            if scenario["violations"]:
                lines.append("- 违反约束：%s" % esc("、".join(scenario["violations"])))
            if scenario["unknowns"]:
                lines.append("- 未知：%s" % esc("、".join(scenario["unknowns"])))
            if scenario["flags"]:
                lines.append("- 提示：%s" % esc("、".join(scenario["flags"])))
            for note in scenario["notes"]:
                lines.append("- %s" % esc(note))
            lines.append("")
    else:
        lines.append("- 无")
        lines.append("")

    lines.append("## 证据缺口")
    lines.append("")
    if evidence_gaps:
        for row in evidence_gaps:
            lines.append("- `%s`：%s" % (esc(row["item_id"]), esc(row["reason"])))
    else:
        lines.append("- 无")
    lines.append("")

    if duplicates:
        lines.append("## 重复编号")
        lines.append("")
        for row in duplicates:
            lines.append("- `%s`：%s" % (esc(row["item_id"]),
                                        esc("、".join(row["paths"]))))
        lines.append("")

    lines.append("## 未知项汇总")
    lines.append("")
    if unknown_summary:
        for row in unknown_summary:
            lines.append("- `%s`：%s" % (
                esc(row["item_id"]), esc("、".join(row["unknowns"]))))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 说明")
    lines.append("")
    if any(item.get("notes") for item in items):
        lines.append("| 成本项 | 备注 |")
        lines.append("|---|---|")
        for item in items:
            if item.get("notes"):
                lines.append("| %s | %s |" % (
                    esc(item.get("item_id")) if item.get("item_id")
                    else esc(item["path"]), hidden(item.get("notes"))))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 人工确认项")
    lines.append("")
    for entry in human_confirm:
        lines.append("- %s" % esc(entry))
    lines.append("")

    lines.append("## 待确认问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- `%s` [%s] %s" % (
                question["id"], esc(question["subject"]),
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

    raw_scenarios = data.get("scenarios")
    if not isinstance(raw_scenarios, list):
        raw_scenarios = []
    if not raw_scenarios:
        warnings.append("NO_SCENARIOS")

    items = [normalise_item(raw, index) for index, raw in enumerate(raw_items)]

    seen = {}
    for item in items:
        if item.get("item_id"):
            seen.setdefault(item["item_id"], []).append(item["path"])
    duplicate_ids = {key for key, paths in seen.items() if len(paths) > 1}
    duplicates = [{"item_id": key, "paths": sorted(seen[key])}
                  for key in sorted(duplicate_ids)]

    classify_items(items, duplicate_ids, as_of_dt)
    items.sort(key=lambda i: (i.get("item_id") or "", i["path"]))

    scenario_ids = []
    for raw in raw_scenarios:
        if isinstance(raw, dict):
            scenario_ids.append(clean_text(raw.get("scenario_id")) or "")
    duplicate_scenarios = {sid for sid in scenario_ids
                           if sid and scenario_ids.count(sid) > 1}

    scenarios, table = build_scenarios(raw_scenarios, items, duplicate_scenarios)

    counts = {name: 0 for name in ITEM_STATES}
    for item in items:
        counts[item["state"]] += 1

    cost_changes = []
    for item in items:
        m = item.get("metrics", {})
        if m.get("unit_delta") is None:
            continue
        cost_changes.append({
            "item_id": m["label"],
            "path": item["path"],
            "currency": m["currency"],
            "direction": m["direction"],
            "unit_delta": money(m["unit_delta"]),
            "unit_delta_pct": pct(m["unit_delta_pct"]),
            "monthly_delta": money(m["monthly_delta"]),
        })

    by_currency = {}
    items_with_impact = 0
    items_without_volume = []
    for item in items:
        m = item.get("metrics", {})
        if m.get("monthly_delta") is None:
            if item["state"] in ("COMPUTED", "PARTIAL"):
                items_without_volume.append(m.get("label") or item["path"])
            continue
        items_with_impact += 1
        currency = m["currency"]
        bucket = by_currency.setdefault(currency, {
            "cost_before": Decimal("0"), "cost_after": Decimal("0"),
            "delta": Decimal("0")})
        bucket["cost_before"] += m["monthly_cost_before"]
        bucket["cost_after"] += m["monthly_cost_after"]
        bucket["delta"] += m["monthly_delta"]
    monthly = {
        "by_currency": {
            currency: {
                "cost_before": money(bucket["cost_before"]),
                "cost_after": money(bucket["cost_after"]),
                "delta": money(bucket["delta"]),
            } for currency, bucket in sorted(by_currency.items())
        },
        "items_with_impact": items_with_impact,
        "items_without_volume": sorted(items_without_volume),
        "complete": (not items_without_volume) and items_with_impact > 0,
    }

    margins = []
    for item in items:
        m = item.get("metrics", {})
        if m.get("margin_after_pct") is None:
            continue
        margins.append({
            "item_id": m["label"],
            "path": item["path"],
            "currency": m["currency"],
            "current_price": money(m["current_price"]),
            "unit_margin_before": money(m["unit_margin_before"]),
            "unit_margin_after": money(m["unit_margin_after"]),
            "margin_before_pct": pct(m["margin_before_pct"]),
            "margin_after_pct": pct(m["margin_after_pct"]),
            "margin_delta_pp": money(m["margin_delta_pp"]),
            "gross_margin_before_given": (
                None if m["gross_margin_before_given"] is None
                else str(m["gross_margin_before_given"].quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP))),
            "gross_margin_before_implied": (
                None if m["gross_margin_before_implied"] is None
                else str(m["gross_margin_before_implied"].quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP))),
            "margin_check": m["margin_check"],
        })

    evidence_gaps = [{"item_id": item.get("item_id") or item["path"],
                      "path": item["path"], "reason": "NO_EVIDENCE_REF"}
                     for item in items if "NO_EVIDENCE_REF" in item["review_flags"]]
    evidence_gaps.sort(key=lambda r: r["item_id"])

    unknown_summary = [{"item_id": item.get("item_id") or item["path"],
                        "path": item["path"],
                        "unknowns": list(item["unknowns"]) + list(item["soft_unknowns"])}
                       for item in items
                       if item["unknowns"] or item["soft_unknowns"]]
    unknown_summary.sort(key=lambda r: r["item_id"])

    human_confirm = list(HUMAN_CONFIRM_BASE)
    if scenarios:
        human_confirm.append("情景之间的取舍需要人工判断，本工具不排序、不推荐")
    if any("REVENUE_EFFECT_NOT_MODELLED" in s["flags"] for s in scenarios):
        human_confirm.append("减少用量或停售的收入影响本工具不建模，需人工评估")
    if duplicates:
        human_confirm.append("重复的成本项编号需要人工确认是否同一项")

    refused_all = []
    for item in items:
        for refused in item.get("refused_refs", []):
            refused_all.append({"item_id": item.get("item_id"),
                                "path": refused["path"],
                                "reason": refused["reason"]})
    refused_all.sort(key=lambda r: (r["item_id"] or "", r["path"]))

    questions = build_questions(items, scenarios)

    if counts["BLOCKED"]:
        status = "BLOCKED"
    elif (counts["PARTIAL"] or counts["INSUFFICIENT_EVIDENCE"]
          or any(s["feasibility"] != "MEETS_CONSTRAINTS" for s in scenarios)):
        status = "GAPS_FOUND"
    else:
        status = "READY"

    business_raw = data.get("business") if isinstance(data.get("business"), dict) else None
    business = None
    if business_raw:
        business = {
            "name": clean_text(business_raw.get("name")),
            "timezone": clean_text(business_raw.get("timezone")) or None,
        }

    markdown = render_markdown(status, as_of_raw, business, counts, items,
                              cost_changes, monthly, margins, scenarios, table,
                              evidence_gaps, duplicates, human_confirm, questions,
                              warnings, unknown_summary)

    serial_items = []
    for item in items:
        item.pop("effective_dt", None)
        item.pop("effective_code", None)
        item.pop("metrics", None)
        for field in ("cost_before", "cost_after", "volume", "price", "margin"):
            state, value = item[field]
            item[field] = {"state": state, "value": money(value)}
        serial_items.append(item)

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "as_of_date": (as_of_dt.date().isoformat() if as_of_dt else None),
        "business": business,
        "item_count": len(items),
        "status_counts": counts,
        "scenario_type_vocabulary": list(SCENARIO_TYPES),
        "items": serial_items,
        "cost_changes": cost_changes,
        "monthly_impact": monthly,
        "margin_scenarios": margins,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
        "scenario_table": table,
        "evidence_gaps": evidence_gaps,
        "unknowns_summary": unknown_summary,
        "duplicate_items": duplicates,
        "refused_refs": refused_all,
        "injection_flagged": list(injections),
        "input_warnings": warnings,
        "human_confirm_items": human_confirm,
        "clarification_questions": questions,
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
