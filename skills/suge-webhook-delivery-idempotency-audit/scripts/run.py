#!/usr/bin/env python3
"""Offline webhook delivery & idempotency audit.

Cross-checks delivery attempts against processing records using a user-declared
dedup key.  Flags acknowledgements without processing evidence, duplicate side
effects, missing expected events, retry backlog and signature gaps.

Read-only: never replays a webhook, never calls an endpoint, never writes to a
queue or database.
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?$")
PROCESSING_SUCCESS = {"succeeded", "success", "ok", "completed", "processed", "done"}
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Secret-shaped literals are rejected so credentials never reach the report.
SECRET_HINTS = (
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
)
DEDUP_FIELDS = {"event_id", "event_type", "object_id"}


def clean_text(value, label, maximum=120, allow_empty=False):
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


def number(value, label, minimum=Decimal("0"), maximum=Decimal("1000000000")):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or not minimum <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
    return result


def integer(value, label, minimum=Decimal("0"), maximum=Decimal("1000000000")):
    result = number(value, label, minimum, maximum)
    if result != result.to_integral_value():
        raise ValueError(label + " must be an integer")
    return int(result)


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


def seconds_between(later, earlier):
    if later.tzinfo is None or earlier.tzinfo is None:
        raise ValueError("timezone-aware datetimes required for interval math")
    return Decimal(str(round((later - earlier).total_seconds(), 4)))


def quant(value, places=2):
    return str(Decimal(value).quantize(Decimal("1").scaleb(-places)))


def parse_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    low = integer(policy.get("success_status_min", 200), "policy.success_status_min", Decimal("100"), Decimal("599"))
    high = integer(policy.get("success_status_max", 299), "policy.success_status_max", Decimal("100"), Decimal("599"))
    if low > high:
        raise ValueError("policy.success_status_min must not exceed success_status_max")
    max_response = number(policy.get("max_response_seconds", 10), "policy.max_response_seconds",
                          Decimal("0.01"), Decimal("86400"))
    dedup_key = policy.get("dedup_key") or ["event_id"]
    if not isinstance(dedup_key, list) or not dedup_key:
        raise ValueError("policy.dedup_key must be a non-empty list")
    if len(dedup_key) > 3:
        raise ValueError("policy.dedup_key supports at most 3 fields")
    for field in dedup_key:
        if field not in DEDUP_FIELDS:
            raise ValueError("policy.dedup_key field not supported: " + str(field))
    if len(set(dedup_key)) != len(dedup_key):
        raise ValueError("policy.dedup_key must not repeat a field")
    allow_ooo = parse_bool(policy.get("allow_out_of_order", True), "policy.allow_out_of_order")
    window_raw = policy.get("observation_window_seconds")
    window = integer(window_raw, "policy.observation_window_seconds", Decimal("60"), Decimal("31536000")) \
        if window_raw is not None and str(window_raw).strip() != "" else None
    tz = policy.get("timezone")
    tz_text = clean_text(str(tz), "policy.timezone", maximum=64, allow_empty=True) if tz is not None else ""
    return {"success_status_min": low, "success_status_max": high, "max_response_seconds": max_response,
            "dedup_key": list(dedup_key), "allow_out_of_order": allow_ooo,
            "observation_window_seconds": window, "timezone": tz_text}


def parse_delivery(item, as_of, seen_ids):
    if not isinstance(item, dict):
        raise ValueError("each delivery must be an object")
    delivery_id = clean_text(item.get("delivery_id") or item.get("id"), "delivery_id", maximum=120)
    if delivery_id in seen_ids:
        raise ValueError("duplicate delivery_id: " + delivery_id)
    seen_ids.add(delivery_id)
    event_id = clean_text(item.get("event_id"), "delivery.event_id", maximum=120)
    event_type = clean_text(item.get("event_type"), "delivery.event_type", maximum=120)
    object_id = clean_text(item.get("object_id"), "delivery.object_id", maximum=120, allow_empty=True)
    attempt_no = integer(item.get("attempt_no", 1), "delivery.attempt_no", Decimal("1"), Decimal("100000"))
    sent_at = parse_dt(item.get("sent_at"), "delivery.sent_at")
    status_raw = item.get("http_status")
    http_status = None
    if status_raw is not None and str(status_raw).strip() != "":
        http_status = integer(status_raw, "delivery.http_status", Decimal("0"), Decimal("999"))
    response_at = parse_dt(item.get("response_at"), "delivery.response_at", required=False)
    manual_raw = item.get("manual_resend")
    manual_resend = parse_bool(manual_raw, "delivery.manual_resend") if manual_raw is not None else False
    sig_raw = item.get("signature_verified")
    if sig_raw is None or (isinstance(sig_raw, str) and sig_raw.strip() == ""):
        signature = None
    else:
        signature = parse_bool(sig_raw, "delivery.signature_verified")
    return {"delivery_id": delivery_id, "event_id": event_id, "event_type": event_type,
            "object_id": object_id, "attempt_no": attempt_no, "sent_at": sent_at,
            "http_status": http_status, "response_at": response_at,
            "manual_resend": manual_resend, "signature_verified": signature,
            "future": sent_at > as_of}


def parse_processing(item, as_of, seen):
    if not isinstance(item, dict):
        raise ValueError("each processing record must be an object")
    event_id = clean_text(item.get("event_id"), "processing.event_id", maximum=120)
    object_id = clean_text(item.get("object_id"), "processing.object_id", maximum=120, allow_empty=True)
    event_type = clean_text(item.get("event_type"), "processing.event_type", maximum=120)
    status = clean_text(str(item.get("processing_status") or ""), "processing.processing_status", maximum=32)
    processed_at = parse_dt(item.get("processed_at"), "processing.processed_at")
    ref_raw = item.get("side_effect_reference")
    reference = clean_text(str(ref_raw), "processing.side_effect_reference", maximum=160, allow_empty=True) \
        if ref_raw is not None else ""
    key = (event_id, object_id, event_type, processed_at.isoformat(), status)
    if key in seen:
        raise ValueError("duplicate processing record: " + event_id)
    seen.add(key)
    return {"event_id": event_id, "object_id": object_id, "event_type": event_type,
            "processing_status": status, "processed_at": processed_at,
            "side_effect_reference": reference, "future": processed_at > as_of}


def dedup_value(record, dedup_key):
    parts = []
    for field in dedup_key:
        raw = record.get(field)
        parts.append(raw if isinstance(raw, str) and raw else "-")
    return "|".join(parts)


def classify_delivery(delivery, policy, as_of):
    if delivery["future"]:
        return "future", "sent_at 晚于基准时间"
    if delivery["http_status"] is None:
        return "no_status", "缺 http_status"
    if not policy["success_status_min"] <= delivery["http_status"] <= policy["success_status_max"]:
        return "failed", "HTTP %d 不在成功区间" % delivery["http_status"]
    if delivery["response_at"] is None:
        return "no_response", "2xx 但缺 response_at，无法证明响应时间"
    if delivery["response_at"] < delivery["sent_at"]:
        return "no_response", "response_at 早于 sent_at"
    elapsed = seconds_between(delivery["response_at"], delivery["sent_at"])
    if elapsed > policy["max_response_seconds"]:
        return "timeout", "响应 %s s 超过上限 %s s" % (quant(elapsed), quant(policy["max_response_seconds"]))
    return "success", ""


def audit_event(event_id, deliveries, processing, policy, as_of, expected_ids, dedup_groups):
    deliveries = sorted(deliveries, key=lambda d: (d["attempt_no"], d["sent_at"]))
    outcomes = []
    successes = []
    for delivery in deliveries:
        outcome, note = classify_delivery(delivery, policy, as_of)
        outcomes.append({"delivery_id": delivery["delivery_id"], "attempt_no": delivery["attempt_no"],
                         "sent_at": delivery["sent_at"].isoformat(), "http_status": delivery["http_status"],
                         "response_at": delivery["response_at"].isoformat() if delivery["response_at"] else None,
                         "manual_resend": delivery["manual_resend"],
                         "signature_verified": delivery["signature_verified"],
                         "outcome": outcome, "note": note})
        if outcome == "success":
            successes.append(delivery)

    proc_success = [p for p in processing if p["processing_status"].lower() in PROCESSING_SUCCESS]
    refs = sorted({p["side_effect_reference"] for p in proc_success if p["side_effect_reference"]})

    sample = deliveries[0] if deliveries else (processing[0] if processing else None)
    object_id = sample["object_id"] if sample else ""
    event_type = sample["event_type"] if sample else ""

    reasons = []
    review_flags = []
    for item in outcomes:
        if item["outcome"] == "future":
            review_flags.append("FUTURE_TIMESTAMP:" + item["delivery_id"])
        elif item["outcome"] == "timeout":
            review_flags.append("RESPONSE_TOO_SLOW:" + item["delivery_id"])
        elif item["outcome"] == "no_response":
            review_flags.append("RESPONSE_MISSING:" + item["delivery_id"])

    manual = [d for d in deliveries if d["manual_resend"]]
    automatic = [d for d in deliveries if not d["manual_resend"]]
    if manual and automatic:
        review_flags.append("MANUAL_RESEND_CONFLICT")
        reasons.append("手工补发与自动重试并存（%d 次自动 / %d 次手工），需确认补发是否被去重键拦住"
                       % (len(automatic), len(manual)))

    in_expected = event_id in expected_ids
    if not in_expected and expected_ids:
        review_flags.append("UNEXPECTED_EVENT")

    group = dedup_groups.get(dedup_value(sample, policy["dedup_key"]), []) if sample else []
    duplicate_event = len(group) > 1

    signature_values = {d["signature_verified"] for d in successes}
    if successes and signature_values == {None}:
        signature_state = "unknown"
    elif successes and None in signature_values:
        signature_state = "partial"
    elif successes and signature_values == {True}:
        signature_state = "verified"
    elif successes and signature_values == {False}:
        signature_state = "unverified"
    else:
        signature_state = "no_success_delivery"

    status = None
    if not deliveries:
        if in_expected:
            status = "MISSING_DELIVERY"
            reasons.append("期望清单中的事件没有任何投递记录（无法证明已送达）")
        elif proc_success:
            status = "UNKNOWN"
            reasons.append("存在处理成功记录但没有任何投递记录，投递侧证据缺失")
        else:
            status = "UNKNOWN"
            reasons.append("仅出现在期望清单之外且无投递/处理证据")
    elif duplicate_event:
        status = "DUPLICATE_EVENT_CANDIDATE"
        reasons.append("去重键 %s 在多个 event_id 上重复出现（%s），需确认是否同一业务事件的重复投递"
                       % ("+".join(policy["dedup_key"]), ", ".join(sorted(group))))
    elif len(refs) >= 2:
        status = "DUPLICATE_EFFECT_CANDIDATE"
        reasons.append("同一事件出现 %d 个不同的 side_effect_reference（%s），疑似重复执行业务副作用"
                       % (len(refs), ", ".join(refs)))
    elif successes and not proc_success:
        status = "ACK_WITHOUT_PROCESSING_EVIDENCE"
        reasons.append("投递已 2xx 但没有任何处理成功记录：不能证明业务已执行")
    elif not successes and deliveries:
        status = "RETRY_PENDING"
        reasons.append("没有任何成功的投递（%d 次尝试均非 2xx/超时/缺响应），重试仍在进行"
                       % len(deliveries))
    elif successes and proc_success:
        if signature_state in ("unknown", "partial", "unverified"):
            status = "SIGNATURE_UNKNOWN"
            reasons.append("存在成功投递但签名校验状态为 %s，不能确认来源可信" % signature_state)
        else:
            status = "PASS"
            reasons.append("重试后成功投递且仅有一次处理成功记录，幂等成立")
    else:
        status = "UNKNOWN"
        reasons.append("证据不足以判定")

    out_of_order = False
    return {"event_id": event_id, "object_id": object_id, "event_type": event_type,
            "dedup_key_value": dedup_value(sample, policy["dedup_key"]) if sample else "",
            "in_expected_list": in_expected, "delivery_total": len(deliveries),
            "delivery_success": len(successes), "retry_candidates": max(0, len(deliveries) - 1),
            "processing_total": len(processing), "processing_success": len(proc_success),
            "side_effect_references": refs, "signature_state": signature_state,
            "status": status, "reasons": reasons, "review_flags": review_flags,
            "timeline": outcomes, "_successes": successes, "_proc_success": proc_success,
            "_out_of_order": out_of_order}


def apply_out_of_order(results, policy):
    """Flag events whose processing order contradicts delivery order for one object."""
    groups = {}
    for result in results:
        if result["_successes"] and result["_proc_success"]:
            key = result["object_id"] or result["event_id"]
            groups.setdefault(key, []).append(result)
    for members in groups.values():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=lambda r: min(d["sent_at"] for d in r["_successes"]))
        last_processed = None
        for result in ordered:
            processed = min(p["processed_at"] for p in result["_proc_success"])
            if last_processed is not None and processed < last_processed:
                result["review_flags"].append("OUT_OF_ORDER")
                result["reasons"].append("处理顺序与投递顺序不一致（同一去重键下后投递的事件先被处理）")
                result["_out_of_order"] = True
                if policy["allow_out_of_order"] is False and result["status"] == "PASS":
                    result["status"] = "UNKNOWN"
                    result["reasons"].append("策略不允许乱序，已降级为 UNKNOWN 待人工核对")
            last_processed = processed if last_processed is None else max(last_processed, processed)


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    as_of = parse_dt(data.get("as_of"), "as_of")
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    policy = parse_policy(data.get("policy") or {})

    expected_raw = data.get("expected_event_ids")
    expected_provided = expected_raw is not None
    if expected_raw is None:
        expected_ids = []
    elif isinstance(expected_raw, list):
        expected_ids = [clean_text(item, "expected_event_ids[]", maximum=120) for item in expected_raw]
        if len(set(expected_ids)) != len(expected_ids):
            raise ValueError("expected_event_ids contains duplicates")
    else:
        raise ValueError("expected_event_ids must be a list or null")

    deliveries_raw = data.get("deliveries")
    if not isinstance(deliveries_raw, list) or len(deliveries_raw) > 5000:
        raise ValueError("deliveries must be a list of up to 5000 items")
    seen_delivery_ids = set()
    deliveries = [parse_delivery(item, as_of, seen_delivery_ids) for item in deliveries_raw]

    processing_raw = data.get("processing_records")
    if not isinstance(processing_raw, list) or len(processing_raw) > 5000:
        raise ValueError("processing_records must be a list of up to 5000 items")
    seen_processing = set()
    processing = [parse_processing(item, as_of, seen_processing) for item in processing_raw]

    by_event = {}
    for delivery in deliveries:
        by_event.setdefault(delivery["event_id"], {"deliveries": [], "processing": []})["deliveries"].append(delivery)
    for record in processing:
        by_event.setdefault(record["event_id"], {"deliveries": [], "processing": []})["processing"].append(record)
    for event_id in expected_ids:
        by_event.setdefault(event_id, {"deliveries": [], "processing": []})

    dedup_groups = {}
    for event_id, bucket in by_event.items():
        sample = bucket["deliveries"][0] if bucket["deliveries"] else (
            bucket["processing"][0] if bucket["processing"] else None)
        if sample is None:
            continue
        dedup_groups.setdefault(dedup_value(sample, policy["dedup_key"]), []).append(event_id)

    results = [audit_event(event_id, bucket["deliveries"], bucket["processing"], policy, as_of,
                           expected_ids, dedup_groups)
               for event_id, bucket in sorted(by_event.items())]
    apply_out_of_order(results, policy)

    for result in results:
        result.pop("_successes", None)
        result.pop("_proc_success", None)
        result.pop("_out_of_order", None)

    counts = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1

    duplicate_groups = [{"dedup_key_value": key, "event_ids": sorted(ids)}
                        for key, ids in sorted(dedup_groups.items()) if len(ids) > 1]
    missing = [r["event_id"] for r in results if r["status"] == "MISSING_DELIVERY"]
    top_flags = []
    if not expected_provided:
        top_flags.append("EXPECTED_SET_ABSENT")
    for result in results:
        for flag in result["review_flags"]:
            top_flags.append(result["event_id"] + ":" + flag)

    checklist = []
    for result in results:
        if result["status"] in ("DUPLICATE_EFFECT_CANDIDATE", "DUPLICATE_EVENT_CANDIDATE"):
            checklist.append("事件 %s：重放前先确认业务副作用是否已执行，禁止直接重放" % result["event_id"])
        elif result["status"] == "ACK_WITHOUT_PROCESSING_EVIDENCE":
            checklist.append("事件 %s：已 2xx 但无处理记录，先查业务日志再决定是否补投" % result["event_id"])
        elif result["status"] == "MISSING_DELIVERY":
            checklist.append("事件 %s：期望清单有但无投递，先确认对端是否发出再补发" % result["event_id"])
        elif result["status"] == "RETRY_PENDING":
            checklist.append("事件 %s：仍在重试，等退避结束再判断，不要手工叠加重试" % result["event_id"])
        elif result["status"] == "SIGNATURE_UNKNOWN":
            checklist.append("事件 %s：签名状态未知，先确认密钥/校验配置再采信" % result["event_id"])

    summary = {
        "as_of": as_of.isoformat(),
        "expected_set_provided": expected_provided,
        "expected_event_total": len(expected_ids),
        "event_count": len(results),
        "status_counts": counts,
        "events": results,
        "duplicate_dedup_groups": duplicate_groups,
        "missing_deliveries": missing,
        "review_flags": top_flags,
        "replay_checklist": checklist,
        "markdown_summary": build_markdown(results, as_of, counts, expected_provided, missing, duplicate_groups),
        "note": "同一 event_id 多次投递只代表重试/重复候选，不等于业务重复执行；"
                "本技能只做只读核对，不发起任何重放、不写队列、不修改生产数据。",
    }
    if not expected_provided:
        summary["note"] += " 未提供期望事件清单，结论仅覆盖已观察到的集合，不能声称完整无漏投。"
    return summary


def build_markdown(results, as_of, counts, expected_provided, missing, duplicate_groups):
    rows = [["事件", "去重键", "投递", "成功", "处理成功", "副作用引用", "签名", "状态"]]
    for result in results:
        rows.append([result["event_id"], result["dedup_key_value"], result["delivery_total"],
                     result["delivery_success"], result["processing_success"],
                     len(result["side_effect_references"]), result["signature_state"], result["status"]])
    head = ["# Webhook 投递与幂等核对（基准 %s）\n\n" % as_of.isoformat()]
    head.append("口径：按用户声明的去重键核对投递与处理。**同一 event_id 多次投递只代表重试/重复候选**，"
                "不等于业务重复执行；2xx 但没有处理成功证据 = 未证实已执行。\n\n")
    head.append(md_table(rows))
    head.append("\n\n状态分布：" + ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())) + "。\n")
    if not expected_provided:
        head.append("\n未提供期望事件清单：结论只覆盖已观察集合，**不能声称完整无漏投**。\n")
    if missing:
        head.append("\n漏投候选：" + ", ".join(missing) + "\n")
    if duplicate_groups:
        head.append("\n去重键重复组：" +
                    "; ".join("%s -> %s" % (g["dedup_key_value"], ",".join(g["event_ids"])) for g in duplicate_groups) + "\n")
    head.append("\n本技能不发起重放、不写队列、不修改生产数据；逐事件时间线与原因见 events[]。")
    return "".join(head)


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
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
                          "message": "请对照 references/guide.md 检查带时区基准时间、delivery_id 唯一、"
                                     "policy 去重键/成功码区间、时间合法性与是否混入凭据。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
