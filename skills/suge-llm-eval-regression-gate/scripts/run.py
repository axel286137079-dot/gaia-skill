#!/usr/bin/env python3
"""Offline LLM evaluation baseline-regression gate.

Compares a candidate evaluation run against a frozen baseline and decides whether
the candidate may be promoted.  It never calls a model, never re-scores anything
and never claims that an automatic score equals real business quality.  Aggregate
scores are reported but can never mask a regression on a critical case, and judge
model / scale / dataset changes are reported as not directly comparable instead of
being silently averaged.
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
RAW_SECRET_PATTERNS = (
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
    re.compile(r"-----BEGIN [A-Z ]*CERTIFICATE-----"),
)
OPAQUE_BLOB = re.compile(r"^[A-Za-z0-9+/=_-]{32,}$")
REDACTION_PREFIXES = ("fp:", "fingerprint:", "sha256:", "sha256=", "hash:", "hashed:",
                      "redacted:", "masked:", "hmac:", "sha1:")
SECRET_VALUE_KEYS = {"password", "passwd", "passphrase", "secret_value", "raw_secret",
                     "private_key", "private_key_pem", "api_key", "access_token",
                     "client_secret", "credential_value"}

DIRECTIONS = ("higher", "lower", "binary")
CRITICALITIES = ("critical", "normal", "low")
STATUS_ORDER = ("INVALID", "NOT_COMPARABLE", "CRITICAL_CASE_FAIL", "REGRESSION",
                "COVERAGE_GAP", "PARTIAL", "PASS")


def privacy_gate(value):
    """Reject input that still contains a real-looking credential. Never echoes it."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in SECRET_VALUE_KEYS:
                raise ValueError(
                    "PRIVACY_GATE: 输入包含疑似未脱敏的凭据字段，已拒绝处理。"
                    "请只提供评测结果，不要提供密钥原文。")
            privacy_gate(item)
    elif isinstance(value, list):
        for item in value:
            privacy_gate(item)
    elif isinstance(value, str):
        for pattern in RAW_SECRET_PATTERNS:
            if pattern.search(value):
                raise ValueError(
                    "PRIVACY_GATE: 输入包含疑似真实令牌/私钥内容，已拒绝处理，且不会回显该内容。")
        if len(value) >= 32 and OPAQUE_BLOB.match(value) \
                and not value.strip().lower().startswith(REDACTION_PREFIXES):
            raise ValueError(
                "PRIVACY_GATE: 输入包含长度较长且无脱敏前缀的不透明字符串，疑似真实凭据，已拒绝处理。")
    return True


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
    return text


def optional_text(value, label, maximum=300):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    return clean_text(value, label, maximum)


def parse_as_of(value):
    if not isinstance(value, str) or "T" not in value.strip():
        raise ValueError("as_of must be an ISO8601 datetime with a UTC offset")
    return parse_dt(value, "as_of")


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


def number(value, label, minimum=None, maximum=None):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite():
        raise ValueError(label + " must be finite (NaN/Infinity is not allowed)")
    if minimum is not None and result < minimum:
        raise ValueError(label + " is below the allowed minimum")
    if maximum is not None and result > maximum:
        raise ValueError(label + " is above the allowed maximum")
    return result


def quant(value, places=2):
    result = Decimal(value).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
    if result == 0:
        result = abs(result)
    return str(result)


def show(value):
    """Render a score for output: Decimal becomes a fixed-point string, bool stays bool."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return quant(value, 4)
    return value


def mean_of(values):
    if not values:
        return None
    return sum(values) / Decimal(len(values))


def median_of(values):
    if not values:
        return None
    ordered = sorted(values)
    size = len(ordered)
    if size % 2:
        return ordered[size // 2]
    return (ordered[size // 2 - 1] + ordered[size // 2]) / Decimal(2)


def parse_result(item, label, cases, metrics):
    if not isinstance(item, dict):
        raise ValueError("each result must be an object")
    case_id = clean_text(item.get("case_id"), label + ".case_id", 120)
    if case_id not in cases:
        raise ValueError(label + ".case_id references an unknown case: " + case_id)
    metric_id = clean_text(item.get("metric_id"), label + ".metric_id", 120)
    if metric_id not in metrics:
        raise ValueError(label + ".metric_id references an unknown metric: " + metric_id)
    direction = metrics[metric_id]["direction"]
    score = None
    label_value = None
    if direction == "binary":
        raw_label = item.get("label")
        if not isinstance(raw_label, bool):
            raise ValueError(label + ".label must be a boolean for a binary metric")
        label_value = raw_label
        if item.get("score") is not None and str(item.get("score")).strip() != "":
            score = number(item.get("score"), label + ".score")
    else:
        score = number(item.get("score"), label + ".score")
    evaluated_at = parse_dt(item.get("evaluated_at"), label + ".evaluated_at", required=False)
    if evaluated_at is not None and evaluated_at.tzinfo is None:
        raise ValueError(label + ".evaluated_at must be timezone-aware")
    return {
        "case_id": case_id, "metric_id": metric_id, "score": score, "label": label_value,
        "judge_id": clean_text(item.get("judge_id"), label + ".judge_id", 120),
        "scale": optional_text(item.get("scale"), label + ".scale", 60),
        "dataset_version": optional_text(item.get("dataset_version"),
                                         label + ".dataset_version", 120),
        "evaluated_at": evaluated_at,
    }


def collect_results(raw, key, cases, metrics, as_of):
    if raw is None:
        raw = []
    if not isinstance(raw, list) or len(raw) > 200000:
        raise ValueError(key + " must be a list of up to 200000 items")
    collected = {}
    for index, item in enumerate(raw):
        record = parse_result(item, "%s[%d]" % (key, index), cases, metrics)
        if record["evaluated_at"] is not None and record["evaluated_at"] > as_of:
            raise ValueError("%s[%d].evaluated_at must not be in the future relative to as_of"
                             % (key, index))
        pair = (record["case_id"], record["metric_id"])
        if pair in collected:
            raise ValueError("duplicate result for case/metric in %s: %s/%s"
                             % (key, pair[0], pair[1]))
        collected[pair] = record
    return collected


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    privacy_gate(data)
    as_of = parse_as_of(data.get("as_of"))
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    raw_metrics = data.get("metric_definitions")
    if not isinstance(raw_metrics, list) or len(raw_metrics) > 200:
        raise ValueError("metric_definitions must be a list of up to 200 items")
    metrics = {}
    order = []
    for index, item in enumerate(raw_metrics):
        label = "metric_definitions[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each metric definition must be an object")
        metric_id = clean_text(item.get("metric_id"), label + ".metric_id", 120)
        if metric_id in metrics:
            raise ValueError("duplicate metric_id: " + metric_id)
        direction = clean_text(item.get("direction"), label + ".direction", 24).lower()
        if direction not in DIRECTIONS:
            raise ValueError(label + ".direction must be one of " + ", ".join(DIRECTIONS))
        threshold_raw = item.get("pass_threshold")
        threshold = None
        if direction == "binary":
            if threshold_raw is not None and str(threshold_raw).strip() != "":
                threshold = number(threshold_raw, label + ".pass_threshold")
        else:
            threshold = number(threshold_raw, label + ".pass_threshold")
        metrics[metric_id] = {
            "metric_id": metric_id, "direction": direction,
            "scale": clean_text(item.get("scale"), label + ".scale", 60),
            "pass_threshold": threshold,
            "max_allowed_regression": number(item.get("max_allowed_regression"),
                                             label + ".max_allowed_regression", Decimal("0")),
            "weight": number(item.get("weight"), label + ".weight", Decimal("0")),
            "cases": item.get("cases"),
        }
        order.append(metric_id)

    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) > 100000:
        raise ValueError("cases must be a list of up to 100000 items")
    cases = {}
    case_order = []
    for index, item in enumerate(raw_cases):
        label = "cases[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each case must be an object")
        case_id = clean_text(item.get("case_id"), label + ".case_id", 120)
        if case_id in cases:
            raise ValueError("duplicate case_id: " + case_id)
        criticality_raw = optional_text(item.get("criticality"), label + ".criticality", 24)
        criticality = criticality_raw.lower() if criticality_raw else "normal"
        if criticality not in CRITICALITIES:
            raise ValueError(label + ".criticality must be one of " + ", ".join(CRITICALITIES))
        cases[case_id] = {"case_id": case_id,
                          "slice": optional_text(item.get("slice"), label + ".slice", 120),
                          "criticality": criticality}
        case_order.append(case_id)
    if not cases or not metrics:
        result = {
            "as_of": as_of.isoformat(), "status": "INVALID", "metric_count": 0,
            "case_count": len(cases), "status_counts": {}, "metrics": [],
            "aggregate": {}, "human_review": {}, "slices": [], "drifts": [],
            "evidence_gaps": [],
            "note": "没有可比较的指标或用例，判定为 INVALID。本技能不调用任何模型、不重评分、"
                    "不声称自动分数等于真实业务质量、不输出无样本支撑的统计显著性。",
        }
        result["markdown_summary"] = build_markdown(result)
        return result

    for metric_id in order:
        declared = metrics[metric_id]["cases"]
        if declared is None:
            metrics[metric_id]["applicable"] = list(case_order)
            continue
        if not isinstance(declared, list):
            raise ValueError("metric " + metric_id + ".cases must be a list")
        applicable = []
        for case_id in declared:
            name = clean_text(case_id, "metric " + metric_id + ".cases[]", 120)
            if name not in cases:
                raise ValueError("metric " + metric_id + " references an unknown case: " + name)
            if name not in applicable:
                applicable.append(name)
        metrics[metric_id]["applicable"] = applicable

    baseline = collect_results(data.get("baseline_results"), "baseline_results",
                               cases, metrics, as_of)
    candidate = collect_results(data.get("candidate_results"), "candidate_results",
                               cases, metrics, as_of)

    min_coverage_raw = data.get("min_coverage")
    min_coverage = (number(min_coverage_raw, "min_coverage", Decimal("0"), Decimal("1"))
                    if min_coverage_raw is not None and str(min_coverage_raw).strip() != ""
                    else Decimal("1"))

    human_labels = {}
    for index, item in enumerate(data.get("human_labels") or []):
        label = "human_labels[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each human label must be an object")
        case_id = clean_text(item.get("case_id"), label + ".case_id", 120)
        metric_id = clean_text(item.get("metric_id"), label + ".metric_id", 120)
        if case_id not in cases or metric_id not in metrics:
            raise ValueError(label + " references an unknown case or metric")
        record = {
            "case_id": case_id, "metric_id": metric_id,
            "label": item.get("label"),
            "score": (number(item.get("score"), label + ".score")
                      if item.get("score") is not None and str(item.get("score")).strip() != ""
                      else None),
            "annotator_id": clean_text(item.get("annotator_id"), label + ".annotator_id", 120),
            "labeled_at": parse_dt(item.get("labeled_at"), label + ".labeled_at", required=False),
        }
        pair = (case_id, metric_id)
        if pair in human_labels:
            raise ValueError("duplicate human label for case/metric: %s/%s" % pair)
        human_labels[pair] = record

    rows = []
    drifts = []
    evidence_gaps = []
    for metric_id in order:
        metric = metrics[metric_id]
        direction = metric["direction"]
        applicable = metric["applicable"]
        flags = set()
        samples = []
        critical_failures = []
        regressions = []
        baseline_scores = []
        candidate_scores = []
        pass_before = 0
        pass_after = 0
        compared = 0
        comparable = 0
        missing_cases = []
        missing_critical = []
        for case_id in applicable:
            case = cases[case_id]
            base = baseline.get((case_id, metric_id))
            cand = candidate.get((case_id, metric_id))
            if base is None or cand is None:
                missing_cases.append(case_id)
                if case["criticality"] == "critical":
                    missing_critical.append(case_id)
                continue
            compared += 1
            pair_drift = []
            if base["judge_id"] != cand["judge_id"]:
                pair_drift.append("JUDGE_DRIFT")
            if base["scale"] is not None and base["scale"] != metric["scale"]:
                pair_drift.append("SCALE_DRIFT")
            if cand["scale"] is not None and cand["scale"] != metric["scale"]:
                pair_drift.append("SCALE_DRIFT")
            if base["dataset_version"] is not None and cand["dataset_version"] is not None \
                    and base["dataset_version"] != cand["dataset_version"]:
                pair_drift.append("DATASET_DRIFT")
            if pair_drift:
                for code in sorted(set(pair_drift)):
                    drifts.append({"metric_id": metric_id, "case_id": case_id, "code": code,
                                   "detail": "该样本不可直接比较，已从统计中排除"})
                flags.update(pair_drift)
                samples.append({
                    "case_id": case_id, "slice": case["slice"], "criticality": case["criticality"],
                    "comparable": False, "flags": sorted(set(pair_drift)),
                    "baseline": show(base["score"]) if direction != "binary" else base["label"],
                    "candidate": show(cand["score"]) if direction != "binary" else cand["label"],
                    "delta": None, "regression": None, "critical_failure": False,
                })
                continue
            comparable += 1
            if direction == "binary":
                was_pass = bool(base["label"])
                is_pass = bool(cand["label"])
                delta = None
                regression = was_pass and not is_pass
                improvement = is_pass and not was_pass
            else:
                delta = cand["score"] - base["score"]
                was_pass = (base["score"] >= metric["pass_threshold"] if direction == "higher"
                            else base["score"] <= metric["pass_threshold"])
                is_pass = (cand["score"] >= metric["pass_threshold"] if direction == "higher"
                           else cand["score"] <= metric["pass_threshold"])
                if direction == "higher":
                    regression = delta < -metric["max_allowed_regression"]
                else:
                    regression = delta > metric["max_allowed_regression"]
                improvement = (delta > 0) if direction == "higher" else (delta < 0)
            if was_pass:
                pass_before += 1
            if is_pass:
                pass_after += 1
            if direction != "binary":
                baseline_scores.append(base["score"])
                candidate_scores.append(cand["score"])
            critical_failure = case["criticality"] == "critical" and (regression or not is_pass)
            if regression:
                regressions.append({"case_id": case_id, "criticality": case["criticality"],
                                    "baseline": show(base["score"]) if direction != "binary"
                                                else base["label"],
                                    "candidate": show(cand["score"]) if direction != "binary"
                                                 else cand["label"],
                                    "delta": None if delta is None else quant(delta, 4)})
            if critical_failure:
                critical_failures.append({"case_id": case_id,
                                          "reason": "关键用例回归" if regression
                                                    else "关键用例未达通过阈值",
                                          "baseline": show(base["score"]) if direction != "binary"
                                                      else base["label"],
                                          "candidate": show(cand["score"]) if direction != "binary"
                                                       else cand["label"]})
            samples.append({
                "case_id": case_id, "slice": case["slice"], "criticality": case["criticality"],
                "comparable": True, "flags": [],
                "baseline": show(base["score"]) if direction != "binary" else base["label"],
                "candidate": show(cand["score"]) if direction != "binary" else cand["label"],
                "delta": None if delta is None else quant(delta, 4),
                "regression": regression, "improvement": improvement,
                "pass_before": was_pass, "pass_after": is_pass,
                "critical_failure": critical_failure,
            })
        coverage = (Decimal(compared) * 100 / Decimal(len(applicable))
                    if applicable else Decimal("0"))
        if missing_cases:
            flags.add("MISSING_RESULTS")
        if missing_critical:
            flags.add("MISSING_CRITICAL_CASE")
            evidence_gaps.append({
                "metric_id": metric_id, "code": "MISSING_CRITICAL_CASE",
                "detail": "关键用例缺少可比结果：" + ", ".join(missing_critical)})
        coverage_gap = (Decimal(len(applicable)) > 0
                        and Decimal(compared) / Decimal(len(applicable)) < min_coverage)
        if coverage_gap:
            flags.add("COVERAGE_GAP")
        if compared and comparable == 0:
            status = "NOT_COMPARABLE"
        elif critical_failures:
            status = "CRITICAL_CASE_FAIL"
        elif regressions:
            status = "REGRESSION"
        elif coverage_gap:
            status = "COVERAGE_GAP"
        elif compared and comparable < compared:
            status = "PARTIAL"
        elif not compared:
            status = "INVALID"
        else:
            status = "PASS"

        next_actions = []
        for item in critical_failures:
            next_actions.append("关键用例 %s 失败（%s），禁止仅凭聚合分放行" % (item["case_id"],
                                                                       item["reason"]))
        for item in regressions:
            next_actions.append("样本 %s 出现回归" % item["case_id"])
        if missing_critical:
            next_actions.append("补齐关键用例结果：" + ", ".join(missing_critical))
        if status == "NOT_COMPARABLE":
            next_actions.append("统一裁判模型/量表/数据集版本后重跑，禁止跨版本直接比较")
        largest = None
        if regressions:
            worst = max(regressions, key=lambda item: abs(Decimal(str(item["delta"] or 0))))
            largest = {"case_id": worst["case_id"], "delta": worst["delta"]}
        rows.append({
            "metric_id": metric_id, "direction": direction, "scale": metric["scale"],
            "weight": quant(metric["weight"], 4),
            "pass_threshold": None if metric["pass_threshold"] is None
                              else quant(metric["pass_threshold"], 4),
            "max_allowed_regression": quant(metric["max_allowed_regression"], 4),
            "status": status, "review_flags": sorted(flags),
            "applicable_cases": applicable, "compared_pairs": compared,
            "comparable_pairs": comparable,
            "coverage": {"applicable": len(applicable), "compared": compared,
                         "coverage_pct": quant(coverage) + "%",
                         "missing_rate_pct": quant(Decimal("100") - coverage) + "%"},
            "missing_cases": missing_cases, "missing_critical_cases": missing_critical,
            "baseline": {"mean": None if not baseline_scores else quant(mean_of(baseline_scores), 4),
                         "median": None if not baseline_scores else quant(median_of(baseline_scores), 4),
                         "pass_rate_pct": None if not comparable
                                          else quant(Decimal(pass_before) * 100 / Decimal(comparable)) + "%"},
            "candidate": {"mean": None if not candidate_scores else quant(mean_of(candidate_scores), 4),
                          "median": None if not candidate_scores else quant(median_of(candidate_scores), 4),
                          "pass_rate_pct": None if not comparable
                                           else quant(Decimal(pass_after) * 100 / Decimal(comparable)) + "%"},
            "mean_delta": None if not baseline_scores or not candidate_scores
                          else quant(mean_of(candidate_scores) - mean_of(baseline_scores), 4),
            "samples": samples,
            "critical_failures": critical_failures,
            "regressions": regressions,
            "largest_regression": largest,
            "next_actions": next_actions or ["无需补充动作；保留本次评测快照以备复核"],
        })

    rows.sort(key=lambda item: order.index(item["metric_id"]))
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    overall = min((row["status"] for row in rows), key=lambda name: STATUS_ORDER.index(name))

    weights_sum = sum((metrics[metric_id]["weight"] for metric_id in order), Decimal("0"))
    weights_closed = any(abs(weights_sum - target) <= Decimal("0.0001")
                         for target in (Decimal("1"), Decimal("100")))
    weight_flags = [] if weights_closed else ["WEIGHTS_NOT_CLOSED"]
    if not weights_closed:
        overall = min(overall, "PARTIAL", key=lambda name: STATUS_ORDER.index(name))

    used_weight = Decimal("0")
    weighted_before = Decimal("0")
    weighted_after = Decimal("0")
    for row in rows:
        if row["baseline"]["pass_rate_pct"] is None or row["status"] == "NOT_COMPARABLE":
            continue
        weight = Decimal(row["weight"])
        used_weight += weight
        weighted_before += weight * Decimal(row["baseline"]["pass_rate_pct"].rstrip("%"))
        weighted_after += weight * Decimal(row["candidate"]["pass_rate_pct"].rstrip("%"))
    if used_weight > 0:
        aggregate_before = weighted_before / used_weight
        aggregate_after = weighted_after / used_weight
    else:
        aggregate_before = aggregate_after = None
    critical_present = any(row["critical_failures"] for row in rows)
    aggregate = {
        "weights_sum": quant(weights_sum, 4),
        "weights_closed": weights_closed,
        "weight_flags": weight_flags,
        "used_weight": quant(used_weight, 4),
        "baseline_pass_rate_pct": None if aggregate_before is None else quant(aggregate_before) + "%",
        "candidate_pass_rate_pct": None if aggregate_after is None else quant(aggregate_after) + "%",
        "delta_pct": None if aggregate_before is None
                     else quant(aggregate_after - aggregate_before) + "%",
        "aggregate_improved": None if aggregate_before is None else aggregate_after > aggregate_before,
        "critical_failure_present": critical_present,
        "aggregate_masks_critical": bool(aggregate_before is not None and critical_present
                                         and aggregate_after >= aggregate_before),
        "note": "聚合分只用各指标通过率（同一 0–100% 量纲）加权，不跨量表直接相加原始分；"
                "聚合分不得掩盖关键用例回归。",
    }

    slice_rows = {}
    for row in rows:
        for sample in row["samples"]:
            key = sample["slice"] or "—"
            entry = slice_rows.setdefault(key, {"slice": key, "samples": 0, "regressions": 0,
                                                "critical_failures": 0, "not_comparable": 0})
            entry["samples"] += 1
            if sample["comparable"] is False:
                entry["not_comparable"] += 1
                continue
            if sample["regression"]:
                entry["regressions"] += 1
            if sample["critical_failure"]:
                entry["critical_failures"] += 1
    slices = sorted(slice_rows.values(), key=lambda item: item["slice"])

    human_rows = []
    agreements = 0
    compared_human = 0
    disagreements = []
    for (case_id, metric_id), record in sorted(human_labels.items()):
        cand = candidate.get((case_id, metric_id))
        direction = metrics[metric_id]["direction"]
        verdict = "NO_CANDIDATE_RESULT"
        if cand is not None:
            if direction == "binary":
                if not isinstance(record["label"], bool):
                    verdict = "HUMAN_LABEL_MISSING"
                else:
                    compared_human += 1
                    if record["label"] == cand["label"]:
                        agreements += 1
                        verdict = "AGREE"
                    else:
                        verdict = "DISAGREE"
                        disagreements.append({"case_id": case_id, "metric_id": metric_id,
                                              "human": record["label"], "model": cand["label"]})
            else:
                if record["score"] is None:
                    verdict = "HUMAN_LABEL_MISSING"
                else:
                    compared_human += 1
                    if record["score"] == cand["score"]:
                        agreements += 1
                        verdict = "AGREE"
                    else:
                        verdict = "DISAGREE"
                        disagreements.append({"case_id": case_id, "metric_id": metric_id,
                                              "human": quant(record["score"], 4),
                                              "model": quant(cand["score"], 4)})
        human_rows.append({"case_id": case_id, "metric_id": metric_id,
                           "annotator_id": record["annotator_id"], "verdict": verdict})
    human_review = {
        "labeled_count": len(human_labels), "compared_count": compared_human,
        "agreement_count": agreements,
        "agreement_pct": None if not compared_human
                         else quant(Decimal(agreements) * 100 / Decimal(compared_human)) + "%",
        "disagreements": disagreements,
        "rows": human_rows,
        "note": "人工标注与模型裁判分栏呈现，不合并进通过/失败判定。",
    }

    result = {
        "as_of": as_of.isoformat(),
        "status": overall,
        "metric_count": len(rows),
        "case_count": len(cases),
        "status_counts": counts,
        "metrics": rows,
        "aggregate": aggregate,
        "human_review": human_review,
        "slices": slices,
        "drifts": sorted(drifts, key=lambda item: (item["metric_id"], item["case_id"], item["code"])),
        "evidence_gaps": sorted(evidence_gaps, key=lambda item: (item["metric_id"], item["code"])),
        "min_coverage": quant(min_coverage, 4),
        "note": "只比较用户提供的评测结果。不调用任何模型、不重评分、不声称自动分数等于真实业务质量、"
                "不输出无样本支撑的统计显著性；裁判模型、量表或数据集版本变化一律标为不可直接比较；"
                "聚合分不得掩盖关键用例回归。",
    }
    result["markdown_summary"] = build_markdown(result)
    return result


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def build_markdown(result):
    lines = ["# LLM 评测基线回归门禁\n\n"]
    lines.append("基准时间 %s，共 %d 个指标 / %d 个用例。**总体判定：%s**。\n\n"
                 % (result["as_of"], result["metric_count"], result["case_count"],
                    result["status"]))
    if not result["metrics"]:
        lines.append("没有可比较的指标或用例。\n")
        lines.append("\n本技能不调用任何模型、不重评分、不声称自动分数等于真实业务质量。")
        return "".join(lines)
    lines.append("状态分布：" + ", ".join("%s=%d" % (key, value)
                                          for key, value in sorted(result["status_counts"].items()))
                 + "。\n\n")
    rows = [["指标", "方向", "状态", "覆盖", "基线→候选均值", "通过率", "关键失败"]]
    for row in result["metrics"]:
        rows.append([row["metric_id"], row["direction"], row["status"],
                     row["coverage"]["coverage_pct"],
                     "%s → %s" % (row["baseline"]["mean"] or "—", row["candidate"]["mean"] or "—"),
                     "%s → %s" % (row["baseline"]["pass_rate_pct"] or "—",
                                  row["candidate"]["pass_rate_pct"] or "—"),
                     len(row["critical_failures"])])
    lines.append(md_table(rows))
    lines.append("\n\n")
    aggregate = result["aggregate"]
    lines.append("聚合通过率：%s → %s（%s）；权重合计 %s（%s）；聚合分掩盖关键回归：%s。\n\n"
                 % (aggregate.get("baseline_pass_rate_pct") or "—",
                    aggregate.get("candidate_pass_rate_pct") or "—",
                    aggregate.get("delta_pct") or "—",
                    aggregate.get("weights_sum"), "闭合" if aggregate.get("weights_closed") else "未闭合",
                    "是" if aggregate.get("aggregate_masks_critical") else "否"))
    for row in result["metrics"]:
        lines.append("## %s（%s）\n\n" % (row["metric_id"], row["status"]))
        lines.append("- 方向 %s / 量表 %s / 权重 %s / 阈值 %s / 允许回归 %s\n"
                     % (row["direction"], row["scale"], row["weight"],
                        row["pass_threshold"] or "—", row["max_allowed_regression"]))
        lines.append("- 覆盖 %s（%d/%d 可比），均值 %s → %s，通过率 %s → %s\n"
                     % (row["coverage"]["coverage_pct"], row["comparable_pairs"],
                        row["coverage"]["applicable"], row["baseline"]["mean"] or "—",
                        row["candidate"]["mean"] or "—",
                        row["baseline"]["pass_rate_pct"] or "—",
                        row["candidate"]["pass_rate_pct"] or "—"))
        if row["critical_failures"]:
            lines.append("- 关键失败：" + ", ".join(
                "%s（%s）" % (item["case_id"], item["reason"])
                for item in row["critical_failures"]) + "\n")
        if row["review_flags"]:
            lines.append("- 标记：" + ", ".join(row["review_flags"]) + "\n")
        if row["next_actions"]:
            lines.append("- 待办：\n")
            for action in row["next_actions"]:
                lines.append("  - " + action + "\n")
        lines.append("\n")
    lines.append("本技能不调用任何模型、不重评分、不声称自动分数等于真实业务质量、"
                 "不输出无样本支撑的统计显著性；聚合分不得掩盖关键用例回归。")
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="UTF-8 JSON file; maximum 8 MB")
    args = parser.parse_args()
    try:
        path = Path(args.input)
        with path.open("rb") as handle:
            raw = handle.read(8_000_001)
        if len(raw) > 8_000_000:
            raise ValueError("input exceeds 8 MB")
        data = json.loads(raw.decode("utf-8-sig"))
        print(json.dumps(analyze(data), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, InvalidOperation):
        print(json.dumps({"error": "invalid_input",
                          "message": "请对照 references/guide.md 检查：输入不含密钥原文、as_of 带时区、"
                                     "metric_id/case_id 唯一、同一 case/metric 不得重复、"
                                     "binary 指标必须给 label、数值不得为 NaN/Infinity、"
                                     "evaluated_at 不晚于 as_of。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
