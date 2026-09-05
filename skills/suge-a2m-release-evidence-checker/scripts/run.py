#!/usr/bin/env python3
"""Offline A2M (402->proof->delivery) release evidence checker. No network, no payment."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ALLOWED_ENVS = {"local": "LOCAL_PASS", "sandbox": "SANDBOX_PASS", "staging": "SANDBOX_PASS",
                "test": "SANDBOX_PASS", "production": "PROD_PASS", "prod": "PROD_PASS", "live": "PROD_PASS"}
FULFILLED = {"confirmed", "delivered", "success", "fulfilled", "completed"}
MONEY_KEYS = ("amount",)
KEY_KEYS = ("resource_id", "trade_no", "order_id")
CURRENCY_KEYS = ("currency",)


def token_hash(value):
    if value is None:
        return None
    text = str(value)
    return text[:6] + "…" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def text_field(value, label, maximum=200):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(label + " must be short nonempty text")
    return value.strip()


def int_field(value, label):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(label + " must be an integer status")
    try:
        result = int(str(value))
    except (TypeError, ValueError):
        raise ValueError(label + " must be an integer status")
    return result


def bool_field(value, label):
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(label + " must be boolean")
    return value


def pick(d, *keys):
    """Read a whitelisted scalar from a dict; unknown keys are ignored entirely."""
    if not isinstance(d, dict):
        return None
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return None


class Ev:
    def __init__(self, rid):
        self.rid = rid

    def fail(self, check, detail):
        return {"check": check, "status": "FAIL", "record": self.rid, "detail": detail}

    def pass_(self, check, detail):
        return {"check": check, "status": "PASS", "record": self.rid, "detail": detail}


def extract_record(rec):
    """Pull only whitelisted fields from one exchange record. Anything else (e.g. an
    instruction smuggled into a body) is data and ignored."""
    if not isinstance(rec, dict):
        raise ValueError("each record must be an object")
    rid = text_field(rec.get("id"), "record.id") or "rec?"
    phase = text_field(rec.get("phase"), "record.phase")
    allowed_phases = {"payment_request", "payment_validation", "delivery", "retry_with_proof", "refund"}
    if phase not in allowed_phases:
        raise ValueError("record.phase must be one of " + ",".join(sorted(allowed_phases)))
    req = rec.get("request")
    resp = rec.get("response")
    if not isinstance(req, dict) or not isinstance(resp, dict):
        raise ValueError("record needs request and response objects")
    method = (text_field(req.get("method"), "method") or "GET").upper()
    url_path = text_field(req.get("url_path"), "url_path") or ""
    proof_present = bool_field(req.get("proof_present"), "proof_present")
    if proof_present is None:
        proof_present = phase in {"retry_with_proof", "delivery"}
    proof_token = token_hash(req.get("proof_token"))
    status = int_field(resp.get("status"), "response.status")
    payment_required = resp.get("payment_required") if isinstance(resp.get("payment_required"), dict) else None
    validation = resp.get("validation") if isinstance(resp.get("validation"), dict) else None
    delivery = resp.get("delivery") if isinstance(resp.get("delivery"), dict) else None
    refund = resp.get("refund") if isinstance(resp.get("refund"), dict) else None
    error_text = text_field(resp.get("error"), "error")
    return {"id": rid, "phase": phase, "method": method, "url_path": url_path,
            "proof_present": bool(proof_present), "proof_token": proof_token,
            "status": status, "payment_required": payment_required,
            "validation": validation, "delivery": delivery, "refund": refund, "error": error_text}


def analyze(data):
    if not isinstance(data, dict) or "records" not in data:
        raise ValueError("input must contain a records array")
    raw_records = data["records"]
    if not isinstance(raw_records, list) or not 1 <= len(raw_records) <= 200:
        raise ValueError("records must be a list of 1-200 exchanges")
    env = text_field(data.get("environment_label"), "environment_label")
    if env:
        env = env.lower()
    claims = data.get("claims") if isinstance(data.get("claims"), dict) else {}

    records = [extract_record(rec) for rec in raw_records]
    checks = []

    # --- 1) initial 402 gate ------------------------------------------------
    gate = next((r for r in records if r["status"] == 402 and r["payment_required"]), None)
    if gate is None:
        checks.append(Ev("-").fail("initial_402",
                                   "未找到返回 402 且带 payment_required 数据的记录；无法证明按量付费入口生效。"))
    else:
        pr = gate["payment_required"]
        provider = text_field(pick(pr, "provider", "network"), "provider")
        proof_url = text_field(pick(pr, "proof_url", "url", "payment_url"), "proof_url")
        expires = text_field(pick(pr, "expires_at", "expires"), "expires_at")
        detail = "402 命中（" + gate["id"] + "）"
        if provider:
            detail += "；provider=" + provider
        if expires:
            detail += "；expires_at=" + expires
        checks.append(Ev(gate["id"]).pass_("initial_402", detail))
        if not proof_url and not gate["proof_token"] and not gate["proof_present"]:
            checks.append(Ev(gate["id"]).fail("proof_present", "402 响应缺少可提取的证明地址/证明标识。"))
        else:
            checks.append(Ev(gate["id"]).pass_("proof_present", "402 响应含证明凭据（地址或标识，仅记录 hash）。"))

    # --- 2) retry with proof -> 200 delivery --------------------------------
    retries = [r for r in records if r["phase"] in {"retry_with_proof", "delivery"} and r["proof_present"]]
    delivered = [r for r in records
                 if r["delivery"] and (r["phase"] == "delivery" or (r["phase"] == "retry_with_proof" and r["status"] == 200))]
    carried = [r for r in records if r["phase"] == "payment_request" and r["proof_present"] and r["status"] == 200]
    proof_carried = retries or carried or delivered
    if not proof_carried:
        checks.append(Ev("-").fail("retry_with_proof", "未发现携带证明重试并得到 200 的记录。"))
    else:
        r = proof_carried[0]
        checks.append(Ev(r["id"]).pass_("retry_with_proof",
                                        "找到携带证明的请求（" + r["id"] + "），响应状态 " + str(r["status"]) + "。"))
    if not delivered:
        checks.append(Ev("-").fail("delivery_200", "缺少带 200 与交付数据的记录（delivery 阶段）。"))
    else:
        d = delivered[0]
        dd = d["delivery"]
        fulfilled = bool_field(pick(dd, "delivered", "fulfilled"), "fulfilled") is True or \
            str(pick(dd, "fulfillment", "state", "status") or "").lower() in FULFILLED
        checks.append(Ev(d["id"]).pass_("delivery_200", "交付响应 200 命中（" + d["id"] + "）。"))
        if not fulfilled:
            checks.append(Ev(d["id"]).fail("fulfillment_confirmed", "交付数据未确认履约（delivered/fulfillment 缺失或非确认态）。"))
        else:
            checks.append(Ev(d["id"]).pass_("fulfillment_confirmed", "履约已确认。"))

    # --- 3) payment validation ----------------------------------------------
    valids = [r for r in records if r["phase"] == "payment_validation" and r["validation"]]
    if not valids:
        checks.append(Ev("-").fail("payment_validation", "缺少支付验证证据（validation 阶段）。"))
    else:
        v = valids[0]
        vv = v["validation"]
        ok = bool_field(pick(vv, "valid", "ok", "verified"), "valid")
        if ok is not True:
            checks.append(Ev(v["id"]).fail("payment_validation", "验证响应存在但 valid/ok 不为 true。"))
        else:
            checks.append(Ev(v["id"]).pass_("payment_validation", "支付验证通过。"))

    # --- 4) settlement consistency across responses --------------------------
    consistency_sources = []
    for r in records:
        for section in (r.get("payment_required"), r.get("validation"), r.get("delivery")):
            if not section:
                continue
            entry = {}
            amount = pick(section, "amount", "total", "price")
            currency = pick(section, "currency", "asset")
            resource = pick(section, "resource_id", "resource", "order_id")
            trade = pick(section, "trade_no", "transaction_id", "payment_id")
            entry["amount"] = text_field(amount, "amount")
            entry["currency"] = text_field(currency, "currency")
            entry["resource_id"] = text_field(resource, "resource_id")
            entry["trade_no"] = text_field(trade, "trade_no")
            if any(v is not None for v in entry.values()):
                entry["_from"] = r["id"]
                consistency_sources.append(entry)
    conflicts = []
    for key in ("amount", "currency", "resource_id", "trade_no"):
        values = [s[key] for s in consistency_sources if s[key] is not None]
        unique = sorted(set(values))
        if len(unique) > 1:
            conflicts.append({"field": key, "values": unique, "sources": [s["_from"] for s in consistency_sources if s[key] is not None]})
    if conflicts:
        for c in conflicts:
            checks.append(Ev("-").fail("settlement_consistent", "跨响应不一致：" + c["field"] + "=" +
                                       ",".join(str(x) for x in c["values"]) + "（" + "/".join(c["sources"]) + "）"))
    else:
        checks.append(Ev("-").pass_("settlement_consistent", "金额/币种/资源号/交易号跨响应一致（可比对字段数为 " +
                                    str(len(consistency_sources)) + "）。"))

    # --- 5) replay / idempotency --------------------------------------------
    by_proof = {}
    for r in records:
        if r["proof_token"]:
            res = None
            for section in (r.get("delivery"), r.get("validation"), r.get("payment_required")):
                res = pick(section, "resource_id", "order_id") if section else None
                if res:
                    break
            by_proof.setdefault(r["proof_token"], set()).add((r["id"], str(res)))
    replay_fail = []
    idem = 0
    for proof, occurrences in by_proof.items():
        resources = {res for _, res in occurrences if res and res != "None"}
        if len(resources) > 1:
            replay_fail.append(proof)
        elif len(occurrences) > 1:
            idem += 1
    if replay_fail:
        checks.append(Ev("-").fail("replay_guard", "相同证明出现在多个不同资源/订单（" +
                                   ",".join(replay_fail[:3]) + "），判定为重放风险。"))
    else:
        checks.append(Ev("-").pass_("replay_guard", "未发现相同证明跨资源复用。"))
        if idem:
            checks.append({"check": "idempotency", "status": "INFO", "record": "-",
                           "detail": "相同证明对同一订单重复出现 " + str(idem) + " 次，依赖服务端幂等；如无幂等保护应标记缺陷。"})

    # --- verdict & level -----------------------------------------------------
    fails = [c for c in checks if c["status"] == "FAIL"]
    chain_keys = {"initial_402", "proof_present", "retry_with_proof", "delivery_200",
                  "payment_validation", "fulfillment_confirmed"}
    chain_missing = [c["check"] for c in checks if c["check"] in chain_keys and c["status"] != "PASS"]
    if fails and any(c["check"] == "settlement_consistent" or c["check"] == "replay_guard" for c in fails):
        verdict, level = "FAIL", "PROD_NOT_PROVEN"
    elif chain_missing:
        verdict, level = "BLOCKED", "PROD_NOT_PROVEN"
    else:
        base = ALLOWED_ENVS.get(env, "PROD_NOT_PROVEN") if env else "PROD_NOT_PROVEN"
        if env and env in ALLOWED_ENVS:
            verdict, level = "PASS", base
        else:
            verdict, level = "UNVERIFIED_ENV", "PROD_NOT_PROVEN"
    refund_evidence = any(r.get("phase") == "refund" for r in records)
    return {
        "skill": "suge-a2m-release-evidence-checker",
        "version": "1.0.0",
        "environment_label": env or None,
        "verdict": verdict,
        "level": level,
        "claims_echo": claims,
        "checks": checks,
        "missing_steps": chain_missing,
        "refund_evidence_present": refund_evidence,
        "record_hashes": [{"id": r["id"], "phase": r["phase"], "status": r["status"],
                           "proof_sha256": r["proof_token"]} for r in records],
        "note": ("离线证据分析：未发起网络请求、未发起支付。依据所提供记录仅能达到对应 level；"
                 "production 之外的通过不代表生产已上线，未验证的跳数不默认为成功。")
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="UTF-8 JSON evidence file; maximum 2 MB")
    args = parser.parse_args()
    try:
        path = Path(args.input)
        raw = path.read_bytes() if path.exists() else open(args.input, "rb").read()
        if len(raw) > 2_000_000:
            raise ValueError("input exceeds 2 MB")
        data = json.loads(raw.decode("utf-8-sig"))
        print(json.dumps(analyze(data), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
        print(json.dumps({"error": "invalid_input",
                          "message": "请对照 references/guide.md 提供脱敏交换记录（records[] 与可选 environment_label/claims）。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
