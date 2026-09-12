#!/usr/bin/env python3
"""Offline OpenAPI breaking-change impact gate.

Compares two OpenAPI 3.x JSON documents (old vs new) and classifies every
structural difference as BREAKING / REVIEW / INFO, folds in declared consumer
usage and time-boxed waivers, and emits a stable change list plus a migration
checklist.  Strictly offline and read-only: no code generation, no remote $ref
retrieval, no spec modification, no claim of proven semantic compatibility.
"""
import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
SECRET_HINTS = (
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
)
METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
COMPLEX_KEYS = ("additionalProperties", "oneOf", "anyOf", "allOf", "not")
MAX_NODES = 60000


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


def parse_as_of(value):
    """as_of must be a full ISO8601 datetime carrying an explicit UTC offset."""
    if not isinstance(value, str) or "T" not in value.strip():
        raise ValueError("as_of must be an ISO8601 datetime with a UTC offset")
    result = parse_dt(value, "as_of")
    if result.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    return result


def parse_date_only(value, label):
    if not isinstance(value, str) or not ISO_DATE.match(value.strip()):
        raise ValueError(label + " must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(label + " is not a valid date") from None


def change_id(kind, method, path, location, detail):
    key = "|".join([kind, method or "", path or "", location or "", detail or ""])
    return "CHG-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def validate_spec(spec, label):
    if not isinstance(spec, dict):
        raise ValueError(label + " must be a JSON object (YAML is not accepted)")
    version = spec.get("openapi") or spec.get("swagger")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(label + " must declare an openapi version")
    paths = spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ValueError(label + ".paths must be a nonempty object")
    for path, item in paths.items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError(label + ".paths keys must be absolute paths")
        if not isinstance(item, dict):
            raise ValueError(label + ".paths." + path + " must be an object")
    return version.strip()


def node_count(value, budget=None):
    if budget is None:
        budget = [0]
    budget[0] += 1
    if budget[0] > MAX_NODES:
        raise ValueError("spec is too large to compare safely")
    if isinstance(value, dict):
        for key in value:
            node_count(value[key], budget)
    elif isinstance(value, list):
        for entry in value:
            node_count(entry, budget)
    return budget[0]


def pointer(spec, ref):
    if not ref.startswith("#/"):
        return None
    node = spec
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            return None
        node = node[token]
    return node


def schema_facts(schema, spec, unresolved, visited=()):
    """Return a normalised fact dict, or None when the schema cannot be resolved."""
    if schema is None:
        return None
    if not isinstance(schema, dict):
        return None
    if "$ref" in schema:
        ref = schema.get("$ref")
        if not isinstance(ref, str):
            unresolved.add("(invalid $ref)")
            return None
        if not ref.startswith("#/"):
            unresolved.add(ref)
            return None
        if ref in visited:
            return None
        target = pointer(spec, ref)
        if target is None:
            unresolved.add(ref)
            return None
        return schema_facts(target, spec, unresolved, tuple(visited) + (ref,))
    facts = {
        "type": schema.get("type") if isinstance(schema.get("type"), str) else None,
        "format": schema.get("format") if isinstance(schema.get("format"), str) else None,
        "enum": None, "required": set(), "properties": {}, "complex": False,
    }
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        facts["enum"] = [entry for entry in enum]
    required = schema.get("required")
    if isinstance(required, list):
        facts["required"] = {str(entry) for entry in required}
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name in properties:
            facts["properties"][str(name)] = properties[name]
    if any(key in schema for key in COMPLEX_KEYS) or schema.get("nullable") is True:
        facts["complex"] = True
    if schema.get("type") == "array":
        items = schema.get("items")
        facts["items"] = schema_facts(items, spec, unresolved, visited) if isinstance(items, dict) else None
    return facts


def compare_schema(old_schema, new_schema, spec, scope, where, path, method, location, out):
    """Append schema-level changes. ``scope`` is REQUEST or RESPONSE."""
    old_facts = schema_facts(old_schema, spec["old"], spec["unresolved"], ())
    new_facts = schema_facts(new_schema, spec["new"], spec["unresolved"], ())
    if old_facts is None or new_facts is None:
        return
    if old_facts["type"] and new_facts["type"] and old_facts["type"] != new_facts["type"]:
        emit(out, "TYPE_CHANGED", "BREAKING", path, method, location,
             "%s→%s" % (old_facts["type"], new_facts["type"]), where)
    if old_facts["format"] and new_facts["format"] and old_facts["format"] != new_facts["format"]:
        emit(out, "FORMAT_CHANGED", "BREAKING", path, method, location,
             "%s→%s" % (old_facts["format"], new_facts["format"]), where)
    if old_facts["enum"] is not None and new_facts["enum"] is not None:
        old_values = [json.dumps(entry, sort_keys=True) for entry in old_facts["enum"]]
        new_values = [json.dumps(entry, sort_keys=True) for entry in new_facts["enum"]]
        removed = sorted(set(old_values) - set(new_values))
        added = sorted(set(new_values) - set(old_values))
        if removed and not added:
            emit(out, "ENUM_NARROWED", "BREAKING", path, method, location,
                 ",".join(json.loads(entry) if isinstance(json.loads(entry), str) else entry
                          for entry in removed), where)
        elif added and not removed:
            emit(out, "ENUM_WIDENED", "INFO", path, method, location,
                 ",".join(json.loads(entry) if isinstance(json.loads(entry), str) else entry
                          for entry in added), where)
        elif removed and added:
            emit(out, "ENUM_CHANGED", "REVIEW", path, method, location,
                 "移除 %s / 新增 %s" % (",".join(removed), ",".join(added)), where)
    if old_facts["complex"] or new_facts["complex"]:
        emit(out, "COMPLEX_SCHEMA_REVIEW", "REVIEW", path, method, location,
             "additionalProperties/oneOf/allOf/nullable", where)
    if scope == "REQUEST":
        added_required = sorted(new_facts["required"] - old_facts["required"])
        if added_required:
            emit(out, "REQUEST_REQUIRED_FIELD_ADDED", "BREAKING", path, method, location,
                 ",".join(added_required), where)
        for name in sorted(set(old_facts["properties"]) - set(new_facts["properties"])):
            emit(out, "REQUEST_FIELD_REMOVED", "REVIEW", path, method, location, name, where)
    else:
        added_required = sorted(new_facts["required"] - old_facts["required"])
        if added_required:
            emit(out, "RESPONSE_REQUIRED_FIELD_ADDED", "BREAKING", path, method, location,
                 ",".join(added_required), where)
        for name in sorted(set(old_facts["properties"]) - set(new_facts["properties"])):
            emit(out, "RESPONSE_FIELD_REMOVED", "REVIEW", path, method, location, name, where)
    if old_facts.get("items") is not None and new_facts.get("items") is not None:
        compare_schema(old_facts["items"], new_facts["items"], spec, scope, where + ".items",
                       path, method, location, out)


def emit(out, kind, severity, path, method, location, detail, evidence):
    out.append({"kind": kind, "severity": severity, "path": path, "method": method,
                "location": location, "detail": detail, "evidence": evidence})


def merge_parameters(path_item, op):
    merged = {}
    for source in (path_item.get("parameters"), op.get("parameters")):
        if isinstance(source, list):
            for entry in source:
                if isinstance(entry, dict) and isinstance(entry.get("name"), str):
                    merged[(entry["name"], str(entry.get("in")))] = entry
    return merged


def media_types(container):
    if not isinstance(container, dict):
        return {}
    content = container.get("content")
    return content if isinstance(content, dict) else {}


def collect_response_schema(operation, spec):
    schemas = []
    responses = operation.get("responses")
    if isinstance(responses, dict):
        for code in sorted(responses):
            if not str(code).startswith("2"):
                continue
            body = responses[code]
            if not isinstance(body, dict):
                continue
            for media in media_types(body).values():
                if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                    schemas.append(media["schema"])
    return schemas


def response_property_names(operation, spec, unresolved):
    names = set()
    for schema in collect_response_schema(operation, spec):
        facts = schema_facts(schema, spec, unresolved, ())
        if facts is None:
            continue
        if facts.get("items") is not None:
            facts = facts["items"]
        names |= set(facts.get("properties", {}))
    return names


def compare_operation(old_path_item, new_path_item, old_op, new_op, spec, path, method, out):
    old_params = merge_parameters(old_path_item, old_op)
    new_params = merge_parameters(new_path_item, new_op)
    for key in sorted(set(old_params) - set(new_params)):
        emit(out, "PARAMETER_REMOVED", "REVIEW", path, method, "parameter:" + key[0],
             "%s(%s)" % (key[0], key[1]), "parameters." + key[0])
    for key in sorted(set(new_params) - set(old_params)):
        entry = new_params[key]
        location = "parameter:" + key[0]
        if entry.get("required") is True:
            emit(out, "ADDED_REQUIRED_PARAMETER", "BREAKING", path, method, location,
                 key[0], "parameters." + key[0])
        else:
            emit(out, "ADDED_OPTIONAL_PARAMETER", "INFO", path, method, location,
                 key[0], "parameters." + key[0])
        added_facts = schema_facts(entry.get("schema"), spec["new"], spec["unresolved"], ())
        if added_facts is not None and added_facts["complex"]:
            emit(out, "COMPLEX_SCHEMA_REVIEW", "REVIEW", path, method, location,
                 "additionalProperties/oneOf/allOf/nullable", "parameters." + key[0])
    for key in sorted(set(old_params) & set(new_params)):
        before = old_params[key]
        after = new_params[key]
        location = "parameter:" + key[0]
        if before.get("required") is not True and after.get("required") is True:
            emit(out, "OPTIONAL_TO_REQUIRED", "BREAKING", path, method, location,
                 key[0], "parameters." + key[0])
        elif before.get("required") is True and after.get("required") is not True:
            emit(out, "REQUIRED_TO_OPTIONAL", "INFO", path, method, location,
                 key[0], "parameters." + key[0])
        compare_schema(before.get("schema"), after.get("schema"), spec, "REQUEST",
                       "parameters." + key[0], path, method, location, out)

    old_body = old_op.get("requestBody")
    new_body = new_op.get("requestBody")
    if isinstance(old_body, dict) and isinstance(new_body, dict):
        if old_body.get("required") is not True and new_body.get("required") is True:
            emit(out, "REQUEST_BODY_NOW_REQUIRED", "BREAKING", path, method, "requestBody",
                 "required:false→true", "requestBody.required")
        old_media = media_types(old_body)
        new_media = media_types(new_body)
        for name in sorted(set(old_media) - set(new_media)):
            emit(out, "REQUEST_MEDIA_TYPE_REMOVED", "BREAKING", path, method, "requestBody",
                 name, "requestBody.content." + name)
        for name in sorted(set(new_media) - set(old_media)):
            emit(out, "REQUEST_MEDIA_TYPE_ADDED", "INFO", path, method, "requestBody",
                 name, "requestBody.content." + name)
        for name in sorted(set(old_media) & set(new_media)):
            before = old_media[name] if isinstance(old_media[name], dict) else {}
            after = new_media[name] if isinstance(new_media[name], dict) else {}
            compare_schema(before.get("schema"), after.get("schema"), spec, "REQUEST",
                           "requestBody.content." + name, path, method, "requestBody", out)
    elif isinstance(new_body, dict) and not isinstance(old_body, dict):
        emit(out, "REQUEST_BODY_ADDED", "BREAKING", path, method, "requestBody",
             "新增 requestBody", "requestBody")
    elif isinstance(old_body, dict) and not isinstance(new_body, dict):
        emit(out, "REQUEST_BODY_REMOVED", "REVIEW", path, method, "requestBody",
             "移除 requestBody", "requestBody")

    old_responses = old_op.get("responses") if isinstance(old_op.get("responses"), dict) else {}
    new_responses = new_op.get("responses") if isinstance(new_op.get("responses"), dict) else {}
    for code in sorted(set(old_responses) - set(new_responses), key=str):
        if str(code).startswith("2"):
            emit(out, "SUCCESS_RESPONSE_REMOVED", "BREAKING", path, method,
                 "response:" + str(code), str(code), "responses." + str(code))
        else:
            emit(out, "RESPONSE_CODE_REMOVED", "REVIEW", path, method,
                 "response:" + str(code), str(code), "responses." + str(code))
    for code in sorted(set(old_responses) & set(new_responses), key=str):
        before = old_responses[code] if isinstance(old_responses[code], dict) else {}
        after = new_responses[code] if isinstance(new_responses[code], dict) else {}
        old_media = media_types(before)
        new_media = media_types(after)
        for name in sorted(set(old_media) - set(new_media)):
            emit(out, "RESPONSE_MEDIA_TYPE_REMOVED", "BREAKING", path, method,
                 "response:" + str(code), name, "responses.%s.content.%s" % (code, name))
        for name in sorted(set(old_media) & set(new_media)):
            old_media_entry = old_media[name] if isinstance(old_media[name], dict) else {}
            new_media_entry = new_media[name] if isinstance(new_media[name], dict) else {}
            compare_schema(old_media_entry.get("schema"), new_media_entry.get("schema"), spec,
                           "RESPONSE", "responses.%s.content.%s" % (code, name),
                           path, method, "response:" + str(code), out)

    old_security = old_op.get("security") if isinstance(old_op.get("security"), list) else None
    new_security = new_op.get("security") if isinstance(new_op.get("security"), list) else None
    if old_security and new_security is not None and not new_security:
        emit(out, "SECURITY_REQUIREMENT_REMOVED", "REVIEW", path, method, "security",
             "operation 不再要求认证", "security")
    referenced = set()
    for entry in (new_security or []):
        if isinstance(entry, dict):
            referenced |= {str(name) for name in entry}
    declared = new_op.get("__schemes__", set())
    for name in sorted(referenced - declared):
        emit(out, "SECURITY_SCHEME_REMOVED", "BREAKING", path, method, "security", name,
             "security." + name)


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    as_of = parse_as_of(data.get("as_of"))
    old_spec = data.get("old_spec")
    new_spec = data.get("new_spec")
    validate_spec(old_spec, "old_spec")
    validate_spec(new_spec, "new_spec")
    node_count(old_spec)
    node_count(new_spec)
    spec = {"old": old_spec, "new": new_spec, "unresolved": set()}

    declared_schemes = set()
    components = new_spec.get("components")
    if isinstance(components, dict) and isinstance(components.get("securitySchemes"), dict):
        declared_schemes = {str(name) for name in components["securitySchemes"]}

    raw = []
    old_paths = old_spec["paths"]
    new_paths = new_spec["paths"]
    for path in sorted(set(old_paths) | set(new_paths)):
        old_item = old_paths.get(path) if isinstance(old_paths.get(path), dict) else None
        new_item = new_paths.get(path) if isinstance(new_paths.get(path), dict) else None
        for method in METHODS:
            old_op = old_item.get(method) if old_item else None
            new_op = new_item.get(method) if new_item else None
            old_op = old_op if isinstance(old_op, dict) else None
            new_op = new_op if isinstance(new_op, dict) else None
            if old_op is not None and new_op is None:
                emit(raw, "REMOVED_OPERATION", "BREAKING", path, method, "operation",
                     "%s %s" % (method.upper(), path), "paths.%s.%s" % (path, method))
            elif new_op is not None and old_op is None:
                emit(raw, "ADDED_OPERATION", "INFO", path, method, "operation",
                     "%s %s" % (method.upper(), path), "paths.%s.%s" % (path, method))
            elif old_op is not None and new_op is not None:
                new_op = dict(new_op, __schemes__=declared_schemes)
                compare_operation(old_item, new_item, old_op, new_op, spec, path, method, raw)

    consumers_raw = data.get("consumer_usage")
    if consumers_raw is None:
        consumers_raw = []
    if not isinstance(consumers_raw, list) or len(consumers_raw) > 5000:
        raise ValueError("consumer_usage must be a list of up to 5000 items")
    consumers = []
    for index, item in enumerate(consumers_raw):
        label = "consumer_usage[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each consumer_usage item must be an object")
        client_id = clean_text(item.get("client_id"), label + ".client_id", 120)
        method = clean_text(item.get("method"), label + ".method", 16).lower()
        if method not in METHODS:
            raise ValueError(label + ".method must be a lowercase HTTP method")
        path = clean_text(item.get("path"), label + ".path", 300)
        if not path.startswith("/"):
            raise ValueError(label + ".path must start with /")
        fields_raw = item.get("fields")
        fields = []
        if fields_raw is not None:
            if not isinstance(fields_raw, list) or len(fields_raw) > 500:
                raise ValueError(label + ".fields must be a list of up to 500 names")
            fields = [clean_text(str(entry), label + ".fields[]", 120) for entry in fields_raw]
        codes_raw = item.get("status_codes")
        codes = []
        if codes_raw is not None:
            if not isinstance(codes_raw, list) or len(codes_raw) > 100:
                raise ValueError(label + ".status_codes must be a list of up to 100 codes")
            codes = [clean_text(str(entry), label + ".status_codes[]", 16) for entry in codes_raw]
        consumers.append({"client_id": client_id, "method": method, "path": path,
                          "fields": fields, "status_codes": codes})

    partial_reasons = []
    consumer_changes = []
    consumer_impact = []
    for consumer in consumers:
        reasons = []
        affected = []
        old_item = old_paths.get(consumer["path"])
        new_item = new_paths.get(consumer["path"])
        old_op = old_item.get(consumer["method"]) if isinstance(old_item, dict) else None
        new_op = new_item.get(consumer["method"]) if isinstance(new_item, dict) else None
        if not isinstance(new_op, dict):
            if isinstance(old_op, dict):
                entry = {"kind": "CONSUMER_OPERATION_REMOVED", "severity": "BREAKING",
                         "path": consumer["path"], "method": consumer["method"],
                         "location": "consumer:" + consumer["client_id"],
                         "detail": "%s %s" % (consumer["method"].upper(), consumer["path"]),
                         "evidence": "consumer_usage." + consumer["client_id"],
                         "client_id": consumer["client_id"]}
                consumer_changes.append(entry)
                reasons.append("该调用方使用的 %s %s 在新版本中已不存在"
                               % (consumer["method"].upper(), consumer["path"]))
            else:
                reasons.append("无法在旧版本中找到 %s %s，不能判定是否为破坏性变更"
                               % (consumer["method"].upper(), consumer["path"]))
                partial_reasons.append({"code": "CONSUMER_TARGET_UNKNOWN",
                                        "detail": consumer["client_id"] + " → " +
                                                  consumer["method"].upper() + " " + consumer["path"]})
        else:
            if consumer["fields"]:
                available = response_property_names(new_op, new_spec, spec["unresolved"])
                missing = [name for name in consumer["fields"] if name not in available]
                for name in missing:
                    entry = {"kind": "CONSUMER_FIELD_REMOVED", "severity": "BREAKING",
                             "path": consumer["path"], "method": consumer["method"],
                             "location": "consumer:" + consumer["client_id"],
                             "detail": name,
                             "evidence": "consumer_usage." + consumer["client_id"] + ".fields",
                             "client_id": consumer["client_id"]}
                    consumer_changes.append(entry)
                    reasons.append("响应中不再提供字段「%s」" % name)
            responses = new_op.get("responses") if isinstance(new_op.get("responses"), dict) else {}
            for code in consumer["status_codes"]:
                if str(code) not in {str(key) for key in responses}:
                    entry = {"kind": "CONSUMER_STATUS_CODE_REMOVED", "severity": "BREAKING",
                             "path": consumer["path"], "method": consumer["method"],
                             "location": "consumer:" + consumer["client_id"],
                             "detail": str(code),
                             "evidence": "consumer_usage." + consumer["client_id"] + ".status_codes",
                             "client_id": consumer["client_id"]}
                    consumer_changes.append(entry)
                    reasons.append("响应码 %s 在新版本中已不存在" % code)
        status = "BREAKING" if reasons and "无法在旧版本中" not in reasons[0] else ("UNKNOWN" if reasons else "OK")
        consumer_impact.append({"client_id": consumer["client_id"], "status": status,
                                "reasons": reasons,
                                "change_ids": [change_id(entry["kind"], entry["method"],
                                                         entry["path"], entry["location"],
                                                         entry["detail"])
                                               for entry in consumer_changes
                                               if entry["client_id"] == consumer["client_id"]]})

    waivers_raw = data.get("waivers")
    if waivers_raw is None:
        waivers_raw = []
    if not isinstance(waivers_raw, list) or len(waivers_raw) > 2000:
        raise ValueError("waivers must be a list of up to 2000 items")
    waivers = []
    seen_waivers = set()
    for index, item in enumerate(waivers_raw):
        label = "waivers[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each waiver must be an object")
        waiver_id = clean_text(item.get("id"), label + ".id", 120)
        if waiver_id in seen_waivers:
            raise ValueError("duplicate waiver id: " + waiver_id)
        seen_waivers.add(waiver_id)
        waivers.append({
            "id": waiver_id,
            "reason": clean_text(item.get("reason"), label + ".reason", 500),
            "owner": clean_text(item.get("owner"), label + ".owner", 160),
            "expires_at": parse_date_only(item.get("expires_at"), label + ".expires_at").isoformat(),
            "kind": optional_text(item.get("kind"), label + ".kind", 60),
            "path": optional_text(item.get("path"), label + ".path", 300),
            "method": (optional_text(item.get("method"), label + ".method", 16) or "").lower(),
            "change_id": optional_text(item.get("change_id"), label + ".change_id", 60),
        })

    changes = []
    consumer_lookup = {consumer["client_id"]: consumer for consumer in consumers}
    breaking_clients = {impact["client_id"] for impact in consumer_impact
                        if impact["status"] == "BREAKING"}
    for entry in raw + consumer_changes:
        entry = dict(entry)
        entry["change_id"] = change_id(entry["kind"], entry["method"], entry["path"],
                                       entry["location"], entry["detail"])
        entry["status"] = entry["severity"]
        entry["waiver_id"] = None
        entry["review_flags"] = []
        if entry.get("client_id"):
            entry["affected_consumers"] = [entry["client_id"]]
        else:
            entry["affected_consumers"] = sorted(
                client_id for client_id in breaking_clients
                if consumer_lookup[client_id]["method"] == entry["method"]
                and consumer_lookup[client_id]["path"] == entry["path"])
        changes.append(entry)
    changes.sort(key=lambda item: (item["path"], item["method"], item["kind"], item["detail"] or ""))

    applied, expired, unmatched = [], [], []
    for waiver in waivers:
        match = None
        for entry in changes:
            if waiver["change_id"] and waiver["change_id"] == entry["change_id"]:
                match = entry
                break
            if (waiver["kind"] and waiver["path"] and waiver["method"]
                    and waiver["kind"] == entry["kind"] and waiver["path"] == entry["path"]
                    and waiver["method"] == entry["method"]):
                match = entry
                break
        if match is None:
            unmatched.append(waiver)
            continue
        expiry = date.fromisoformat(waiver["expires_at"])
        if expiry < as_of.date():
            expired.append(waiver)
            match["review_flags"] = sorted(set(match["review_flags"]) | {"EXPIRED_WAIVER"})
        else:
            applied.append(waiver)
            match["status"] = "WAIVED"
            match["waiver_id"] = waiver["id"]

    for ref in sorted(spec["unresolved"]):
        partial_reasons.append({"code": "UNRESOLVED_REF",
                                "detail": "无法在本地解析引用（不取回远程文档）: " + ref})

    counts = {}
    for entry in changes:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1

    if partial_reasons:
        status = "PARTIAL"
    elif counts.get("BREAKING"):
        status = "BREAKING"
    elif counts.get("REVIEW"):
        status = "REVIEW"
    else:
        status = "PASS"

    checklist = []
    for entry in changes:
        if entry["status"] != "BREAKING":
            continue
        checklist.append("%s %s：%s（%s）→ 迁移/兼容处理"
                         % (entry["method"].upper(), entry["path"], entry["kind"], entry["detail"]))
    if not checklist:
        checklist.append("未发现未豁免的破坏性变更；仍需人工确认复杂 schema 语义。")

    result = {
        "as_of": as_of.isoformat(),
        "status": status,
        "old_version": str(old_spec.get("info", {}).get("version", "")) if isinstance(old_spec.get("info"), dict) else "",
        "new_version": str(new_spec.get("info", {}).get("version", "")) if isinstance(new_spec.get("info"), dict) else "",
        "change_count": len(changes),
        "status_counts": counts,
        "changes": changes,
        "breaking_changes": [entry["change_id"] for entry in changes if entry["status"] == "BREAKING"],
        "review_changes": [entry["change_id"] for entry in changes if entry["status"] == "REVIEW"],
        "waivers": {"applied": applied, "expired": expired, "unmatched": unmatched},
        "consumer_impact": sorted(consumer_impact, key=lambda item: item["client_id"]),
        "migration_checklist": checklist,
        "unresolved_refs": sorted({ref for ref in spec["unresolved"]}),
        "partial_reasons": partial_reasons,
        "note": "只比较用户提供的两份 JSON 规范。不运行代码生成、不访问远程 $ref、不修改规范，"
                "也不宣称语义兼容性已被完全证明。新增可选字段默认非破坏，"
                "additionalProperties/oneOf/allOf/nullable 等复杂语义一律保守标人工复核。",
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
    lines = ["# OpenAPI 破坏性变更影响门禁\n\n"]
    lines.append("版本 %s → %s，基准时间 %s。共 %d 处差异。\n\n"
                 % (result["old_version"] or "—", result["new_version"] or "—",
                    result["as_of"], result["change_count"]))
    lines.append("**总体判定：%s**。\n\n" % result["status"])
    lines.append("状态分布：" + ", ".join("%s=%d" % (key, value)
                                          for key, value in sorted(result["status_counts"].items())) + "。\n\n")
    rows = [["级别", "方法", "路径", "类型", "细节", "状态"]]
    for entry in result["changes"]:
        rows.append([entry["severity"], entry["method"].upper(), entry["path"],
                     entry["kind"], entry["detail"], entry["status"]])
    lines.append(md_table(rows))
    if result["consumer_impact"]:
        lines.append("\n\n调用方影响：\n")
        for impact in result["consumer_impact"]:
            lines.append("- %s：%s%s\n" % (impact["client_id"], impact["status"],
                                           ("；" + "；".join(impact["reasons"])) if impact["reasons"] else ""))
    if result["waivers"]["applied"] or result["waivers"]["expired"] or result["waivers"]["unmatched"]:
        lines.append("\n豁免：已生效 %d、已过期 %d、未匹配 %d。已过期豁免不抑制破坏性判定。\n"
                     % (len(result["waivers"]["applied"]), len(result["waivers"]["expired"]),
                        len(result["waivers"]["unmatched"])))
    lines.append("\n迁移清单：\n")
    for item in result["migration_checklist"]:
        lines.append("- " + item + "\n")
    lines.append("\n本技能不运行代码生成、不访问远程 $ref、不修改规范，"
                 "也不宣称语义兼容性已被完全证明。")
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
    except (ValueError, KeyError, TypeError, OSError, RecursionError):
        print(json.dumps({"error": "invalid_input",
                          "message": "请对照 references/guide.md 检查：as_of 带时区、old_spec/new_spec 为含 "
                                     "openapi 与 paths 的 JSON 对象（不接受 YAML）、consumer_usage[].method 为小写 "
                                     "HTTP 方法且 path 以 / 开头、waivers[].expires_at 为 YYYY-MM-DD。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
