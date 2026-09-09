#!/usr/bin/env python3
"""Offline backup RPO (recovery point objective) coverage audit.
Only successful, not-yet-expired restore points count as coverage.  Latest
point age and max adjacent gap are compared against target RPO; retention
window checks the earliest valid point.  Restore tests are evaluated
separately from backup success.  Never performs backup/restore/delete or
policy changes."""
import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?$")
VALID_POINT_STATUS = {"success", "succeeded", "completed"}
FUTURE_TOLERANCE_HOURS = Decimal("1")


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


def text(value, label, maximum=120, allow_empty=False):
    if value is None:
        if allow_empty:
            return ""
        raise ValueError(label + " is required")
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(label + " must be text within length limit")
    value = value.strip()
    if not value and not allow_empty:
        raise ValueError(label + " must be nonempty text")
    return value


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
        # plain date -> treated as UTC midnight so interval math stays valid
        return datetime.combine(date.fromisoformat(raw), datetime.min.time(), tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(label + " is not a valid date") from None


def hours_between(later, earlier):
    """Wall-clock hours between two aware datetimes (absolute)."""
    if later.tzinfo is None or earlier.tzinfo is None:
        raise ValueError("timezone-aware datetimes required for interval math")
    delta = (later - earlier).total_seconds() / 3600.0
    return Decimal(str(round(delta, 4)))


def quant(value, places=2):
    return str(value.quantize(Decimal("1").scaleb(-places)))


def parse_point(point, seen_ids, asset_id, as_of):
    rp_id = text(point.get("rp_id") or point.get("restore_point_id") or point.get("id"), "rp_id", maximum=120)
    if rp_id in seen_ids:
        raise ValueError("duplicate restore point id in asset " + asset_id + ": " + rp_id)
    seen_ids.add(rp_id)
    status = text(str(point.get("status") or ""), "status", maximum=20)
    completed = parse_dt(point.get("completed_at"), "completed_at")
    expiry_raw = point.get("expiry_at")
    expiry = parse_dt(expiry_raw, "expiry_at", required=False) \
        if (expiry_raw is not None and str(expiry_raw).strip() != "") else None
    region = point.get("region")
    region_text = text(str(region or ""), "region", maximum=60, allow_empty=True) if region is not None else ""
    job = point.get("source_job_id")
    job_text = text(str(job or ""), "source_job_id", maximum=120, allow_empty=True) if job is not None else ""
    issues = []
    if completed > as_of:
        issues.append("completed_in_future（完成时间 %s 晚于基准，忽略不计入覆盖）" % completed.isoformat())
    return {"rp_id": rp_id, "status": status, "completed_at": completed, "expiry_at": expiry,
            "region": region_text, "source_job_id": job_text, "issues": issues}


def parse_asset(asset, as_of, seen_assets):
    asset_id = text(asset.get("asset_id"), "asset_id", maximum=80)
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", asset_id):
        raise ValueError("asset_id must be a plain identifier")
    if asset_id in seen_assets:
        raise ValueError("duplicate asset_id: " + asset_id)
    seen_assets.add(asset_id)
    criticality = text(str(asset.get("criticality") or "normal"), "criticality", maximum=16)
    if criticality not in ("critical", "high", "normal", "low", "unknown"):
        raise ValueError("criticality must be critical/high/normal/low/unknown")
    rpo_raw = asset.get("target_rpo_hours")
    rpo_hours = number(rpo_raw, "target_rpo_hours", minimum=Decimal("0.01"), maximum=Decimal("100000")) \
        if (rpo_raw is not None and str(rpo_raw).strip() != "") else None
    ret_raw = asset.get("target_retention_days")
    retention_days = integer(ret_raw, "target_retention_days", minimum=Decimal("1"), maximum=Decimal("100000")) \
        if (ret_raw is not None and str(ret_raw).strip() != "") else None
    policy_enabled = asset.get("policy_enabled")
    policy_enabled_bool = None
    if policy_enabled is not None:
        policy_enabled_bool = parse_bool(policy_enabled, "policy_enabled")
    interval_raw = asset.get("policy_interval_hours")
    interval_hours = number(interval_raw, "policy_interval_hours", minimum=Decimal("0.01"),
                            maximum=Decimal("100000")) \
        if (interval_raw is not None and str(interval_raw).strip() != "") else None
    bound_raw = asset.get("resource_bound")
    resource_bound = None
    if bound_raw is not None:
        resource_bound = parse_bool(bound_raw, "resource_bound")
    keep_raw = asset.get("keep_at_least_one")
    keep_at_least_one = parse_bool(keep_raw, "keep_at_least_one") if keep_raw is not None else False
    cr_req_raw = asset.get("cross_region_required")
    cross_region_required = parse_bool(cr_req_raw, "cross_region_required") if cr_req_raw is not None else False
    cr_obs_raw = asset.get("cross_region_observed")
    cross_region_observed = None
    if cr_obs_raw is not None:
        cross_region_observed = parse_bool(cr_obs_raw, "cross_region_observed")
    imm_req_raw = asset.get("immutable_required")
    immutable_required = parse_bool(imm_req_raw, "immutable_required") if imm_req_raw is not None else False
    imm_obs_raw = asset.get("immutable_observed")
    immutable_observed = None
    if imm_obs_raw is not None:
        immutable_observed = parse_bool(imm_obs_raw, "immutable_observed")
    test_at_raw = asset.get("last_restore_test_at")
    test_at = parse_dt(test_at_raw, "last_restore_test_at", required=False) \
        if (test_at_raw is not None and str(test_at_raw).strip() != "") else None
    test_result_raw = asset.get("last_restore_test_result")
    test_result = None
    if test_result_raw is not None and str(test_result_raw).strip() != "":
        test_result = text(str(test_result_raw), "last_restore_test_result", maximum=20)
    test_evidence_raw = asset.get("last_restore_test_evidence")
    test_evidence = text(str(test_evidence_raw or ""), "last_restore_test_evidence", maximum=200, allow_empty=True) \
        if test_evidence_raw is not None else None
    points_raw = asset.get("restore_points")
    if not isinstance(points_raw, list) or len(points_raw) > 5000:
        raise ValueError("restore_points must be a list of up to 5000 items")
    seen_points = set()
    points = [parse_point(p, seen_points, asset_id, as_of) for p in points_raw]
    return {"asset_id": asset_id, "criticality": criticality, "target_rpo_hours": rpo_hours,
            "target_retention_days": retention_days, "policy_enabled": policy_enabled_bool,
            "policy_interval_hours": interval_hours, "resource_bound": resource_bound,
            "keep_at_least_one": keep_at_least_one,
            "cross_region_required": cross_region_required, "cross_region_observed": cross_region_observed,
            "immutable_required": immutable_required, "immutable_observed": immutable_observed,
            "last_restore_test_at": test_at, "last_restore_test_result": test_result,
            "last_restore_test_evidence": test_evidence, "restore_points": points}


def parse_bool(value, label):
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    raise ValueError(label + " must be a boolean")


def audit_asset(asset, as_of):
    reasons = []
    points = asset["restore_points"]
    issues_all = []
    for p in points:
        issues_all.extend(p["issues"])

    # valid = success status + not expired + not in future
    valid = []
    for p in points:
        if p["completed_at"] > as_of:
            continue  # future point, flagged, not coverage
        if p["status"] not in VALID_POINT_STATUS:
            continue
        if p["expiry_at"] is not None and p["expiry_at"] <= as_of:
            continue  # expired recovery point cannot restore today
        valid.append(p)
    valid_sorted = sorted(valid, key=lambda p: p["completed_at"])

    # -- policy binding --
    policy_ok = True
    policy_reasons = []
    if asset["policy_enabled"] is not True:
        policy_ok = False
        policy_reasons.append("policy_not_enabled（备份策略未启用，或 policy_enabled 未确认为 true）")
    if asset["resource_bound"] is not True:
        policy_ok = False
        policy_reasons.append("resource_not_bound（策略启用但资源未绑定，备份任务可能根本没有执行对象）")
    if not policy_ok:
        reasons.extend(policy_reasons)
        return build_result(asset, "POLICY_NOT_BOUND", as_of, reasons, [], valid_sorted,
                            None, None, None, None, None, issues_all)

    # -- coverage vs RPO --
    rpo = asset["target_rpo_hours"]
    if rpo is None:
        reasons.append("target_rpo_hours_missing（缺目标 RPO，无法判定 RPO 覆盖）")
        status_candidate = "UNKNOWN"
        max_gap_hours = None
        latest_age_hours = None
        continuity = "UNKNOWN"
        rpo_reasons = ["target_rpo_missing"]
    else:
        if not valid_sorted:
            status_candidate = "RPO_GAP"
            latest_age_hours = None
            max_gap_hours = None
            continuity = "NO_POINTS"
            rpo_reasons = ["no_valid_restore_points（无成功且未过期的恢复点，无覆盖）"]
        else:
            latest = valid_sorted[-1]
            latest_age_hours = hours_between(as_of, latest["completed_at"])
            gaps = []
            for prev, cur in zip(valid_sorted, valid_sorted[1:]):
                gaps.append(hours_between(cur["completed_at"], prev["completed_at"]))
            max_gap_hours = max(gaps) if gaps else None
            latest_ok = latest_age_hours <= rpo
            gap_ok = max_gap_hours is None or max_gap_hours <= rpo
            if len(valid_sorted) < 2:
                continuity = "UNKNOWN"
                rpo_reasons = ["insufficient_history（仅 %d 个有效点：当前年龄可判 %s，但连续覆盖无法证实，标 UNKNOWN）"
                               % (len(valid_sorted), "OK" if latest_ok else "GAP")]
                status_candidate = "RPO_GAP" if not latest_ok else "UNKNOWN"
            else:
                continuity = "OK" if gap_ok else "GAP"
                rpo_reasons = []
                if not latest_ok:
                    rpo_reasons.append("latest_point_age_%s（最新有效点年龄 %s h > 目标 RPO %s h）"
                                       % ("gap", quant(latest_age_hours), quant(rpo)))
                if not gap_ok:
                    rpo_reasons.append("max_adjacent_gap_%s（相邻恢复点最大间隔 %s h > 目标 RPO %s h）"
                                       % ("gap", quant(max_gap_hours), quant(rpo)))
                status_candidate = "RPO_GAP" if (not latest_ok or not gap_ok) else None

    # -- retention window --
    ret_days = asset["target_retention_days"]
    retention_ok = True
    retention_reasons = []
    earliest = valid_sorted[0] if valid_sorted else None
    if ret_days is None:
        retention_reasons.append("target_retention_days_missing（缺保留期目标，不核对保留窗口）")
        retention_ok = None
    elif earliest is None:
        retention_reasons.append("retention_uncheckable（无有效点）")
        retention_ok = False
    else:
        retention_span_hours = hours_between(as_of, earliest["completed_at"])
        retention_span_days = retention_span_hours / Decimal("24")
        if retention_span_days < Decimal(ret_days) - Decimal("0.0001"):
            retention_ok = False
            retention_reasons.append("earliest_valid_point_age_%s（最早有效点距今 %s 天 < 目标保留 %s 天；"
                                     "keep_at_least_one 不替代长期保留证据）"
                                     % (quant(retention_span_days), quant(retention_span_days), ret_days))
        else:
            retention_reasons.append("earliest_valid_point_covers（最早有效点覆盖 %s 天保留窗口）"
                                     % quant(retention_span_days))

    # -- restore test (backup success is NOT the same as recoverability) --
    restore_ok = True
    restore_reasons = []
    test = asset["last_restore_test_result"]
    test_at = asset["last_restore_test_at"]
    evidence = asset["last_restore_test_evidence"]
    if test is None:
        restore_ok = False
        restore_reasons.append("restore_test_not_run（未做恢复测试：备份成功不代表可恢复）")
    elif test.lower() in ("success", "passed", "ok", "succeeded"):
        if test_at is None:
            restore_ok = False
            restore_reasons.append("restore_test_date_missing（测试结果为 success 但缺测试日期，证据不完整）")
        elif evidence is None or str(evidence).strip() == "":
            restore_ok = False
            restore_reasons.append("restore_test_evidence_missing（测试结果 success 但缺证据，不可称可恢复）")
        else:
            restore_reasons.append("restore_test_passed_with_evidence（最近恢复测试通过且证据在位）")
    else:
        restore_ok = False
        restore_reasons.append("restore_test_not_passed（最近恢复测试结果=%s：不得称可恢复）" % test)

    # -- compliance: cross-region / immutable separately --
    comp_reasons = []
    if asset["cross_region_required"] and asset["cross_region_observed"] is not True:
        comp_reasons.append("cross_region_not_met（要求跨区但 observed 非 true）")
    if asset["immutable_required"] and asset["immutable_observed"] is not True:
        comp_reasons.append("immutable_not_met（要求不可变但 observed 非 true）")

    # -- final status merge --
    status = None
    if status_candidate in ("RPO_GAP", "UNKNOWN", "NO_POINTS") and status_candidate == "RPO_GAP":
        status = "RPO_GAP"
        reasons.extend(rpo_reasons)
    elif status_candidate == "UNKNOWN":
        status = "UNKNOWN"
        reasons.extend(rpo_reasons)
    elif retention_ok is False:
        status = "RETENTION_GAP"
        reasons.extend(retention_reasons)
    elif not restore_ok:
        status = "RESTORE_UNVERIFIED"
        reasons.extend(restore_reasons)
    elif comp_reasons:
        status = "RESTORE_UNVERIFIED"
        reasons.extend(comp_reasons)
    else:
        status = "PASS"
        if retention_ok is not None:
            reasons.extend(retention_reasons)
        reasons.extend(restore_reasons)

    flags = [f for f in issues_all]
    return build_result(asset, status, as_of, reasons, flags, valid_sorted,
                        latest_age_hours, max_gap_hours, continuity, retention_ok,
                        restore_ok, comp_reasons)


def build_result(asset, status, as_of, reasons, review_flags, valid_sorted,
                 latest_age_hours, max_gap_hours, continuity,
                 retention_ok, restore_ok, comp_reasons):
    return {
        "asset_id": asset["asset_id"], "criticality": asset["criticality"],
        "target_rpo_hours": None if asset["target_rpo_hours"] is None else quant(asset["target_rpo_hours"]),
        "target_retention_days": asset["target_retention_days"],
        "policy_enabled": asset["policy_enabled"], "policy_interval_hours":
            None if asset["policy_interval_hours"] is None else quant(asset["policy_interval_hours"]),
        "resource_bound": asset["resource_bound"], "keep_at_least_one": asset["keep_at_least_one"],
        "status": status,
        "restore_point_total": len(asset["restore_points"]),
        "valid_restore_points": len(valid_sorted),
        "latest_valid_point_age_hours": None if latest_age_hours is None else quant(latest_age_hours),
        "max_adjacent_gap_hours": None if max_gap_hours is None else quant(max_gap_hours),
        "coverage_continuity": continuity,
        "retention_ok": retention_ok, "restore_verified": restore_ok,
        "cross_region_met": asset["cross_region_observed"] if asset["cross_region_required"] else None,
        "immutable_met": asset["immutable_observed"] if asset["immutable_required"] else None,
        "review_flags": review_flags,
        "reasons": reasons,
        "note": "只有成功且未过期的恢复点计入覆盖；备份成功与可恢复是两个不同字段；"
                "本技能不执行备份/恢复/删除或策略修改。"}


def analyze(data):
    if not isinstance(data, dict) or "assets" not in data:
        raise ValueError("input must contain an assets array")
    as_of = parse_dt(data.get("as_of") or data.get("as_of_date") or "", "as_of")
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    assets_raw = data["assets"]
    if not isinstance(assets_raw, list) or not 1 <= len(assets_raw) <= 500:
        raise ValueError("assets must be a list of 1-500 items")
    seen = set()
    results = [audit_asset(parse_asset(a, as_of, seen), as_of) for a in assets_raw]
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    priority = {"critical": 0, "high": 1, "normal": 2, "low": 3, "unknown": 4}
    gap_assets = [r for r in results if r["status"] in ("RPO_GAP", "RETENTION_GAP", "RESTORE_UNVERIFIED",
                                                         "POLICY_NOT_BOUND")]
    gap_assets.sort(key=lambda r: (priority.get(r["criticality"], 4), r["asset_id"]))
    summary = {"as_of": as_of.isoformat(), "asset_count": len(results),
               "status_counts": counts, "assets": results,
               "priority_gaps": [{"asset_id": r["asset_id"], "criticality": r["criticality"],
                                  "status": r["status"]} for r in gap_assets],
               "markdown_summary": build_markdown(results, as_of, gap_assets)}
    return summary


def build_markdown(results, as_of, gap_assets):
    rows = [["asset", "关键级", "RPO(h)", "保留(天)", "有效点", "最新点年龄(h)", "最大间隔(h)", "连续性", "状态"]]
    for r in results:
        rows.append([r["asset_id"], r["criticality"], r["target_rpo_hours"] if r["target_rpo_hours"] else "—",
                     r["target_retention_days"] if r["target_retention_days"] is not None else "—",
                     r["valid_restore_points"],
                     r["latest_valid_point_age_hours"] if r["latest_valid_point_age_hours"] is not None else "—",
                     r["max_adjacent_gap_hours"] if r["max_adjacent_gap_hours"] is not None else "—",
                     r["coverage_continuity"] if r["coverage_continuity"] else "—",
                     r["status"]])
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    head = ["# 备份 RPO 恢复点覆盖审计（基准 %s）\n\n" % as_of.isoformat()]
    head.append("口径：只有**成功且未过期**的恢复点计入覆盖；最新点年龄与相邻点最大间隔对照目标 RPO；"
                "最早有效点覆盖保留窗口；恢复测试单独评价——**备份成功 ≠ 可恢复**。\n\n")
    head.append(md_table(rows))
    head.append("\n\n状态分布：" + ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())) + "。\n")
    if gap_assets:
        head.append("\n按关键级排序的缺口清单：" +
                    ", ".join("%s(%s:%s)" % (g["asset_id"], g["criticality"], g["status"]) for g in gap_assets) + "\n")
    head.append("\n本技能不执行备份、恢复、删除或策略修改；逐资产 evidence 与原因见 assets[].reasons。")
    return "".join(head)


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
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
                          "message": "请对照 references/guide.md 检查带时区基准时间、asset_id/rp_id 唯一、"
                                     "策略与布尔字段、恢复点状态/过期语义、NaN 与时间合法性。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
