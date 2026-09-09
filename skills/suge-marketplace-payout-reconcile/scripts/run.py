#!/usr/bin/env python3
"""Offline marketplace payout reconciliation by currency & payout id.
Three layers: transaction net sum vs platform declared payout vs bank receipt.
Standard library only; read-only audit, never connects accounts nor raises
disputes.  Only produces evidence lines and REVIEW hints."""
import argparse
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

TWO = Decimal("0.01")
ZERO = Decimal("0")
ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?$")
ALLOWED_TYPES = {"charge", "refund", "fee", "adjustment", "reserve", "release", "other"}
SETTLED = {"settled", "paid", "completed"}
NOT_SETTLED = {"pending", "unassigned", "failed", "processing", "rejected", "unknown"}
FORMULAS = {"gross+fee", "gross-fee"}  # net formula; sign direction must be user-declared


def number(value, label, minimum=ZERO, maximum=Decimal("1000000000000000")):
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


def parse_dt(value, label, required=True):
    """ISO8601 datetime with tz offset, or plain date (kept as date)."""
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
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(label + " is not a valid date") from None


def money(value):
    return str(value.quantize(TWO, rounding=ROUND_HALF_UP))


def fmt_amount(value):
    if value is None:
        return None
    return str(value.quantize(TWO, rounding=ROUND_HALF_UP))


def audit_transaction(tx, payout_id, currency, formula, as_of, type_totals):
    tx_id = text(tx.get("transaction_id"), "transaction_id", maximum=80)
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", tx_id):
        raise ValueError("transaction_id must be a plain identifier")
    ttype = text(str(tx.get("type") or ""), "type", maximum=16)
    gross = number(tx.get("gross"), "gross", minimum=Decimal("-1000000000000000"))
    fee_raw = tx.get("fee")
    fee = ZERO
    has_fee = fee_raw is not None and str(fee_raw).strip() != ""
    if has_fee:
        fee = number(fee_raw, "fee", minimum=Decimal("-1000000000000000"))
    net_raw = tx.get("net")
    tx_currency = tx.get("currency")
    tx_currency_text = text(str(tx_currency or ""), "currency", maximum=6, allow_empty=True) \
        if tx_currency is not None else ""
    tx_payout = tx.get("payout_id")
    tx_payout_text = text(str(tx_payout or ""), "payout_id", maximum=80, allow_empty=True) \
        if tx_payout is not None else ""
    tx_reasons = []

    # -- currency consistency (do not mix currencies in one payout) --
    cross_currency = bool(tx_currency_text and tx_currency_text != currency)
    if cross_currency:
        tx_reasons.append("cross_currency（交易币种 %s 与回款币种 %s 不同，不混入本币汇总）" % (tx_currency_text, currency))

    # -- payout binding --
    binding_mismatch = bool(tx_payout_text and tx_payout_text != payout_id)
    if binding_mismatch:
        tx_reasons.append("payout_binding_mismatch（交易归属 %s 与所在回款 %s 不一致）" % (tx_payout_text, payout_id))

    # -- type accounting (unknown types do not get net semantics, flagged) --
    unknown_type = ttype not in ALLOWED_TYPES
    if unknown_type:
        tx_reasons.append("unknown_type（%s：类型不在 charge/refund/fee/adjustment/reserve/release/other，语义未确认）" % ttype)
    elif not cross_currency:
        bucket = type_totals.setdefault(ttype, {"count": 0, "gross": ZERO, "fee": ZERO, "net": ZERO})
        bucket["count"] += 1
        bucket["gross"] += gross
        bucket["fee"] += fee if has_fee else ZERO
        if net_raw is not None and str(net_raw).strip() != "":
            bucket["net"] += number(net_raw, "net", minimum=Decimal("-1000000000000000"))

    # -- net verification against declared formula --
    net = None
    net_verified = True
    if net_raw is not None and str(net_raw).strip() != "":
        net = number(net_raw, "net", minimum=Decimal("-1000000000000000"))
        if formula is None:
            if has_fee and fee != ZERO:
                net_verified = False
                tx_reasons.append("net_formula_missing（fee=%s 但未声明 net=gross+fee 或 net=gross-fee，不猜 fee 正负方向）" % money(fee))
        else:
            if formula == "gross+fee":
                expect = gross + fee
            else:  # gross-fee
                expect = gross - fee
            expect = expect.quantize(TWO, rounding=ROUND_HALF_UP)
            if net.quantize(TWO, rounding=ROUND_HALF_UP) != expect:
                net_verified = False
                tx_reasons.append("net_formula_mismatch（net=%s 与 %s=%s 不一致）" % (money(net), formula, money(expect)))
    else:
        # net omitted: derive only when a formula is declared, else REVIEW
        if formula is not None:
            net = (gross + fee if formula == "gross+fee" else gross - fee).quantize(TWO, rounding=ROUND_HALF_UP)
        else:
            if has_fee and fee != ZERO:
                net_verified = False
                tx_reasons.append("net_missing_with_fee（net 与 net_formula 均缺失且 fee 非零，无法确定净额）")
            else:
                net = gross.quantize(TWO, rounding=ROUND_HALF_UP)
                tx_reasons.append("net_derived_as_gross（fee 为 0/缺省，net 按 gross 处理）")

    # -- future occurrence sanity --
    occ = parse_dt(tx.get("occurred_at"), "occurred_at", required=False)
    if occ is not None:
        if isinstance(occ, datetime) and isinstance(as_of, datetime) and occ > as_of:
            tx_reasons.append("occurred_in_future（交易时间晚于基准时间，请核对）")

    return {"transaction_id": tx_id, "type": ttype, "gross": money(gross),
            "fee": None if not has_fee else money(fee),
            "net": money(net) if net is not None else None,
            "currency": tx_currency_text if tx_currency_text else currency,
            "payout_id": tx_payout_text if tx_payout_text else payout_id,
            "net_verified": net_verified,
            "issues": tx_reasons}


def audit_payout(payout, as_of, currency, seen_payouts, seen_transactions):
    payout_id = text(payout.get("payout_id"), "payout_id", maximum=80)
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", payout_id):
        raise ValueError("payout_id must be a plain identifier")
    if payout_id in seen_payouts:
        raise ValueError("duplicate payout_id: " + payout_id)
    seen_payouts.add(payout_id)

    raw_status = text(str(payout.get("status") or ""), "status", maximum=20)
    expected = number(payout.get("expected_platform_payout"), "expected_platform_payout")
    tolerance_raw = payout.get("tolerance")
    tolerance = number(tolerance_raw, "tolerance", maximum=Decimal("1000000")) \
        if (tolerance_raw is not None and str(tolerance_raw).strip() != "") else ZERO
    bank_raw = payout.get("bank_received_amount")
    bank = None
    if bank_raw is not None and str(bank_raw).strip() != "":
        bank = number(bank_raw, "bank_received_amount")
    bank_at = parse_dt(payout.get("bank_received_at"), "bank_received_at", required=False)
    formula_raw = payout.get("net_formula")
    formula = None
    if formula_raw is not None and str(formula_raw).strip() != "":
        formula = text(str(formula_raw), "net_formula", maximum=20)
        if formula not in FORMULAS:
            raise ValueError("net_formula must be gross+fee or gross-fee")
    trace = payout.get("trace_reference")
    trace_text = text(str(trace or ""), "trace_reference", maximum=120, allow_empty=True) \
        if trace is not None else None

    transactions = payout.get("transactions")
    if transactions is None:
        transactions = []
    if not isinstance(transactions, list) or len(transactions) > 2000:
        raise ValueError("transactions must be a list of up to 2000 items")

    reasons = []
    review_flags = []
    type_totals = {}
    tx_details = []
    dup_tx = False
    for tx in transactions:
        if not isinstance(tx, dict):
            raise ValueError("each transaction must be an object")
        tx_id = tx.get("transaction_id")
        if tx_id is not None and str(tx_id).strip() != "":
            key = (str(tx_id).strip(),)
            if key in seen_transactions:
                dup_tx = True
                review_flags.append("duplicate_transaction_id（交易 %s 在本批跨回款重复出现）" % str(tx_id))
            else:
                seen_transactions.add(key)
        detail = audit_transaction(tx, payout_id, currency, formula, as_of, type_totals)
        tx_details.append(detail)
        for issue in detail["issues"]:
            if issue.startswith("net_derived_as_gross"):
                reasons.append(issue)  # informational only
            else:
                review_flags.append(issue)

    # trusted sum = known-type, same-currency, correctly-bound transactions only
    calculated_net = ZERO
    trusted_excluded = []
    for d in tx_details:
        eligible = (d["type"] in ALLOWED_TYPES and d["type"] != "other"
                    and d["currency"] == currency and d["net"] is not None
                    and not any("payout_binding_mismatch" in r for r in d["issues"]))
        if eligible:
            calculated_net += Decimal(d["net"])
        else:
            trusted_excluded.append(d["transaction_id"])
    if trusted_excluded:
        review_flags.append("excluded_from_net_sum（%d 条因未知类型/other/跨币种/归属不一致未计入 trusted net 汇总：%s）"
                            % (len(trusted_excluded), ",".join(trusted_excluded[:10])))

    platform_diff = calculated_net - expected
    bank_diff = None
    if bank is not None:
        bank_diff = calculated_net - bank

    # duplicate flag
    if dup_tx:
        review_flags.append("review_duplicate_ids（存在重复交易 ID，流水可信度存疑，请人工核对原始明细）")

    # empty / untraceable transactions
    manual_untraceable = False
    if len(transactions) == 0:
        manual_untraceable = True
        reasons.append("manual_settlement_no_transactions（无交易组成可追溯，无法从流水验证净额）")

    # -- status decision --
    status = None
    if raw_status in SETTLED:
        if manual_untraceable:
            status = "UNKNOWN"
            reasons.append("settled_but_no_breakdown（已结算但无交易明细，无法追溯 → UNKNOWN，不臆断差异）")
        elif review_flags:
            status = "UNKNOWN"
            reasons.append("review_required（存在需要人工核对的线索：%s）" % "; ".join(review_flags[:5]))
        elif abs(platform_diff) > tolerance:
            status = "PLATFORM_LEDGER_DIFFERENCE"
            reasons.append("platform_ledger_difference（trusted net 与平台声明差 %s，超容差 %s）"
                           % (money(platform_diff), money(tolerance)))
        elif bank is None or (bank_at is not None and isinstance(as_of, datetime)
                              and isinstance(bank_at, datetime) and bank_at > as_of):
            status = "UNKNOWN"
            reasons.append("bank_receipt_not_observed（平台账一致，但银行实收未观察到%s，不能判为平台欠款，也不能判 MATCH）"
                           % ("（银行到账日期晚于基准）" if bank_at is not None else ""))
        elif abs(bank_diff) > tolerance:
            status = "BANK_RECEIPT_DIFFERENCE"
            reasons.append("bank_receipt_difference（trusted net 与银行实收差 %s，超容差 %s）"
                           % (money(bank_diff), money(tolerance)))
        else:
            status = "MATCH"
            reasons.append("match（trusted net = 平台声明 = 银行实收，差异均在容差内）")
    elif raw_status in NOT_SETTLED or raw_status == "":
        status = "PENDING"
        reasons.append("not_settled（原状态 %s：单独列出，不混入已结算判定）" % (raw_status or "missing"))
    else:
        status = "UNKNOWN"
        reasons.append("unknown_status（%s：不猜测结算语义）" % raw_status)

    return {
        "payout_id": payout_id, "raw_status": raw_status, "status": status,
        "currency": currency, "expected_platform_payout": money(expected),
        "bank_received_amount": None if bank is None else money(bank),
        "bank_received_at": None if bank_at is None else (bank_at.isoformat() if hasattr(bank_at, "isoformat") else str(bank_at)),
        "trace_reference": trace_text, "tolerance": money(tolerance),
        "net_formula": formula,
        "transaction_count": len(transactions),
        "trusted_calculated_net": money(calculated_net),
        "platform_difference": money(platform_diff),
        "bank_difference": None if bank_diff is None else money(bank_diff),
        "type_totals": {k: {"count": v["count"], "gross": money(v["gross"]), "fee": money(v["fee"]),
                            "net": money(v["net"]) if v["count"] else None} for k, v in sorted(type_totals.items())},
        "excluded_transactions": trusted_excluded,
        "review_flags": review_flags,
        "reasons": reasons,
        "note": "三层核对：trusted net=Σ(同币种已知类型交易 net)。只输出线索：不连接账户、不发起提现或争议；"
                "银行未到账不自动写成平台欠款。"}


def analyze(data):
    if not isinstance(data, dict) or "payouts" not in data:
        raise ValueError("input must contain a payouts array")
    currency = text(str(data.get("currency") or "CNY"), "currency", maximum=6)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    as_of = parse_dt(data.get("as_of") or data.get("as_of_date") or "", "as_of")
    payouts_raw = data["payouts"]
    if not isinstance(payouts_raw, list) or not 1 <= len(payouts_raw) <= 500:
        raise ValueError("payouts must be a list of 1-500 items")
    seen_payouts = set()
    seen_transactions = set()
    results = [audit_payout(p, as_of, currency, seen_payouts, seen_transactions) for p in payouts_raw]
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    totals = {"trusted_net": sum((Decimal(r["trusted_calculated_net"]) for r in results
                                  if r["status"] in ("MATCH", "PLATFORM_LEDGER_DIFFERENCE", "BANK_RECEIPT_DIFFERENCE", "UNKNOWN")
                                  and r["raw_status"] in SETTLED), ZERO),
              "platform_declared": sum((Decimal(r["expected_platform_payout"]) for r in results
                                        if r["raw_status"] in SETTLED), ZERO),
              "bank_received": sum((Decimal(r["bank_received_amount"]) for r in results
                                    if r["bank_received_amount"] is not None), ZERO)}
    summary = {
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
        "currency": currency, "payout_count": len(results),
        "status_counts": counts, "payouts": results,
        "totals_settled": {k: money(v) for k, v in totals.items()},
        "markdown_summary": build_markdown(results, currency, as_of, totals)}
    return summary


def build_markdown(results, currency, as_of, totals):
    rows = [["payout", "原状态", "交易数", "trusted net", "平台声明", "银行实收", "平台差", "银行差", "状态"]]
    for r in results:
        rows.append([r["payout_id"], r["raw_status"], r["transaction_count"],
                     r["trusted_calculated_net"], r["expected_platform_payout"],
                     r["bank_received_amount"] if r["bank_received_amount"] is not None else "—",
                     r["platform_difference"],
                     r["bank_difference"] if r["bank_difference"] is not None else "—",
                     r["status"]])
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    head = ["# 渠道回款拆分对账（基准 %s，币种 %s）\n\n" % (as_of, currency)]
    head.append("三层核对口径：trusted net = Σ(同币种且类型明确的交易 net)；与平台声明回款、银行实收逐层比较，"
                "差异超过容差才报差异；银行未到账不写成平台欠款。net 与 fee 正负方向必须由用户声明的 net_formula "
                "（gross+fee / gross-fee）确认，缺失/不一致标线索不猜方向。\n\n")
    head.append(md_table(rows))
    head.append("\n\n已结算汇总：trusted net 合计 %s %s、平台声明合计 %s、银行实收合计 %s（仅统计已结算回款）。\n\n"
                % (money(totals["trusted_net"]), currency, money(totals["platform_declared"]), money(totals["bank_received"])))
    head.append("状态分布：" + ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())) + "。\n")
    head.append("\n本输出只是对账线索：不连接账户、不发起提现或争议；重复交易 ID、跨币种、缺归属、未知类型等 "
                "REVIEW 线索已逐条列出，交人工核实原始流水。")
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
                          "message": "请对照 references/guide.md 检查基准时间、payout_id/transaction_id 唯一、"
                                     "币种一致、net_formula 声明（gross+fee/gross-fee）、非负金额与带时区时间。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
