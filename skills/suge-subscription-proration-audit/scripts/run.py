#!/usr/bin/env python3
"""Offline subscription proration audit (same-cycle linear). Standard library
only; never refunds, never issues credit, never changes invoices."""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

TWO = Decimal("0.01")
ZERO = Decimal("0")
ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?$")


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


def flag(value, label):
    if not isinstance(value, bool):
        raise ValueError(label + " must be boolean")
    return value


def money(value):
    return str(value.quantize(TWO, rounding=ROUND_HALF_UP))


def parse_dt(value, label):
    if not isinstance(value, str) or not ISO_DT.match(value.strip()):
        raise ValueError(label + " must be ISO8601 datetime with offset, e.g. 2026-09-16T00:00:00+08:00")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    if not re.search(r"[+-]\d{2}:?\d{2}$", raw):
        raise ValueError(label + " must include a UTC offset")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(label + " is not a valid datetime") from None


def audit_proration(sub):
    sub_id = text(sub.get("subscription_id"), "subscription_id", maximum=64)
    mode = sub.get("mode")
    if mode is None or str(mode).strip() == "":
        mode = "linear_same_cycle"
    else:
        mode = text(str(mode), "mode", maximum=40)
    old_amount = number(sub.get("old_period_amount"), "old_period_amount")
    new_amount = number(sub.get("new_period_amount"), "new_period_amount")
    basis = sub.get("billing_basis")
    if basis is None or str(basis).strip() == "":
        basis = "seconds"
    else:
        basis = text(str(basis), "billing_basis", maximum=20)
    if basis not in ("seconds", "actual_days"):
        raise ValueError("billing_basis must be seconds or actual_days")
    policy = sub.get("policy_source")
    policy_text = text(str(policy or ""), "policy_source", maximum=200, allow_empty=True)

    start = parse_dt(sub.get("cycle_start"), "cycle_start")
    end = parse_dt(sub.get("cycle_end"), "cycle_end")
    change = parse_dt(sub.get("change_at"), "change_at")
    if end <= start:
        raise ValueError("cycle_end must be after cycle_start")
    paid_raw = sub.get("old_invoice_paid")
    paid = flag(paid_raw, "old_invoice_paid") if paid_raw is not None else None
    invoiced_raw = sub.get("invoiced_adjustment")
    invoiced = number(invoiced_raw, "invoiced_adjustment") \
        if (invoiced_raw is not None and str(invoiced_raw).strip() != "") else None

    reasons = []
    status = "REVIEW"
    # -- blockers (in order) --
    if mode != "linear_same_cycle":
        reasons.append("mode_unsupported（仅支持 mode=linear_same_cycle，当前=" + str(mode) + "）")
    if policy_text == "":
        reasons.append("policy_source_missing（无折算规则来源，不判确定差异）")
    if paid is not True:
        reasons.append("old_invoice_unpaid_or_unknown（旧账单未确认已付，不自动退款/不判确定抵扣）")
    if change < start or change > end:
        reasons.append("change_outside_cycle（变更时间超出计费周期）")

    fraction = None
    remaining_label = None
    if not reasons:
        if basis == "seconds":
            total = (end - start).total_seconds()
            remain = (end - change).total_seconds()
            if total <= 0 or remain < 0:
                reasons.append("cycle_seconds_invalid")
            else:
                fraction = Decimal(str(remain)) / Decimal(str(total))
                remaining_label = "%.6f 秒占比" % remain
        else:
            # actual_days: same fixed UTC offset + local midnight boundaries
            offsets = {start.utcoffset(), end.utcoffset(), change.utcoffset()}
            local_midnight = all((t.hour, t.minute, t.second) == (0, 0, 0)
                                 for t in (start, end, change))
            if len(offsets) != 1:
                reasons.append("complex_timezone（actual_days 仅接受同一固定 UTC 偏移）")
            elif not local_midnight:
                reasons.append("non_midnight_boundary（actual_days 要求周期与变更均为当地午夜 00:00）")
            else:
                total_days = (end.date() - start.date()).days
                remain_days = (end.date() - change.date()).days
                if total_days <= 0 or remain_days < 0:
                    reasons.append("cycle_days_invalid")
                else:
                    fraction = Decimal(remain_days) / Decimal(total_days)
                    remaining_label = "%d/%d 天" % (remain_days, total_days)

    charge = None
    credit = None
    net = None
    charge_raw = None
    credit_raw = None
    net_raw = None
    if not reasons and fraction is not None:
        charge_raw = new_amount * fraction
        credit_raw = old_amount * fraction
        charge = charge_raw.quantize(TWO, rounding=ROUND_HALF_UP)
        credit = credit_raw.quantize(TWO, rounding=ROUND_HALF_UP)
        net = charge - credit
        net_raw = charge_raw - credit_raw
        if net > ZERO:
            status = "ADDITIONAL_CHARGE"
        elif net < ZERO:
            status = "CREDIT_CANDIDATE"
        else:
            status = "NO_CHANGE"

    invoice_match = None
    invoice_diff = None
    if net is not None and invoiced is not None:
        invoice_diff = (invoiced - net).quantize(TWO, rounding=ROUND_HALF_UP)
        invoice_match = invoice_diff == ZERO

    return {
        "subscription_id": sub_id,
        "mode": mode, "billing_basis": basis,
        "cycle_start": start.isoformat(), "cycle_end": end.isoformat(),
        "change_at": change.isoformat(),
        "old_period_amount": money(old_amount), "new_period_amount": money(new_amount),
        "remaining_fraction": None if fraction is None else str(fraction),
        "remaining_detail": remaining_label,
        "proration_charge_estimate": None if charge is None else money(charge),
        "proration_credit_estimate": None if credit is None else money(credit),
        "net_adjustment": None if net is None else money(net),
        "rounding_breakdown": {
            "charge_raw": None if charge_raw is None else str(charge_raw),
            "charge_rounded": None if charge is None else money(charge),
            "credit_raw": None if credit_raw is None else str(credit_raw),
            "credit_rounded": None if credit is None else money(credit),
            "net_raw": None if net_raw is None else str(net_raw),
            "net_rounded": None if net is None else money(net),
            "rule": "逐项 ROUND_HALF_UP 到分后再求净额"},
        "old_invoice_paid": paid,
        "invoiced_adjustment": None if invoiced is None else money(invoiced),
        "invoice_match": invoice_match,
        "invoice_diff": None if invoice_diff is None else money(invoice_diff),
        "policy_source": policy_text,
        "status": status, "reasons": reasons,
        "note": "仅支持同周期线性折算（mode=linear_same_cycle），为选定假设，不是通用账单引擎。"
                "CREDIT_CANDIDATE 为候选抵扣、非已退款；REVIEW 需人工确认；本技能不发起退款/不生成账单。"}


def analyze(data):
    if not isinstance(data, dict) or "subscriptions" not in data:
        raise ValueError("input must contain a subscriptions array")
    currency = text(str(data.get("currency") or "CNY"), "currency", maximum=6)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    subs = data["subscriptions"]
    if not isinstance(subs, list) or not 1 <= len(subs) <= 500:
        raise ValueError("subscriptions must be a list of 1-500 items")
    seen = set()
    results = []
    for item in subs:
        sid = item.get("subscription_id")
        if sid is None or str(sid).strip() in seen:
            raise ValueError("duplicate or missing subscription_id")
        seen.add(str(sid).strip())
        results.append(audit_proration(item))

    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = {
        "currency": currency, "subscription_count": len(results),
        "status_counts": counts, "subscriptions": results}

    rows = [["subscription", "剩余占比", "新增(charge)", "抵扣(credit)", "净额", "状态"]]
    for r in results:
        rows.append([r["subscription_id"],
                     (r["remaining_detail"] or "—"),
                     r["proration_charge_estimate"] if r["proration_charge_estimate"] is not None else "—",
                     r["proration_credit_estimate"] if r["proration_credit_estimate"] is not None else "—",
                     r["net_adjustment"] if r["net_adjustment"] is not None else "—",
                     r["status"]])
    summary["markdown_summary"] = (
        "# 订阅变更折算核对（" + currency + "）\n\n"
        "仅支持同周期线性折算：新增(charge)=新周期价×剩余占比；抵扣(credit)=旧周期价×剩余占比；净额=新增−抵扣。"
        "金额逐项 ROUND_HALF_UP 到分后求净额，舍入点见各条 rounding_breakdown。降配净额为负时标 CREDIT_CANDIDATE（候选抵扣，非已退款）。\n\n"
        + md_table(rows) +
        "\n\n状态口径：ADDITIONAL_CHARGE=应补差额；CREDIT_CANDIDATE=候选抵扣；NO_CHANGE=无净变化；"
        "REVIEW=模式不支持/跨周期/未付旧账单/缺规则来源/复杂时区（交人工，不自动退款）。折算公式为用户选定假设，非通用账单引擎。")
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
                          "message": "请对照 references/guide.md 检查订阅 ID 唯一、带时区时间戳、非负金额、币种一致与必填布尔字段。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
