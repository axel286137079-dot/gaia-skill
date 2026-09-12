#!/usr/bin/env python3
"""Offline leaked-secret rotation and revocation evidence closure audit.

Builds a per-incident state machine from user-supplied, already-redacted incident,
dependency and event records; checks that every dependency has update *and*
verification evidence; measures the window in which the old credential may still
be valid; and applies the declared SLA.  Strictly offline and read-only: it never
validates or revokes a real key, never touches GitHub or a cloud console, never
rewrites history and never closes a security alert.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
RAW_SECRET_PATTERNS = (
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
    re.compile(r"-----BEGIN [A-Z ]*CERTIFICATE-----"),
)
OPAQUE_BLOB = re.compile(r"^[A-Za-z0-9+/=_-]{32,}$")
REDACTION_PREFIXES = ("fp:", "fingerprint:", "sha256:", "sha256=", "hash:", "hashed:",
                      "redacted:", "masked:", "hmac:", "sha1:")
SECRET_VALUE_KEYS = {"secret_value", "raw_secret", "secret_plaintext", "plaintext_secret",
                     "private_key", "private_key_pem", "password", "passphrase"}

EVENT_TYPES = ("detected", "owner_notified", "new_secret_created", "dependency_updated",
               "deployment_verified", "old_secret_revoked", "alert_closed", "postcheck_passed")
DEPENDENCY_EVENTS = ("dependency_updated", "deployment_verified")
SEVERITIES = ("low", "medium", "high", "critical")

STATUS_ORDER = ("INVALID", "PARTIAL", "SLA_BREACH", "OPEN", "ROTATION_IN_PROGRESS",
                "READY_TO_REVOKE", "REVOKED_PENDING_VERIFY", "CLOSED_WITH_EVIDENCE")


def privacy_gate(value):
    """Reject input that still contains a real-looking credential. Never echoes it."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in SECRET_VALUE_KEYS:
                raise ValueError(
                    "PRIVACY_GATE: 输入包含疑似未脱敏的密钥字段，已拒绝处理。"
                    "请只提供脱敏标识（如 fingerprint 使用 sha256: 前缀），不要提供密钥原文。")
            privacy_gate(item)
    elif isinstance(value, list):
        for item in value:
            privacy_gate(item)
    elif isinstance(value, str):
        for pattern in RAW_SECRET_PATTERNS:
            if pattern.search(value):
                raise ValueError(
                    "PRIVACY_GATE: 输入包含疑似真实密钥/私钥/令牌内容，已拒绝处理，且不会回显该内容。"
                    "请先完成脱敏（仅保留 sha256: 等前缀标识）后重试。")
        if len(value) >= 32 and OPAQUE_BLOB.match(value) \
                and not value.strip().lower().startswith(REDACTION_PREFIXES):
            raise ValueError(
                "PRIVACY_GATE: 输入包含长度较长且无脱敏前缀的不透明字符串，疑似真实密钥，已拒绝处理。"
                "请改用带 sha256:/fp: 前缀的脱敏指纹。")
    return True


def clean_text(value, label, maximum=300, allow_empty=False):
    if value is None:
        if allow_empty:
            return ""
        raise ValueError(label + " is required")
    if not isinstance(value, str):
        raise ValueError(label + " must be text")
    if CONTROL.search(value):
        raise ValueError(label + " must not contain control characters")
    text = value.strip()
    if len(text) > maximum:
        raise ValueError(label + " exceeds length limit")
    if not text and not allow_empty:
        raise ValueError(label + " must be nonempty text")
    return text


def optional_text(value, label, maximum=300):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return clean_text(value, label, maximum)


def parse_as_of(value):
    if not isinstance(value, str) or "T" not in value.strip():
        raise ValueError("as_of must be an ISO8601 datetime with a UTC offset")
    return parse_dt(value, "as_of")


def parse_dt(value, label, required=True):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        if required:
            raise ValueError(label + " is required")
        return None
    if not isinstance(value, str) or not ISO_DT.match(value.strip()):
        raise ValueError(label + " must be YYYY-MM-DD or ISO8601 datetime")
    raw = value.strip()
    if "T" in raw:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        if not re.search(r"[+-]\d{2}:?\d{2}$", raw):
            raise ValueError(label + " datetime must include a UTC offset")
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            raise ValueError(label + " is not a valid datetime") from None
    try:
        return datetime.combine(date.fromisoformat(raw), datetime.min.time(), tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(label + " is not a valid date") from None


def nonneg(value, label, maximum=Decimal("1000000")):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or not Decimal("0") <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
    return result


def quant(value, places=2):
    result = Decimal(value).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
    if result == 0:
        result = abs(result)
    return str(result)


def hours_between(start, end):
    if start is None or end is None:
        return None
    return Decimal(str((end - start).total_seconds())) / Decimal("3600")


def parse_sla(raw):
    if raw is None:
        return {"ack_hours": None, "rotation_hours": None}
    if not isinstance(raw, dict):
        raise ValueError("sla must be an object")
    ack_raw = raw.get("ack_hours")
    rotation_raw = raw.get("rotation_hours")
    return {
        "ack_hours": nonneg(ack_raw, "sla.ack_hours", Decimal("8760"))
        if ack_raw is not None and str(ack_raw).strip() != "" else None,
        "rotation_hours": nonneg(rotation_raw, "sla.rotation_hours", Decimal("8760"))
        if rotation_raw is not None and str(rotation_raw).strip() != "" else None,
    }


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    privacy_gate(data)
    as_of = parse_as_of(data.get("as_of"))
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    sla = parse_sla(data.get("sla"))

    incidents_raw = data.get("incidents")
    if not isinstance(incidents_raw, list) or len(incidents_raw) > 2000:
        raise ValueError("incidents must be a list of up to 2000 items")
    if not incidents_raw:
        result = {
            "as_of": as_of.isoformat(), "status": "INVALID", "incident_count": 0,
            "status_counts": {}, "incidents": [], "sla_breaches": [], "evidence_gaps": [],
            "conflicts": [], "duplicate_events": [], "orphan_dependencies": [],
            "note": "没有任何 incident 可审计，判定为 INVALID。本技能不验证或撤销真实密钥、"
                    "不访问 GitHub/云平台、不改历史、不自动关闭安全告警。",
        }
        result["markdown_summary"] = build_markdown(result)
        return result

    incidents = []
    seen_incidents = set()
    for index, item in enumerate(incidents_raw):
        label = "incidents[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each incident must be an object")
        incident_id = clean_text(item.get("incident_id"), label + ".incident_id", 120)
        if incident_id in seen_incidents:
            raise ValueError("duplicate incident_id: " + incident_id)
        seen_incidents.add(incident_id)
        severity_raw = optional_text(item.get("severity"), label + ".severity", 24)
        severity = severity_raw.lower() if severity_raw else None
        if severity is not None and severity not in SEVERITIES:
            raise ValueError(label + ".severity must be one of " + ", ".join(SEVERITIES))
        exposed_at = parse_dt(item.get("exposed_at"), label + ".exposed_at", required=False)
        detected_at = parse_dt(item.get("detected_at"), label + ".detected_at", required=False)
        incidents.append({
            "incident_id": incident_id,
            "provider": optional_text(item.get("provider"), label + ".provider", 120),
            "secret_type": optional_text(item.get("secret_type"), label + ".secret_type", 120),
            "fingerprint": optional_text(item.get("fingerprint"), label + ".fingerprint", 200),
            "environment": optional_text(item.get("environment"), label + ".environment", 120),
            "severity": severity,
            "owner": optional_text(item.get("owner"), label + ".owner", 160),
            "exposed_at": exposed_at, "detected_at": detected_at,
        })
    known = {item["incident_id"] for item in incidents}

    dependencies_raw = data.get("dependencies")
    if dependencies_raw is None:
        dependencies_raw = []
    if not isinstance(dependencies_raw, list) or len(dependencies_raw) > 20000:
        raise ValueError("dependencies must be a list of up to 20000 items")
    dependencies = []
    seen_dependencies = set()
    orphans = []
    for index, item in enumerate(dependencies_raw):
        label = "dependencies[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each dependency must be an object")
        service_id = clean_text(item.get("service_id"), label + ".service_id", 120)
        if service_id in seen_dependencies:
            raise ValueError("duplicate service_id: " + service_id)
        seen_dependencies.add(service_id)
        incident_id = clean_text(item.get("incident_id"), label + ".incident_id", 120)
        if incident_id not in known:
            orphans.append({"service_id": service_id, "incident_id": incident_id})
            continue
        dependencies.append({
            "service_id": service_id, "incident_id": incident_id,
            "owner": optional_text(item.get("owner"), label + ".owner", 160),
            "criticality": optional_text(item.get("criticality"), label + ".criticality", 24),
            "updated_at": None, "verified_at": None,
        })

    events_raw = data.get("events")
    if events_raw is None:
        events_raw = []
    if not isinstance(events_raw, list) or len(events_raw) > 50000:
        raise ValueError("events must be a list of up to 50000 items")
    events = []
    seen_events = {}
    duplicate_events = []
    for index, item in enumerate(events_raw):
        label = "events[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each event must be an object")
        event_id = clean_text(item.get("event_id"), label + ".event_id", 120)
        incident_id = clean_text(item.get("incident_id"), label + ".incident_id", 120)
        if incident_id not in known:
            raise ValueError(label + ".incident_id references an unknown incident: " + incident_id)
        event_type = clean_text(item.get("type"), label + ".type", 60).lower()
        if event_type not in EVENT_TYPES:
            raise ValueError(label + ".type must be one of " + ", ".join(EVENT_TYPES))
        occurred_at = parse_dt(item.get("occurred_at"), label + ".occurred_at")
        if occurred_at.tzinfo is None:
            raise ValueError(label + ".occurred_at must be timezone-aware")
        if occurred_at > as_of:
            raise ValueError(label + ".occurred_at must not be in the future relative to as_of")
        service_id = optional_text(item.get("service_id"), label + ".service_id", 120)
        if event_id in seen_events:
            seen_events[event_id] += 1
            continue
        seen_events[event_id] = 1
        events.append({
            "event_id": event_id, "incident_id": incident_id, "type": event_type,
            "occurred_at": occurred_at,
            "evidence_id": optional_text(item.get("evidence_id"), label + ".evidence_id", 200),
            "actor": optional_text(item.get("actor"), label + ".actor", 160),
            "service_id": service_id,
        })
    for event_id in sorted(seen_events):
        if seen_events[event_id] > 1:
            duplicate_events.append({"event_id": event_id,
                                     "incident_id": next(e["incident_id"] for e in events
                                                         if e["event_id"] == event_id),
                                     "occurrences": seen_events[event_id]})

    by_incident = {}
    for event in events:
        by_incident.setdefault(event["incident_id"], []).append(event)
    dependencies_by_incident = {}
    for dependency in dependencies:
        dependencies_by_incident.setdefault(dependency["incident_id"], []).append(dependency)

    rows = []
    sla_breaches = []
    evidence_gaps = []
    conflict_rows = []
    for incident in incidents:
        incident_id = incident["incident_id"]
        incident_events = sorted(by_incident.get(incident_id, []),
                                 key=lambda entry: (entry["occurred_at"], entry["event_id"]))
        steps = {}
        for event_type in EVENT_TYPES:
            matching = [entry for entry in incident_events if entry["type"] == event_type]
            steps[event_type] = {
                "done": bool(matching),
                "occurred_at": matching[0]["occurred_at"].isoformat() if matching else None,
                "event_ids": [entry["event_id"] for entry in matching],
            }
        flags = set()
        missing_actions = []
        conflicts = []
        incident_deps = dependencies_by_incident.get(incident_id, [])
        detected_events = [entry for entry in incident_events if entry["type"] == "detected"]
        detected_at = detected_events[0]["occurred_at"] if detected_events else incident["detected_at"]
        if detected_at is None:
            flags.add("DETECTION_TIME_MISSING")
            missing_actions.append("补录检测时间或 detected 事件")
        if incident["owner"] is None:
            flags.add("OWNER_MISSING")
            missing_actions.append("指定事件负责人")
        if incident["fingerprint"] is None:
            flags.add("FINGERPRINT_MISSING")
            missing_actions.append("补录脱敏指纹以区分具体凭据")
        if incident["exposed_at"] is None:
            flags.add("EXPOSED_AT_MISSING")

        for event in incident_events:
            if event["type"] == "dependency_updated":
                target = None
                if event["service_id"]:
                    target = next((dep for dep in incident_deps
                                   if dep["service_id"] == event["service_id"]), None)
                    if target is None:
                        flags.add("DEPENDENCY_REFERENCE_UNKNOWN")
                else:
                    flags.add("DEPENDENCY_ATTRIBUTION_MISSING")
                if target is not None:
                    if target["updated_at"] is None or event["occurred_at"] < target["updated_at"]:
                        target["updated_at"] = event["occurred_at"]
            elif event["type"] == "deployment_verified":
                for dep in incident_deps:
                    if dep["updated_at"] is None or dep["updated_at"] > event["occurred_at"]:
                        continue
                    if event["service_id"] and event["service_id"] != dep["service_id"]:
                        continue
                    if dep["verified_at"] is None or event["occurred_at"] < dep["verified_at"]:
                        dep["verified_at"] = event["occurred_at"]

        revoked_at = None
        if steps["old_secret_revoked"]["done"]:
            revoked_at = datetime.fromisoformat(steps["old_secret_revoked"]["occurred_at"])
        created_at = None
        if steps["new_secret_created"]["done"]:
            created_at = datetime.fromisoformat(steps["new_secret_created"]["occurred_at"])
        verified_at = None
        if steps["deployment_verified"]["done"]:
            verified_at = datetime.fromisoformat(steps["deployment_verified"]["occurred_at"])
        postcheck_at = None
        if steps["postcheck_passed"]["done"]:
            postcheck_at = datetime.fromisoformat(steps["postcheck_passed"]["occurred_at"])

        if steps["alert_closed"]["done"] and revoked_at is None:
            flags.add("ALERT_CLOSED_WITHOUT_REVOCATION")
            missing_actions.append("关闭告警不能替代供应商撤销，仍需完成旧密钥撤销")
        if incident["severity"] in ("high", "critical") and revoked_at is None:
            flags.add("IMMEDIATE_REVOCATION_TRADEOFF")
            missing_actions.append("高危事件需在停机窗口与暴露时长之间明确立即撤销的取舍")
        if revoked_at is not None and verified_at is not None and revoked_at < verified_at:
            conflicts.append({"code": "REVOKE_BEFORE_VERIFY",
                              "detail": "旧密钥在部署验证之前就被撤销"})
        latest_update = max((dep["updated_at"] for dep in incident_deps
                             if dep["updated_at"] is not None), default=None)
        if revoked_at is not None and latest_update is not None and revoked_at < latest_update:
            conflicts.append({"code": "REVOKE_BEFORE_DEPENDENCY_UPDATE",
                              "detail": "旧密钥在依赖更新完成之前就被撤销"})
        if revoked_at is not None and created_at is not None and revoked_at < created_at:
            conflicts.append({"code": "REVOKE_BEFORE_NEW_SECRET",
                              "detail": "旧密钥在新密钥创建之前就被撤销"})
        if revoked_at is not None and postcheck_at is not None and postcheck_at < revoked_at:
            conflicts.append({"code": "POSTCHECK_BEFORE_REVOKE",
                              "detail": "后检时间早于撤销时间"})
        if any(entry["occurrences"] > 1 for entry in duplicate_events
               if entry["incident_id"] == incident_id):
            flags.add("DUPLICATE_EVENT_ID")

        updated = [dep["service_id"] for dep in incident_deps if dep["updated_at"] is not None]
        verified = [dep["service_id"] for dep in incident_deps if dep["verified_at"] is not None]
        never_updated = [dep["service_id"] for dep in incident_deps if dep["updated_at"] is None]
        updated_but_unverified = [service for service in updated if service not in verified]
        coverage = {"total": len(incident_deps), "updated": len(updated), "verified": len(verified),
                    "updated_but_unverified": sorted(updated_but_unverified),
                    "never_updated": sorted(never_updated)}
        if never_updated:
            flags.add("DEPENDENCY_UPDATE_MISSING")
            missing_actions.append("以下依赖未更新到新凭据：" + ", ".join(sorted(never_updated)))
        if updated_but_unverified:
            flags.add("DEPENDENCY_VERIFICATION_MISSING")
            missing_actions.append("以下依赖缺少部署验证证据：" + ", ".join(sorted(updated_but_unverified)))
        if revoked_at is not None and steps["postcheck_passed"]["done"] is False:
            flags.add("POSTCHECK_MISSING")
            missing_actions.append("补录撤销后的后检证据")
        if revoked_at is not None and not steps["alert_closed"]["done"]:
            flags.add("ALERT_NOT_CLOSED")
            missing_actions.append("撤销完成后关闭对应安全告警")
        if not incident_events:
            flags.add("NO_EVENTS")

        ack_actual = hours_between(detected_at,
                                   datetime.fromisoformat(steps["owner_notified"]["occurred_at"])
                                   if steps["owner_notified"]["done"] else None)
        rotation_actual = hours_between(detected_at, revoked_at if revoked_at is not None else as_of)
        ack_breached = (sla["ack_hours"] is not None and ack_actual is not None
                        and ack_actual > sla["ack_hours"])
        rotation_breached = (sla["rotation_hours"] is not None and rotation_actual is not None
                             and rotation_actual > sla["rotation_hours"])
        if ack_breached:
            flags.add("ACK_SLA_EXCEEDED")
            sla_breaches.append({"incident_id": incident_id, "code": "ACK_SLA_EXCEEDED",
                                 "detail": "从检测到通知负责人用时 %s 小时，超过 SLA %s 小时"
                                           % (quant(ack_actual), quant(sla["ack_hours"]))})
        if rotation_breached:
            flags.add("ROTATION_SLA_EXCEEDED")
            sla_breaches.append({"incident_id": incident_id, "code": "ROTATION_SLA_EXCEEDED",
                                 "detail": "从检测到旧密钥撤销用时 %s 小时，超过 SLA %s 小时"
                                           % (quant(rotation_actual), quant(sla["rotation_hours"]))})

        partial = bool({"OWNER_MISSING", "FINGERPRINT_MISSING", "DETECTION_TIME_MISSING"} & flags)
        if partial:
            status = "PARTIAL"
        elif ack_breached or rotation_breached:
            status = "SLA_BREACH"
        elif revoked_at is not None:
            if steps["postcheck_passed"]["done"] and not never_updated and not updated_but_unverified \
                    and steps["alert_closed"]["done"]:
                status = "CLOSED_WITH_EVIDENCE"
            else:
                status = "REVOKED_PENDING_VERIFY"
        elif created_at is not None and not never_updated and not updated_but_unverified:
            status = "READY_TO_REVOKE"
        elif created_at is not None or updated:
            status = "ROTATION_IN_PROGRESS"
        elif steps["owner_notified"]["done"] or detected_at is not None:
            status = "OPEN"
        else:
            status = "PARTIAL"
            flags.add("NO_TIMELINE")

        exposure_hours = hours_between(incident["exposed_at"],
                                       revoked_at if revoked_at is not None else as_of)
        for conflict in conflicts:
            flags.add(conflict["code"])
            conflict_rows.append({"incident_id": incident_id, "code": conflict["code"],
                                  "detail": conflict["detail"]})
        for code in ("DEPENDENCY_UPDATE_MISSING", "DEPENDENCY_VERIFICATION_MISSING",
                     "POSTCHECK_MISSING", "DEPENDENCY_ATTRIBUTION_MISSING",
                     "DEPENDENCY_REFERENCE_UNKNOWN", "ALERT_NOT_CLOSED"):
            if code in flags:
                evidence_gaps.append({"incident_id": incident_id, "code": code,
                                      "detail": "、".join(missing_actions) or code})

        rows.append({
            "incident_id": incident_id,
            "provider": incident["provider"], "secret_type": incident["secret_type"],
            "fingerprint": incident["fingerprint"], "environment": incident["environment"],
            "severity": incident["severity"], "owner": incident["owner"],
            "status": status,
            "review_flags": sorted(flags),
            "exposed_at": incident["exposed_at"].isoformat() if incident["exposed_at"] else None,
            "detected_at": detected_at.isoformat() if detected_at else None,
            "steps": steps,
            "timeline": [{"event_id": entry["event_id"], "type": entry["type"],
                          "occurred_at": entry["occurred_at"].isoformat(),
                          "evidence_id": entry["evidence_id"], "actor": entry["actor"],
                          "service_id": entry["service_id"]} for entry in incident_events],
            "dependencies": [{"service_id": dep["service_id"], "owner": dep["owner"],
                              "criticality": dep["criticality"],
                              "updated": dep["updated_at"] is not None,
                              "verified": dep["verified_at"] is not None,
                              "updated_at": dep["updated_at"].isoformat() if dep["updated_at"] else None,
                              "verified_at": dep["verified_at"].isoformat() if dep["verified_at"] else None}
                             for dep in sorted(incident_deps, key=lambda d: d["service_id"])],
            "dependency_coverage": coverage,
            "missing_actions": missing_actions,
            "conflicts": conflicts,
            "exposure_window_hours": None if exposure_hours is None else quant(exposure_hours),
            "old_secret_valid_window_open": revoked_at is None,
            "sla": {"ack_hours_limit": None if sla["ack_hours"] is None else quant(sla["ack_hours"]),
                    "rotation_hours_limit": None if sla["rotation_hours"] is None
                                             else quant(sla["rotation_hours"]),
                    "ack_hours_actual": None if ack_actual is None else quant(ack_actual),
                    "rotation_hours_actual": None if rotation_actual is None
                                             else quant(rotation_actual),
                    "ack_breached": ack_breached, "rotation_breached": rotation_breached},
            "next_actions": sorted(set(missing_actions)) or ["无需补充动作；保留证据以备复核"],
        })

    rows.sort(key=lambda item: item["incident_id"])
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    overall = min((row["status"] for row in rows), key=lambda name: STATUS_ORDER.index(name))
    if orphans:
        overall = min(overall, "PARTIAL", key=lambda name: STATUS_ORDER.index(name))

    result = {
        "as_of": as_of.isoformat(),
        "status": overall,
        "incident_count": len(rows),
        "status_counts": counts,
        "incidents": rows,
        "sla_breaches": sorted(sla_breaches, key=lambda item: (item["incident_id"], item["code"])),
        "evidence_gaps": sorted(evidence_gaps, key=lambda item: (item["incident_id"], item["code"])),
        "conflicts": sorted(conflict_rows, key=lambda item: (item["incident_id"], item["code"])),
        "duplicate_events": duplicate_events,
        "orphan_dependencies": sorted(orphans, key=lambda item: item["service_id"]),
        "note": "只审计用户提供的、已脱敏的事件与依赖记录。不验证或撤销真实密钥、不访问 GitHub 或云平台、"
                "不改历史、不自动关闭安全告警；从代码删除或关闭告警都不等于供应商侧撤销。"
                "dependency_updated/deployment_verified 事件建议带 service_id 才能精确归属到具体依赖。",
    }
    result["markdown_summary"] = build_markdown(result)
    return result


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def build_markdown(result):
    lines = ["# 泄露密钥轮换与撤销证据闭环\n\n"]
    lines.append("基准时间 %s，共 %d 个 incident。**总体判定：%s**。\n\n"
                 % (result["as_of"], result["incident_count"], result["status"]))
    if not result["incidents"]:
        lines.append("没有任何 incident 可审计。\n")
        lines.append("\n本技能不验证或撤销真实密钥、不访问 GitHub/云平台、不改历史、不自动关闭安全告警。")
        return "".join(lines)
    lines.append("状态分布：" + ", ".join("%s=%d" % (key, value)
                                          for key, value in sorted(result["status_counts"].items())) + "。\n\n")
    rows = [["incident", "严重度", "状态", "暴露窗口(h)", "依赖覆盖", "旧凭据窗口"]]
    for row in result["incidents"]:
        coverage = row["dependency_coverage"]
        rows.append([row["incident_id"], row["severity"] or "—", row["status"],
                     row["exposure_window_hours"] or "—",
                     "%d/%d 更新 · %d/%d 验证" % (coverage["updated"], coverage["total"],
                                                  coverage["verified"], coverage["total"]),
                     "仍可能有效" if row["old_secret_valid_window_open"] else "已撤销"])
    lines.append(md_table(rows))
    lines.append("\n\n")
    for row in result["incidents"]:
        lines.append("## %s（%s）\n\n" % (row["incident_id"], row["status"]))
        lines.append("- 供应商 %s / 类型 %s / 环境 %s / 负责人 %s\n"
                     % (row["provider"] or "—", row["secret_type"] or "—",
                        row["environment"] or "—", row["owner"] or "—"))
        lines.append("- 暴露窗口 %s 小时；旧凭据是否仍可能有效：%s\n"
                     % (row["exposure_window_hours"] or "—",
                        "是" if row["old_secret_valid_window_open"] else "否"))
        lines.append("- 依赖覆盖：更新 %d/%d，验证 %d/%d；未更新 %s；已更新未验证 %s\n"
                     % (row["dependency_coverage"]["updated"], row["dependency_coverage"]["total"],
                        row["dependency_coverage"]["verified"], row["dependency_coverage"]["total"],
                        ", ".join(row["dependency_coverage"]["never_updated"]) or "无",
                        ", ".join(row["dependency_coverage"]["updated_but_unverified"]) or "无"))
        if row["review_flags"]:
            lines.append("- 标记：" + ", ".join(row["review_flags"]) + "\n")
        if row["missing_actions"]:
            lines.append("- 待办：\n")
            for action in row["missing_actions"]:
                lines.append("  - " + action + "\n")
        lines.append("\n")
    lines.append("本技能不验证或撤销真实密钥、不访问 GitHub/云平台、不改历史、不自动关闭安全告警；"
                 "从代码删除或关闭告警都不等于供应商侧撤销。")
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="UTF-8 JSON file; maximum 4 MB")
    args = parser.parse_args()
    try:
        path = Path(args.input)
        with path.open("rb") as handle:
            raw = handle.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("input exceeds 4 MB")
        data = json.loads(raw.decode("utf-8-sig"))
        print(json.dumps(analyze(data), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, InvalidOperation):
        print(json.dumps({"error": "invalid_input",
                          "message": "请对照 references/guide.md 检查：输入必须已脱敏（不得含密钥原文、"
                                     "secret_value 字段或无私钥/令牌样式字符串）、as_of 带时区、"
                                     "incident_id/service_id/event_id 唯一、事件类型在允许集合内、"
                                     "事件时间不晚于 as_of。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
