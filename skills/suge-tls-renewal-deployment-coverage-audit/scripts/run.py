#!/usr/bin/env python3
"""Offline TLS certificate renewal & deployment coverage audit.

Evaluates four independent dimensions per certificate: expiry risk, renewal
process state, replacement issuance, and deployment coverage across targets.
Only user-provided observations are used - no network probing, no issuance and
no deployment is ever performed.
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
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
    re.compile(r"(?<![A-Za-z])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
)
RENEWAL_BLOCKING = {"failed", "blocked"}
RENEWAL_CLAIMED = {"auto_enabled", "pending", "completed", "renewed", "paid"}
VALIDATION_OK = {"valid", "ok", "passed", "good"}


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
            raise ValueError(label + " contains key material; remove it before auditing")
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


def integer(value, label, minimum=Decimal("0"), maximum=Decimal("1000000")):
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


def quant(value, places=2):
    return str(Decimal(value).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP))


def hours_between(later, earlier):
    return Decimal(str(round((later - earlier).total_seconds() / 3600.0, 4)))


def san_covers(pattern, name):
    pattern = pattern.lower().strip()
    name = name.lower().strip()
    if not pattern or not name:
        return False
    if pattern == name:
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return name.endswith(suffix) and name.count(".") == pattern.count(".")
    return False


def covers_all(cert_sans, required):
    return all(any(san_covers(pattern, need) for pattern in cert_sans) for need in required)


def parse_policy(policy):
    if not isinstance(policy, dict):
        raise ValueError("policy must be an object")
    warn_days = integer(policy.get("warn_days", 30), "policy.warn_days", Decimal("0"), Decimal("3650"))
    stale_hours = number(policy.get("max_observation_stale_hours", 48),
                         "policy.max_observation_stale_hours", Decimal("0.01"), Decimal("87600"))
    require_all = parse_bool(policy.get("require_all_targets_deployed", True),
                             "policy.require_all_targets_deployed")
    return {"warn_days": warn_days, "max_observation_stale_hours": stale_hours,
            "require_all_targets_deployed": require_all}


def parse_certificate(item, seen_ids, seen_fingerprints):
    if not isinstance(item, dict):
        raise ValueError("each certificate must be an object")
    cert_id = clean_text(item.get("certificate_id") or item.get("id"), "certificate_id", maximum=120)
    if cert_id in seen_ids:
        raise ValueError("duplicate certificate_id: " + cert_id)
    seen_ids.add(cert_id)
    fingerprint_raw = item.get("serial_fingerprint")
    fingerprint = clean_text(str(fingerprint_raw), "serial_fingerprint", maximum=200, allow_empty=True) \
        if fingerprint_raw is not None else ""
    duplicate_fingerprint = bool(fingerprint) and fingerprint in seen_fingerprints
    if fingerprint:
        seen_fingerprints.add(fingerprint)
    sans_raw = item.get("sans")
    if sans_raw is None:
        sans_raw = []
    if not isinstance(sans_raw, list) or len(sans_raw) > 200:
        raise ValueError("sans must be a list of up to 200 items")
    sans = [clean_text(entry, "sans[]", maximum=253) for entry in sans_raw]
    if len(set(sans)) != len(sans):
        raise ValueError("sans contains duplicates in " + cert_id)
    issuer_raw = item.get("issuer")
    issuer = clean_text(str(issuer_raw), "issuer", maximum=200, allow_empty=True) \
        if issuer_raw is not None else ""
    not_before = parse_dt(item.get("not_before"), "not_before", required=False)
    not_after = parse_dt(item.get("not_after"), "not_after")
    if not_before is not None and not_after <= not_before:
        raise ValueError("not_after must be later than not_before in " + cert_id)
    renewal_mode = clean_text(str(item.get("renewal_mode") or "unknown"), "renewal_mode", maximum=32)
    renewal_status = clean_text(str(item.get("renewal_status") or "unknown"), "renewal_status", maximum=32)
    validation_status = clean_text(str(item.get("validation_status") or "unknown"), "validation_status", maximum=32)
    replacement_raw = item.get("issued_replacement_id")
    replacement = clean_text(str(replacement_raw), "issued_replacement_id", maximum=120, allow_empty=True) \
        if replacement_raw is not None else ""
    return {"certificate_id": cert_id, "serial_fingerprint": fingerprint,
            "duplicate_fingerprint": duplicate_fingerprint, "sans": sans, "issuer": issuer,
            "issuer_known": bool(issuer), "not_before": not_before, "not_after": not_after,
            "renewal_mode": renewal_mode, "renewal_status": renewal_status,
            "validation_status": validation_status,
            "issued_replacement_id": replacement or None}


def parse_target(item, seen_ids):
    if not isinstance(item, dict):
        raise ValueError("each deployment target must be an object")
    target_id = clean_text(item.get("target_id") or item.get("id"), "target_id", maximum=120)
    if target_id in seen_ids:
        raise ValueError("duplicate target_id: " + target_id)
    seen_ids.add(target_id)
    hostname = clean_text(item.get("hostname"), "target.hostname", maximum=253)
    port_raw = item.get("port", 443)
    port = integer(port_raw, "target.port", Decimal("1"), Decimal("65535"))
    required_raw = item.get("required_sans")
    if required_raw is None:
        required_raw = [hostname]
    if not isinstance(required_raw, list) or len(required_raw) > 200:
        raise ValueError("required_sans must be a list of up to 200 items")
    required = [clean_text(entry, "required_sans[]", maximum=253) for entry in required_raw]
    expected_raw = item.get("expected_certificate_id")
    expected = clean_text(str(expected_raw), "expected_certificate_id", maximum=120, allow_empty=True) \
        if expected_raw is not None else ""
    return {"target_id": target_id, "hostname": hostname, "port": port,
            "required_sans": required, "expected_certificate_id": expected or None}


def parse_observation(item, seen, as_of):
    if not isinstance(item, dict):
        raise ValueError("each observation must be an object")
    target_id = clean_text(item.get("target_id"), "observation.target_id", maximum=120)
    observed_at = parse_dt(item.get("observed_at"), "observation.observed_at")
    key = (target_id, observed_at.isoformat())
    if key in seen:
        raise ValueError("duplicate observation for target at the same timestamp: " + target_id)
    seen.add(key)
    observed_id_raw = item.get("observed_certificate_id")
    observed_id = clean_text(str(observed_id_raw), "observed_certificate_id", maximum=120, allow_empty=True) \
        if observed_id_raw is not None else ""
    fingerprint_raw = item.get("observed_fingerprint")
    fingerprint = clean_text(str(fingerprint_raw), "observed_fingerprint", maximum=200, allow_empty=True) \
        if fingerprint_raw is not None else ""
    hostname_raw = item.get("hostname_match")
    hostname_match = parse_bool(hostname_raw, "observation.hostname_match") if hostname_raw is not None else None
    chain_raw = item.get("chain_valid")
    chain_valid = parse_bool(chain_raw, "observation.chain_valid") if chain_raw is not None else None
    return {"target_id": target_id, "observed_at": observed_at, "observed_certificate_id": observed_id or None,
            "observed_fingerprint": fingerprint or None, "hostname_match": hostname_match,
            "chain_valid": chain_valid, "future": observed_at > as_of}


def audit_certificate(cert, targets, observations, policy, as_of):
    warn = policy["warn_days"]
    stale = policy["max_observation_stale_hours"]
    days_to_expiry = Decimal(str(round((cert["not_after"] - as_of).total_seconds() / 86400.0, 4)))
    expired = days_to_expiry < 0
    reasons = []
    review_flags = []
    if cert["duplicate_fingerprint"]:
        review_flags.append("DUPLICATE_FINGERPRINT")
    if not cert["issuer_known"]:
        review_flags.append("ISSUER_UNKNOWN")

    # -- dimension 1: expiry risk --
    if expired:
        expiry_risk = "EXPIRED"
        reasons.append("证书已于 %s 过期（距基准 %s 天）" % (cert["not_after"].isoformat(), quant(days_to_expiry)))
    elif days_to_expiry <= Decimal(warn):
        expiry_risk = "WARN"
        reasons.append("距到期 %s 天，进入 %d 天预警窗口" % (quant(days_to_expiry), warn))
    else:
        expiry_risk = "OK"

    # -- dimension 2: renewal process --
    if cert["renewal_status"] in RENEWAL_BLOCKING:
        renewal_process = "BLOCKED"
    elif cert["renewal_status"] == "not_configured":
        renewal_process = "BLOCKED" if days_to_expiry <= Decimal(warn) else "NOT_STARTED"
    elif cert["renewal_status"] in ("completed", "renewed"):
        renewal_process = "COMPLETED"
    elif cert["renewal_status"] in ("pending", "paid"):
        renewal_process = "IN_PROGRESS"
    elif cert["renewal_status"] == "auto_enabled":
        renewal_process = "SCHEDULED"
    else:
        renewal_process = "MANUAL"

    # -- dimension 3: replacement issuance --
    replacement = cert["issued_replacement_id"]
    if replacement:
        replacement_state = "ISSUED"
    elif cert["renewal_status"] in RENEWAL_CLAIMED and days_to_expiry <= Decimal(warn):
        replacement_state = "NOT_ISSUED"
    elif days_to_expiry <= Decimal(warn):
        replacement_state = "NOT_ISSUED"
    else:
        replacement_state = "NOT_REQUIRED"

    expected_deployed = replacement or cert["certificate_id"]

    # -- dimension 4: deployment coverage --
    related = [t for t in targets if t["expected_certificate_id"] == cert["certificate_id"]]
    target_rows = []
    hostname_mismatch = False
    drift = False
    chain_invalid = False
    stale_observation = False
    unverified = False
    for target in related:
        if not covers_all(cert["sans"], target["required_sans"]):
            hostname_mismatch = True
        history = [o for o in observations if o["target_id"] == target["target_id"] and not o["future"]]
        latest = max(history, key=lambda o: o["observed_at"]) if history else None
        row = {"target_id": target["target_id"], "hostname": target["hostname"], "port": target["port"],
               "required_sans": target["required_sans"], "expected_deployed": expected_deployed,
               "observed_certificate_id": None, "observed_at": None, "age_hours": None,
               "hostname_match": None, "chain_valid": None, "coverage_status": "UNVERIFIED"}
        if latest is None:
            unverified = True
            row["coverage_status"] = "NO_OBSERVATION"
        else:
            age = hours_between(as_of, latest["observed_at"])
            row.update({"observed_certificate_id": latest["observed_certificate_id"],
                        "observed_at": latest["observed_at"].isoformat(), "age_hours": quant(age),
                        "hostname_match": latest["hostname_match"], "chain_valid": latest["chain_valid"]})
            row_stale = age > stale
            row_drift = latest["observed_certificate_id"] != expected_deployed
            row_hostname = latest["hostname_match"] is False
            row_chain = latest["chain_valid"] is False
            if row_stale:
                stale_observation = True
            if row_drift:
                drift = True
            if row_hostname:
                hostname_mismatch = True
            if row_chain:
                chain_invalid = True
            if row_chain:
                row["coverage_status"] = "CHAIN_INVALID"
            elif row_hostname:
                row["coverage_status"] = "HOSTNAME_MISMATCH"
            elif row_drift:
                row["coverage_status"] = "DRIFT"
            elif row_stale:
                row["coverage_status"] = "STALE"
            else:
                row["coverage_status"] = "COVERED"
        target_rows.append(row)

    if not related:
        deployment_coverage = "NO_TARGETS"
        if policy["require_all_targets_deployed"]:
            unverified = True
            deployment_coverage = "UNVERIFIED"
    elif drift:
        deployment_coverage = "DRIFT"
    elif unverified:
        deployment_coverage = "UNVERIFIED"
    elif hostname_mismatch or chain_invalid or stale_observation:
        deployment_coverage = "INCOMPLETE"
    else:
        deployment_coverage = "COMPLETE"

    # -- final status by documented priority --
    if cert["renewal_status"] in RENEWAL_BLOCKING:
        status = "RENEWAL_BLOCKED"
        reasons.append("续期流程状态为 %s，自动/手动续期均未推进" % cert["renewal_status"])
    elif cert["renewal_status"] == "not_configured" and days_to_expiry <= Decimal(warn):
        status = "RENEWAL_BLOCKED"
        reasons.append("未配置续期机制且已进入预警窗口")
    elif replacement_state == "NOT_ISSUED" and cert["renewal_status"] in RENEWAL_CLAIMED:
        status = "REPLACEMENT_NOT_ISSUED"
        reasons.append("续期状态为 %s 但 issued_replacement_id 为空：已扣款/已续期不等于替代证书已签发"
                       % cert["renewal_status"])
    elif replacement_state == "NOT_ISSUED":
        status = "EXPIRING"
        reasons.append("距到期 %s 天且无替代证书签发记录" % quant(days_to_expiry))
    elif expired:
        status = "EXPIRING"
    elif drift:
        status = "DEPLOYMENT_DRIFT"
        drift_targets = [r["target_id"] for r in target_rows if r["coverage_status"] == "DRIFT"]
        reasons.append("替代证书已签发但目标仍在观测旧证书：%s" % ", ".join(drift_targets))
    elif hostname_mismatch:
        status = "HOSTNAME_MISMATCH"
        reasons.append("证书 SAN 未覆盖目标要求的域名，或观测 hostname_match 为 false")
    elif chain_invalid:
        status = "CHAIN_INVALID"
        reasons.append("观测链校验失败（chain_valid=false）或证书自身校验状态为 %s" % cert["validation_status"])
    elif stale_observation:
        status = "OBSERVATION_STALE"
        reasons.append("最近观测陈旧（超过 %s 小时），部署状态无法确认" % quant(stale))
    elif unverified or not cert["issuer_known"]:
        status = "UNKNOWN"
        reasons.append("缺少观测数据或 issuer 未知，无法确认部署覆盖")
    else:
        status = "PASS"
        reasons.append("续期已完成、替代证书已签发且全部目标观测到新证书")

    if cert["validation_status"] not in VALIDATION_OK and cert["validation_status"] != "unknown" \
            and status == "PASS":
        status = "CHAIN_INVALID"
        reasons.append("证书自身校验状态为 %s" % cert["validation_status"])

    return {
        "certificate_id": cert["certificate_id"], "serial_fingerprint": cert["serial_fingerprint"],
        "sans": cert["sans"], "issuer": cert["issuer"], "issuer_known": cert["issuer_known"],
        "not_before": cert["not_before"].isoformat() if cert["not_before"] else None,
        "not_after": cert["not_after"].isoformat(), "days_to_expiry": quant(days_to_expiry),
        "expired": expired, "renewal_mode": cert["renewal_mode"], "renewal_status": cert["renewal_status"],
        "validation_status": cert["validation_status"], "issued_replacement_id": cert["issued_replacement_id"],
        "expected_deployed_id": expected_deployed,
        "dimensions": {"expiry_risk": expiry_risk, "renewal_process": renewal_process,
                       "replacement_issuance": replacement_state, "deployment_coverage": deployment_coverage},
        "targets": target_rows, "status": status, "reasons": reasons, "review_flags": review_flags,
    }


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    as_of = parse_dt(data.get("as_of"), "as_of")
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    policy = parse_policy(data.get("policy") or {})

    certs_raw = data.get("certificates")
    if not isinstance(certs_raw, list) or not 1 <= len(certs_raw) <= 500:
        raise ValueError("certificates must be a list of 1-500 items")
    seen_cert_ids = set()
    seen_fingerprints = set()
    certs = [parse_certificate(item, seen_cert_ids, seen_fingerprints) for item in certs_raw]

    targets_raw = data.get("deployment_targets")
    if targets_raw is None:
        targets_raw = []
    if not isinstance(targets_raw, list) or len(targets_raw) > 1000:
        raise ValueError("deployment_targets must be a list of up to 1000 items")
    seen_target_ids = set()
    targets = [parse_target(item, seen_target_ids) for item in targets_raw]

    obs_raw = data.get("observations")
    if obs_raw is None:
        obs_raw = []
    if not isinstance(obs_raw, list) or len(obs_raw) > 10000:
        raise ValueError("observations must be a list of up to 10000 items")
    seen_obs = set()
    observations = [parse_observation(item, seen_obs, as_of) for item in obs_raw]
    for observation in observations:
        if observation["target_id"] not in seen_target_ids:
            raise ValueError("observation references unknown target_id: " + observation["target_id"])

    known_cert_ids = seen_cert_ids
    for target in targets:
        if target["expected_certificate_id"] and target["expected_certificate_id"] not in known_cert_ids:
            raise ValueError("target references unknown expected_certificate_id: " + target["expected_certificate_id"])
        if target["expected_certificate_id"] is None:
            matches = [c for c in certs if covers_all(c["sans"], target["required_sans"])]
            if len(matches) == 1:
                target["expected_certificate_id"] = matches[0]["certificate_id"]

    results = [audit_certificate(cert, targets, observations, policy, as_of) for cert in certs]
    counts = {}
    for result in results:
        counts[result["status"]] = counts.get(result["status"], 0) + 1

    matrix = []
    for target in targets:
        history = [o for o in observations if o["target_id"] == target["target_id"] and not o["future"]]
        latest = max(history, key=lambda o: o["observed_at"]) if history else None
        age = hours_between(as_of, latest["observed_at"]) if latest else None
        row = {"target_id": target["target_id"], "hostname": target["hostname"], "port": target["port"],
               "required_sans": target["required_sans"],
               "expected_certificate_id": target["expected_certificate_id"],
               "observed_certificate_id": latest["observed_certificate_id"] if latest else None,
               "observed_at": latest["observed_at"].isoformat() if latest else None,
               "age_hours": quant(age) if age is not None else None,
               "hostname_match": latest["hostname_match"] if latest else None,
               "chain_valid": latest["chain_valid"] if latest else None}
        if latest is None:
            row["coverage_status"] = "NO_OBSERVATION"
        elif age is not None and age > policy["max_observation_stale_hours"]:
            row["coverage_status"] = "STALE"
        elif latest["chain_valid"] is False:
            row["coverage_status"] = "CHAIN_INVALID"
        elif latest["hostname_match"] is False:
            row["coverage_status"] = "HOSTNAME_MISMATCH"
        elif target["expected_certificate_id"] and latest["observed_certificate_id"] != \
                (next((c["issued_replacement_id"] for c in certs
                       if c["certificate_id"] == target["expected_certificate_id"]), None)
                 or target["expected_certificate_id"]):
            row["coverage_status"] = "DRIFT"
        else:
            row["coverage_status"] = "COVERED"
        matrix.append(row)

    order = sorted(results, key=lambda r: r["not_after"])
    top_flags = []
    for result in results:
        for flag in result["review_flags"]:
            top_flags.append(result["certificate_id"] + ":" + flag)

    checklist = []
    for result in results:
        if result["status"] == "RENEWAL_BLOCKED":
            checklist.append("%s：续期流程中断，先修复自动续期/配置再谈签发" % result["certificate_id"])
        elif result["status"] == "REPLACEMENT_NOT_ISSUED":
            checklist.append("%s：已扣款/已续期但无替代证书，先确认 CA 是否真的签发" % result["certificate_id"])
        elif result["status"] == "DEPLOYMENT_DRIFT":
            checklist.append("%s：新证书已签发但部分目标仍在用旧证书，按目标逐个重新加载" % result["certificate_id"])
        elif result["status"] == "EXPIRING":
            checklist.append("%s：距到期 %s 天，优先确认续期与部署时间窗" % (result["certificate_id"],
                                                                            result["days_to_expiry"]))
        elif result["status"] == "OBSERVATION_STALE":
            checklist.append("%s：观测陈旧，先补一次真实观测再判断" % result["certificate_id"])
        elif result["status"] == "UNKNOWN":
            checklist.append("%s：缺观测或 issuer 未知，补齐证据后再判定" % result["certificate_id"])

    return {
        "as_of": as_of.isoformat(),
        "certificate_count": len(results),
        "target_count": len(targets),
        "observation_count": len(observations),
        "status_counts": counts,
        "certificates": results,
        "coverage_matrix": matrix,
        "renewal_order": [{"certificate_id": r["certificate_id"], "not_after": r["not_after"],
                           "days_to_expiry": r["days_to_expiry"], "status": r["status"]} for r in order],
        "review_flags": top_flags,
        "action_checklist": checklist,
        "markdown_summary": build_markdown(results, matrix, as_of, counts, policy),
        "note": "四个维度分别评估：到期风险 / 续期流程 / 替代证书签发 / 部署覆盖。"
                "已扣款或已续期不等于替代证书已签发，已签发不等于已部署到所有目标。"
                "本技能只依据用户提供的观测，不联网探测、不购买、不签发、不部署证书。",
    }


def build_markdown(results, matrix, as_of, counts, policy):
    rows = [["证书", "SAN", "距到期(天)", "续期", "替代证书", "部署覆盖", "状态"]]
    for result in results:
        rows.append([result["certificate_id"], len(result["sans"]), result["days_to_expiry"],
                     result["renewal_status"], result["dimensions"]["replacement_issuance"],
                     result["dimensions"]["deployment_coverage"], result["status"]])
    head = ["# TLS 证书续期部署覆盖审计（基准 %s，预警 %d 天）\n\n" % (as_of.isoformat(), policy["warn_days"])]
    head.append("口径：四个维度**分别评估**——到期风险、续期流程、替代证书签发、部署覆盖。"
                "已续费/已扣款但替代证书未签发不算完成；已签发但目标仍观测旧指纹 = DEPLOYMENT_DRIFT。\n\n")
    head.append(md_table(rows))
    head.append("\n\n状态分布：" + ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())) + "。\n")
    head.append("\n按最早到期排序：")
    head.append(", ".join("%s(%s 天)" % (r["certificate_id"], r["days_to_expiry"])
                          for r in sorted(results, key=lambda x: x["not_after"])))
    head.append("\n\n覆盖矩阵（目标 → 观测）：\n\n")
    matrix_rows = [["目标", "域名", "端口", "期望证书", "观测证书", "观测年龄(h)", "覆盖"]]
    for row in matrix:
        matrix_rows.append([row["target_id"], row["hostname"], row["port"],
                            row["expected_certificate_id"] or "—", row["observed_certificate_id"] or "—",
                            row["age_hours"] if row["age_hours"] is not None else "—", row["coverage_status"]])
    head.append(md_table(matrix_rows))
    head.append("\n\n本技能只依据用户提供的观测，不联网探测、不购买、不签发、不部署证书。")
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
                          "message": "请对照 references/guide.md 检查带时区基准时间、certificate_id/target_id 唯一、"
                                     "证书 SAN 与目标 required_sans、观测时间合法性，以及是否混入私钥内容。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
