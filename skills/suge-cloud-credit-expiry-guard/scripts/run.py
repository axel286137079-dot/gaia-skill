#!/usr/bin/env python3
"""Offline cloud credit expiry & overage guard. Standard library only;
per-package scenario estimates, never modifies any cloud resource."""
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
STALE_DAYS = 30
SHORT_EVIDENCE_DAYS = 7


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


def parse_date(value, label, required=True):
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
            return datetime.fromisoformat(raw).date()
        except ValueError:
            raise ValueError(label + " is not a valid datetime") from None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(label + " is not a valid date") from None


def money(value):
    return str(value.quantize(TWO, rounding=ROUND_HALF_UP))


def audit_package(pkg, as_of, currency, seen):
    pkg_id = text(pkg.get("id"), "id", maximum=64)
    if pkg_id in seen:
        raise ValueError("duplicate package id: " + pkg_id)
    seen.add(pkg_id)

    kind = pkg.get("kind")
    kind = text(str(kind or ""), "kind", maximum=16)
    if kind not in ("monetary", "quota"):
        raise ValueError("kind must be monetary or quota")

    unit = pkg.get("unit")
    unit_text = text(str(unit or ""), "unit", maximum=24, allow_empty=True)
    remaining = number(pkg.get("remaining"), "remaining")
    expires = parse_date(pkg.get("expires_at"), "expires_at")

    applicable = pkg.get("applicable_products")
    product = pkg.get("product")
    product_text = text(str(product or ""), "product", maximum=80, allow_empty=True) if product is not None else ""
    applicable_list = None
    if applicable is not None:
        if not isinstance(applicable, list) or len(applicable) > 100:
            raise ValueError("applicable_products must be a list of up to 100 items")
        applicable_list = [text(str(x), "applicable_products[]", maximum=80) for x in applicable]

    usage_raw = pkg.get("usage_per_day")
    usage = None
    if usage_raw is not None and str(usage_raw).strip() != "":
        usage = number(usage_raw, "usage_per_day")
    evidence_days_raw = pkg.get("usage_evidence_days")
    evidence_days = integer(evidence_days_raw, "usage_evidence_days", maximum=Decimal("1000000")) \
        if (evidence_days_raw is not None and str(evidence_days_raw).strip() != "") else None
    observed = parse_date(pkg.get("usage_observed_at"), "usage_observed_at", required=False)
    price_raw = pkg.get("overage_unit_price")
    price = number(price_raw, "overage_unit_price") \
        if (price_raw is not None and str(price_raw).strip() != "") else None
    forecast_raw = pkg.get("forecast_days")
    forecast = integer(forecast_raw, "forecast_days", minimum=Decimal("1"), maximum=Decimal("100000")) \
        if (forecast_raw is not None and str(forecast_raw).strip() != "") else None

    reasons = []
    days_to_expiry = (expires - as_of).days

    # -- scope mismatch --
    status = None
    if kind == "quota" and unit_text == "":
        reasons.append("unit_missing（quota 额度缺少计量单位，无法量化）")
        status = "UNKNOWN"
    if kind == "monetary" and unit_text not in ("", currency):
        reasons.append("unit_kind_mismatch（monetary 额度不应使用非币种单位，金额券不能按其它单位直接抵扣）")
        status = "INELIGIBLE"
    if product_text != "" and applicable_list and product_text not in applicable_list:
        reasons.append("product_not_in_applicable_products（该额度不适用于 %s）" % product_text)
        status = "INELIGIBLE"
    if product_text == "" and applicable_list:
        reasons.append("product_not_specified（未指定产品，适用范围未核对）")

    if status is None and days_to_expiry < 0:
        status = "EXPIRED"
        reasons.append("expired（%s 已到期，过期额度不能再抵扣）" % expires.isoformat())
    elif status is None:
        # -- usage data quality --
        if usage is None:
            status = "UNKNOWN"
            reasons.append("usage_per_day_missing（缺日均用量，不做可信耗尽预测）")
        elif usage > ZERO and (observed is None or (as_of - observed).days > STALE_DAYS):
            status = "UNKNOWN"
            reasons.append("usage_observed_stale（用量观测早于基准 30 天以上，陈旧采样不做可信预测）"
                           if observed is not None else "usage_observed_at_missing（缺用量观测日期）")
        elif usage > ZERO and observed is not None and observed > as_of:
            status = "UNKNOWN"
            reasons.append("usage_observed_future（用量观测日期晚于基准日，采样异常）")
        elif usage > ZERO and (evidence_days is None or evidence_days < SHORT_EVIDENCE_DAYS):
            status = "UNKNOWN"
            reasons.append("evidence_window_short（样本天数不足 %d 天，不做可信预测）" % SHORT_EVIDENCE_DAYS)

    # -- usable horizon & scenario (per package, independent) --
    depletion_days = None
    usable_until = None
    uncovered_days = None
    excess_units = None
    overage_cost = None
    expiry_first = None
    if status is None:
        if days_to_expiry == 0:
            expiry_first = True
            usable_until = Decimal("0.00")
        elif usage is not None and usage > ZERO:
            depletion_days = (remaining / usage).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            expiry_first = Decimal(days_to_expiry) < depletion_days
            usable_until = min(Decimal(days_to_expiry).quantize(Decimal("0.01")), depletion_days)
        else:
            # usage == 0 -> no depletion, usable until expiry
            usable_until = Decimal(days_to_expiry).quantize(Decimal("0.01"))

        if usage is not None and usage > ZERO:
            if forecast is not None:
                uncovered_days = max(ZERO, Decimal(forecast) - usable_until)
                if uncovered_days > ZERO:
                    excess_units = (usage * uncovered_days).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                    if kind == "monetary":
                        overage_cost = excess_units.quantize(TWO, rounding=ROUND_HALF_UP)
                        reasons.append("overage_price_ignored_for_monetary（金额券超额费用即超额金额，不乘单价）" if price is not None else "amount_credit_no_unit_price（金额券超额费用即超额金额）")
                    elif price is not None:
                        overage_cost = (excess_units * price).quantize(TWO, rounding=ROUND_HALF_UP)
                    else:
                        reasons.append("overage_unit_price_missing（缺超额单价，仅给超额量不给费用）")
            else:
                reasons.append("forecast_days_missing（无预测期，不估算超额量）")

        if forecast is not None and usable_until >= Decimal(forecast):
            status = "OK"
        elif expiry_first:
            status = "EXPIRING"
        else:
            status = "DEPLETING" if usage is not None and usage > ZERO else "EXPIRING"
        if usage == ZERO and forecast is None and days_to_expiry > SHORT_EVIDENCE_DAYS:
            status = "OK"

    return {
        "id": pkg_id, "kind": kind,
        "unit": unit_text if kind == "quota" else currency,
        "remaining": str(remaining),
        "expires_at": expires.isoformat(),
        "days_to_expiry": days_to_expiry,
        "applicable_products": applicable_list,
        "product": product_text if product_text else None,
        "usage_per_day": None if usage is None else str(usage),
        "usage_evidence_days": evidence_days,
        "usage_observed_at": None if observed is None else observed.isoformat(),
        "forecast_days": forecast,
        "depletion_days_estimate": None if depletion_days is None else str(depletion_days),
        "expiry_before_depletion": expiry_first,
        "usable_until_days": None if usable_until is None else str(usable_until),
        "uncovered_days": None if uncovered_days is None else str(uncovered_days),
        "candidate_excess_units": None if excess_units is None else str(excess_units),
        "candidate_overage_cost": None if overage_cost is None else money(overage_cost),
        "overage_unit_price": None if price is None else str(price),
        "status": status, "reasons": reasons,
        "note": "单包情景估算：额度逐包独立，不跨包合并/不相加重叠额度；日均恒定是估算假设。"
                "过期额度不能再抵扣；候选超额/费用为估算，不构成账单，本技能不修改任何云资源。"}


def analyze(data):
    if not isinstance(data, dict) or "packages" not in data:
        raise ValueError("input must contain a packages array")
    currency = text(str(data.get("currency") or "CNY"), "currency", maximum=6)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    as_of = parse_date(data.get("as_of") or data.get("as_of_date") or "", "as_of")
    packages = data["packages"]
    if not isinstance(packages, list) or not 1 <= len(packages) <= 500:
        raise ValueError("packages must be a list of 1-500 items")
    seen = set()
    results = [audit_package(item, as_of, currency, seen) for item in packages]
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = {
        "as_of": as_of.isoformat(), "currency": currency, "package_count": len(results),
        "status_counts": counts, "packages": results}

    rows = [["package", "kind/单位", "剩余", "到期", "耗尽候选(天)", "超额量", "超额费用", "状态"]]
    for r in results:
        rows.append([r["id"], (r["kind"] + "/" + r["unit"]), r["remaining"], r["expires_at"],
                     r["depletion_days_estimate"] if r["depletion_days_estimate"] is not None else "—",
                     r["candidate_excess_units"] if r["candidate_excess_units"] is not None else "—",
                     (r["candidate_overage_cost"] + " " + currency) if r["candidate_overage_cost"] is not None else "—",
                     r["status"]])
    summary["markdown_summary"] = (
        "# 云额度到期与超额风险（基准 " + as_of.isoformat() + "）\n\n"
        "单包情景估算：额度逐包独立核算，不跨包合并、不相加重叠额度；日均恒定是估算假设。"
        "耗尽候选=剩余/日均用量；可用天数=min(到期剩余,耗尽候选)；超额候选=日均用量×(预测期−可用天数)（如为正）。\n\n"
        + md_table(rows) +
        "\n\n状态口径：EXPIRING=将先到期（到期后额度作废，剩余期间可能产生超额）；DEPLETING=将先耗尽；"
        "INELIGIBLE=额度范围/单位不适用于该产品；EXPIRED=已到期不能继续抵扣；UNKNOWN=用量缺失/样本过短/观测异常；"
        "OK=预测期内无到期无耗尽。候选超额与费用为估算，不构成账单；本技能不改动任何云资源。")
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
                          "message": "请对照 references/guide.md 检查基准日、包 ID 唯一、kind/unit/币种一致、非负金额与日期字段。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
