#!/usr/bin/env python3
"""Offline, standard-library analysis. No network or external writes."""
import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_CEILING
from pathlib import Path

def number(value, label, minimum=Decimal("0"), maximum=Decimal("1000000000000")):
    if isinstance(value, bool) or value is None:
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric") from None
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
    return result

def text(value, label, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(label + " must be nonempty text within length limit")
    return value.strip()

def money(value):
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

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
        if not isinstance(data, dict):
            raise ValueError("input must be an object")
        print(json.dumps(analyze(data), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, InvalidOperation):
        print(json.dumps({"error": "invalid_input", "message": "Check required fields, number ranges, dates and duplicate IDs against references/guide.md."}), file=sys.stderr)
        sys.exit(2)

def analyze(data):
    required = ["currency", "items", "fixed_cost", "buffer_rate", "margin_rate", "fee_rate", "tax_rate", "payment_ratios", "extra_hours", "extra_hourly_cost"]
    if any(key not in data for key in required):
        raise ValueError("missing required field")
    currency = text(data["currency"], "currency")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    items = data["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise ValueError("items must contain 1-200 rows")
    labor = Decimal(0)
    lines = []
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("item must be an object")
        label = text(row["label"], "item label")
        subtotal = number(row["hours"], "hours", maximum=Decimal(100000)) * number(row["hourly_cost"], "hourly_cost", maximum=Decimal(1000000))
        labor += subtotal
        lines.append({"label": label, "cost": money(subtotal)})
    fixed = number(data["fixed_cost"], "fixed_cost")
    buffer = number(data["buffer_rate"], "buffer_rate", maximum=Decimal(1))
    margin = number(data["margin_rate"], "margin_rate", maximum=Decimal("0.99"))
    fee = number(data["fee_rate"], "fee_rate", maximum=Decimal("0.99"))
    tax = number(data["tax_rate"], "tax_rate", maximum=Decimal(1))
    if margin + fee >= 1:
        raise ValueError("margin_rate plus fee_rate must be below 1")
    cost = (labor + fixed) * (1 + buffer)
    minimum = (cost / (1 - margin - fee)).quantize(Decimal(".01"), rounding=ROUND_CEILING)
    offered = data.get("offered_net")
    net = minimum if offered is None else number(offered, "offered_net", minimum=Decimal(".01"))
    if net != net.quantize(Decimal('.01')):
        raise ValueError('offered_net must have at most two decimal places')
    tax_amount = (net * tax).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    gross = net + tax_amount
    ratios = data["payment_ratios"]
    if not isinstance(ratios, list) or not 1 <= len(ratios) <= 12:
        raise ValueError("payment_ratios must contain 1-12 values")
    ratios = [number(v, "payment ratio", maximum=Decimal(1)) for v in ratios]
    if sum(ratios) != 1:
        raise ValueError("payment ratios must sum to 1")
    paid = Decimal(0)
    payments = []
    for index, ratio in enumerate(ratios):
        value = gross - paid if index == len(ratios) - 1 else (gross * ratio).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        if value < 0:
            raise ValueError("rounding made final payment negative; simplify schedule")
        payments.append(money(value))
        paid += value
    extra_cost = number(data["extra_hours"], "extra_hours", maximum=Decimal(100000)) * number(data["extra_hourly_cost"], "extra_hourly_cost", maximum=Decimal(1000000)) * (1 + buffer)
    extra_quote = (extra_cost / (1 - margin - fee)).quantize(Decimal(".01"), rounding=ROUND_CEILING)
    profit = net * (1 - fee) - cost
    return {
        "currency": currency, "status": "internal_estimate_not_sent",
        "cost_lines": lines, "labor_cost": money(labor), "fixed_cost": money(fixed),
        "cost_with_buffer": money(cost), "minimum_net_quote": money(minimum),
        "selected_net_quote": money(net), "tax_amount": money(tax_amount),
        "gross_quote": money(gross), "base_profit": money(profit),
        "modeled_margin_pct": money(profit / net * 100),
        "below_target": net < minimum, "payments": payments,
        "extra_buffered_cost": money(extra_cost),
        "change_minimum_net_charge": money(extra_quote),
        "profit_without_change_charge": money(profit - extra_cost),
        "notes": ["All rates are user assumptions, not statutory rates.",
                  "Fee is modeled on pre-tax revenue; tax shown is not profit.",
                  "Buffer is a cost reserve. Extra work excludes fixed materials unless added explicitly."]
    }

if __name__ == "__main__":
    main()
