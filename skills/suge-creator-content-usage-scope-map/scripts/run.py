#!/usr/bin/env python3
"""创作者内容使用范围地图 — offline content-usage scope map builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no command
execution, no attachment opening: only plain file *basenames* are ever handled.

This engine records what a collaboration says about which asset may be used, by
whom, where, for what purpose and until when, and checks new requests against
that record. It does not interpret legal effect, does not draft contract text and
does not decide whether anything is infringing.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import date, datetime

VERSION = "1.0.0"

# --------------------------------------------------------------------------
# fixed vocabulary (documented in references/guide.md)
# --------------------------------------------------------------------------
# Scope values are free text on purpose: a collaboration may use its own wording
# and a typo must not silently turn a granted scope into a "not granted" verdict.
# The guide lists the frequently used values (organic / paid_ads / website /
# email / retail_screen / whitelisting for purposes; crop / caption / translate /
# re_edit / raw_footage / altered_audio for modifications).
SCOPE_FIELDS = ("authorized_parties", "channels", "territory", "purposes")
GRANT_STATUSES = ("DEFINED", "INCOMPLETE", "EXPIRED", "INVALID", "CONFLICT")

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
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

DISCLAIMER = (
    "本输出只按登记内容整理事实与范围差异，不是法律意见，不解释条款效力、不起草合同、不判断是否侵权；"
    "权利范围以各方签署的书面文件为准。"
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    """Collapse control characters and whitespace; never raises.

    NFKC is deliberately NOT applied: it would rewrite Chinese full-width
    punctuation and degrade the confirmation sheet the user sends back.
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


def parse_date(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def text_list(value):
    """Return a de-duplicated, order-preserving list of non-empty strings."""
    out = []
    if isinstance(value, list):
        for entry in value:
            if has_text(entry):
                item = clean_text(entry)
                if item not in out:
                    out.append(item)
    return out


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
    """Return (ok, safe_name, reason). The raw reference is never echoed."""
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


def reject(hits):
    return {
        "version": VERSION,
        "status": "REJECTED",
        "reason": "CREDENTIAL_DETECTED",
        "credential_findings": [{"path": h["path"], "reason": h["reason"]} for h in hits],
        "as_of": None, "as_of_date": None, "collaboration": {},
        "grant_count": 0, "asset_count": 0,
        "status_counts": {name: 0 for name in GRANT_STATUSES},
        "scope_matrix": [], "conflicts": [], "unknown_overlap_pairs": [],
        "expired_grants": [], "expiring_soon": [], "end_date_unknown": [],
        "not_yet_started": [],
        "expiry_timeline": {"expired": [], "within_30_days": [], "within_60_days": [],
                            "within_90_days": [], "unknown_end": [], "not_yet_started": []},
        "over_scope_candidates": [], "all_requests": [],
        "attribution_gaps": [], "renewal_not_stated": [], "source_evidence_not_stated": [],
        "orphan_assets": [], "dangling_grants": [], "refused_refs": [],
        "clarification_questions": [], "injection_flagged": [],
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "markdown_summary": (
            "# 创作者内容使用范围地图\n\n"
            "- 状态：**REJECTED**\n"
            "- 原因：输入疑似包含凭据（密钥 / 令牌 / 密码）。\n"
            "- 处理：已拒绝处理，**未回显**任何疑似凭据内容。\n\n"
            "> 请移除凭据字段后重新提交。本工具不需要也不需要保存任何登录凭据，"
            "也不会替你向任何平台授权。\n"
        ),
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# grant reading
# --------------------------------------------------------------------------
def normalise_grant(raw, index, as_of_date, known_assets):
    path = "grants[%d]" % index
    if not isinstance(raw, dict):
        return {
            "grant_id": None, "path": path, "invalid": True, "flags": ["INVALID_GRANT_RECORD"],
            "asset_id": None, "authorized_parties": [], "channels": [], "territory": [],
            "purposes": [], "modifications": [], "starts_on": None, "ends_on": None,
            "start_date": None, "end_date": None, "time_state": "UNKNOWN", "status": "INCOMPLETE",
            "attribution_required": None, "attribution_text": None, "renewal_note": None,
            "source_evidence": None, "notes": None,
        }

    flags = []
    grant_id = clean_text(raw.get("grant_id")) if has_text(raw.get("grant_id")) else None
    if grant_id is None:
        flags.append("MISSING_GRANT_ID")

    asset_id = clean_text(raw.get("asset_id")) if has_text(raw.get("asset_id")) else None
    if asset_id is None:
        flags.append("MISSING_ASSET_ID")
    elif asset_id not in known_assets:
        flags.append("DANGLING_ASSET_ID")

    authorized_parties = text_list(raw.get("authorized_parties"))
    channels = text_list(raw.get("channels"))
    territory = text_list(raw.get("territory"))
    purposes = text_list(raw.get("purposes"))
    scope_values = {"authorized_parties": authorized_parties, "channels": channels,
                    "territory": territory, "purposes": purposes}
    for field in SCOPE_FIELDS:
        if not scope_values[field]:
            flags.append("UNKNOWN_SCOPE_" + field.upper())

    if "modifications" in raw:
        modifications = text_list(raw.get("modifications"))
        if not modifications:
            # An explicitly empty list is "none recorded", which is still unknown:
            # it must never be read as "any change is forbidden".
            flags.append("MODIFICATIONS_UNKNOWN")
    else:
        modifications = []
        flags.append("MODIFICATIONS_UNKNOWN")

    start_raw = raw.get("starts_on")
    end_raw = raw.get("ends_on")
    start_date = parse_date(start_raw)
    end_date = parse_date(end_raw)
    if has_text(start_raw) and start_date is None:
        flags.append("INVALID_START_DATE")
    if has_text(end_raw) and end_date is None:
        flags.append("INVALID_END_DATE")
    if start_date is None:
        flags.append("MISSING_START_DATE")
    if end_date is None:
        flags.append("END_DATE_UNKNOWN")
    if start_date is not None and end_date is not None and start_date > end_date:
        flags.append("INVALID_DATE_RANGE")

    time_state = "UNKNOWN"
    if start_date is not None and as_of_date is not None and start_date > as_of_date:
        time_state = "NOT_YET_STARTED"
    elif end_date is not None and as_of_date is not None:
        if end_date < as_of_date:
            time_state = "EXPIRED"
        elif end_date == as_of_date:
            time_state = "ACTIVE_ENDS_TODAY"
        elif (end_date - as_of_date).days <= 30:
            time_state = "EXPIRING_SOON"
        else:
            time_state = "ACTIVE"
    elif end_date is None and start_date is not None and as_of_date is not None:
        time_state = "ACTIVE_END_UNKNOWN"

    attribution_required = read_bool(raw.get("attribution_required"))
    attribution_text = clean_text(raw.get("attribution_text")) if has_text(raw.get("attribution_text")) else None
    if "attribution_required" not in raw or attribution_required is None:
        flags.append("ATTRIBUTION_REQUIREMENT_UNKNOWN")
    if attribution_required is True and attribution_text is None:
        flags.append("ATTRIBUTION_TEXT_MISSING")

    renewal_note = clean_text(raw.get("renewal_note")) if has_text(raw.get("renewal_note")) else None
    if renewal_note is None:
        flags.append("RENEWAL_NOT_STATED")
    source_evidence = clean_text(raw.get("source_evidence")) if has_text(raw.get("source_evidence")) else None
    if source_evidence is None:
        flags.append("SOURCE_EVIDENCE_NOT_STATED")

    return {
        "grant_id": grant_id, "path": path, "index": index,
        "invalid": bool(grant_id is None or asset_id is None or asset_id not in known_assets
                        or "INVALID_START_DATE" in flags or "INVALID_END_DATE" in flags
                        or "INVALID_DATE_RANGE" in flags),
        "flags": flags, "asset_id": asset_id,
        "authorized_parties": authorized_parties, "channels": channels,
        "territory": territory, "purposes": purposes, "modifications": modifications,
        "starts_on": clean_text(start_raw) if has_text(start_raw) else None,
        "ends_on": clean_text(end_raw) if has_text(end_raw) else None,
        "start_date": start_date, "end_date": end_date,
        "time_state": time_state, "status": "INCOMPLETE",
        "attribution_required": attribution_required, "attribution_text": attribution_text,
        "renewal_note": renewal_note, "source_evidence": source_evidence,
        "notes": raw.get("notes"),
    }


def window_overlaps(a_start, a_end, b_start, b_end):
    return a_start <= b_end and b_start <= a_end


def detect_conflicts(grants):
    """Return (conflicts, unknown_overlap_pairs) over grants sharing an asset."""
    by_asset = {}
    order = []
    for grant in grants:
        key = grant["asset_id"]
        if key not in by_asset:
            by_asset[key] = []
            order.append(key)
        by_asset[key].append(grant)

    conflicts = []
    unknown_overlap = []
    for asset_id in order:
        group = by_asset[asset_id]
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                first, second = group[i], group[j]
                if first["start_date"] is None or first["end_date"] is None \
                        or second["start_date"] is None or second["end_date"] is None:
                    unknown_overlap.append({
                        "asset_id": asset_id,
                        "grant_ids": sorted([str(first["grant_id"]), str(second["grant_id"])]),
                        "reason": "WINDOW_INCOMPLETE_CANNOT_COMPARE",
                    })
                    continue
                if not window_overlaps(first["start_date"], first["end_date"],
                                       second["start_date"], second["end_date"]):
                    continue
                differences = []
                for field in SCOPE_FIELDS + ("modifications",):
                    first_values = set(first[field])
                    second_values = set(second[field])
                    if first_values and second_values and first_values != second_values:
                        differences.append({
                            "field": field,
                            "values": sorted(first_values),
                            "other_values": sorted(second_values),
                        })
                if differences:
                    conflict = {
                        "asset_id": asset_id,
                        "grant_ids": sorted([str(first["grant_id"]), str(second["grant_id"])]),
                        "overlap": {
                            "from": str(max(first["start_date"], second["start_date"])),
                            "to": str(min(first["end_date"], second["end_date"])),
                        },
                        "differences": differences,
                    }
                    conflicts.append(conflict)
                    first["flags"].append("SCOPE_CONFLICT")
                    second["flags"].append("SCOPE_CONFLICT")
    return conflicts, unknown_overlap


def evaluate_request(request, grants_by_asset, conflicted_ids):
    """Return a per-request scope check. This is a record comparison, not a verdict."""
    path = request["path"]
    out = {
        "request_id": request["request_id"], "path": path, "asset_id": request["asset_id"],
        "party": request["party"], "channel": request["channel"], "territory": request["territory"],
        "purpose": request["purpose"], "modifications": list(request["modifications"]),
        "planned_on": request["planned_on"], "result": "UNKNOWN_CANNOT_CHECK",
        "covering_grant_ids": [], "checks": [],
        "note": "按登记范围核对，不是法律结论。",
    }
    if request["invalid"]:
        out["result"] = "UNKNOWN_CANNOT_CHECK"
        out["checks"].append({"field": "request", "result": "INVALID_REQUEST_RECORD"})
        return out
    if request["asset_id"] not in grants_by_asset:
        out["checks"].append({"field": "asset_id", "result": "NO_GRANT_FOR_ASSET"})
        return out

    planned = request["planned_date"]
    per_grant = []
    for grant in grants_by_asset[request["asset_id"]]:
        if grant["invalid"]:
            per_grant.append(("UNKNOWN_CANNOT_CHECK", None, []))
            continue
        if planned is not None:
            if grant["start_date"] is None or grant["end_date"] is None:
                per_grant.append(("UNKNOWN_CANNOT_CHECK", None, []))
                continue
            if not (grant["start_date"] <= planned <= grant["end_date"]):
                continue
        checks = []
        status = "COVERED"
        for field, requested in (("authorized_parties", request["party"]),
                                 ("channels", request["channel"]),
                                 ("territory", request["territory"]),
                                 ("purposes", request["purpose"])):
            if not has_text(requested):
                continue
            registered = grant[field]
            if not registered:
                checks.append({"field": field, "requested": clean_text(requested),
                               "registered": [], "result": "CANNOT_CHECK_UNKNOWN_SCOPE"})
                if status == "COVERED":
                    status = "UNKNOWN_CANNOT_CHECK"
                continue
            if clean_text(requested) in registered:
                checks.append({"field": field, "requested": clean_text(requested),
                               "registered": registered, "result": "IN_SCOPE"})
            else:
                checks.append({"field": field, "requested": clean_text(requested),
                               "registered": registered, "result": "OUT_OF_SCOPE"})
                status = "OUT_OF_SCOPE"
        for requested in request["modifications"]:
            registered = grant["modifications"]
            if not registered:
                checks.append({"field": "modifications", "requested": requested,
                               "registered": [], "result": "CANNOT_CHECK_UNKNOWN_SCOPE"})
                if status == "COVERED":
                    status = "UNKNOWN_CANNOT_CHECK"
            elif requested in registered:
                checks.append({"field": "modifications", "requested": requested,
                               "registered": registered, "result": "IN_SCOPE"})
            else:
                checks.append({"field": "modifications", "requested": requested,
                               "registered": registered, "result": "OUT_OF_SCOPE"})
                status = "OUT_OF_SCOPE"
        per_grant.append((status, grant["grant_id"], checks))

    if not per_grant:
        # The asset has scope records, but none of their windows covers the
        # planned date (for example the only record already expired).
        out["result"] = "NO_ACTIVE_WINDOW"
        out["checks"].append({"field": "planned_on", "result": "NO_GRANT_WINDOW_COVERS_THE_DATE"})
        return out

    covering = [gid for status, gid, _ in per_grant if status == "COVERED"]
    if covering:
        out["covering_grant_ids"] = [g for g in covering if g is not None]
        # A grant whose own records contradict another record for the same asset
        # must not be reported as a clean yes: the conflict needs a human first.
        if any(g in conflicted_ids for g in out["covering_grant_ids"]):
            out["result"] = "COVERED_BUT_CONFLICTED"
        else:
            out["result"] = "COVERED"
        for status, gid, checks in per_grant:
            if status == "COVERED":
                out["checks"].extend(checks)
                break
        return out
    if any(status == "UNKNOWN_CANNOT_CHECK" for status, _, _ in per_grant):
        out["result"] = "UNKNOWN_CANNOT_CHECK"
    else:
        out["result"] = "OUT_OF_SCOPE"
    for status, gid, checks in per_grant:
        if status == out["result"]:
            out["checks"].extend(checks)
            break
    return out


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
    as_of_dt = parse_dt(data.get("as_of"))
    if as_of_dt is None:
        warnings.append("AS_OF_MISSING_OR_INVALID")
    as_of_date = as_of_dt.date() if as_of_dt is not None else None

    collab_raw = data.get("collaboration") if isinstance(data.get("collaboration"), dict) else {}
    collaboration = {
        "collab_id": clean_text(collab_raw.get("collab_id")) if has_text(collab_raw.get("collab_id")) else None,
        "brand": clean_text(collab_raw.get("brand")) if has_text(collab_raw.get("brand")) else None,
        "creator": clean_text(collab_raw.get("creator")) if has_text(collab_raw.get("creator")) else None,
    }

    # ---- assets ----
    assets_raw = data.get("assets")
    raw_assets = assets_raw if isinstance(assets_raw, list) else []
    assets = []
    refused_refs = []
    for index, raw in enumerate(raw_assets):
        path = "assets[%d]" % index
        if not isinstance(raw, dict):
            assets.append({"asset_id": None, "path": path, "base": None, "kind": None,
                           "invalid": True, "refused": False, "flags": ["INVALID_ASSET_RECORD"]})
            continue
        flags = []
        asset_id = clean_text(raw.get("asset_id")) if has_text(raw.get("asset_id")) else None
        if asset_id is None:
            flags.append("MISSING_ASSET_ID")
        ok, base, reason = safe_basename(raw.get("basename"))
        refused = not ok
        if refused:
            flags.append("REFUSED_BASENAME_" + str(reason))
            refused_refs.append({"path": path + "/basename"})
        assets.append({
            "asset_id": asset_id, "path": path, "base": base,
            "kind": clean_text(raw.get("kind")) if has_text(raw.get("kind")) else None,
            "source_note": clean_text(raw.get("source_note")) if has_text(raw.get("source_note")) else None,
            "invalid": bool(asset_id is None or refused), "refused": refused,
            "flags": sorted(set(flags)),
        })

    known_assets = {a["asset_id"] for a in assets if a["asset_id"]}

    # ---- grants ----
    grants_raw = data.get("grants")
    grants_missing = grants_raw is None or not isinstance(grants_raw, list) or len(grants_raw) == 0
    raw_grants = grants_raw if isinstance(grants_raw, list) else []
    grants = [normalise_grant(raw, index, as_of_date, known_assets)
              for index, raw in enumerate(raw_grants)]

    grouped_grants = {}
    grant_order = []
    duplicate_grant_ids = []
    survivors = []
    for grant in grants:
        key = grant["grant_id"]
        if key not in grouped_grants:
            grouped_grants[key] = []
            grant_order.append(key)
        grouped_grants[key].append(grant)
    for key in grant_order:
        group = grouped_grants[key]
        if len(group) > 1:
            duplicate_grant_ids.append({
                "grant_id": key,
                "occurrences": len(group),
                "paths": sorted(g["path"] for g in group),
            })
            primary = group[0]
            primary["flags"].append("DUPLICATE_GRANT_ID")
            survivors.append(primary)
        else:
            survivors.append(group[0])

    conflicts, unknown_overlap = detect_conflicts(survivors)

    conflicting_grant_ids = set()
    for conflict in conflicts:
        conflicting_grant_ids.update(conflict["grant_ids"])

    # ---- status per grant ----
    status_counts = {name: 0 for name in GRANT_STATUSES}
    for grant in survivors:
        # Priority: a scope contradiction needs a human first, then a record that
        # cannot be read at all, then an expired window, then an incomplete one.
        if grant["grant_id"] in conflicting_grant_ids:
            grant["status"] = "CONFLICT"
        elif grant["invalid"]:
            grant["status"] = "INVALID"
        elif grant["time_state"] == "EXPIRED":
            grant["status"] = "EXPIRED"
        elif any(flag.startswith("UNKNOWN_SCOPE_") or flag in
                 ("MODIFICATIONS_UNKNOWN", "ATTRIBUTION_REQUIREMENT_UNKNOWN",
                  "MISSING_START_DATE", "END_DATE_UNKNOWN") for flag in grant["flags"]):
            grant["status"] = "INCOMPLETE"
        else:
            grant["status"] = "DEFINED"
        status_counts[grant["status"]] += 1

    expired_grants = [g["grant_id"] for g in survivors if g["time_state"] == "EXPIRED"]
    expiring_soon = [g["grant_id"] for g in survivors if g["time_state"] == "EXPIRING_SOON"]
    ends_today = [g["grant_id"] for g in survivors if g["time_state"] == "ACTIVE_ENDS_TODAY"]
    end_date_unknown = [g["grant_id"] for g in survivors
                        if g["end_date"] is None and g["start_date"] is not None]
    not_yet_started = [g["grant_id"] for g in survivors if g["time_state"] == "NOT_YET_STARTED"]

    # ---- expiry timeline (sorted, null end dates last) ----
    def timeline_bucket(grant):
        if grant["time_state"] == "NOT_YET_STARTED":
            return "not_yet_started"
        if grant["end_date"] is None:
            return "unknown_end"
        if grant["end_date"] < as_of_date:
            return "expired"
        remaining = (grant["end_date"] - as_of_date).days
        if remaining <= 30:
            return "within_30_days"
        if remaining <= 60:
            return "within_60_days"
        if remaining <= 90:
            return "within_90_days"
        return None

    timeline = {"expired": [], "within_30_days": [], "within_60_days": [],
                "within_90_days": [], "unknown_end": [], "not_yet_started": []}
    if as_of_date is not None:
        for grant in sorted(survivors, key=lambda g: (
                g["end_date"] is None, g["end_date"] or date.min, g["grant_id"] or "")):
            bucket = timeline_bucket(grant)
            if bucket:
                timeline[bucket].append(grant["grant_id"])

    # ---- attribution / renewal / evidence / orphans ----
    attribution_gaps = [g["grant_id"] for g in survivors if "ATTRIBUTION_TEXT_MISSING" in g["flags"]]
    renewal_not_stated = [g["grant_id"] for g in survivors if "RENEWAL_NOT_STATED" in g["flags"]]
    source_evidence_not_stated = [g["grant_id"] for g in survivors if "SOURCE_EVIDENCE_NOT_STATED" in g["flags"]]
    granted_asset_ids = {g["asset_id"] for g in survivors if g["asset_id"]}
    orphan_assets = [a["asset_id"] for a in assets if a["asset_id"] and a["asset_id"] not in granted_asset_ids]
    dangling_grants = [g["grant_id"] for g in survivors if "DANGLING_ASSET_ID" in g["flags"]]

    # ---- requests ----
    requests_raw = data.get("requests") if isinstance(data.get("requests"), list) else []
    grants_by_asset = {}
    for grant in survivors:
        grants_by_asset.setdefault(grant["asset_id"], []).append(grant)

    requests = []
    for index, raw in enumerate(requests_raw):
        if not isinstance(raw, dict):
            continue
        path = "requests[%d]" % index
        request_id = clean_text(raw.get("request_id")) if has_text(raw.get("request_id")) else None
        planned_raw = raw.get("planned_on")
        planned_date = parse_date(planned_raw)
        flags = []
        if request_id is None:
            flags.append("MISSING_REQUEST_ID")
        if has_text(planned_raw) and planned_date is None:
            flags.append("INVALID_PLANNED_ON")
        requests.append({
            "request_id": request_id, "path": path,
            "asset_id": clean_text(raw.get("asset_id")) if has_text(raw.get("asset_id")) else None,
            "party": clean_text(raw.get("party")) if has_text(raw.get("party")) else None,
            "channel": clean_text(raw.get("channel")) if has_text(raw.get("channel")) else None,
            "territory": clean_text(raw.get("territory")) if has_text(raw.get("territory")) else None,
            "purpose": clean_text(raw.get("purpose")) if has_text(raw.get("purpose")) else None,
            "modifications": text_list(raw.get("modifications")),
            "planned_on": clean_text(planned_raw) if has_text(planned_raw) else None,
            "planned_date": planned_date,
            "flags": flags,
            "invalid": bool(request_id is None or (has_text(planned_raw) and planned_date is None)),
        })

    all_requests = [evaluate_request(r, grants_by_asset, conflicting_grant_ids) for r in requests]
    over_scope_candidates = [r for r in all_requests if r["result"] in
                             ("OUT_OF_SCOPE", "UNKNOWN_CANNOT_CHECK",
                              "NO_ACTIVE_WINDOW", "COVERED_BUT_CONFLICTED")]

    # ---- state ----
    if refused_refs or duplicate_grant_ids or dangling_grants:
        status = "BLOCKED"
    elif any(g["invalid"] for g in survivors) or any(g["invalid"] for g in assets):
        status = "BLOCKED"
    elif any(r["invalid"] for r in requests):
        status = "BLOCKED"
    elif as_of_date is None or grants_missing:
        status = "INPUT_INCOMPLETE"
    elif (any(f.startswith("UNKNOWN_SCOPE_") for g in survivors for f in g["flags"])
          or conflicts or unknown_overlap
          or attribution_gaps or renewal_not_stated or source_evidence_not_stated
          or orphan_assets or end_date_unknown or not_yet_started
          or over_scope_candidates
          or any("ATTRIBUTION_REQUIREMENT_UNKNOWN" in g["flags"] for g in survivors)):
        status = "GAPS_FOUND"
    else:
        status = "READY"

    # ---- clarification questions ----
    questions = []

    def ask(topic, text):
        questions.append({"id": "Q-%02d" % (len(questions) + 1), "topic": topic, "question": text})

    if as_of_date is None:
        ask("AS_OF", "请提供带时区偏移的 as_of（例如 2026-09-27T20:00:00+08:00），否则无法比较起止日期。")
    if grants_missing:
        ask("GRANTS", "没有提供任何使用范围记录，请确认本次是否确实没有需要登记的范围。")
    if refused_refs:
        ask("BASENAME_REFUSED", "部分附件引用不是纯文件名（含路径或链接），已拒绝并只保留文件名；请改为单一文件名后重新提交。")
    if duplicate_grant_ids:
        ask("DUPLICATE_GRANT_ID", "以下范围编号重复出现，请确认是否为同一条记录："
            + "、".join(str(d["grant_id"]) for d in duplicate_grant_ids))
    if dangling_grants:
        ask("DANGLING_GRANT", "以下范围记录指向未登记的资产编号，请补齐资产或修正编号："
            + "、".join(str(x) for x in dangling_grants))
    for grant in survivors:
        missing_scope = [f for f in grant["flags"] if f.startswith("UNKNOWN_SCOPE_")]
        if missing_scope:
            ask("UNKNOWN_SCOPE", "%s 未登记 %s，请补齐；缺失的范围不会被当作「全渠道永久」。" % (
                grant["grant_id"] or grant["path"],
                "、".join(f.replace("UNKNOWN_SCOPE_", "").lower() for f in missing_scope)))
    if any("MODIFICATIONS_UNKNOWN" in g["flags"] for g in survivors):
        ask("MODIFICATIONS", "部分范围记录没有说明允许的修改方式（裁剪 / 字幕 / 翻译 / 二次剪辑 / 原始素材 / 改音），"
                             "缺失不会被当作「不允许修改」，也不会被当作「随意修改」。")
    if any("MISSING_START_DATE" in g["flags"] for g in survivors):
        ask("START_DATE", "部分范围记录没有开始日期，请补齐；缺失时无法判断生效窗口。")
    if end_date_unknown:
        ask("END_DATE", "以下范围记录没有结束日期，请补齐；缺失一律记为「结束日未知」，不会当作永久授权："
            + "、".join(str(x) for x in end_date_unknown))
    if any("INVALID_START_DATE" in g["flags"] or "INVALID_END_DATE" in g["flags"] for g in survivors):
        ask("DATE_FORMAT", "部分日期不可解析，请改用 YYYY-MM-DD（例如 2026-12-31）。")
    if any("INVALID_DATE_RANGE" in g["flags"] for g in survivors):
        ask("DATE_RANGE", "部分范围的开始日期晚于结束日期，请修正后再核对。")
    if conflicts:
        ask("SCOPE_CONFLICT", "以下资产在同一时间段内出现了互相冲突的范围记录，请确认以哪一条为准："
            + "、".join("%s（%s）" % (c["asset_id"], " / ".join(c["grant_ids"])) for c in conflicts))
    if unknown_overlap:
        ask("OVERLAP_UNKNOWN", "以下资产的两条范围记录因日期不完整而无法比较是否重叠，请补齐日期："
            + "、".join("%s（%s）" % (u["asset_id"], " / ".join(u["grant_ids"])) for u in unknown_overlap))
    if attribution_gaps:
        ask("ATTRIBUTION_TEXT", "以下范围要求署名但没有署名文案，请补充对外的署名写法："
            + "、".join(str(x) for x in attribution_gaps))
    if any("ATTRIBUTION_REQUIREMENT_UNKNOWN" in g["flags"] for g in survivors):
        ask("ATTRIBUTION_REQUIREMENT", "部分范围记录没有说明是否需要署名，请确认。")
    if renewal_not_stated:
        ask("RENEWAL", "以下范围记录没有写明到期后如何处理，请确认是否续期、停止使用或再议："
            + "、".join(str(x) for x in renewal_not_stated))
    if source_evidence_not_stated:
        ask("SOURCE_EVIDENCE", "以下范围记录没有来源说明，请注明依据来自哪份沟通或文件（只写名称，不要粘贴原文）："
            + "、".join(str(x) for x in source_evidence_not_stated))
    if orphan_assets:
        ask("ORPHAN_ASSET", "以下资产没有任何范围记录，请确认是否允许使用以及范围："
            + "、".join(str(x) for x in orphan_assets))
    if over_scope_candidates:
        ask("OVER_SCOPE", "以下新请求与登记范围不一致或无法核对，请先确认范围再执行："
            + "、".join(str(r["request_id"] or r["path"]) for r in over_scope_candidates))
    if any(r["invalid"] for r in requests):
        ask("REQUEST_RECORD", "部分请求记录的编号缺失或计划日期不可解析，请修正。")

    markdown = render_markdown(
        status=status, collaboration=collaboration, as_of=data.get("as_of"),
        as_of_date=as_of_date, survivors=survivors, assets=assets,
        status_counts=status_counts, conflicts=conflicts, timeline=timeline,
        attribution_gaps=attribution_gaps, renewal_not_stated=renewal_not_stated,
        source_evidence_not_stated=source_evidence_not_stated,
        orphan_assets=orphan_assets, dangling_grants=dangling_grants,
        all_requests=all_requests, questions=questions, warnings=warnings,
        ends_today=ends_today,
    )

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(data.get("as_of")) if has_text(data.get("as_of")) else None,
        "as_of_date": str(as_of_date) if as_of_date is not None else None,
        "collaboration": collaboration,
        "grant_count": len(survivors),
        "asset_count": len(assets),
        "assets": [asset_view(a) for a in assets],
        "status_counts": status_counts,
        "scope_matrix": [scope_view(g) for g in survivors],
        "conflicts": conflicts,
        "unknown_overlap_pairs": unknown_overlap,
        "expired_grants": expired_grants,
        "expiring_soon": expiring_soon,
        "ends_today": ends_today,
        "end_date_unknown": end_date_unknown,
        "not_yet_started": not_yet_started,
        "expiry_timeline": timeline,
        "over_scope_candidates": over_scope_candidates,
        "all_requests": all_requests,
        "attribution_gaps": attribution_gaps,
        "renewal_not_stated": renewal_not_stated,
        "source_evidence_not_stated": source_evidence_not_stated,
        "orphan_assets": orphan_assets,
        "dangling_grants": dangling_grants,
        "duplicate_grant_ids": duplicate_grant_ids,
        "refused_refs": refused_refs,
        "clarification_questions": questions,
        "injection_flagged": injection_flagged,
        "input_warnings": warnings,
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
    }


def asset_view(asset):
    return {
        "asset_id": asset["asset_id"],
        "path": asset["path"],
        "basename": asset["base"],
        "kind": asset["kind"],
        "source_note": asset["source_note"],
        "review_flags": sorted(set(asset["flags"])),
    }


def scope_view(grant):
    return {
        "grant_id": grant["grant_id"],
        "path": grant["path"],
        "asset_id": grant["asset_id"],
        "status": grant["status"],
        "time_state": grant["time_state"],
        "authorized_parties": list(grant["authorized_parties"]),
        "channels": list(grant["channels"]),
        "territory": list(grant["territory"]),
        "purposes": list(grant["purposes"]),
        "modifications": list(grant["modifications"]),
        "starts_on": grant["starts_on"],
        "ends_on": grant["ends_on"],
        "attribution_required": grant["attribution_required"],
        "attribution_text": grant["attribution_text"],
        "renewal_note": grant["renewal_note"],
        "source_evidence": grant["source_evidence"],
        "unknown_fields": sorted(
            f.replace("UNKNOWN_SCOPE_", "").lower() for f in grant["flags"]
            if f.startswith("UNKNOWN_SCOPE_")),
        "review_flags": sorted(set(grant["flags"])),
    }


def render_markdown(status, collaboration, as_of, as_of_date, survivors, assets,
                    status_counts, conflicts, timeline, attribution_gaps,
                    renewal_not_stated, source_evidence_not_stated, orphan_assets,
                    dangling_grants, all_requests, questions, warnings, ends_today):
    lines = []
    title = collaboration["brand"] or collaboration["collab_id"] or "未命名合作"
    lines.append("# 内容使用范围地图 — %s" % esc(title))
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 基准日：%s" % (str(as_of_date) if as_of_date else "未提供"))
    lines.append("- 创作者：%s" % (esc(collaboration["creator"]) if collaboration["creator"] else "未提供"))
    lines.append("")
    lines.append("> 本表只整理登记内容与范围差异，不是法律意见，不解释条款效力、不起草合同、不判断是否侵权。")
    lines.append("")

    lines.append("## 范围状态汇总")
    lines.append("")
    lines.append("| 状态 | 数量 |")
    lines.append("|---|---:|")
    for name in GRANT_STATUSES:
        lines.append("| %s | %d |" % (name, status_counts[name]))
    lines.append("")

    lines.append("## 资产 × 使用范围矩阵")
    lines.append("")
    if survivors:
        lines.append("| 范围编号 | 资产 | 状态 | 使用主体 | 渠道 | 地域 | 用途 | 修改 | 起 | 止 | 时间状态 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for grant in survivors:
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                esc(grant["grant_id"]) if grant["grant_id"] else "未提供",
                esc(grant["asset_id"]) if grant["asset_id"] else "未提供",
                grant["status"],
                esc("、".join(grant["authorized_parties"])) if grant["authorized_parties"] else "未知",
                esc("、".join(grant["channels"])) if grant["channels"] else "未知",
                esc("、".join(grant["territory"])) if grant["territory"] else "未知",
                esc("、".join(grant["purposes"])) if grant["purposes"] else "未知",
                esc("、".join(grant["modifications"])) if grant["modifications"] else "未知",
                esc(grant["starts_on"]) if grant["starts_on"] else "未知",
                esc(grant["ends_on"]) if grant["ends_on"] else "未知",
                grant["time_state"],
            ))
    else:
        lines.append("- 无范围记录")
    lines.append("")

    if assets:
        lines.append("## 资产清单")
        lines.append("")
        lines.append("| 资产编号 | 文件名 | 类型 |")
        lines.append("|---|---|---|")
        for asset in assets:
            lines.append("| %s | %s | %s |" % (
                esc(asset["asset_id"]) if asset["asset_id"] else "未提供",
                esc(asset["base"]) if asset["base"] else "已拒绝（仅保留文件名，未回显原引用）",
                esc(asset["kind"]) if asset["kind"] else "未提供",
            ))
        lines.append("")

    if conflicts:
        lines.append("## 范围冲突（需人工裁决）")
        lines.append("")
        for conflict in conflicts:
            lines.append("- 资产 %s：范围 %s 在 %s 至 %s 重叠" % (
                esc(conflict["asset_id"]), esc(" 与 ".join(conflict["grant_ids"])),
                esc(conflict["overlap"]["from"]), esc(conflict["overlap"]["to"])))
            for diff in conflict["differences"]:
                lines.append("  - %s：%s ↔ %s" % (
                    esc(diff["field"]),
                    esc("、".join(diff["values"])),
                    esc("、".join(diff["other_values"]))))
        lines.append("")

    lines.append("## 到期时间线")
    lines.append("")
    for key, label in (("expired", "已到期"), ("within_30_days", "30 天内到期"),
                       ("within_60_days", "60 天内到期"), ("within_90_days", "90 天内到期"),
                       ("unknown_end", "结束日未知"), ("not_yet_started", "尚未生效")):
        values = timeline[key]
        lines.append("- %s：%s" % (label, "、".join(esc(str(v)) for v in values) if values else "无"))
    if ends_today:
        lines.append("- 基准日当天到期：%s" % "、".join(esc(str(v)) for v in ends_today))
    lines.append("")

    lines.append("## 新请求的范围核对")
    lines.append("")
    if all_requests:
        lines.append("| 请求 | 资产 | 结果 | 覆盖范围 | 明细 |")
        lines.append("|---|---|---|---|---|")
        for request in all_requests:
            detail = "；".join("%s=%s" % (c["field"], c["result"]) for c in request["checks"]) or "—"
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(request["request_id"]) if request["request_id"] else "未提供",
                esc(request["asset_id"]) if request["asset_id"] else "未提供",
                request["result"],
                esc("、".join(str(g) for g in request["covering_grant_ids"])) if request["covering_grant_ids"] else "—",
                esc(detail),
            ))
        lines.append("")
        lines.append("> 上表只做登记范围比对：`COVERED` 表示所请求的字段都在登记范围内，"
                     "`COVERED_BUT_CONFLICTED` 表示所依据的范围记录本身与他条冲突、需先裁决，"
                     "`OUT_OF_SCOPE` 表示至少一个字段不在登记范围内，"
                     "`NO_ACTIVE_WINDOW` 表示没有任何登记窗口覆盖该日期，"
                     "`UNKNOWN_CANNOT_CHECK` 表示登记信息不足、无法比对。以上都不是法律结论。")
    else:
        lines.append("- 未提交新请求")
    lines.append("")

    lines.append("## 缺口清单")
    lines.append("")
    lines.append("- 要求署名但缺署名文案：%s" % (
        "、".join(esc(str(x)) for x in attribution_gaps) if attribution_gaps else "无"))
    lines.append("- 未写明到期后处理：%s" % (
        "、".join(esc(str(x)) for x in renewal_not_stated) if renewal_not_stated else "无"))
    lines.append("- 未写明来源依据：%s" % (
        "、".join(esc(str(x)) for x in source_evidence_not_stated) if source_evidence_not_stated else "无"))
    lines.append("- 无范围记录的资产：%s" % (
        "、".join(esc(str(x)) for x in orphan_assets) if orphan_assets else "无"))
    lines.append("- 指向未登记资产的范围：%s" % (
        "、".join(esc(str(x)) for x in dangling_grants) if dangling_grants else "无"))
    lines.append("")

    if any(has_text(g.get("renewal_note")) or has_text(g.get("source_evidence"))
           or has_text(g.get("notes")) for g in survivors):
        lines.append("## 来源依据与续期说明")
        lines.append("")
        for grant in survivors:
            label = esc(grant["grant_id"]) if grant["grant_id"] else grant["path"]
            if has_text(grant.get("source_evidence")):
                lines.append("- %s 来源依据：%s" % (label, hidden(grant["source_evidence"])))
            if has_text(grant.get("renewal_note")):
                lines.append("- %s 续期说明：%s" % (label, hidden(grant["renewal_note"])))
            if has_text(grant.get("notes")):
                lines.append("- %s 备注：%s" % (label, hidden(grant["notes"])))
        lines.append("")

    lines.append("## 待澄清问题")
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
