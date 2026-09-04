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

from datetime import date
import math

def day(value):
    return date.fromisoformat(text(value, "date", 10))

def analyze(data):
    base = text(data["base_currency"], "base_currency")
    if not re.fullmatch(r"[A-Z]{3}", base):
        raise ValueError("invalid currency")
    qty = number(data["quantity"], "quantity", minimum=Decimal(".000001"), maximum=Decimal(100000000))
    unit = text(data["unit"], "unit")
    asof = day(data["as_of"])
    deadline = day(data["required_by"])
    if deadline < asof:
        raise ValueError("required_by is before as_of")
    if not isinstance(data["quotes"], list) or not 1 <= len(data["quotes"]) <= 100:
        raise ValueError("quotes must contain 1-100 rows")
    seen, results = set(), []
    for row in data["quotes"]:
        if not isinstance(row, dict):
            raise ValueError("quote must be an object")
        ident = text(row["id"], "quote id", 80)
        if ident in seen:
            raise ValueError("duplicate quote id")
        seen.add(ident)
        blockers = []
        cur = row.get("currency")
        if not isinstance(cur, str) or not re.fullmatch(r"[A-Z]{3}", cur):
            blockers.append("currency_missing_or_invalid")
        if row.get("unit") != unit:
            blockers.append("unit_not_normalized")
        if row.get("spec_confirmed") is not True:
            blockers.append("spec_not_confirmed")
        if row.get("destination_confirmed") is not True:
            blockers.append("destination_not_confirmed")
        if not row.get("source_ref"):
            blockers.append("source_missing")
        if not row.get("payment_terms"):
            blockers.append("payment_terms_missing")
        if not row.get("valid_until") or day(row["valid_until"]) < asof:
            blockers.append("expired_or_validity_missing")
        lead = row.get("lead_days")
        if lead is None:
            blockers.append("lead_time_missing")
        else:
            lead_num = number(lead, "lead_days", maximum=Decimal(3650))
            if lead_num != lead_num.to_integral_value():
                raise ValueError("lead_days must be integer")
            if lead_num > (deadline - asof).days:
                blockers.append("late_delivery")
        cash, packs, units, landed = None, None, None, None
        inputs = ["units_per_pack", "min_packs", "price_per_pack_net", "tax_rate", "shipping_gross", "setup_net", "other_gross"]
        missing = [key for key in inputs if row.get(key) is None]
        blockers.extend("missing_" + key for key in missing)
        fx = Decimal(1)
        if cur != base:
            if row.get("fx_to_base") is None or not row.get("fx_source") or not row.get("fx_date"):
                blockers.append("fx_evidence_missing")
                fx = None
            else:
                fx = number(row["fx_to_base"], "fx_to_base", minimum=Decimal(".000000001"), maximum=Decimal(1000000))
                fxday = day(row["fx_date"])
                if fxday > asof or (asof - fxday).days > 7:
                    blockers.append("fx_date_future_or_older_than_7_days")
        if not missing:
            size = number(row["units_per_pack"], "units_per_pack", minimum=Decimal(".000001"), maximum=Decimal(100000000))
            minimum = number(row["min_packs"], "min_packs", maximum=Decimal(100000000))
            if minimum != minimum.to_integral_value():
                raise ValueError("min_packs must be integer")
            packs = max(math.ceil(qty / size), int(minimum))
            units = Decimal(packs) * size
            price = number(row["price_per_pack_net"], "price_per_pack_net")
            tax = number(row["tax_rate"], "tax_rate", maximum=Decimal(1))
            shipping = number(row["shipping_gross"], "shipping_gross")
            setup = number(row["setup_net"], "setup_net")
            other = number(row["other_gross"], "other_gross")
            cash = (packs * price + setup) * (1 + tax) + shipping + other
            if fx is not None:
                landed = cash * fx
        results.append({
            "id": ident, "eligible": not blockers, "blockers": blockers,
            "buy_packs": packs, "buy_units": money(units) if units is not None else None,
            "surplus_units": money(units - qty) if units is not None else None,
            "landed_cash_base": money(landed) if landed is not None else None,
            "cost_per_required_unit": money(landed / qty) if landed is not None else None,
            "source_ref": row.get("source_ref", ""),
            "payment_terms": row.get("payment_terms", ""),
        })
    ranked = sorted((r for r in results if r["eligible"]), key=lambda r: (Decimal(r["landed_cash_base"]), r["id"]))
    return {
        "base_currency": base, "as_of": data["as_of"],
        "ranked_ids": [r["id"] for r in ranked], "quotes": results,
        "status": "comparison_only_not_purchase_authorization",
        "notes": ["Rank by total cash for satisfying the same demand, not unit sticker price.",
                  "Tax is user supplied. Gross cash is not net accounting cost or recoverable VAT.",
                  "No missing charge is silently treated as zero; all price rows require confirmation."]
    }

if __name__ == "__main__":
    main()
