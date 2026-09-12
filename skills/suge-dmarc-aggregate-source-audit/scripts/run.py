#!/usr/bin/env python3
"""Offline DMARC aggregate (RUA) report anomaly and sending-source audit.

Parses user-supplied DMARC aggregate XML in memory, dedupes reports, separates
message counts from record counts, aggregates by sending IP, and layers expected
sources / unknown sources / authentication failures / policy-vs-disposition
mismatches.  Strictly offline and read-only: no IP geolocation lookup, no DNS
change, no mail sent, no file or URL opened.
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
SECRET_HINTS = (
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
)
FORBIDDEN_XML = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
EXTERNAL_ID = re.compile(r"\b(SYSTEM|PUBLIC)\b")
RECOVER_ID = re.compile(r"<report_id>\s*([^<]{1,200})")
IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

MAX_XML_CHARS = 200_000
MAX_REPORTS = 500
MAX_RECORDS_PER_REPORT = 20_000
MAX_COUNT = Decimal("100000000")
ALIGNMENT_VALUES = {"pass", "fail"}
DISPOSITIONS = {"none", "quarantine", "reject"}
POLICY_VALUES = {"none", "quarantine", "reject"}


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


def count_number(value, label):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or result != result.to_integral_value():
        raise ValueError(label + " must be a whole number")
    if not Decimal("0") <= result <= MAX_COUNT:
        raise ValueError(label + " is outside the allowed range")
    return int(result)


def epoch_number(value, label):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or result != result.to_integral_value():
        raise ValueError(label + " must be a whole number of seconds")
    if not Decimal("0") <= result <= Decimal("4102444800"):
        raise ValueError(label + " is outside the allowed range")
    return int(result)


def nonneg(value, label, maximum=Decimal("1000000000000")):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or not Decimal("0") <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
    return result


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
        result = abs(result)
    return str(result)


def pct(numerator, denominator, places=2):
    if denominator == 0:
        return None
    return quant(Decimal(numerator) / Decimal(denominator) * Decimal("100"), places) + "%"


def local_name(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def child(element, name):
    if element is None:
        return None
    for node in element:
        if local_name(node.tag) == name:
            return node
    return None


def text_of(element, name, required=True):
    node = child(element, name)
    if node is None or node.text is None or node.text.strip() == "":
        if required:
            raise ValueError("missing <" + name + ">")
        return None
    return node.text.strip()


def all_of(element, name):
    if element is None:
        return []
    return [node for node in element if local_name(node.tag) == name]


def guard_xml(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("report XML must be a nonempty string")
    if len(raw) > MAX_XML_CHARS:
        raise ValueError("report XML exceeds the per-report size limit")
    if CONTROL.search(raw):
        raise ValueError("report XML must not contain control characters")
    if FORBIDDEN_XML.search(raw):
        raise ValueError("report XML must not contain DOCTYPE or ENTITY declarations")
    if EXTERNAL_ID.search(raw):
        raise ValueError("report XML must not declare external SYSTEM/PUBLIC identifiers")
    return raw.strip()


def parse_expected(sources):
    if not isinstance(sources, list) or not 1 <= len(sources) <= 2000:
        raise ValueError("expected_sources must be a list of 1-2000 items")
    seen = set()
    out = []
    for item in sources:
        if not isinstance(item, dict):
            raise ValueError("each expected_sources item must be an object")
        source_id = clean_text(item.get("source_id"), "expected_sources.source_id", 120)
        if source_id in seen:
            raise ValueError("duplicate expected_sources.source_id: " + source_id)
        seen.add(source_id)
        ip = clean_text(item.get("ip"), "expected_sources.ip", 64)
        if not IPV4.match(ip):
            raise ValueError("expected_sources.ip must be an IPv4 address")
        out.append({"source_id": source_id, "ip": ip,
                    "label": optional_text(item.get("label"), "expected_sources.label", 160)
                    or source_id})
    return out


def parse_thresholds(raw):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("thresholds must be an object")
    rate_raw = raw.get("auth_failure_rate_pct")
    rate = nonneg(rate_raw, "thresholds.auth_failure_rate_pct", Decimal("100")) \
        if rate_raw is not None and str(rate_raw).strip() != "" else None
    unknown_raw = raw.get("unknown_source_min_messages", 1)
    unknown = count_number(unknown_raw, "thresholds.unknown_source_min_messages")
    sample_raw = raw.get("min_sample_messages")
    sample = count_number(sample_raw, "thresholds.min_sample_messages") \
        if sample_raw is not None and str(sample_raw).strip() != "" else None
    return {"auth_failure_rate_pct": None if rate is None else quant(rate),
            "unknown_source_min_messages": unknown,
            "min_sample_messages": sample}


def parse_report(item, index):
    label = "xml_reports[%d]" % index
    if not isinstance(item, dict):
        raise ValueError("each xml_reports item must be an object")
    source_id = clean_text(item.get("source_id"), label + ".source_id", 120)
    raw = guard_xml(item.get("xml"))
    errors = []
    report_id = None
    try:
        root = ET.fromstring(raw)
        if local_name(root.tag) != "feedback":
            raise ValueError("root element must be <feedback>")
        metadata = child(root, "report_metadata")
        if metadata is None:
            raise ValueError("missing <report_metadata>")
        report_id = text_of(metadata, "report_id", required=False)
        org_name = text_of(metadata, "org_name", required=False)
        if not report_id:
            errors.append("MISSING_REPORT_ID")
        if not org_name:
            errors.append("MISSING_ORG_NAME")
        date_range = child(metadata, "date_range")
        begin = None
        end = None
        if date_range is None:
            errors.append("MISSING_DATE_RANGE")
        else:
            try:
                begin = epoch_number(text_of(date_range, "begin", required=False), "date_range.begin")
            except ValueError:
                errors.append("INVALID_DATE_RANGE")
            try:
                end = epoch_number(text_of(date_range, "end", required=False), "date_range.end")
            except ValueError:
                errors.append("INVALID_DATE_RANGE")
        policy = child(root, "policy_published")
        policy_published = {"p": None, "sp": None, "pct": None}
        domain = None
        if policy is None:
            errors.append("MISSING_POLICY_PUBLISHED")
        else:
            domain = text_of(policy, "domain", required=False)
            p_value = text_of(policy, "p", required=False)
            sp_value = text_of(policy, "sp", required=False)
            pct_value = text_of(policy, "pct", required=False)
            if p_value is not None:
                if p_value.lower() not in POLICY_VALUES:
                    errors.append("INVALID_POLICY_VALUE")
                else:
                    policy_published["p"] = p_value.lower()
            if sp_value is not None:
                if sp_value.lower() not in POLICY_VALUES:
                    errors.append("INVALID_POLICY_VALUE")
                else:
                    policy_published["sp"] = sp_value.lower()
            if pct_value is not None:
                try:
                    policy_published["pct"] = count_number(pct_value, "policy_published.pct")
                except ValueError:
                    errors.append("INVALID_PCT")
            if p_value is None:
                errors.append("MISSING_POLICY_VALUE")
        records = []
        record_nodes = all_of(root, "record")
        if len(record_nodes) > MAX_RECORDS_PER_REPORT:
            raise ValueError(label + " has too many <record> entries")
        for position, node in enumerate(record_nodes):
            row = child(node, "row")
            if row is None:
                errors.append("RECORD_WITHOUT_ROW")
                continue
            try:
                source_ip = text_of(row, "source_ip")
            except ValueError:
                errors.append("RECORD_WITHOUT_SOURCE_IP")
                continue
            if not IPV4.match(source_ip):
                errors.append("INVALID_SOURCE_IP")
                continue
            try:
                count = count_number(text_of(row, "count", required=False), "record.count")
            except ValueError:
                raise ValueError(label + " record %d has an invalid <count>" % position) from None
            evaluated = child(row, "policy_evaluated")
            disposition = None
            dkim = None
            spf = None
            if evaluated is None:
                errors.append("RECORD_WITHOUT_POLICY_EVALUATED")
            else:
                disposition_raw = text_of(evaluated, "disposition", required=False)
                if disposition_raw is not None:
                    if disposition_raw.lower() not in DISPOSITIONS:
                        errors.append("INVALID_DISPOSITION")
                    else:
                        disposition = disposition_raw.lower()
                dkim_raw = text_of(evaluated, "dkim", required=False)
                spf_raw = text_of(evaluated, "spf", required=False)
                if dkim_raw is not None:
                    if dkim_raw.lower() not in ALIGNMENT_VALUES:
                        errors.append("INVALID_ALIGNMENT")
                    else:
                        dkim = dkim_raw.lower()
                if spf_raw is not None:
                    if spf_raw.lower() not in ALIGNMENT_VALUES:
                        errors.append("INVALID_ALIGNMENT")
                    else:
                        spf = spf_raw.lower()
                if dkim is None or spf is None:
                    errors.append("MISSING_ALIGNMENT")
            identifiers = child(node, "identifiers")
            header_from = text_of(identifiers, "header_from", required=False) \
                if identifiers is not None else None
            records.append({"source_ip": source_ip, "count": count, "disposition": disposition,
                            "dkim": dkim, "spf": spf, "header_from": header_from})
    except ET.ParseError:
        errors.append("XML_PARSE_ERROR")
        recovered = RECOVER_ID.search(raw)
        if recovered:
            report_id = recovered.group(1).strip()
        org_name = None
        domain = None
        begin = end = None
        policy_published = {"p": None, "sp": None, "pct": None}
        records = []
    except ValueError as error:
        if str(error).startswith("root element") or str(error).startswith("missing <"):
            errors.append("XML_STRUCTURE_ERROR")
            recovered = RECOVER_ID.search(raw)
            if recovered:
                report_id = recovered.group(1).strip()
            org_name = None
            domain = None
            begin = end = None
            policy_published = {"p": None, "sp": None, "pct": None}
            records = []
        else:
            raise
    if not records and "XML_PARSE_ERROR" not in errors and "XML_STRUCTURE_ERROR" not in errors:
        errors.append("NO_RECORDS")
    if "XML_PARSE_ERROR" not in errors and "XML_STRUCTURE_ERROR" not in errors:
        if begin is None or end is None or end < begin:
            if "MISSING_DATE_RANGE" not in errors and "INVALID_DATE_RANGE" not in errors:
                errors.append("INVALID_DATE_RANGE")
    message_count = sum(record["count"] for record in records)
    fatal = {"XML_PARSE_ERROR", "XML_STRUCTURE_ERROR", "NO_RECORDS", "MISSING_REPORT_ID",
             "MISSING_DATE_RANGE", "INVALID_DATE_RANGE"}
    status = "INVALID" if fatal & set(errors) else "VALID"
    return {
        "source_id": source_id, "report_id": report_id or source_id,
        "org_name": org_name, "domain": domain,
        "policy_published": policy_published,
        "period_begin": None if begin is None else datetime.fromtimestamp(begin, tz=timezone.utc).isoformat(),
        "period_end": None if end is None else datetime.fromtimestamp(end, tz=timezone.utc).isoformat(),
        "period_begin_epoch": begin, "period_end_epoch": end,
        "record_count": len(records), "message_count": message_count,
        "status": status, "review_flags": sorted(set(errors)), "errors": sorted(set(errors)),
        "duplicate_of": None, "records": records,
        "dedup_key": "%s|%s|%s|%s" % (report_id or source_id, org_name or "", begin, end),
    }


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    raw_as_of = data.get("as_of")
    if not isinstance(raw_as_of, str) or "T" not in raw_as_of.strip():
        raise ValueError("as_of must be an ISO8601 datetime with a UTC offset")
    as_of = parse_dt(raw_as_of, "as_of")
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    org_domain = clean_text(data.get("org_domain"), "org_domain", 255).lower()
    expected = parse_expected(data.get("expected_sources"))
    thresholds = parse_thresholds(data.get("thresholds"))
    reports_raw = data.get("xml_reports")
    if not isinstance(reports_raw, list) or not 1 <= len(reports_raw) <= MAX_REPORTS:
        raise ValueError("xml_reports must be a list of 1-%d items" % MAX_REPORTS)

    reports = [parse_report(item, index) for index, item in enumerate(reports_raw)]

    seen_keys = {}
    for report in reports:
        if report["status"] != "VALID":
            continue
        key = report["dedup_key"]
        if key in seen_keys:
            report["status"] = "DUPLICATE"
            report["duplicate_of"] = seen_keys[key]
            report["review_flags"] = sorted(set(report["review_flags"]) | {"DUPLICATE_REPORT"})
        else:
            seen_keys[key] = report["report_id"]

    valid = [report for report in reports if report["status"] == "VALID"]
    invalid = [report for report in reports if report["status"] == "INVALID"]
    duplicates = [report for report in reports if report["status"] == "DUPLICATE"]

    expected_by_ip = {}
    for entry in expected:
        expected_by_ip.setdefault(entry["ip"], []).append(entry)

    baseline_policy = {}
    for report in sorted(valid, key=lambda item: (item["period_begin_epoch"], item["report_id"])):
        domain = report["domain"] or org_domain
        current = report["policy_published"]["p"]
        if current is None:
            continue
        if domain in baseline_policy and baseline_policy[domain] != current:
            report["review_flags"] = sorted(set(report["review_flags"]) | {"POLICY_CHANGED"})
        else:
            baseline_policy.setdefault(domain, current)

    buckets = {}
    policy_mismatches = []
    for report in valid:
        for record in report["records"]:
            bucket = buckets.setdefault(record["source_ip"], {
                "source_ip": record["source_ip"], "message_count": 0, "record_count": 0,
                "report_ids": set(), "dkim_pass_messages": 0, "spf_pass_messages": 0,
                "dmarc_pass_messages": 0, "auth_failure_messages": 0, "unverifiable_messages": 0})
            bucket["message_count"] += record["count"]
            bucket["record_count"] += 1
            bucket["report_ids"].add(report["report_id"])
            if record["dkim"] == "pass":
                bucket["dkim_pass_messages"] += record["count"]
            if record["spf"] == "pass":
                bucket["spf_pass_messages"] += record["count"]
            known = record["dkim"] in ALIGNMENT_VALUES and record["spf"] in ALIGNMENT_VALUES
            if not known:
                bucket["unverifiable_messages"] += record["count"]
                continue
            if record["dkim"] == "pass" and record["spf"] == "pass":
                bucket["dmarc_pass_messages"] += record["count"]
            else:
                bucket["auth_failure_messages"] += record["count"]
            policy_value = report["policy_published"]["p"]
            pct_value = report["policy_published"]["pct"]
            disposition = record["disposition"]
            if policy_value is None or disposition is None:
                continue
            enforced = policy_value in ("quarantine", "reject") and (pct_value is None or pct_value == 100)
            failed = not (record["dkim"] == "pass" and record["spf"] == "pass")
            over = policy_value == "none" and disposition in ("quarantine", "reject")
            if (enforced and failed and disposition == "none") or over:
                policy_mismatches.append({
                    "source_ip": record["source_ip"], "report_id": report["report_id"],
                    "policy_published_p": policy_value, "pct": pct_value,
                    "disposition": disposition, "message_count": record["count"]})

    sources = []
    for source_ip in sorted(buckets):
        bucket = buckets[source_ip]
        matches = expected_by_ip.get(source_ip, [])
        unknown = not matches
        if bucket["auth_failure_messages"] > 0:
            status = "AUTH_FAILURE"
        elif unknown:
            status = "UNKNOWN_SOURCE"
        else:
            status = "OK"
        sources.append({
            "source_ip": source_ip,
            "message_count": bucket["message_count"], "record_count": bucket["record_count"],
            "report_ids": sorted(bucket["report_ids"]),
            "expected": bool(matches),
            "expected_source_ids": sorted(entry["source_id"] for entry in matches),
            "expected_labels": sorted(entry["label"] for entry in matches),
            "dkim_pass_messages": bucket["dkim_pass_messages"],
            "spf_pass_messages": bucket["spf_pass_messages"],
            "dmarc_pass_messages": bucket["dmarc_pass_messages"],
            "auth_failure_messages": bucket["auth_failure_messages"],
            "unverifiable_messages": bucket["unverifiable_messages"],
            "dmarc_pass_rate_pct": pct(bucket["dmarc_pass_messages"],
                                       bucket["dmarc_pass_messages"] + bucket["auth_failure_messages"]),
            "status": status,
        })

    message_total = sum(report["message_count"] for report in valid)
    record_total = sum(report["record_count"] for report in valid)
    pass_total = sum(source["dmarc_pass_messages"] for source in sources)
    fail_total = sum(source["auth_failure_messages"] for source in sources)
    unverifiable_total = sum(source["unverifiable_messages"] for source in sources)
    unknown_sources = [{"source_ip": source["source_ip"], "message_count": source["message_count"],
                        "record_count": source["record_count"]}
                       for source in sources
                       if not source["expected"]
                       and source["message_count"] >= thresholds["unknown_source_min_messages"]]
    auth_failures = [{"source_ip": source["source_ip"], "message_count": source["auth_failure_messages"],
                      "dkim_fail_messages": source["message_count"] - source["dkim_pass_messages"],
                      "spf_fail_messages": source["message_count"] - source["spf_pass_messages"]}
                     for source in sources if source["auth_failure_messages"] > 0]

    overlaps = []
    ordered = sorted(valid, key=lambda item: (item["period_begin_epoch"], item["report_id"]))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if left["report_id"] == right["report_id"]:
                continue
            start = max(left["period_begin_epoch"], right["period_begin_epoch"])
            stop = min(left["period_end_epoch"], right["period_end_epoch"])
            if stop > start:
                overlaps.append({"report_ids": sorted([left["report_id"], right["report_id"]]),
                                 "overlap_hours": quant(Decimal(stop - start) / Decimal("3600"))})

    if valid:
        begin_epoch = min(report["period_begin_epoch"] for report in valid)
        end_epoch = max(report["period_end_epoch"] for report in valid)
        coverage = {"begin": datetime.fromtimestamp(begin_epoch, tz=timezone.utc).isoformat(),
                    "end": datetime.fromtimestamp(end_epoch, tz=timezone.utc).isoformat(),
                    "begin_epoch": begin_epoch, "end_epoch": end_epoch,
                    "span_hours": quant(Decimal(end_epoch - begin_epoch) / Decimal("3600"))}
    else:
        coverage = {"begin": None, "end": None, "begin_epoch": None, "end_epoch": None,
                    "span_hours": None}

    attention = []
    if thresholds["min_sample_messages"] is not None and message_total < thresholds["min_sample_messages"]:
        attention.append({"code": "LOW_SAMPLE",
                          "detail": "有效报告仅覆盖 %d 封邮件，低于声明的最小样本 %d"
                                    % (message_total, thresholds["min_sample_messages"])})
    failure_rate = pct(fail_total, pass_total + fail_total)
    if thresholds["auth_failure_rate_pct"] is not None and failure_rate is not None:
        if Decimal(failure_rate.rstrip("%")) > Decimal(thresholds["auth_failure_rate_pct"]):
            attention.append({"code": "AUTH_FAILURE_RATE_EXCEEDED",
                              "detail": "认证失败率 %s 超过声明阈值 %s%%"
                                        % (failure_rate, thresholds["auth_failure_rate_pct"])})

    if not valid:
        status = "INVALID"
    elif unknown_sources:
        status = "UNKNOWN_SOURCE"
    elif fail_total > 0:
        status = "AUTH_FAILURE"
    elif policy_mismatches:
        status = "POLICY_MISMATCH"
    elif invalid:
        status = "PARTIAL"
    elif attention:
        status = "ATTENTION"
    else:
        status = "PASS"

    checklist = []
    if unknown_sources:
        checklist.append("逐一确认未知发信源是否为自有基础设施；不是则按发信渠道排查。")
    if fail_total:
        checklist.append("按失败来源核对 SPF/DKIM 对齐，确认是配置问题还是仿冒。")
    if policy_mismatches:
        checklist.append("核对策略声明与实际 disposition 不一致的记录，确认是否有 pct 抽样或网关改写。")
    if invalid:
        checklist.append("补齐解析失败报告，避免只凭部分报告下结论。")
    if overlaps:
        checklist.append("核对时间窗重叠的报告是否重复投递。")
    checklist.append("聚合统计不是单封邮件证据，最终判定需人工结合原始日志。")

    result = {
        "as_of": as_of.isoformat(),
        "org_domain": org_domain,
        "status": status,
        "report_count": len(reports),
        "valid_report_count": len(valid),
        "invalid_report_count": len(invalid),
        "duplicate_report_count": len(duplicates),
        "message_count_total": message_total,
        "record_count_total": record_total,
        "dmarc_pass_message_total": pass_total,
        "auth_failure_message_total": fail_total,
        "unverifiable_message_total": unverifiable_total,
        "dmarc_pass_rate_pct": pct(pass_total, pass_total + fail_total),
        "auth_failure_rate_pct": failure_rate,
        "reports": [{"source_id": report["source_id"], "report_id": report["report_id"],
                     "org_name": report["org_name"], "domain": report["domain"],
                     "policy_published": report["policy_published"],
                     "period_begin": report["period_begin"], "period_end": report["period_end"],
                     "record_count": report["record_count"], "message_count": report["message_count"],
                     "status": report["status"], "review_flags": report["review_flags"],
                     "errors": report["errors"], "duplicate_of": report["duplicate_of"]}
                    for report in reports],
        "duplicate_reports": [{"dedup_key": report["dedup_key"],
                               "report_ids": sorted([report["report_id"], report["duplicate_of"]]),
                               "source_ids": [report["source_id"]]}
                              for report in duplicates],
        "sources": sources,
        "unknown_sources": unknown_sources,
        "auth_failures": auth_failures,
        "policy_mismatches": sorted(policy_mismatches,
                                    key=lambda item: (item["source_ip"], item["report_id"])),
        "coverage": coverage,
        "window_overlaps": overlaps,
        "thresholds": thresholds,
        "attention": attention,
        "manual_checklist": checklist,
        "note": "只解析用户提供的 DMARC 聚合 XML，不查询 IP 归属、不修改 DNS、不发送邮件、"
                "不打开路径或 URL、不解析外部实体。记录条数不是邮件量，消息数与记录数分别统计。"
                "聚合统计不构成单封邮件证据，也不建议直接从 p=none 跳到 reject。",
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
    lines = ["# DMARC 聚合报告异常与发信源审计（%s）\n\n" % result["org_domain"]]
    lines.append("基准时间 %s。报告 %d 份（有效 %d / 重复 %d / 无效 %d），"
                 "覆盖 %s ~ %s。\n\n" % (
                     result["as_of"], result["report_count"], result["valid_report_count"],
                     result["duplicate_report_count"], result["invalid_report_count"],
                     result["coverage"]["begin"] or "—", result["coverage"]["end"] or "—"))
    lines.append("**消息数（邮件量）合计 %d；记录数合计 %d。**记录条数不是邮件量，两者不可混用。\n\n"
                 % (result["message_count_total"], result["record_count_total"]))
    lines.append("DMARC 通过率 %s；认证失败 %d 封；无法判定对齐 %d 封。\n\n"
                 % (result["dmarc_pass_rate_pct"] or "—", result["auth_failure_message_total"],
                    result["unverifiable_message_total"]))
    rows = [["来源 IP", "期望来源", "消息数", "记录数", "DMARC 通过", "认证失败", "状态"]]
    for source in result["sources"]:
        rows.append([source["source_ip"],
                     ", ".join(source["expected_labels"]) if source["expected"] else "未声明",
                     source["message_count"], source["record_count"],
                     source["dmarc_pass_messages"], source["auth_failure_messages"],
                     source["status"]])
    lines.append(md_table(rows))
    lines.append("\n\n总体判定：**%s**。\n" % result["status"])
    if result["unknown_sources"]:
        lines.append("\n未知来源：" + ", ".join(
            "%s（%d 封）" % (item["source_ip"], item["message_count"])
            for item in result["unknown_sources"]) + "。\n")
    if result["policy_mismatches"]:
        lines.append("\n策略与实际 disposition 不一致 %d 条。\n" % len(result["policy_mismatches"]))
    lines.append("\n人工核对清单：\n")
    for item in result["manual_checklist"]:
        lines.append("- " + item + "\n")
    lines.append("\n本技能不查询 IP 归属、不修改 DNS、不建议直接从 p=none 跳到 reject，"
                 "聚合统计不当作单封邮件证据。")
    return "".join(lines)


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
                          "message": "请对照 references/guide.md 检查：as_of 带时区、org_domain 非空、"
                                     "expected_sources[].ip 为 IPv4、xml_reports[] 为含 source_id 与 "
                                     "xml 的对象且不含 DOCTYPE/ENTITY、record count 为 0~100000000 的整数。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
