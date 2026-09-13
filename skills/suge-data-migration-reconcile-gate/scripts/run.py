#!/usr/bin/env python3
"""Offline data-migration row-count / schema / summary consistency gate.

Compares user-supplied, already-collected source and target table statistics and
decides whether a migration batch may be signed off.  It never connects to a
database, never reads business rows, never infers a mapping that was not given
and never repairs or re-runs a migration.  Hash comparison is refused outright
when the algorithm, salt or normalisation rules do not match.
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
                     "private_key", "private_key_pem", "api_token", "access_token",
                     "client_secret", "credential_value", "dsn", "connection_string",
                     "jdbc_url", "database_url"}

CHECK_TYPES = ("COLUMN", "TYPE", "NULLABLE", "ROW_COUNT", "NULL_COUNT",
               "DISTINCT_COUNT", "MIN_MAX", "HASH")
SEVERITY = {
    "MISSING_TABLE": "HIGH", "COLUMN_REMOVED": "HIGH", "TYPE_INCOMPATIBLE": "HIGH",
    "TYPE_NARROWED": "HIGH", "NULLABLE_NARROWED": "HIGH",
    "WATERMARK_OUTSIDE_WINDOW": "HIGH", "SNAPSHOT_BEFORE_WINDOW_END": "HIGH",
    "WINDOW_MISSING": "HIGH", "SNAPSHOT_TIME_MISSING": "MEDIUM",
    "ROW_COUNT_DIFF": "MEDIUM", "NULL_COUNT_DIFF": "LOW", "DISTINCT_COUNT_DIFF": "LOW",
    "MIN_MAX_DIFF": "LOW", "HASH_BUCKET_DIFF": "LOW", "HASH_BUCKET_MISSING": "LOW",
    "HASH_NOT_COMPARABLE": "INFO", "TYPE_WIDENED": "INFO",
}
DRIFT_CHECKS = ("COLUMN", "TYPE", "NULLABLE")
SNAPSHOT_CHECKS = ("SNAPSHOT",)

STATUS_ORDER = ("INVALID", "SNAPSHOT_NOT_COMPARABLE", "SCHEMA_DRIFT", "COUNT_MISMATCH",
                "CONTENT_MISMATCH", "PARTIAL", "MATCH")

TYPE_RANKS = {
    "integer": {"tinyint": 1, "smallint": 2, "int": 3, "integer": 3, "serial": 3,
                "bigint": 4, "bigserial": 5},
    "decimal": {"real": 1, "float": 2, "double": 3, "decimal": 4, "numeric": 4, "money": 4},
    "text": {"char": 1, "character": 1, "varchar": 2, "nvarchar": 2, "string": 3,
             "text": 3, "clob": 3},
    "temporal": {"date": 1, "time": 1, "datetime": 2, "timestamp": 2, "timestamptz": 3},
    "boolean": {"boolean": 1, "bool": 1},
    "binary": {"binary": 1, "varbinary": 2, "blob": 2, "bytea": 2},
    "json": {"json": 1, "jsonb": 2},
    "uuid": {"uuid": 1},
}
GROUP_OF = {}
for _group, _members in TYPE_RANKS.items():
    for _base in _members:
        GROUP_OF[_base] = _group


def privacy_gate(value):
    """Reject input that still contains a real-looking credential. Never echoes it."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in SECRET_VALUE_KEYS:
                raise ValueError(
                    "PRIVACY_GATE: 输入包含疑似未脱敏的连接串/凭据字段，已拒绝处理。"
                    "请只提供脱敏后的表统计，不要提供连接串、账号或口令原文。")
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


def count_of(value, label):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a non-negative integer")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or result != result.to_integral_value() \
            or not Decimal("0") <= result <= Decimal("1000000000000000000"):
        raise ValueError(label + " must be a non-negative integer")
    return int(result)


def quant(value, places=2):
    result = Decimal(value).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
    if result == 0:
        result = abs(result)
    return str(result)


def type_shape(type_text):
    raw = type_text.strip().lower()
    match = re.match(r"^([a-z0-9_ ]+?)\s*(?:\(([^)]*)\))?$", raw)
    if not match:
        raise ValueError("unsupported column type: " + type_text)
    base = match.group(1).strip().replace(" ", "")
    params = []
    if match.group(2):
        for part in match.group(2).split(","):
            piece = part.strip()
            if not re.fullmatch(r"\d+", piece):
                raise ValueError("unsupported column type parameters: " + type_text)
            params.append(int(piece))
    return base, params


def compare_types(source_type, target_type):
    """Return (code, detail) where code is OK / TYPE_WIDENED / TYPE_NARROWED / TYPE_INCOMPATIBLE."""
    src_base, src_params = type_shape(source_type)
    tgt_base, tgt_params = type_shape(target_type)
    src_group = GROUP_OF.get(src_base)
    tgt_group = GROUP_OF.get(tgt_base)
    if src_group is None or tgt_group is None:
        raise ValueError("unsupported column type: " + source_type + " / " + target_type)
    if src_base == tgt_base and src_params == tgt_params:
        return "OK", "类型一致"
    if src_group != tgt_group:
        return "TYPE_INCOMPATIBLE", "类型分组不同：%s（%s）→ %s（%s）" % (
            source_type, src_group, target_type, tgt_group)
    if src_group == "text":
        src_size = src_params[0] if src_params else None
        tgt_size = tgt_params[0] if tgt_params else None
        if tgt_size is None:
            return "TYPE_WIDENED", "文本长度放宽：%s → %s" % (source_type, target_type)
        if src_size is None:
            return "TYPE_NARROWED", "目标列长度受限而源列未知：%s → %s" % (source_type, target_type)
        if src_size <= tgt_size:
            return "TYPE_WIDENED", "文本长度放宽：%s → %s" % (source_type, target_type)
        return "TYPE_NARROWED", "文本长度收窄：%s → %s" % (source_type, target_type)
    if src_group == "decimal" and len(src_params) >= 1 and len(tgt_params) >= 1:
        src_p = src_params[0]
        tgt_p = tgt_params[0]
        src_s = src_params[1] if len(src_params) > 1 else 0
        tgt_s = tgt_params[1] if len(tgt_params) > 1 else 0
        if src_p <= tgt_p and src_s <= tgt_s:
            return "TYPE_WIDENED", "精度放宽：%s → %s" % (source_type, target_type)
        return "TYPE_NARROWED", "精度收窄：%s → %s" % (source_type, target_type)
    src_rank = TYPE_RANKS[src_group][src_base]
    tgt_rank = TYPE_RANKS[tgt_group][tgt_base]
    if src_rank <= tgt_rank:
        return "TYPE_WIDENED", "同组类型放宽：%s → %s" % (source_type, target_type)
    return "TYPE_NARROWED", "同组类型收窄：%s → %s" % (source_type, target_type)


def as_number(value):
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def serializable(record):
    """Copy an exclusion record with datetimes rendered as ISO strings."""
    copy = dict(record)
    if isinstance(copy.get("expires_at"), datetime):
        copy["expires_at"] = copy["expires_at"].isoformat()
    return copy


def parse_table(item, label):
    if not isinstance(item, dict):
        raise ValueError("each table must be an object")
    table_id = clean_text(item.get("table_id"), label + ".table_id", 120)
    captured_at = parse_dt(item.get("captured_at"), label + ".captured_at", required=False)
    if captured_at is not None and captured_at.tzinfo is None:
        raise ValueError(label + ".captured_at must be timezone-aware")
    watermark = parse_dt(item.get("watermark"), label + ".watermark", required=False)
    if watermark is not None and watermark.tzinfo is None:
        raise ValueError(label + ".watermark must be timezone-aware")
    schema = []
    seen_columns = set()
    for index, column in enumerate(item.get("schema") or []):
        column_label = "%s.schema[%d]" % (label, index)
        if not isinstance(column, dict):
            raise ValueError("each schema column must be an object")
        name = clean_text(column.get("column"), column_label + ".column", 120)
        if name in seen_columns:
            raise ValueError("duplicate column in %s: %s" % (label, name))
        seen_columns.add(name)
        schema.append({
            "column": name,
            "type": clean_text(column.get("type"), column_label + ".type", 60),
            "nullable": bool(column.get("nullable", True)),
            "key": bool(column.get("key", False)),
        })
    counters = {}
    for field in ("null_counts", "distinct_counts"):
        raw = item.get(field)
        if raw is None:
            counters[field] = None
            continue
        if not isinstance(raw, dict):
            raise ValueError(label + "." + field + " must be an object")
        counters[field] = {clean_text(key, label + "." + field + " key", 120):
                           count_of(value, "%s.%s[%s]" % (label, field, key))
                           for key, value in raw.items()}
    min_max = item.get("min_max")
    if min_max is None:
        min_max = {}
    if not isinstance(min_max, dict):
        raise ValueError(label + ".min_max must be an object")
    hash_info = item.get("hash")
    if hash_info is None:
        hash_info = None
    elif not isinstance(hash_info, dict):
        raise ValueError(label + ".hash must be an object")
    else:
        buckets = {}
        for index, bucket in enumerate(hash_info.get("buckets") or []):
            bucket_label = "%s.hash.buckets[%d]" % (label, index)
            if not isinstance(bucket, dict):
                raise ValueError("each hash bucket must be an object")
            name = clean_text(bucket.get("bucket"), bucket_label + ".bucket", 120)
            if name in buckets:
                raise ValueError("duplicate hash bucket in %s: %s" % (label, name))
            buckets[name] = count_of(bucket.get("count"), bucket_label + ".count")
        hash_info = {
            "algorithm": optional_text(hash_info.get("algorithm"), label + ".hash.algorithm", 60),
            "salt_id": optional_text(hash_info.get("salt_id"), label + ".hash.salt_id", 120),
            "normalization": optional_text(hash_info.get("normalization"),
                                           label + ".hash.normalization", 120),
            "buckets": buckets,
        }
    tolerance = item.get("tolerance")
    if tolerance is not None and not isinstance(tolerance, dict):
        raise ValueError(label + ".tolerance must be an object")
    return {
        "table_id": table_id,
        "row_count": count_of(item.get("row_count"), label + ".row_count"),
        "captured_at": captured_at,
        "watermark": watermark,
        "schema": schema,
        "null_counts": counters["null_counts"],
        "distinct_counts": counters["distinct_counts"],
        "min_max": min_max,
        "hash": hash_info,
        "tolerance": tolerance,
    }


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    privacy_gate(data)
    as_of = parse_as_of(data.get("as_of"))
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    source_tables = []
    target_tables = []
    for key, store in (("source_tables", source_tables), ("target_tables", target_tables)):
        raw = data.get(key)
        if not isinstance(raw, list) or len(raw) > 5000:
            raise ValueError(key + " must be a list of up to 5000 items")
        seen = set()
        for index, item in enumerate(raw):
            table = parse_table(item, "%s[%d]" % (key, index))
            if table["table_id"] in seen:
                raise ValueError("duplicate table_id in %s: %s" % (key, table["table_id"]))
            seen.add(table["table_id"])
            store.append(table)
    if not source_tables and not target_tables:
        result = {
            "as_of": as_of.isoformat(), "status": "INVALID", "table_count": 0,
            "status_counts": {}, "tables": [], "coverage": {}, "exclusions": {},
            "unmatched_source_tables": [], "unmatched_target_tables": [],
            "evidence_gaps": [], "snapshot_notes": [],
            "note": "没有任何表统计可比较，判定为 INVALID。本技能不连接数据库、不读取业务原始行、"
                    "不推断未提供的映射、不自动修复或重跑迁移。",
        }
        result["markdown_summary"] = build_markdown(result)
        return result

    source_map = {table["table_id"]: table for table in source_tables}
    target_map = {table["table_id"]: table for table in target_tables}

    mappings = data.get("table_mappings")
    if not isinstance(mappings, list) or not mappings:
        raise ValueError("table_mappings must be a non-empty list")
    pairs = []
    seen_sources = set()
    seen_targets = set()
    for index, item in enumerate(mappings):
        label = "table_mappings[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each mapping must be an object")
        source_id = clean_text(item.get("source_table_id"), label + ".source_table_id", 120)
        target_id = clean_text(item.get("target_table_id"), label + ".target_table_id", 120)
        if source_id in seen_sources:
            raise ValueError("duplicate source_table_id in mappings: " + source_id)
        if target_id in seen_targets:
            raise ValueError("duplicate target_table_id in mappings: " + target_id)
        seen_sources.add(source_id)
        seen_targets.add(target_id)
        pairs.append({"source_table_id": source_id, "target_table_id": target_id})

    window = data.get("incremental_window")
    if window is None:
        window = None
    elif not isinstance(window, dict):
        raise ValueError("incremental_window must be an object")
    else:
        window = {
            "from": parse_dt(window.get("from"), "incremental_window.from"),
            "to": parse_dt(window.get("to"), "incremental_window.to"),
        }
        if window["from"] > window["to"]:
            raise ValueError("incremental_window.from must not be after to")

    global_tolerance = data.get("tolerance") or {}
    if not isinstance(global_tolerance, dict):
        raise ValueError("tolerance must be an object")
    tolerance_abs = nonneg(global_tolerance.get("row_count_abs", 0), "tolerance.row_count_abs",
                           Decimal("1000000000"))
    tolerance_rel = nonneg(global_tolerance.get("row_count_rel_pct", 0),
                           "tolerance.row_count_rel_pct", Decimal("100"))

    raw_exclusions = data.get("exclusions") or []
    if not isinstance(raw_exclusions, list):
        raise ValueError("exclusions must be a list")
    applied, expired, invalid, orphan_exclusions = [], [], [], []
    valid_exclusions = []
    seen_exclusions = set()
    for index, item in enumerate(raw_exclusions):
        label = "exclusions[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each exclusion must be an object")
        exclusion_id = clean_text(item.get("exclusion_id"), label + ".exclusion_id", 120)
        if exclusion_id in seen_exclusions:
            raise ValueError("duplicate exclusion_id: " + exclusion_id)
        seen_exclusions.add(exclusion_id)
        table_id = clean_text(item.get("table_id"), label + ".table_id", 120)
        check = clean_text(item.get("check"), label + ".check", 40).upper()
        if check not in CHECK_TYPES:
            raise ValueError(label + ".check must be one of " + ", ".join(CHECK_TYPES))
        record = {
            "exclusion_id": exclusion_id, "table_id": table_id, "check": check,
            "column": optional_text(item.get("column"), label + ".column", 120),
            "owner": optional_text(item.get("owner"), label + ".owner", 160),
            "reason": optional_text(item.get("reason"), label + ".reason", 300),
            "expires_at": parse_dt(item.get("expires_at"), label + ".expires_at", required=False),
        }
        if table_id not in source_map and table_id not in target_map:
            orphan_exclusions.append({**serializable(record), "code": "UNKNOWN_TABLE"})
            continue
        if record["owner"] is None or record["reason"] is None or record["expires_at"] is None:
            invalid.append({**serializable(record), "code": "INCOMPLETE_EXCLUSION",
                            "detail": "豁免必须同时具备 owner、reason 与 expires_at"})
            continue
        if record["expires_at"] < as_of:
            expired.append({**serializable(record), "code": "EXPIRED_EXCLUSION",
                            "detail": "豁免已于 %s 过期，不再生效" % record["expires_at"].isoformat()})
            continue
        valid_exclusions.append(record)

    def match_exclusion(table_id, check, column):
        for exclusion in valid_exclusions:
            if exclusion["table_id"] != table_id or exclusion["check"] != check:
                continue
            if exclusion["column"] is not None and exclusion["column"] != column:
                continue
            return exclusion
        return None

    rows = []
    evidence_gaps = []
    snapshot_notes = []
    for pair in pairs:
        source = source_map.get(pair["source_table_id"])
        target = target_map.get(pair["target_table_id"])
        table_id = pair["source_table_id"]
        issues = []
        applied_here = []
        checks = {}

        def add_issue(code, check, column, detail):
            exclusion = match_exclusion(table_id, check, column) \
                or match_exclusion(pair["target_table_id"], check, column)
            if exclusion is not None:
                applied_here.append({"exclusion_id": exclusion["exclusion_id"], "check": check,
                                     "column": column, "code": code,
                                     "owner": exclusion["owner"],
                                     "expires_at": exclusion["expires_at"].isoformat()})
                return False
            issues.append({"code": code, "check": check, "column": column, "detail": detail,
                           "severity": SEVERITY.get(code, "INFO")})
            return True

        if source is None or target is None:
            missing = []
            if source is None:
                missing.append(pair["source_table_id"])
            if target is None:
                missing.append(pair["target_table_id"])
            add_issue("MISSING_TABLE", "COLUMN", None,
                      "映射指向的表缺少统计：" + ", ".join(missing))
            checks = {"schema": {"status": "NOT_COMPARABLE", "details": ["缺少表统计"]},
                      "snapshot": {"status": "NOT_COMPARABLE", "details": ["缺少表统计"]},
                      "row_count": {"status": "NOT_COMPARABLE", "details": []},
                      "null_count": {"status": "NOT_COMPARABLE", "details": []},
                      "distinct_count": {"status": "NOT_COMPARABLE", "details": []},
                      "min_max": {"status": "NOT_COMPARABLE", "details": []},
                      "hash": {"status": "NOT_COMPARABLE", "details": []}}
        else:
            # ---- snapshot comparability -------------------------------------
            snapshot_details = []
            if source["captured_at"] is None or target["captured_at"] is None:
                add_issue("SNAPSHOT_TIME_MISSING", "SNAPSHOT", None,
                          "源表或目标表缺少 captured_at，无法确认快照时点")
                snapshot_details.append("缺少 captured_at")
                checks["snapshot"] = {"status": "INCOMPLETE", "details": snapshot_details}
            else:
                snapshot_ok = True
                if window is not None:
                    for side, table in (("源", source), ("目标", target)):
                        if table["watermark"] is not None \
                                and not window["from"] <= table["watermark"] <= window["to"]:
                            snapshot_ok = add_issue(
                                "WATERMARK_OUTSIDE_WINDOW", "SNAPSHOT", None,
                                "%s表 watermark %s 不在增量窗口内"
                                % (side, table["watermark"].isoformat())) and snapshot_ok
                            snapshot_details.append("%s表 watermark 落在窗口外" % side)
                        elif table["watermark"] is None:
                            snapshot_details.append("%s表无 watermark（按全量快照处理）" % side)
                        if table["captured_at"] < window["to"]:
                            snapshot_ok = add_issue(
                                "SNAPSHOT_BEFORE_WINDOW_END", "SNAPSHOT", None,
                                "%s表 captured_at 早于增量窗口结束时间" % side) and snapshot_ok
                            snapshot_details.append("%s表快照早于窗口结束" % side)
                elif source["watermark"] is not None or target["watermark"] is not None:
                    add_issue("WINDOW_MISSING", "SNAPSHOT", None,
                              "表带有 watermark（增量）但未提供 incremental_window，禁止比较")
                    snapshot_details.append("增量表缺少 incremental_window")
                    snapshot_ok = False
                else:
                    snapshot_notes.append({
                        "table_id": table_id,
                        "detail": "全量快照；源 captured_at=%s，目标 captured_at=%s"
                                  % (source["captured_at"].isoformat(),
                                     target["captured_at"].isoformat())})
                checks["snapshot"] = {"status": "OK" if snapshot_ok else "NOT_COMPARABLE",
                                      "details": snapshot_details}

            # ---- schema ------------------------------------------------------
            schema_details = []
            source_columns = {column["column"]: column for column in source["schema"]}
            target_columns = {column["column"]: column for column in target["schema"]}
            for name in sorted(set(source_columns) - set(target_columns)):
                add_issue("COLUMN_REMOVED", "COLUMN", name, "目标表缺少源列：" + name)
                schema_details.append("缺列 " + name)
            for name in sorted(set(target_columns) - set(source_columns)):
                add_issue("COLUMN_ADDED", "COLUMN", name, "目标表新增源表没有的列：" + name)
                schema_details.append("新增列 " + name)
            for name in sorted(set(source_columns) & set(target_columns)):
                code, detail = compare_types(source_columns[name]["type"],
                                             target_columns[name]["type"])
                if code == "OK":
                    continue
                if code == "TYPE_WIDENED":
                    issues.append({"code": code, "check": "TYPE", "column": name, "detail": detail,
                                   "severity": "INFO"})
                    continue
                add_issue(code, "TYPE", name, detail)
                schema_details.append(detail)
            for name in sorted(set(source_columns) & set(target_columns)):
                if source_columns[name]["nullable"] and not target_columns[name]["nullable"]:
                    add_issue("NULLABLE_NARROWED", "NULLABLE", name,
                              "源列可空而目标列非空：" + name)
                    schema_details.append("可空收窄 " + name)
            checks["schema"] = {"status": "DIFF" if any(
                issue["check"] in DRIFT_CHECKS for issue in issues) else "OK",
                "details": schema_details}

            # ---- row count ---------------------------------------------------
            difference = target["row_count"] - source["row_count"]
            relative = (abs(Decimal(difference)) * 100 / Decimal(source["row_count"])
                        if source["row_count"] else Decimal("0"))
            allowed = max(tolerance_abs, Decimal(source["row_count"]) * tolerance_rel / 100)
            row_details = ["源 %d 行 / 目标 %d 行 / 差 %d 行 / 允许 %s 行"
                           % (source["row_count"], target["row_count"], difference,
                              quant(allowed))]
            if abs(Decimal(difference)) > allowed:
                add_issue("ROW_COUNT_DIFF", "ROW_COUNT", None,
                          "行数差 %d 超过允许值 %s" % (difference, quant(allowed)))
                checks["row_count"] = {"status": "DIFF", "details": row_details,
                                       "difference": difference,
                                       "allowed": quant(allowed),
                                       "relative_pct": quant(relative)}
            else:
                checks["row_count"] = {"status": "OK", "details": row_details,
                                       "difference": difference,
                                       "allowed": quant(allowed),
                                       "relative_pct": quant(relative)}

            # ---- null / distinct counts --------------------------------------
            for field, check, code in (("null_counts", "NULL_COUNT", "NULL_COUNT_DIFF"),
                                       ("distinct_counts", "DISTINCT_COUNT",
                                        "DISTINCT_COUNT_DIFF")):
                source_counter = source[field]
                target_counter = target[field]
                details = []
                if source_counter is None or target_counter is None:
                    checks[check.lower()] = {"status": "NOT_PROVIDED", "details": []}
                    continue
                for name in sorted(set(source_counter) & set(target_counter)):
                    delta = target_counter[name] - source_counter[name]
                    if delta:
                        add_issue(code, check, name,
                                  "%s 差异：源 %d / 目标 %d" % (name, source_counter[name],
                                                              target_counter[name]))
                        details.append("%s 差 %d" % (name, delta))
                checks[check.lower()] = {"status": "DIFF" if details else "OK", "details": details}

            # ---- min / max ----------------------------------------------------
            min_max_details = []
            common_min_max = sorted(set(source["min_max"]) & set(target["min_max"]))
            for name in common_min_max:
                source_pair = source["min_max"][name]
                target_pair = target["min_max"][name]
                if not isinstance(source_pair, list) or not isinstance(target_pair, list) \
                        or len(source_pair) != 2 or len(target_pair) != 2:
                    raise ValueError("min_max for " + name + " must be a two-item list")
                same = all(_equal_value(source_pair[index], target_pair[index])
                           for index in (0, 1))
                if not same:
                    add_issue("MIN_MAX_DIFF", "MIN_MAX", name,
                              "%s 极值不同：源 %s..%s / 目标 %s..%s"
                              % (name, source_pair[0], source_pair[1],
                                 target_pair[0], target_pair[1]))
                    min_max_details.append(name)
            checks["min_max"] = {"status": "DIFF" if min_max_details else "OK",
                                 "details": min_max_details}

            # ---- hash ---------------------------------------------------------
            source_hash = source["hash"]
            target_hash = target["hash"]
            if source_hash is None or target_hash is None:
                checks["hash"] = {"status": "NOT_PROVIDED", "details": []}
            elif (source_hash["algorithm"], source_hash["salt_id"], source_hash["normalization"]) \
                    != (target_hash["algorithm"], target_hash["salt_id"],
                        target_hash["normalization"]):
                issues.append({"code": "HASH_NOT_COMPARABLE", "check": "HASH", "column": None,
                               "detail": "哈希算法/盐/规范化规则不一致（%s/%s/%s → %s/%s/%s），"
                                         "禁止比较分桶"
                                         % (source_hash["algorithm"], source_hash["salt_id"],
                                            source_hash["normalization"],
                                            target_hash["algorithm"], target_hash["salt_id"],
                                            target_hash["normalization"]),
                               "severity": "INFO"})
                checks["hash"] = {"status": "NOT_COMPARABLE", "details": ["算法/盐/规范化不一致"]}
            else:
                buckets = sorted(set(source_hash["buckets"]) | set(target_hash["buckets"]))
                details = []
                largest = None
                for name in buckets:
                    if name not in source_hash["buckets"] or name not in target_hash["buckets"]:
                        add_issue("HASH_BUCKET_MISSING", "HASH", None,
                                  "分桶 %s 仅存在于一侧" % name)
                        details.append("分桶 %s 缺失" % name)
                        continue
                    delta = target_hash["buckets"][name] - source_hash["buckets"][name]
                    if delta:
                        add_issue("HASH_BUCKET_DIFF", "HASH", None,
                                  "分桶 %s 差异 %d" % (name, delta))
                        details.append("分桶 %s 差 %d" % (name, delta))
                        if largest is None or abs(delta) > abs(largest["difference"]):
                            largest = {"bucket": name, "difference": delta}
                checks["hash"] = {"status": "DIFF" if details else "OK", "details": details,
                                  "largest_diff_bucket": largest,
                                  "algorithm": source_hash["algorithm"],
                                  "salt_id": source_hash["salt_id"],
                                  "normalization": source_hash["normalization"]}

        drift_codes = [issue for issue in issues if issue["check"] in DRIFT_CHECKS]
        snapshot_issue = [issue for issue in issues if issue["check"] in SNAPSHOT_CHECKS]
        row_issue = [issue for issue in issues if issue["code"] == "ROW_COUNT_DIFF"]
        content_issue = [issue for issue in issues if issue["code"] in (
            "NULL_COUNT_DIFF", "DISTINCT_COUNT_DIFF", "MIN_MAX_DIFF",
            "HASH_BUCKET_DIFF", "HASH_BUCKET_MISSING")]
        hash_blocked = [issue for issue in issues if issue["code"] == "HASH_NOT_COMPARABLE"]
        if any(issue["code"] == "MISSING_TABLE" for issue in issues):
            status = "SCHEMA_DRIFT"
        elif snapshot_issue and any(issue["code"] != "SNAPSHOT_TIME_MISSING"
                                    for issue in snapshot_issue):
            status = "SNAPSHOT_NOT_COMPARABLE"
        elif drift_codes:
            status = "SCHEMA_DRIFT"
        elif row_issue:
            status = "COUNT_MISMATCH"
        elif content_issue:
            status = "CONTENT_MISMATCH"
        elif snapshot_issue or hash_blocked:
            status = "PARTIAL"
        else:
            status = "MATCH"

        for issue in issues:
            if issue["code"] == "SNAPSHOT_TIME_MISSING" or issue["code"] == "HASH_NOT_COMPARABLE":
                evidence_gaps.append({"table_id": table_id, "code": issue["code"],
                                      "detail": issue["detail"]})
        if applied_here:
            applied.extend([{**item, "table_id": table_id} for item in applied_here])

        next_actions = []
        for issue in issues:
            if issue["severity"] in ("HIGH", "MEDIUM"):
                next_actions.append("%s：%s" % (issue["code"], issue["detail"]))
        rows.append({
            "source_table_id": pair["source_table_id"],
            "target_table_id": pair["target_table_id"],
            "status": status,
            "review_flags": sorted({issue["code"] for issue in issues
                                    if issue["severity"] != "INFO"}),
            "issues": sorted(issues, key=lambda item: (item["check"], item["column"] or "",
                                                       item["code"])),
            "checks": checks,
            "source_row_count": None if source is None else source["row_count"],
            "target_row_count": None if target is None else target["row_count"],
            "source_captured_at": None if source is None or source["captured_at"] is None
                                  else source["captured_at"].isoformat(),
            "target_captured_at": None if target is None or target["captured_at"] is None
                                  else target["captured_at"].isoformat(),
            "applied_exclusions": applied_here,
            "next_actions": next_actions or ["无需补充动作；保留统计快照以备复核"],
        })

    rows.sort(key=lambda item: item["source_table_id"])
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    overall = min((row["status"] for row in rows), key=lambda name: STATUS_ORDER.index(name))
    if orphan_exclusions or invalid:
        overall = min(overall, "PARTIAL", key=lambda name: STATUS_ORDER.index(name))

    compared = sum(1 for row in rows
                   if row["source_row_count"] is not None and row["target_row_count"] is not None)
    coverage = {
        "source_table_count": len(source_tables), "target_table_count": len(target_tables),
        "mapping_count": len(pairs), "compared_mapping_count": compared,
        "unmatched_source_tables": sorted(set(source_map) - seen_sources),
        "unmatched_target_tables": sorted(set(target_map) - seen_targets),
    }
    result = {
        "as_of": as_of.isoformat(),
        "status": overall,
        "table_count": len(rows),
        "status_counts": counts,
        "tables": rows,
        "coverage": coverage,
        "unmatched_source_tables": coverage["unmatched_source_tables"],
        "unmatched_target_tables": coverage["unmatched_target_tables"],
        "exclusions": {
            "applied": sorted(applied, key=lambda item: item["exclusion_id"]),
            "expired": sorted(expired, key=lambda item: item["exclusion_id"]),
            "invalid": sorted(invalid, key=lambda item: item["exclusion_id"]),
            "orphan": sorted(orphan_exclusions, key=lambda item: item["exclusion_id"]),
        },
        "evidence_gaps": sorted(evidence_gaps, key=lambda item: (item["table_id"], item["code"])),
        "snapshot_notes": snapshot_notes,
        "tolerance": {"row_count_abs": quant(tolerance_abs, 0),
                      "row_count_rel_pct": quant(tolerance_rel)},
        "note": "只比较用户提供的表统计快照。不连接数据库、不读取业务原始行、不推断未提供的映射、"
                "不自动修复或重跑迁移；哈希算法/盐/规范化规则不一致时禁止比较分桶；"
                "豁免必须有 owner、reason 与未过期的 expires_at。",
    }
    result["markdown_summary"] = build_markdown(result)
    return result


def _equal_value(left, right):
    left_number = as_number(left)
    right_number = as_number(right)
    if left_number is not None and right_number is not None:
        return left_number == right_number
    return str(left) == str(right)


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(out)


def build_markdown(result):
    lines = ["# 数据迁移行数与结构一致性门禁\n\n"]
    lines.append("基准时间 %s，共比较 %d 组表映射。**总体判定：%s**。\n\n"
                 % (result["as_of"], result["table_count"], result["status"]))
    if not result["tables"]:
        lines.append("没有任何表统计可比较。\n")
        lines.append("\n本技能不连接数据库、不读取业务原始行、不推断未提供的映射、不自动修复或重跑迁移。")
        return "".join(lines)
    lines.append("状态分布：" + ", ".join("%s=%d" % (key, value)
                                          for key, value in sorted(result["status_counts"].items()))
                 + "。\n\n")
    rows = [["源表", "目标表", "状态", "行数(源/目标)", "标记"]]
    for row in result["tables"]:
        rows.append([row["source_table_id"], row["target_table_id"], row["status"],
                     "%s / %s" % (row["source_row_count"] if row["source_row_count"] is not None else "—",
                                  row["target_row_count"] if row["target_row_count"] is not None else "—"),
                     ", ".join(row["review_flags"]) or "无"])
    lines.append(md_table(rows))
    lines.append("\n\n")
    for row in result["tables"]:
        lines.append("## %s → %s（%s）\n\n"
                     % (row["source_table_id"], row["target_table_id"], row["status"]))
        for name, check in sorted(row["checks"].items()):
            lines.append("- %s：%s%s\n" % (name, check["status"],
                                           ("（" + "；".join(check["details"]) + "）")
                                           if check.get("details") else ""))
        if row["issues"]:
            lines.append("- 问题：\n")
            for issue in row["issues"]:
                lines.append("  - [%s] %s：%s\n" % (issue["severity"], issue["code"], issue["detail"]))
        if row["applied_exclusions"]:
            lines.append("- 已应用豁免：" + ", ".join(
                "%s(%s)" % (item["exclusion_id"], item["check"])
                for item in row["applied_exclusions"]) + "\n")
        lines.append("\n")
    lines.append("本技能不连接数据库、不读取业务原始行、不推断未提供的映射、不自动修复或重跑迁移；"
                 "哈希算法/盐/规范化规则不一致时禁止比较分桶。")
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
                          "message": "请对照 references/guide.md 检查：输入必须不含连接串或口令原文、"
                                     "as_of 带时区、table_id 唯一、captured_at/watermark 带时区、"
                                     "exclusions 的 check 在允许集合内且具备 owner/reason/expires_at。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
