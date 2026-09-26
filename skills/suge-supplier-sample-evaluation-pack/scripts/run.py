#!/usr/bin/env python3
"""供应商样品评估准备包 — offline supplier sample evidence pack.

Pure Python 3.9+ standard library. Reads exactly one local JSON file, writes one
JSON document to stdout. No network, no filesystem writes, no image or document
reading, no command execution, no lab or certification conclusion.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = "1.0.0"

CELL_PASS = "PASS"
CELL_FAIL = "FAIL"
CELL_NOT_TESTED = "NOT_TESTED"
CELL_UNKNOWN = "UNKNOWN"

SAMPLE_PASS = "REQUIRED_SPECS_PASS"
SAMPLE_INCONCLUSIVE = "INCONCLUSIVE"
SAMPLE_FAIL = "FAIL"

BLOCKER = "BLOCKER"
REVIEW = "REVIEW"

PLACEHOLDER = "已隐藏疑似提示注入文本"

# Unit conversion is a closed whitelist. Anything outside it is "unit unknown" and
# the cell can never be judged PASS. Groups are never mixed.
UNIT_GROUPS = (
    ("mass", {"mg": "0.001", "g": "1", "kg": "1000"}),
    ("length", {"mm": "0.1", "cm": "1", "m": "100"}),
    ("area", {"cm2": "1", "m2": "10000"}),
    ("area_density", {"g/m2": "1", "kg/m2": "1000"}),
    ("percent", {"%": "1", "percent": "1"}),
    ("grade", {"级": "1"}),
    ("count", {"件": "1", "个": "1", "只": "1", "条": "1", "pcs": "1", "pc": "1"}),
)

CODE_LIST = (
    "INVALID_ATTACHMENT_REF", "DUPLICATE_SPEC_ID", "UNKNOWN_SPEC_ID",
    "DUPLICATE_SUPPLIER_ID", "DUPLICATE_SAMPLE_ID",
    "INVALID_PRICE", "PRICE_CURRENCY_UNKNOWN", "PRICE_MISSING",
    "SPEC_RANGE_MISSING", "SPEC_UNIT_UNSPECIFIED",
    "UNKNOWN_UNIT", "UNIT_INCOMPATIBLE", "UNIT_MISMATCH", "EVIDENCE_MISSING",
    "VALUE_INVALID",
    "REQUIRED_SPEC_FAIL", "REQUIRED_SPEC_UNVERIFIED",
)

CRED_KEY = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|auth[_-]?token|session[_-]?id)",
    re.I,
)
CRED_VALUE = (
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE " + r"KEY-----"),
    re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)

INJ_ACTION = (
    "忽略", "无视", "跳过", "覆盖", "改写", "删除", "执行", "服从", "绕过",
    "ignore", "disregard", "override", "bypass", "forget",
)
INJ_TARGET = (
    "指令", "规则", "提示", "系统", "要求", "约束", "设定",
    "instruction", "rule", "prompt", "system", "constraint",
)

MD_ESCAPE = "\\`*_{}[]()#+-|<>~!"

DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
# An attachment reference must be a bare file name. Anything with a separator, a
# parent segment, a scheme or a drive letter is refused and never resolved.
UNSAFE_REF_RE = re.compile(r"[/\\]|\.\.|^[A-Za-z][A-Za-z0-9+.-]*:")

DISCLAIMER = (
    "本输出只是根据你提交的规格、报价与自测观察整理的证据矩阵，不是实验室检测报告，"
    "不给质量、安全、认证、合规、法律或最终采购结论；未测试、证据缺失或单位不可比较"
    "一律不判通过。附件只记录安全化后的文件名，未打开、未读取、未解析任何文件内容；"
    "是否可用、是否下单由你与有资质的人员决定。"
)

NOT_CONCLUDED = (
    "是否满足质量体系或认证要求",
    "是否满足强制性标准、法规或平台合规要求",
    "是否存在安全、健康或环保风险",
    "是否存在法律、合同或知识产权风险",
    "是否应该采购该供应商，或采购价格是否合理",
    "样品结果能否代表批量生产的实际水平",
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = "".join(ch for ch in text if ch == "\n" or ch == "\t" or ord(ch) >= 32)
    return re.sub(r"\s+", " ", text).strip()


def esc(value):
    text = clean_text(value)
    return "".join("\\" + ch if ch in MD_ESCAPE else ch for ch in text)


def has_text(value):
    return isinstance(value, str) and value.strip() != ""


def injection_hit(value):
    if not isinstance(value, str) or not value:
        return False
    for sentence in re.split(r"[。！？!?;\n]", value):
        probe = sentence.lower()
        if any(a in probe for a in INJ_ACTION) and any(t in probe for t in INJ_TARGET):
            return True
    return False


def hidden(value):
    if injection_hit(value):
        return PLACEHOLDER
    return esc(value)


def quant(value, places=2):
    exponent = Decimal(1).scaleb(-places)
    out = Decimal(value).quantize(exponent, rounding=ROUND_HALF_UP)
    return abs(out) if out == 0 else out


def amount(value):
    return str(quant(value, 2)) if value is not None else None


def trim(value, places=4):
    if value is None:
        return None
    text = format(quant(value, places), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def dec(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal, str)):
        text = str(value).strip()
        if text == "":
            return None
        try:
            parsed = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
        if not parsed.is_finite():
            return None
        return parsed
    return None


def as_amount(value):
    if value is None:
        return None, False, True
    parsed = dec(value)
    if parsed is None:
        return None, True, False
    return parsed, True, True


def norm_unit(value):
    """Compatibility-normalise a unit token: g/m², G/M2 and g / m2 are one unit."""
    if not has_text(value):
        return None
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", "", text)


def unit_group(normalized):
    if normalized is None:
        return None
    for name, members in UNIT_GROUPS:
        if normalized in members:
            return name
    return None


def text_key(value):
    """Comparison key for text specs: normalised, case-folded, whitespace removed."""
    if not has_text(value):
        return None
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", "", text)


def safe_basename(text):
    """Last path segment only, with the scheme or drive prefix stripped.

    A refused reference is never echoed back as a path: only this sanitised file
    name may be recorded, so no directory structure ever leaves the process and no
    file is opened or resolved.
    """
    if not has_text(text):
        return None
    probe = re.split(r"[\\/]", clean_text(text))[-1]
    probe = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*:", "", probe)
    probe = probe.replace("..", "").strip()
    if not probe or len(probe) > 120:
        return None
    return probe


def find_credentials(node, path="/", hits=None):
    if hits is None:
        hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = path + str(key)
            if CRED_KEY.search(str(key)):
                hits.append({"path": here, "reason": "CREDENTIAL_FIELD_NAME"})
                continue
            find_credentials(value, here + "/", hits)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            find_credentials(value, path + "[%d]/" % index, hits)
    elif isinstance(node, str):
        for pattern in CRED_VALUE:
            if pattern.search(node):
                hits.append({"path": path.rstrip("/") or "/", "reason": "CREDENTIAL_VALUE_SHAPE"})
                break
    return hits


def reject(hits):
    return {
        "version": VERSION,
        "status": "REJECTED",
        "reason": "CREDENTIAL_DETECTED",
        "credential_findings": [{"path": h["path"], "reason": h["reason"]} for h in hits],
        "as_of": None,
        "requirement": None,
        "counts": {"specs": 0, "required_specs": 0, "suppliers": 0, "samples": 0,
                   "observations": 0, "cell_counts": {}, "sample_verdict_counts": {}},
        "specs": [], "suppliers": [], "samples": [], "evidence_matrix": [],
        "spec_summary": [], "sample_differences": [], "non_comparable_items": [],
        "retest_items": [], "follow_up_questions": [], "excluded_quotes": [],
        "quote_summary_by_currency": {}, "attachment_index": [],
        "decision_pack": [], "findings": [], "finding_counts": {code: 0 for code in CODE_LIST},
        "blocker_count": 0, "review_count": 0,
        "not_concluded": list(NOT_CONCLUDED),
        "markdown_summary": "# 供应商样品评估准备单\n\n- 状态：**REJECTED**\n- 已拒绝处理，未回显疑似凭据内容。\n",
        "injection_flagged": [], "input_warnings": ["CREDENTIAL_DETECTED"],
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def analyse(data):
    if not isinstance(data, dict):
        data = {}
    hits = find_credentials(data)
    if hits:
        return reject(hits)

    warnings = []
    findings = []

    def add(code, severity, supplier_id, sample_id, subject, detail):
        findings.append({
            "code": code, "severity": severity, "supplier_id": supplier_id,
            "sample_id": sample_id, "subject": subject, "detail": detail,
        })

    as_of_raw = data.get("as_of")
    as_of_ok = False
    if not has_text(as_of_raw):
        warnings.append("AS_OF_MISSING")
    elif not DT_RE.match(clean_text(as_of_raw)):
        warnings.append("AS_OF_TIMEZONE_MISSING_OR_INVALID")
    else:
        as_of_ok = True

    # ---- requirement specs ----
    requirement_raw = data.get("requirement") if isinstance(data.get("requirement"), dict) else {}
    requirement_id = clean_text(requirement_raw.get("requirement_id")) if has_text(requirement_raw.get("requirement_id")) else None
    requirement_name = clean_text(requirement_raw.get("name")) if has_text(requirement_raw.get("name")) else None
    specs = []
    spec_index = {}
    duplicate_specs = []
    for index, entry in enumerate(requirement_raw.get("specs") if isinstance(requirement_raw.get("specs"), list) else []):
        if not isinstance(entry, dict):
            continue
        spec_id = clean_text(entry.get("spec_id")) if has_text(entry.get("spec_id")) else None
        name = clean_text(entry.get("name")) if has_text(entry.get("name")) else ""
        kind = clean_text(entry.get("kind")).lower() if has_text(entry.get("kind")) else None
        required = entry.get("required") is True
        target = clean_text(entry.get("target")) if has_text(entry.get("target")) else None
        spec_unit_raw = entry.get("unit")
        spec_unit = clean_text(spec_unit_raw) if has_text(spec_unit_raw) else None
        spec_unit_norm = norm_unit(spec_unit)
        value_min, min_present, min_valid = as_amount(entry.get("min"))
        value_max, max_present, max_valid = as_amount(entry.get("max"))

        flags = []
        if spec_id is None:
            flags.append("MISSING_SPEC_ID")
        elif spec_id in spec_index:
            flags.append("DUPLICATE_SPEC_ID")
            duplicate_specs.append(spec_id)
            add("DUPLICATE_SPEC_ID", BLOCKER, None, None, "spec:%s" % spec_id,
                "同一个规格编号在需求里出现了多次，无法确定应以哪一条为准。")
        if not name:
            flags.append("NAME_MISSING")
        if kind not in ("numeric", "text"):
            flags.append("SPEC_KIND_UNKNOWN")
            kind = kind if kind else "unknown"
        if kind == "numeric":
            if not min_present and not max_present:
                flags.append("SPEC_RANGE_MISSING")
                add("SPEC_RANGE_MISSING", REVIEW, None, None, "spec:%s" % spec_id,
                    "数值规格没有给出下限或上限，无法判定通过与否。")
            if spec_unit_norm is not None and unit_group(spec_unit_norm) is None:
                flags.append("SPEC_UNIT_UNKNOWN")
        if kind == "text" and target is None:
            flags.append("SPEC_TARGET_MISSING")
        if kind == "numeric" and spec_unit is None:
            flags.append("SPEC_UNIT_UNSPECIFIED")
            add("SPEC_UNIT_UNSPECIFIED", REVIEW, None, None, "spec:%s" % spec_id,
                "数值规格没有给出单位，只能按无单位数值比较，请确认是否需要补充单位。")

        record = {
            "spec_id": spec_id, "name": name, "kind": kind, "required": required,
            "target": target, "min": value_min, "max": value_max,
            "unit": spec_unit, "unit_norm": spec_unit_norm,
            "unit_group": unit_group(spec_unit_norm),
            "review_flags": sorted(set(flags)), "index": index,
        }
        specs.append(record)
        if spec_id is not None and spec_id not in spec_index:
            spec_index[spec_id] = record

    # ---- suppliers and samples ----
    suppliers_raw = data.get("suppliers") if isinstance(data.get("suppliers"), list) else []
    suppliers = []
    samples = []
    observations = []
    supplier_seen = {}
    sample_seen = {}
    attachment_index = []
    excluded_quotes = []
    for s_index, entry in enumerate(suppliers_raw):
        if not isinstance(entry, dict):
            continue
        supplier_id = clean_text(entry.get("supplier_id")) if has_text(entry.get("supplier_id")) else None
        name = clean_text(entry.get("name")) if has_text(entry.get("name")) else ""
        flags = []
        if supplier_id is None:
            flags.append("MISSING_SUPPLIER_ID")
        elif supplier_id in supplier_seen:
            flags.append("DUPLICATE_SUPPLIER_ID")
            add("DUPLICATE_SUPPLIER_ID", BLOCKER, supplier_id, None, "supplier:%s" % supplier_id,
                "供应商编号重复，两条记录无法区分。")
        else:
            supplier_seen[supplier_id] = s_index

        quote_raw = entry.get("quote") if isinstance(entry.get("quote"), dict) else {}
        price_raw = quote_raw.get("price") if isinstance(quote_raw.get("price"), dict) else {}
        price, price_present, price_valid = as_amount(price_raw.get("value"))
        currency = clean_text(price_raw.get("currency")).upper() if has_text(price_raw.get("currency")) else None
        price_state = "UNKNOWN"
        if not price_present:
            flags.append("PRICE_MISSING")
            add("PRICE_MISSING", REVIEW, supplier_id, None, "supplier:%s" % supplier_id,
                "报价未提供，该供应商不进入任何币种的报价区间。")
        elif not price_valid:
            price_state = "INVALID"
            flags.append("PRICE_INVALID")
            excluded_quotes.append({"supplier_id": supplier_id, "reason": "PRICE_INVALID",
                                    "value_raw": clean_text(price_raw.get("value"))})
            add("INVALID_PRICE", REVIEW, supplier_id, None, "supplier:%s" % supplier_id,
                "报价不是有效数字，已排除在区间统计之外，请让供应商重新确认。")
        elif price < 0:
            price_state = "INVALID"
            flags.append("PRICE_NEGATIVE")
            excluded_quotes.append({"supplier_id": supplier_id, "reason": "PRICE_NEGATIVE",
                                    "value_raw": clean_text(price_raw.get("value"))})
            add("INVALID_PRICE", REVIEW, supplier_id, None, "supplier:%s" % supplier_id,
                "报价为负数，已排除在区间统计之外，请让供应商重新确认。")
        elif currency is None:
            price_state = "NO_CURRENCY"
            flags.append("PRICE_CURRENCY_UNKNOWN")
            excluded_quotes.append({"supplier_id": supplier_id, "reason": "PRICE_CURRENCY_UNKNOWN",
                                    "value_raw": clean_text(price_raw.get("value"))})
            add("PRICE_CURRENCY_UNKNOWN", REVIEW, supplier_id, None, "supplier:%s" % supplier_id,
                "报价没有币种，无法归入任何币种区间，也不做汇率猜测。")
        else:
            price_state = "KNOWN"

        moq = dec(quote_raw.get("moq")) if not isinstance(quote_raw.get("moq"), bool) else None
        lead_time = dec(quote_raw.get("lead_time_days")) if not isinstance(quote_raw.get("lead_time_days"), bool) else None

        suppliers.append({
            "supplier_id": supplier_id, "name": name, "price_state": price_state,
            "price": price, "currency": currency,
            "moq": moq, "lead_time_days": lead_time,
            "review_flags": sorted(set(flags)), "index": s_index,
        })

        for a_index, sample_raw in enumerate(entry.get("samples") if isinstance(entry.get("samples"), list) else []):
            if not isinstance(sample_raw, dict):
                continue
            sample_id = clean_text(sample_raw.get("sample_id")) if has_text(sample_raw.get("sample_id")) else None
            s_flags = []
            if sample_id is None:
                s_flags.append("MISSING_SAMPLE_ID")
            elif sample_id in sample_seen:
                s_flags.append("DUPLICATE_SAMPLE_ID")
                add("DUPLICATE_SAMPLE_ID", BLOCKER, supplier_id, sample_id, "sample:%s" % sample_id,
                    "样品编号重复，两条记录无法区分。")
            else:
                sample_seen[sample_id] = (s_index, a_index)

            attachments = []
            for ref_raw in (sample_raw.get("attachments") if isinstance(sample_raw.get("attachments"), list) else []):
                if not has_text(ref_raw):
                    continue
                text = clean_text(ref_raw)
                if UNSAFE_REF_RE.search(text) or len(text) > 200:
                    s_flags.append("INVALID_ATTACHMENT_REF")
                    attachment_index.append({
                        "supplier_id": supplier_id, "sample_id": sample_id,
                        "filename": None, "basename": safe_basename(text), "valid": False,
                        "reason": "INVALID_ATTACHMENT_REF",
                    })
                    add("INVALID_ATTACHMENT_REF", BLOCKER, supplier_id, sample_id,
                        "attachment:%s" % (sample_id or "?"),
                        "附件引用含路径分隔符、上级目录、URL scheme 或盘符，已拒绝且未解析该路径；"
                        "原引用未回显，只保留安全化后的文件名。")
                else:
                    attachments.append(text)
                    attachment_index.append({
                        "supplier_id": supplier_id, "sample_id": sample_id,
                        "filename": text, "basename": text, "valid": True, "reason": None,
                    })

            cells = []
            for o_index, obs_raw in enumerate(sample_raw.get("observations") if isinstance(sample_raw.get("observations"), list) else []):
                if not isinstance(obs_raw, dict):
                    continue
                cells.append({
                    "spec_id": clean_text(obs_raw.get("spec_id")) if has_text(obs_raw.get("spec_id")) else None,
                    "tested": obs_raw.get("tested"),
                    "value_raw": obs_raw.get("value"),
                    "unit_raw": obs_raw.get("unit"),
                    "method": clean_text(obs_raw.get("method")) if has_text(obs_raw.get("method")) else None,
                    "note": clean_text(obs_raw.get("note")) if has_text(obs_raw.get("note")) else "",
                    "index": o_index,
                })

            samples.append({
                "supplier_id": supplier_id, "sample_id": sample_id,
                "batch": clean_text(sample_raw.get("batch")) if has_text(sample_raw.get("batch")) else None,
                "attachments": attachments,
                "cells": cells, "review_flags": sorted(set(s_flags)), "index": a_index,
            })

    # ---- evaluate every (sample, spec) ----
    for sample in samples:
        by_spec = {}
        for cell in sample["cells"]:
            by_spec.setdefault(cell["spec_id"], []).append(cell)
        results = []
        for spec in specs:
            spec_id = spec["spec_id"]
            group = by_spec.get(spec_id)
            if group is None:
                results.append({
                    "spec_id": spec_id, "spec_name": spec["name"], "required": spec["required"],
                    "status": CELL_NOT_TESTED, "value": None, "unit": None,
                    "display_value": None, "flags": ["NO_OBSERVATION"], "method": None,
                })
                continue
            cell = group[0]
            entry = _evaluate_cell(spec, cell, sample, add)
            if len(group) > 1:
                entry["flags"] = sorted(set(entry["flags"] + ["DUPLICATE_OBSERVATION"]))
            results.append(entry)
        sample["results"] = results
        extra_specs = sorted({c["spec_id"] for c in sample["cells"] if c["spec_id"] not in spec_index})
        sample["undeclared_spec_ids"] = extra_specs
        for spec_id in extra_specs:
            add("UNKNOWN_SPEC_ID", BLOCKER, sample["supplier_id"], sample["sample_id"],
                "observation:%s" % (spec_id if spec_id else "?"),
                "观察记录引用了需求里不存在的规格编号，无法判断它对应哪一项要求。")

    # ---- sample verdict ----
    for sample in samples:
        required_fail = [r for r in sample["results"] if r["required"] and r["status"] == CELL_FAIL]
        required_unknown = [r for r in sample["results"] if r["required"]
                            and r["status"] in (CELL_NOT_TESTED, CELL_UNKNOWN)]
        optional_fail = [r for r in sample["results"] if not r["required"] and r["status"] == CELL_FAIL]
        if sample["review_flags"]:
            verdict = "INVALID"
        elif required_fail:
            verdict = SAMPLE_FAIL
        elif required_unknown:
            verdict = SAMPLE_INCONCLUSIVE
        else:
            verdict = SAMPLE_PASS
        sample["verdict"] = verdict
        sample["required_fail_specs"] = [r["spec_id"] for r in required_fail]
        sample["required_unverified_specs"] = [r["spec_id"] for r in required_unknown]
        sample["optional_fail_specs"] = [r["spec_id"] for r in optional_fail]

    # ---- spec summary ----
    spec_summary = []
    for spec in specs:
        counts = {CELL_PASS: 0, CELL_FAIL: 0, CELL_NOT_TESTED: 0, CELL_UNKNOWN: 0}
        comparable = 0
        for sample in samples:
            cell = next((r for r in sample["results"] if r["spec_id"] == spec["spec_id"]), None)
            if cell is None:
                continue
            counts[cell["status"]] += 1
            if cell["value"] is not None:
                comparable += 1
        spec_summary.append({
            "spec_id": spec["spec_id"], "name": spec["name"], "required": spec["required"],
            "unit": spec["unit"], "counts": counts, "comparable_samples": comparable,
            "review_flags": list(spec["review_flags"]),
        })

    # ---- evidence matrix ----
    evidence_matrix = []
    for spec in specs:
        row = {"spec_id": spec["spec_id"], "name": spec["name"], "required": spec["required"],
               "unit": spec["unit"], "cells": []}
        for sample in samples:
            cell = next((r for r in sample["results"] if r["spec_id"] == spec["spec_id"]), None)
            row["cells"].append({
                "supplier_id": sample["supplier_id"], "sample_id": sample["sample_id"],
                "status": cell["status"] if cell else CELL_NOT_TESTED,
                "display_value": cell["display_value"] if cell else None,
                "flags": list(cell["flags"]) if cell else ["NO_OBSERVATION"],
            })
        evidence_matrix.append(row)

    # ---- sample differences (pure arithmetic, no threshold) ----
    sample_differences = []
    for spec in specs:
        if spec["kind"] != "numeric":
            continue
        values = []
        for sample in samples:
            cell = next((r for r in sample["results"] if r["spec_id"] == spec["spec_id"]), None)
            if cell is not None and cell["value"] is not None:
                values.append((sample["sample_id"], cell["value"]))
        if len(values) < 2:
            continue
        numeric = [v for _sid, v in values]
        low, high = min(numeric), max(numeric)
        mean = sum(numeric) / Decimal(len(numeric))
        spread = high - low
        sample_differences.append({
            "spec_id": spec["spec_id"], "name": spec["name"], "unit": spec["unit"],
            "values": [{"sample_id": sid, "value": trim(v)} for sid, v in values],
            "comparable_samples": len(values),
            "min": trim(low), "max": trim(high), "spread": trim(spread),
            "spread_pct_of_mean": (trim(spread / mean * Decimal(100), 2)
                                   if mean != 0 else None),
            "note": "差异大小是否可接受由你判断，本工具不做合格判定。",
        })

    # ---- non comparable ----
    non_comparable = []
    for sample in samples:
        for cell in sample["results"]:
            if cell["status"] == CELL_UNKNOWN and any(
                    f in cell["flags"] for f in ("UNKNOWN_UNIT", "UNIT_INCOMPATIBLE", "UNIT_MISMATCH")):
                non_comparable.append({
                    "supplier_id": sample["supplier_id"], "sample_id": sample["sample_id"],
                    "spec_id": cell["spec_id"], "status": cell["status"],
                    "flags": list(cell["flags"]), "unit": cell["unit"],
                })

    # ---- retest items ----
    retest_items = []
    for sample in samples:
        for cell in sample["results"]:
            if cell["required"] and cell["status"] in (CELL_NOT_TESTED, CELL_UNKNOWN):
                retest_items.append({
                    "supplier_id": sample["supplier_id"], "sample_id": sample["sample_id"],
                    "spec_id": cell["spec_id"], "spec_name": cell["spec_name"],
                    "status": cell["status"], "flags": list(cell["flags"]),
                    "ask": _retest_ask(cell),
                })

    # ---- observation notes (自由文本，命中注入即隐藏) ----
    note_index = []
    for sample in samples:
        for cell in sample["cells"]:
            if cell["note"]:
                note_index.append({
                    "supplier_id": sample["supplier_id"], "sample_id": sample["sample_id"],
                    "spec_id": cell["spec_id"], "note": clean_text(cell["note"]),
                    "method": cell["method"],
                })

    # ---- follow-up questions ----
    follow_up = []
    for supplier in suppliers:
        topics = []
        if supplier["review_flags"]:
            for flag in supplier["review_flags"]:
                topics.append({"topic": flag, "detail": _supplier_ask(flag)})
        for sample in samples:
            if sample["supplier_id"] != supplier["supplier_id"]:
                continue
            if sample["review_flags"]:
                topics.append({"topic": "ATTACHMENT",
                               "detail": "以下附件引用含路径或链接，请提供裸文件名（本工具未解析这些路径）。"})
            missing = [r["spec_id"] for r in sample["results"]
                       if r["required"] and (r["status"] == CELL_NOT_TESTED or "NO_OBSERVATION" in r["flags"])]
            if missing:
                topics.append({"topic": "MISSING_SPECS",
                               "detail": "样品 %s 缺少以下必填规格的测试或观察记录：%s。"
                                         % (sample["sample_id"], "、".join(str(m) for m in missing))})
            unclear = sorted({r["spec_id"] for r in sample["results"]
                              if any(f in r["flags"] for f in ("UNKNOWN_UNIT", "UNIT_INCOMPATIBLE", "UNIT_MISMATCH"))})
            if unclear:
                topics.append({"topic": "UNIT_CLARIFY",
                               "detail": "样品 %s 的以下规格单位与需求不一致或无法识别，请让供应商提供带明确单位的原始数据：%s。"
                                         % (sample["sample_id"], "、".join(str(u) for u in unclear))})
            no_evidence = sorted({r["spec_id"] for r in sample["results"]
                                  if "EVIDENCE_MISSING" in r["flags"] or "VALUE_INVALID" in r["flags"]})
            if no_evidence:
                topics.append({"topic": "EVIDENCE",
                               "detail": "样品 %s 的以下规格声称已测但没有可用的数值或结论，请补充原始记录：%s。"
                                         % (sample["sample_id"], "、".join(str(u) for u in no_evidence))})
        follow_up.append({"supplier_id": supplier["supplier_id"], "name": supplier["name"],
                          "topics": topics})

    # ---- quotes by currency ----
    quote_summary = {}
    for supplier in suppliers:
        if supplier["price_state"] != "KNOWN" or not supplier["currency"]:
            continue
        bucket = quote_summary.setdefault(supplier["currency"], {"supplier_count": 0, "min": None, "max": None})
        bucket["supplier_count"] += 1
        bucket["min"] = supplier["price"] if bucket["min"] is None else min(bucket["min"], supplier["price"])
        bucket["max"] = supplier["price"] if bucket["max"] is None else max(bucket["max"], supplier["price"])
    quote_view = {}
    for currency in sorted(quote_summary):
        bucket = quote_summary[currency]
        quote_view[currency] = {"supplier_count": bucket["supplier_count"],
                                "min": amount(bucket["min"]), "max": amount(bucket["max"])}

    # ---- required spec findings ----
    for sample in samples:
        for cell in sample["results"]:
            if not cell["required"]:
                continue
            if cell["status"] == CELL_FAIL:
                add("REQUIRED_SPEC_FAIL", REVIEW, sample["supplier_id"], sample["sample_id"],
                    "spec:%s" % cell["spec_id"],
                    "必填规格「%s」未达标（实测 %s，%s）。"
                    % (cell["spec_name"], cell["display_value"] or "无可用值", _range_text(spec_index.get(cell["spec_id"]))))
            elif cell["status"] in (CELL_NOT_TESTED, CELL_UNKNOWN):
                add("REQUIRED_SPEC_UNVERIFIED", REVIEW, sample["supplier_id"], sample["sample_id"],
                    "spec:%s" % cell["spec_id"],
                    "必填规格「%s」证据不足（%s），不能判为通过。"
                    % (cell["spec_name"], "、".join(cell["flags"])))

    # ---- counts ----
    cell_counts = {CELL_PASS: 0, CELL_FAIL: 0, CELL_NOT_TESTED: 0, CELL_UNKNOWN: 0}
    for sample in samples:
        for cell in sample["results"]:
            cell_counts[cell["status"]] += 1
    sample_verdict_counts = {}
    for sample in samples:
        sample_verdict_counts[sample["verdict"]] = sample_verdict_counts.get(sample["verdict"], 0) + 1
    required_fail_total = sum(1 for s in samples for r in s["results"] if r["required"] and r["status"] == CELL_FAIL)
    optional_fail_total = sum(1 for s in samples for r in s["results"] if not r["required"] and r["status"] == CELL_FAIL)

    counts = {
        "specs": len(specs),
        "required_specs": sum(1 for s in specs if s["required"]),
        "suppliers": len(suppliers),
        "samples": len(samples),
        "observations": sum(len(s["cells"]) for s in samples),
        "cell_counts": cell_counts,
        "sample_verdict_counts": sample_verdict_counts,
        "required_fail_count": required_fail_total,
        "optional_fail_count": optional_fail_total,
        "retest_count": len(retest_items),
        "non_comparable_count": len(non_comparable),
    }

    # ---- status ----
    blocker_count = sum(1 for f in findings if f["severity"] == BLOCKER)
    review_count = sum(1 for f in findings if f["severity"] == REVIEW)
    if not as_of_ok or not specs or not suppliers:
        status = "INPUT_INCOMPLETE"
    elif blocker_count:
        status = "BLOCKED"
    elif required_fail_total or len(retest_items) or non_comparable or warnings:
        status = "GAPS_FOUND"
    else:
        status = "READY"

    # ---- decision pack ----
    decision_pack = []

    def decide(topic, action):
        decision_pack.append({"step": len(decision_pack) + 1, "topic": topic, "action": action})

    decide("SPEC_SIGNOFF", "与有资质的人员确认需求规格、区间与单位是否就是要评的那一份。")
    if blocker_count:
        decide("BLOCKERS", "先解决 %d 条阻塞项（附件引用、编号重复或引用了不存在的规格）后重跑。" % blocker_count)
    if required_fail_total:
        decide("REQUIRED_FAIL", "有 %d 处必填规格未达标，需人工判断是否仍纳入比较。" % required_fail_total)
    if len(retest_items):
        decide("RETEST", "有 %d 处必填规格证据不足，请按复测清单补齐或要求供应商重测。" % len(retest_items))
    if non_comparable:
        decide("NON_COMPARABLE", "有 %d 处单位不可比较，先统一单位再判断，不要在单位不一致时下结论。" % len(non_comparable))
    if sample_differences:
        decide("SAMPLE_SPREAD", "样品间存在差异（见「样品间差异」），是否可接受由你判断，本工具不给结论。")
    if excluded_quotes:
        decide("QUOTE_FIX", "有 %d 家报价无效或缺少币种，已排除在区间统计之外，请重新确认。" % len(excluded_quotes))
    decide("NO_CERTIFICATION", "本工具不给质量、安全、认证、合规、法律或最终采购结论；如需结论请走有资质的检测或评审流程。")
    decide("NO_ACTION", "本工具不联系供应商、不下单、不发送任何消息；追问清单由人工发出。")

    # ---- injection report ----
    injection_flagged = []
    for supplier in suppliers:
        if injection_hit(supplier["name"]):
            injection_flagged.append({"path": "suppliers[%d]/name" % supplier["index"],
                                      "marker": "PROMPT_INJECTION"})
    for sample in samples:
        for cell in sample["cells"]:
            if injection_hit(cell["note"]):
                injection_flagged.append({
                    "path": "suppliers[...]/samples[%d]/observations[%d]/note" % (sample["index"], cell["index"]),
                    "marker": "PROMPT_INJECTION"})
            if injection_hit(cell["method"]):
                injection_flagged.append({
                    "path": "suppliers[...]/samples[%d]/observations[%d]/method" % (sample["index"], cell["index"]),
                    "marker": "PROMPT_INJECTION"})
    for spec in specs:
        if injection_hit(spec["name"]) or injection_hit(spec["target"]):
            injection_flagged.append({"path": "requirement/specs[%d]" % spec["index"],
                                      "marker": "PROMPT_INJECTION"})

    ordered_findings = sorted(
        findings,
        key=lambda f: (0 if f["severity"] == BLOCKER else 1,
                       str(f["supplier_id"] or ""), str(f["sample_id"] or ""),
                       f["code"], str(f["subject"] or "")),
    )
    finding_counts = {code: 0 for code in CODE_LIST}
    for finding in findings:
        finding_counts[finding["code"]] += 1
    finding_counts = {code: finding_counts[code] for code in CODE_LIST if finding_counts[code]}

    markdown = render_markdown(
        status=status, as_of=as_of_raw, requirement_id=requirement_id, requirement_name=requirement_name,
        specs=specs, suppliers=suppliers, samples=samples, evidence_matrix=evidence_matrix,
        spec_summary=spec_summary, sample_differences=sample_differences,
        non_comparable=non_comparable, retest_items=retest_items, follow_up=follow_up,
        excluded_quotes=excluded_quotes, quote_view=quote_view, attachment_index=attachment_index,
        decision_pack=decision_pack, findings=ordered_findings, finding_counts=finding_counts,
        counts=counts, warnings=warnings, injection_flagged=injection_flagged,
        note_index=note_index,
    )

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "requirement": {"requirement_id": requirement_id, "name": requirement_name,
                        "spec_count": len(specs),
                        "required_spec_count": counts["required_specs"]},
        "counts": counts,
        "specs": [{
            "spec_id": s["spec_id"], "name": s["name"], "kind": s["kind"],
            "required": s["required"], "target": s["target"],
            "min": trim(s["min"]), "max": trim(s["max"]), "unit": s["unit"],
            "review_flags": s["review_flags"],
        } for s in specs],
        "suppliers": [{
            "supplier_id": s["supplier_id"], "name": s["name"],
            "price_state": s["price_state"],
            "price": amount(s["price"]) if s["price_state"] == "KNOWN" else None,
            "currency": s["currency"],
            "moq": trim(s["moq"], 3), "lead_time_days": trim(s["lead_time_days"], 3),
            "review_flags": s["review_flags"],
        } for s in suppliers],
        "samples": [{
            "supplier_id": s["supplier_id"], "sample_id": s["sample_id"], "batch": s["batch"],
            "attachments": s["attachments"], "verdict": s["verdict"],
            "required_fail_specs": s["required_fail_specs"],
            "required_unverified_specs": s["required_unverified_specs"],
            "optional_fail_specs": s["optional_fail_specs"],
            "undeclared_spec_ids": s.get("undeclared_spec_ids", []),
            "review_flags": s["review_flags"],
        } for s in samples],
        "evidence_matrix": evidence_matrix,
        "spec_summary": spec_summary,
        "sample_differences": sample_differences,
        "non_comparable_items": non_comparable,
        "retest_items": retest_items,
        "follow_up_questions": follow_up,
        "excluded_quotes": excluded_quotes,
        "quote_summary_by_currency": quote_view,
        "attachment_index": attachment_index,
        "observation_notes": note_index,
        "decision_pack": decision_pack,
        "findings": ordered_findings,
        "finding_counts": finding_counts,
        "blocker_count": blocker_count,
        "review_count": review_count,
        "not_concluded": list(NOT_CONCLUDED),
        "markdown_summary": markdown,
        "injection_flagged": injection_flagged,
        "input_warnings": sorted(set(warnings)),
        "disclaimer": DISCLAIMER,
    }


def _range_text(spec):
    if spec is None:
        return "未提供"
    if spec["kind"] == "text":
        return "应为「%s」" % spec["target"] if spec["target"] else "未提供目标值"
    unit = spec["unit"] or ""
    if spec["min"] is not None and spec["max"] is not None:
        return "%s–%s %s" % (trim(spec["min"]), trim(spec["max"]), unit)
    if spec["min"] is not None:
        return "≥ %s %s" % (trim(spec["min"]), unit)
    if spec["max"] is not None:
        return "≤ %s %s" % (trim(spec["max"]), unit)
    return "未提供区间"


def _evaluate_cell(spec, cell, sample, add):
    """Returns the evidence-matrix cell. Never PASS unless the evidence supports it."""
    spec_id = spec["spec_id"]
    base = {"spec_id": spec_id, "spec_name": spec["name"], "required": spec["required"],
            "status": CELL_NOT_TESTED, "value": None, "unit": None,
            "display_value": None, "flags": [], "method": cell["method"]}
    if cell["tested"] is not True:
        base["status"] = CELL_NOT_TESTED
        base["flags"] = ["TESTED_FLAG_MISSING"] if cell["tested"] is None else ["NOT_TESTED"]
        return base

    raw_value = cell["value_raw"]
    if raw_value is None or (isinstance(raw_value, str) and raw_value.strip() == ""):
        base["status"] = CELL_UNKNOWN
        base["flags"] = ["EVIDENCE_MISSING"]
        return base

    if spec["kind"] == "text":
        base["display_value"] = clean_text(raw_value) if isinstance(raw_value, str) else str(raw_value)
        if spec["target"] is None:
            base["status"] = CELL_UNKNOWN
            base["flags"] = ["SPEC_TARGET_MISSING"]
            return base
        if text_key(raw_value) == text_key(spec["target"]):
            base["status"] = CELL_PASS
        else:
            base["status"] = CELL_FAIL
        return base

    if spec["kind"] != "numeric":
        base["status"] = CELL_UNKNOWN
        base["flags"] = ["SPEC_KIND_UNKNOWN"]
        return base

    value = dec(raw_value)
    if value is None:
        base["status"] = CELL_UNKNOWN
        base["flags"] = ["VALUE_INVALID"]
        return base

    obs_unit_norm = norm_unit(cell["unit_raw"])
    base["unit"] = clean_text(cell["unit_raw"]) if has_text(cell["unit_raw"]) else None

    if spec["unit_norm"] is not None:
        if obs_unit_norm is None:
            base["status"] = CELL_UNKNOWN
            base["flags"] = ["UNIT_MISMATCH"]
            base["display_value"] = trim(value)
            return base
        if unit_group(obs_unit_norm) is None:
            base["status"] = CELL_UNKNOWN
            base["flags"] = ["UNKNOWN_UNIT"]
            base["display_value"] = trim(value)
            add("UNKNOWN_UNIT", REVIEW, sample["supplier_id"], sample["sample_id"],
                "observation:%s" % (spec_id if spec_id else "?"),
                "观察记录使用了白名单之外的单位「%s」，不猜测换算关系，也不参与判定。"
                % clean_text(cell["unit_raw"]))
            return base
        if spec["unit_group"] is not None and unit_group(obs_unit_norm) != spec["unit_group"]:
            base["status"] = CELL_UNKNOWN
            base["flags"] = ["UNIT_INCOMPATIBLE"]
            base["display_value"] = "%s %s" % (trim(value), clean_text(cell["unit_raw"]))
            add("UNIT_INCOMPATIBLE", REVIEW, sample["supplier_id"], sample["sample_id"],
                "observation:%s" % (spec_id if spec_id else "?"),
                "观察单位「%s」与需求单位「%s」不属于同一量纲，不可比较，本工具不做换算。"
                % (clean_text(cell["unit_raw"]), clean_text(spec["unit"])))
            return base
        if spec["unit_group"] is None:
            base["status"] = CELL_UNKNOWN
            base["flags"] = ["SPEC_UNIT_UNKNOWN"]
            base["display_value"] = "%s %s" % (trim(value), clean_text(cell["unit_raw"]))
            return base
        factor_obs = Decimal(dict(UNIT_GROUPS)[spec["unit_group"]][obs_unit_norm])
        factor_spec = Decimal(dict(UNIT_GROUPS)[spec["unit_group"]][spec["unit_norm"]])
        converted = value * factor_obs / factor_spec
    else:
        if obs_unit_norm is not None:
            base["status"] = CELL_UNKNOWN
            base["flags"] = ["UNIT_MISMATCH"]
            base["display_value"] = "%s %s" % (trim(value), clean_text(cell["unit_raw"]))
            return base
        converted = value

    base["value"] = converted
    base["display_value"] = trim(converted)

    if spec["min"] is None and spec["max"] is None:
        base["status"] = CELL_UNKNOWN
        base["flags"] = ["SPEC_RANGE_MISSING"]
        return base
    ok = True
    if spec["min"] is not None and converted < spec["min"]:
        ok = False
    if spec["max"] is not None and converted > spec["max"]:
        ok = False
    base["status"] = CELL_PASS if ok else CELL_FAIL
    return base


def _retest_ask(cell):
    if cell["status"] == CELL_NOT_TESTED:
        return "请补做「%s」的测试或观察并给出数值/结论。" % cell["spec_name"]
    if "UNIT_MISMATCH" in cell["flags"]:
        return "「%s」缺少单位，请提供带明确单位的原始数据。" % cell["spec_name"]
    if "UNKNOWN_UNIT" in cell["flags"]:
        return "「%s」的单位不在可换算白名单内，请提供需求单位的原始数值。" % cell["spec_name"]
    if "UNIT_INCOMPATIBLE" in cell["flags"]:
        return "「%s」的单位与需求不同量纲，请按需求单位重新提供。" % cell["spec_name"]
    if "EVIDENCE_MISSING" in cell["flags"]:
        return "「%s」标记为已测但没有可用结论，请补充原始记录。" % cell["spec_name"]
    if "VALUE_INVALID" in cell["flags"]:
        return "「%s」的数值无法解析，请提供明确的数值。" % cell["spec_name"]
    return "请补齐「%s」的证据。" % cell["spec_name"]


def _supplier_ask(flag):
    mapping = {
        "PRICE_INVALID": "请供应商重新确认报价（当前值不是有效数字或为负数）。",
        "PRICE_NEGATIVE": "请供应商重新确认报价（当前值不是有效数字或为负数）。",
        "PRICE_MISSING": "请供应商提供正式报价。",
        "PRICE_CURRENCY_UNKNOWN": "请供应商确认报价币种，本工具不做汇率猜测。",
        "MISSING_SUPPLIER_ID": "请补充供应商编号，否则多家报价无法区分。",
        "DUPLICATE_SUPPLIER_ID": "供应商编号重复，请区分两条记录。",
    }
    return mapping.get(flag, "请补充「%s」相关信息。" % flag)


SPEC_CELL_LABEL = {
    CELL_PASS: "通过",
    CELL_FAIL: "未达标",
    CELL_NOT_TESTED: "未测试",
    CELL_UNKNOWN: "证据不足",
}

SAMPLE_VERDICT_LABEL = {
    SAMPLE_PASS: "必填规格全部通过",
    SAMPLE_INCONCLUSIVE: "证据不足，无法判断",
    SAMPLE_FAIL: "存在必填规格未达标",
    "INVALID": "记录本身无效",
}


def render_markdown(status, as_of, requirement_id, requirement_name, specs, suppliers, samples,
                    evidence_matrix, spec_summary, sample_differences, non_comparable, retest_items,
                    follow_up, excluded_quotes, quote_view, attachment_index, decision_pack,
                    findings, finding_counts, counts, warnings, injection_flagged, note_index):
    lines = []
    lines.append("# 供应商样品评估准备单 — %s" % esc(requirement_name or requirement_id or "未命名需求"))
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 需求编号：%s ／ 规格 %d 项（其中必填 %d 项）" % (
        esc(requirement_id) if requirement_id else "未提供",
        counts["specs"], counts["required_specs"]))
    lines.append("- 供应商 %d 家 ／ 样品 %d 个 ／ 观察记录 %d 条" % (
        counts["suppliers"], counts["samples"], counts["observations"]))
    lines.append("- 单元格：通过 %d ／ 未达标 %d ／ 未测试 %d ／ 证据不足 %d" % (
        counts["cell_counts"][CELL_PASS], counts["cell_counts"][CELL_FAIL],
        counts["cell_counts"][CELL_NOT_TESTED], counts["cell_counts"][CELL_UNKNOWN]))
    lines.append("- 生成基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 阻塞项：**%d** ／ 待确认项：**%d**" % (
        sum(1 for f in findings if f["severity"] == BLOCKER),
        sum(1 for f in findings if f["severity"] == REVIEW)))
    lines.append("")

    lines.append("## 逐规格证据矩阵")
    lines.append("")
    if specs and samples:
        header = "| 规格 | 必填 | 需求 | " + " | ".join(
            esc(s["sample_id"]) if s["sample_id"] else "未提供" for s in samples) + " |"
        lines.append(header)
        lines.append("|---|---|---|" + "---|" * len(samples))
        for row in evidence_matrix:
            cells = []
            for cell in row["cells"]:
                label = SPEC_CELL_LABEL[cell["status"]]
                cells.append(label if cell["display_value"] is None
                             else "%s（%s）" % (label, esc(cell["display_value"])))
            spec = next((s for s in specs if s["spec_id"] == row["spec_id"]), None)
            lines.append("| %s | %s | %s | %s |" % (
                hidden(row["name"]) if row["name"] else esc(row["spec_id"]),
                "是" if row["required"] else "否",
                esc(_range_text(spec)), " | ".join(cells)))
        lines.append("")
        lines.append("> 「未测试」「证据不足」「单位不可比较」一律**不判通过**。")
    else:
        lines.append("- 无可比较的规格或样品")
    lines.append("")

    lines.append("## 规格汇总")
    lines.append("")
    if spec_summary:
        lines.append("| 规格 | 必填 | 单位 | 通过 | 未达标 | 未测试 | 证据不足 |")
        lines.append("|---|---|---|---:|---:|---:|---:|")
        for item in spec_summary:
            lines.append("| %s | %s | %s | %d | %d | %d | %d |" % (
                hidden(item["name"]) if item["name"] else esc(item["spec_id"]),
                "是" if item["required"] else "否",
                esc(item["unit"]) if item["unit"] else "—",
                item["counts"][CELL_PASS], item["counts"][CELL_FAIL],
                item["counts"][CELL_NOT_TESTED], item["counts"][CELL_UNKNOWN]))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 样品结论")
    lines.append("")
    if samples:
        lines.append("| 样品 | 供应商 | 批次 | 结论 | 必填未达标 | 必填待补证 |")
        lines.append("|---|---|---|---|---|---|")
        for sample in samples:
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                esc(sample["sample_id"]) if sample["sample_id"] else "未提供",
                esc(sample["supplier_id"]) if sample["supplier_id"] else "未提供",
                esc(sample["batch"]) if sample["batch"] else "未提供",
                SAMPLE_VERDICT_LABEL.get(sample["verdict"], sample["verdict"]),
                esc("、".join(str(x) for x in sample["required_fail_specs"])) or "—",
                esc("、".join(str(x) for x in sample["required_unverified_specs"])) or "—"))
    else:
        lines.append("- 无样品")
    lines.append("")

    if sample_differences:
        lines.append("## 样品间差异（只做算术，不给合格判断）")
        lines.append("")
        lines.append("| 规格 | 单位 | 可比较样品 | 最小 | 最大 | 极差 | 极差/均值 |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for item in sample_differences:
            lines.append("| %s | %s | %d | %s | %s | %s | %s%% |" % (
                hidden(item["name"]) if item["name"] else esc(item["spec_id"]),
                esc(item["unit"]) if item["unit"] else "—",
                item["comparable_samples"], item["min"], item["max"], item["spread"],
                item["spread_pct_of_mean"] if item["spread_pct_of_mean"] is not None else "—"))
        lines.append("")
        lines.append("> 差异大小是否可接受由你判断，本工具不做合格判定。")
        lines.append("")

    lines.append("## 复测与补证清单（仅必填规格）")
    lines.append("")
    if retest_items:
        for item in retest_items:
            lines.append("- `%s`（%s 必填）：%s" % (
                esc(item["spec_id"]) if item["spec_id"] else "未提供",
                SAMPLE_VERDICT_LABEL.get(item["status"], SPEC_CELL_LABEL.get(item["status"], item["status"])),
                esc(item["ask"])))
    else:
        lines.append("- 无")
    lines.append("")

    if non_comparable:
        lines.append("## 不可比较项")
        lines.append("")
        for item in non_comparable:
            lines.append("- `%s` ／ 样品 `%s` ／ 规格 `%s`：%s（单位 %s）" % (
                esc(item["supplier_id"]) if item["supplier_id"] else "未提供",
                esc(item["sample_id"]) if item["sample_id"] else "未提供",
                esc(item["spec_id"]) if item["spec_id"] else "未提供",
                esc("、".join(item["flags"])),
                esc(item["unit"]) if item["unit"] else "未提供"))
        lines.append("")

    lines.append("## 供应商追问清单")
    lines.append("")
    if any(item["topics"] for item in follow_up):
        for item in follow_up:
            if not item["topics"]:
                continue
            lines.append("### %s（%s）" % (
                esc(item["name"]) if item["name"] else esc(item["supplier_id"]),
                esc(item["supplier_id"]) if item["supplier_id"] else "未提供"))
            lines.append("")
            for topic in item["topics"]:
                lines.append("- `%s` %s" % (esc(topic["topic"]), esc(topic["detail"])))
            lines.append("")
    else:
        lines.append("- 无")
        lines.append("")

    lines.append("## 报价（按币种分组，不跨币种合计、不换汇）")
    lines.append("")
    if quote_view:
        for currency in sorted(quote_view):
            bucket = quote_view[currency]
            lines.append("- %s：%d 家，区间 %s – %s" % (
                esc(currency), bucket["supplier_count"], bucket["min"], bucket["max"]))
    else:
        lines.append("- 没有可用的报价")
    lines.append("")
    if excluded_quotes:
        lines.append("**未进入统计的报价**")
        lines.append("")
        for item in excluded_quotes:
            lines.append("- `%s`：%s（原值 %s）" % (
                esc(item["supplier_id"]) if item["supplier_id"] else "未提供",
                esc(item["reason"]), esc(item["value_raw"]) if item["value_raw"] else "未提供"))
        lines.append("")

    lines.append("## 附件索引（只记文件名，未打开、未读取、未解析）")
    lines.append("")
    if attachment_index:
        for item in attachment_index:
            if item["valid"]:
                lines.append("- `%s` ／ `%s` ／ %s" % (
                    esc(item["supplier_id"]) if item["supplier_id"] else "未提供",
                    esc(item["sample_id"]) if item["sample_id"] else "未提供",
                    esc(item["filename"])))
            else:
                lines.append("- `%s` ／ `%s` ／ **已拒绝（未解析）**（%s）%s" % (
                    esc(item["supplier_id"]) if item["supplier_id"] else "未提供",
                    esc(item["sample_id"]) if item["sample_id"] else "未提供",
                    esc(item["reason"]),
                    esc("，安全化文件名 %s" % item["basename"]) if item.get("basename") else ""))
    else:
        lines.append("- 无")
    lines.append("")

    if note_index:
        lines.append("## 观察记录备注")
        lines.append("")
        lines.append("| 供应商 | 样品 | 规格 | 备注 |")
        lines.append("|---|---|---|---|")
        for item in note_index:
            lines.append("| %s | %s | %s | %s |" % (
                esc(item["supplier_id"]) if item["supplier_id"] else "未提供",
                esc(item["sample_id"]) if item["sample_id"] else "未提供",
                esc(item["spec_id"]) if item["spec_id"] else "未提供",
                hidden(item["note"])))
        lines.append("")
        lines.append("> 备注按不可信数据处理：命中提示注入的内容会被替换为固定占位，不会被当作指令执行。")
        lines.append("")

    lines.append("## 发现项")
    lines.append("")
    if findings:
        lines.append("| 严重度 | 代码 | 供应商 | 样品 | 对象 | 说明 |")
        lines.append("|---|---|---|---|---|---|")
        for finding in findings:
            lines.append("| %s | %s | %s | %s | %s | %s |" % (
                "阻塞" if finding["severity"] == BLOCKER else "待确认",
                esc(finding["code"]),
                esc(finding["supplier_id"]) if finding["supplier_id"] else "—",
                esc(finding["sample_id"]) if finding["sample_id"] else "—",
                esc(finding["subject"]) if finding["subject"] else "—",
                hidden(finding["detail"])))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 人工决策包")
    lines.append("")
    for entry in decision_pack:
        lines.append("%d. **%s** %s" % (entry["step"], esc(entry["topic"]), esc(entry["action"])))
    lines.append("")

    lines.append("## 本工具不结论的事项")
    lines.append("")
    for item in NOT_CONCLUDED:
        lines.append("- %s" % esc(item))
    lines.append("")

    if injection_flagged:
        lines.append("## 已隐藏的可疑文本")
        lines.append("")
        for hit in injection_flagged:
            lines.append("- `%s`（%s）：内容按不可信数据隐藏，未执行其中任何指令。" % (
                esc(hit["path"]), esc(hit["marker"])))
        lines.append("")

    if warnings:
        lines.append("## 输入提示")
        lines.append("")
        for warning in warnings:
            lines.append("- %s" % esc(warning))
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("> %s" % esc(DISCLAIMER))
    lines.append("")
    return "\n".join(lines)


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: run.py <input.json>\n")
        return 2
    try:
        with open(argv[1], "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as error:
        sys.stderr.write("cannot read input: %s\n" % error)
        return 2
    sys.stdout.write(json.dumps(analyse(data), ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
