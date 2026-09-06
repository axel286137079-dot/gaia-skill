#!/usr/bin/env python3
"""Offline creator campaign delivery & settlement reconciliation. Never pays, never files taxes."""
import argparse
import json
import re
import sys
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

TWO = Decimal("0.01")
INVOICE_STATES = {"missing", "pending", "present", "n_a"}


def number(value, label, minimum=Decimal("0"), maximum=Decimal("1000000000000")):
    if isinstance(value, bool) or value is None:
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
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


def pct(value):
    return str((value * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)) + "%"


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def reconcile_creator(item, currency):
    cid = text(item.get("creator_id"), "creator_id")
    base_fee = number(item.get("base_fee"), "base_fee")
    rate = number(item.get("commission_rate"), "commission_rate", maximum=Decimal("10"))
    net_sales = item.get("eligible_net_sales")
    refunds = item.get("refunds_pending")
    required = item.get("deliverables_required")
    accepted = item.get("deliverables_accepted")
    invoice = item.get("invoice_status")
    paid = number(item.get("paid_amount") or "0", "paid_amount")
    window_ended = item.get("refund_window_ended")
    if window_ended is not None and not isinstance(window_ended, bool):
        raise ValueError("refund_window_ended must be boolean when provided")

    missing = []
    if net_sales is None or str(net_sales).strip() == "":
        missing.append("eligible_net_sales")
    if refunds is None or str(refunds).strip() == "":
        missing.append("refunds_pending")
    if required is None or accepted is None:
        missing.append("deliverables")
    if invoice is None or str(invoice).strip() == "":
        missing.append("invoice_status")
    if window_ended is None:
        missing.append("refund_window_ended")

    def _num(key, default=None):
        v = item.get(key)
        if v is None or str(v).strip() == "":
            return default
        return number(v, key)

    net_sales_d = _num("eligible_net_sales")
    refunds_d = _num("refunds_pending")
    required_i = _num("deliverables_required")
    accepted_i = _num("deliverables_accepted")
    if required_i is not None and required_i != required_i.to_integral_value():
        raise ValueError("deliverables_required must be an integer")
    if accepted_i is not None and accepted_i != accepted_i.to_integral_value():
        raise ValueError("deliverables_accepted must be an integer")
    required_i = None if required_i is None else int(required_i)
    accepted_i = None if accepted_i is None else int(accepted_i)
    invoice_s = text(invoice, "invoice_status", maximum=20) if invoice is not None and str(invoice).strip() else None
    if invoice_s and invoice_s not in INVOICE_STATES:
        raise ValueError("invoice_status must be one of " + ",".join(sorted(INVOICE_STATES)))

    issues = []
    status = "READY_TO_REVIEW"
    commission = None
    base_net = None
    hold = None
    net_payable = None
    gross_payable = None

    if rate > Decimal("1"):
        issues.append("commission_rate>1（超出常规佣金率上限，交人工复核）")
    elif net_sales_d is None or refunds_d is None:
        issues.append("sales_or_refunds_missing（净销售或退款数据缺失，无法计算净额佣金）")
        status = "HOLD"
    else:
        base_net = net_sales_d - refunds_d
        commission = base_net * rate
        gross_payable = base_fee + commission
        if window_ended is None:
            issues.append("refund_window_ended_missing（退款窗口状态未知，暂缓退款金额不可释放）")
            status = "HOLD"
        elif window_ended is True:
            hold = Decimal("0")
            net_payable = gross_payable
        else:
            hold = refunds_d
            net_payable = gross_payable - hold

    if required_i is not None and accepted_i is not None:
        if accepted_i > required_i:
            issues.append("accepted_gt_required（已验收数大于要求数，数据可疑，交人工复核）")
            status = "REVIEW" if status in ("READY_TO_REVIEW",) else status
        elif accepted_i < required_i:
            issues.append("deliverable_shortfall（交付验收 %d/%d 未达标，暂不可标记可付款）" % (accepted_i, required_i))
            status = "HOLD"
    elif "deliverables" in missing:
        issues.append("deliverables_missing（交付要求或验收数缺失）")
        status = "HOLD"

    if invoice_s != "present":
        if net_payable is not None and net_payable > 0:
            issues.append("invoice_gap（发票/凭证状态为 %s，付款前需补齐）" % (invoice_s or "missing"))
            if status in ("READY_TO_REVIEW",):
                status = "REVIEW"
        elif status == "READY_TO_REVIEW":
            issues.append("invoice_gap（发票状态非 present）")
            status = "REVIEW"

    paid_diff = None
    if net_payable is not None:
        paid_diff = paid - net_payable
        if paid_diff > 0:
            issues.append("paid_gt_candidate（已付大于应付候选，疑似多付，不作法律/税务结论）")
            status = "OVERPAID_CANDIDATE"

    if missing and status == "READY_TO_REVIEW":
        status = "HOLD"
    if not missing and status == "READY_TO_REVIEW":
        status = "READY_TO_REVIEW"

    return {"creator_id": cid, "base_fee": money(base_fee), "commission_rate": str(rate),
            "eligible_net_sales": None if net_sales_d is None else money(net_sales_d),
            "refunds_pending": None if refunds_d is None else money(refunds_d),
            "commission_base_net": None if base_net is None else money(base_net),
            "commission": None if commission is None else money(commission),
            "gross_payable": None if gross_payable is None else money(gross_payable),
            "refund_window_ended": window_ended,
            "hold_amount": None if hold is None else money(hold),
            "net_payable_candidate": None if net_payable is None else money(net_payable),
            "paid_amount": money(paid),
            "paid_diff": None if paid_diff is None else money(paid_diff),
            "deliverables_required": required_i, "deliverables_accepted": accepted_i,
            "invoice_status": invoice_s, "status": status, "issues": issues,
            "note": "供财务/业务人工复核；不发起付款、不提交税务材料、不构成法律或税务结论。"}


def analyze(data):
    if not isinstance(data, dict) or "creators" not in data:
        raise ValueError("input must contain a creators array")
    currency = text(str(data.get("currency") or "CNY"), "currency", maximum=6)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    settlement = iso(data.get("settlement_date") or "", "settlement_date")
    campaign = text(str(data.get("campaign_id") or "CMP-UNKNOWN"), "campaign_id", maximum=60)
    creators = data["creators"]
    if not isinstance(creators, list) or not 1 <= len(creators) <= 300:
        raise ValueError("creators must be a list of 1-300 items")
    results = [reconcile_creator(item, currency) for item in creators]
    seen = {}
    for r in results:
        seen[r["creator_id"]] = seen.get(r["creator_id"], 0) + 1
    duplicates = [cid for cid, n in seen.items() if n > 1]
    for r in results:
        if r["creator_id"] in duplicates:
            r["issues"].append("duplicate_creator_id（同一 creator_id 出现多次，请核对是否重复行）")
            if r["status"] in ("READY_TO_REVIEW", "OVERPAID_CANDIDATE"):
                r["status"] = "REVIEW"
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    g = [Decimal(r["gross_payable"]) for r in results if r["gross_payable"] is not None]
    h = [Decimal(r["hold_amount"]) for r in results if r["hold_amount"] is not None]
    n = [Decimal(r["net_payable_candidate"]) for r in results if r["net_payable_candidate"] is not None]
    p = sum((Decimal(r["paid_amount"]) for r in results), Decimal("0"))
    rows = [["creator", "base", "commission", "gross", "hold", "net_payable", "paid", "delivery", "invoice", "status"]]
    for r in results:
        rows.append([r["creator_id"], r["base_fee"], r["commission"] or "?",
                     r["gross_payable"] or "?", r["hold_amount"] or "?",
                     r["net_payable_candidate"] or "?", r["paid_amount"],
                     ("%s/%s" % (r["deliverables_accepted"], r["deliverables_required"])) if r["deliverables_required"] is not None else "?",
                     r["invoice_status"] or "?", r["status"]])
    summary = {
        "campaign_id": campaign, "settlement_date": settlement.isoformat(), "currency": currency,
        "creator_count": len(results),
        "total_gross_payable": money(sum(g)) if g else None,
        "total_hold_amount": money(sum(h)) if h else None,
        "total_net_payable_candidate": money(sum(n)) if n else None,
        "total_paid_amount": money(p),
        "total_paid_diff": money(sum(n) - p) if n else None,
        "duplicate_creator_ids": duplicates or None,
        "status_counts": counts,
        "creators": results}
    summary["markdown_summary"] = (
        "# 达人合作交付与结算核对（" + campaign + " / " + settlement.isoformat() + "）\n\n"
        "合计应付候选 " + (money(sum(n)) if n else "未知") + " " + currency + "，暂缓 " +
        (money(sum(h)) if h else "0.00") + " " + currency + "，已付 " + money(p) + " " + currency + "。\n\n" +
        md_table(rows) +
        "\n\n结算口径：READY_TO_REVIEW=可提交人工复核；HOLD=退款窗口/发票/交付或数据缺失，暂不可付款；"
        "REVIEW=发票缺、验收超要求或重复行；OVERPAID_CANDIDATE=已付大于应付候选（疑似多付，不作结论）。"
        "不发起付款、不提交税务材料。")
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
                          "message": "请对照 references/guide.md 检查 creator 字段、金额范围、佣金率与 ISO 日期。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
