#!/usr/bin/env python3
"""Offline platform dispute deadline guard. Standard library only; builds a
neutral factual summary, never submits disputes, never predicts outcomes."""
import argparse
import json
import re
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

TWO = Decimal("0.01")
ZERO = Decimal("0")
CRITICAL_DAYS = 3
RISK_DAYS = 7

PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
IDCARD = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
BANKCARD = re.compile(r"(?<!\d)(\d{16,19})(?!\d)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


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


def integer(value, label, minimum=ZERO, maximum=Decimal("3650")):
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


def iso(value, label, required=True):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        if required:
            raise ValueError(label + " is required")
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise ValueError(label + " must be ISO YYYY-MM-DD")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(label + " is not a valid calendar date") from None


def money(value):
    return str(value.quantize(TWO, rounding=ROUND_HALF_UP))


def mask_pii(text_value):
    """Mask likely mobile / ID / bank-card / email candidates in free text.
    Returns (masked, hit_count). Never opens or executes anything."""
    if not isinstance(text_value, str):
        return text_value, 0
    masked = text_value
    count = 0
    for pattern, repl in (
            (EMAIL, lambda m: re.sub(r"(?<=.)(?=.)", "*", m.group(0).split("@")[0][1:-1]) and _mask_email(m.group(0))),
            (PHONE, lambda m: m.group(0)[:3] + "****" + m.group(0)[7:]),
            (IDCARD, lambda m: m.group(0)[:6] + "********" + m.group(0)[-4:]),
            (BANKCARD, lambda m: m.group(0)[:4] + "**********" + m.group(0)[-4:])):
        new_text, hits = pattern.subn(repl, masked)
        masked = new_text
        count += hits
    return masked, count


def _mask_email(email_value):
    local, _, domain = email_value.partition("@")
    if len(local) <= 2:
        local_masked = local[0] + "*"
    else:
        local_masked = local[0] + "*" * (len(local) - 2) + local[-1]
    return local_masked + "@" + domain


def guard_case(case, as_of, seen_ids):
    case_id = text(case.get("case_id"), "case_id", maximum=64)
    platform_raw = case.get("platform")
    platform = text(platform_raw, "platform", maximum=120) if platform_raw is not None else "未命名平台"
    case_type_raw = case.get("case_type")
    case_type = text(case_type_raw, "case_type", maximum=120) if case_type_raw is not None else "未分类争议"
    duplicate = case_id in seen_ids
    seen_ids.add(case_id)

    notice = iso(case.get("notice_date"), "notice_date", required=False)
    explicit = iso(case.get("explicit_deadline"), "explicit_deadline", required=False)
    days_raw = case.get("deadline_days")
    days = None
    if days_raw is not None and str(days_raw).strip() != "":
        days = integer(days_raw, "deadline_days", minimum=Decimal("1"))
    if explicit is None and days is None:
        days = None  # no deadline rule at all
    amount_raw = case.get("amount_at_risk")
    amount = number(amount_raw, "amount_at_risk") if (amount_raw is not None and str(amount_raw).strip() != "") else None

    required_raw = case.get("required_evidence")
    available_raw = case.get("available_evidence")
    required = []
    available = []
    for items, target, label in ((required_raw, required, "required_evidence"),
                                 (available_raw, available, "available_evidence")):
        if items is None:
            continue
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError(label + " must be a list of up to 100 evidence type ids")
        for item in items:
            target.append(text(str(item), label, maximum=64))
    missing_evidence = sorted(set(required) - set(available))

    timeline_raw = case.get("event_timeline")
    timeline = []
    if timeline_raw is not None:
        if not isinstance(timeline_raw, list) or len(timeline_raw) > 200:
            raise ValueError("event_timeline must be a list of up to 200 items")
        for item in timeline_raw:
            if not isinstance(item, dict):
                raise ValueError("each timeline item must be an object")
            timeline.append({"date": iso(item.get("date"), "event_timeline[].date").isoformat(),
                             "note": str(item.get("note") or "")})
        timeline.sort(key=lambda entry: entry["date"])

    pii_hits = []
    fields = [("platform", platform), ("case_type", case_type)]
    fields += [("timeline_note", entry["note"]) for entry in timeline]
    for field_name, raw_value in fields:
        cleaned, hits = mask_pii(raw_value)
        if hits:
            if field_name == "platform":
                platform = cleaned
            elif field_name == "case_type":
                case_type = cleaned
            else:
                for entry in timeline:
                    if entry["note"] == raw_value:
                        entry["note"] = cleaned
            pii_hits.append({"case_id": case_id, "field": field_name, "masked_count": hits})

    deadline = None
    deadline_source = None
    if explicit is not None:
        deadline = explicit
        deadline_source = "explicit"
    elif days is not None and notice is not None:
        deadline = notice + timedelta(days=days)
        deadline_source = "relative"

    remaining = None
    status = None
    urgency = None
    reasons = []
    if duplicate:
        status = "UNKNOWN"
        urgency = "duplicate"
        reasons.append("duplicate_case_id（同一 case_id 出现多次，需人工去重后复核）")
    elif explicit is None and days is None:
        status = "UNKNOWN"
        urgency = "unknown_deadline"
        reasons.append("no_deadline_rule（无明确截止日也无相对天数，未套用任何平台默认期限）")
    elif explicit is None and days is not None and notice is None:
        status = "UNKNOWN"
        urgency = "unknown_deadline"
        reasons.append("notice_date_missing（有相对天数但缺通知日锚点，无法推算截止日）")
    elif deadline is None:
        status = "UNKNOWN"
        urgency = "unknown_deadline"
        reasons.append("deadline_unresolved（截止信息不完整，无法推算）")
    else:
        remaining = (deadline - as_of).days
        if remaining < 0:
            status = "EXPIRED"
            urgency = "expired"
            reasons.append("deadline_expired（截止日 %s 已过 %d 天）" % (deadline.isoformat(), -remaining))
        elif remaining == 0:
            status = "DEADLINE_RISK"
            urgency = "due_today"
            reasons.append("deadline_due_today（今天即截止）")
        elif remaining <= CRITICAL_DAYS:
            status = "DEADLINE_RISK"
            urgency = "critical"
            reasons.append("deadline_critical（剩余 %d 天）" % remaining)
        elif remaining <= RISK_DAYS:
            status = "DEADLINE_RISK"
            urgency = "high"
            reasons.append("deadline_high_risk（剩余 %d 天）" % remaining)
        elif missing_evidence:
            status = "EVIDENCE_MISSING"
            urgency = "normal"
            reasons.append("evidence_missing（缺 %d 类证据：%s）" % (len(missing_evidence), ", ".join(missing_evidence)))
        else:
            status = "READY_FOR_HUMAN_REVIEW"
            urgency = "normal"
            reasons.append("ready_for_human_review（人工核对后按平台入口提交）")
    if explicit is not None and days is not None:
        reasons.append("explicit_deadline_overrides_deadline_days（明确截止日优先于相对天数）")

    return {
        "case_id": case_id, "platform": platform, "case_type": case_type,
        "notice_date": None if notice is None else notice.isoformat(),
        "explicit_deadline": None if explicit is None else explicit.isoformat(),
        "deadline_days": days,
        "deadline_source": deadline_source,
        "deadline": None if deadline is None else deadline.isoformat(),
        "remaining_days": remaining,
        "amount_at_risk": None if amount is None else money(amount),
        "currency": None,
        "required_evidence": required,
        "missing_evidence": missing_evidence,
        "event_timeline": timeline,
        "pii_masked_fields": pii_hits,
        "status": status, "urgency": urgency, "reasons": reasons,
        "note": "中性事实摘要：本技能不代理提交申诉、不预测胜诉结果、不构成法律意见；截止日按日历日计算。"}


def analyze(data):
    if not isinstance(data, dict) or "cases" not in data:
        raise ValueError("input must contain a cases array")
    as_of = iso(data.get("as_of_date") or "", "as_of_date")
    currency = text(str(data.get("currency") or "CNY"), "currency", maximum=6)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    timezone = None
    if data.get("timezone") is not None and str(data.get("timezone")).strip() != "":
        timezone = text(str(data.get("timezone")), "timezone", maximum=64)
    cases = data["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 500:
        raise ValueError("cases must be a list of 1-500 items")
    seen = set()
    results = [guard_case(item, as_of, seen) for item in cases]
    for r in results:
        r["currency"] = currency
    dupes = sorted({c["case_id"] for c in results
                    if sum(1 for other in results if other["case_id"] == c["case_id"]) > 1})
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total_risk = sum((Decimal(r["amount_at_risk"]) for r in results if r["amount_at_risk"] is not None), ZERO)

    summary = {
        "as_of_date": as_of.isoformat(), "currency": currency, "timezone": timezone,
        "case_count": len(results), "duplicate_case_ids": dupes,
        "total_amount_at_risk_known": money(total_risk) if results else None,
        "status_counts": counts,
        "cross_timezone_note": "截止日按日历日计算；跨时区场景请以平台本地日历日为准。"
        if timezone else "未提供时区；如需跨时区核对请补充 timezone。",
        "cases": results}

    rows = [["case", "platform", "deadline", "剩余天数", "风险金额", "缺证据", "状态"]]
    for r in results:
        rows.append([r["case_id"], r["platform"],
                     r["deadline"] or "未知", r["remaining_days"] if r["remaining_days"] is not None else "-",
                     r["amount_at_risk"] if r["amount_at_risk"] is not None else "-",
                     str(len(r["missing_evidence"])), r["status"]])
    summary["markdown_summary"] = (
        "# 平台争议申诉时限守门（" + as_of.isoformat() + "）\n\n"
        + md_table(rows) +
        "\n\n状态口径：READY_FOR_HUMAN_REVIEW=证据齐且期限充裕；EVIDENCE_MISSING=期限充裕但缺证据；"
        "DEADLINE_RISK=已过期/当天截止/剩余 ≤7 天；EXPIRED=已过截止日；UNKNOWN=无截止规则或 case 重复（不套用平台默认期限）。"
        "本技能只生成中性事实摘要：不代理提交、不预测胜诉、不构成法律意见；自由文本中的手机号/身份证/银行卡候选值已脱敏，请人工复核。"
        + summary["cross_timezone_note"])
    return summary


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
                          "message": "请对照 references/guide.md 检查 case_id/日期/金额/证据类型标识字段。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
