#!/usr/bin/env python3
"""Offline LLM API usage / cache-billing reconciliation.

Recomputes expected cost from user-supplied price cards and compares it with the
amount the provider says it charged.  Offline and read-only: never calls a
provider API, never fetches a price page, never touches a billing account.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
SECRET_HINTS = (
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
)
CATEGORIES = ("input", "cache_read", "cache_write", "output", "reasoning")
BATCH_CATEGORIES = ("batch_input", "batch_cache_read", "batch_cache_write", "batch_output", "batch_reasoning")
UNITS = {"per_million_tokens": Decimal("1000000"), "per_1k_tokens": Decimal("1000"), "per_token": Decimal("1")}
# Row statuses that mean "could not be priced / compared".
BLOCKED_ROW_STATUSES = {"UNKNOWN_MODEL", "PRICE_NOT_EFFECTIVE", "PRICE_OVERLAP",
                        "UNPRICED", "FX_MISSING", "CHARGED_NOT_OBSERVED"}


def clean_text(value, label, maximum=200, allow_empty=False):
    if value is None:
        if allow_empty:
            return ""
        raise ValueError(label + " is required")
    if not isinstance(value, str):
        raise ValueError(label + " must be text")
    if CONTROL.search(value):
        raise ValueError(label + " must not contain control characters")
    text = value.strip()
    if len(text) > maximum:
        raise ValueError(label + " exceeds length limit")
    if not text and not allow_empty:
        raise ValueError(label + " must be nonempty text")
    for pattern in SECRET_HINTS:
        if pattern.search(text):
            raise ValueError(label + " looks like a credential; remove it before reconciling")
    return text


def number(value, label, minimum=Decimal("0"), maximum=Decimal("1000000000000000")):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
    return result


def integer(value, label, minimum=Decimal("0"), maximum=Decimal("1000000000000")):
    result = number(value, label, minimum, maximum)
    if result != result.to_integral_value():
        raise ValueError(label + " must be an integer")
    return int(result)


def optional_number(value, label, minimum=Decimal("0")):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return number(value, label, minimum)


def parse_bool(value, label):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n"):
        return False
    raise ValueError(label + " must be a boolean")


def parse_dt(value, label, required=True):
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
        return datetime.combine(date.fromisoformat(raw), datetime.min.time(), tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(label + " is not a valid date") from None


def quant(value, places=2):
    result = Decimal(value).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
    if result == 0:
        result = abs(result)  # avoid "-0.00"
    return str(result)


def parse_policy(policy):
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    tol_abs = number(policy.get("tolerance_abs", 0), "policy.tolerance_abs", Decimal("0"), Decimal("1000000"))
    tol_pct = number(policy.get("tolerance_pct", 0), "policy.tolerance_pct", Decimal("0"), Decimal("1"))
    tz = policy.get("timezone")
    tz_text = clean_text(str(tz), "policy.timezone", maximum=64, allow_empty=True) if tz is not None else ""
    return {"tolerance_abs": tol_abs, "tolerance_pct": tol_pct, "timezone": tz_text}


def parse_fx(item, seen_keys):
    if not isinstance(item, dict):
        raise ValueError("each fx_rate must be an object")
    src = clean_text(item.get("from"), "fx.from", maximum=16).upper()
    dst = clean_text(item.get("to"), "fx.to", maximum=16).upper()
    if src == dst:
        raise ValueError("fx.from and fx.to must differ")
    rate = number(item.get("rate"), "fx.rate", Decimal("0.0000000001"), Decimal("1000000000"))
    source = clean_text(item.get("source"), "fx.source", maximum=120)
    effective_at = parse_dt(item.get("effective_at"), "fx.effective_at")
    key = (src, dst)
    if key in seen_keys:
        raise ValueError("duplicate fx rate for %s->%s" % (src, dst))
    seen_keys.add(key)
    return {"from": src, "to": dst, "rate": rate, "source": source, "effective_at": effective_at}


def parse_price_card(item, seen_keys):
    if not isinstance(item, dict):
        raise ValueError("each price_card must be an object")
    provider = clean_text(item.get("provider"), "price_card.provider", maximum=80).lower()
    model = clean_text(item.get("model"), "price_card.model", maximum=120).lower()
    tier = clean_text(item.get("service_tier"), "price_card.service_tier", maximum=40).lower()
    currency = clean_text(item.get("currency"), "price_card.currency", maximum=16).upper()
    unit = clean_text(item.get("unit", "per_million_tokens"), "price_card.unit", maximum=40).lower()
    if unit not in UNITS:
        raise ValueError("price_card.unit must be one of " + ", ".join(sorted(UNITS)))
    effective_from = parse_dt(item.get("effective_from"), "price_card.effective_from")
    effective_to = parse_dt(item.get("effective_to"), "price_card.effective_to", required=False)
    if effective_to is not None and effective_to < effective_from:
        raise ValueError("price_card.effective_to must not precede effective_from")
    rates_raw = item.get("rates")
    if not isinstance(rates_raw, dict) or not rates_raw:
        raise ValueError("price_card.rates must be a nonempty object")
    rates = {}
    for key, value in rates_raw.items():
        name = clean_text(str(key), "price_card.rates key", maximum=40).lower()
        if name not in CATEGORIES + BATCH_CATEGORIES:
            raise ValueError("unsupported price_card rate key: " + name)
        rates[name] = number(value, "price_card.rates." + name, Decimal("0"), Decimal("1000000"))
    key = (provider, model, tier, effective_from.date().isoformat(),
           effective_to.date().isoformat() if effective_to else None)
    if key in seen_keys:
        raise ValueError("duplicate price_card for %s/%s/%s starting %s" % (provider, model, tier, key[3]))
    seen_keys.add(key)
    return {"provider": provider, "model": model, "service_tier": tier, "currency": currency,
            "unit": unit, "divisor": UNITS[unit], "effective_from": effective_from,
            "effective_to": effective_to, "rates": rates}


def parse_usage(item, seen_ids):
    if not isinstance(item, dict):
        raise ValueError("each usage row must be an object")
    usage_id = clean_text(item.get("usage_id") or item.get("id"), "usage.usage_id", maximum=120)
    if usage_id in seen_ids:
        raise ValueError("duplicate usage_id: " + usage_id)
    seen_ids.add(usage_id)
    day = parse_dt(item.get("date"), "usage.date")
    provider = clean_text(item.get("provider"), "usage.provider", maximum=80).lower()
    model = clean_text(item.get("model"), "usage.model", maximum=120).lower()
    tier = clean_text(item.get("service_tier", "standard"), "usage.service_tier", maximum=40).lower()
    currency = clean_text(item.get("currency"), "usage.currency", maximum=16).upper()
    tokens_raw = item.get("tokens")
    if not isinstance(tokens_raw, dict):
        raise ValueError("usage.tokens must be an object for " + usage_id)
    tokens = {}
    for category in CATEGORIES:
        raw = tokens_raw.get(category, 0)
        if raw is None or (isinstance(raw, str) and raw.strip() == ""):
            raw = 0
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            raise ValueError("usage.tokens.%s must be numeric for %s" % (category, usage_id))
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("usage.tokens.%s must be an integer for %s" % (category, usage_id))
        tokens[category] = int(value)
    charged_raw = item.get("charged_amount")
    charged = None
    if charged_raw is not None and not (isinstance(charged_raw, str) and charged_raw.strip() == ""):
        charged = number(charged_raw, "usage.charged_amount", Decimal("-1000000000000"), Decimal("1000000000000"))
    async_raw = item.get("async_allowed")
    async_allowed = parse_bool(async_raw, "usage.async_allowed") if async_raw is not None else False
    return {"usage_id": usage_id, "date": day, "provider": provider, "model": model,
            "service_tier": tier, "currency": currency, "tokens": tokens,
            "charged_amount": charged, "async_allowed": async_allowed}


def select_card(cards, row):
    """Return (card, problem). problem is None when exactly one card covers the day."""
    candidates = [c for c in cards
                  if c["provider"] == row["provider"] and c["model"] == row["model"]
                  and c["service_tier"] == row["service_tier"]]
    if not candidates:
        return None, "UNKNOWN_MODEL"
    covering = [c for c in candidates
                if c["effective_from"].date() <= row["date"].date()
                and (c["effective_to"] is None or row["date"].date() <= c["effective_to"].date())]
    if not covering:
        return None, "PRICE_NOT_EFFECTIVE"
    if len(covering) > 1:
        return None, "PRICE_OVERLAP"
    return covering[0], None


def rate_key(tier, category):
    if tier == "batch":
        return "batch_" + category
    return category


def price_row(row, card, fx_index):
    """Return (expected_in_price_currency, priced_categories, problem, used_fx)."""
    missing = [c for c in CATEGORIES
               if row["tokens"][c] > 0 and rate_key(row["service_tier"], c) not in card["rates"]]
    if missing:
        return None, {}, "UNPRICED:" + ",".join(missing), None
    total = Decimal("0")
    priced = {}
    for category in CATEGORIES:
        count = row["tokens"][category]
        if count == 0:
            priced[category] = "0.00"
            continue
        rate = card["rates"][rate_key(row["service_tier"], category)]
        amount = (Decimal(count) / card["divisor"]) * rate
        priced[category] = quant(amount, 6)
        total += amount
    if row["currency"] == card["currency"]:
        return total, priced, None, None
    fx = fx_index.get((card["currency"], row["currency"]))
    if fx is None:
        return None, priced, "FX_MISSING:" + card["currency"] + "->" + row["currency"], None
    return total * fx["rate"], priced, None, fx


def evaluate_row(row, cards, fx_index, policy, settlement_currency):
    flags = []
    reasons = []
    card, problem = select_card(cards, row)
    expected = None
    priced = {}
    used_fx = None
    status = None
    if any(row["tokens"][c] < 0 for c in CATEGORIES):
        status = "INVALID"
        reasons.append("token 数量为负，属于无效数据，不得参与计价")
        flags.append("NEGATIVE_TOKENS")
    elif problem is not None:
        status = problem
        reasons.append({"UNKNOWN_MODEL": "价目表中没有该 provider/model/service_tier 的任何价目",
                        "PRICE_NOT_EFFECTIVE": "存在该 provider/model/tier 的价目，但没有任何一条覆盖该用量日期",
                        "PRICE_OVERLAP": "该日期有多条价目同时生效，无法确定唯一单价"}[problem])
        flags.append(problem)
    else:
        expected, priced, problem, used_fx = price_row(row, card, fx_index)
        if problem is not None:
            status = problem.split(":", 1)[0]
            reasons.append("缺少计价依据：" + problem.split(":", 1)[1])
            flags.append(status)
        elif row["charged_amount"] is None:
            status = "CHARGED_NOT_OBSERVED"
            reasons.append("未提供 provider 已收费金额，只能给出期望金额，无法核对")
            flags.append("CHARGED_NOT_OBSERVED")
        else:
            difference = row["charged_amount"] - expected
            allowed = max(policy["tolerance_abs"], expected.copy_abs() * policy["tolerance_pct"])
            if difference.copy_abs() <= allowed:
                status = "MATCH"
                reasons.append("已收费金额与期望金额差异 %s 在容差 %s 内" % (quant(difference), quant(allowed)))
            else:
                status = "VARIANCE"
                reasons.append("已收费金额与期望金额差异 %s 超出容差 %s" % (quant(difference), quant(allowed)))
            if row["currency"] != settlement_currency:
                flags.append("CURRENCY_NOT_SETTLEMENT")
    return {
        "usage_id": row["usage_id"], "date": row["date"].date().isoformat(),
        "provider": row["provider"], "model": row["model"], "service_tier": row["service_tier"],
        "currency": row["currency"], "tokens": dict(row["tokens"]),
        "async_allowed": row["async_allowed"],
        "price_card": None if card is None else {
            "currency": card["currency"], "unit": card["unit"],
            "effective_from": card["effective_from"].date().isoformat(),
            "effective_to": card["effective_to"].date().isoformat() if card["effective_to"] else None},
        "priced_categories": priced,
        "fx": None if used_fx is None else {"from": used_fx["from"], "to": used_fx["to"],
                                            "rate": str(used_fx["rate"]), "source": used_fx["source"],
                                            "effective_at": used_fx["effective_at"].isoformat()},
        "expected_amount": None if expected is None else quant(expected),
        "charged_amount": None if row["charged_amount"] is None else quant(row["charged_amount"]),
        "difference": None if (expected is None or row["charged_amount"] is None)
                      else quant(row["charged_amount"] - expected),
        "status": status, "review_flags": flags, "reasons": reasons,
    }


def summarize_group(rows):
    statuses = [r["status"] for r in rows]
    comparable = [s for s in statuses if s in ("MATCH", "VARIANCE")]
    if statuses and all(s == "INVALID" for s in statuses):
        status = "INVALID"
    elif "INVALID" in statuses:
        status = "PARTIAL"
    elif not comparable:
        status = "UNKNOWN"
    elif len(comparable) < len(statuses):
        status = "PARTIAL"
    elif "VARIANCE" in statuses:
        status = "VARIANCE"
    else:
        status = "MATCH"
    return status


def build_markdown(result):
    rows = [["usage_id", "provider/model", "tier", "币种", "input", "cache_read", "cache_write",
             "output", "reasoning", "期望", "已收费", "差异", "状态"]]
    for row in result["rows"]:
        tokens = row["tokens"]
        rows.append([row["usage_id"], "%s/%s" % (row["provider"], row["model"]), row["service_tier"],
                     row["currency"], tokens["input"], tokens["cache_read"], tokens["cache_write"],
                     tokens["output"], tokens["reasoning"],
                     row["expected_amount"] or "—", row["charged_amount"] or "—",
                     row["difference"] or "—", row["status"]])
    head = ["# LLM API 用量成本与缓存账单复核（基准 %s）\n\n" % result["as_of"]]
    head.append("口径：按日期选择**唯一**生效价目，按 token 类别与单位逐项 Decimal 计价；"
                "缓存命中/未命中、同步/批处理、reasoning 分开计价；缺价、重叠价、未知模型、"
                "负 token、跨币种无汇率一律**不硬算**。\n\n")
    head.append(md_table(rows))
    head.append("\n\n状态分布：" + ", ".join("%s=%d" % (k, v) for k, v in sorted(result["status_counts"].items())) + "。\n")
    totals = result["totals"]
    head.append("\n结算币种 %s 合计：期望 %s，已收费 %s，差异 %s。\n"
                % (result["settlement_currency"], totals["expected"] or "—",
                   totals["charged"] or "—", totals["difference"] or "—"))
    if totals["excluded_currencies"]:
        head.append("未纳入合计的币种（非结算币种）：%s。\n" % ", ".join(totals["excluded_currencies"]))
    cache = result["cache_hit_ratio"]
    if cache["ratio_pct"] is not None:
        head.append("\n缓存命中占比（仅事实）：%s（cache_read %d / (input %d + cache_read %d)）。\n"
                    % (cache["ratio_pct"], cache["cache_read_tokens"], cache["input_tokens"],
                       cache["cache_read_tokens"]))
    else:
        head.append("\n缓存命中占比：无缓存字段，无法计算（NOT_AVAILABLE）。\n")
    batch = result["batch_candidate"]
    if batch["usage_ids"]:
        head.append("用户标明可异步的同步调用候选：%d 条，合计 %d tokens（仅候选量，不承诺折扣）。\n"
                    % (len(batch["usage_ids"]), batch["tokens_total"]))
    else:
        head.append("未标明可异步的调用，本次不给出批处理候选量。\n")
    head.append("\n本技能只做离线复核：不调用任何 provider API、不抓取价格页、不修改账单。")
    return "".join(head)


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    as_of = parse_dt(data.get("as_of"), "as_of")
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    settlement_currency = clean_text(data.get("settlement_currency"), "settlement_currency", maximum=16).upper()
    policy = parse_policy(data.get("policy"))

    fx_raw = data.get("fx_rates")
    if fx_raw is None:
        fx_raw = []
    if not isinstance(fx_raw, list) or len(fx_raw) > 200:
        raise ValueError("fx_rates must be a list of up to 200 items")
    seen_fx = set()
    fx_list = [parse_fx(item, seen_fx) for item in fx_raw]
    fx_index = {(item["from"], item["to"]): item for item in fx_list}

    cards_raw = data.get("price_cards")
    if not isinstance(cards_raw, list) or not 1 <= len(cards_raw) <= 2000:
        raise ValueError("price_cards must be a list of 1-2000 items")
    seen_cards = set()
    cards = [parse_price_card(item, seen_cards) for item in cards_raw]

    usage_raw = data.get("usage")
    if not isinstance(usage_raw, list) or not 1 <= len(usage_raw) <= 20000:
        raise ValueError("usage must be a list of 1-20000 items")
    seen_usage = set()
    usage = [parse_usage(item, seen_usage) for item in usage_raw]

    rows = [evaluate_row(row, cards, fx_index, policy, settlement_currency) for row in usage]

    groups = {}
    for row in rows:
        key = (row["provider"], row["model"], row["service_tier"])
        groups.setdefault(key, []).append(row)

    group_list = []
    for key in sorted(groups):
        members = groups[key]
        comparable = [m for m in members if m["status"] in ("MATCH", "VARIANCE")]
        expected_total = sum((Decimal(m["expected_amount"]) for m in comparable), Decimal("0"))
        charged_total = sum((Decimal(m["charged_amount"]) for m in comparable), Decimal("0"))
        group_flags = sorted({flag for m in members for flag in m["review_flags"]})
        group_list.append({
            "provider": key[0], "model": key[1], "service_tier": key[2],
            "row_count": len(members), "status": summarize_group(members),
            "expected_total": quant(expected_total), "charged_total": quant(charged_total),
            "difference_total": quant(charged_total - expected_total),
            "review_flags": group_flags,
        })

    status_counts = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    group_statuses = [g["status"] for g in group_list]
    if group_statuses and all(s == "INVALID" for s in group_statuses):
        overall = "INVALID"
    elif any(s in ("INVALID", "PARTIAL") for s in group_statuses):
        overall = "PARTIAL"
    elif all(s == "UNKNOWN" for s in group_statuses):
        overall = "UNKNOWN"
    elif "UNKNOWN" in group_statuses:
        overall = "PARTIAL"
    elif "VARIANCE" in group_statuses:
        overall = "VARIANCE"
    else:
        overall = "MATCH"

    settlement_rows = [r for r in rows if r["status"] in ("MATCH", "VARIANCE")
                       and r["currency"] == settlement_currency]
    expected_total = sum((Decimal(r["expected_amount"]) for r in settlement_rows), Decimal("0"))
    charged_total = sum((Decimal(r["charged_amount"]) for r in settlement_rows), Decimal("0"))
    excluded = sorted({r["currency"] for r in rows
                       if r["status"] in ("MATCH", "VARIANCE") and r["currency"] != settlement_currency})

    ratio_rows = [r for r in rows if r["status"] != "INVALID"]
    cache_read = sum(r["tokens"]["cache_read"] for r in ratio_rows)
    cache_input = sum(r["tokens"]["input"] for r in ratio_rows)
    denominator = cache_input + cache_read
    ratio = None if denominator == 0 else quant(Decimal(cache_read) / Decimal(denominator) * 100, 2) + "%"

    batch_ids = [r["usage_id"] for r in rows if r["async_allowed"] and r["service_tier"] != "batch"]
    batch_tokens = sum(sum(r["tokens"].values()) for r in rows
                       if r["async_allowed"] and r["service_tier"] != "batch")

    gaps = [{"usage_id": r["usage_id"], "status": r["status"],
             "reason": r["reasons"][0] if r["reasons"] else ""}
            for r in rows if r["status"] in BLOCKED_ROW_STATUSES or r["status"] == "INVALID"]

    result = {
        "as_of": as_of.isoformat(),
        "settlement_currency": settlement_currency,
        "usage_count": len(rows),
        "status": overall,
        "status_counts": status_counts,
        "groups": group_list,
        "rows": rows,
        "unpriced_usage_ids": [r["usage_id"] for r in rows if r["status"] in ("UNPRICED", "UNKNOWN_MODEL",
                                                                             "PRICE_NOT_EFFECTIVE",
                                                                             "PRICE_OVERLAP", "FX_MISSING")],
        "evidence_gaps": gaps,
        "cache_hit_ratio": {"cache_read_tokens": cache_read, "input_tokens": cache_input,
                            "ratio_pct": ratio},
        "batch_candidate": {"usage_ids": batch_ids, "tokens_total": batch_tokens},
        "totals": {"expected": quant(expected_total), "charged": quant(charged_total),
                   "difference": quant(charged_total - expected_total),
                   "excluded_currencies": excluded},
        "note": "按日期选择唯一生效价目、按 token 类别与单位逐项 Decimal 计价；"
                "缓存命中/未命中、同步/批处理、reasoning 分开计价；缺价、重叠价、未知模型、"
                "负 token、跨币种无汇率一律不硬算；不调用 provider API、不抓取价格页、不修改账单。",
    }
    result["markdown_summary"] = build_markdown(result)
    return result


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
                          "message": "请对照 references/guide.md 检查：基准时间带时区、价目含 source/生效日、"
                                     "usage_id 唯一、token 为非负整数、跨币种需提供含 source/effective_at 的汇率。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
