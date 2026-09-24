#!/usr/bin/env python3
"""现场服务每日进度更新包 — offline field-service daily update builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file, writes one
JSON document to stdout. No network, no filesystem writes, no image reading,
no command execution.

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

VALID_RECORD_STATE = ("done", "partial", "blocked", "not_started")
VALID_BLOCKER_KIND = ("material", "personnel", "access", "customer", "other")

SECTIONS = ("completed", "partial", "not_completed", "blocked", "unverifiable")

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
# A photo reference must be a bare file name. Anything with a separator, a parent
# segment, a scheme or a drive letter is refused outright and never resolved.
UNSAFE_REF_RE = re.compile(r"[/\\]|\.\.|^[A-Za-z][A-Za-z0-9+.-]*:")

DISCLAIMER = (
    "本输出是根据你提交的记录整理的进度材料，不是现场验收结论，也不代表工程质量、"
    "安全合规或工期承诺；照片内容未被读取，实际完成情况以现场负责人确认与验收记录为准。"
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    """Collapse control characters and whitespace. Chinese punctuation is preserved."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = "".join(
        ch for ch in text
        if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C"
    )
    return re.sub(r"\s+", " ", text).strip()


def ref_key(text):
    """Comparison key for file names: compatibility-normalised and case-folded.

    Two names that differ only by Unicode width or letter case would collide on the
    default macOS / Windows filesystems, so they are treated as the same file while
    the original spelling is still what gets displayed.
    """
    return unicodedata.normalize("NFKC", text).casefold()


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
    except ImportError:  # pragma: no cover
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
        "sections": {name: [] for name in SECTIONS},
        "section_counts": {name: 0 for name in SECTIONS},
        "tasks": [],
        "unplanned_records": [],
        "conflicts": [],
        "duplicates": [],
        "plan_deviation": {"entries": [], "deviation_hours": "0.00"},
        "blockers": [],
        "material_gaps": [],
        "photo_evidence_index": [],
        "photo_issues": {"caption_missing": [], "duplicate_filenames": [],
                         "unreferenced": [], "undeclared": [], "invalid_refs": []},
        "customer_confirmations": [],
        "tomorrow_plan": [],
        "human_send_checklist": [],
        "clarification_questions": [],
        "internal_daily_report": "# 现场日报\n\n- 状态：**REJECTED**\n- 已拒绝处理，未回显疑似凭据内容。\n",
        "customer_progress_draft": "",
        "markdown_summary": "# 现场日报\n\n- 状态：**REJECTED**\n- 已拒绝处理，未回显疑似凭据内容。\n",
        "injection_flagged": [],
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# main
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

    project_raw = data.get("project") if isinstance(data.get("project"), dict) else {}
    project_id = clean_text(project_raw.get("project_id")) if has_text(project_raw.get("project_id")) else None
    project_name = clean_text(project_raw.get("name")) if has_text(project_raw.get("name")) else None
    tz_name = clean_text(project_raw.get("timezone")) if has_text(project_raw.get("timezone")) else None
    tz = resolve_tz(tz_name)
    if tz_name and tz is None:
        warnings.append("TIMEZONE_UNRESOLVED")

    work_date = clean_text(data.get("work_date")) if has_text(data.get("work_date")) else None
    if work_date is not None and not DATE_RE.match(work_date):
        warnings.append("WORK_DATE_INVALID")
        work_date_ok = False
    else:
        work_date_ok = work_date is not None
    if work_date is None:
        warnings.append("WORK_DATE_MISSING")

    local_date = None
    if as_of is not None:
        local_date = as_of.astimezone(tz).date() if tz is not None else as_of.date()
        if work_date_ok and local_date.isoformat() != work_date:
            warnings.append("WORK_DATE_TIMEZONE_MISMATCH")

    project_offset = None
    if as_of is not None:
        project_offset = local_date and (as_of.astimezone(tz) if tz is not None else as_of).utcoffset()

    # ---- plan ----
    plan_raw = data.get("plan") if isinstance(data.get("plan"), list) else []
    plan = []
    plan_ids = []
    for index, entry in enumerate(plan_raw):
        if not isinstance(entry, dict):
            continue
        task_id = clean_text(entry.get("task_id")) if has_text(entry.get("task_id")) else None
        planned = dec(entry.get("planned_hours"))
        flags = []
        if task_id is None:
            flags.append("MISSING_TASK_ID")
        if entry.get("planned_hours") is not None and planned is None:
            flags.append("INVALID_PLANNED_HOURS")
        if planned is not None and planned < 0:
            flags.append("NEGATIVE_PLANNED_HOURS")
        plan.append({
            "task_id": task_id,
            "title": clean_text(entry.get("title")) if has_text(entry.get("title")) else "",
            "owner": clean_text(entry.get("owner")) if has_text(entry.get("owner")) else None,
            "planned_hours": planned,
            "index": index,
            "flags": flags,
        })
        plan_ids.append(task_id)

    # ---- progress records ----
    progress_raw = data.get("progress") if isinstance(data.get("progress"), list) else []
    records = []
    for index, entry in enumerate(progress_raw):
        if not isinstance(entry, dict):
            records.append({
                "index": index, "path": "progress[%d]" % index, "task_id": None,
                "state": None, "note": "", "hours": None, "photo_refs": [],
                "occurred_at": None, "flags": ["INVALID_RECORD"], "injection": False,
            })
            continue
        task_id = clean_text(entry.get("task_id")) if has_text(entry.get("task_id")) else None
        state = clean_text(entry.get("state")).lower() if has_text(entry.get("state")) else None
        flags = []
        if task_id is None:
            flags.append("MISSING_TASK_ID")
        if state is None:
            flags.append("MISSING_STATE")
        elif state not in VALID_RECORD_STATE:
            flags.append("INVALID_STATE")
        note = clean_text(entry.get("note")) if has_text(entry.get("note")) else ""
        hours = dec(entry.get("hours_spent"))
        if entry.get("hours_spent") is not None and hours is None:
            flags.append("INVALID_HOURS_SPENT")
        elif hours is not None and hours < 0:
            flags.append("NEGATIVE_HOURS_SPENT")

        refs_raw = entry.get("photo_refs") if isinstance(entry.get("photo_refs"), list) else []
        refs = []
        for ref in refs_raw:
            if not has_text(ref):
                continue
            text = clean_text(ref)
            if UNSAFE_REF_RE.search(text) or len(text) > 200:
                flags.append("INVALID_PHOTO_REF")
                refs.append({"ref": text, "valid": False})
            else:
                refs.append({"ref": text, "valid": True})

        occurred_raw = entry.get("occurred_at")
        occurred = parse_dt(occurred_raw)
        if has_text(occurred_raw) and occurred is None:
            flags.append("INVALID_OCCURRED_AT")
        elif occurred is not None and project_offset is not None:
            if occurred.utcoffset() != project_offset:
                flags.append("RECORD_TZ_OFFSET_DIFFERS")

        if state is not None and not note and not refs:
            flags.append("RECORD_EMPTY")

        records.append({
            "index": index, "path": "progress[%d]" % index, "task_id": task_id,
            "state": state, "note": note, "hours": hours, "photo_refs": refs,
            "occurred_at": clean_text(occurred_raw) if has_text(occurred_raw) else None,
            "flags": flags, "injection": injection_hit(entry.get("note")),
        })

    plan_missing = not plan
    records_missing = not records

    # ---- duplicates / conflicts ----
    grouped = {}
    for record in records:
        grouped.setdefault(record["task_id"], []).append(record)

    duplicates = []
    conflicts = []
    primary_record = {}
    for key in sorted(grouped, key=lambda x: (x is None, x or "")):
        group = grouped[key]
        if len(group) == 1:
            primary_record[key] = group[0]
            continue
        states = sorted({str(g["state"]) for g in group})
        entry = {
            "task_id": key,
            "occurrences": len(group),
            "states": states,
            "paths": sorted(g["path"] for g in group),
        }
        if len(states) > 1:
            conflicts.append(entry)
            group[0]["flags"].append("CONFLICTING_STATE")
        else:
            duplicates.append(entry)
            group[0]["flags"].append("DUPLICATE_RECORD")
        primary_record[key] = group[0]

    # ---- resolve every plan task ----
    tasks = []
    unplanned = []
    for entry in plan:
        record = primary_record.get(entry["task_id"])
        flags = list(entry["flags"])
        if record is None:
            state = "UNVERIFIABLE"
            flags.append("RECORD_MISSING")
            record_view = None
        else:
            flags.extend(f for f in record["flags"] if f not in flags)
            raw_state = record["state"]
            if raw_state is None or raw_state not in VALID_RECORD_STATE:
                state = "UNVERIFIABLE"
            elif raw_state == "done":
                state = "DONE"
            elif raw_state == "partial":
                state = "PARTIAL"
            elif raw_state == "blocked":
                state = "BLOCKED"
            else:
                state = "NOT_STARTED"
            record_view = record

        tasks.append({
            "task_id": entry["task_id"],
            "title": entry["title"],
            "owner": entry["owner"],
            "planned_hours": entry["planned_hours"],
            "state": state,
            "record_present": record is not None,
            "note": record_view["note"] if record_view else "",
            "hours_spent": record_view["hours"] if record_view else None,
            "photo_refs": [r["ref"] for r in record_view["photo_refs"]] if record_view else [],
            "review_flags": sorted(set(flags)),
        })

    for key in sorted(grouped, key=lambda x: (x is None, x or "")):
        if key not in plan_ids:
            unplanned.append({
                "task_id": key,
                "paths": sorted(g["path"] for g in grouped[key]),
                "states": sorted({str(g["state"]) for g in grouped[key]}),
                "flag": "PROGRESS_WITHOUT_PLAN_TASK",
            })

    sections = {name: [] for name in SECTIONS}
    for task in tasks:
        if task["state"] == "DONE":
            sections["completed"].append(task)
        elif task["state"] == "PARTIAL":
            sections["partial"].append(task)
        elif task["state"] == "NOT_STARTED":
            sections["not_completed"].append(task)
        elif task["state"] == "BLOCKED":
            sections["blocked"].append(task)
        else:
            sections["unverifiable"].append(task)

    # ---- hours deviation ----
    deviation_entries = []
    total_planned = Decimal("0")
    total_recorded = Decimal("0")
    planned_present = False
    recorded_present = False
    for task in tasks:
        planned = task["planned_hours"]
        spent = task["hours_spent"]
        if planned is not None:
            total_planned += planned
            planned_present = True
        if spent is not None:
            total_recorded += spent
            recorded_present = True
        if planned is None and spent is None:
            continue
        if planned is None:
            flag = "PLAN_HOURS_UNKNOWN"
        elif spent is None:
            flag = "RECORDED_HOURS_UNKNOWN"
        else:
            diff = spent - planned
            if diff > 0:
                flag = "OVER_PLAN"
            elif diff < 0:
                flag = "UNDER_PLAN"
            else:
                flag = "ON_PLAN"
        deviation_entries.append({
            "task_id": task["task_id"],
            "planned_hours": str(quant(planned, 2)) if planned is not None else None,
            "hours_spent": str(quant(spent, 2)) if spent is not None else None,
            "deviation_hours": str(quant(spent - planned, 2)) if (planned is not None and spent is not None) else None,
            "flag": flag,
        })
    deviation_hours = str(quant(total_recorded - total_planned, 2)) if (planned_present and recorded_present) else None

    # ---- blockers ----
    blockers_raw = data.get("blockers") if isinstance(data.get("blockers"), list) else []
    blockers = []
    material_gaps = []
    for index, entry in enumerate(blockers_raw):
        if not isinstance(entry, dict):
            continue
        blocker_id = clean_text(entry.get("blocker_id")) if has_text(entry.get("blocker_id")) else None
        kind = clean_text(entry.get("kind")).lower() if has_text(entry.get("kind")) else None
        flags = []
        if blocker_id is None:
            flags.append("MISSING_BLOCKER_ID")
        if kind is None:
            flags.append("MISSING_BLOCKER_KIND")
        elif kind not in VALID_BLOCKER_KIND:
            flags.append("INVALID_BLOCKER_KIND")
        eta_raw = entry.get("eta")
        eta = parse_dt(eta_raw)
        if has_text(eta_raw) and eta is None:
            flags.append("INVALID_ETA")
        if eta is not None and as_of is not None and eta < as_of:
            flags.append("ETA_PASSED")
        record = {
            "blocker_id": blocker_id,
            "kind": kind,
            "description": clean_text(entry.get("description")) if has_text(entry.get("description")) else "",
            "owner": clean_text(entry.get("owner")) if has_text(entry.get("owner")) else None,
            "eta": clean_text(eta_raw) if has_text(eta_raw) else None,
            "eta_state": ("PASSED" if eta is not None and as_of is not None and eta < as_of
                          else ("PENDING" if eta is not None else "UNKNOWN")),
            "review_flags": sorted(set(flags)),
            "index": index,
        }
        blockers.append(record)
        if kind == "material":
            material_gaps.append(record)

    blocker_counts = {}
    for record in blockers:
        key = record["kind"] or "unknown"
        blocker_counts[key] = blocker_counts.get(key, 0) + 1

    # ---- photos ----
    photos_raw = data.get("photos") if isinstance(data.get("photos"), list) else []
    photos = []
    filename_seen = {}
    for index, entry in enumerate(photos_raw):
        if not isinstance(entry, dict):
            continue
        filename = clean_text(entry.get("filename")) if has_text(entry.get("filename")) else None
        caption = clean_text(entry.get("caption")) if has_text(entry.get("caption")) else ""
        flags = []
        if filename is None:
            flags.append("MISSING_FILENAME")
        else:
            if UNSAFE_REF_RE.search(filename):
                flags.append("INVALID_PHOTO_NAME")
            filename_seen.setdefault(ref_key(filename), []).append(index)
        if not caption:
            flags.append("CAPTION_MISSING")
        photos.append({
            "filename": filename,
            "caption": caption,
            "caption_present": bool(caption),
            "index": index,
            "flags": flags,
            "referenced_by": [],
        })

    photo_index = {}
    for photo in photos:
        if photo["filename"]:
            photo_index.setdefault(ref_key(photo["filename"]), []).append(photo)

    duplicate_filenames = []
    for key in sorted(filename_seen):
        indexes = filename_seen[key]
        if len(indexes) > 1:
            duplicate_filenames.append({
                "filename": photos[indexes[0]]["filename"],
                "names": sorted({photos[i]["filename"] for i in indexes}),
                "occurrences": len(indexes),
                "indexes": sorted(indexes),
            })
            for index in indexes:
                photos[index]["flags"].append("DUPLICATE_FILENAME")

    undeclared = []
    invalid_refs = []
    for task in tasks:
        for ref in task["photo_refs"]:
            if UNSAFE_REF_RE.search(ref):
                invalid_refs.append({"task_id": task["task_id"], "ref": ref, "flag": "INVALID_PHOTO_REF"})
                continue
            targets = photo_index.get(ref_key(ref))
            if not targets:
                undeclared.append({"task_id": task["task_id"], "ref": ref,
                                   "flag": "PHOTO_NOT_DECLARED"})
            else:
                # Every declaration sharing this file name is marked as referenced;
                # the duplicate is reported separately instead of hiding one record.
                for target in targets:
                    target["referenced_by"].append(task["task_id"])

    unreferenced = [p["filename"] for p in photos
                    if p["filename"] and not p["referenced_by"]]
    caption_missing = [p["filename"] for p in photos if not p["caption_present"]]

    photo_evidence_index = [{
        "filename": p["filename"],
        "caption": p["caption"],
        "caption_present": p["caption_present"],
        "referenced_by": sorted(set(p["referenced_by"])),
        "flags": sorted(set(p["flags"])),
    } for p in photos]

    # ---- customer confirmations ----
    pending_raw = data.get("customer_pending") if isinstance(data.get("customer_pending"), list) else []
    customer_confirmations = []
    for index, entry in enumerate(pending_raw):
        if not isinstance(entry, dict):
            continue
        item_id = clean_text(entry.get("item_id")) if has_text(entry.get("item_id")) else None
        raised_raw = entry.get("raised_at")
        raised = parse_dt(raised_raw)
        flags = []
        if item_id is None:
            flags.append("MISSING_ITEM_ID")
        if not has_text(entry.get("question")):
            flags.append("QUESTION_MISSING")
        if raised is None:
            flags.append("RAISED_AT_MISSING")
        customer_confirmations.append({
            "item_id": item_id,
            "question": clean_text(entry.get("question")) if has_text(entry.get("question")) else "",
            "raised_at": clean_text(raised_raw) if has_text(raised_raw) else None,
            "source": "CUSTOMER_PENDING",
            "review_flags": sorted(set(flags)),
            "index": index,
        })

    for task in tasks:
        if task["state"] in ("UNVERIFIABLE", "BLOCKED"):
            customer_confirmations.append({
                "item_id": task["task_id"],
                "question": ("该事项当日没有可核对的完成记录，请现场负责人确认实际进展后再对客户说明。"
                             if task["state"] == "UNVERIFIABLE" else
                             "该事项当日受阻，请确认客户是否需要调整时间或方案。"),
                "raised_at": None,
                "source": "DERIVED_FROM_TASK",
                "review_flags": [],
                "index": None,
            })

    # ---- tomorrow plan ----
    tomorrow_raw = data.get("next_day_plan") if isinstance(data.get("next_day_plan"), list) else []
    tomorrow_plan = []
    for index, entry in enumerate(tomorrow_raw):
        if not isinstance(entry, dict):
            continue
        task_id = clean_text(entry.get("task_id")) if has_text(entry.get("task_id")) else None
        flags = []
        if task_id is None:
            flags.append("MISSING_TASK_ID")
        if not has_text(entry.get("title")):
            flags.append("TITLE_MISSING")
        if not has_text(entry.get("owner")):
            flags.append("OWNER_MISSING")
        tomorrow_plan.append({
            "task_id": task_id,
            "title": clean_text(entry.get("title")) if has_text(entry.get("title")) else "",
            "owner": clean_text(entry.get("owner")) if has_text(entry.get("owner")) else None,
            "review_flags": sorted(set(flags)),
            "index": index,
        })

    # ---- record level gaps that must surface in the daily state ----
    RECORD_GAP_FLAGS = (
        "RECORD_TZ_OFFSET_DIFFERS", "RECORD_EMPTY", "INVALID_HOURS_SPENT",
        "NEGATIVE_HOURS_SPENT", "INVALID_OCCURRED_AT", "MISSING_STATE",
        "INVALID_STATE", "MISSING_TASK_ID",
    )
    record_gaps = [{"path": r["path"], "flags": sorted(set(r["flags"]))}
                   for r in records if any(f in RECORD_GAP_FLAGS for f in r["flags"])]

    # ---- state ----
    # BLOCKED is reserved for the two conditions that make the report unsafe to
    # send: contradictory completion states for the same task, and a photo
    # reference that tries to escape the local file namespace. A duplicate photo
    # file name is a hygiene gap (GAPS_FOUND), not a blocker.
    if conflicts or invalid_refs:
        status = "BLOCKED"
    elif any(t["state"] == "UNVERIFIABLE" for t in tasks) or unplanned or record_gaps:
        status = "GAPS_FOUND"
    elif warnings:
        status = "GAPS_FOUND"
    elif duplicate_filenames or caption_missing or unreferenced:
        status = "GAPS_FOUND"
    elif not tasks:
        status = "INPUT_INCOMPLETE"
    else:
        status = "READY"
    if as_of is None or plan_missing or records_missing:
        if status != "BLOCKED":
            status = "INPUT_INCOMPLETE"

    # ---- send checklist ----
    send_checklist = []

    def step(topic, action):
        send_checklist.append({"step": len(send_checklist) + 1, "topic": topic, "action": action})

    step("OWNER_CONFIRM", "现场负责人已确认内部日报与客户稿中的完成情况一致。")
    if sections["unverifiable"]:
        step("UNVERIFIABLE_HELD", "没有当日记录的事项**不要**写进客户稿的已完成部分，已单独列出等待确认。")
    if sections["partial"]:
        step("PARTIAL_PHRASING", "部分完成的事项在客户稿中写明剩余工作，不写“已完成”。")
    if customer_confirmations:
        step("CUSTOMER_QUESTIONS", "需要客户确认的问题已逐条列出，由负责人决定是否随进度稿一并发问。")
    if material_gaps:
        step("MATERIAL_GAP", "材料缺口已确认到货时间，必要时先告知客户可能的顺延。")
    if photo_evidence_index:
        step("PHOTO_INDEX", "照片仅按文件名与说明索引，**未读取图片内容**；发送前人工核对图片确实对应所述事项。")
    if caption_missing:
        step("PHOTO_CAPTION", "缺少说明的照片已补说明，避免客户误读。")
    if duplicate_filenames:
        step("DUPLICATE_FILENAME", "同名照片已人工区分，避免发送时互相覆盖。")
    if unplanned:
        step("UNPLANNED_RECORD", "计划外的事项已确认是否属于本次服务范围后再对客户披露。")
    step("NO_AUTO_SEND", "本工具不发送任何消息；发送动作由人工在确认后执行。")

    # ---- questions ----
    questions = []

    def ask(topic, text):
        questions.append({"id": "Q-%02d" % (len(questions) + 1), "topic": topic, "question": text})

    if as_of is None:
        ask("AS_OF", "请提供带时区偏移的 as_of（例如 2026-09-25T19:30:00+08:00）。")
    if work_date is None:
        ask("WORK_DATE", "请提供当日工作日期 work_date（YYYY-MM-DD）。")
    elif "WORK_DATE_TIMEZONE_MISMATCH" in warnings:
        ask("WORK_DATE_TIMEZONE", "work_date 与 as_of 在项目时区下的日期不一致，请确认要写进日报的是哪一天。")
    if plan_missing:
        ask("PLAN", "没有提供当日计划任务，无法判断偏差与完整度，请补充 plan[]。")
    if records_missing:
        ask("PROGRESS", "没有提供任何进度记录，请补充 progress[]。")
    if sections["unverifiable"]:
        ask("RECORD_MISSING", "以下计划任务当日没有记录，请确认实际状态：" +
            "、".join(str(t["task_id"]) for t in sections["unverifiable"]))
    if unplanned:
        ask("UNPLANNED", "以下记录找不到对应的计划任务，请确认是新增工作还是写错编号：" +
            "、".join(str(u["task_id"]) for u in unplanned))
    if conflicts:
        ask("CONFLICTING_STATE", "以下任务出现了互相矛盾的完成状态，请确认哪一条为准：" +
            "、".join(str(c["task_id"]) for c in conflicts))
    if duplicates:
        ask("DUPLICATE_RECORD", "以下任务被记录了多次，请确认是否需要合并：" +
            "、".join(str(d["task_id"]) for d in duplicates))
    if material_gaps:
        # The question must match what the record actually says. An expired ETA must
        # never be described as "time not yet confirmed", or the user will re-enter a
        # date that is already there and miss the real problem.
        overdue_eta = [m for m in material_gaps if m["eta_state"] == "PASSED"]
        missing_eta = [m for m in material_gaps if m["eta_state"] == "UNKNOWN"]
        prompts = []
        if overdue_eta:
            prompts.append("以下材料缺口的原预计到货时间已过，请更新到货状态或给出新的预计时间：" +
                           "、".join(str(m["blocker_id"]) for m in overdue_eta))
        if missing_eta:
            prompts.append("以下材料缺口没有填写预计到货时间，请补充：" +
                           "、".join(str(m["blocker_id"]) for m in missing_eta))
        if prompts:
            ask("MATERIAL_GAP", " ".join(prompts))
        # Every material ETA is present and still in the future: there is no time gap
        # to ask about, so no MATERIAL_GAP question is generated at all.
    if any(b["kind"] == "personnel" for b in blockers):
        ask("PERSONNEL_GAP", "人员缺口是否影响明日计划，请确认是否需要调整排班。")
    if customer_confirmations and any(c["source"] == "CUSTOMER_PENDING" and "RAISED_AT_MISSING" in c["review_flags"]
                                      for c in customer_confirmations):
        ask("RAISED_AT", "部分待客户确认事项缺少提出时间，请补充以便判断跟进优先级。")
    if caption_missing:
        ask("PHOTO_CAPTION", "以下照片缺少说明，请补充：" + "、".join(caption_missing))
    if duplicate_filenames:
        ask("DUPLICATE_FILENAME", "存在同名或仅大小写 / 全半角不同的照片，请重命名以区分：" +
            "、".join(d["filename"] for d in duplicate_filenames))
    if photo_evidence_index == [] and tasks:
        ask("PHOTO_NONE", "当日没有任何照片记录，请确认是否确实无需影像留证。")
    if tomorrow_plan == []:
        ask("TOMORROW_PLAN", "没有提供明日计划，客户稿中将无法说明下一步安排。")
    if "TIMEZONE_UNRESOLVED" in warnings:
        ask("TIMEZONE", "项目时区无法解析，请提供有效的 IANA 时区名，或全部时间戳都带明确偏移。")

    # ---- injection report ----
    injection_flagged = []
    for record in records:
        if record["injection"]:
            injection_flagged.append({"path": record["path"] + "/note", "marker": "PROMPT_INJECTION"})

    internal_report = render_internal(
        status=status, project_name=project_name, project_id=project_id, work_date=work_date,
        as_of=data.get("as_of"), sections=sections, tasks=tasks, deviation_entries=deviation_entries,
        deviation_hours=deviation_hours, blockers=blockers, photo_evidence_index=photo_evidence_index,
        customer_confirmations=customer_confirmations, tomorrow_plan=tomorrow_plan,
        send_checklist=send_checklist, questions=questions, warnings=warnings,
        unplanned=unplanned, conflicts=conflicts, duplicates=duplicates,
        material_gaps=material_gaps, record_count=len(records), record_gaps=record_gaps,
    )
    customer_draft = render_customer(
        project_name=project_name, project_id=project_id, work_date=work_date,
        sections=sections, customer_confirmations=customer_confirmations,
        tomorrow_plan=tomorrow_plan, material_gaps=material_gaps, tasks=tasks,
    )

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(data.get("as_of")) if has_text(data.get("as_of")) else None,
        "work_date": work_date,
        "work_date_local_in_project_tz": local_date.isoformat() if local_date else None,
        "project": {"project_id": project_id, "name": project_name, "timezone": tz_name,
                    "timezone_resolved": tz is not None},
        "plan_task_count": len(tasks),
        "record_count": len(records),
        "sections": {name: [task_view(t) for t in sections[name]] for name in SECTIONS},
        "section_counts": {name: len(sections[name]) for name in SECTIONS},
        "tasks": [task_view(t) for t in tasks],
        "unplanned_records": unplanned,
        "conflicts": conflicts,
        "duplicates": duplicates,
        "record_gaps": record_gaps,
        "plan_deviation": {
            "entries": deviation_entries,
            "total_planned_hours": str(quant(total_planned, 2)) if planned_present else None,
            "total_recorded_hours": str(quant(total_recorded, 2)) if recorded_present else None,
            "deviation_hours": deviation_hours,
        },
        "blockers": blockers,
        "blocker_counts": blocker_counts,
        "material_gaps": material_gaps,
        "photo_evidence_index": photo_evidence_index,
        "photo_issues": {
            "caption_missing": caption_missing,
            "duplicate_filenames": duplicate_filenames,
            "unreferenced": unreferenced,
            "undeclared": undeclared,
            "invalid_refs": invalid_refs,
        },
        "customer_confirmations": customer_confirmations,
        "tomorrow_plan": tomorrow_plan,
        "human_send_checklist": send_checklist,
        "clarification_questions": questions,
        "internal_daily_report": internal_report,
        "customer_progress_draft": customer_draft,
        "markdown_summary": internal_report,
        "injection_flagged": injection_flagged,
        "input_warnings": warnings,
        "disclaimer": DISCLAIMER,
    }


def task_view(task):
    return {
        "task_id": task["task_id"],
        "title": task["title"],
        "owner": task["owner"],
        "state": task["state"],
        "record_present": task["record_present"],
        "planned_hours": str(quant(task["planned_hours"], 2)) if task["planned_hours"] is not None else None,
        "hours_spent": str(quant(task["hours_spent"], 2)) if task["hours_spent"] is not None else None,
        "photo_refs": list(task["photo_refs"]),
        "review_flags": list(task["review_flags"]),
    }


STATE_LABEL = {
    "DONE": "已完成（按记录）",
    "PARTIAL": "部分完成",
    "NOT_STARTED": "未开始",
    "BLOCKED": "受阻",
    "UNVERIFIABLE": "无记录待确认",
}


def render_internal(status, project_name, project_id, work_date, as_of, sections, tasks,
                    deviation_entries, deviation_hours, blockers, photo_evidence_index,
                    customer_confirmations, tomorrow_plan, send_checklist, questions,
                    warnings, unplanned, conflicts, duplicates, material_gaps, record_count,
                    record_gaps):
    lines = []
    lines.append("# 现场日报（内部） — %s" % esc(project_name or project_id or "项目"))
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 工作日期：%s" % (esc(work_date) if work_date else "未提供"))
    lines.append("- 生成基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 计划任务：%d 项 ／ 进度记录：%d 条" % (len(tasks), record_count))
    lines.append("")

    lines.append("## 完成情况")
    lines.append("")
    for name in SECTIONS:
        entries = sections[name]
        lines.append("### %s（%d）" % (STATE_LABEL[[
            "DONE", "PARTIAL", "NOT_STARTED", "BLOCKED", "UNVERIFIABLE"][SECTIONS.index(name)]], len(entries)))
        lines.append("")
        if not entries:
            lines.append("- 无")
            lines.append("")
            continue
        lines.append("| 编号 | 内容 | 责任人 | 状态 | 备注 |")
        lines.append("|---|---|---|---|---|")
        for task in entries:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(task["task_id"]) if task["task_id"] else "未提供",
                hidden(task["title"]) if task["title"] else "未提供",
                esc(task["owner"]) if task["owner"] else "未指派",
                STATE_LABEL[task["state"]],
                hidden(task["note"]) if task["note"] else "无记录",
            ))
        lines.append("")

    lines.append("## 工时偏差")
    lines.append("")
    if deviation_entries:
        lines.append("| 编号 | 计划 | 实际 | 偏差 | 判定 |")
        lines.append("|---|---|---|---|---|")
        for entry in deviation_entries:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(entry["task_id"]) if entry["task_id"] else "未提供",
                entry["planned_hours"] if entry["planned_hours"] is not None else "未知",
                entry["hours_spent"] if entry["hours_spent"] is not None else "未知",
                entry["deviation_hours"] if entry["deviation_hours"] is not None else "无法计算",
                esc(entry["flag"]),
            ))
    else:
        lines.append("- 无可比较的工时数据")
    lines.append("")
    lines.append("- 合计偏差：%s 小时" % (deviation_hours if deviation_hours is not None else "无法计算"))
    lines.append("")

    lines.append("## 阻塞与依赖")
    lines.append("")
    if blockers:
        lines.append("| 编号 | 类型 | 说明 | 责任人 | 预计解除 | 状态 |")
        lines.append("|---|---|---|---|---|---|")
        for blocker in blockers:
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                esc(blocker["blocker_id"]) if blocker["blocker_id"] else "未提供",
                esc(blocker["kind"]) if blocker["kind"] else "未提供",
                hidden(blocker["description"]) if blocker["description"] else "未填写",
                esc(blocker["owner"]) if blocker["owner"] else "未指派",
                esc(blocker["eta"]) if blocker["eta"] else "未知",
                esc(blocker["eta_state"]),
            ))
    else:
        lines.append("- 无")
    lines.append("")

    if material_gaps:
        lines.append("## 材料缺口")
        lines.append("")
        for gap in material_gaps:
            lines.append("- `%s` %s（责任人 %s，预计解除 %s）" % (
                esc(gap["blocker_id"]) if gap["blocker_id"] else "未提供",
                hidden(gap["description"]) if gap["description"] else "未填写",
                esc(gap["owner"]) if gap["owner"] else "未指派",
                esc(gap["eta"]) if gap["eta"] else "未知",
            ))
        lines.append("")

    lines.append("## 照片证据索引（未读取图片内容）")
    lines.append("")
    if photo_evidence_index:
        lines.append("| 文件名 | 说明 | 引用任务 |")
        lines.append("|---|---|---|")
        for photo in photo_evidence_index:
            lines.append("| %s | %s | %s |" % (
                esc(photo["filename"]) if photo["filename"] else "未提供",
                hidden(photo["caption"]) if photo["caption"] else "缺说明",
                esc("、".join(photo["referenced_by"])) if photo["referenced_by"] else "未被引用",
            ))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 需要客户确认")
    lines.append("")
    if customer_confirmations:
        for item in customer_confirmations:
            lines.append("- `%s` %s" % (
                esc(item["item_id"]) if item["item_id"] else "未提供",
                hidden(item["question"]),
            ))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 明日计划")
    lines.append("")
    if tomorrow_plan:
        for entry in tomorrow_plan:
            lines.append("- `%s` %s（责任人 %s）" % (
                esc(entry["task_id"]) if entry["task_id"] else "未提供",
                hidden(entry["title"]) if entry["title"] else "未填写",
                esc(entry["owner"]) if entry["owner"] else "未指派",
            ))
    else:
        lines.append("- 未提供")
    lines.append("")

    if unplanned or conflicts or duplicates or record_gaps:
        lines.append("## 记录问题")
        lines.append("")
        for item in unplanned:
            lines.append("- 计划外记录：`%s`（%s）" % (
                esc(item["task_id"]) if item["task_id"] else "未提供",
                esc("、".join(item["paths"]))))
        for item in conflicts:
            lines.append("- 状态冲突：`%s` 出现 %d 次（%s）" % (
                esc(item["task_id"]) if item["task_id"] else "未提供",
                item["occurrences"], esc("、".join(item["states"]))))
        for item in duplicates:
            lines.append("- 重复记录：`%s` 出现 %d 次" % (
                esc(item["task_id"]) if item["task_id"] else "未提供", item["occurrences"]))
        for gap in record_gaps:
            lines.append("- 记录缺陷：`%s`（%s）" % (
                esc(gap["path"]), esc("、".join(gap["flags"]))))
        lines.append("")

    lines.append("## 发送前检查表")
    lines.append("")
    for entry in send_checklist:
        lines.append("%d. [ ] %s" % (entry["step"], esc(entry["action"])))
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


def render_customer(project_name, project_id, work_date, sections, customer_confirmations,
                    tomorrow_plan, material_gaps, tasks):
    """Customer-facing draft. Only ever states what was RECORDED, never 'verified done'."""
    lines = []
    lines.append("## %s — %s 进度" % (
        esc(project_name or project_id or "项目"),
        esc(work_date) if work_date else "当日",
    ))
    lines.append("")
    lines.append("> 以下内容根据现场记录整理，供你了解当日进展；不构成完工验收或工期承诺。")
    lines.append("")

    if sections["completed"]:
        lines.append("**当日已完成（按现场记录）**")
        lines.append("")
        for task in sections["completed"]:
            lines.append("- " + (hidden(task["title"]) if task["title"] else "（未填写内容）"))
        lines.append("")

    if sections["partial"]:
        lines.append("**进行中（未全部完成）**")
        lines.append("")
        for task in sections["partial"]:
            lines.append("- " + (hidden(task["title"]) if task["title"] else "（未填写内容）"))
        lines.append("")

    if sections["blocked"]:
        lines.append("**当日受阻**")
        lines.append("")
        for task in sections["blocked"]:
            lines.append("- " + (hidden(task["title"]) if task["title"] else "（未填写内容）"))
        lines.append("")

    held = sections["unverifiable"] + sections["not_completed"]
    if held:
        lines.append("**尚未开始或需进一步确认**")
        lines.append("")
        for task in held:
            lines.append("- " + (hidden(task["title"]) if task["title"] else "（未填写内容）"))
        lines.append("")

    if material_gaps:
        lines.append("**材料进度**")
        lines.append("")
        for gap in material_gaps:
            # The fallback must follow the recorded ETA state: never tell a customer
            # the arrival time is "to be confirmed" when an ETA was recorded - least of
            # all when that ETA has already passed.
            if gap["description"]:
                lines.append("- " + hidden(gap["description"]))
            elif gap["eta_state"] == "PASSED":
                lines.append("- 材料到货时间已超原预计，正在确认最新进度")
            elif gap["eta_state"] == "PENDING":
                lines.append("- 材料预计到货时间：" + hidden(gap["eta"]))
            else:
                lines.append("- 材料到货时间待确认")
        lines.append("")

    if tomorrow_plan:
        lines.append("**下一步安排**")
        lines.append("")
        for entry in tomorrow_plan:
            lines.append("- " + (hidden(entry["title"]) if entry["title"] else "（未填写内容）"))
        lines.append("")

    if customer_confirmations:
        lines.append("**需要你确认**")
        lines.append("")
        # Only questions the user themselves raised are shown to the customer.
        # Internally derived prompts (blocked / unrecorded tasks) stay in the
        # internal report; the sections above already describe the status.
        for item in customer_confirmations:
            if item["source"] != "CUSTOMER_PENDING":
                continue
            lines.append("- " + hidden(item["question"]))
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
