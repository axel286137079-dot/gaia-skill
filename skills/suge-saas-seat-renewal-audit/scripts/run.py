#!/usr/bin/env python3
"""Offline SaaS seat & renewal audit. Standard library only; never cancels or downgrades."""
import argparse
import json
import re
import sys
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

TWO = Decimal("0.01")
CYCLES = {"monthly", "yearly"}


def number(value, label, minimum=Decimal("0"), maximum=Decimal("1000000000000"), integer=False):
    if isinstance(value, bool) or value is None:
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
    if integer and result != result.to_integral_value():
        raise ValueError(label + " must be an integer")
    return result


def text(value, label, maximum=120):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(label + " must be nonempty text within length limit")
    return value.strip()


def flag(value, label):
    if not isinstance(value, bool):
        raise ValueError(label + " must be boolean")
    return value


def iso(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise ValueError(label + " must be ISO YYYY-MM-DD")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(label + " is not a valid calendar date") from None


def money(value):
    return str(value.quantize(TWO, rounding=ROUND_HALF_UP))


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def audit_subscription(sub, as_of):
    vendor = text(sub.get("vendor"), "vendor") if sub.get("vendor") is not None else "未命名服务"
    plan = sub.get("plan")
    plan = text(plan, "plan") if plan is not None else ""
    seats = number(sub.get("seats_purchased"), "seats_purchased", maximum=Decimal("1000000"), integer=True)
    seats = int(seats)
    unit = number(sub.get("unit_price"), "unit_price", maximum=Decimal("10000000"))
    cycle = sub.get("billing_cycle")
    cycle = text(cycle, "billing_cycle", maximum=20)
    if cycle not in CYCLES:
        raise ValueError("billing_cycle must be monthly or yearly")
    renewal = iso(sub.get("renewal_date"), "renewal_date")
    auto = flag(sub.get("auto_renew"), "auto_renew")
    minimum_seats = number(sub.get("minimum_seats"), "minimum_seats", maximum=Decimal("1000000"), integer=True)
    minimum_seats = int(minimum_seats)
    notice_days = sub.get("cancellation_notice_days")
    notice = int(number(notice_days, "cancellation_notice_days", maximum=Decimal("3650"), integer=True)) \
        if notice_days is not None and str(notice_days).strip() != "" else None
    active = sub.get("active_users_30d")
    active_val = None
    active_known = active is not None and str(active).strip() != ""
    if active_known:
        active_val = int(number(active, "active_users_30d", maximum=Decimal("1000000"), integer=True))

    day = (renewal - as_of).days
    expired = renewal < as_of
    idle = None
    candidate = None
    utilization_pct = None
    if active_known:
        idle = seats - active_val
        if seats > 0:
            utilization_pct = str((Decimal(active_val) / Decimal(seats) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) + "%"
        candidate = max(0, min(idle, seats - max(minimum_seats, 0)))
    cost_monthly = unit * Decimal(seats)
    cost_annualized = cost_monthly * 12 if cycle == "monthly" else unit * Decimal(seats)
    savings_annual = None
    if candidate:
        savings_annual = Decimal(candidate) * unit * (12 if cycle == "monthly" else 1)

    reasons = []
    action = "KEEP"
    if expired:
        action = "REVIEW"
        reasons.append("renewal_date_past（" + renewal.isoformat() + " 早于基准日）")
    elif not active_known:
        action = "UNKNOWN"
        reasons.append("active_users_30d_missing（活跃人数未知，未将全部席位判为闲置）")
    elif active_val > seats:
        action = "REVIEW"
        reasons.append("active_gt_purchased（活跃人数大于已购席位，数据可疑）")
    elif candidate and candidate > 0:
        action = "REDUCE_CANDIDATE"
        reasons.append("idle_seats=%d，最低承诺后可削减候选=%d" % (idle, candidate))
    if action == "KEEP" and auto and notice is not None and not expired and (renewal - as_of).days <= notice:
        action = "REVIEW"
        reasons.append("auto_renew_within_notice（自动续费已进入不可无痛取消窗口）")
    last_cancel_date = None
    if notice is not None:
        last_cancel_date = (renewal - timedelta(days=notice)).isoformat()
    if last_cancel_date and last_cancel_date < as_of.isoformat() and auto and not expired:
        if action == "KEEP":
            action = "REVIEW"
        reasons.append("cancel_window_passed（取消通知最后日期已过，自动续费将扣款）")

    days_label = "expired" if expired else ("due<=30" if day <= 30 else "due<=60" if day <= 60 else "due<=90" if day <= 90 else "ok")
    return {
        "vendor": vendor, "plan": plan, "seats_purchased": seats,
        "active_users_30d": None if not active_known else active_val,
        "idle_seats": None if idle is None else int(idle),
        "utilization_pct": utilization_pct,
        "unit_price": money(unit), "billing_cycle": cycle,
        "cost_monthly": money(cost_monthly), "cost_annualized": money(cost_annualized),
        "renewal_date": renewal.isoformat(), "days_to_renewal": day, "renewal_window": days_label,
        "auto_renew": auto, "cancellation_notice_days": notice,
        "last_cancel_date": last_cancel_date, "minimum_seats": minimum_seats,
        "seat_reduction_candidate": None if candidate is None else int(candidate),
        "potential_annual_savings_estimate": None if savings_annual is None else money(savings_annual),
        "action": action, "reasons": reasons,
        "note": "估算基于下次续费即降配的假设，非保证节省；本技能不执行取消或降配。"}


def analyze(data):
    if not isinstance(data, dict) or "subscriptions" not in data:
        raise ValueError("input must contain a subscriptions array")
    as_of = iso(data.get("as_of_date") or "", "as_of_date")
    currency = text(str(data.get("currency") or "CNY"), "currency", maximum=6)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    subs = data["subscriptions"]
    if not isinstance(subs, list) or not 1 <= len(subs) <= 500:
        raise ValueError("subscriptions must be a list of 1-500 items")
    results = [audit_subscription(item, as_of) for item in subs]
    total_monthly = sum((Decimal(r["cost_monthly"]) for r in results), Decimal("0"))
    total_annual = sum((Decimal(r["cost_annualized"]) for r in results), Decimal("0"))
    known_idle = [r["idle_seats"] for r in results if r["idle_seats"] is not None]
    cand = [r["seat_reduction_candidate"] for r in results if r["seat_reduction_candidate"]]
    savings = [Decimal(r["potential_annual_savings_estimate"]) for r in results if r["potential_annual_savings_estimate"]]
    counts = {}
    for r in results:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    summary = {
        "as_of_date": as_of.isoformat(), "currency": currency, "subscription_count": len(results),
        "total_monthly_cost": money(total_monthly), "total_annualized_cost": money(total_annual),
        "total_idle_seats_known": None if not known_idle else int(sum(known_idle)),
        "total_reduction_candidates": int(sum(cand)),
        "potential_annual_savings_estimate_total": money(sum(savings)) if savings else None,
        "action_counts": counts,
        "subscriptions": results}
    # Markdown summary directly renderable.
    rows = [["vendor/plan", "seats", "active", "idle", "util", "renewal", "window", "cancel_deadline", "action"]]
    for r in results:
        rows.append([(r["vendor"] + ("/" + r["plan"] if r["plan"] else "")), r["seats_purchased"],
                     "?" if r["active_users_30d"] is None else r["active_users_30d"],
                     "?" if r["idle_seats"] is None else r["idle_seats"],
                     r["utilization_pct"] or "?", r["renewal_date"], r["renewal_window"],
                     r["last_cancel_date"] or "未知", r["action"]])
    summary["markdown_summary"] = (
        "# SaaS 席位与续费审计（" + as_of.isoformat() + "）\n\n"
        "月成本 " + money(total_monthly) + " " + currency + "，年化 " + money(total_annual) + " " + currency + "；"
        "已知闲置席位 " + str(summary["total_idle_seats_known"]) + "，可削减候选 " +
        str(summary["total_reduction_candidates"]) + "，理论年节省估算 " +
        (money(sum(savings)) if savings else "0.00") + " " + currency + "。\n\n" +
        md_table(rows) +
        "\n\n动作口径：REDUCE_CANDIDATE=存在超过最低承诺的闲置席位（未执行降配）；REVIEW=过期/数据可疑/错过取消窗口；"
        "UNKNOWN=活跃人数缺失（不把全部席位判闲置）；KEEP=维持。估算不构成节省保证。")
    return summary


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
                          "message": "请对照 references/guide.md 检查必填字段、数字范围、周期与 ISO 日期。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
