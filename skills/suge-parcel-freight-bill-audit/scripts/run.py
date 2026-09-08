#!/usr/bin/env python3
"""Offline parcel freight bill audit. Standard library only; read-only,
never pays invoices and never submits claims."""
import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from pathlib import Path

TWO = Decimal("0.01")
ZERO = Decimal("0")


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


def positive(value, label):
    return number(value, label, minimum=Decimal("0.000000001"))


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


def money(value):
    return str(value.quantize(TWO, rounding=ROUND_HALF_UP))


def audit_shipment(ship, seen):
    shipment_id = text(ship.get("shipment_id"), "shipment_id", maximum=64)
    if shipment_id in seen:
        raise ValueError("duplicate shipment_id: " + shipment_id)
    seen.add(shipment_id)

    actual = number(ship.get("actual_weight_kg"), "actual_weight_kg")
    step = positive(ship.get("weight_step_kg"), "weight_step_kg")
    first_weight = number(ship.get("first_weight_kg"), "first_weight_kg", maximum=Decimal("1000000"))
    first_price = number(ship.get("first_price"), "first_price")
    extra_price = number(ship.get("extra_step_price"), "extra_step_price")

    rounding = ship.get("rounding_mode")
    if rounding is None or str(rounding).strip() == "":
        rounding = "ceil"
    else:
        rounding = text(str(rounding), "rounding_mode", maximum=16)
    if rounding != "ceil":
        raise ValueError("rounding_mode must be 'ceil'（本包仅支持按重量步长向上取整）")

    invoiced = number(ship.get("invoiced_amount"), "invoiced_amount")
    rate_source = text(str(ship.get("rate_source") or ""), "rate_source", maximum=200, allow_empty=True)

    dims = ship.get("dimensions_cm")
    dims_present = dims is not None
    dims_list = []
    if dims_present:
        if not isinstance(dims, list) or len(dims) != 3:
            raise ValueError("dimensions_cm must be a list of exactly 3 numbers")
        dims_list = [positive(item, "dimensions_cm[]") for item in dims]

    divisor_raw = ship.get("volumetric_divisor")
    divisor_known = divisor_raw is not None and str(divisor_raw).strip() != ""
    divisor = positive(divisor_raw, "volumetric_divisor") if divisor_known else None

    # surcharges: an explicit list; EVERY entry must be confirmed to allow a
    # full estimate. Absent or unconfirmed surcharges block a MATCH verdict.
    sur_raw = ship.get("surcharges")
    surcharges_ok = False
    surcharge_total = ZERO
    surcharge_items = []
    unknown_reasons = []
    if sur_raw is None:
        unknown_reasons.append("surcharges_missing（未提供附加费清单，无法确认是否完整）")
    elif not isinstance(sur_raw, list):
        raise ValueError("surcharges must be a list")
    else:
        confirmed_all = True
        for idx, item in enumerate(sur_raw):
            if not isinstance(item, dict):
                raise ValueError("each surcharge must be an object")
            name = text(str(item.get("name") or ""), "surcharge name", maximum=80, allow_empty=True)
            amount = number(item.get("amount"), "surcharge amount")
            confirmed = item.get("confirmed")
            if confirmed is None or confirmed is not True:
                confirmed_all = False
                unknown_reasons.append("unknown_surcharge（附加费 '%s' 未确认，阻断完整差异结论）" % (name or ("#" + str(idx))))
                continue
            surcharge_total += amount
            surcharge_items.append({"name": name, "amount": money(amount), "confirmed": True})
        surcharges_ok = confirmed_all

    # volumetric weight (only computable when dimensions AND divisor known)
    volumetric = None
    if dims_present and not divisor_known:
        unknown_reasons.append("volumetric_divisor_missing（有尺寸但缺体积除数，无法核算体积重）")
    elif dims_present and divisor_known:
        volumetric = (dims_list[0] * dims_list[1] * dims_list[2] / divisor).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP)

    basis = "volume_weight" if volumetric is not None and volumetric > actual else "actual_weight"
    base_weight = volumetric if basis == "volume_weight" else actual

    chargeable = None
    extra_steps = None
    estimate = None
    if not unknown_reasons:
        # chargeable weight rounded UP to the weight step
        step_count = (base_weight / step).to_integral_value(rounding=ROUND_CEILING)
        if step_count * step < base_weight:  # guard against precision drift
            step_count += 1
        chargeable = step_count * step
        extra_raw = chargeable - first_weight
        if extra_raw > ZERO:
            extra_steps = int((extra_raw / step).to_integral_value(rounding=ROUND_CEILING))
            if Decimal(extra_steps) * step < extra_raw:
                extra_steps += 1
        else:
            extra_steps = 0
        estimate = first_price + Decimal(extra_steps) * extra_price + (surcharge_total if surcharges_ok else ZERO)

    status = "UNKNOWN"
    diff = None
    if not unknown_reasons and estimate is not None:
        diff = (invoiced - estimate).quantize(TWO, rounding=ROUND_HALF_UP)
        status = "MATCH" if diff == ZERO else "DIFFERENCE_REVIEW"
    reasons = list(unknown_reasons)
    if not reasons and dims_present and volumetric is None:
        reasons.append("volumetric_not_applicable（尺寸缺除数未计入体积重，按实际重量口径估算）")
    if not reasons and not dims_present:
        reasons.append("dimensions_not_provided（按实际重量口径估算，体积口径未核对）")

    detail = {
        "shipment_id": shipment_id,
        "actual_weight_kg": str(actual),
        "dimensions_cm": [str(x) for x in dims_list] if dims_present else None,
        "volumetric_divisor": str(divisor) if divisor_known else None,
        "volumetric_weight_kg": None if volumetric is None else str(volumetric),
        "weight_basis": basis,
        "weight_step_kg": str(step),
        "first_weight_kg": str(first_weight),
        "chargeable_weight_kg": None if chargeable is None else str(chargeable),
        "extra_steps": extra_steps,
        "first_price": money(first_price),
        "extra_step_price": money(extra_price),
        "surcharges_confirmed": surcharges_ok,
        "surcharges": surcharge_items,
        "surcharge_total": money(surcharge_total) if surcharges_ok else None,
        "estimate_total": None if estimate is None else money(estimate),
        "invoiced_amount": money(invoiced),
        "difference": None if diff is None else money(diff),
        "rate_source": rate_source,
        "rounding_mode": rounding,
        "status": status,
        "reasons": reasons,
        "note": "估算依据：计费重=max(实际重,体积重)按步长向上取整；续重阶梯=（计费重−首重）按步长向上取整；估价=首价+阶梯费+已确认附加费。"
                "未知附加费/缺体积除数时判 UNKNOWN，不判多收费；本技能不代付款、不代申诉。"}
    return detail


def analyze(data):
    if not isinstance(data, dict) or "shipments" not in data:
        raise ValueError("input must contain a shipments array")
    currency = text(str(data.get("currency") or "CNY"), "currency", maximum=6)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    shipments = data["shipments"]
    if not isinstance(shipments, list) or not 1 <= len(shipments) <= 500:
        raise ValueError("shipments must be a list of 1-500 items")
    seen = set()
    results = [audit_shipment(item, seen) for item in shipments]
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    summary = {
        "currency": currency, "shipment_count": len(results),
        "status_counts": counts, "shipments": results}

    rows = [["shipment", "计费重(kg)", "体积重(kg)", "续重阶梯", "估价", "账单", "差异", "状态"]]
    for r in results:
        rows.append([r["shipment_id"],
                     r["chargeable_weight_kg"] if r["chargeable_weight_kg"] is not None else "?",
                     r["volumetric_weight_kg"] if r["volumetric_weight_kg"] is not None else "—",
                     r["extra_steps"] if r["extra_steps"] is not None else "?",
                     r["estimate_total"] if r["estimate_total"] is not None else "?",
                     r["invoiced_amount"],
                     r["difference"] if r["difference"] is not None else "—",
                     r["status"]])
    summary["markdown_summary"] = (
        "# 包裹运费账单核对\n\n口径：计费重=max(实际重,体积重=长宽高/体积除数)，按重量步长向上取整；"
        "续重阶梯=(计费重−首重)按步长向上取整；估价=首价+阶梯费×续重单价+已确认附加费。币种统一 " + currency + "，不混合。\n\n"
        + md_table(rows) +
        "\n\n状态口径：MATCH=账单=估价；DIFFERENCE_REVIEW=账单≠估价（差异为核对提示，不自动认定多收）；"
        "UNKNOWN=附加费未确认/体积规则不完整等，不判多收费；INVALID=结构非法整体拒绝。本技能只读核对：不付款、不代申诉、不改账单。")
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
                          "message": "请对照 references/guide.md 检查必填字段、非负金额、重量步长>0、rounding_mode=ceil 与重复 shipment_id。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
