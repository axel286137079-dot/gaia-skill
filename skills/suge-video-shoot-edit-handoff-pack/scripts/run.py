#!/usr/bin/env python3
"""短视频拍摄到剪辑交接包 — offline shoot-to-edit handoff builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no command
execution, no media decoding: only file *basenames* are ever handled.

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
SHOT_STATES = ("planned", "shot", "missing", "reshoot", "unknown")
ASSET_KINDS = ("video", "audio", "image", "graphics", "other")
ASSET_STATES = ("available", "missing", "unknown")

COVERAGE_STATES = ("COVERED", "COVERED_UNVERIFIED", "NOT_SHOT", "MISSING", "RESHOOT", "UNKNOWN")

COVERAGE_ORDER = ("COVERED", "COVERED_UNVERIFIED", "NOT_SHOT", "MISSING", "RESHOOT", "UNKNOWN")

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
# the SAME sentence, so ordinary production notes are not mislabelled.
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

# A basename must be a single path segment ending in a media-ish file name.
BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

DISCLAIMER = (
    "本输出是拍摄与剪辑交接的信息核对材料，不是版权、肖像权或平台合规结论；"
    "素材能否对外使用，以权利方的书面许可和平台规则为准。"
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    """Collapse control characters and whitespace; never raises.

    NFKC is deliberately NOT applied: it would rewrite Chinese full-width
    punctuation and degrade the sheet the user actually hands over.
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


def read_bool(value):
    """Tri-state boolean: True / False / None (unknown)."""
    if isinstance(value, bool):
        return value
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
    """Return (ok, safe_name, reason).

    The raw reference is NEVER returned: a refused value must not be echoed into
    any output field, not even the structured JSON.
    """
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
        "project": {},
        "shot_count": 0,
        "coverage_counts": {name: 0 for name in COVERAGE_STATES},
        "shots": [],
        "coverage_matrix": [],
        "missing_shots": [],
        "reshoot_shots": [],
        "confirm_items": [],
        "shots_without_asset": [],
        "asset_count": 0,
        "assets": [],
        "asset_conflicts": [],
        "duplicate_asset_ids": [],
        "refused_refs": [],
        "dangling_refs": [],
        "brand_elements": [],
        "brand_element_gaps": [],
        "edit_order": [],
        "duration_known_shots": 0,
        "duration_unknown_shots": 0,
        "total_duration_sec": None,
        "handoff": {"by": None, "to": None},
        "responsibility": [],
        "clarification_questions": [],
        "injection_flagged": [],
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "markdown_summary": (
            "# 短视频拍摄到剪辑交接包\n\n"
            "- 状态：**REJECTED**\n"
            "- 原因：输入疑似包含凭据（密钥 / 令牌 / 密码）。\n"
            "- 处理：已拒绝处理，**未回显**任何疑似凭据内容。\n\n"
            "> 请移除凭据字段后重新提交。本工具不需要也不需要保存任何登录凭据。\n"
        ),
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# shot / asset level work
# --------------------------------------------------------------------------
def coverage_of(status, take_ids):
    if status == "shot":
        return "COVERED" if take_ids else "COVERED_UNVERIFIED"
    if status == "missing":
        return "MISSING"
    if status == "reshoot":
        return "RESHOOT"
    if status == "planned":
        return "NOT_SHOT"
    return "UNKNOWN"


def normalise_shot(raw, index, ctx):
    path = "shots[%d]" % index
    if not isinstance(raw, dict):
        return {
            "shot_id": None, "index": index, "path": path, "invalid": True,
            "flags": ["INVALID_SHOT_RECORD"], "status": None, "description": "",
            "narrative_order": None, "order_source": "none", "duration_sec": None,
            "duration_state": "unknown", "take_ids": [], "owner": None,
            "due_at": None, "due_at_parsed": None, "is_overdue": False,
            "subtitles_required": None, "audio_note": None, "brand_elements": [],
            "coverage": "UNKNOWN", "notes": None, "injection": False,
        }

    flags = []
    raw_id = raw.get("shot_id")
    shot_id = clean_text(raw_id) if has_text(raw_id) else None
    if shot_id is None:
        flags.append("MISSING_SHOT_ID")

    status = clean_text(raw.get("status")).lower() if has_text(raw.get("status")) else None
    if status is None:
        flags.append("MISSING_STATUS")
    elif status not in SHOT_STATES:
        flags.append("INVALID_STATUS")

    description = clean_text(raw.get("description")) if has_text(raw.get("description")) else ""
    if not description:
        flags.append("MISSING_DESCRIPTION")

    order = None
    order_source = "none"
    if "narrative_order" in raw and raw.get("narrative_order") is not None:
        parsed_order = dec(raw.get("narrative_order"))
        if parsed_order is None or parsed_order != parsed_order.to_integral_value() or parsed_order < 0:
            flags.append("INVALID_NARRATIVE_ORDER")
        else:
            order = int(parsed_order)
            order_source = "stated"
    else:
        flags.append("ORDER_DERIVED_FROM_INPUT")
        order_source = "derived"

    duration_sec = None
    duration_state = "unknown"
    if "duration_sec" in raw and raw.get("duration_sec") is not None:
        parsed_duration = dec(raw.get("duration_sec"))
        if parsed_duration is None:
            flags.append("INVALID_DURATION")
            duration_state = "invalid"
        elif parsed_duration < 0:
            flags.append("NEGATIVE_DURATION")
            duration_state = "invalid"
        else:
            duration_sec = quant(parsed_duration, 2)
            duration_state = "known"
    else:
        flags.append("DURATION_UNKNOWN")

    take_ids = []
    raw_takes = raw.get("take_ids")
    if isinstance(raw_takes, list):
        for entry in raw_takes:
            if has_text(entry):
                take_ids.append(clean_text(entry))

    owner = clean_text(raw.get("owner")) if has_text(raw.get("owner")) else None
    if owner is None:
        flags.append("OWNER_MISSING")

    due_raw = raw.get("due_at")
    due_at = parse_dt(due_raw)
    if has_text(due_raw) and due_at is None:
        flags.append("INVALID_DUE_AT")

    subtitles_required = read_bool(raw.get("subtitles_required"))
    if "subtitles_required" not in raw or subtitles_required is None:
        # A stated null is "we were not told", exactly like a missing key. Neither
        # one may be read as "no subtitles needed".
        flags.append("SUBTITLE_REQUIREMENT_UNKNOWN")

    audio_note = clean_text(raw.get("audio_note")) if has_text(raw.get("audio_note")) else None
    if audio_note is None:
        flags.append("AUDIO_NOTE_MISSING")

    brand_elements = []
    raw_elements = raw.get("brand_elements")
    if isinstance(raw_elements, list):
        for entry in raw_elements:
            if has_text(entry):
                brand_elements.append(clean_text(entry))

    coverage = coverage_of(status, take_ids)
    if coverage == "COVERED_UNVERIFIED":
        flags.append("TAKE_IDS_MISSING")

    is_overdue = bool(
        due_at is not None and ctx["as_of"] is not None
        and due_at < ctx["as_of"] and status != "shot"
    )

    invalid = bool(
        shot_id is None or status is None or status not in SHOT_STATES
        or duration_state == "invalid" or "INVALID_NARRATIVE_ORDER" in flags
    )

    return {
        "shot_id": shot_id, "index": index, "path": path, "invalid": invalid,
        "flags": flags, "status": status, "description": description,
        "narrative_order": order, "order_source": order_source,
        "duration_sec": duration_sec, "duration_state": duration_state,
        "take_ids": take_ids, "owner": owner,
        "due_at": clean_text(due_raw) if has_text(due_raw) else None,
        "due_at_parsed": due_at, "is_overdue": is_overdue,
        "subtitles_required": subtitles_required, "audio_note": audio_note,
        "brand_elements": brand_elements, "coverage": coverage,
        "notes": raw.get("notes"), "injection": injection_hit(raw.get("notes")),
    }


def normalise_asset(raw, index):
    path = "assets[%d]" % index
    if not isinstance(raw, dict):
        return {
            "asset_id": None, "index": index, "path": path, "invalid": True,
            "flags": ["INVALID_ASSET_RECORD"], "base": None, "kind": None,
            "version": None, "status": None, "shot_refs": [], "owner": None,
            "refused": False, "injection": False,
        }

    flags = []
    raw_id = raw.get("asset_id")
    asset_id = clean_text(raw_id) if has_text(raw_id) else None
    if asset_id is None:
        flags.append("MISSING_ASSET_ID")

    ok, base, reason = safe_basename(raw.get("basename"))
    refused = not ok
    if refused:
        flags.append("REFUSED_BASENAME_" + str(reason))
        if base is None:
            base = None

    kind = clean_text(raw.get("kind")).lower() if has_text(raw.get("kind")) else None
    if kind is None:
        flags.append("MISSING_ASSET_KIND")
    elif kind not in ASSET_KINDS:
        flags.append("INVALID_ASSET_KIND")

    version = None
    if "version" in raw and raw.get("version") is not None:
        version = clean_text(raw.get("version"))
        if not version:
            version = None
            flags.append("INVALID_ASSET_VERSION")
    else:
        flags.append("ASSET_VERSION_UNKNOWN")

    status = clean_text(raw.get("status")).lower() if has_text(raw.get("status")) else None
    if status is None:
        flags.append("MISSING_ASSET_STATUS")
    elif status not in ASSET_STATES:
        flags.append("INVALID_ASSET_STATUS")
    if status == "unknown":
        flags.append("ASSET_STATUS_UNKNOWN")

    shot_refs = []
    raw_refs = raw.get("shot_refs")
    if isinstance(raw_refs, list):
        for entry in raw_refs:
            if has_text(entry):
                shot_refs.append(clean_text(entry))

    owner = clean_text(raw.get("owner")) if has_text(raw.get("owner")) else None
    if owner is None:
        flags.append("OWNER_MISSING")

    invalid = bool(
        asset_id is None or refused or kind is None or kind not in ASSET_KINDS
        or status is None or status not in ASSET_STATES
    )

    return {
        "asset_id": asset_id, "index": index, "path": path, "invalid": invalid,
        "flags": flags, "base": base, "kind": kind, "version": version,
        "status": status, "shot_refs": shot_refs, "owner": owner,
        "refused": refused, "injection": False,
    }


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

    project_raw = data.get("project") if isinstance(data.get("project"), dict) else {}
    project = {
        "project_id": clean_text(project_raw.get("project_id")) if has_text(project_raw.get("project_id")) else None,
        "name": clean_text(project_raw.get("name")) if has_text(project_raw.get("name")) else None,
        "platform": clean_text(project_raw.get("platform")) if has_text(project_raw.get("platform")) else None,
        "aspect_ratio": clean_text(project_raw.get("aspect_ratio")) if has_text(project_raw.get("aspect_ratio")) else None,
        "version": clean_text(project_raw.get("version")) if has_text(project_raw.get("version")) else None,
        "editor": clean_text(project_raw.get("editor")) if has_text(project_raw.get("editor")) else None,
        "due_at": clean_text(project_raw.get("due_at")) if has_text(project_raw.get("due_at")) else None,
    }
    project_due = parse_dt(project_raw.get("due_at"))
    if has_text(project_raw.get("due_at")) and project_due is None:
        warnings.append("INVALID_PROJECT_DUE_AT")
    if project["aspect_ratio"] is None:
        warnings.append("ASPECT_RATIO_UNKNOWN")
    if project["version"] is None:
        warnings.append("PROJECT_VERSION_UNKNOWN")

    handoff_raw = data.get("handoff") if isinstance(data.get("handoff"), dict) else {}
    handoff = {
        "by": clean_text(handoff_raw.get("by")) if has_text(handoff_raw.get("by")) else None,
        "to": clean_text(handoff_raw.get("to")) if has_text(handoff_raw.get("to")) else None,
    }

    shots_raw = data.get("shots")
    shots_missing = shots_raw is None or not isinstance(shots_raw, list) or len(shots_raw) == 0
    raw_shots = shots_raw if isinstance(shots_raw, list) else []

    assets_raw = data.get("assets")
    raw_assets = assets_raw if isinstance(assets_raw, list) else []

    ctx = {"as_of": as_of}
    shots = [normalise_shot(raw, index, ctx) for index, raw in enumerate(raw_shots)]
    assets = [normalise_asset(raw, index) for index, raw in enumerate(raw_assets)]

    known_shot_ids = {s["shot_id"] for s in shots if s["shot_id"]}
    known_asset_ids = {a["asset_id"] for a in assets if a["asset_id"]}

    # ---- dangling references ----
    dangling = []
    for shot in shots:
        for take in shot["take_ids"]:
            if take not in known_asset_ids:
                shot["flags"].append("DANGLING_TAKE_REF")
                dangling.append({"path": shot["path"] + "/take_ids", "ref": take,
                                 "kind": "UNKNOWN_ASSET_ID"})
    for asset in assets:
        for ref in asset["shot_refs"]:
            if ref not in known_shot_ids:
                asset["flags"].append("DANGLING_SHOT_REF")
                dangling.append({"path": asset["path"] + "/shot_refs", "ref": ref,
                                 "kind": "UNKNOWN_SHOT_ID"})

    # ---- asset grouping: version conflicts and exact duplicates ----
    grouped = {}
    order = []
    for asset in assets:
        key = asset["asset_id"]
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(asset)

    asset_conflicts = []
    duplicate_asset_ids = []
    survivors = []
    for key in order:
        group = grouped[key]
        if len(group) > 1:
            versions = sorted({str(a["version"]) for a in group})
            if len(versions) > 1:
                asset_conflicts.append({
                    "asset_id": key,
                    "versions": versions,
                    "occurrences": len(group),
                    "paths": sorted(a["path"] for a in group),
                })
                primary = group[0]
                primary["flags"].append("CONFLICTING_ASSET_VERSION")
                survivors.append(primary)
            else:
                duplicate_asset_ids.append({
                    "asset_id": key,
                    "version": versions[0] if versions else None,
                    "occurrences": len(group),
                    "paths": sorted(a["path"] for a in group),
                })
                primary = group[0]
                primary["flags"].append("DUPLICATE_ASSET_ID")
                survivors.append(primary)
        else:
            survivors.append(group[0])

    refused_refs = [{"path": a["path"] + "/basename"} for a in assets if a["refused"]]

    # ---- assets referenced by at least one shot ----
    referenced = set()
    for shot in shots:
        for take in shot["take_ids"]:
            referenced.add(take)
    for asset in survivors:
        for ref in asset["shot_refs"]:
            referenced.add(asset["asset_id"])

    shots_without_asset = []
    for shot in shots:
        if not shot["shot_id"]:
            continue
        linked = bool(shot["take_ids"])
        if not linked:
            for asset in survivors:
                if shot["shot_id"] in asset["shot_refs"]:
                    linked = True
                    break
        if not linked:
            shots_without_asset.append(shot["shot_id"])

    # ---- brand elements ----
    elements_raw = data.get("brand_elements")
    brand_elements = []
    brand_element_gaps = []
    if isinstance(elements_raw, list):
        for index, entry in enumerate(elements_raw):
            if not isinstance(entry, dict):
                continue
            element_id = clean_text(entry.get("element_id")) if has_text(entry.get("element_id")) else None
            name = clean_text(entry.get("name")) if has_text(entry.get("name")) else None
            required = read_bool(entry.get("required"))
            available = read_bool(entry.get("available"))
            flags = []
            if element_id is None:
                flags.append("MISSING_ELEMENT_ID")
            if name is None:
                flags.append("MISSING_ELEMENT_NAME")
            if "required" not in entry:
                flags.append("REQUIRED_FLAG_UNKNOWN")
            if available is None:
                flags.append("AVAILABILITY_UNKNOWN")
            if required is True and available is not True:
                flags.append("REQUIRED_ELEMENT_UNAVAILABLE")
                brand_element_gaps.append({
                    "element_id": element_id, "name": name,
                    "reason": "REQUIRED_BUT_NOT_CONFIRMED_AVAILABLE",
                })
            brand_elements.append({
                "element_id": element_id, "name": name, "required": required,
                "available": available, "version": clean_text(entry.get("version")) if has_text(entry.get("version")) else None,
                "flags": sorted(set(flags)), "index": index,
            })
    referenced_elements = set()
    for shot in shots:
        for element in shot["brand_elements"]:
            referenced_elements.add(element)
    for element in brand_elements:
        if element["element_id"] and element["element_id"] not in referenced_elements:
            element["flags"] = sorted(set(element["flags"] + ["ELEMENT_NOT_REFERENCED"]))

    # ---- coverage aggregation ----
    coverage_counts = {name: 0 for name in COVERAGE_STATES}
    coverage_matrix = []
    missing_shots = []
    reshoot_shots = []
    confirm_items = []
    for shot in shots:
        coverage_counts[shot["coverage"]] += 1
        if shot["coverage"] == "MISSING" or shot["is_overdue"]:
            missing_shots.append(shot["shot_id"])
        if shot["coverage"] == "RESHOOT":
            reshoot_shots.append(shot["shot_id"])
        if shot["coverage"] in ("UNKNOWN", "COVERED_UNVERIFIED") or shot["invalid"]:
            confirm_items.append({
                "shot_id": shot["shot_id"],
                "path": shot["path"],
                "reason": "INVALID_RECORD" if shot["invalid"] else shot["coverage"],
            })
        coverage_matrix.append({
            "shot_id": shot["shot_id"],
            "description": shot["description"],
            "state": shot["coverage"],
            "status": shot["status"],
            "narrative_order": shot["narrative_order"],
            "order_source": shot["order_source"],
            "take_count": len(shot["take_ids"]),
            "duration_sec": str(shot["duration_sec"]) if shot["duration_sec"] is not None else None,
            "owner": shot["owner"],
            "due_at": shot["due_at"],
            "is_overdue": shot["is_overdue"],
            "brand_elements": list(shot["brand_elements"]),
            "flags": sorted(set(shot["flags"])),
        })

    # ---- duration: total only when every shot states a duration ----
    known = [s for s in shots if s["duration_state"] == "known"]
    unknown_count = len(shots) - len(known)
    total_duration = quant(sum((s["duration_sec"] for s in known), Decimal("0")), 2) if not unknown_count and shots else None

    # ---- edit order ----
    ordered = sorted(
        shots,
        key=lambda s: (
            s["narrative_order"] if s["narrative_order"] is not None else 10 ** 6 + s["index"],
            s["index"],
        ),
    )
    ACTION_TEXT = {
        "COVERED": "可直接进入剪辑",
        "COVERED_UNVERIFIED": "剪辑前先确认素材编号",
        "NOT_SHOT": "待拍摄",
        "MISSING": "先补拍",
        "RESHOOT": "重拍后替换",
        "UNKNOWN": "与拍摄负责人确认镜头状态",
    }
    edit_order = []
    position = 0
    for shot in ordered:
        if shot["shot_id"] is None:
            continue
        position += 1
        edit_order.append({
            "position": position,
            "shot_id": shot["shot_id"],
            "state": shot["coverage"],
            "action": ACTION_TEXT[shot["coverage"]],
        })

    # ---- responsibility table ----
    responsibility = []
    for shot in shots:
        responsibility.append({
            "item": shot["shot_id"] or shot["path"],
            "kind": "shot",
            "owner": shot["owner"],
            "due_at": shot["due_at"],
        })
    for asset in survivors:
        responsibility.append({
            "item": asset["asset_id"] or asset["path"],
            "kind": "asset",
            "owner": asset["owner"],
            "due_at": None,
        })

    # ---- state ----
    invalid_shots = [s for s in shots if s["invalid"]]
    invalid_assets = [a for a in survivors if a["invalid"]]
    if asset_conflicts or refused_refs or dangling or invalid_shots or invalid_assets:
        status = "BLOCKED"
    elif as_of is None or shots_missing:
        status = "INPUT_INCOMPLETE"
    elif (unknown_count or any("OWNER_MISSING" in s["flags"] for s in shots)
          or any("OWNER_MISSING" in a["flags"] for a in survivors)
          or brand_element_gaps or shots_without_asset
          or any("SUBTITLE_REQUIREMENT_UNKNOWN" in s["flags"] for s in shots)
          or any("AUDIO_NOTE_MISSING" in s["flags"] for s in shots)
          or project["aspect_ratio"] is None or project["version"] is None
          or handoff["by"] is None or handoff["to"] is None
          or any(a["version"] is None for a in survivors)
          or any(a["status"] == "unknown" for a in survivors)):
        status = "GAPS_FOUND"
    else:
        status = "READY"

    # ---- clarification questions (fixed topic order) ----
    questions = []

    def ask(topic, text):
        questions.append({"id": "Q-%02d" % (len(questions) + 1), "topic": topic, "question": text})

    if as_of is None:
        ask("AS_OF", "请提供带时区偏移的 as_of（例如 2026-09-27T20:00:00+08:00），否则无法判断镜头与项目是否逾期。")
    if shots_missing:
        ask("SHOTS", "没有提供任何镜头记录，请确认本次是否确实无镜头需要交接。")
    if project["aspect_ratio"] is None:
        ask("ASPECT_RATIO", "项目画幅（aspect_ratio）未提供，请确认竖版 9:16、横版 16:9 或方版 1:1 等具体比例，剪辑无法在未知画幅下开工。")
    if project["version"] is None:
        ask("PROJECT_VERSION", "项目版本号（version）未提供，请确认当前是第几版，避免与旧版成片混淆。")
    if asset_conflicts:
        ask("CONFLICTING_ASSET_VERSION", "同一素材编号出现多个版本，请确认以哪一版为准：" +
            "、".join(str(c["asset_id"]) for c in asset_conflicts))
    if duplicate_asset_ids:
        ask("DUPLICATE_ASSET_ID", "以下素材编号重复登记，请确认是否为同一份素材：" +
            "、".join(str(d["asset_id"]) for d in duplicate_asset_ids))
    if refused_refs:
        ask("BASENAME_REFUSED", "部分素材引用不是纯文件名（含路径或链接），已拒绝并只保留文件名；请改为单一文件名后重新提交。")
    if dangling:
        ask("DANGLING_REFERENCE", "部分镜头与素材互相引用的编号不存在，请补齐缺失的镜头编号或素材编号。")
    if missing_shots:
        ask("MISSING_SHOTS", "以下镜头缺失或已过截止时间，请确认补拍责任人与补拍时间：" +
            "、".join(str(x) for x in missing_shots))
    if reshoot_shots:
        ask("RESHOOT_SHOTS", "以下镜头标记为重拍，请确认重拍完成时间与所需场地/人员：" +
            "、".join(str(x) for x in reshoot_shots))
    if shots_without_asset:
        ask("SHOT_WITHOUT_ASSET", "以下镜头没有任何素材引用，请确认是对应素材还没登记，还是确实无素材可剪：" +
            "、".join(str(x) for x in shots_without_asset))
    if unknown_count:
        ask("DURATION", "部分镜头未给出时长，总时长无法合计；本工具不会把未知时长当作 0。")
    if brand_element_gaps:
        ask("BRAND_ELEMENT", "以下必需品牌元素尚未确认可用，请确认素材来源与授权：" +
            "、".join(str(g["element_id"] or g["name"]) for g in brand_element_gaps))
    if any(a["version"] is None for a in survivors):
        ask("ASSET_VERSION", "部分素材未给出版本号，请补齐以便与成片追溯对应。")
    if any(a["status"] == "unknown" for a in survivors):
        ask("ASSET_STATUS", "部分素材状态为未知，请确认已导出、缺失还是待上传。")
    if any("SUBTITLE_REQUIREMENT_UNKNOWN" in s["flags"] for s in shots):
        ask("SUBTITLES", "部分镜头未说明是否需要字幕，请按平台要求补全。")
    if any("AUDIO_NOTE_MISSING" in s["flags"] for s in shots):
        ask("AUDIO", "部分镜头缺少音频说明（同期声 / 配音 / 无音频），请补全。")
    if any("OWNER_MISSING" in s["flags"] for s in shots) or any("OWNER_MISSING" in a["flags"] for a in survivors):
        ask("OWNER", "部分镜头或素材没有责任人，请指派到具体成员，否则交接无法闭环。")
    if handoff["by"] is None or handoff["to"] is None:
        ask("HANDOFF", "请确认交接双方（拍摄方与剪辑方），缺失时无法形成责任交接。")

    markdown = render_markdown(
        status=status, project=project, handoff=handoff, as_of=data.get("as_of"),
        coverage_counts=coverage_counts, coverage_matrix=coverage_matrix,
        missing_shots=missing_shots, reshoot_shots=reshoot_shots,
        confirm_items=confirm_items, asset_conflicts=asset_conflicts,
        duplicate_asset_ids=duplicate_asset_ids, assets=survivors,
        brand_elements=brand_elements, brand_element_gaps=brand_element_gaps,
        edit_order=edit_order, total_duration=total_duration,
        duration_unknown=unknown_count, responsibility=responsibility,
        questions=questions, warnings=warnings, shots=shots,
    )

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(data.get("as_of")) if has_text(data.get("as_of")) else None,
        "project": dict(project),
        "handoff": handoff,
        "shot_count": len(shots),
        "coverage_counts": coverage_counts,
        "shots": [shot_view(s) for s in shots],
        "coverage_matrix": coverage_matrix,
        "missing_shots": missing_shots,
        "reshoot_shots": reshoot_shots,
        "confirm_items": confirm_items,
        "shots_without_asset": shots_without_asset,
        "asset_count": len(assets),
        "assets": [asset_view(a) for a in survivors],
        "asset_conflicts": asset_conflicts,
        "duplicate_asset_ids": duplicate_asset_ids,
        "refused_refs": refused_refs,
        "dangling_refs": dangling,
        "brand_elements": [element_view(e) for e in brand_elements],
        "brand_element_gaps": brand_element_gaps,
        "edit_order": edit_order,
        "duration_known_shots": len(known),
        "duration_unknown_shots": unknown_count,
        "total_duration_sec": str(total_duration) if total_duration is not None else None,
        "responsibility": responsibility,
        "clarification_questions": questions,
        "injection_flagged": injection_flagged,
        "input_warnings": warnings,
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
    }


def shot_view(shot):
    return {
        "shot_id": shot["shot_id"],
        "path": shot["path"],
        "description": shot["description"],
        "status": shot["status"],
        "coverage": shot["coverage"],
        "narrative_order": shot["narrative_order"],
        "order_source": shot["order_source"],
        "duration_sec": str(shot["duration_sec"]) if shot["duration_sec"] is not None else None,
        "take_ids": list(shot["take_ids"]),
        "owner": shot["owner"],
        "due_at": shot["due_at"],
        "is_overdue": shot["is_overdue"],
        "subtitles_required": shot["subtitles_required"],
        "audio_note": shot["audio_note"],
        "brand_elements": list(shot["brand_elements"]),
        "review_flags": sorted(set(shot["flags"])),
    }


def asset_view(asset):
    return {
        "asset_id": asset["asset_id"],
        "path": asset["path"],
        "basename": asset["base"],
        "kind": asset["kind"],
        "version": asset["version"],
        "status": asset["status"],
        "shot_refs": list(asset["shot_refs"]),
        "owner": asset["owner"],
        "review_flags": sorted(set(asset["flags"])),
    }


def element_view(element):
    return {
        "element_id": element["element_id"],
        "name": element["name"],
        "required": element["required"],
        "available": element["available"],
        "version": element["version"],
        "review_flags": sorted(set(element["flags"])),
    }


def render_markdown(status, project, handoff, as_of, coverage_counts, coverage_matrix,
                    missing_shots, reshoot_shots, confirm_items, asset_conflicts,
                    duplicate_asset_ids, assets, brand_elements, brand_element_gaps,
                    edit_order, total_duration, duration_unknown, responsibility,
                    questions, warnings, shots):
    lines = []
    title = project["name"] or project["project_id"] or "未命名项目"
    lines.append("# 短视频拍摄到剪辑交接包 — %s" % esc(title))
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 平台：%s" % (esc(project["platform"]) if project["platform"] else "未提供"))
    lines.append("- 画幅：%s" % (esc(project["aspect_ratio"]) if project["aspect_ratio"] else "未知"))
    lines.append("- 项目版本：%s" % (esc(project["version"]) if project["version"] else "未知"))
    lines.append("- 项目截止：%s" % (esc(project["due_at"]) if project["due_at"] else "未提供"))
    lines.append("- 交接：%s → %s" % (
        esc(handoff["by"]) if handoff["by"] else "未提供",
        esc(handoff["to"]) if handoff["to"] else "未提供",
    ))
    lines.append("- 总时长：%s" % (
        ("%s 秒" % total_duration) if total_duration is not None else "无法合计（%d 个镜头未给时长）" % duration_unknown
    ))
    lines.append("")

    lines.append("## 镜头覆盖")
    lines.append("")
    lines.append("| 覆盖状态 | 数量 |")
    lines.append("|---|---:|")
    for name in COVERAGE_ORDER:
        lines.append("| %s | %d |" % (name, coverage_counts[name]))
    lines.append("")

    lines.append("## 镜头覆盖矩阵")
    lines.append("")
    if coverage_matrix:
        lines.append("| 镜头 | 画面内容 | 状态 | 覆盖 | 素材数 | 时长(秒) | 责任人 | 截止 |")
        lines.append("|---|---|---|---|---:|---|---|---|")
        for row in coverage_matrix:
            lines.append("| %s | %s | %s | %s | %d | %s | %s | %s |" % (
                esc(row["shot_id"]) if row["shot_id"] else "未提供",
                hidden(row["description"]) if row["description"] else "未提供",
                esc(row["status"]) if row["status"] else "未提供",
                row["state"],
                row["take_count"],
                row["duration_sec"] if row["duration_sec"] is not None else "未知",
                esc(row["owner"]) if row["owner"] else "未指派",
                esc(row["due_at"]) if row["due_at"] else "未提供",
            ))
    else:
        lines.append("- 无镜头记录")
    lines.append("")

    lines.append("## 缺拍 / 重拍 / 待确认")
    lines.append("")
    lines.append("- 缺失或逾期镜头：%s" % ("、".join(esc(x) for x in missing_shots) if missing_shots else "无"))
    lines.append("- 需重拍镜头：%s" % ("、".join(esc(x) for x in reshoot_shots) if reshoot_shots else "无"))
    lines.append("- 待确认镜头：%s" % ("、".join(esc(str(c["shot_id"] or c["path"])) for c in confirm_items) if confirm_items else "无"))
    lines.append("")

    lines.append("## 素材命名与版本")
    lines.append("")
    if assets:
        lines.append("| 素材编号 | 文件名 | 类型 | 版本 | 状态 | 责任人 |")
        lines.append("|---|---|---|---|---|---|")
        for asset in assets:
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                esc(asset["asset_id"]) if asset["asset_id"] else "未提供",
                esc(asset["base"]) if asset["base"] else "已拒绝（仅保留文件名，未回显原引用）",
                esc(asset["kind"]) if asset["kind"] else "未提供",
                esc(asset["version"]) if asset["version"] else "未知",
                esc(asset["status"]) if asset["status"] else "未提供",
                esc(asset["owner"]) if asset["owner"] else "未指派",
            ))
    else:
        lines.append("- 无素材记录")
    lines.append("")
    if asset_conflicts:
        lines.append("**版本冲突（阻塞）**")
        lines.append("")
        for conflict in asset_conflicts:
            lines.append("- %s：版本 %s" % (
                esc(conflict["asset_id"]), esc(" / ".join(conflict["versions"]))))
        lines.append("")
    if duplicate_asset_ids:
        lines.append("**重复登记**")
        lines.append("")
        for duplicate in duplicate_asset_ids:
            lines.append("- %s（%d 次）" % (esc(duplicate["asset_id"]), duplicate["occurrences"]))
        lines.append("")

    if brand_elements:
        lines.append("## 品牌元素")
        lines.append("")
        lines.append("| 元素编号 | 名称 | 必需 | 可用 |")
        lines.append("|---|---|---|---|")
        for element in brand_elements:
            lines.append("| %s | %s | %s | %s |" % (
                esc(element["element_id"]) if element["element_id"] else "未提供",
                hidden(element["name"]) if element["name"] else "未提供",
                "是" if element["required"] is True else ("否" if element["required"] is False else "未知"),
                "是" if element["available"] is True else ("否" if element["available"] is False else "未知"),
            ))
        lines.append("")

    if any(has_text(s.get("notes")) for s in shots):
        lines.append("## 镜头备注")
        lines.append("")
        for shot in shots:
            if has_text(shot.get("notes")):
                lines.append("- %s：%s" % (
                    esc(shot["shot_id"]) if shot["shot_id"] else shot["path"],
                    hidden(shot["notes"]),
                ))
        lines.append("")

    lines.append("## 剪辑顺序建议")
    lines.append("")
    if edit_order:
        lines.append("| 顺序 | 镜头 | 覆盖 | 下一步 |")
        lines.append("|---:|---|---|---|")
        for entry in edit_order:
            lines.append("| %d | %s | %s | %s |" % (
                entry["position"], esc(entry["shot_id"]), entry["state"], esc(entry["action"])))
    else:
        lines.append("- 无可排序镜头")
    lines.append("")

    lines.append("## 交接责任表")
    lines.append("")
    if responsibility:
        lines.append("| 对象 | 类型 | 责任人 | 截止 |")
        lines.append("|---|---|---|---|")
        for row in responsibility:
            lines.append("| %s | %s | %s | %s |" % (
                esc(row["item"]),
                "镜头" if row["kind"] == "shot" else "素材",
                esc(row["owner"]) if row["owner"] else "未指派",
                esc(row["due_at"]) if row["due_at"] else "未提供",
            ))
    else:
        lines.append("- 无")
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
