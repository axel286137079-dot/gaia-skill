#!/usr/bin/env python3
"""Offline object-storage lifecycle change preview (scenario only).
Maps rules onto aggregated object cohorts using user-supplied policy profile
and price table; never calls cloud APIs, never changes lifecycle nor deletes
objects.  Only gives hit ranges, cost candidates and REVIEW hints."""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

TWO = Decimal("0.01")
ZERO = Decimal("0")
GB = Decimal(2) ** 30
ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?$")


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


def integer(value, label, minimum=ZERO, maximum=Decimal("1000000000000")):
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


def days_between(later, earlier):
    return (later - earlier).days


def parse_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("policy_profile must be an object")
    allowed = {"min_billable_bytes", "minimum_storage_days", "small_object_threshold_bytes",
               "conflict_priority", "time_rounding", "note"}
    unknown = set(profile) - allowed
    if unknown:
        raise ValueError("policy_profile contains unsupported keys: " + ",".join(sorted(unknown)))
    note = profile.get("note")
    out = {
        "min_billable_bytes": integer(profile.get("min_billable_bytes", "0"), "min_billable_bytes",
                                      maximum=Decimal("1000000000000")),
        "minimum_storage_days": integer(profile.get("minimum_storage_days", "0"), "minimum_storage_days",
                                        maximum=Decimal("1000000")),
        "small_object_threshold_bytes": integer(profile.get("small_object_threshold_bytes", "0"),
                                                "small_object_threshold_bytes", maximum=Decimal("1000000000000")),
        "conflict_priority": text(str(profile.get("conflict_priority") or "review"), "conflict_priority",
                                  maximum=24),
        "time_rounding": text(str(profile.get("time_rounding") or "day"), "time_rounding", maximum=16),
        "note": text(str(note or ""), "note", maximum=160, allow_empty=True) if note is not None else None,
    }
    if out["conflict_priority"] not in ("review", "earliest", "latest", "expiration_first", "transition_first"):
        raise ValueError("conflict_priority must be review/earliest/latest/expiration_first/transition_first")
    return out


def parse_price_table(table):
    if not isinstance(table, dict):
        raise ValueError("price_table must be an object")
    effective = parse_date(table.get("effective_at"), "effective_at", required=False)
    source = table.get("source")
    source_text = text(str(source or ""), "source", maximum=160, allow_empty=True) if source is not None else None
    classes = table.get("classes")
    if classes is None:
        raise ValueError("price_table.classes is required (user-supplied rates; missing class -> usage only)")
    if not isinstance(classes, dict):
        raise ValueError("price_table.classes must be an object keyed by storage class")
    parsed = {}
    for class_name, rates in classes.items():
        name = text(str(class_name), "class name", maximum=60)
        if not isinstance(rates, dict):
            raise ValueError("rates for class " + name + " must be an object")
        parsed[name] = {}
        storage_raw = rates.get("storage_gb_month")
        if storage_raw is not None and str(storage_raw).strip() != "":
            parsed[name]["storage_gb_month"] = number(storage_raw, "storage_gb_month")
        req_raw = rates.get("transition_per_1000_objects")
        if req_raw is not None and str(req_raw).strip() != "":
            parsed[name]["transition_per_1000_objects"] = number(req_raw, "transition_per_1000_objects")
    return {"effective_at": effective, "source": source_text, "classes": parsed}


def parse_filters(raw):
    """Optional rule filter: prefix / tags / size range on cohort level."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("rule.filters must be an object or null")
    allowed = {"prefix", "tags", "min_bytes", "max_bytes"}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError("rule.filters unsupported keys: " + ",".join(sorted(unknown)))
    out = {}
    if raw.get("prefix") is not None:
        out["prefix"] = text(str(raw["prefix"]), "prefix", maximum=200)
    if raw.get("tags") is not None:
        tags = raw["tags"]
        if not isinstance(tags, list) or len(tags) > 20:
            raise ValueError("rule.filters.tags must be a list of up to 20 items")
        out["tags"] = [text(str(t), "tag", maximum=80) for t in tags]
    if raw.get("min_bytes") is not None and str(raw["min_bytes"]).strip() != "":
        out["min_bytes"] = integer(raw["min_bytes"], "min_bytes", maximum=Decimal("1000000000000"))
    if raw.get("max_bytes") is not None and str(raw["max_bytes"]).strip() != "":
        out["max_bytes"] = integer(raw["max_bytes"], "max_bytes", maximum=Decimal("1000000000000"))
    if "min_bytes" in out and "max_bytes" in out and out["min_bytes"] > out["max_bytes"]:
        raise ValueError("rule.filters.min_bytes must not exceed max_bytes")
    return out


def parse_rule(rule, seen_rules):
    rule_id = text(rule.get("rule_id"), "rule_id", maximum=80)
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", rule_id):
        raise ValueError("rule_id must be a plain identifier")
    if rule_id in seen_rules:
        raise ValueError("duplicate rule_id: " + rule_id)
    seen_rules.add(rule_id)
    action = text(str(rule.get("action") or ""), "action", maximum=16)
    if action not in ("transition", "expiration"):
        raise ValueError("rule.action must be transition or expiration")
    days = integer(rule.get("days"), "days", minimum=Decimal("0"), maximum=Decimal("100000"))
    target = None
    if action == "transition":
        target = text(str(rule.get("target_class") or ""), "target_class", maximum=60)
    filters = parse_filters(rule.get("filters"))
    return {"rule_id": rule_id, "action": action, "days": days, "target_class": target,
            "filters": filters, "note": text(str(rule.get("note") or ""), "note", maximum=160, allow_empty=True)}


def parse_cohort(cohort, seen_cohorts):
    cohort_id = text(cohort.get("cohort_id"), "cohort_id", maximum=80)
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", cohort_id):
        raise ValueError("cohort_id must be a plain identifier")
    if cohort_id in seen_cohorts:
        raise ValueError("duplicate cohort_id: " + cohort_id)
    seen_cohorts.add(cohort_id)
    object_count = integer(cohort.get("object_count"), "object_count", minimum=Decimal("1"),
                           maximum=Decimal("1000000000000"))
    total_bytes = integer(cohort.get("total_bytes"), "total_bytes", minimum=Decimal("0"),
                          maximum=Decimal("1000000000000000"))
    avg_raw = cohort.get("avg_object_bytes")
    avg = integer(avg_raw, "avg_object_bytes", minimum=Decimal("0"), maximum=Decimal("1000000000000000")) \
        if (avg_raw is not None and str(avg_raw).strip() != "") else None
    if avg is None and total_bytes > ZERO and object_count > 0:
        avg = int((Decimal(total_bytes) / Decimal(object_count)).to_integral_value())
    if avg is None:
        avg = 0
    current_class = text(str(cohort.get("current_class") or ""), "current_class", maximum=60)
    if not current_class:
        raise ValueError("current_class is required")
    last_mod = parse_date(cohort.get("last_modified_at"), "last_modified_at", required=False)
    versioning = text(str(cohort.get("versioning_state") or "unknown"), "versioning_state", maximum=16)
    if versioning not in ("versioned", "unversioned", "unknown", "suspended"):
        raise ValueError("versioning_state must be versioned/unversioned/suspended/unknown")
    expected_delete = parse_date(cohort.get("expected_delete_or_overwrite_at"), "expected_delete_or_overwrite_at",
                                 required=False)
    return {"cohort_id": cohort_id, "object_count": object_count, "total_bytes": total_bytes, "avg": avg,
            "current_class": current_class, "last_modified_at": last_mod, "versioning_state": versioning,
            "expected_delete_or_overwrite_at": expected_delete}


def class_price(price_table, class_name):
    if price_table is None:
        return None
    return price_table["classes"].get(class_name)


def match_filter(cohort, filters):
    """Rule filter vs aggregated cohort: only decisions possible at cohort level.
    Size ranges are tested against avg_object_bytes; a cohort spanning the
    boundary is marked RANGE_SPAN (needs finer data)."""
    if not filters:
        return None
    problems = []
    if "prefix" in filters and filters["prefix"]:
        # Cohort level has no key prefix info; unknown prefix match.
        problems.append("prefix_filter_unverifiable（cohort 未提供 key 前缀，无法核对规则前缀 %s）" % filters["prefix"])
    if "tags" in filters and filters["tags"]:
        problems.append("tag_filter_unverifiable（cohort 未提供标签，无法核对规则标签匹配）")
    if "min_bytes" in filters or "max_bytes" in filters:
        lo = Decimal(filters.get("min_bytes", 0))
        hi = Decimal(filters.get("max_bytes", Decimal("1000000000000000")))
        if Decimal(cohort["avg"]) < lo or Decimal(cohort["avg"]) > hi:
            return {"matched": False, "reason": "size_out_of_range（avg %d 字节不在规则字节范围）" % cohort["avg"]}
        return {"matched": True, "reason": None}
    if problems:
        return {"matched": None, "reason": "; ".join(problems)}
    return {"matched": True, "reason": None}


def evaluate_rule(cohort, rule, profile, price_table, as_of):
    """Per (cohort, rule) scenario hit. Cohort is an aggregate: hit counts are
    upper-bound estimates assuming the whole cohort is affected."""
    out = {"rule_id": rule["rule_id"], "action": rule["action"], "days": rule["days"],
           "target_class": rule["target_class"], "applicable": None, "trigger_date": None,
           "already_triggered": None, "billable_bytes_estimate": None, "billable_gb_estimate": None,
           "candidate_monthly_storage_cost": None, "request_cost_candidate": None, "reasons": [], "note": None}

    # -- filter gate --
    fm = match_filter(cohort, rule["filters"])
    if fm is not None:
        if fm["matched"] is False:
            out["applicable"] = False
            out["reasons"].append("filter_not_matched（%s）" % fm["reason"])
            return out
        if fm["matched"] is None:
            out["applicable"] = None
            out["reasons"].append("filter_unverifiable（%s）" % fm["reason"])
            return out

    # -- small-object threshold gate (user profile, not cross-cloud defaults) --
    if profile["small_object_threshold_bytes"] > 0 and cohort["avg"] < profile["small_object_threshold_bytes"]:
        out["applicable"] = False
        out["reasons"].append("small_object_excluded（avg %d 字节 < 小对象阈值 %d 字节：按本 profile 默认不转此类对象）"
                              % (cohort["avg"], profile["small_object_threshold_bytes"]))
        return out

    # -- age data gate --
    if cohort["last_modified_at"] is None:
        out["applicable"] = None
        out["reasons"].append("age_data_missing（缺 last_modified_at，无法判定触发日期；不做单对象精确预估）")
        return out
    trigger = cohort["last_modified_at"] + _delta_days(rule["days"])
    out["trigger_date"] = trigger.isoformat()
    out["already_triggered"] = trigger < as_of or trigger == as_of
    out["applicable"] = True

    # -- billable estimate (whole cohort upper bound; no age distribution) --
    min_bill = profile["min_billable_bytes"]
    billable = Decimal(cohort["total_bytes"])
    if min_bill > 0 and cohort["avg"] < min_bill:
        billable = Decimal(cohort["object_count"]) * Decimal(min_bill)
        out["reasons"].append("minimum_billing_space_applied（avg %d 字节 < 最小计量 %d 字节/对象：按对象数×最小计量计费，"
                              "billable=%s 字节 而非总字节 %d）" % (cohort["avg"], min_bill, billable, cohort["total_bytes"]))
    out["billable_bytes_estimate"] = str(int(billable))
    out["billable_gb_estimate"] = str((billable / GB).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

    # -- cost candidates (only when user price available for the effective class) --
    price_class = rule["target_class"] if rule["action"] == "transition" else cohort["current_class"]
    pc = class_price(price_table, price_class)
    if pc is None:
        out["reasons"].append("price_missing_for_%s（price_table 缺 %s 费率：只给用量，不报金额）" % (price_class, price_class))
    else:
        if "storage_gb_month" in pc:
            cost = (billable / GB) * pc["storage_gb_month"]
            out["candidate_monthly_storage_cost"] = money(cost)
        if "transition_per_1000_objects" in pc and rule["action"] == "transition":
            req_cost = (Decimal(cohort["object_count"]) / Decimal("1000")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP) * pc["transition_per_1000_objects"]
            out["request_cost_candidate"] = money(req_cost)

    # -- minimum storage days exposure (after rule trigger) --
    min_days = profile["minimum_storage_days"]
    delete_on = cohort["expected_delete_or_overwrite_at"]
    if min_days > 0 and delete_on is not None and trigger is not None:
        if delete_on < trigger:
            out["reasons"].append("deleted_before_rule_trigger（预计删除/覆盖日 %s 早于规则触发日 %s：规则实际不会产生作用）"
                                  % (delete_on.isoformat(), trigger.isoformat()))
        elif days_between(delete_on, trigger) < min_days:
            out["reasons"].append("below_minimum_storage_days（转换/到期后 %d 天即删除/覆盖 < 最低存储 %d 天："
                                  "提前变更可能产生最低时长费用或不可行）" % (days_between(delete_on, trigger), min_days))
    return out


def _delta_days(days):
    return timedelta(days=int(days))


def cohort_status(out_list, cohort, profile):
    """Decide status for one cohort across its rule matches."""
    applied = [o for o in out_list if o["applicable"] is True]
    conflicts = []
    transitions = [o for o in applied if o["action"] == "transition"]
    expirations = [o for o in applied if o["action"] == "expiration"]
    seen_targets = {}
    for t in transitions:
        seen_targets.setdefault(t["target_class"], []).append(t["rule_id"])
    if len(seen_targets) > 1:
        conflicts.append("multiple_transition_targets（同批命中多个目标类：%s，未声明优先级）"
                         % ", ".join(sorted(seen_targets)))
    for e in expirations:
        if transitions:
            conflicts.append("transition_and_expiration_overlap（规则 %s 转换 与规则 %s 到期重叠）"
                             % (",".join(t["rule_id"] for t in transitions), e["rule_id"]))
    if conflicts and profile["conflict_priority"] == "review":
        return "CONFLICT_REVIEW", conflicts

    versioning = cohort["versioning_state"]
    delete_risks = []
    cost_risks = []
    for o in out_list:
        for r in o["reasons"]:
            if r.startswith(("minimum_billing_space_applied", "below_minimum_storage_days",
                             "deleted_before_rule_trigger")):
                cost_risks.append(r)
    for e in expirations:
        if versioning == "versioned":
            delete_risks.append("expiration_soft_delete（规则 %s 到期：versioned 桶只删除当前对象产生删除标记，"
                                "历史版本仍保留，不构成永久删除）" % e["rule_id"])
        else:
            delete_risks.append("expiration_permanent_delete（规则 %s 到期：%s 场景过期对象将被永久删除，"
                                "操作不可逆）" % (e["rule_id"], versioning))
    if delete_risks and any("permanent_delete" in r for r in delete_risks):
        return "DELETE_RISK", delete_risks + cost_risks
    if cost_risks:
        return "COST_RISK", cost_risks
    if any(o["applicable"] is None for o in out_list):
        return "UNKNOWN", ["rule_match_unknown（%s）" % o["rule_id"] for o in out_list if o["applicable"] is None]
    return "SAFE_PREVIEW", (delete_risks if delete_risks else [])


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be an object")
    for key in ("object_cohorts", "rules"):
        if key not in data or not isinstance(data[key], list) or not 1 <= len(data[key]) <= 500:
            raise ValueError(key + " must be a list of 1-500 items")
    provider = text(str(data.get("provider") or ""), "provider", maximum=40)
    if not provider:
        raise ValueError("provider is required")
    currency = text(str(data.get("currency") or "CNY"), "currency", maximum=6)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be a three-letter code")
    as_of = parse_date(data.get("as_of") or data.get("as_of_date") or "", "as_of")
    profile = parse_profile(data.get("policy_profile"))
    table_raw = data.get("price_table")
    price_table = None
    if table_raw is not None and isinstance(table_raw, dict) and table_raw.get("classes"):
        price_table = parse_price_table(table_raw)

    seen_rules = set()
    rules = [parse_rule(r, seen_rules) for r in data["rules"]]
    seen_cohorts = set()
    cohorts = [parse_cohort(c, seen_cohorts) for c in data["object_cohorts"]]

    results = []
    for cohort in cohorts:
        evals = [evaluate_rule(cohort, rule, profile, price_table, as_of) for rule in rules]
        status, reasons = cohort_status(evals, cohort, profile)
        hits = [o for o in evals if o["applicable"] is True]
        total_billable_bytes = sum((Decimal(o["billable_bytes_estimate"]) for o in hits if o["billable_bytes_estimate"]), ZERO)
        results.append({
            "cohort_id": cohort["cohort_id"], "current_class": cohort["current_class"],
            "versioning_state": cohort["versioning_state"],
            "object_count": cohort["object_count"], "total_bytes": cohort["total_bytes"],
            "avg_object_bytes": cohort["avg"],
            "last_modified_at": None if cohort["last_modified_at"] is None else cohort["last_modified_at"].isoformat(),
            "expected_delete_or_overwrite_at": None if cohort["expected_delete_or_overwrite_at"] is None
            else cohort["expected_delete_or_overwrite_at"].isoformat(),
            "rule_hits": len(hits), "status": status,
            "hit_billable_bytes_estimate": str(int(total_billable_bytes)) if hits else None,
            "cost_monthly_candidate": None,
            "rule_evaluations": evals, "reasons": reasons,
            "note": "只做情景预演：不调用云 API、不修改生命周期、不删除对象；cohort 为聚合，命中按整批上界估算，"
                    "缺对象年龄/大小分布时不假装精确到单对象。"})
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = {"provider": provider, "as_of": as_of.isoformat(), "currency": currency,
               "policy_profile": {"min_billable_bytes": profile["min_billable_bytes"],
                                  "minimum_storage_days": profile["minimum_storage_days"],
                                  "small_object_threshold_bytes": profile["small_object_threshold_bytes"],
                                  "conflict_priority": profile["conflict_priority"],
                                  "note": profile["note"]},
               "cohort_count": len(results), "status_counts": counts,
               "cohorts": results, "markdown_summary": build_markdown(results, provider, as_of, currency)}
    return summary


def build_markdown(results, provider, as_of, currency):
    rows = [["cohort", "存储类/版本", "对象数", "总字节", "avg字节", "命中规则", "状态"]]
    for r in results:
        rows.append([r["cohort_id"], r["current_class"] + "/" + r["versioning_state"], r["object_count"],
                     r["total_bytes"], r["avg_object_bytes"], r["rule_hits"], r["status"]])
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    head = ["# 对象存储生命周期变更预演（%s，基准 %s，币种 %s）\n\n" % (provider, as_of, currency)]
    head.append("只做**情景预演**：不调用云 API、不修改生命周期、不删除对象。规则/费率/最低时长/小对象阈值均由用户提供"
                "（不跨云套用默认值）；cohort 为聚合批次，命中按整批上界估算，缺年龄/大小分布时不假装精确到单对象。\n\n")
    head.append(md_table(rows))
    head.append("\n\n状态分布：" + ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())) + "。\n")
    head.append("\nDELETE_RISK/COST_RISK 仅为费用与删除语义提示；任何结果都不代表云端已发生变更。")
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
                          "message": "请对照 references/guide.md 检查 provider/as_of、cohort_id/rule_id 唯一、"
                                     "数字非负有限、policy_profile 与 price_table 字段、时间带时区。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
