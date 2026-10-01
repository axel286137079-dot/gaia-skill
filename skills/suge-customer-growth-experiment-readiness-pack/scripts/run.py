#!/usr/bin/env python3
"""客户增长实验准备包 — offline customer-growth experiment readiness builder.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes, no ad spend, no outbound contact and no CRM write-back: evidence
references are handled as file *basenames*.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = "1.0.0"

# --------------------------------------------------------------------------
# fixed vocabulary (documented in references/guide.md)
# --------------------------------------------------------------------------
# A channel must come from this list. An unrecognised value is a structural
# error, not an unknown, because it cannot be mapped onto any exact behaviour.
CHANNELS = ("content", "email", "sms", "paid_ads", "social", "referral",
            "in_store", "phone", "event", "other")

EXPERIMENT_STATES = ("READY", "ACTION_NEEDED", "INSUFFICIENT_EVIDENCE",
                     "BLOCKED")

BLOCKER_ORDER = ("INVALID_EXPERIMENT_RECORD", "INVALID_CHANNEL",
                 "INVALID_EXPERIMENT_WINDOW", "NEGATIVE_BUDGET",
                 "TARGET_NOT_ABOVE_BASELINE")

UNKNOWN_ORDER = ("NO_GOAL", "NO_AUDIENCE", "NO_OFFER", "NO_BASELINE",
                 "INVALID_BASELINE", "NO_TARGET", "INVALID_TARGET",
                 "UNKNOWN_METRIC", "UNKNOWN_CHANNEL", "UNKNOWN_OWNER",
                 "NO_WINDOW", "INVALID_WINDOW", "NO_BUDGET",
                 "UNKNOWN_BUDGET_CURRENCY", "UNKNOWN_OUTREACH_REQUIREMENT")

PREP_ORDER = ("NO_STOP_CONDITIONS", "NO_CONSENT_BASIS", "WINDOW_IN_PAST",
              "METRIC_DEFINITION_MISSING")

PLACEHOLDER = "已隐藏疑似提示注入文本"

CRED_KEY = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|auth[_-]?token|passphrase)",
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

# Injection is flagged only when an action verb and an instruction word appear in
# the SAME sentence, so ordinary experiment notes are not mislabelled.
INJ_ACTION = (
    "忽略", "无视", "跳过", "覆盖", "改写", "删除", "执行", "服从", "绕过",
    "ignore", "disregard", "override", "bypass", "forget",
)
INJ_TARGET = (
    "指令", "规则", "提示", "系统", "要求", "约束",
    "instruction", "rule", "prompt", "system", "constraint",
)

MD_ESCAPE = "\\`*_{}[]()#+-|<>~!"

DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$")

BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

DISCLAIMER = (
    "本输出是客户增长实验的人工准备材料，不是增长预测、投放建议或财务结论；"
    "所有结论只由输入中明示的事实推导，任何外发、投放与花费都必须由人工另行决定并自行取得同意。"
)

HUMAN_CONFIRM_BASE = (
    "实验是否值得做、是否算成功，由经营者人工判断，本工具不下结论",
    "对外触达与投放必须由人工执行，并自行确认已取得同意（本工具不发送、不投放）",
    "预算与花费必须由人工决定和支付（本工具不花钱、不下单、不绑定广告账号）",
    "基线数字只按输入登记值使用，本工具不核实、不推算、不补齐基线",
)

NO_SEND_DECLARATION = (
    "本工具不会联系任何客户、不会发送任何消息、不会创建任何广告或投放，"
    "也不会写入任何 CRM、表格或外部系统；它只输出一份准备材料。"
)

NO_SPEND_DECLARATION = (
    "本工具不会花钱、不会下单、不会绑定或修改任何广告账号与预算；"
    "预算上限只作为输入事实被登记，是否支出由人工决定。"
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    """Collapse control characters and whitespace; never raises."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = "".join(
        ch for ch in text
        if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C"
    )
    return re.sub(r"\s+", " ", text).strip()


def esc(value):
    """Escape Markdown structure characters so untrusted text cannot forge layout."""
    text = clean_text(value)
    return "".join("\\" + ch if ch in MD_ESCAPE else ch for ch in text)


def has_text(value):
    return isinstance(value, str) and value.strip() != ""


def parse_dt(value):
    """Parse an ISO timestamp that MUST carry an explicit UTC offset."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not DT_RE.match(text):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def read_bool(value):
    """Tri-state boolean: True / False / None (unknown)."""
    if isinstance(value, bool):
        return value
    return None


def read_decimal(value, allow_negative=False):
    """Optional decimal; None means "not provided", never 0."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str)):
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None
        if parsed.is_nan() or parsed.is_infinite():
            return None
        if parsed < 0 and not allow_negative:
            return None
        return parsed
    return None


def money(value):
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def injection_hit(value):
    if not isinstance(value, str) or not value:
        return False
    for sentence in re.split(r"[。！？!?;\n]", value):
        probe = sentence.lower()
        if any(a in probe for a in INJ_ACTION) and any(t in probe for t in INJ_TARGET):
            return True
    return False


def hidden(value):
    """Render a free-text value for Markdown; injection hits become a placeholder."""
    if injection_hit(value):
        return PLACEHOLDER
    return esc(value)


def safe_basename(raw):
    """Return (ok, safe_name, reason). The raw reference is never returned."""
    if not has_text(raw):
        return False, None, "EMPTY"
    text = clean_text(raw)
    if URL_RE.match(text):
        return False, None, "URL_REFERENCE"
    if "\\" in text or "/" in text:
        tail = re.split(r"[\\/]+", text)[-1]
        if tail in ("", ".", ".."):
            return False, None, "PATH_REFERENCE"
        return False, clean_text(tail), "PATH_REFERENCE"
    if text in (".", "..") or text.startswith("~"):
        return False, None, "PATH_REFERENCE"
    if re.match(r"^[A-Za-z]:", text):
        return False, clean_text(re.split(r"[:/\\]+", text)[-1]), "PATH_REFERENCE"
    if not BASENAME_RE.match(text):
        return False, None, "ILLEGAL_CHARACTERS"
    return True, text, None


def find_credentials(node, path="", hits=None):
    """Collect credential-shaped key names and values, with a readable path."""
    if hits is None:
        hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + "/" + str(key) if path else str(key)
            if CRED_KEY.search(str(key)):
                hits.append({"path": child, "reason": "CREDENTIAL_FIELD_NAME"})
                continue
            find_credentials(value, child, hits)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = (path + "[%d]") % index if path else "[%d]" % index
            find_credentials(value, child, hits)
    elif isinstance(node, str):
        for pattern in CRED_VALUE:
            if pattern.search(node):
                hits.append({"path": path or "/", "reason": "CREDENTIAL_VALUE_SHAPE"})
                break
    return hits


def find_injections(node, path="", hits=None):
    """Collect free-text leaves that look like a prompt injection."""
    if hits is None:
        hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + "/" + str(key) if path else str(key)
            find_injections(value, child, hits)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = (path + "[%d]") % index if path else "[%d]" % index
            find_injections(value, child, hits)
    elif isinstance(node, str) and injection_hit(node):
        hits.append({"path": path or "/", "marker": "PROMPT_INJECTION"})
    return hits


def tri(value):
    return "是" if value is True else ("否" if value is False else "未知")


# --------------------------------------------------------------------------
# empty / rejected envelopes
# --------------------------------------------------------------------------
def reject(hits):
    return {
        "version": VERSION,
        "status": "REJECTED",
        "reason": "CREDENTIAL_DETECTED",
        "credential_findings": [{"path": h["path"], "reason": h["reason"]} for h in hits],
        "as_of": None,
        "experiment_count": 0,
        "status_counts": {name: 0 for name in EXPERIMENT_STATES},
        "experiments": [],
        "experiment_cards": [],
        "ready_board": [],
        "action_needed_board": [],
        "blocked_board": [],
        "insufficient_board": [],
        "missing_facts": [],
        "budgets_by_currency": {},
        "budget_currency_note": "币种不做换算，也不跨币种合计。",
        "outreach_plan": [],
        "do_not_outreach": [],
        "human_confirm_items": [],
        "clarification_questions": [],
        "refused_refs": [],
        "injection_flagged": [],
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "no_send_declaration": NO_SEND_DECLARATION,
        "no_spend_declaration": NO_SPEND_DECLARATION,
        "markdown_summary": (
            "# 客户增长实验准备包\n\n"
            "- 状态：**REJECTED**\n"
            "- 原因：输入疑似包含凭据（密钥 / 令牌 / 密码）。\n"
            "- 处理：已拒绝处理，**未回显**任何疑似凭据内容。\n\n"
            "> 请移除凭据字段后重新提交。本工具不需要也不需要保存任何登录凭据。\n"
        ),
        "disclaimer": DISCLAIMER,
    }


def envelope(status, as_of_raw, injections, warnings):
    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "experiment_count": 0,
        "status_counts": {name: 0 for name in EXPERIMENT_STATES},
        "experiments": [],
        "experiment_cards": [],
        "ready_board": [],
        "action_needed_board": [],
        "blocked_board": [],
        "insufficient_board": [],
        "missing_facts": [],
        "budgets_by_currency": {},
        "budget_currency_note": "币种不做换算，也不跨币种合计。",
        "outreach_plan": [],
        "do_not_outreach": [],
        "human_confirm_items": [],
        "clarification_questions": [],
        "refused_refs": [],
        "injection_flagged": list(injections),
        "input_warnings": list(warnings),
        "no_send_declaration": NO_SEND_DECLARATION,
        "no_spend_declaration": NO_SPEND_DECLARATION,
        "markdown_summary": (
            "# 客户增长实验准备包\n\n"
            "- 状态：**%s**\n"
            "- 原因：%s\n\n"
            "> 请补齐必填输入后重新提交。\n" % (status, "; ".join(warnings) or "输入不完整")
        ),
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# record normalisation
# --------------------------------------------------------------------------
def normalise_experiment(raw, index):
    path = "experiments[%d]" % index
    if not isinstance(raw, dict):
        return {
            "experiment_id": None, "index": index, "path": path, "invalid": True,
            "goal": "", "audience": "", "offer": "", "channel": None,
            "channel_state": "NOT_PROVIDED",
            "baseline": None, "baseline_state": "NOT_PROVIDED",
            "target": None, "target_state": "NOT_PROVIDED",
            "metric_name": "", "metric_definition": "",
            "metric_definition_provided": False,
            "budget": {"amount": None, "currency": None, "amount_provided": False,
                       "currency_provided": False, "raw": None},
            "window": {"start_at": None, "end_at": None, "well_formed": False,
                       "start_state": "NOT_PROVIDED", "end_state": "NOT_PROVIDED",
                       "duration_days": None},
            "window_in_past": False,
            "owner": None, "outreach_required": None, "consent_basis": "",
            "stop_conditions": [], "evidence_refs": [], "refused_refs": [],
            "notes": "",
            "state": "BLOCKED", "blockers": ["INVALID_EXPERIMENT_RECORD"],
            "unknowns": [], "review_flags": [], "reasons": [],
        }

    experiment_id = clean_text(raw.get("experiment_id")) or None

    channel_raw = clean_text(raw.get("channel")).lower()
    if not channel_raw:
        channel_state = "NOT_PROVIDED"
    elif channel_raw in CHANNELS:
        channel_state = "KNOWN"
    else:
        channel_state = "INVALID"

    baseline_raw = raw.get("baseline")
    baseline = read_decimal(baseline_raw)
    if baseline is not None:
        baseline_state = "PARSED"
    elif has_text(baseline_raw) or isinstance(baseline_raw, (int, float)):
        baseline_state = "INVALID"
    else:
        baseline_state = "NOT_PROVIDED"

    target_raw = raw.get("target")
    target = read_decimal(target_raw)
    if target is not None:
        target_state = "PARSED"
    elif has_text(target_raw) or isinstance(target_raw, (int, float)):
        target_state = "INVALID"
    else:
        target_state = "NOT_PROVIDED"

    metric_raw = raw.get("metric")
    metric_name = ""
    metric_definition = ""
    if isinstance(metric_raw, dict):
        metric_name = clean_text(metric_raw.get("name"))
        metric_definition = clean_text(metric_raw.get("definition"))
    elif has_text(metric_raw):
        metric_name = clean_text(metric_raw)

    budget_raw = raw.get("budget_cap") if isinstance(raw.get("budget_cap"), dict) else {}
    amount_raw = budget_raw.get("amount")
    amount = read_decimal(amount_raw, allow_negative=True)
    amount_provided = amount is not None
    currency = clean_text(budget_raw.get("currency")).upper() or None

    start_raw = raw.get("start_at")
    end_raw = raw.get("end_at")
    start_dt = parse_dt(start_raw)
    end_dt = parse_dt(end_raw)
    start_state = _dt_state(start_raw, start_dt)
    end_state = _dt_state(end_raw, end_dt)
    well_formed = start_dt is not None and end_dt is not None

    stop_raw = raw.get("stop_conditions")
    stop_conditions = []
    if isinstance(stop_raw, list):
        for entry in stop_raw:
            text = clean_text(entry)
            if text and text not in stop_conditions:
                stop_conditions.append(text)

    refs_raw = raw.get("evidence_refs")
    evidence_refs = []
    refused = []
    if isinstance(refs_raw, list):
        for offset, entry in enumerate(refs_raw):
            if not has_text(entry):
                continue
            ok, base, reason = safe_basename(entry)
            if ok:
                evidence_refs.append(base)
            else:
                refused.append({
                    "path": "%s/evidence_refs[%d]" % (path, offset),
                    "reason": reason,
                })
                if base:
                    evidence_refs.append(base)

    return {
        "experiment_id": experiment_id, "index": index, "path": path,
        "invalid": False,
        "goal": clean_text(raw.get("goal")),
        "audience": clean_text(raw.get("audience")),
        "offer": clean_text(raw.get("offer")),
        "channel": channel_raw or None,
        "channel_state": channel_state,
        "baseline": baseline, "baseline_state": baseline_state,
        "target": target, "target_state": target_state,
        "metric_name": metric_name,
        "metric_definition": metric_definition,
        "metric_definition_provided": bool(metric_definition),
        "budget": {
            "amount": amount, "currency": currency,
            "amount_provided": amount_provided,
            "currency_provided": bool(currency),
            "raw": amount_raw if isinstance(amount_raw, (int, float, str)) else None,
        },
        "window": {
            "start_at": clean_text(start_raw) if has_text(start_raw) else None,
            "end_at": clean_text(end_raw) if has_text(end_raw) else None,
            "well_formed": well_formed,
            "start_state": start_state, "end_state": end_state,
            "duration_days": None,
        },
        "window_in_past": False,
        "owner": clean_text(raw.get("owner")) or None,
        "outreach_required": read_bool(raw.get("outreach_required")),
        "consent_basis": clean_text(raw.get("consent_basis")),
        "stop_conditions": stop_conditions,
        "evidence_refs": evidence_refs,
        "refused_refs": refused,
        "notes": clean_text(raw.get("notes")),
        "state": "INSUFFICIENT_EVIDENCE",
        "blockers": [], "unknowns": [], "review_flags": [], "reasons": [],
        "_start_dt": start_dt, "_end_dt": end_dt,
    }


def _dt_state(raw, parsed):
    if not has_text(raw):
        return "NOT_PROVIDED"
    return "PARSED" if parsed is not None else "INVALID"


# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------
def classify(experiments, as_of_dt):
    for item in experiments:
        if item["invalid"]:
            continue
        start_dt = item.get("_start_dt")
        end_dt = item.get("_end_dt")
        window = item["window"]
        if start_dt is not None and end_dt is not None:
            duration = Decimal((end_dt - start_dt).total_seconds()) / Decimal(86400)
            window["duration_days"] = str(
                duration.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            # The experiment is over only when its end is already behind as_of;
            # that is a scheduling fact, never a success or failure verdict.
            if as_of_dt is not None and end_dt < as_of_dt:
                item["window_in_past"] = True

    for item in experiments:
        if item["invalid"]:
            continue
        blockers = []
        if item["channel_state"] == "INVALID":
            blockers.append("INVALID_CHANNEL")
        start_dt = item.get("_start_dt")
        end_dt = item.get("_end_dt")
        if start_dt is not None and end_dt is not None and end_dt <= start_dt:
            blockers.append("INVALID_EXPERIMENT_WINDOW")
        amount = item["budget"]["amount"]
        if amount is not None and amount < 0:
            blockers.append("NEGATIVE_BUDGET")
        baseline = item["baseline"]
        target = item["target"]
        if baseline is not None and target is not None and target <= baseline:
            # A target at or below the baseline is not an experiment; it is a
            # contradiction the operator must resolve before any planning.
            blockers.append("TARGET_NOT_ABOVE_BASELINE")
        if blockers:
            item["blockers"] = [code for code in BLOCKER_ORDER if code in blockers]
            item["state"] = "BLOCKED"
            item["reasons"].append("输入存在无法执行的硬矛盾，必须先人工修正")
            continue

        unknowns = []
        if not item["goal"]:
            unknowns.append("NO_GOAL")
        if not item["audience"]:
            unknowns.append("NO_AUDIENCE")
        if not item["offer"]:
            unknowns.append("NO_OFFER")
        if item["baseline_state"] == "NOT_PROVIDED":
            unknowns.append("NO_BASELINE")
        elif item["baseline_state"] == "INVALID":
            unknowns.append("INVALID_BASELINE")
        if item["target_state"] == "NOT_PROVIDED":
            unknowns.append("NO_TARGET")
        elif item["target_state"] == "INVALID":
            unknowns.append("INVALID_TARGET")
        if not item["metric_name"]:
            unknowns.append("UNKNOWN_METRIC")
        if item["channel_state"] == "NOT_PROVIDED":
            unknowns.append("UNKNOWN_CHANNEL")
        if not item["owner"]:
            unknowns.append("UNKNOWN_OWNER")
        start_state = item["window"]["start_state"]
        end_state = item["window"]["end_state"]
        if "NOT_PROVIDED" in (start_state, end_state):
            unknowns.append("NO_WINDOW")
        elif not item["window"]["well_formed"]:
            unknowns.append("INVALID_WINDOW")
        if not item["budget"]["amount_provided"]:
            unknowns.append("NO_BUDGET")
        elif not item["budget"]["currency_provided"]:
            unknowns.append("UNKNOWN_BUDGET_CURRENCY")
        if item["outreach_required"] is None:
            unknowns.append("UNKNOWN_OUTREACH_REQUIREMENT")

        if unknowns:
            item["unknowns"] = [code for code in UNKNOWN_ORDER if code in unknowns]
            item["state"] = "INSUFFICIENT_EVIDENCE"
            item["reasons"].append("缺少明示事实，必须先追问而不是替你补齐")
            continue

        prep = []
        if not item["stop_conditions"]:
            prep.append("NO_STOP_CONDITIONS")
        if item["outreach_required"] is True and not item["consent_basis"]:
            prep.append("NO_CONSENT_BASIS")
        if item["window_in_past"]:
            prep.append("WINDOW_IN_PAST")
        if not item["metric_definition_provided"]:
            prep.append("METRIC_DEFINITION_MISSING")

        item["review_flags"] = [code for code in PREP_ORDER if code in prep]
        if "NO_STOP_CONDITIONS" in prep:
            item["reasons"].append("没有停止条件：无法判断何时该停")
        if "NO_CONSENT_BASIS" in prep:
            item["reasons"].append("需要外发但没有同意依据：不会生成任何外发动作")
        if "WINDOW_IN_PAST" in prep:
            item["reasons"].append("实验窗口已经结束：需人工决定是复盘还是重开")
        if "METRIC_DEFINITION_MISSING" in prep:
            item["reasons"].append("指标没有口径说明：同一数字可能被算成不同结果")

        # The only flags that require action are the ones the operator can fix
        # before running; a missing metric definition is a documentation gap.
        actionable = [code for code in prep if code != "METRIC_DEFINITION_MISSING"]
        item["state"] = "ACTION_NEEDED" if actionable else "READY"

    for item in experiments:
        item.pop("_start_dt", None)
        item.pop("_end_dt", None)


def build_questions(experiments):
    questions = []
    for item in experiments:
        label = item.get("experiment_id") or item["path"]
        for unknown in item.get("unknowns", []):
            questions.append((label, unknown))
    out = []
    for index, (label, topic) in enumerate(questions, start=1):
        out.append({
            "id": "Q-%02d" % index,
            "experiment_id": label,
            "topic": topic,
            "question": _question_text(topic),
        })
    return out


def _question_text(topic):
    mapping = {
        "NO_GOAL": "没有写明实验目标，无法判断要改变什么，请补齐目标。",
        "NO_AUDIENCE": "没有写明面向谁，触达对象不明，请补齐受众。",
        "NO_OFFER": "没有写明给对方的钩子，请补齐实验里要提供的内容。",
        "NO_BASELINE": "没有基线数字，无法判断是否变化；本工具不替你推算基线。",
        "INVALID_BASELINE": "基线不是可比较的数值，请给出明确的当前数字与口径。",
        "NO_TARGET": "没有目标数字，无法判断实验是否成立，请补齐目标。",
        "INVALID_TARGET": "目标不是可比较的数值，请给出明确的期望数字与口径。",
        "UNKNOWN_METRIC": "没有指标名，无法定义成功与否，请补齐指标。",
        "UNKNOWN_CHANNEL": "没有渠道，无法准备动作，请补齐渠道。",
        "UNKNOWN_OWNER": "没有责任人，无法落地，本工具也不会自动分派。",
        "NO_WINDOW": "没有起止时间，无法判断窗口，请补齐（不要只写大概时间）。",
        "INVALID_WINDOW": "起止时间格式无效或缺少时区偏移，请修正后重新提交。",
        "NO_BUDGET": "没有预算上限，无法准备预算约束；请给出上限，本工具不按 0 计算。",
        "UNKNOWN_BUDGET_CURRENCY": "有预算金额但没有币种，无法登记；币种不做自动换算。",
        "UNKNOWN_OUTREACH_REQUIREMENT": "没有说明是否需要外发，无法判断同意门禁，请补齐。",
    }
    return mapping.get(topic, "存在待确认事实：" + topic)


def build_outreach(experiments):
    """Return (plan, do_not_outreach). Never sends anything: drafts only."""
    plan = []
    blocked = []
    for item in experiments:
        if item["outreach_required"] is not True:
            continue
        label = item.get("experiment_id") or item["path"]
        if item["consent_basis"] and not item["window_in_past"] \
                and item["state"] in ("READY", "ACTION_NEEDED"):
            plan.append({
                "experiment_id": label,
                "channel": item["channel"],
                "audience": item["audience"],
                "consent_basis": item["consent_basis"],
                "status": "DRAFT_NOT_SENT",
            })
        else:
            reason = ("NO_CONSENT_BASIS" if not item["consent_basis"]
                      else ("WINDOW_IN_PAST" if item["window_in_past"]
                            else ("NOT_PREPARED" if item["state"] in
                                  ("BLOCKED", "INSUFFICIENT_EVIDENCE")
                                  else "NOT_PREPARED")))
            blocked.append({"experiment_id": label, "reason": reason})
    plan.sort(key=lambda r: r["experiment_id"])
    blocked.sort(key=lambda r: r["experiment_id"])
    return plan, blocked


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def render_markdown(status, as_of, business, counts, experiments, cards,
                    ready, action_needed, blocked, insufficient, missing_facts,
                    budgets, budget_note, plan, no_outreach, human_confirm,
                    questions, warnings):
    lines = []
    lines.append("# 客户增长实验准备包")
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    if business:
        lines.append("- 业务：%s" % (hidden(business.get("name")) or "未提供"))
        lines.append("- 时区：%s" % (esc(business.get("timezone")) or "未提供"))
    lines.append("- 实验数：%d" % len(experiments))
    lines.append("")
    lines.append("## 实验结论")
    lines.append("")
    lines.append("| 结论 | 数量 |")
    lines.append("|---|---:|")
    for name in EXPERIMENT_STATES:
        lines.append("| %s | %d |" % (name, counts[name]))
    lines.append("")
    lines.append("| 实验 | 目标 | 渠道 | 基线 | 目标值 | 负责人 | 结论 |")
    lines.append("|---|---|---|---|---|---|---|")
    for item in experiments:
        lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            esc(item.get("experiment_id")) if item.get("experiment_id")
            else esc(item["path"]),
            hidden(item.get("goal")) or "未提供",
            esc(item.get("channel")) if item.get("channel") else "未提供",
            money(item.get("baseline")) if item.get("baseline") is not None else "未提供",
            money(item.get("target")) if item.get("target") is not None else "未提供",
            esc(item.get("owner")) if item.get("owner") else "未指派",
            item["state"],
        ))
    lines.append("")

    lines.append("## 实验卡")
    lines.append("")
    if cards:
        lines.append("| 实验 | 指标 | 口径 | 基线→目标 | 预算上限 | 窗口 | 停止条件 |")
        lines.append("|---|---|---|---|---|---|---|")
        for card in cards:
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                esc(card["experiment_id"]),
                hidden(card["metric_name"]) or "未提供",
                hidden(card["metric_definition"]) or "未提供",
                esc(card["baseline_to_target"]),
                esc(card["budget_display"]),
                esc(card["window_display"]),
                hidden(card["stop_conditions_display"]) or "未提供",
            ))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 已就绪可以开始")
    lines.append("")
    if ready:
        for row in ready:
            lines.append("- `%s` %s" % (esc(row["experiment_id"]),
                                       hidden(row["goal"]) or "未提供目标"))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 开始前必须补齐")
    lines.append("")
    if action_needed:
        lines.append("| 实验 | 需要处理 | 说明 |")
        lines.append("|---|---|---|")
        for row in action_needed:
            lines.append("| %s | %s | %s |" % (
                esc(row["experiment_id"]),
                esc("、".join(row["review_flags"])) or "未记录",
                hidden("；".join(row["reasons"])) or "未记录"))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 证据不足（先追问，不猜）")
    lines.append("")
    if insufficient:
        lines.append("| 实验 | 缺失事实 |")
        lines.append("|---|---|")
        for row in insufficient:
            lines.append("| %s | %s |" % (
                esc(row["experiment_id"]), esc("、".join(row["unknowns"]))))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 阻塞（硬矛盾，必须先修正）")
    lines.append("")
    if blocked:
        lines.append("| 实验 | 阻塞原因 |")
        lines.append("|---|---|")
        for row in blocked:
            lines.append("| %s | %s |" % (
                esc(row["experiment_id"]), esc("、".join(row["blockers"]))))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 缺失事实汇总")
    lines.append("")
    if missing_facts:
        for row in missing_facts:
            lines.append("- `%s`：%s" % (
                esc(row["experiment_id"]), esc("、".join(row["unknowns"]))))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 预算上限（不换算、不跨币种合计）")
    lines.append("")
    if budgets:
        lines.append("| 币种 | 合计上限 |")
        lines.append("|---|---:|")
        for currency in sorted(budgets):
            lines.append("| %s | %s |" % (esc(currency), esc(budgets[currency])))
    else:
        lines.append("- 无可用预算上限（缺失值不按 0 计算）")
    lines.append("")
    lines.append("> %s" % esc(budget_note))
    lines.append("")

    lines.append("## 外发准备（草稿，永不自动发送）")
    lines.append("")
    if plan:
        lines.append("| 实验 | 渠道 | 受众 | 同意依据 | 状态 |")
        lines.append("|---|---|---|---|---|")
        for row in plan:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(row["experiment_id"]),
                esc(row["channel"]) if row["channel"] else "未提供",
                hidden(row["audience"]) or "未提供",
                hidden(row["consent_basis"]) or "未提供",
                esc(row["status"])))
    else:
        lines.append("- 无")
    lines.append("")
    lines.append("## 不会外发的实验")
    lines.append("")
    if no_outreach:
        for row in no_outreach:
            lines.append("- `%s`：%s" % (esc(row["experiment_id"]), esc(row["reason"])))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 实验说明")
    lines.append("")
    if any(item.get("notes") for item in experiments):
        lines.append("| 实验 | 备注 |")
        lines.append("|---|---|")
        for item in experiments:
            if item.get("notes"):
                lines.append("| %s | %s |" % (
                    esc(item.get("experiment_id")) if item.get("experiment_id")
                    else esc(item["path"]),
                    hidden(item.get("notes"))))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 人工确认项")
    lines.append("")
    for item in human_confirm:
        lines.append("- %s" % esc(item))
    lines.append("")

    lines.append("## 待确认问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- `%s` [%s] %s" % (
                question["id"], esc(question["experiment_id"]),
                esc(question["question"])))
    else:
        lines.append("- 无")
    lines.append("")

    if warnings:
        lines.append("## 输入提示")
        lines.append("")
        for warning in warnings:
            lines.append("- %s" % esc(warning))
        lines.append("")

    lines.append("## 安全声明")
    lines.append("")
    lines.append("- %s" % esc(NO_SEND_DECLARATION))
    lines.append("- %s" % esc(NO_SPEND_DECLARATION))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("> %s" % esc(DISCLAIMER))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------
def analyse(data):
    if not isinstance(data, dict):
        data = {}
    creds = find_credentials(data)
    if creds:
        return reject(creds)
    injections = find_injections(data)

    as_of_raw = data.get("as_of")
    as_of_dt = parse_dt(as_of_raw)
    raw_experiments = data.get("experiments")
    if not isinstance(raw_experiments, list):
        raw_experiments = []

    warnings = []
    if as_of_dt is None:
        warnings.append("AS_OF_MISSING_OR_INVALID")
    if not raw_experiments:
        warnings.append("NO_EXPERIMENTS")
    if warnings:
        return envelope("INPUT_INCOMPLETE", as_of_raw, injections, warnings)

    experiments = [normalise_experiment(raw, index)
                   for index, raw in enumerate(raw_experiments)]
    classify(experiments, as_of_dt)

    experiments.sort(key=lambda i: (i.get("experiment_id") or "", i["path"]))

    counts = {name: 0 for name in EXPERIMENT_STATES}
    for item in experiments:
        counts[item["state"]] += 1

    def row(item):
        return {
            "experiment_id": item.get("experiment_id") or item["path"],
            "path": item["path"],
            "goal": item.get("goal"),
            "channel": item.get("channel"),
            "owner": item.get("owner"),
            "unknowns": list(item.get("unknowns", [])),
            "blockers": list(item.get("blockers", [])),
            "review_flags": list(item.get("review_flags", [])),
            "reasons": list(item.get("reasons", [])),
        }

    ready = [row(i) for i in experiments if i["state"] == "READY"]
    action_needed = [row(i) for i in experiments if i["state"] == "ACTION_NEEDED"]
    blocked = [row(i) for i in experiments if i["state"] == "BLOCKED"]
    insufficient = [row(i) for i in experiments
                    if i["state"] == "INSUFFICIENT_EVIDENCE"]

    missing_facts = [{"experiment_id": item.get("experiment_id") or item["path"],
                      "path": item["path"],
                      "unknowns": list(item["unknowns"])}
                     for item in experiments if item["unknowns"]]
    missing_facts.sort(key=lambda r: r["experiment_id"])

    cards = []
    for item in experiments:
        baseline = item["baseline"]
        target = item["target"]
        delta = None
        if baseline is not None and target is not None:
            delta = money(target - baseline)
        baseline_to_target = "%s → %s" % (
            money(baseline) if baseline is not None else "未提供",
            money(target) if target is not None else "未提供")
        budget = item["budget"]
        if budget["amount"] is not None and budget["currency"]:
            budget_display = "%s %s" % (money(budget["amount"]), budget["currency"])
        elif budget["amount"] is not None:
            budget_display = "%s（币种未提供）" % money(budget["amount"])
        else:
            budget_display = "未提供（未按 0 计算）"
        window = item["window"]
        if window["well_formed"]:
            window_display = "%s → %s（%s 天）" % (
                window["start_at"], window["end_at"], window["duration_days"])
        elif item["window"]["start_state"] == "NOT_PROVIDED" \
                and item["window"]["end_state"] == "NOT_PROVIDED":
            window_display = "未提供"
        else:
            window_display = "无效时间（缺时区偏移或格式错误）"
        cards.append({
            "experiment_id": item.get("experiment_id") or item["path"],
            "path": item["path"],
            "goal": item.get("goal"),
            "audience": item.get("audience"),
            "channel": item.get("channel"),
            "offer": item.get("offer"),
            "metric_name": item.get("metric_name"),
            "metric_definition": item.get("metric_definition"),
            "baseline": money(baseline),
            "target": money(target),
            "target_delta": delta,
            "baseline_to_target": baseline_to_target,
            "budget_display": budget_display,
            "window_display": window_display,
            "window_in_past": item["window_in_past"],
            "owner": item.get("owner"),
            "stop_conditions": list(item.get("stop_conditions", [])),
            "stop_conditions_display": "；".join(item.get("stop_conditions", [])),
            "state": item["state"],
        })

    budgets = {}
    for item in experiments:
        amount = item["budget"]["amount"]
        currency = item["budget"]["currency"]
        if amount is not None and amount >= 0 and currency:
            budgets[currency] = budgets.get(currency, Decimal("0")) + amount
    budgets = {currency: money(total) for currency, total in budgets.items()}
    budget_note = ("不同币种分别列示、不做汇率换算、也不合计；"
                   "缺失金额一律保持未知，不按 0 计算。")

    plan, no_outreach = build_outreach(experiments)

    human_confirm = list(HUMAN_CONFIRM_BASE)
    if blocked:
        human_confirm.append("存在硬矛盾的实验必须先人工修正，本工具不会替你改目标或基线")
    if no_outreach:
        human_confirm.append("未外发的原因需要人工处理：补齐同意依据或另定窗口")
    if any(item["baseline_state"] == "INVALID" for item in experiments):
        human_confirm.append("基线数字口径需人工统一后再比较")

    refused_all = []
    for item in experiments:
        for refused in item.get("refused_refs", []):
            refused_all.append({"experiment_id": item.get("experiment_id"),
                                "path": refused["path"],
                                "reason": refused["reason"]})
    refused_all.sort(key=lambda r: (r["experiment_id"] or "", r["path"]))

    questions = build_questions(experiments)

    if counts["BLOCKED"]:
        status = "BLOCKED"
    elif counts["ACTION_NEEDED"] or counts["INSUFFICIENT_EVIDENCE"]:
        status = "GAPS_FOUND"
    else:
        status = "READY"

    business_raw = data.get("business") if isinstance(data.get("business"), dict) else None
    business = None
    if business_raw:
        business = {
            "name": clean_text(business_raw.get("name")),
            "timezone": clean_text(business_raw.get("timezone")) or None,
        }

    markdown = render_markdown(status, as_of_raw, business, counts, experiments,
                              cards, ready, action_needed, blocked, insufficient,
                              missing_facts, budgets, budget_note, plan, no_outreach,
                              human_confirm, questions, warnings)

    for item in experiments:
        item["baseline"] = money(item["baseline"])
        item["target"] = money(item["target"])
        item["budget"]["amount"] = money(item["budget"]["amount"])

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "as_of_date": (as_of_dt.date().isoformat() if as_of_dt else None),
        "business": business,
        "experiment_count": len(experiments),
        "status_counts": counts,
        "experiments": experiments,
        "experiment_cards": cards,
        "ready_board": ready,
        "action_needed_board": action_needed,
        "blocked_board": blocked,
        "insufficient_board": insufficient,
        "missing_facts": missing_facts,
        "budgets_by_currency": budgets,
        "budget_currency_note": budget_note,
        "outreach_plan": plan,
        "do_not_outreach": no_outreach,
        "refused_refs": refused_all,
        "injection_flagged": list(injections),
        "input_warnings": warnings,
        "human_confirm_items": human_confirm,
        "clarification_questions": questions,
        "no_send_declaration": NO_SEND_DECLARATION,
        "no_spend_declaration": NO_SPEND_DECLARATION,
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
    }


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
