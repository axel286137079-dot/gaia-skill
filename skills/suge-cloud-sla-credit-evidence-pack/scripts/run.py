#!/usr/bin/env python3
"""Offline cloud SLA credit evidence pack builder. Standard library only;
never files a claim, never contacts the provider."""
import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

TWO = Decimal("0.01")
ZERO = Decimal("0")
PERCENT4 = Decimal("0.0001")


def number(value, label, minimum=ZERO, maximum=Decimal("1000000000000")):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
    return result


def integer(value, label, minimum=ZERO, maximum=Decimal("1000000000")):
    result = number(value, label, minimum, maximum)
    if result != result.to_integral_value():
        raise ValueError(label + " must be an integer")
    return int(result)


def text(value, label, maximum=200, allow_empty=False):
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


def iso_date(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise ValueError(label + " must be ISO YYYY-MM-DD")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(label + " is not a valid calendar date") from None


def money(value):
    return str(value.quantize(TWO, rounding=ROUND_HALF_UP))


ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?$")


def parse_dt(value, label, tz):
    if not isinstance(value, str) or not ISO_DT.match(value.strip()):
        raise ValueError(label + " must be ISO8601 datetime like 2026-08-05T09:10:00+08:00")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    has_offset = bool(re.search(r"[+-]\d{2}:?\d{2}$", raw))
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(label + " is not a valid datetime") from None
    if not has_offset:
        if tz is None:
            raise ValueError(label + " is naive but no timezone is configured")
        dt = dt.replace(tzinfo=tz)  # localize naive timestamp in configured timezone
    return dt.astimezone(timezone.utc)


def resolve_timezone(value):
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    if re.fullmatch(r"[+-]\d{2}:?\d{2}", raw):
        normalized = raw.replace(":", "")
        sign = -1 if normalized[0] == "-" else 1
        hours = int(normalized[1:3])
        minutes = int(normalized[3:5]) if len(normalized) > 3 else 0
        return timezone(sign * timedelta(hours=hours, minutes=minutes))
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(raw)
    except Exception:
        raise ValueError("timezone must be an IANA name or +/-HH:MM offset") from None


def cycle_bounds_utc(billing_cycle, tz):
    year, month = billing_cycle.split("-")
    start_local = datetime(int(year), int(month), 1, tzinfo=tz)
    if month == "12":
        end_local = datetime(int(year) + 1, 1, 1, tzinfo=tz)
    else:
        end_local = datetime(int(year), int(month) + 1, 1, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def tier_match(availability_num, tiers):
    if not tiers:
        return None
    best = None
    for tier in tiers:
        minimum = number(tier.get("min_availability"), "min_availability", maximum=Decimal("100"))
        credit = number(tier.get("credit_percent"), "credit_percent", maximum=Decimal("100"))
        if availability_num < minimum:
            if best is None or credit > best["credit_percent"]:
                best = {"min_availability": minimum, "credit_percent": credit}
    return best


def audit_cycle(data):
    if not isinstance(data, dict) or "incidents" not in data:
        raise ValueError("input must contain an incidents array")
    billing = text(str(data.get("billing_cycle") or ""), "billing_cycle", maximum=7)
    if not re.fullmatch(r"\d{4}-\d{2}", billing):
        raise ValueError("billing_cycle must be YYYY-MM")
    provider = text(data.get("provider") or "未知服务商", "provider", maximum=80)
    service = text(data.get("service") or "未知服务", "service", maximum=80)
    currency = text(str(data.get("currency") or "CNY"), "currency", maximum=6)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    total_minutes = integer(data.get("total_minutes"), "total_minutes", minimum=Decimal("1"), maximum=Decimal("100000000"))
    incidents = data["incidents"]
    if not isinstance(incidents, list) or len(incidents) > 500:
        raise ValueError("incidents must be a list of up to 500 items")

    fee_raw = data.get("eligible_fee")
    fee_known = fee_raw is not None and str(fee_raw).strip() != ""
    eligible_fee = number(fee_raw, "eligible_fee") if fee_known else None

    tz = resolve_timezone(data.get("timezone"))
    tz_usable = tz is not None

    tiers_raw = data.get("credit_tiers")
    tiers = None
    if tiers_raw is not None:
        if not isinstance(tiers_raw, list) or not 1 <= len(tiers_raw) <= 50:
            raise ValueError("credit_tiers must be a list of 1-50 items")
        tiers = tiers_raw

    as_of = None
    claim_deadline = None
    if data.get("as_of_date") is not None and str(data.get("as_of_date")).strip() != "":
        as_of = iso_date(data.get("as_of_date"), "as_of_date")
    if data.get("claim_deadline") is not None and str(data.get("claim_deadline")).strip() != "":
        claim_deadline = iso_date(data.get("claim_deadline"), "claim_deadline")

    if tz_usable:
        cycle_start_utc, cycle_end_utc = cycle_bounds_utc(billing, tz)
    else:
        cycle_start_utc = cycle_end_utc = None

    counted = []          # clipped intervals (utc) for non-excluded incidents
    excluded_records = []
    evidence_gaps = []
    outside_cycle = []
    for idx, inc in enumerate(incidents):
        if not isinstance(inc, dict):
            raise ValueError("each incident must be an object")
        if inc.get("excluded") is not None and not isinstance(inc.get("excluded"), bool):
            raise ValueError("excluded must be boolean")
        excluded = bool(inc.get("excluded", False))
        start = parse_dt(inc.get("start"), "incidents[%d].start" % idx, tz) if tz_usable \
            else None
        end = parse_dt(inc.get("end"), "incidents[%d].end" % idx, tz) if tz_usable \
            else None
        if tz_usable and end < start:
            raise ValueError("incidents[%d].end is earlier than start" % idx)
        error_type = text(inc.get("error_type"), "incidents[%d].error_type" % idx, maximum=120, allow_empty=True)
        exclusion_reason = text(inc.get("exclusion_reason"), "exclusion_reason", maximum=120, allow_empty=True) \
            if inc.get("exclusion_reason") is not None else ""
        resource_id = inc.get("resource_id")
        log_evidence = inc.get("log_evidence")

        if excluded:
            excluded_records.append({
                "index": idx, "error_type": error_type,
                "exclusion_reason": exclusion_reason or "excluded_flag",
                "start": inc.get("start"), "end": inc.get("end"),
                "minutes": "不计入"})
            continue
        if tz_usable:
            clipped_start = max(start, cycle_start_utc)
            clipped_end = min(end, cycle_end_utc)
            if clipped_end > clipped_start:
                minutes = int((clipped_end - clipped_start).total_seconds() // 60)
                counted.append({"start": clipped_start, "end": clipped_end,
                                "minutes": minutes, "index": idx})
            else:
                outside_cycle.append({
                    "index": idx, "error_type": error_type,
                    "start": inc.get("start"), "end": inc.get("end"),
                    "note": "该故障不在本计费周期内，未计入"})
        if not log_evidence or str(log_evidence).strip() == "" or not resource_id or str(resource_id).strip() == "":
            evidence_gaps.append({
                "index": idx,
                "missing": [item for item, present in
                            (("log_evidence", log_evidence), ("resource_id", resource_id)) if not present or str(present).strip() == ""],
                "error_type": error_type})

    # Merge overlapping counted intervals (union of unique downtime minutes).
    merged = []
    if tz_usable:
        counted.sort(key=lambda item: item["start"])
        for item in counted:
            if item["minutes"] == 0:
                continue
            if merged and item["start"] < merged[-1]["end"]:
                merged[-1]["end"] = max(merged[-1]["end"], item["end"])
            else:
                merged.append({"start": item["start"], "end": item["end"], "minutes": item["minutes"]})
        downtime = 0
        for seg in merged:
            seg["minutes"] = int((seg["end"] - seg["start"]).total_seconds() // 60)
            downtime += seg["minutes"]
        merged_segments = len(merged)
        availability_num = (Decimal(total_minutes) - Decimal(downtime)) / Decimal(total_minutes) * Decimal(100)
        availability_display = str(availability_num.quantize(PERCENT4, rounding=ROUND_HALF_UP)) + "%"
    else:
        downtime = None
        merged_segments = None
        availability_num = None
        availability_display = None

    matched_tier = tier_match(availability_num, tiers) if (availability_num is not None and tiers) else None
    if matched_tier is not None:
        matched_tier = {"min_availability": str(matched_tier["min_availability"]),
                        "credit_percent": str(matched_tier["credit_percent"])}
    estimated_credit = None
    if matched_tier is not None and eligible_fee is not None:
        credit = Decimal(matched_tier["credit_percent"])
        estimated_credit = money((eligible_fee * credit / Decimal(100)).quantize(TWO, rounding=ROUND_HALF_UP))

    reasons = []
    deadline_state = "ok"
    if claim_deadline is not None and as_of is not None:
        days = (claim_deadline - as_of).days
        if days < 0:
            deadline_state = "expired"
            reasons.append("claim_deadline_expired（申请期限已过 %d 天）" % (-days))
        elif days <= 7:
            deadline_state = "within_7_days"
            reasons.append("claim_deadline_within_7_days（剩余 %d 天）" % days)
    if claim_deadline is None:
        reasons.append("claim_deadline_missing（无申请期限，无法提示截止风险）")

    if not tz_usable:
        reasons.append("timezone_missing_or_invalid（无时区，无法裁切跨月故障与核算可用性，未估算赔付）")
    if tiers is None:
        reasons.append("credit_tiers_missing（无 SLA 档位，未估算赔付比例）")
    if not fee_known:
        reasons.append("eligible_fee_missing（无该周期可申请费用，未估算赔付金额）")
    for gap in evidence_gaps:
        reasons.append("evidence_gap（incidents[%d] 缺 %s）" % (gap["index"], "/".join(gap["missing"])))
    if not incidents:
        reasons.append("no_incidents（无故障记录，不构成补偿申请依据）")

    status = "ESTIMATE_READY"
    if deadline_state in ("expired", "within_7_days"):
        status = "DEADLINE_RISK"
    elif not tz_usable or tiers is None or not fee_known or evidence_gaps:
        status = "EVIDENCE_MISSING"
    elif excluded_records:
        status = "REVIEW_EXCLUSIONS"
    elif not incidents:
        status = "UNKNOWN"

    merged_output = []
    if merged:
        for i, seg in enumerate(merged):
            merged_output.append({
                "segment": i + 1,
                "start_utc": seg["start"].isoformat(), "end_utc": seg["end"].isoformat(),
                "minutes": seg["minutes"]})

    disclaimer = "本结果为估算，以购买时生效的 SLA 条款与厂商最终审核为准；本技能不代提交补偿申请、不代联系客服。"
    result = {
        "billing_cycle": billing, "provider": provider, "service": service,
        "currency": currency, "eligible_fee": None if eligible_fee is None else money(eligible_fee),
        "total_minutes": total_minutes, "timezone": str(data.get("timezone") or "unknown"),
        "as_of_date": None if as_of is None else as_of.isoformat(),
        "claim_deadline": None if claim_deadline is None else claim_deadline.isoformat(),
        "candidate_downtime_minutes": downtime,
        "merged_segments": merged_segments,
        "availability_pct": availability_display,
        "matched_tier": matched_tier,
        "estimated_credit": None if estimated_credit is None else estimated_credit,
        "excluded_count": len(excluded_records),
        "excluded_records": excluded_records,
        "outside_cycle": outside_cycle,
        "evidence_gaps": evidence_gaps,
        "merged_output": merged_output,
        "status": status, "reasons": reasons,
        "disclaimer": disclaimer}
    # Markdown summary, directly renderable.
    rows = [["项目", "值"]]
    rows.append(["计费周期", billing + "（" + provider + " / " + service + "）"])
    rows.append(["候选不可用分钟（去重合并后）", str(downtime) if downtime is not None else "未知"])
    rows.append(["可用性", availability_display if availability_display else "未知"])
    rows.append(["匹配档位", ("<" + str(matched_tier["min_availability"]) + "% → 补偿 " + str(matched_tier["credit_percent"]) + "%")
                 if matched_tier else "无/未估算"])
    rows.append(["估算补偿金额", (estimated_credit + " " + currency) if estimated_credit else "未估算"])
    rows.append(["排除记录数", str(len(excluded_records))])
    rows.append(["证据缺口数", str(len(evidence_gaps))])
    rows.append(["状态", status])
    result["markdown_summary"] = (
        "# 云服务 SLA 补偿证据包（" + billing + "）\n\n"
        + md_table(rows) +
        "\n\n计算口径：候选不可用分钟 = 合并重叠故障后、裁切到本计费周期内的分钟总数；可用性 = (周期总分钟 − 候选不可用分钟) / 周期总分钟；"
        "已标记除外（计划维护/客户配置等）事件不自动计入。\n\n" + disclaimer)
    return result


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
        print(json.dumps(audit_cycle(data), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, InvalidOperation):
        print(json.dumps({"error": "invalid_input",
                          "message": "请对照 references/guide.md 检查计费周期、时区、时间戳（结束须晚于开始）、档位与金额字段。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
