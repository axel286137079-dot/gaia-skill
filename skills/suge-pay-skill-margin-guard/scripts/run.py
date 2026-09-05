#!/usr/bin/env python3
"""Offline Pay-Skill margin & pricing guard. Standard library only, no network."""
import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_CEILING
from pathlib import Path

REQUIRED = ["display_price", "register_price", "server_price", "avg_input_tokens",
            "avg_output_tokens", "input_price_per_mtok", "output_price_per_mtok",
            "monthly_fixed_cost", "expected_monthly_success_calls"]
OPTIONAL_RATES = ["payment_fee_rate", "refund_rate", "failure_retry_rate", "tax_rate"]
OPTIONAL_COSTS = ["manual_review_cost_per_call", "target_margin_rate"]
TWO = Decimal("0.01")


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


def text(value, label, maximum=40):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(label + " must be nonempty text within length limit")
    return value.strip()


def money(value):
    return str(value.quantize(TWO, rounding=ROUND_HALF_UP))


def parse_optional(data, key, minimum, maximum):
    """Return parsed Decimal when the key holds a non-empty finite value, else None.
    Missing / blank / non-numeric optionals must surface as unknown assumptions."""
    if key not in data or data[key] is None:
        return None
    raw = str(data[key]).strip()
    if raw == "":
        return None
    return number(data[key], key, minimum, maximum)


def evaluate_core(cfg):
    """cfg carries fully-parsed Decimals for a single scenario. Deterministic core."""
    model_cost = (cfg["in_tokens"] * cfg["in_price"] + cfg["out_tokens"] * cfg["out_price"]) / Decimal("1000000")
    retry_rate = cfg["failure_retry_rate"] or Decimal("0")
    model_with_retry = model_cost * (Decimal("1") + retry_rate)
    manual = cfg["manual_cost"] or Decimal("0")
    cost_unit = model_with_retry + manual
    refund = cfg["refund_rate"] or Decimal("0")
    cost_unit_with_refund = cost_unit * (Decimal("1") + refund)
    revenue = cfg["server_price"] * (Decimal("1") - refund)
    fee_charge = cfg["server_price"] * (cfg["fee_rate"] or Decimal("0"))
    tax_charge = cfg["server_price"] * (cfg["tax_rate"] or Decimal("0"))
    variable_cost = cost_unit_with_refund + fee_charge + tax_charge
    contribution = revenue - variable_cost

    margin_pct = None
    if revenue > 0:
        margin_pct = (contribution / revenue * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
    state = "ok"
    if revenue <= 0:
        state = "unprofitable_revenue_zero"
    elif contribution <= 0:
        state = "unprofitable_contribution"

    calls = cfg["expected_monthly_success_calls"]
    monthly_profit = None
    if calls > 0 and contribution is not None:
        monthly_profit = contribution * calls - cfg["monthly_fixed_cost"]
    break_even = None
    if calls == 0:
        break_even = {"monthly_calls": None, "state": "cannot_compute_calls_zero"}
    elif contribution > 0 and cfg["monthly_fixed_cost"] > 0:
        need = (cfg["monthly_fixed_cost"] / contribution).to_integral_value(rounding=ROUND_CEILING)
        break_even = {"monthly_calls": str(int(need)), "state": "reachable"}
    elif contribution <= 0:
        break_even = {"monthly_calls": None, "state": "unreachable_contribution_not_positive"}
    else:
        break_even = {"monthly_calls": "0", "state": "no_fixed_cost"}

    minimum_price = None
    if cfg["target_margin"] is not None:
        # Solve P so that (P*(1-R) - C*(1+R) - P*F - P*T) / (P*(1-R)) == target
        denominator = (Decimal("1") - refund) - (cfg["fee_rate"] or Decimal("0")) \
            - (cfg["tax_rate"] or Decimal("0")) - cfg["target_margin"] * (Decimal("1") - refund)
        if denominator > 0:
            minimum_price = money((cost_unit_with_refund / denominator).quantize(
                Decimal("0.01"), rounding=ROUND_CEILING))

    return {"model_cost": money(model_cost), "model_cost_with_retry": money(model_with_retry),
            "variable_cost_expected": money(variable_cost),
            "revenue_expected": money(revenue),
            "gross_margin_pct": None if margin_pct is None else str(margin_pct) + "%",
            "monthly_profit": None if monthly_profit is None else money(monthly_profit),
            "break_even": break_even,
            "state": state,
            "minimum_server_price_to_target": minimum_price,
            "fee_charge_included": cfg["fee_rate"] is not None,
            "tax_charge_included": cfg["tax_rate"] is not None,
            "manual_cost_included": cfg["manual_cost"] is not None,
            "refund_included": cfg["refund_rate"] is not None,
            "retry_included": cfg["failure_retry_rate"] is not None}


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise ValueError("missing required field: " + ",".join(missing))

    currency = text(str(data["currency"]) if "currency" in data and data["currency"] is not None else "CNY",
                    "currency")
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")

    # --- parse required scalars -------------------------------------------------
    display_price = number(data["display_price"], "display_price", maximum=Decimal("1000000000"))
    register_price = number(data["register_price"], "register_price", maximum=Decimal("1000000000"))
    server_price = number(data["server_price"], "server_price", maximum=Decimal("1000000000"))
    in_tokens = number(data["avg_input_tokens"], "avg_input_tokens", maximum=Decimal("10000000000"))
    out_tokens = number(data["avg_output_tokens"], "avg_output_tokens", maximum=Decimal("10000000000"))
    in_price = number(data["input_price_per_mtok"], "input_price_per_mtok", maximum=Decimal("1000000"))
    out_price = number(data["output_price_per_mtok"], "output_price_per_mtok", maximum=Decimal("1000000"))
    monthly_fixed = number(data["monthly_fixed_cost"], "monthly_fixed_cost", maximum=Decimal("1000000000000"))
    calls = number(data["expected_monthly_success_calls"], "expected_monthly_success_calls",
                   maximum=Decimal("1000000000000"))
    if calls != calls.to_integral_value():
        raise ValueError("expected_monthly_success_calls must be an integer")

    # --- parse optionals (missing stays None => unknown_assumptions) -----------
    fee_rate = parse_optional(data, "payment_fee_rate", Decimal("0"), Decimal("0.99"))
    refund_rate = parse_optional(data, "refund_rate", Decimal("0"), Decimal("1"))
    retry_rate = parse_optional(data, "failure_retry_rate", Decimal("0"), Decimal("0.99"))
    tax_rate = parse_optional(data, "tax_rate", Decimal("0"), Decimal("0.99"))
    manual_cost = parse_optional(data, "manual_review_cost_per_call", Decimal("0"), Decimal("1000000000"))
    target_margin = parse_optional(data, "target_margin_rate", Decimal("0"), Decimal("0.99"))

    unknown = [k for k in OPTIONAL_RATES + OPTIONAL_COSTS
               if (k not in data or data[k] is None or str(data[k]).strip() == "")]
    complete = not unknown

    prices = {"display": display_price, "register": register_price, "server": server_price}
    consistent = len({money(v) for v in prices.values()}) == 1

    def base_cfg(price_mult, call_mult, refund_mult, retry_mult):
        return {"in_tokens": in_tokens, "out_tokens": out_tokens,
                "in_price": in_price * price_mult, "out_price": out_price * price_mult,
                "server_price": server_price, "monthly_fixed_cost": monthly_fixed,
                "expected_monthly_success_calls": int(calls * call_mult),
                "fee_rate": fee_rate, "refund_rate": None if refund_rate is None else min(refund_rate * refund_mult, Decimal("1")),
                "failure_retry_rate": None if retry_rate is None else min(retry_rate * retry_mult, Decimal("0.99")),
                "tax_rate": tax_rate, "manual_cost": manual_cost, "target_margin": target_margin}

    scenarios = {}
    for name, (pm, cm, rm, tm) in {
        "base": (Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1")),
        "pessimistic": (Decimal("1.5"), Decimal("0.5"), Decimal("2"), Decimal("2")),
        "optimistic": (Decimal("0.9"), Decimal("1.5"), Decimal("0.5"), Decimal("0.5")),
    }.items():
        cfg = base_cfg(pm, cm, rm, tm)
        result = evaluate_core(cfg)
        result["estimate_scope"] = "COMPLETE_COST_MODEL" if complete else "KNOWN_COSTS_ONLY_LOWER_BOUND"
        result["profitability_claim_allowed"] = bool(complete and consistent)
        scenarios[name] = {
            "multipliers": {"model_price": str(pm), "expected_calls": str(cm),
                            "refund": str(rm), "failure_retry": str(tm)},
            "result": result}

    warnings = []
    if unknown:
        warnings.append("缺少的可选成本项已列入 unknown_assumptions；数值情景仅按已知成本计算，是成本下界估算，不能据此宣称利润。")
    if not consistent:
        warnings.append("展示价/注册价/服务端价不一致，上线前应先对齐，否则不能声称按展示价收费。")

    return {
        "skill": "suge-pay-skill-margin-guard",
        "version": "1.0.1",
        "completeness": "COMPLETE" if complete else "INCOMPLETE",
        "profitability_claim_allowed": bool(complete and consistent),
        "currency": currency,
        "input_echo": {
            "display_price": str(display_price), "register_price": str(register_price),
            "server_price": str(server_price), "avg_input_tokens": str(int(in_tokens)),
            "avg_output_tokens": str(int(out_tokens)), "input_price_per_mtok": str(in_price),
            "output_price_per_mtok": str(out_price), "monthly_fixed_cost": money(monthly_fixed),
            "expected_monthly_success_calls": str(int(calls)),
            "payment_fee_rate": None if fee_rate is None else str(fee_rate),
            "refund_rate": None if refund_rate is None else str(refund_rate),
            "failure_retry_rate": None if retry_rate is None else str(retry_rate),
            "tax_rate": None if tax_rate is None else str(tax_rate),
            "manual_review_cost_per_call": None if manual_cost is None else money(manual_cost),
            "target_margin_rate": None if target_margin is None else str(target_margin)},
        "price_consistency": {"status": "RED" if not consistent else "GREEN",
                              "prices": {k: money(v) for k, v in prices.items()}},
        "formula_note": ("model_cost=((in_tokens*in_price+out_tokens*out_price)/1e6); "
                         "variable_cost=(model_with_retry+manual)*(1+refund)+fee+tax; "
                         "margin=(revenue-variable_cost)/revenue; "
                         "break_even=monthly_fixed/(revenue-variable_cost)"),
        "unknown_assumptions": unknown,
        "scenarios": scenarios,
        "warnings": warnings,
        "note": "确定性测算，非投资建议，不保证盈利，不构成税务或法律意见；缺失项保持未知，相关数字仅是已知成本下界。"
    }


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
                          "message": "请对照 references/guide.md 检查必填字段、数字范围与费率上限。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
