#!/usr/bin/env python3
"""Offline API rate-limit headroom & backoff planner.

Computes per-dimension headroom, peak utilisation, retry-after pressure and a
conservative throttle plan for a backlog.  Simulation only: never issues a
request, never changes a gateway, never probes a provider.
"""
import argparse
import json
import math
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


def number(value, label, minimum=Decimal("0"), maximum=Decimal("1000000000000")):
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
    return str(Decimal(value).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP))


def parse_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    safety = number(policy.get("safety_utilization", "0.8"), "policy.safety_utilization",
                    Decimal("0.01"), Decimal("1"))
    target = number(policy.get("throttle_target_utilization", "0.7"), "policy.throttle_target_utilization",
                    Decimal("0.01"), Decimal("1"))
    tolerance = number(policy.get("remaining_tolerance_abs", 0), "policy.remaining_tolerance_abs",
                       Decimal("0"), Decimal("1000000"))
    tz = policy.get("timezone")
    tz_text = clean_text(str(tz), "policy.timezone", maximum=64, allow_empty=True) if tz is not None else ""
    return {"safety_utilization": safety, "throttle_target_utilization": target,
            "remaining_tolerance_abs": tolerance, "timezone": tz_text}


def parse_profile(item, seen_ids):
    if not isinstance(item, dict):
        raise ValueError("each quota profile must be an object")
    profile_id = clean_text(item.get("profile_id") or item.get("id"), "profile_id", maximum=120)
    if profile_id in seen_ids:
        raise ValueError("duplicate profile_id: " + profile_id)
    seen_ids.add(profile_id)
    provider = clean_text(str(item.get("provider") or "unknown"), "provider", maximum=80)
    keys_raw = item.get("dimension_keys")
    if not isinstance(keys_raw, list) or not 1 <= len(keys_raw) <= 4:
        raise ValueError("dimension_keys must be a list of 1-4 fields")
    dimension_keys = [clean_text(entry, "dimension_keys[]", maximum=60) for entry in keys_raw]
    if len(set(dimension_keys)) != len(dimension_keys):
        raise ValueError("dimension_keys must not repeat: " + profile_id)
    window_seconds = integer(item.get("window_seconds"), "window_seconds", Decimal("1"), Decimal("86400"))
    limit = integer(item.get("limit"), "limit", Decimal("1"), Decimal("1000000000"))
    remaining_raw = item.get("remaining")
    remaining = integer(remaining_raw, "remaining", Decimal("0"), Decimal("1000000000")) \
        if remaining_raw is not None and str(remaining_raw).strip() != "" else None
    reset_at = parse_dt(item.get("reset_at"), "reset_at", required=False)
    conc_raw = item.get("concurrency_limit")
    concurrency_limit = integer(conc_raw, "concurrency_limit", Decimal("1"), Decimal("100000")) \
        if conc_raw is not None and str(conc_raw).strip() != "" else None
    secondary_raw = item.get("secondary_limit_observable")
    secondary = parse_bool(secondary_raw, "secondary_limit_observable") if secondary_raw is not None else False
    return {"profile_id": profile_id, "provider": provider, "dimension_keys": dimension_keys,
            "window_seconds": window_seconds, "limit": limit, "remaining": remaining,
            "reset_at": reset_at, "concurrency_limit": concurrency_limit,
            "secondary_limit_observable": secondary}


def parse_bucket(item, profiles, seen_keys):
    if not isinstance(item, dict):
        raise ValueError("each request bucket must be an object")
    profile_id = clean_text(item.get("profile_id"), "bucket.profile_id", maximum=120)
    if profile_id not in profiles:
        raise ValueError("bucket references unknown profile_id: " + profile_id)
    profile = profiles[profile_id]
    dims_raw = item.get("dimensions")
    if not isinstance(dims_raw, dict):
        raise ValueError("bucket.dimensions must be an object for " + profile_id)
    dimensions = {}
    for key in profile["dimension_keys"]:
        if key not in dims_raw:
            raise ValueError("bucket.dimensions missing key %s for %s" % (key, profile_id))
        dimensions[key] = clean_text(str(dims_raw[key]), "bucket.dimensions." + key, maximum=120)
    window_start = parse_dt(item.get("window_start"), "bucket.window_start")
    window_end = parse_dt(item.get("window_end"), "bucket.window_end")
    if window_end <= window_start:
        raise ValueError("bucket.window_end must be later than window_start")
    key = (profile_id, tuple(dimensions[k] for k in profile["dimension_keys"]),
           window_start.isoformat(), window_end.isoformat())
    if key in seen_keys:
        raise ValueError("duplicate bucket window for " + profile_id)
    seen_keys.add(key)
    request_count = integer(item.get("request_count"), "bucket.request_count", Decimal("0"), Decimal("1000000000"))
    error_count = integer(item.get("error_429_403_count", 0), "bucket.error_429_403_count",
                          Decimal("0"), Decimal("1000000000"))
    retry_raw = item.get("retry_after_seconds")
    if retry_raw is None:
        retry_raw = []
    if not isinstance(retry_raw, list) or len(retry_raw) > 200:
        raise ValueError("retry_after_seconds must be a list of up to 200 numbers")
    retry_after = [number(entry, "retry_after_seconds[]", Decimal("0"), Decimal("86400")) for entry in retry_raw]
    max_concurrency = integer(item.get("max_concurrency", 0), "bucket.max_concurrency",
                              Decimal("0"), Decimal("100000"))
    latency_raw = item.get("latency_ms_p95")
    latency = number(latency_raw, "bucket.latency_ms_p95", Decimal("0"), Decimal("10000000")) \
        if latency_raw is not None and str(latency_raw).strip() != "" else None
    return {"profile_id": profile_id, "dimensions": dimensions, "window_start": window_start,
            "window_end": window_end, "request_count": request_count, "error_count": error_count,
            "retry_after": retry_after, "max_concurrency": max_concurrency, "latency_ms_p95": latency}


def current_bucket(buckets, as_of):
    containing = [b for b in buckets if b["window_start"] <= as_of < b["window_end"]]
    if containing:
        return max(containing, key=lambda b: b["window_start"])
    return max(buckets, key=lambda b: b["window_end"])


def audit_profile(profile, buckets, policy, as_of):
    reasons = []
    review_flags = []
    groups = {}
    for bucket in buckets:
        key = tuple(bucket["dimensions"][name] for name in profile["dimension_keys"])
        groups.setdefault(key, []).append(bucket)

    if not buckets:
        reasons.append("没有该 profile 的用量桶数据，无法核算余量")
        return {"profile_id": profile["profile_id"], "provider": profile["provider"],
                "dimension_keys": profile["dimension_keys"], "window_seconds": profile["window_seconds"],
                "limit": profile["limit"], "header_remaining": profile["remaining"],
                "concurrency_limit": profile["concurrency_limit"],
                "observed_max_concurrency": None,
                "secondary_limit_observable": profile["secondary_limit_observable"],
                "dimensions": [], "status": "UNKNOWN", "reasons": reasons,
                "review_flags": ["NO_USAGE_BUCKET"],
                "recommended_rate_per_second": quant(Decimal(profile["limit"]) / profile["window_seconds"]
                                                     * policy["throttle_target_utilization"], 4),
                "earliest_recovery_at": None,
                "assumptions": ["缺用量数据时不得声称余量充足"]}

    limit = Decimal(profile["limit"])
    rows = []
    worst_utilization = Decimal("0")
    total_errors = 0
    max_retry_after = Decimal("0")
    observed_concurrency = 0
    any_stale_window = False
    for key in sorted(groups):
        group = sorted(groups[key], key=lambda b: b["window_end"])
        active = current_bucket(group, as_of)
        peak = max(b["request_count"] for b in group)
        errors = sum(b["error_count"] for b in group)
        retry_max = max([entry for b in group for entry in b["retry_after"]] or [Decimal("0")])
        concurrency = max(b["max_concurrency"] for b in group)
        utilization = Decimal(active["request_count"]) / limit
        self_remaining = limit - Decimal(active["request_count"])
        header_remaining = profile["remaining"]
        consistent = True
        if header_remaining is not None:
            consistent = abs(Decimal(header_remaining) - self_remaining) <= policy["remaining_tolerance_abs"]
            if not consistent:
                review_flags.append("REMAINING_INCONSISTENT:" + "|".join(key))
        seconds_to_reset = None
        reset_crosses_day = None
        if profile["reset_at"] is not None:
            seconds_to_reset = Decimal(str(round((profile["reset_at"] - as_of).total_seconds(), 4)))
            reset_crosses_day = profile["reset_at"].date() != as_of.date()
        window_seconds_actual = Decimal(str(round((active["window_end"] - active["window_start"]).total_seconds(), 4)))
        if window_seconds_actual != Decimal(profile["window_seconds"]):
            any_stale_window = True
        rows.append({
            "dimension_values": dict(zip(profile["dimension_keys"], key)),
            "bucket_count": len(group),
            "used_in_current_window": active["request_count"],
            "peak_request_count": peak,
            "utilization": quant(utilization, 4),
            "utilization_pct": quant(utilization * 100, 2) + "%",
            "peak_utilization": quant(Decimal(peak) / limit, 4),
            "self_calculated_remaining": quant(self_remaining, 4),
            "header_remaining": header_remaining,
            "remaining_consistent": consistent,
            "error_429_403_count": errors,
            "retry_after_max_seconds": quant(retry_max, 2) if retry_max > 0 else None,
            "max_concurrency": concurrency,
            "latency_ms_p95": None if active["latency_ms_p95"] is None else quant(active["latency_ms_p95"], 2),
            "window_start": active["window_start"].isoformat(),
            "window_end": active["window_end"].isoformat(),
            "window_seconds_declared": profile["window_seconds"],
            "window_seconds_observed": quant(window_seconds_actual, 2),
            "reset_at": profile["reset_at"].isoformat() if profile["reset_at"] else None,
            "seconds_to_reset": None if seconds_to_reset is None else quant(seconds_to_reset, 2),
            "reset_crosses_day": reset_crosses_day,
        })
        worst_utilization = max(worst_utilization, utilization)
        total_errors += errors
        max_retry_after = max(max_retry_after, retry_max)
        observed_concurrency = max(observed_concurrency, concurrency)

    if any_stale_window:
        review_flags.append("WINDOW_LENGTH_MISMATCH")

    earliest_recovery = None
    if profile["reset_at"] is not None:
        earliest_recovery = profile["reset_at"].isoformat()
    elif max_retry_after > 0:
        earliest_recovery = (as_of + timedelta(seconds=float(max_retry_after))).isoformat()

    status = None
    if max_retry_after > 0:
        status = "RETRY_AFTER_ACTIVE"
        reasons.append("观测到 retry-after 最长 %s 秒，必须优先遵从服务端退避，不得使用本地退避覆盖"
                       % quant(max_retry_after, 2))
    elif profile["concurrency_limit"] is not None and observed_concurrency > profile["concurrency_limit"]:
        status = "CONCURRENCY_RISK"
        reasons.append("观测并发峰值 %d 超过声明并发上限 %d"
                       % (observed_concurrency, profile["concurrency_limit"]))
    elif total_errors > 0:
        status = "THROTTLE_RECOMMENDED"
        reasons.append("出现 %d 次 429/403：主限额有余量也不等于安全，必须降速" % total_errors)
    elif worst_utilization >= policy["safety_utilization"]:
        status = "THROTTLE_RECOMMENDED"
        reasons.append("峰值利用率 %s 达到/超过安全线 %s"
                       % (quant(worst_utilization, 4), quant(policy["safety_utilization"], 2)))
    elif not profile["secondary_limit_observable"]:
        status = "SECONDARY_LIMIT_UNKNOWN"
        reasons.append("次级限额不可观察：不得因主限额有余量就声称安全")
    else:
        status = "HEADROOM_OK"
        reasons.append("主限额余量充足、无 429/403、并发与退避均正常")

    return {"profile_id": profile["profile_id"], "provider": profile["provider"],
            "dimension_keys": profile["dimension_keys"], "window_seconds": profile["window_seconds"],
            "limit": profile["limit"], "header_remaining": profile["remaining"],
            "concurrency_limit": profile["concurrency_limit"],
            "observed_max_concurrency": observed_concurrency,
            "secondary_limit_observable": profile["secondary_limit_observable"],
            "dimensions": rows, "status": status, "reasons": reasons, "review_flags": review_flags,
            "recommended_rate_per_second": quant(limit / profile["window_seconds"]
                                                 * policy["throttle_target_utilization"], 4),
            "earliest_recovery_at": earliest_recovery,
            "assumptions": ["建议速率 = limit / window_seconds × 目标利用率 %s，仅为模拟参数"
                            % quant(policy["throttle_target_utilization"], 2),
                            "不跨窗口相加，不跨维度合并；仅输出模拟参数，不实际请求或修改网关"]}


def plan_backlog(backlog, profiles, policy, as_of):
    if backlog is None:
        return None
    if not isinstance(backlog, dict):
        raise ValueError("backlog must be an object or null")
    profile_id = clean_text(backlog.get("profile_id"), "backlog.profile_id", maximum=120)
    if profile_id not in profiles:
        raise ValueError("backlog references unknown profile_id: " + profile_id)
    profile = profiles[profile_id]
    pending = integer(backlog.get("pending_tasks"), "backlog.pending_tasks", Decimal("0"), Decimal("100000000"))
    per_task = integer(backlog.get("requests_per_task"), "backlog.requests_per_task", Decimal("0"),
                       Decimal("1000000"))
    deadline = parse_dt(backlog.get("deadline"), "backlog.deadline")
    total = pending * per_task
    limit = profile["limit"]
    window_seconds = profile["window_seconds"]
    windows_needed = math.ceil(total / limit) if total else 0
    theoretical_seconds = Decimal(windows_needed * window_seconds)
    theoretical_at = as_of + timedelta(seconds=float(theoretical_seconds))
    rate = (Decimal(limit) / window_seconds) * policy["throttle_target_utilization"]
    conservative_seconds = (Decimal(total) / rate) if rate > 0 else None
    conservative_at = as_of + timedelta(seconds=float(conservative_seconds)) if conservative_seconds is not None else None
    reachable = theoretical_at <= deadline
    tight = conservative_at is not None and conservative_at > deadline
    if not reachable:
        status = "BACKLOG_DEADLINE_RISK"
        risk = "UNREACHABLE"
    elif tight:
        status = "THROTTLE_RECOMMENDED"
        risk = "TIGHT"
    else:
        status = "HEADROOM_OK"
        risk = "REACHABLE"
    return {"profile_id": profile_id, "total_requests": total,
            "limit": limit, "window_seconds": window_seconds,
            "deadline": deadline.isoformat(),
            "theoretical_windows_needed": windows_needed,
            "theoretical_min_seconds": quant(theoretical_seconds, 2),
            "theoretical_completion_at": theoretical_at.isoformat(),
            "conservative_rate_per_second": quant(rate, 4),
            "conservative_min_seconds": None if conservative_seconds is None else quant(conservative_seconds, 2),
            "conservative_completion_at": None if conservative_at is None else conservative_at.isoformat(),
            "deadline_reachable": reachable, "risk_level": risk, "status": status,
            "assumptions": ["最短理论完成时间按整窗上限估算，未计入当前窗口剩余额度",
                            "保守节流按目标利用率 %s 折算，实际吞吐受网络与重试影响"
                            % quant(policy["throttle_target_utilization"], 2)]}


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    as_of = parse_dt(data.get("as_of"), "as_of")
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    policy = parse_policy(data.get("policy") or {})

    profiles_raw = data.get("quota_profiles")
    if not isinstance(profiles_raw, list) or not 1 <= len(profiles_raw) <= 200:
        raise ValueError("quota_profiles must be a list of 1-200 items")
    seen_profile_ids = set()
    profile_list = [parse_profile(item, seen_profile_ids) for item in profiles_raw]
    profiles = {profile["profile_id"]: profile for profile in profile_list}

    buckets_raw = data.get("request_buckets")
    if buckets_raw is None:
        buckets_raw = []
    if not isinstance(buckets_raw, list) or len(buckets_raw) > 20000:
        raise ValueError("request_buckets must be a list of up to 20000 items")
    seen_bucket_keys = set()
    buckets = [parse_bucket(item, profiles, seen_bucket_keys) for item in buckets_raw]

    by_profile = {}
    for bucket in buckets:
        by_profile.setdefault(bucket["profile_id"], []).append(bucket)

    results = [audit_profile(profile, by_profile.get(profile["profile_id"], []), policy, as_of)
               for profile in profile_list]
    counts = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1

    backlog_plan = plan_backlog(data.get("backlog"), profiles, policy, as_of)
    top_flags = []
    for result in results:
        for flag in result["review_flags"]:
            top_flags.append(result["profile_id"] + ":" + flag)

    return {
        "as_of": as_of.isoformat(),
        "profile_count": len(results),
        "bucket_count": len(buckets),
        "status_counts": counts,
        "profiles": results,
        "backlog_plan": backlog_plan,
        "review_flags": top_flags,
        "markdown_summary": build_markdown(results, backlog_plan, as_of, counts, policy),
        "note": "严格按 profile 声明的维度与窗口核算，不跨窗口相加、不跨维度合并；"
                "retry-after 优先于本地退避；次级限额不可观察时不得声称安全。"
                "本技能只输出模拟参数，不实际请求、不修改网关、不改变限流配置。",
    }


def build_markdown(results, backlog_plan, as_of, counts, policy):
    rows = [["Profile", "维度", "窗口(s)", "限额", "当前用量", "利用率", "余量", "429/403", "并发", "状态"]]
    for result in results:
        if not result["dimensions"]:
            rows.append([result["profile_id"], "+".join(result["dimension_keys"]), result["window_seconds"],
                         result["limit"], "—", "—", "—", "—", "—", result["status"]])
            continue
        for row in result["dimensions"]:
            rows.append([result["profile_id"], ",".join("%s=%s" % (k, v) for k, v in row["dimension_values"].items()),
                         result["window_seconds"], result["limit"], row["used_in_current_window"],
                         row["utilization_pct"], row["self_calculated_remaining"],
                         row["error_429_403_count"], row["max_concurrency"], result["status"]])
    head = ["# API 限额余量与退避预演（基准 %s）\n\n" % as_of.isoformat()]
    head.append("口径：严格按 profile 声明的维度与窗口核算，**不跨窗口相加、不跨维度合并**；"
                "retry-after 优先于本地退避；主限额有余量但有 429 时仍不视为安全；"
                "次级限额不可观察时标 SECONDARY_LIMIT_UNKNOWN。\n\n")
    head.append(md_table(rows))
    head.append("\n\n状态分布：" + ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())) + "。\n")
    if backlog_plan:
        head.append("\n积压计划：共 %d 请求，最短理论完成 %s（%s），保守速率 %s req/s，风险 %s。\n"
                    % (backlog_plan["total_requests"], backlog_plan["theoretical_completion_at"],
                       backlog_plan["theoretical_min_seconds"] + " s",
                       backlog_plan["conservative_rate_per_second"], backlog_plan["risk_level"]))
    else:
        head.append("\n未提供 backlog，本次只做余量核算。\n")
    head.append("\n本技能只输出模拟参数：不实际请求、不修改网关、不改变限流配置。")
    return "".join(head)


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


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
                          "message": "请对照 references/guide.md 检查带时区基准时间、profile_id 唯一、"
                                     "limit>0、bucket 维度与 profile.dimension_keys 一致、时间与数值合法性。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
