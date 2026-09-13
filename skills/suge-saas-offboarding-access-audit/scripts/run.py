#!/usr/bin/env python3
"""Offline SaaS offboarding access-revocation coverage audit.

Builds a subject -> system -> account -> action coverage graph from user-supplied,
already-redacted offboarding records.  Key semantics: disabling a login does NOT
prove that sessions or API tokens are dead; a successful action needs both a
timestamp and an evidence id; deleting an account before the asset/data handover
is a conflict; "account not found" is reported separately from "already revoked".

Strictly offline and read-only: it never opens a URL or path, never calls an
identity provider, never disables or deletes a real account, and never rewrites
history.
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
                     "secret_plaintext", "private_key", "private_key_pem", "api_token",
                     "access_token", "refresh_token", "bearer_token", "session_cookie",
                     "cookie", "cookies", "client_secret", "credential_value"}

ACTION_TYPES = ("disable_login", "revoke_sessions", "revoke_tokens", "remove_groups",
                "remove_roles", "remove_licenses", "transfer_assets", "archive_data",
                "delete_account", "verify_access_denied")
SESSION_SEMANTICS = {
    "stateless": (),
    "session_cookie": ("revoke_sessions",),
    "api_token": ("revoke_tokens",),
    "hybrid": ("revoke_sessions", "revoke_tokens"),
}
ACCOUNT_STATUSES = ("active", "disabled", "deleted", "not_found", "unknown")
ACTION_STATUSES = ("success", "failed", "pending")
RISK_LEVELS = ("low", "medium", "high")
HANDOVER_STATUSES = ("transferred", "pending", "missing", "not_required")

# Worst-first.  A concrete access residue outranks an incomplete record, and an
# incomplete record outranks a merely unfinished-but-clean offboarding.
STATUS_ORDER = ("INVALID", "ACCESS_RESIDUE", "ASSET_TRANSFER_BLOCKED", "SLA_BREACH",
                "PARTIAL", "IN_PROGRESS", "COMPLETE")


def privacy_gate(value):
    """Reject input that still contains a real-looking credential. Never echoes it."""
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in SECRET_VALUE_KEYS:
                raise ValueError(
                    "PRIVACY_GATE: 输入包含疑似未脱敏的凭据字段，已拒绝处理。"
                    "请只提供脱敏标识，不要提供密码、令牌、Cookie 或私钥原文。")
            privacy_gate(item)
    elif isinstance(value, list):
        for item in value:
            privacy_gate(item)
    elif isinstance(value, str):
        for pattern in RAW_SECRET_PATTERNS:
            if pattern.search(value):
                raise ValueError(
                    "PRIVACY_GATE: 输入包含疑似真实令牌/私钥内容，已拒绝处理，且不会回显该内容。"
                    "请先完成脱敏后重试。")
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


def nonneg(value, label, maximum=Decimal("1000000")):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(label + " must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(label + " must be numeric")
    if not result.is_finite() or not Decimal("0") <= result <= maximum:
        raise ValueError(label + " is outside the allowed range")
    return result


def quant(value, places=2):
    result = Decimal(value).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP)
    if result == 0:
        result = abs(result)
    return str(result)


def hours_between(start, end):
    if start is None or end is None:
        return None
    return Decimal(str((end - start).total_seconds())) / Decimal("3600")


def parse_sla(raw):
    if raw is None:
        return {"offboarding_hours": None}
    if not isinstance(raw, dict):
        raise ValueError("sla must be an object")
    value = raw.get("offboarding_hours")
    if value is None or str(value).strip() == "":
        return {"offboarding_hours": None}
    return {"offboarding_hours": nonneg(value, "sla.offboarding_hours", Decimal("8760"))}


def text_list(value, label, maximum=60, allowed=None):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(label + " must be a list")
    result = []
    for index, item in enumerate(value):
        text = clean_text(item, "%s[%d]" % (label, index), maximum)
        if allowed is not None and text not in allowed:
            raise ValueError(label + "[%d] must be one of " % index + ", ".join(allowed))
        if text not in result:
            result.append(text)
    return result


def analyze(data):
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    privacy_gate(data)
    as_of = parse_as_of(data.get("as_of"))
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    sla = parse_sla(data.get("sla"))

    departures_raw = data.get("departures")
    if not isinstance(departures_raw, list) or len(departures_raw) > 2000:
        raise ValueError("departures must be a list of up to 2000 items")
    if not departures_raw:
        result = {
            "as_of": as_of.isoformat(), "status": "INVALID", "subject_count": 0,
            "status_counts": {}, "subjects": [], "conflicts": [], "duplicate_actions": [],
            "orphan_accounts": [], "orphan_actions": [], "evidence_gaps": [],
            "sla_breaches": [], "system_declaration_gaps": [], "not_found_accounts": [],
            "note": "没有任何离职主体可审计，判定为 INVALID。本技能不打开 URL 或路径、"
                    "不调用任何身份供应商、不修改任何外部系统。",
        }
        result["markdown_summary"] = build_markdown(result)
        return result

    systems = []
    system_map = {}
    declaration_gaps = []
    seen_systems = set()
    for index, item in enumerate(data.get("systems") or []):
        label = "systems[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each system must be an object")
        system_id = clean_text(item.get("system_id"), label + ".system_id", 120)
        if system_id in seen_systems:
            raise ValueError("duplicate system_id: " + system_id)
        seen_systems.add(system_id)
        required = text_list(item.get("required_actions"), label + ".required_actions", 60, ACTION_TYPES)
        not_applicable = text_list(item.get("not_applicable_actions"),
                                   label + ".not_applicable_actions", 60, ACTION_TYPES)
        semantics_raw = optional_text(item.get("session_semantics"), label + ".session_semantics", 24)
        semantics = semantics_raw.lower() if semantics_raw else "stateless"
        if semantics not in SESSION_SEMANTICS:
            raise ValueError(label + ".session_semantics must be one of "
                             + ", ".join(sorted(SESSION_SEMANTICS)))
        derived = list(SESSION_SEMANTICS[semantics])
        undeclared = [action for action in derived if action not in required]
        if undeclared:
            declaration_gaps.append({
                "system_id": system_id, "session_semantics": semantics,
                "undeclared_actions": sorted(undeclared),
                "detail": "系统声明会话语义为 %s，但 required_actions 未包含 %s"
                          % (semantics, ", ".join(sorted(undeclared))),
            })
        overlap = sorted(set(required) & set(not_applicable))
        if overlap:
            declaration_gaps.append({
                "system_id": system_id, "session_semantics": semantics,
                "undeclared_actions": [], "detail": "同一动作同时被列为必需与不适用：" + ", ".join(overlap),
            })
        effective = sorted((set(required) | set(derived)) - set(not_applicable))
        record = {"system_id": system_id, "required_actions": sorted(set(required)),
                  "not_applicable_actions": sorted(set(not_applicable)),
                  "session_semantics": semantics, "effective_actions": effective}
        systems.append(record)
        system_map[system_id] = record

    subjects = []
    subject_map = {}
    seen_subjects = set()
    for index, item in enumerate(departures_raw):
        label = "departures[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each departure must be an object")
        subject_id = clean_text(item.get("subject_id"), label + ".subject_id", 120)
        if subject_id in seen_subjects:
            raise ValueError("duplicate subject_id: " + subject_id)
        seen_subjects.add(subject_id)
        risk_raw = optional_text(item.get("risk_level"), label + ".risk_level", 24)
        risk = risk_raw.lower() if risk_raw else None
        if risk is not None and risk not in RISK_LEVELS:
            raise ValueError(label + ".risk_level must be one of " + ", ".join(RISK_LEVELS))
        record = {
            "subject_id": subject_id,
            "last_working_at": parse_dt(item.get("last_working_at"), label + ".last_working_at"),
            "owner": optional_text(item.get("owner"), label + ".owner", 160),
            "risk_level": risk,
        }
        subjects.append(record)
        subject_map[subject_id] = record

    orphan_accounts = []
    accounts = []
    seen_accounts = set()
    for index, item in enumerate(data.get("accounts") or []):
        label = "accounts[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each account must be an object")
        account_id = clean_text(item.get("account_id"), label + ".account_id", 120)
        if account_id in seen_accounts:
            raise ValueError("duplicate account_id: " + account_id)
        seen_accounts.add(account_id)
        subject_id = clean_text(item.get("subject_id"), label + ".subject_id", 120)
        system_id = clean_text(item.get("system_id"), label + ".system_id", 120)
        if subject_id not in subject_map:
            orphan_accounts.append({"account_id": account_id, "subject_id": subject_id,
                                    "system_id": system_id, "code": "UNKNOWN_SUBJECT"})
            continue
        if system_id not in system_map:
            orphan_accounts.append({"account_id": account_id, "subject_id": subject_id,
                                    "system_id": system_id, "code": "UNKNOWN_SYSTEM"})
            continue
        status_raw = optional_text(item.get("status"), label + ".status", 24)
        status = status_raw.lower() if status_raw else "unknown"
        if status not in ACCOUNT_STATUSES:
            raise ValueError(label + ".status must be one of " + ", ".join(ACCOUNT_STATUSES))
        accounts.append({
            "account_id": account_id, "subject_id": subject_id, "system_id": system_id,
            "status": status,
            "roles": text_list(item.get("roles"), label + ".roles", 120),
            "licenses": text_list(item.get("licenses"), label + ".licenses", 120),
            "last_seen_at": parse_dt(item.get("last_seen_at"), label + ".last_seen_at", required=False),
        })
    account_map = {account["account_id"]: account for account in accounts}

    actions = []
    orphan_actions = []
    seen_actions = {}
    duplicate_actions = []
    for index, item in enumerate(data.get("actions") or []):
        label = "actions[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each action must be an object")
        action_id = clean_text(item.get("action_id"), label + ".action_id", 120)
        account_id = clean_text(item.get("account_id"), label + ".account_id", 120)
        if account_id not in account_map:
            orphan_actions.append({"action_id": action_id, "account_id": account_id,
                                   "code": "UNKNOWN_ACCOUNT"})
            continue
        action_type = clean_text(item.get("type"), label + ".type", 60).lower()
        if action_type not in ACTION_TYPES:
            raise ValueError(label + ".type must be one of " + ", ".join(ACTION_TYPES))
        status = clean_text(item.get("status"), label + ".status", 24).lower()
        if status not in ACTION_STATUSES:
            raise ValueError(label + ".status must be one of " + ", ".join(ACTION_STATUSES))
        occurred_at = parse_dt(item.get("occurred_at"), label + ".occurred_at")
        if occurred_at.tzinfo is None:
            raise ValueError(label + ".occurred_at must be timezone-aware")
        if occurred_at > as_of:
            raise ValueError(label + ".occurred_at must not be in the future relative to as_of")
        if action_id in seen_actions:
            seen_actions[action_id] += 1
            continue
        seen_actions[action_id] = 1
        actions.append({
            "action_id": action_id, "account_id": account_id, "type": action_type,
            "status": status, "occurred_at": occurred_at,
            "evidence_id": optional_text(item.get("evidence_id"), label + ".evidence_id", 200),
            "actor": optional_text(item.get("actor"), label + ".actor", 160),
        })
    for action_id in sorted(seen_actions):
        if seen_actions[action_id] > 1:
            duplicate_actions.append({
                "action_id": action_id,
                "account_id": next(a["account_id"] for a in actions if a["action_id"] == action_id),
                "occurrences": seen_actions[action_id],
            })

    assets = []
    orphan_assets = []
    seen_assets = set()
    for index, item in enumerate(data.get("assets") or []):
        label = "assets[%d]" % index
        if not isinstance(item, dict):
            raise ValueError("each asset must be an object")
        asset_id = clean_text(item.get("asset_id"), label + ".asset_id", 120)
        if asset_id in seen_assets:
            raise ValueError("duplicate asset_id: " + asset_id)
        seen_assets.add(asset_id)
        subject_id = clean_text(item.get("subject_id"), label + ".subject_id", 120)
        if subject_id not in subject_map:
            orphan_assets.append({"asset_id": asset_id, "subject_id": subject_id,
                                  "code": "UNKNOWN_SUBJECT"})
            continue
        handover_raw = optional_text(item.get("handover_status"), label + ".handover_status", 24)
        handover = handover_raw.lower() if handover_raw else "pending"
        if handover not in HANDOVER_STATUSES:
            raise ValueError(label + ".handover_status must be one of " + ", ".join(HANDOVER_STATUSES))
        assets.append({
            "asset_id": asset_id, "subject_id": subject_id,
            "asset_type": optional_text(item.get("asset_type"), label + ".asset_type", 120),
            "owner_after": optional_text(item.get("owner_after"), label + ".owner_after", 160),
            "handover_status": handover,
            "evidence_id": optional_text(item.get("evidence_id"), label + ".evidence_id", 200),
        })

    actions_by_account = {}
    for action in actions:
        actions_by_account.setdefault(action["account_id"], []).append(action)
    accounts_by_subject = {}
    for account in accounts:
        accounts_by_subject.setdefault(account["subject_id"], []).append(account)
    assets_by_subject = {}
    for asset in assets:
        assets_by_subject.setdefault(asset["subject_id"], []).append(asset)

    rows = []
    conflicts = []
    evidence_gaps = []
    sla_breaches = []
    not_found_accounts = []
    for subject in subjects:
        subject_id = subject["subject_id"]
        subject_accounts = sorted(accounts_by_subject.get(subject_id, []),
                                  key=lambda item: item["account_id"])
        subject_assets = sorted(assets_by_subject.get(subject_id, []),
                                key=lambda item: item["asset_id"])
        flags = set()
        missing_actions = []
        subject_conflicts = []
        if subject["owner"] is None:
            flags.add("OWNER_MISSING")
            missing_actions.append("指定离职流程负责人")
        if subject["risk_level"] is None:
            flags.add("RISK_LEVEL_MISSING")
            missing_actions.append("补录风险等级（低/中/高）")
        if not subject_accounts:
            flags.add("NO_ACCOUNTS")
            missing_actions.append("补录该主体在各系统中的账号，或确认确无账号")

        account_rows = []
        covered = 0
        residual = {"logins": [], "sessions": [], "tokens": [], "roles": [],
                    "licenses": [], "handover": [], "deletion": [], "verification": []}
        covered_at = None
        delete_done = False
        for account in subject_accounts:
            system = system_map[account["system_id"]]
            required = system["effective_actions"]
            account_actions = sorted(actions_by_account.get(account["account_id"], []),
                                     key=lambda item: (item["occurred_at"], item["action_id"]))
            satisfied = {}
            pending_types = []
            failed_types = []
            for action in account_actions:
                if action["status"] == "success":
                    if action["evidence_id"] is None:
                        flags.add("EVIDENCE_MISSING")
                        evidence_gaps.append({
                            "subject_id": subject_id, "account_id": account["account_id"],
                            "action_id": action["action_id"], "code": "SUCCESS_WITHOUT_EVIDENCE",
                            "detail": "动作 %s 状态为成功但缺少 evidence_id，不能计为已覆盖"
                                      % action["action_id"]})
                        continue
                    if action["type"] not in satisfied or action["occurred_at"] < satisfied[action["type"]]:
                        satisfied[action["type"]] = action["occurred_at"]
                elif action["status"] == "pending":
                    pending_types.append(action["type"])
                else:
                    failed_types.append(action["type"])
            account_missing = [action for action in required if action not in satisfied]
            account_state = "COVERED"
            if account["status"] == "not_found":
                account_state = "ACCOUNT_NOT_FOUND"
                account_missing = []
                flags.add("ACCOUNT_NOT_FOUND")
                not_found_accounts.append({
                    "subject_id": subject_id, "account_id": account["account_id"],
                    "system_id": account["system_id"]})
                missing_actions.append("确认账号 %s 是否从未创建（与已撤销区分）" % account["account_id"])
            elif account_missing:
                account_state = "ACCESS_RESIDUE"
                for action_type in account_missing:
                    if action_type == "disable_login":
                        residual["logins"].append(account["account_id"])
                    elif action_type == "revoke_sessions":
                        residual["sessions"].append(account["account_id"])
                    elif action_type == "revoke_tokens":
                        residual["tokens"].append(account["account_id"])
                    elif action_type == "remove_roles":
                        residual["roles"].append(account["account_id"])
                    elif action_type == "remove_licenses":
                        residual["licenses"].append(account["account_id"])
                    elif action_type in ("transfer_assets", "archive_data"):
                        residual["handover"].append(account["account_id"])
                    elif action_type == "delete_account":
                        residual["deletion"].append(account["account_id"])
                    elif action_type == "verify_access_denied":
                        residual["verification"].append(account["account_id"])
                missing_actions.append("账号 %s 缺少动作：%s"
                                       % (account["account_id"], ", ".join(account_missing)))
            else:
                covered += 1
                latest = max(satisfied.values())
                if covered_at is None or latest > covered_at:
                    covered_at = latest
            if account["status"] != "not_found":
                if "delete_account" in satisfied and account["status"] != "deleted":
                    flags.add("DELETE_ACTION_NOT_REFLECTED")
                    missing_actions.append("账号 %s 记录了删除动作但状态仍为 %s"
                                           % (account["account_id"], account["status"]))
                if "delete_account" in satisfied:
                    delete_done = True
            if account["status"] != "not_found" and account["status"] != "deleted":
                if account["status"] == "active" and "disable_login" in satisfied:
                    flags.add("ACCOUNT_STILL_ACTIVE")
                if account["status"] == "active" and account["last_seen_at"] is not None \
                        and account["last_seen_at"] > subject["last_working_at"]:
                    flags.add("POST_DEPARTURE_ACTIVITY")
                    missing_actions.append("账号 %s 在离职生效后仍有活动记录，需核实"
                                           % account["account_id"])
            account_rows.append({
                "account_id": account["account_id"], "system_id": account["system_id"],
                "status": account["status"], "state": account_state,
                "required_actions": required,
                "satisfied_actions": sorted(satisfied),
                "missing_actions": account_missing,
                "pending_actions": sorted(set(pending_types)),
                "failed_actions": sorted(set(failed_types)),
                "roles_remaining": account["roles"], "licenses_remaining": account["licenses"],
                "last_seen_at": account["last_seen_at"].isoformat() if account["last_seen_at"] else None,
                "action_ids": [action["action_id"] for action in account_actions],
            })

        handover_pending = [asset["asset_id"] for asset in subject_assets
                            if asset["handover_status"] in ("pending", "missing")]
        for asset in subject_assets:
            if asset["handover_status"] in ("pending", "missing") and asset["evidence_id"] is None:
                flags.add("HANDOVER_EVIDENCE_MISSING")
                evidence_gaps.append({
                    "subject_id": subject_id, "account_id": None, "action_id": None,
                    "code": "HANDOVER_EVIDENCE_MISSING",
                    "detail": "资产 %s 尚未交接且缺少证据编号" % asset["asset_id"]})
        if handover_pending:
            flags.add("HANDOVER_PENDING")
            missing_actions.append("完成资产/数据交接：" + ", ".join(handover_pending))

        for action in actions:
            if action["account_id"] not in {a["account_id"] for a in subject_accounts}:
                continue
            if action["occurred_at"] < subject["last_working_at"]:
                code = "EARLY_ACTION_BEFORE_DEPARTURE"
                if code not in flags:
                    flags.add(code)
                    subject_conflicts.append({
                        "code": code,
                        "detail": "动作 %s 发生时间早于离职生效时间" % action["action_id"]})
                    conflicts.append({"subject_id": subject_id, "code": code,
                                      "detail": "动作 %s 发生时间早于离职生效时间" % action["action_id"]})
                break

        blocked = bool(handover_pending) and (delete_done or any(
            account["status"] == "deleted" for account in subject_accounts))
        if blocked:
            flags.add("DELETE_BEFORE_HANDOVER")
            detail = "账号已删除但资产/数据交接未完成：" + ", ".join(handover_pending)
            subject_conflicts.append({"code": "DELETE_BEFORE_HANDOVER", "detail": detail})
            conflicts.append({"subject_id": subject_id, "code": "DELETE_BEFORE_HANDOVER",
                              "detail": detail})
            missing_actions.append("在删除账号前先完成资产/数据交接")

        residual_total = sum(len(values) for values in residual.values())
        if residual_total:
            flags.add("ACCESS_RESIDUE")
        fully_covered = bool(subject_accounts) and covered == len(subject_accounts) and not residual_total
        elapsed = hours_between(subject["last_working_at"],
                                covered_at if covered_at is not None else as_of)
        breached = (sla["offboarding_hours"] is not None and elapsed is not None
                    and elapsed > sla["offboarding_hours"])
        if breached:
            flags.add("SLA_EXCEEDED")
            sla_breaches.append({
                "subject_id": subject_id,
                "detail": "离职生效%s %s 小时，超过 SLA %s 小时"
                          % ("至完成撤销用时" if covered_at is not None else "至今已过",
                             quant(elapsed), quant(sla["offboarding_hours"]))})
            missing_actions.append("SLA 已超时，需说明原因并复核流程时效")

        partial = bool({"OWNER_MISSING", "RISK_LEVEL_MISSING", "NO_ACCOUNTS"} & flags)
        if partial:
            status = "PARTIAL"
        elif blocked:
            status = "ASSET_TRANSFER_BLOCKED"
        elif residual_total:
            status = "ACCESS_RESIDUE"
        elif breached:
            status = "SLA_BREACH"
        elif fully_covered:
            status = "COMPLETE"
        else:
            status = "IN_PROGRESS"

        coverage_pct = (Decimal(covered) * 100 / Decimal(len(subject_accounts))
                        if subject_accounts else None)
        rows.append({
            "subject_id": subject_id, "owner": subject["owner"],
            "risk_level": subject["risk_level"],
            "last_working_at": subject["last_working_at"].isoformat(),
            "status": status,
            "review_flags": sorted(flags),
            "coverage": {"total_accounts": len(subject_accounts), "covered_accounts": covered,
                         "coverage_pct": None if coverage_pct is None else quant(coverage_pct) + "%"},
            "accounts": account_rows,
            "assets": [{"asset_id": asset["asset_id"], "asset_type": asset["asset_type"],
                        "owner_after": asset["owner_after"],
                        "handover_status": asset["handover_status"],
                        "evidence_id": asset["evidence_id"]} for asset in subject_assets],
            "handover_blocked": bool(handover_pending),
            "handover_pending_assets": handover_pending,
            "residual": {key: sorted(set(values)) for key, values in residual.items()},
            "residual_total": residual_total,
            "elapsed_hours": None if elapsed is None else quant(elapsed),
            "sla_hours_limit": None if sla["offboarding_hours"] is None
                               else quant(sla["offboarding_hours"]),
            "sla_exceeded": breached,
            "conflicts": subject_conflicts,
            "missing_actions": sorted(set(missing_actions)),
            "next_actions": sorted(set(missing_actions)) or ["无需补充动作；保留证据以备复核"],
        })

    rows.sort(key=lambda item: item["subject_id"])
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    overall = min((row["status"] for row in rows), key=lambda name: STATUS_ORDER.index(name))
    if orphan_accounts or orphan_actions or orphan_assets:
        overall = min(overall, "PARTIAL", key=lambda name: STATUS_ORDER.index(name))

    result = {
        "as_of": as_of.isoformat(),
        "status": overall,
        "subject_count": len(rows),
        "status_counts": counts,
        "subjects": rows,
        "system_declaration_gaps": sorted(declaration_gaps,
                                          key=lambda item: (item["system_id"], item["detail"])),
        "systems": systems,
        "conflicts": sorted(conflicts, key=lambda item: (item["subject_id"], item["code"])),
        "duplicate_actions": duplicate_actions,
        "orphan_accounts": sorted(orphan_accounts, key=lambda item: item["account_id"]),
        "orphan_actions": sorted(orphan_actions, key=lambda item: item["action_id"]),
        "orphan_assets": sorted(orphan_assets, key=lambda item: item["asset_id"]),
        "evidence_gaps": sorted(evidence_gaps,
                                key=lambda item: (item["subject_id"], item["code"],
                                                  item["action_id"] or "")),
        "sla_breaches": sorted(sla_breaches, key=lambda item: item["subject_id"]),
        "not_found_accounts": sorted(not_found_accounts,
                                     key=lambda item: (item["subject_id"], item["account_id"])),
        "note": "只审计用户提供的脱敏离职记录。禁用登录不等于会话或 API 令牌已失效；"
                "成功动作必须同时具备时间与 evidence_id；删除账号前必须先完成资产/数据交接；"
                "账号未发现与已撤销分开报告。本技能不打开 URL 或路径、不调用任何身份供应商、"
                "不修改任何外部系统。",
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
    lines = ["# SaaS 离职账号与访问撤销覆盖审计\n\n"]
    lines.append("基准时间 %s，共 %d 个离职主体。**总体判定：%s**。\n\n"
                 % (result["as_of"], result["subject_count"], result["status"]))
    if not result["subjects"]:
        lines.append("没有任何离职主体可审计。\n")
        lines.append("\n本技能不打开 URL 或路径、不调用任何身份供应商、不修改任何外部系统。")
        return "".join(lines)
    lines.append("状态分布：" + ", ".join("%s=%d" % (key, value)
                                          for key, value in sorted(result["status_counts"].items()))
                 + "。\n\n")
    rows = [["主体", "风险", "状态", "覆盖率", "残留项", "交接", "SLA"]]
    for row in result["subjects"]:
        rows.append([row["subject_id"], row["risk_level"] or "—", row["status"],
                     row["coverage"]["coverage_pct"] or "—", row["residual_total"],
                     "阻塞" if row["handover_blocked"] else "正常",
                     "超时" if row["sla_exceeded"] else "—"])
    lines.append(md_table(rows))
    lines.append("\n\n")
    for row in result["subjects"]:
        lines.append("## %s（%s）\n\n" % (row["subject_id"], row["status"]))
        lines.append("- 负责人 %s / 风险 %s / 离职生效 %s\n"
                     % (row["owner"] or "—", row["risk_level"] or "—", row["last_working_at"]))
        lines.append("- 覆盖 %d/%d 个账号（%s）；残留项合计 %d\n"
                     % (row["coverage"]["covered_accounts"], row["coverage"]["total_accounts"],
                        row["coverage"]["coverage_pct"] or "—", row["residual_total"]))
        residual = row["residual"]
        lines.append("- 残留：登录 %s / 会话 %s / 令牌 %s / 角色 %s / 许可 %s / 删除 %s / 验证 %s\n"
                     % (", ".join(residual["logins"]) or "无", ", ".join(residual["sessions"]) or "无",
                        ", ".join(residual["tokens"]) or "无", ", ".join(residual["roles"]) or "无",
                        ", ".join(residual["licenses"]) or "无", ", ".join(residual["deletion"]) or "无",
                        ", ".join(residual["verification"]) or "无"))
        if row["review_flags"]:
            lines.append("- 标记：" + ", ".join(row["review_flags"]) + "\n")
        if row["missing_actions"]:
            lines.append("- 待办：\n")
            for action in row["missing_actions"]:
                lines.append("  - " + action + "\n")
        lines.append("\n")
    if result["system_declaration_gaps"]:
        lines.append("## 系统声明缺口\n\n")
        for gap in result["system_declaration_gaps"]:
            lines.append("- %s：%s\n" % (gap["system_id"], gap["detail"]))
        lines.append("\n")
    lines.append("禁用登录不等于会话或 API 令牌已失效；成功动作必须同时具备时间与 evidence_id；"
                 "删除账号前必须先完成资产/数据交接。本技能不打开 URL 或路径、不调用任何身份供应商、"
                 "不修改任何外部系统。")
    return "".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="UTF-8 JSON file; maximum 4 MB")
    args = parser.parse_args()
    try:
        path = Path(args.input)
        with path.open("rb") as handle:
            raw = handle.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("input exceeds 4 MB")
        data = json.loads(raw.decode("utf-8-sig"))
        print(json.dumps(analyze(data), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, InvalidOperation):
        print(json.dumps({"error": "invalid_input",
                          "message": "请对照 references/guide.md 检查：输入必须已脱敏（不得含密码、"
                                     "令牌、Cookie 或私钥原文）、as_of 带时区、subject_id/account_id/"
                                     "action_id/system_id/asset_id 唯一、动作类型与状态在允许集合内、"
                                     "动作时间不晚于 as_of。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
