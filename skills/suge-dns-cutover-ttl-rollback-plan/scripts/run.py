#!/usr/bin/env python3
"""Offline DNS cutover TTL & rollback window dry-run.

Replays a planned DNS change against user-supplied snapshots: TTL lowering
history, resolver observations and declared preconditions.  Dry-run only: never
resolves a name, never contacts a resolver, never changes a DNS record.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
SECRET_HINTS = (
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
)
RECORD_TYPES = ("A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "CAA")
# Worst-first severity ladder, used for both per-record and overall verdicts.
SEVERITY = ["INVALID", "RECORD_CONFLICT", "TOO_LATE_TO_LOWER_TTL",
            "OBSERVATION_GAP", "UNKNOWN", "READY_FOR_HUMAN_REVIEW"]


def clean_text(value, label, maximum=200, allow_empty=False):
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
    for pattern in SECRET_HINTS:
        if pattern.search(text):
            raise ValueError(label + " looks like a credential; remove it before planning")
    return text


def number(value, label, minimum=Decimal("0"), maximum=Decimal("1000000000")):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
    return result


def integer(value, label, minimum=Decimal("0"), maximum=Decimal("1000000000")):
    result = number(value, label, minimum, maximum)
    if result != result.to_integral_value():
        raise ValueError(label + " must be an integer")
    return int(result)


def parse_bool(value, label):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n"):
        return False
    raise ValueError(label + " must be a boolean")


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


def quant(value, places=2):
    result = Decimal(value).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
    if result == 0:
        result = abs(result)
    return str(result)


def parse_policy(policy):
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    proxied_ttl = integer(policy.get("proxied_ttl_seconds", 300), "policy.proxied_ttl_seconds",
                          Decimal("1"), Decimal("604800"))
    max_age = number(policy.get("max_observation_age_hours", 24), "policy.max_observation_age_hours",
                     Decimal("0"), Decimal("8760"))
    tz = policy.get("timezone")
    tz_text = clean_text(str(tz), "policy.timezone", maximum=64, allow_empty=True) if tz is not None else ""
    return {"proxied_ttl_seconds": proxied_ttl, "max_observation_age_hours": max_age, "timezone": tz_text}


def parse_record(item, seen_ids):
    if not isinstance(item, dict):
        raise ValueError("each record must be an object")
    record_id = clean_text(item.get("record_id") or item.get("id"), "record.record_id", maximum=120)
    if record_id in seen_ids:
        raise ValueError("duplicate record_id: " + record_id)
    seen_ids.add(record_id)
    name = clean_text(item.get("name"), "record.name", maximum=253).lower()
    rtype = clean_text(item.get("type"), "record.type", maximum=10).upper()
    if rtype not in RECORD_TYPES:
        raise ValueError("record.type must be one of " + ", ".join(RECORD_TYPES))
    old_raw = item.get("old_value")
    old_value = None
    if old_raw is not None and not (isinstance(old_raw, str) and old_raw.strip() == ""):
        old_value = clean_text(old_raw, "record.old_value", maximum=500)
    new_raw = item.get("new_value")
    new_value = None
    if new_raw is not None and not (isinstance(new_raw, str) and new_raw.strip() == ""):
        new_value = clean_text(new_raw, "record.new_value", maximum=500)
    current_ttl = integer(item.get("current_ttl"), "record.current_ttl", Decimal("0"), Decimal("604800"))
    provider_min_ttl = integer(item.get("provider_min_ttl"), "record.provider_min_ttl",
                               Decimal("1"), Decimal("604800"))
    proxied_raw = item.get("proxied")
    proxied = parse_bool(proxied_raw, "record.proxied") if proxied_raw is not None else False
    return {"record_id": record_id, "name": name, "type": rtype, "old_value": old_value,
            "new_value": new_value, "current_ttl": current_ttl,
            "provider_min_ttl": provider_min_ttl, "proxied": proxied}


def parse_lowering(item, records):
    if not isinstance(item, dict):
        raise ValueError("each ttl_lowering must be an object")
    record_id = clean_text(item.get("record_id"), "ttl_lowering.record_id", maximum=120)
    if record_id not in records:
        raise ValueError("ttl_lowering references unknown record_id: " + record_id)
    lowered_at = parse_dt(item.get("lowered_at"), "ttl_lowering.lowered_at")
    from_ttl = integer(item.get("from_ttl"), "ttl_lowering.from_ttl", Decimal("0"), Decimal("604800"))
    to_ttl = integer(item.get("to_ttl"), "ttl_lowering.to_ttl", Decimal("0"), Decimal("604800"))
    return {"record_id": record_id, "lowered_at": lowered_at, "from_ttl": from_ttl, "to_ttl": to_ttl}


def parse_observation(item, records):
    if not isinstance(item, dict):
        raise ValueError("each observation must be an object")
    record_id = clean_text(item.get("record_id"), "observation.record_id", maximum=120)
    if record_id not in records:
        raise ValueError("observation references unknown record_id: " + record_id)
    resolver = clean_text(item.get("resolver"), "observation.resolver", maximum=120)
    observed_value = clean_text(item.get("observed_value"), "observation.observed_value", maximum=500)
    observed_at = parse_dt(item.get("observed_at"), "observation.observed_at")
    observed_ttl_raw = item.get("observed_ttl")
    observed_ttl = integer(observed_ttl_raw, "observation.observed_ttl", Decimal("0"), Decimal("604800")) \
        if observed_ttl_raw is not None and str(observed_ttl_raw).strip() != "" else None
    source_id = clean_text(item.get("source_id"), "observation.source_id", maximum=120)
    return {"record_id": record_id, "resolver": resolver, "observed_value": observed_value,
            "observed_at": observed_at, "observed_ttl": observed_ttl, "source_id": source_id}


def evaluate_record(record, lowerings, observations, policy, planned_cutover_at, planned_rollback_at,
                    as_of, conflict_flags, preconditions):
    flags = list(conflict_flags)
    reasons = []
    effective_ttl = policy["proxied_ttl_seconds"] if record["proxied"] else record["current_ttl"]

    if record["current_ttl"] < 1:
        flags.append("INVALID_TTL")
    if record["new_value"] is None:
        flags.append("MISSING_NEW_VALUE")
    if record["old_value"] is None:
        flags.append("MISSING_OLD_VALUE")
    if not record["proxied"] and record["current_ttl"] < record["provider_min_ttl"]:
        flags.append("TTL_BELOW_PROVIDER_MIN")
    if record["proxied"]:
        flags.append("PROXIED_FIXED_TTL")

    own = sorted([item for item in lowerings if item["record_id"] == record["record_id"]],
                 key=lambda item: item["lowered_at"])
    latest_expiry = None
    expiry_source_ttl = None
    for item in own:
        candidate = item["lowered_at"] + timedelta(seconds=item["from_ttl"])
        if latest_expiry is None or candidate > latest_expiry:
            latest_expiry = candidate
            expiry_source_ttl = item["from_ttl"]
    if not own and not record["proxied"]:
        flags.append("NO_TTL_LOWERING")

    obs = sorted([item for item in observations if item["record_id"] == record["record_id"]],
                 key=lambda item: item["observed_at"])
    cutoff = as_of - timedelta(hours=float(policy["max_observation_age_hours"]))
    fresh = [item for item in obs if item["observed_at"] >= cutoff]
    confirmed_new = [item for item in fresh if item["observed_value"] == record["new_value"]]
    still_old = [item for item in fresh
                 if record["old_value"] is not None and item["observed_value"] == record["old_value"]]
    other = [item for item in fresh
             if item["observed_value"] not in (record["new_value"], record["old_value"])]
    if not obs:
        flags.append("NO_OBSERVATION")
    elif not fresh:
        flags.append("OBSERVATION_STALE")
    if still_old:
        flags.append("STILL_SERVING_OLD")
    if other:
        flags.append("UNEXPECTED_VALUE_OBSERVED")

    if latest_expiry is not None and latest_expiry > as_of:
        flags.append("PROPAGATION_WINDOW_OPEN")
    propagation_confirmed = (latest_expiry is not None and latest_expiry <= as_of
                             and bool(confirmed_new) and not still_old)

    planned_cutover_at_for_record = planned_cutover_at
    if planned_cutover_at is None:
        flags.append("NO_CUTOVER_TIME")
    if latest_expiry is not None and planned_cutover_at is not None and latest_expiry > planned_cutover_at:
        flags.append("TTL_LOWERED_TOO_LATE")

    if preconditions is not None:
        for key, value in sorted(preconditions.items()):
            if not value:
                flags.append("PRECONDITION_PENDING:" + key)

    status = None
    if "INVALID_TTL" in flags:
        status = "INVALID"
        reasons.append("current_ttl 小于 1，属于无效配置")
    elif any(flag in flags for flag in ("DUPLICATE_RECORD", "CNAME_CONFLICT", "MISSING_NEW_VALUE",
                                        "TTL_BELOW_PROVIDER_MIN")):
        status = "RECORD_CONFLICT"
        if "DUPLICATE_RECORD" in flags:
            reasons.append("同名同类型的记录出现多次，无法确定哪一条才是要切换的目标")
        if "CNAME_CONFLICT" in flags:
            reasons.append("同一名称下 CNAME 与其他记录共存，属于冲突配置")
        if "MISSING_NEW_VALUE" in flags:
            reasons.append("缺少切换后的新值，无法预演")
        if "TTL_BELOW_PROVIDER_MIN" in flags:
            reasons.append("TTL %d 低于该服务商声明的最小 TTL %d"
                           % (record["current_ttl"], record["provider_min_ttl"]))
    elif latest_expiry is not None and planned_cutover_at is not None and latest_expiry > planned_cutover_at:
        status = "TOO_LATE_TO_LOWER_TTL"
        reasons.append("旧缓存理论最晚过期时间 %s 晚于计划切换时间 %s，TTL 下调过晚"
                       % (latest_expiry.isoformat(), planned_cutover_at.isoformat()))
    elif not propagation_confirmed:
        status = "OBSERVATION_GAP"
        if latest_expiry is None:
            reasons.append("没有 TTL 下调记录，无法界定理论过期窗口，也就无法确认传播完成")
        elif latest_expiry > as_of:
            reasons.append("理论过期窗口尚未走完，不得把理论过期当作全球传播完成")
        elif still_old:
            reasons.append("基准时间后仍有解析器返回旧值，传播未完成")
        elif not confirmed_new:
            reasons.append("没有任何新鲜观测确认已返回新值")
        else:
            reasons.append("观测证据不足，无法确认传播完成")
    elif planned_cutover_at is None or record["old_value"] is None:
        status = "UNKNOWN"
        if planned_cutover_at is None:
            reasons.append("未提供计划切换时间，无法给出最早建议人工切换时刻")
        if record["old_value"] is None:
            reasons.append("缺少切换前的旧值，无法判断解析器是否仍在返回旧值")
    elif preconditions is not None and any(not value for value in preconditions.values()):
        status = "UNKNOWN"
        pending = sorted(key for key, value in preconditions.items() if not value)
        reasons.append("用户声明的前置条件未就绪：" + ", ".join(pending))
    else:
        status = "READY_FOR_HUMAN_REVIEW"
        reasons.append("TTL 下调早于计划切换时间至少一个旧 TTL，观测已确认新值生效，等待人工执行")

    lead_seconds = None
    if latest_expiry is not None and planned_cutover_at is not None:
        lead_seconds = Decimal(str(round((planned_cutover_at - latest_expiry).total_seconds(), 4)))

    rollback_fully_effective_at = None
    if planned_rollback_at is not None:
        rollback_fully_effective_at = planned_rollback_at + timedelta(seconds=effective_ttl)

    timeline = []
    for item in own:
        timeline.append({"at": item["lowered_at"].isoformat(), "event": "TTL_LOWERED",
                         "detail": "%d → %d" % (item["from_ttl"], item["to_ttl"])})
    if latest_expiry is not None:
        timeline.append({"at": latest_expiry.isoformat(), "event": "CACHE_THEORETICALLY_EXPIRED",
                         "detail": "按旧 TTL %s 推演" % expiry_source_ttl})
    if planned_cutover_at is not None:
        timeline.append({"at": planned_cutover_at.isoformat(), "event": "PLANNED_CUTOVER", "detail": ""})
    if planned_rollback_at is not None:
        timeline.append({"at": planned_rollback_at.isoformat(), "event": "PLANNED_ROLLBACK", "detail": ""})
    for item in obs:
        timeline.append({"at": item["observed_at"].isoformat(), "event": "OBSERVED",
                         "detail": "%s → %s" % (item["resolver"], item["observed_value"])})
    timeline.sort(key=lambda entry: entry["at"])

    missing = []
    if not obs:
        missing.append("没有任何解析器观测样本")
    if not fresh:
        missing.append("没有超过陈旧阈值的新鲜观测（阈值 %s 小时）"
                       % quant(policy["max_observation_age_hours"], 2))
    if latest_expiry is None:
        missing.append("没有 TTL 下调事件记录")
    if planned_cutover_at is None:
        missing.append("没有计划切换时间")
    if record["old_value"] is None:
        missing.append("没有切换前旧值")
    if preconditions is None:
        missing.append("未声明健康检查/回滚前置条件")

    checklist = [
        "确认切换前已在权威 DNS 完成 TTL 下调，并已等待超过一个旧 TTL",
        "确认健康检查通过、回滚方案已就绪，且回滚操作已演练",
        "切换后按多个解析器抽样验证新值生效，不只依赖理论过期时间",
    ]
    if record["proxied"]:
        checklist.append("代理记录使用固定 TTL，无法靠下调 TTL 缩短传播时间，必须以观测为准")
    if status == "TOO_LATE_TO_LOWER_TTL":
        checklist.append("当前 TTL 下调过晚，建议把人工切换推迟到 %s 之后" % latest_expiry.isoformat())
    if status == "OBSERVATION_GAP":
        checklist.append("补齐切换前的多解析器观测证据（带时间戳与来源标识）后再判断传播是否完成")
    if status == "RECORD_CONFLICT":
        checklist.append("先解决同名记录冲突、重复记录或 TTL 低于服务商下限的问题")
    if status == "UNKNOWN":
        checklist.append("补齐缺失证据后再预演，不要以缺失值当默认值")

    return {
        "record_id": record["record_id"], "name": record["name"], "type": record["type"],
        "old_value": record["old_value"], "new_value": record["new_value"],
        "current_ttl": record["current_ttl"], "provider_min_ttl": record["provider_min_ttl"],
        "proxied": record["proxied"], "effective_ttl_seconds": effective_ttl,
        "status": status,
        "latest_cache_expiry_at": None if latest_expiry is None else latest_expiry.isoformat(),
        "earliest_safe_cutover_at": None if latest_expiry is None else latest_expiry.isoformat(),
        "planned_cutover_at": None if planned_cutover_at_for_record is None
                              else planned_cutover_at_for_record.isoformat(),
        "ttl_lowering_lead_seconds": None if lead_seconds is None else quant(lead_seconds, 2),
        "rollback_fully_effective_at": None if rollback_fully_effective_at is None
                                        else rollback_fully_effective_at.isoformat(),
        "rollback_exposure_seconds": quant(Decimal(effective_ttl), 2),
        "observation": {
            "total": len(obs), "fresh": len(fresh), "confirmed_new": len(confirmed_new),
            "still_old": len(still_old), "unexpected_value": len(other),
            "latest_observed_at": obs[-1]["observed_at"].isoformat() if obs else None,
            "resolvers": sorted({item["resolver"] for item in obs}),
        },
        "propagation_confirmed": propagation_confirmed,
        "timeline": timeline, "missing_evidence": missing,
        "review_flags": sorted(set(flags)), "reasons": reasons, "human_checklist": checklist,
    }


def build_markdown(result):
    rows = [["record_id", "名称", "类型", "当前TTL", "有效TTL", "理论过期", "最早可切换", "观测(新/旧)",
             "状态"]]
    for rec in result["records"]:
        obs = rec["observation"]
        rows.append([rec["record_id"], rec["name"], rec["type"], rec["current_ttl"],
                     rec["effective_ttl_seconds"], rec["latest_cache_expiry_at"] or "—",
                     rec["earliest_safe_cutover_at"] or "—",
                     "%d/%d" % (obs["confirmed_new"], obs["still_old"]), rec["status"]])
    head = ["# DNS 切换 TTL 与回滚窗口预演（基准 %s）\n\n" % result["as_of"]]
    head.append("口径：**只根据你提供的快照预演**，不执行 dig/curl、不访问域名、不修改解析。"
                "TTL 下调必须早于计划切换时间至少一个旧 TTL，否则标 TOO_LATE_TO_LOWER_TTL；"
                "**理论过期不等于全球传播完成**，必须有新鲜观测确认新值生效。\n\n")
    head.append(md_table(rows))
    head.append("\n\n状态分布：" + ", ".join("%s=%d" % (k, v) for k, v in sorted(result["status_counts"].items())) + "。\n")
    head.append("\n总体判定：**%s**（按最严重记录优先给出）。\n" % result["status"])
    order = result["recommended_cutover_order"]
    if order:
        head.append("\n建议人工切换顺序（按最早可切换时刻）：%s。\n" % " → ".join(order))
    head.append("\n本技能不执行任何解析操作，也不修改任何 DNS 记录。")
    return "".join(head)


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    as_of = parse_dt(data.get("as_of"), "as_of")
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    planned_cutover_at = parse_dt(data.get("planned_cutover_at"), "planned_cutover_at", required=False)
    if planned_cutover_at is not None and planned_cutover_at.tzinfo is None:
        raise ValueError("planned_cutover_at must be timezone-aware")
    planned_rollback_at = parse_dt(data.get("planned_rollback_at"), "planned_rollback_at", required=False)
    if planned_rollback_at is not None and planned_rollback_at.tzinfo is None:
        raise ValueError("planned_rollback_at must be timezone-aware")
    policy = parse_policy(data.get("policy"))

    records_raw = data.get("records")
    if not isinstance(records_raw, list) or not 1 <= len(records_raw) <= 2000:
        raise ValueError("records must be a list of 1-2000 items")
    seen_ids = set()
    record_list = [parse_record(item, seen_ids) for item in records_raw]
    records = {item["record_id"]: item for item in record_list}

    lowerings_raw = data.get("ttl_lowerings")
    if lowerings_raw is None:
        lowerings_raw = []
    if not isinstance(lowerings_raw, list) or len(lowerings_raw) > 5000:
        raise ValueError("ttl_lowerings must be a list of up to 5000 items")
    lowerings = [parse_lowering(item, records) for item in lowerings_raw]

    observations_raw = data.get("observations")
    if observations_raw is None:
        observations_raw = []
    if not isinstance(observations_raw, list) or len(observations_raw) > 20000:
        raise ValueError("observations must be a list of up to 20000 items")
    observations = [parse_observation(item, records) for item in observations_raw]

    preconditions_raw = data.get("preconditions")
    preconditions = None
    if preconditions_raw is not None:
        if not isinstance(preconditions_raw, dict) or not preconditions_raw:
            raise ValueError("preconditions must be a nonempty object when provided")
        preconditions = {clean_text(str(key), "preconditions key", maximum=60):
                         parse_bool(value, "preconditions." + str(key))
                         for key, value in preconditions_raw.items()}

    # Structural conflicts, computed across records.
    by_name = {}
    for record in record_list:
        by_name.setdefault(record["name"], []).append(record)
    conflict_map = {record["record_id"]: [] for record in record_list}
    for name, members in by_name.items():
        types = [member["type"] for member in members]
        if len(members) > 1 and len(set(types)) != len(types):
            for member in members:
                conflict_map[member["record_id"]].append("DUPLICATE_RECORD")
        if "CNAME" in types and len(members) > 1:
            for member in members:
                conflict_map[member["record_id"]].append("CNAME_CONFLICT")

    evaluated = [evaluate_record(record, lowerings, observations, policy, planned_cutover_at,
                                 planned_rollback_at, as_of, conflict_map[record["record_id"]],
                                 preconditions)
                 for record in record_list]

    status_counts = {}
    for record in evaluated:
        status_counts[record["status"]] = status_counts.get(record["status"], 0) + 1

    present = [status for status in SEVERITY if status in status_counts]
    overall = present[0] if present else "UNKNOWN"

    ordered = sorted([record for record in evaluated if record["earliest_safe_cutover_at"]],
                     key=lambda record: record["earliest_safe_cutover_at"])

    result = {
        "as_of": as_of.isoformat(),
        "planned_cutover_at": None if planned_cutover_at is None else planned_cutover_at.isoformat(),
        "planned_rollback_at": None if planned_rollback_at is None else planned_rollback_at.isoformat(),
        "record_count": len(evaluated),
        "observation_count": len(observations),
        "status": overall,
        "status_counts": status_counts,
        "records": evaluated,
        "recommended_cutover_order": [record["record_id"] for record in ordered],
        "preconditions": None if preconditions is None else dict(sorted(preconditions.items())),
        "note": "只根据用户提供的快照做只读预演：不执行 dig/curl、不访问域名、不修改解析记录。"
                "TTL 下调必须早于计划切换至少一个旧 TTL；理论过期不等于全球传播完成，"
                "必须有新鲜观测确认新值生效。",
    }
    result["markdown_summary"] = build_markdown(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="UTF-8 JSON file; maximum 2 MB")
    args = parser.parse_args()
    try:
        path = Path(args.input)
        with path.open("rb") as handle:
            raw = handle.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("input exceeds 2 MB")
        data = json.loads(raw.decode("utf-8-sig"))
        print(json.dumps(analyze(data), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, InvalidOperation):
        print(json.dumps({"error": "invalid_input",
                          "message": "请对照 references/guide.md 检查：基准时间带时区、record_id 唯一、"
                                     "current_ttl 为 0-604800 的整数、观测引用的 record_id 已声明、"
                                     "preconditions 为布尔对象。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
