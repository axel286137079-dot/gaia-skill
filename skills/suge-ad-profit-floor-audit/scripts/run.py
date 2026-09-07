#!/usr/bin/env python3
"""Offline advertising profit-floor audit. Standard library only; read-only,
never pauses campaigns, raises budgets or changes bids."""
import argparse
import json
import re
import sys
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

TWO = Decimal("0.01")
ZERO = Decimal("0")
RATE_MAX = Decimal("1")


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


def rate(value, label):
    return number(value, label, ZERO, RATE_MAX)


def integer(value, label, minimum=ZERO, maximum=Decimal("1000000000")):
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


def iso(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise ValueError(label + " must be ISO YYYY-MM-DD")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(label + " is not a valid calendar date") from None


def money(value):
    return str(value.quantize(TWO, rounding=ROUND_HALF_UP))


def pct(value):
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) + "%"


def audit_campaign(cam, as_of, seen_ids):
    campaign_id = text(cam.get("campaign_id"), "campaign_id", maximum=64)
    name = text(cam.get("name"), "name", maximum=120, allow_empty=True)
    spend = number(cam.get("spend"), "spend")
    revenue = number(cam.get("attributed_revenue"), "attributed_revenue")
    margin_raw = cam.get("gross_margin_rate")
    target_raw = cam.get("target_profit_margin")
    conversions_raw = cam.get("conversions")

    assumed_zero = []
    refund = ZERO
    if cam.get("refund_amount") is not None and str(cam.get("refund_amount")).strip() != "":
        refund = number(cam.get("refund_amount"), "refund_amount")
    else:
        assumed_zero.append("refund_amount")
    platform_fee = ZERO
    if cam.get("platform_fee") is not None and str(cam.get("platform_fee")).strip() != "":
        platform_fee = number(cam.get("platform_fee"), "platform_fee")
    else:
        assumed_zero.append("platform_fee")
    fulfillment = ZERO
    if cam.get("fulfillment_cost") is not None and str(cam.get("fulfillment_cost")).strip() != "":
        fulfillment = number(cam.get("fulfillment_cost"), "fulfillment_cost")
    else:
        assumed_zero.append("fulfillment_cost")

    lag_raw = cam.get("attribution_lag_days")
    lag = 0
    if lag_raw is not None and str(lag_raw).strip() != "":
        lag = integer(lag_raw, "attribution_lag_days", maximum=Decimal("3650"))
    else:
        assumed_zero.append("attribution_lag_days")  # treat as closed window (0), stated in reasons

    margin_known = margin_raw is not None and str(margin_raw).strip() != ""
    margin = rate(margin_raw, "gross_margin_rate") if margin_known else None
    target = rate(target_raw, "target_profit_margin") if (target_raw is not None and str(target_raw).strip() != "") else None
    conversions = integer(conversions_raw, "conversions", maximum=Decimal("1000000000")) \
        if (conversions_raw is not None and str(conversions_raw).strip() != "") else None

    duplicate = campaign_id in seen_ids
    seen_ids.add(campaign_id)

    net_revenue = (revenue - refund).quantize(TWO, rounding=ROUND_HALF_UP)
    gross_profit = None
    contribution = None
    if margin_known:
        gross_profit = (net_revenue * margin).quantize(TWO, rounding=ROUND_HALF_UP)
        contribution = (gross_profit - spend - platform_fee - fulfillment).quantize(TWO, rounding=ROUND_HALF_UP)

    roas = None
    if spend > 0:
        roas = (net_revenue / spend).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    elif net_revenue > 0:
        roas = None  # spend=0: ROAS undefined (no denominator)

    break_even_roas = None
    max_affordable_cpa = None
    if margin_known and spend > 0:
        denom = spend * margin
        if denom > 0:
            break_even_roas = ((spend + platform_fee + fulfillment) / denom).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
    if margin_known and conversions and conversions > 0:
        rev_per_conv = net_revenue / Decimal(conversions)
        max_affordable_cpa = (rev_per_conv * margin - (platform_fee + fulfillment) / Decimal(conversions)).quantize(
            TWO, rounding=ROUND_HALF_UP)

    cost_per_conversion = None
    if conversions and conversions > 0:
        cost_per_conversion = (spend / Decimal(conversions)).quantize(TWO, rounding=ROUND_HALF_UP)

    required_profit = None
    if margin_known and target is not None:
        required_profit = (net_revenue * target).quantize(TWO, rounding=ROUND_HALF_UP)

    reasons = []
    if duplicate:
        reasons.append("duplicate_campaign_id（case 重复，需人工去重核对）")
    for field in assumed_zero:
        reasons.append(field + "_missing_assumed_zero（缺省按 0 计，输出口径已注明）")
    if spend == 0:
        reasons.append("spend_zero（花费为 0，ROAS 无分母不输出）")

    status = None
    if duplicate:
        status = "REVIEW"
        reasons.append("duplicate_campaign_id_forced_review")
    elif not margin_known:
        status = "UNKNOWN"
        reasons.append("gross_margin_rate_missing（毛利率缺失，未输出保本 ROAS/最高可承受 CPA）")
    elif lag > 0:
        status = "DATA_DELAY"
        reasons.append("attribution_window_open（归因窗口未关闭，还需 %d 天，收入可能未完全归因，不直接判亏损）" % lag)
    else:
        if contribution is not None and contribution < 0:
            status = "LOSS_CANDIDATE"
            reasons.append("contribution_negative（贡献利润为负，仅提示复核，未暂停/未加预算/未改出价）")
        elif required_profit is not None and contribution < required_profit:
            status = "REVIEW"
            reasons.append("below_target_margin（有正贡献但低于目标利润率）")
        elif contribution is not None and contribution >= 0:
            status = "HEALTHY"
        else:
            status = "UNKNOWN"

    if roas is not None and break_even_roas is not None and margin_known and lag == 0 and not duplicate:
        if roas < break_even_roas and status not in ("LOSS_CANDIDATE",):
            status = "REVIEW"
            reasons.append("roas_below_break_even")
        if status == "LOSS_CANDIDATE" and roas >= break_even_roas:
            reasons.append("roas_above_break_even_but_negative_contribution（口径冲突，人工复核费用项）")

    checklist = []
    if status == "LOSS_CANDIDATE":
        checklist.append("核对退款与渠道费口径是否重复计入")
        checklist.append("核对转化归因是否跨活动错配")
        checklist.append("连续两个完整归因周期仍为负再考虑暂停（本技能不执行暂停）")
    elif status == "DATA_DELAY":
        checklist.append("等待归因窗口关闭后再评估（本技能不提前下亏损结论）")
        checklist.append("窗口关闭后重跑本脚本复核")
    elif status == "REVIEW":
        checklist.append("核对输入口径（毛利率/退款/费用）与目标利润率设置")
        checklist.append("小额分日测试后再决定是否调整出价（本技能不改出价）")
    elif status == "HEALTHY":
        checklist.append("维持现有投放节奏；按周复核毛利率与退款率变化")
    checklist.append("任何暂停、加预算、改出价动作均需人工在投放平台执行")

    return {
        "campaign_id": campaign_id,
        "name": name,
        "as_of_date": as_of.isoformat(),
        "spend": money(spend),
        "attributed_revenue": money(revenue),
        "refund_amount": money(refund),
        "net_revenue": money(net_revenue),
        "gross_margin_rate": pct(margin) if margin_known else None,
        "gross_profit": None if gross_profit is None else money(gross_profit),
        "platform_fee": money(platform_fee),
        "fulfillment_cost": money(fulfillment),
        "contribution_profit": None if contribution is None else money(contribution),
        "target_profit_margin": pct(target) if target is not None else None,
        "required_profit": None if required_profit is None else money(required_profit),
        "conversions": conversions,
        "cost_per_conversion": None if cost_per_conversion is None else money(cost_per_conversion),
        "roas": None if roas is None else str(roas),
        "break_even_roas": None if break_even_roas is None else str(break_even_roas),
        "max_affordable_cpa": None if max_affordable_cpa is None else money(max_affordable_cpa),
        "attribution_lag_days": lag,
        "formula_breakdown": {
            "net_revenue": "attributed_revenue(%s) - refund_amount(%s)" % (money(revenue), money(refund)),
            "gross_profit": "net_revenue(%s) x gross_margin_rate(%s)" % (money(net_revenue), pct(margin) if margin_known else "?") if margin_known else None,
            "contribution_profit": "gross_profit(%s) - spend(%s) - platform_fee(%s) - fulfillment_cost(%s)" % (
                money(gross_profit) if gross_profit is not None else "?", money(spend), money(platform_fee), money(fulfillment)) if margin_known else None,
            "roas": "net_revenue(%s) / spend(%s)" % (money(net_revenue), money(spend)) if spend > 0 else None,
            "break_even_roas": "（spend(%s)+platform_fee(%s)+fulfillment_cost(%s)）/ (spend(%s) x gross_margin_rate(%s))" % (
                money(spend), money(platform_fee), money(fulfillment), money(spend), pct(margin) if margin_known else "?") if (margin_known and spend > 0) else None,
            "cost_per_conversion": "spend(%s) / conversions(%s)" % (money(spend), conversions) if conversions else None,
            "max_affordable_cpa": "net_revenue_per_conversion x gross_margin_rate - (platform_fee+fulfillment_cost)/conversions" if (margin_known and conversions) else None,
        },
        "status": status,
        "reasons": reasons,
        "checklist": checklist,
        "note": "本结果只读复核：不暂停活动、不调整预算、不改出价；归属统计以平台后台为准。"}


def analyze(data):
    if not isinstance(data, dict) or "campaigns" not in data:
        raise ValueError("input must contain a campaigns array")
    as_of = iso(data.get("as_of_date") or "", "as_of_date")
    currency = text(str(data.get("currency") or "CNY"), "currency", maximum=6)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    campaigns = data["campaigns"]
    if not isinstance(campaigns, list) or not 1 <= len(campaigns) <= 500:
        raise ValueError("campaigns must be a list of 1-500 items")
    seen = set()
    results = [audit_campaign(item, as_of, seen) for item in campaigns]

    dupes = sorted({c["campaign_id"] for c in results
                    if sum(1 for other in results if other["campaign_id"] == c["campaign_id"]) > 1})
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    total_spend = sum((Decimal(r["spend"]) for r in results), ZERO)
    total_net_revenue = sum((Decimal(r["net_revenue"]) for r in results), ZERO)
    contribution_known = [Decimal(r["contribution_profit"]) for r in results if r["contribution_profit"] is not None]

    summary = {
        "as_of_date": as_of.isoformat(), "currency": currency, "campaign_count": len(results),
        "total_spend": money(total_spend), "total_net_revenue": money(total_net_revenue),
        "total_contribution_profit_known": money(sum(contribution_known)) if contribution_known else None,
        "duplicate_campaign_ids": dupes,
        "status_counts": counts,
        "campaigns": results}

    rows = [["campaign", "spend", "net_rev", "ROAS", "保本ROAS", "贡献利润", "转化成本", "状态"]]
    for r in results:
        rows.append([r["campaign_id"], r["spend"], r["net_revenue"],
                     r["roas"] if r["roas"] is not None else "?", r["break_even_roas"] or "?",
                     r["contribution_profit"] if r["contribution_profit"] is not None else "?",
                     r["cost_per_conversion"] if r["cost_per_conversion"] is not None else "?",
                     r["status"]])
    summary["markdown_summary"] = (
        "# 广告投放利润底线审计（" + as_of.isoformat() + "）\n\n"
        "口径：净收入=归因收入−退款；毛利贡献=净收入×毛利率；贡献利润=毛利贡献−花费−平台费−履约费。"
        "总花费 " + money(total_spend) + " " + currency + "，总净收入 " + money(total_net_revenue) + " " + currency + "。\n\n"
        + md_table(rows) +
        "\n\n状态口径：HEALTHY=贡献利润达标；REVIEW=正贡献但低于目标/口径待核；LOSS_CANDIDATE=贡献利润为负（仅复核提示）；"
        "DATA_DELAY=归因窗口未关闭不判亏损；UNKNOWN=毛利率缺失。比率口径：ROAS=净收入/花费；保本ROAS=（花费+平台费+履约费）/（花费×毛利率），"
        "含平台与履约成本；最高可承受CPA=单转化净收入×毛利率−(平台费+履约费)/转化数。止损复核清单见各活动 detail，"
        "本技能不执行暂停/加预算/改出价。")
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
                          "message": "请对照 references/guide.md 检查必填字段、金额/比率范围与 ISO 日期。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
