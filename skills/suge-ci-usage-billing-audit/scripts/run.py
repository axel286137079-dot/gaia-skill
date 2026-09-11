#!/usr/bin/env python3
"""Offline CI runner & artifact usage billing audit.

Re-checks gross-discount-net arithmetic, attributes cost by repository / workflow
/ SKU / OS / runner, projects a straight-line month-end run-rate and quantifies
artifact storage exposure.  Offline and read-only: never logs in to a CI
provider, never calls an API, never deletes an artifact, never edits a workflow.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OFFSET_TZ = re.compile(r"^([+-])(\d{2}):(\d{2})$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
SECRET_HINTS = (
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
)
GIB = Decimal("1073741824")
UNIT_ALIASES = {"minutes": "minutes", "minute": "minutes", "gb_days": "gb_days",
                "gb-days": "gb_days", "gbdays": "gb_days", "gigabyte_days": "gb_days"}
SEVERITY = ["INVALID", "BILLING_DIFFERENCE", "BUDGET_RISK", "PARTIAL", "UNKNOWN", "MATCH"]


def clean_text(value, label, maximum=300, allow_empty=False):
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
            raise ValueError(label + " looks like a credential; remove it before auditing")
    return text


def optional_text(value, label, maximum=300):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return clean_text(value, label, maximum)


def number(value, label, minimum=Decimal("-1000000000000"), maximum=Decimal("1000000000000")):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
    return result


def nonneg(value, label, maximum=Decimal("1000000000000")):
    return number(value, label, Decimal("0"), maximum)


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


def parse_date_only(value, label):
    if not isinstance(value, str) or not ISO_DATE.match(value.strip()):
        raise ValueError(label + " must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(label + " is not a valid date") from None


def resolve_tz(name):
    text = clean_text(name, "period.account_timezone", maximum=64)
    match = OFFSET_TZ.match(text)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        delta = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
        return timezone(sign * delta)
    if ZoneInfo is None:
        raise ValueError("period.account_timezone must be a fixed offset like +08:00")
    try:
        return ZoneInfo(text)
    except Exception:
        raise ValueError("period.account_timezone is not a known IANA timezone: " + text) from None


def quant(value, places=2):
    result = Decimal(value).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
    if result == 0:
        result = abs(result)
    return str(result)


def parse_period(period):
    if not isinstance(period, dict):
        raise ValueError("period must be an object")
    start = parse_date_only(period.get("start_date"), "period.start_date")
    end = parse_date_only(period.get("end_date"), "period.end_date")
    if end < start:
        raise ValueError("period.end_date must not precede start_date")
    tz = resolve_tz(period.get("account_timezone"))
    return {"start_date": start, "end_date": end, "account_timezone": str(period.get("account_timezone")),
            "tz": tz}


def parse_plan(plan):
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    currency = clean_text(plan.get("currency"), "plan.currency", maximum=16).upper()
    included_minutes = nonneg(plan.get("included_minutes", 0), "plan.included_minutes", Decimal("100000000"))
    included_gb_days = nonneg(plan.get("included_storage_gb_days", 0),
                              "plan.included_storage_gb_days", Decimal("100000000"))
    price_minute = nonneg(plan.get("overage_price_per_minute", 0), "plan.overage_price_per_minute")
    price_gb_day = nonneg(plan.get("overage_price_per_gb_day", 0), "plan.overage_price_per_gb_day")
    budget_raw = plan.get("budget_amount")
    budget = nonneg(budget_raw, "plan.budget_amount") if budget_raw is not None \
        and str(budget_raw).strip() != "" else None
    known_raw = plan.get("known_skus")
    known_skus = None
    if known_raw is not None:
        if not isinstance(known_raw, list) or not known_raw:
            raise ValueError("plan.known_skus must be a nonempty list when provided")
        known_skus = {clean_text(str(entry), "plan.known_skus[]", maximum=160) for entry in known_raw}
    return {"currency": currency, "included_minutes": included_minutes,
            "included_storage_gb_days": included_gb_days, "overage_price_per_minute": price_minute,
            "overage_price_per_gb_day": price_gb_day, "budget_amount": budget, "known_skus": known_skus}


def parse_policy(policy):
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    tolerance = nonneg(policy.get("tolerance_abs", 0), "policy.tolerance_abs", Decimal("1000000"))
    quota_raw = policy.get("quota_applied_in_discount")
    quota_in_discount = parse_bool(quota_raw, "policy.quota_applied_in_discount") \
        if quota_raw is not None else False
    tz = policy.get("timezone")
    tz_text = clean_text(str(tz), "policy.timezone", maximum=64, allow_empty=True) if tz is not None else ""
    return {"tolerance_abs": tolerance, "quota_applied_in_discount": quota_in_discount, "timezone": tz_text}


def parse_line(item, seen_ids, period):
    if not isinstance(item, dict):
        raise ValueError("each billing line must be an object")
    line_id = clean_text(item.get("line_id") or item.get("id"), "line.line_id", maximum=120)
    if line_id in seen_ids:
        raise ValueError("duplicate line_id: " + line_id)
    seen_ids.add(line_id)
    when = parse_dt(item.get("date"), "line.date")
    if when.tzinfo is None:
        raise ValueError("line.date must be timezone-aware for " + line_id)
    product = clean_text(item.get("product"), "line.product", maximum=80)
    sku = clean_text(item.get("sku"), "line.sku", maximum=160)
    unit_raw = clean_text(item.get("unit_type"), "line.unit_type", maximum=40).lower()
    if unit_raw not in UNIT_ALIASES:
        raise ValueError("line.unit_type must be one of " + ", ".join(sorted(set(UNIT_ALIASES.values()))))
    unit_type = UNIT_ALIASES[unit_raw]
    quantity = nonneg(item.get("quantity"), "line.quantity")
    gross = number(item.get("gross_amount"), "line.gross_amount")
    discount = number(item.get("discount_amount", 0), "line.discount_amount")
    net = number(item.get("net_amount"), "line.net_amount")
    currency = clean_text(item.get("currency"), "line.currency", maximum=16).upper()
    return {"line_id": line_id, "date": when, "product": product, "sku": sku, "unit_type": unit_type,
            "quantity": quantity, "gross_amount": gross, "discount_amount": discount, "net_amount": net,
            "currency": currency,
            "repository": optional_text(item.get("repository"), "line.repository", 200),
            "workflow_path": optional_text(item.get("workflow_path"), "line.workflow_path", 300),
            "runner_type": optional_text(item.get("runner_type"), "line.runner_type", 60),
            "os": optional_text(item.get("os"), "line.os", 60),
            "local_date": when.astimezone(period["tz"]).date()}


def parse_artifact(item, seen_ids):
    if not isinstance(item, dict):
        raise ValueError("each artifact must be an object")
    artifact_id = clean_text(item.get("artifact_id") or item.get("id"), "artifact.artifact_id", maximum=120)
    if artifact_id in seen_ids:
        raise ValueError("duplicate artifact_id: " + artifact_id)
    seen_ids.add(artifact_id)
    size_bytes = nonneg(item.get("size_bytes"), "artifact.size_bytes", Decimal("1000000000000000"))
    created_at = parse_dt(item.get("created_at"), "artifact.created_at")
    expires_at = parse_dt(item.get("expires_at"), "artifact.expires_at")
    retention_days = nonneg(item.get("retention_days", 0), "artifact.retention_days", Decimal("3650"))
    return {"artifact_id": artifact_id,
            "repository": optional_text(item.get("repository"), "artifact.repository", 200),
            "workflow_path": optional_text(item.get("workflow_path"), "artifact.workflow_path", 300),
            "size_bytes": size_bytes, "size_gb": size_bytes / GIB, "created_at": created_at,
            "expires_at": expires_at, "retention_days": retention_days}


def audit_line(line, plan, period, policy):
    flags = []
    reasons = []
    in_period = period["start_date"] <= line["local_date"] <= period["end_date"]
    if not in_period:
        flags.append("OUT_OF_PERIOD")
    if line["currency"] != plan["currency"]:
        flags.append("CURRENCY_MISMATCH")
    if plan["known_skus"] is not None and line["sku"] not in plan["known_skus"]:
        flags.append("UNKNOWN_SKU")
    expected_net = line["gross_amount"] - line["discount_amount"]
    delta = line["net_amount"] - expected_net
    arithmetic_ok = delta.copy_abs() <= policy["tolerance_abs"]
    if not arithmetic_ok:
        flags.append("ARITHMETIC_MISMATCH")
    if "OUT_OF_PERIOD" in flags:
        status = "OUT_OF_PERIOD"
        reasons.append("按账户时区换算后该行日期 %s 不在账期 %s ~ %s 内"
                       % (line["local_date"].isoformat(), period["start_date"].isoformat(),
                          period["end_date"].isoformat()))
    elif "CURRENCY_MISMATCH" in flags:
        status = "CURRENCY_MISMATCH"
        reasons.append("该行币种 %s 与套餐币种 %s 不一致，不能直接并入合计"
                       % (line["currency"], plan["currency"]))
    elif "UNKNOWN_SKU" in flags:
        status = "UNKNOWN_SKU"
        reasons.append("SKU「%s」不在用户声明的已知 SKU 清单中，无法判断计价口径" % line["sku"])
    elif not arithmetic_ok:
        status = "ARITHMETIC_MISMATCH"
        reasons.append("gross %s − discount %s = %s，与账单 net %s 相差 %s，超出容差 %s"
                       % (quant(line["gross_amount"]), quant(line["discount_amount"]),
                          quant(expected_net), quant(line["net_amount"]), quant(delta),
                          quant(policy["tolerance_abs"])))
    else:
        status = "MATCH"
        reasons.append("gross − discount = net 复核一致（差异 %s）" % quant(delta))
    return {
        "line_id": line["line_id"], "date_utc": line["date"].isoformat(),
        "local_date": line["local_date"].isoformat(), "product": line["product"], "sku": line["sku"],
        "unit_type": line["unit_type"], "quantity": quant(line["quantity"], 4),
        "gross_amount": quant(line["gross_amount"]), "discount_amount": quant(line["discount_amount"]),
        "net_amount": quant(line["net_amount"]), "expected_net": quant(expected_net),
        "arithmetic_difference": quant(delta), "currency": line["currency"],
        "repository": line["repository"], "workflow_path": line["workflow_path"],
        "runner_type": line["runner_type"], "os": line["os"],
        "status": status, "review_flags": sorted(set(flags)), "reasons": reasons,
    }


def aggregate(rows, key):
    buckets = {}
    for row in rows:
        name = row[key] if row[key] is not None else "(未标注)"
        entry = buckets.setdefault(name, {"key": name, "line_count": 0, "net_total": Decimal("0"),
                                          "minutes": Decimal("0"), "gb_days": Decimal("0")})
        entry["line_count"] += 1
        entry["net_total"] += Decimal(row["net_amount"])
        if row["unit_type"] == "minutes":
            entry["minutes"] += Decimal(row["quantity"])
        else:
            entry["gb_days"] += Decimal(row["quantity"])
    out = []
    for name in sorted(buckets):
        entry = buckets[name]
        out.append({"key": entry["key"], "line_count": entry["line_count"],
                    "net_total": quant(entry["net_total"]), "minutes": quant(entry["minutes"], 4),
                    "gb_days": quant(entry["gb_days"], 4)})
    return out


def build_markdown(result):
    rows = [["line_id", "本地日期", "SKU", "单位", "数量", "gross", "discount", "net", "复核net",
             "仓库", "状态"]]
    for row in result["lines"]:
        rows.append([row["line_id"], row["local_date"], row["sku"], row["unit_type"], row["quantity"],
                     row["gross_amount"], row["discount_amount"], row["net_amount"],
                     row["expected_net"], row["repository"] or "—", row["status"]])
    head = ["# CI Runner 与 Artifact 用量账单审计（账期 %s ~ %s，账户时区 %s）\n\n"
            % (result["period"]["start_date"], result["period"]["end_date"],
               result["period"]["account_timezone"])]
    head.append("口径：账单行日期按 UTC 记录，**按账户时区换算后再判定是否落在账期内**；"
                "`gross − discount = net` 独立复核；额度是否已在 discount 中体现**由用户声明**，"
                "声明为是时不再二次抵扣；月末值为**直线情景**，不是预测。\n\n")
    head.append(md_table(rows))
    head.append("\n\n行状态分布：" + ", ".join("%s=%d" % (k, v) for k, v in sorted(result["status_counts"].items())) + "。\n")
    head.append("\n总体判定：**%s**。\n" % result["status"])
    head.append("\n账期内净额合计（%s）：%s；月末直线情景：%s（已过 %d / 共 %d 天）。\n"
                % (result["plan_currency"], result["totals"]["net_total"],
                   result["run_rate"]["month_end_run_rate"] or "—",
                   result["run_rate"]["elapsed_days"], result["run_rate"]["total_days"]))
    overage = result["overage_estimate"]
    if overage["applicable"]:
        head.append("超额估算（按用户声明额度未被折扣覆盖）：分钟超额 %s，存储超额 %s GB-天，估算费用 %s。\n"
                    % (overage["overage_minutes"], overage["overage_gb_days"], overage["estimated_cost"]))
    else:
        head.append("额度已声明包含在 discount 中，**不做二次抵扣**，因此不给出超额估算。\n")
    head.append("\nArtifact 存储暴露：在存 %d 个，合计 %s GB-天。\n"
                % (result["artifacts"]["stored_count"], result["artifacts"]["exposure_gb_days"]))
    head.append("\n本技能不登录 CI 平台、不调用 API、不删除 Artifact、不修改 workflow 或预算。")
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
    period = parse_period(data.get("period"))
    plan = parse_plan(data.get("plan"))
    policy = parse_policy(data.get("policy"))

    lines_raw = data.get("billing_lines")
    if not isinstance(lines_raw, list) or not 1 <= len(lines_raw) <= 50000:
        raise ValueError("billing_lines must be a list of 1-50000 items")
    seen_lines = set()
    lines = [parse_line(item, seen_lines, period) for item in lines_raw]

    artifacts_raw = data.get("artifacts")
    if artifacts_raw is None:
        artifacts_raw = []
    if not isinstance(artifacts_raw, list) or len(artifacts_raw) > 20000:
        raise ValueError("artifacts must be a list of up to 20000 items")
    seen_artifacts = set()
    artifacts = [parse_artifact(item, seen_artifacts) for item in artifacts_raw]

    rows = [audit_line(line, plan, period, policy) for line in lines]

    status_counts = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    countable = [row for row in rows if row["status"] not in ("OUT_OF_PERIOD", "CURRENCY_MISMATCH")]
    net_total = sum((Decimal(row["net_amount"]) for row in countable), Decimal("0"))
    minutes_total = sum((Decimal(row["quantity"]) for row in countable if row["unit_type"] == "minutes"),
                        Decimal("0"))
    gb_days_total = sum((Decimal(row["quantity"]) for row in countable if row["unit_type"] == "gb_days"),
                        Decimal("0"))

    as_of_local = as_of.astimezone(period["tz"])
    total_days = (period["end_date"] - period["start_date"]).days + 1
    run_rate_flags = []
    if as_of_local.date() < period["start_date"]:
        elapsed_days = 0
        run_rate = None
        run_rate_status = "UNKNOWN"
        run_rate_flags.append("AS_OF_BEFORE_PERIOD")
    else:
        effective_end = min(as_of_local.date(), period["end_date"])
        elapsed_days = (effective_end - period["start_date"]).days + 1
        elapsed_days = max(elapsed_days, 1)
        run_rate = net_total / Decimal(elapsed_days) * Decimal(total_days)
        if as_of_local.date() > period["end_date"]:
            run_rate_status = "PERIOD_CLOSED"
            run_rate_flags.append("PERIOD_CLOSED")
        else:
            run_rate_status = "IN_PROGRESS"

    budget_risk = False
    if run_rate is not None and plan["budget_amount"] is not None and run_rate > plan["budget_amount"]:
        budget_risk = True

    if policy["quota_applied_in_discount"]:
        overage = {"applicable": False, "overage_minutes": None, "overage_gb_days": None,
                   "estimated_cost": None, "reason": "用户声明额度已体现在 discount 中，禁止二次抵扣"}
    else:
        overage_minutes = max(Decimal("0"), minutes_total - plan["included_minutes"])
        overage_gb_days = max(Decimal("0"), gb_days_total - plan["included_storage_gb_days"])
        cost = (overage_minutes * plan["overage_price_per_minute"]
                + overage_gb_days * plan["overage_price_per_gb_day"])
        overage = {"applicable": True, "overage_minutes": quant(overage_minutes, 4),
                   "overage_gb_days": quant(overage_gb_days, 4), "estimated_cost": quant(cost),
                   "reason": "按用户提供的额度与单价做直线估算，不是账单金额"}

    stored = []
    expired = []
    artifact_rows = []
    exposure_gb_days = Decimal("0")
    for artifact in artifacts:
        days_remaining = Decimal(str((artifact["expires_at"].date() - as_of_local.date()).days))
        implied_expiry = artifact["created_at"] + timedelta(days=float(artifact["retention_days"]))
        flags = []
        if days_remaining <= 0:
            flags.append("EXPIRED")
            expired.append(artifact["artifact_id"])
            artifact_exposure = Decimal("0")
        else:
            stored.append(artifact["artifact_id"])
            artifact_exposure = artifact["size_gb"] * days_remaining
            exposure_gb_days += artifact_exposure
        if implied_expiry < artifact["expires_at"]:
            flags.append("RETENTION_NOT_RETROACTIVE")
        artifact_rows.append({
            "artifact_id": artifact["artifact_id"], "repository": artifact["repository"],
            "size_gb": quant(artifact["size_gb"], 4), "created_at": artifact["created_at"].isoformat(),
            "expires_at": artifact["expires_at"].isoformat(), "retention_days": quant(artifact["retention_days"], 2),
            "days_remaining": int(days_remaining), "exposure_gb_days": quant(artifact_exposure, 4),
            "implied_expiry_from_retention": implied_expiry.isoformat(),
            "status": "EXPIRED" if days_remaining <= 0 else "STORED",
            "review_flags": sorted(set(flags)),
        })

    if not countable:
        overall = "INVALID"
    elif status_counts.get("ARITHMETIC_MISMATCH"):
        overall = "BILLING_DIFFERENCE"
    elif budget_risk:
        overall = "BUDGET_RISK"
    elif any(row["status"] in ("UNKNOWN_SKU", "CURRENCY_MISMATCH", "OUT_OF_PERIOD") for row in rows):
        overall = "PARTIAL"
    elif run_rate is None:
        overall = "UNKNOWN"
    else:
        overall = "MATCH"

    result = {
        "as_of": as_of.isoformat(),
        "as_of_local": as_of_local.isoformat(),
        "period": {"start_date": period["start_date"].isoformat(),
                   "end_date": period["end_date"].isoformat(),
                   "account_timezone": period["account_timezone"]},
        "plan_currency": plan["currency"],
        "line_count": len(rows),
        "in_period_countable_lines": len(countable),
        "status": overall,
        "status_counts": status_counts,
        "lines": rows,
        "totals": {"net_total": quant(net_total), "gross_total": quant(sum(
            (Decimal(row["gross_amount"]) for row in countable), Decimal("0"))),
            "discount_total": quant(sum((Decimal(row["discount_amount"]) for row in countable),
                                        Decimal("0"))),
            "minutes_total": quant(minutes_total, 4), "gb_days_total": quant(gb_days_total, 4)},
        "by_repository": aggregate(countable, "repository"),
        "by_workflow": aggregate(countable, "workflow_path"),
        "by_sku": aggregate(countable, "sku"),
        "by_os": aggregate(countable, "os"),
        "by_runner_type": aggregate(countable, "runner_type"),
        "run_rate": {"status": run_rate_status, "elapsed_days": elapsed_days,
                     "total_days": total_days, "days_remaining": max(total_days - elapsed_days, 0),
                     "month_end_run_rate": None if run_rate is None else quant(run_rate),
                     "budget_amount": None if plan["budget_amount"] is None
                                      else quant(plan["budget_amount"]),
                     "budget_risk": budget_risk, "review_flags": run_rate_flags,
                     "disclaimer": "直线情景：按已过天数的日均净额外推到账期末，**不是预测**，"
                                   "也未计入后续用量、折扣或额度变化"},
        "overage_estimate": overage,
        "artifacts": {"stored_count": len(stored), "expired_count": len(expired),
                      "exposure_gb_days": quant(exposure_gb_days, 4), "items": artifact_rows},
        "note": "账单行日期按 UTC 记录，按账户时区换算后再判定是否落在账期内；"
                "gross − discount = net 独立复核；额度是否已在 discount 中体现由用户声明，"
                "声明为是时不做二次抵扣；月末值为直线情景不是预测；Artifact 保留期调整不追溯既有对象。"
                "不登录 CI 平台、不调用 API、不删除 Artifact、不修改 workflow 或预算。",
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
                          "message": "请对照 references/guide.md 检查：基准时间带时区、period 含合法 IANA 时区、"
                                     "line_id 唯一、账单行日期带时区、unit_type 为 minutes 或 gb_days、"
                                     "币种与数值合法。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
