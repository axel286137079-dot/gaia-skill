#!/usr/bin/env python3
"""小团队 SOP 自动化机会地图 — offline SOP automation opportunity mapper.

Pure Python 3.9+ standard library. Reads exactly one local JSON file, writes one
JSON document to stdout. No network, no filesystem writes, no system connection,
no command execution, no workflow generation.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = "1.0.0"

VERDICT_KEEP_MANUAL = "keep_manual"
VERDICT_ASSIST = "assist"
VERDICT_AUTOMATE = "automate_candidate"
VERDICT_INSUFFICIENT = "insufficient_evidence"

VERDICTS = (VERDICT_AUTOMATE, VERDICT_ASSIST, VERDICT_KEEP_MANUAL, VERDICT_INSUFFICIENT)

FREQUENCIES = ("daily", "weekly", "monthly", "per_delivery", "ad_hoc")

# Actions that must keep a human decision in the loop. Never inferred from prose:
# only the step's own `touches[]` values and declared sensitivity count.
HIGH_RISK_TOUCHES = (
    "payment", "deletion", "outbound_message", "external_publish",
    "account_permission", "legal", "medical", "financial",
)
KNOWN_TOUCHES = HIGH_RISK_TOUCHES + (
    "read_only", "data_entry", "internal_note", "scheduling", "reporting",
)

# Sensitivity levels that must never reach an unattended automation channel.
PROTECTED_SENSITIVITY = ("financial", "regulated")

CONSEQUENCES = ("low", "medium", "high")
SENSITIVITIES = ("public", "internal", "customer_pii", "financial", "regulated")

DEFAULT_SHADOW_RUNS = 10

PLACEHOLDER = "已隐藏疑似提示注入文本"

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

DISCLAIMER = (
    "本输出只是依据你自己填写的流程事实做出的机会盘点与实施顺序建议，不是自动化收益、"
    "节省金额或实施成功的承诺；本工具不生成任何可执行的自动化脚本或工作流，不连接你的"
    "任何系统，不索取任何凭据，也不代为执行、发送、删除或付款。缺失数据一律标为证据不足，"
    "不虚构优先级；是否实施仍由你与团队决定。"
)

TOOL_LIMITS = (
    "不生成可执行的 n8n / Zapier / Make / 脚本代码",
    "不连接你的任何系统，不读取你的任何后台或账号",
    "不索取账号、密码、Token、Cookie 或任何凭据",
    "不代为发送、发布、删除、付款或修改权限",
    "不做法律、医疗、金融或人力资源的合规判断",
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


def trim(value):
    """Display-only trimmed numeric string: 22.00 -> 22, 12.50 -> 12.5."""
    if value is None:
        return None
    text = format(quant(value, 2), "f")
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


def parse_cost(node):
    """Returns (Decimal|None, currency|None, state) with state in MISSING/INVALID/OK."""
    if not isinstance(node, dict) or node.get("value") in (None, ""):
        return None, None, "MISSING"
    value, _present, valid = as_amount(node.get("value"))
    currency = clean_text(node.get("currency")).upper() if has_text(node.get("currency")) else None
    if not valid or value is None or value < 0:
        return None, currency, "INVALID"
    if currency is None:
        return value, None, "NO_CURRENCY"
    return value, currency, "OK"


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
        "team": None,
        "counts": {"processes": 0, "steps": 0, "ranked_steps": 0, "unranked_steps": 0,
                   "verdict_counts": {v: 0 for v in VERDICTS}},
        "processes": [], "steps": [], "priority_matrix": [], "unranked_steps": [],
        "implementation_order": [], "manual_only_steps": [], "human_checkpoints": [],
        "data_permission_prerequisites": [], "failure_fallbacks": [],
        "time_saving_hypothesis": {"by_step": [], "by_currency": {}, "missing_inputs": []},
        "markdown_summary": "# SOP 自动化机会地图\n\n- 状态：**REJECTED**\n- 已拒绝处理，未回显疑似凭据内容。\n",
        "injection_flagged": [], "input_warnings": ["CREDENTIAL_DETECTED"],
        "tool_limits": list(TOOL_LIMITS),
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

    as_of_raw = data.get("as_of")
    as_of_ok = has_text(as_of_raw) and bool(DT_RE.match(clean_text(as_of_raw)))
    if not has_text(as_of_raw):
        warnings.append("AS_OF_MISSING")
    elif not as_of_ok:
        warnings.append("AS_OF_TIMEZONE_MISSING_OR_INVALID")

    team_raw = data.get("team") if isinstance(data.get("team"), dict) else {}
    team_name = clean_text(team_raw.get("name")) if has_text(team_raw.get("name")) else None
    team_size = team_raw.get("size") if isinstance(team_raw.get("size"), int) and not isinstance(team_raw.get("size"), bool) else None
    default_cost, default_currency, default_cost_state = parse_cost(team_raw.get("default_hourly_cost"))
    if default_cost_state == "INVALID":
        warnings.append("TEAM_DEFAULT_HOURLY_COST_INVALID")
    elif default_cost_state == "NO_CURRENCY":
        warnings.append("TEAM_DEFAULT_HOURLY_COST_NO_CURRENCY")

    processes_raw = data.get("processes") if isinstance(data.get("processes"), list) else []
    processes = []
    steps = []
    for p_index, entry in enumerate(processes_raw):
        if not isinstance(entry, dict):
            continue
        process_id = clean_text(entry.get("process_id")) if has_text(entry.get("process_id")) else None
        process_name = clean_text(entry.get("name")) if has_text(entry.get("name")) else ""
        owner = clean_text(entry.get("owner")) if has_text(entry.get("owner")) else None
        p_flags = []
        if process_id is None:
            p_flags.append("MISSING_PROCESS_ID")
        raw_steps = entry.get("steps") if isinstance(entry.get("steps"), list) else []
        if not raw_steps:
            p_flags.append("NO_STEPS_PROVIDED")
        processes.append({
            "process_id": process_id, "name": process_name, "owner": owner,
            "step_count": len([s for s in raw_steps if isinstance(s, dict)]),
            "review_flags": sorted(set(p_flags)), "index": p_index,
        })

        for s_index, step_raw in enumerate(raw_steps):
            if not isinstance(step_raw, dict):
                continue
            steps.append(_read_step(step_raw, process_id, process_name, owner,
                                    default_cost, default_currency, warnings,
                                    p_index, s_index))

    for index, step in enumerate(steps):
        step["index"] = index

    # ---- verdict ----
    for step in steps:
        _decide(step)

    # ---- priority ----
    ranked = []
    unranked = []
    for step in steps:
        if step["monthly_hours"] is not None and step["error_rate_pct"] is not None:
            step["score"] = quant(step["monthly_hours"] * Decimal(10) + step["error_rate_pct"], 2)
        else:
            step["score"] = None
        if step["verdict"] in (VERDICT_AUTOMATE, VERDICT_ASSIST) and step["score"] is not None:
            ranked.append(step)
        else:
            unranked.append(step)

    ranked.sort(key=lambda s: (-s["score"], str(s["step_id"] or ""), str(s["process_id"] or "")))
    for position, step in enumerate(ranked, start=1):
        step["rank"] = position
    for step in steps:
        step.setdefault("rank", None)

    unranked_reasons = []
    for step in sorted(unranked, key=lambda s: (str(s["process_id"] or ""), str(s["step_id"] or ""))):
        missing = []
        if step["monthly_hours"] is None:
            missing.append("MONTHLY_HOURS_NOT_COMPUTABLE")
        if step["error_rate_pct"] is None:
            missing.append("ERROR_RATE_MISSING")
        unranked_reasons.append({
            "process_id": step["process_id"], "step_id": step["step_id"], "verdict": step["verdict"],
            "reason": missing or ["VERDICT_NOT_RANKABLE"],
            "note": ("证据不足的步骤不进入优先级排序，先补数据，不先做自动化。"
                     if step["verdict"] == VERDICT_INSUFFICIENT else
                     "保持人工或先做辅助，不参与自动化优先级排序。"),
        })

    # ---- implementation order ----
    automate = [s for s in ranked if s["verdict"] == VERDICT_AUTOMATE]
    assist = [s for s in ranked if s["verdict"] == VERDICT_ASSIST]
    keep = sorted([s for s in steps if s["verdict"] == VERDICT_KEEP_MANUAL],
                  key=lambda s: (str(s["process_id"] or ""), str(s["step_id"] or "")))
    insufficient = sorted([s for s in steps if s["verdict"] == VERDICT_INSUFFICIENT],
                          key=lambda s: (str(s["process_id"] or ""), str(s["step_id"] or "")))
    order = []
    for step in automate + assist + keep + insufficient:
        order.append({
            "order": len(order) + 1,
            "process_id": step["process_id"], "step_id": step["step_id"], "name": step["name"],
            "verdict": step["verdict"], "score": amount(step["score"]),
        })

    # ---- prerequisites ----
    prerequisites = []
    for step in sorted(steps, key=lambda s: (str(s["process_id"] or ""), str(s["step_id"] or ""))):
        items = []
        if step["data_sensitivity"] == "customer_pii":
            items.append("含个人数据：先做最小化与保留期限约定，不把原始对话整段喂给自动化")
        if step["data_sensitivity"] in PROTECTED_SENSITIVITY:
            items.append("财务或受监管数据：只允许人工辅助，不进入无人值守通道")
        if step["data_sensitivity"] is None:
            items.append("数据敏感度未填写：按未知处理，先补数据再判断")
        for touch in step["touches"]:
            if touch == "account_permission":
                items.append("涉及账号权限：需先走权限变更审批；本工具不索取任何凭据")
            elif touch == "external_publish":
                items.append("涉及对外发布：需人工确认内容与账号后才能执行")
            elif touch == "payment":
                items.append("涉及付款：必须双人复核，任何情况下都不交给自动化执行")
            elif touch == "deletion":
                items.append("涉及删除：先确认可恢复备份与保留策略")
        if step["external_dependency"]:
            items.append("存在外部依赖：先确认对端接口、变更窗口与负责人")
        if step["unknown_touches"]:
            items.append("存在未识别的动作标签 %s：按「风险未知」处理，不得当作安全"
                         % "、".join(step["unknown_touches"]))
        if items:
            prerequisites.append({
                "process_id": step["process_id"], "step_id": step["step_id"],
                "items": items,
            })

    # ---- human checkpoints ----
    checkpoints = []
    for step in sorted(steps, key=lambda s: (str(s["process_id"] or ""), str(s["step_id"] or ""))):
        forced = []
        for touch in step["touches"]:
            if touch in HIGH_RISK_TOUCHES:
                forced.append("HIGH_RISK_TOUCH:" + touch)
        if step["unknown_touches"]:
            forced.append("UNKNOWN_TOUCH_TAG")
        if step["data_sensitivity"] in PROTECTED_SENSITIVITY:
            forced.append("PROTECTED_DATA:" + str(step["data_sensitivity"]))
        if step["error_consequence"] == "high":
            forced.append("HIGH_ERROR_CONSEQUENCE")
        if step["human_approval_required"]:
            forced.append("USER_DECLARED")
        if not forced:
            continue
        if step["verdict"] == VERDICT_KEEP_MANUAL:
            mode = "FULLY_MANUAL"
            action = "整步保持人工：本工具不建议自动化该步骤。"
        elif step["verdict"] == VERDICT_INSUFFICIENT:
            mode = "WAIT_FOR_EVIDENCE"
            action = "先补齐耗时、频率与错误率；在补齐前不进入任何自动化通道。"
        else:
            mode = "APPROVE_BEFORE_USE"
            action = "可以辅助生成草稿，但产出必须由人工确认后才能对外使用或进入下一步。"
        checkpoints.append({
            "process_id": step["process_id"], "step_id": step["step_id"],
            "verdict": step["verdict"], "mode": mode, "reasons": sorted(set(forced)),
            "action": action,
        })

    # ---- failure fallbacks ----
    fallbacks = []
    for step in sorted(steps, key=lambda s: (str(s["process_id"] or ""), str(s["step_id"] or ""))):
        if step["verdict"] == VERDICT_AUTOMATE:
            runs = step["shadow_runs"] if step["shadow_runs"] is not None else DEFAULT_SHADOW_RUNS
            fallbacks.append({
                "process_id": step["process_id"], "step_id": step["step_id"],
                "verdict": step["verdict"],
                "fallback": ("先并行人工复核 %d 次（默认值，可自定），逐条比对结果；任一条不一致就立刻"
                             "回到全人工，并在修正规则后重新并行复核。建议保留随时切回人工的一键开关。"
                             % runs),
            })
        elif step["verdict"] == VERDICT_ASSIST:
            fallbacks.append({
                "process_id": step["process_id"], "step_id": step["step_id"],
                "verdict": step["verdict"],
                "fallback": "辅助产出必须人工确认后才使用；人工不在场时退回原来的手工流程，不自动放行。",
            })
        elif step["verdict"] == VERDICT_KEEP_MANUAL:
            fallbacks.append({
                "process_id": step["process_id"], "step_id": step["step_id"],
                "verdict": step["verdict"],
                "fallback": "不投入自动化；如果流程本身趋于稳定，可在一个季度后重新盘点。",
            })
        else:
            fallbacks.append({
                "process_id": step["process_id"], "step_id": step["step_id"],
                "verdict": step["verdict"],
                "fallback": "先补数据再判断，本步暂不进入实施清单。",
            })

    # ---- time saving hypothesis (explicit inputs only) ----
    by_currency = {}
    by_step = []
    missing_inputs = []
    for step in sorted(steps, key=lambda s: (str(s["process_id"] or ""), str(s["step_id"] or ""))):
        entry = {
            "process_id": step["process_id"], "step_id": step["step_id"],
            "verdict": step["verdict"],
            "monthly_hours": amount(step["monthly_hours"]),
            "assumed_saving_ratio": amount(step["assumed_saving_ratio"]) if step["assumed_saving_ratio"] is not None else None,
            "monthly_saving_hours_hypothesis": amount(step["monthly_saving_hours"]),
            "hourly_cost": amount(step["effective_cost"]),
            "hourly_cost_currency": step["effective_currency"],
            "hourly_cost_source": step["cost_source"],
            "monthly_saving_amount_hypothesis": amount(step["monthly_saving_amount"]),
            "missing_inputs": step["money_missing"],
        }
        if any([step["monthly_hours"] is None, step["assumed_saving_ratio"] is None,
                step["effective_cost"] is None, step["effective_currency"] is None]):
            by_step.append(entry)
            missing_inputs.append({
                "process_id": step["process_id"], "step_id": step["step_id"],
                "missing_inputs": step["money_missing"],
            })
        else:
            by_step.append(entry)
            bucket = by_currency.setdefault(step["effective_currency"], Decimal("0"))
            by_currency[step["effective_currency"]] = bucket + step["monthly_saving_amount"]

    currency_view = {code: amount(by_currency[code]) for code in sorted(by_currency)}

    # ---- status ----
    verdict_counts = {v: 0 for v in VERDICTS}
    for step in steps:
        verdict_counts[step["verdict"]] += 1

    if not as_of_ok or not processes or not steps:
        status = "INPUT_INCOMPLETE"
    elif not any(v in (VERDICT_AUTOMATE, VERDICT_ASSIST) for v in
                 [s["verdict"] for s in steps]):
        status = "NO_CANDIDATE"
    else:
        status = "MAPPED"

    counts = {
        "processes": len(processes), "steps": len(steps),
        "ranked_steps": len(ranked), "unranked_steps": len(unranked),
        "verdict_counts": verdict_counts,
    }

    # ---- injection report ----
    injection_flagged = []
    for step in steps:
        if injection_hit(step["name"]):
            injection_flagged.append({"path": step["path"] + "/name",
                                      "marker": "PROMPT_INJECTION"})
        if injection_hit(step["notes"]):
            injection_flagged.append({"path": step["path"] + "/notes",
                                      "marker": "PROMPT_INJECTION"})

    markdown = render_markdown(
        status=status, as_of=as_of_raw, team_name=team_name, team_size=team_size,
        default_cost=default_cost, default_currency=default_currency,
        processes=processes, steps=steps, ranked=ranked, unranked_reasons=unranked_reasons,
        order=order, prerequisites=prerequisites, checkpoints=checkpoints,
        fallbacks=fallbacks, currency_view=currency_view, counts=counts,
        warnings=warnings, injection_flagged=injection_flagged,
    )

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "team": {
            "name": team_name, "size": team_size,
            "default_hourly_cost": amount(default_cost),
            "default_hourly_cost_currency": default_currency,
            "default_hourly_cost_state": default_cost_state,
        },
        "counts": counts,
        "processes": processes,
        "steps": [step_view(step) for step in steps],
        "priority_matrix": [step_view(step) for step in ranked],
        "unranked_steps": unranked_reasons,
        "implementation_order": order,
        "manual_only_steps": [step_view(s) for s in keep],
        "insufficient_evidence_steps": [step_view(s) for s in insufficient],
        "human_checkpoints": checkpoints,
        "data_permission_prerequisites": prerequisites,
        "failure_fallbacks": fallbacks,
        "time_saving_hypothesis": {
            "by_step": by_step,
            "by_currency": currency_view,
            "missing_inputs": missing_inputs,
            "assumptions": [
                "月均运行次数 monthly_runs 必须由你显式提供，本工具不按「每天 = 21 次」推算。",
                "节省比例 assumed_saving_ratio 必须由你显式提供，本工具不假设任何节省比例。",
                "小时成本取步骤 hourly_cost，缺失时回退到 team.default_hourly_cost，不自动折算币种。",
                "不同币种的节省金额分别列出，不做跨币种合计，也不做汇率换算。",
            ],
        },
        "markdown_summary": markdown,
        "injection_flagged": injection_flagged,
        "input_warnings": sorted(set(warnings)),
        "tool_limits": list(TOOL_LIMITS),
        "disclaimer": DISCLAIMER,
    }


def _read_step(raw, process_id, process_name, process_owner, default_cost, default_currency,
               warnings, p_index, s_index):
    step_id = clean_text(raw.get("step_id")) if has_text(raw.get("step_id")) else None
    name = clean_text(raw.get("name")) if has_text(raw.get("name")) else ""
    frequency_raw = raw.get("frequency")
    frequency = clean_text(frequency_raw).lower() if has_text(frequency_raw) else None
    minutes, minutes_present, minutes_valid = as_amount(raw.get("minutes_per_run"))
    runs, runs_present, runs_valid = as_amount(raw.get("monthly_runs"))
    error_rate, error_present, error_valid = as_amount(raw.get("error_rate_pct"))
    ratio, ratio_present, ratio_valid = as_amount(raw.get("assumed_saving_ratio"))
    consequence_raw = raw.get("error_consequence")
    consequence = clean_text(consequence_raw).lower() if has_text(consequence_raw) else None
    sensitivity_raw = raw.get("data_sensitivity")
    sensitivity = clean_text(sensitivity_raw).lower() if has_text(sensitivity_raw) else None
    cost, cost_currency, cost_state = parse_cost(raw.get("hourly_cost"))

    flags = []
    if step_id is None:
        flags.append("MISSING_STEP_ID")
    if not name:
        flags.append("NAME_MISSING")
    if frequency is None:
        flags.append("FREQUENCY_MISSING")
    elif frequency not in FREQUENCIES:
        flags.append("FREQUENCY_UNKNOWN")
    if not minutes_present:
        flags.append("MINUTES_MISSING")
    elif not minutes_valid:
        flags.append("MINUTES_INVALID")
    elif minutes < 0:
        flags.append("MINUTES_NEGATIVE")
        minutes = None
    if not runs_present:
        flags.append("MONTHLY_RUNS_MISSING")
    elif not runs_valid:
        flags.append("MONTHLY_RUNS_INVALID")
    elif runs < 0:
        flags.append("MONTHLY_RUNS_NEGATIVE")
        runs = None
    if not error_present:
        flags.append("ERROR_RATE_MISSING")
    elif not error_valid:
        flags.append("ERROR_RATE_INVALID")
    if not ratio_present:
        flags.append("SAVING_RATIO_MISSING")
    elif not ratio_valid:
        flags.append("SAVING_RATIO_INVALID")
    if consequence is None:
        flags.append("ERROR_CONSEQUENCE_MISSING")
    elif consequence not in CONSEQUENCES:
        flags.append("ERROR_CONSEQUENCE_UNKNOWN")
    if sensitivity is None:
        flags.append("DATA_SENSITIVITY_MISSING")
    elif sensitivity not in SENSITIVITIES:
        flags.append("DATA_SENSITIVITY_UNKNOWN")
    if cost_state == "INVALID":
        flags.append("HOURLY_COST_INVALID")
    elif cost_state == "NO_CURRENCY":
        flags.append("HOURLY_COST_NO_CURRENCY")

    touches = []
    unknown_touches = []
    if isinstance(raw.get("touches"), list):
        for item in raw["touches"]:
            text = clean_text(item).lower() if isinstance(item, str) else ""
            if not text or text in touches or text in unknown_touches:
                continue
            if text not in KNOWN_TOUCHES:
                unknown_touches.append(text)
                flags.append("UNKNOWN_TOUCH_TAG")
            touches.append(text)
    external_dependency = raw.get("external_dependency") is True
    manual_only = raw.get("manual_only") is True
    rule_based = raw.get("rule_based")
    rule_based = rule_based if isinstance(rule_based, bool) else None
    if rule_based is None:
        flags.append("RULE_BASED_MISSING")
    human_approval_required = raw.get("human_approval_required") is True
    shadow_runs = raw.get("shadow_runs")
    shadow_runs = shadow_runs if isinstance(shadow_runs, int) and not isinstance(shadow_runs, bool) and shadow_runs > 0 else None

    # evidence sufficiency: all three of frequency / minutes / error rate must be
    # explicitly usable. Anything else is insufficient_evidence, never a guess.
    evidence_complete = (
        frequency in FREQUENCIES
        and minutes is not None
        and error_rate is not None
    )

    # cost resolution: step level first, then team default. Never converted.
    if cost is not None:
        effective_cost, effective_currency, cost_source = cost, cost_currency, "STEP"
    elif default_cost is not None:
        effective_cost, effective_currency, cost_source = default_cost, default_currency, "TEAM_DEFAULT"
    else:
        effective_cost, effective_currency, cost_source = None, None, "MISSING"

    monthly_hours = None
    if minutes is not None and runs is not None:
        monthly_hours = quant(minutes * runs / Decimal(60), 2)
    monthly_saving_hours = None
    if monthly_hours is not None and ratio is not None:
        monthly_saving_hours = quant(monthly_hours * ratio, 2)
    monthly_saving_amount = None
    if monthly_saving_hours is not None and effective_cost is not None and effective_currency is not None:
        monthly_saving_amount = quant(monthly_saving_hours * effective_cost, 2)

    money_missing = []
    if monthly_hours is None:
        money_missing.append("MONTHLY_HOURS")
    if ratio is None:
        money_missing.append("ASSUMED_SAVING_RATIO")
    if effective_cost is None:
        money_missing.append("HOURLY_COST")
    if effective_currency is None:
        money_missing.append("HOURLY_COST_CURRENCY")

    return {
        "process_id": process_id, "process_name": process_name, "process_owner": process_owner,
        "path": "processes[%d]/steps[%d]" % (p_index, s_index),
        "step_id": step_id, "name": name, "frequency": frequency,
        "minutes_per_run": minutes, "monthly_runs": runs, "error_rate_pct": error_rate,
        "assumed_saving_ratio": ratio, "error_consequence": consequence,
        "data_sensitivity": sensitivity, "touches": sorted(touches),
        "unknown_touches": sorted(unknown_touches), "external_dependency": external_dependency,
        "manual_only": manual_only, "rule_based": rule_based,
        "human_approval_required": human_approval_required, "shadow_runs": shadow_runs,
        "notes": clean_text(raw.get("notes")) if has_text(raw.get("notes")) else "",
        "inputs": _string_list(raw.get("inputs")), "outputs": _string_list(raw.get("outputs")),
        "systems": _string_list(raw.get("systems")),
        "evidence_complete": evidence_complete,
        "effective_cost": effective_cost, "effective_currency": effective_currency,
        "cost_source": cost_source,
        "monthly_hours": monthly_hours, "monthly_saving_hours": monthly_saving_hours,
        "monthly_saving_amount": monthly_saving_amount, "money_missing": money_missing,
        "review_flags": sorted(set(flags)),
        "verdict": None, "verdict_reason": None, "score": None, "rank": None,
    }


def _string_list(value):
    out = []
    if isinstance(value, list):
        for item in value:
            text = clean_text(item) if isinstance(item, str) else ""
            if text and text not in out:
                out.append(text)
    return out


def _decide(step):
    """Deterministic verdict. Order matters and is documented in references/guide.md."""
    forced = []
    for touch in step["touches"]:
        if touch in HIGH_RISK_TOUCHES:
            forced.append(touch)
    if step["unknown_touches"]:
        forced.append("UNKNOWN_TOUCH")
    if step["data_sensitivity"] in PROTECTED_SENSITIVITY:
        forced.append(step["data_sensitivity"])
    if step["error_consequence"] == "high":
        forced.append("high_error_consequence")
    if step["human_approval_required"]:
        forced.append("human_approval_required")

    if not step["evidence_complete"]:
        step["verdict"] = VERDICT_INSUFFICIENT
        step["verdict_reason"] = ("单次耗时、运行频率或错误率缺失，证据不足：不判断优先级、"
                                  "不建议自动化，先补数据。")
        return
    if step["manual_only"]:
        step["verdict"] = VERDICT_KEEP_MANUAL
        step["verdict_reason"] = "你已声明该步骤必须人工完成。"
        return
    if step["frequency"] == "ad_hoc":
        step["verdict"] = VERDICT_KEEP_MANUAL
        step["verdict_reason"] = ("频率不稳定（ad_hoc）：流程本身还没定型，先保持人工，"
                                  "等它稳定后再盘点。")
        return
    if forced:
        step["verdict"] = VERDICT_ASSIST
        step["verdict_reason"] = ("证据完整，但涉及高风险动作或高敏感数据（%s）："
                                  "只适合辅助，必须保留人工审批，不得无人值守执行。"
                                  % "、".join(sorted(set(forced))))
        return
    if step["external_dependency"]:
        step["verdict"] = VERDICT_ASSIST
        step["verdict_reason"] = "证据完整，但存在外部依赖：先确认对端，短期只做辅助。"
        return
    if step["rule_based"] is True and step["data_sensitivity"] in ("public", "internal", "customer_pii"):
        step["verdict"] = VERDICT_AUTOMATE
        step["verdict_reason"] = ("证据完整、有明确规则、错误后果可控且不涉及高风险动作："
                                  "可以作为自动化候选，先并行人工复核。")
        return
    step["verdict"] = VERDICT_ASSIST
    step["verdict_reason"] = ("证据完整，但没有声明该步骤遵循固定规则："
                              "只适合辅助，不能直接判为可自动化。")


def step_view(step):
    return {
        "process_id": step["process_id"], "process_name": step["process_name"],
        "step_id": step["step_id"], "name": step["name"],
        "frequency": step["frequency"],
        "minutes_per_run": amount(step["minutes_per_run"]),
        "monthly_runs": amount(step["monthly_runs"]),
        "error_rate_pct": amount(step["error_rate_pct"]),
        "error_consequence": step["error_consequence"],
        "data_sensitivity": step["data_sensitivity"],
        "touches": list(step["touches"]),
        "unknown_touches": list(step["unknown_touches"]),
        "external_dependency": step["external_dependency"],
        "manual_only": step["manual_only"],
        "rule_based": step["rule_based"],
        "human_approval_required": step["human_approval_required"],
        "systems": list(step["systems"]),
        "verdict": step["verdict"], "verdict_reason": step["verdict_reason"],
        "monthly_hours": amount(step["monthly_hours"]),
        "score": amount(step["score"]), "rank": step["rank"],
        "review_flags": list(step["review_flags"]),
    }


VERDICT_LABEL = {
    VERDICT_AUTOMATE: "可自动化候选",
    VERDICT_ASSIST: "只适合辅助（需人工审批）",
    VERDICT_KEEP_MANUAL: "保持人工",
    VERDICT_INSUFFICIENT: "证据不足（先补数据）",
}


def render_markdown(status, as_of, team_name, team_size, default_cost, default_currency,
                    processes, steps, ranked, unranked_reasons, order, prerequisites,
                    checkpoints, fallbacks, currency_view, counts, warnings, injection_flagged):
    lines = []
    lines.append("# SOP 自动化机会地图 — %s" % esc(team_name or "未命名团队"))
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 团队规模：%s ／ 默认小时成本：%s %s" % (
        team_size if team_size is not None else "未提供",
        amount(default_cost) if default_cost is not None else "未提供",
        default_currency or ""))
    lines.append("- 生成基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 流程 %d 个 ／ 步骤 %d 个 ／ 可自动化候选 %d ／ 只适合辅助 %d ／ 保持人工 %d ／ 证据不足 %d" % (
        counts["processes"], counts["steps"],
        counts["verdict_counts"][VERDICT_AUTOMATE],
        counts["verdict_counts"][VERDICT_ASSIST],
        counts["verdict_counts"][VERDICT_KEEP_MANUAL],
        counts["verdict_counts"][VERDICT_INSUFFICIENT]))
    lines.append("")

    lines.append("## 逐步骤判定")
    lines.append("")
    if steps:
        lines.append("| 流程 | 步骤 | 内容 | 频率 | 单次耗时 | 月均次数 | 错误率 | 判定 | 备注 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for step in steps:
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                esc(step["process_id"]) if step["process_id"] else "未提供",
                esc(step["step_id"]) if step["step_id"] else "未提供",
                hidden(step["name"]) if step["name"] else "未填写",
                esc(step["frequency"]) if step["frequency"] else "**未填写**",
                ("%s 分钟" % trim(step["minutes_per_run"])) if step["minutes_per_run"] is not None else "**未填写**",
                trim(step["monthly_runs"]) if step["monthly_runs"] is not None else "**未填写**",
                ("%s%%" % trim(step["error_rate_pct"])) if step["error_rate_pct"] is not None else "**未填写**",
                VERDICT_LABEL[step["verdict"]],
                hidden(step["notes"]) if step["notes"] else "—"))
    else:
        lines.append("- 未提供步骤")
    lines.append("")

    lines.append("## 优先级矩阵（仅限证据完整的辅助 / 可自动化步骤）")
    lines.append("")
    if ranked:
        lines.append("| 排序 | 步骤 | 内容 | 判定 | 月耗时 | 分值 |")
        lines.append("|---:|---|---|---|---:|---:|")
        for step in ranked:
            lines.append("| %d | %s | %s | %s | %s | %s |" % (
                step["rank"], esc(step["step_id"]) if step["step_id"] else "未提供",
                hidden(step["name"]) if step["name"] else "未填写",
                VERDICT_LABEL[step["verdict"]],
                amount(step["monthly_hours"]), amount(step["score"])))
        lines.append("")
        lines.append("> 分值 = 月耗时(小时，保留两位) × 10 + 错误率(%%)。分数高只代表「值得先看」，不代表收益。")
    else:
        lines.append("- 没有可排序的步骤")
    lines.append("")

    if unranked_reasons:
        lines.append("### 未排序步骤与原因")
        lines.append("")
        for entry in unranked_reasons:
            lines.append("- `%s`（%s）：%s —— %s" % (
                esc(entry["step_id"]) if entry["step_id"] else "未提供",
                VERDICT_LABEL[entry["verdict"]],
                esc("、".join(entry["reason"])),
                esc(entry["note"])))
        lines.append("")

    lines.append("## 建议实施顺序")
    lines.append("")
    if order:
        for entry in order:
            lines.append("%d. `%s` %s —— %s" % (
                entry["order"], esc(entry["step_id"]) if entry["step_id"] else "未提供",
                hidden(entry["name"]) if entry["name"] else "未填写",
                VERDICT_LABEL[entry["verdict"]]))
        lines.append("")
        lines.append("> 顺序是「先做可自动化候选，再做辅助，再处理保持人工与证据不足」；"
                     "同一档内按分值从高到低。")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 节省时间假设（只使用你显式提供的数字）")
    lines.append("")
    if currency_view:
        for currency in sorted(currency_view):
            lines.append("- %s：按你的假设，每月可省 **%s %s**（不同币种不合计）" % (
                esc(currency), currency_view[currency], esc(currency)))
    else:
        lines.append("- 无法给出金额：缺少月均次数、节省比例或小时成本中的至少一项。")
        lines.append("")
        lines.append("  缺失明细见 `time_saving_hypothesis.missing_inputs`。")
    lines.append("")

    lines.append("## 数据与权限前置项")
    lines.append("")
    if prerequisites:
        for entry in prerequisites:
            lines.append("- `%s`：" % (esc(entry["step_id"]) if entry["step_id"] else "未提供"))
            for item in entry["items"]:
                lines.append("  - %s" % esc(item))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 人工检查点")
    lines.append("")
    if checkpoints:
        lines.append("| 步骤 | 判定 | 模式 | 触发原因 | 要求 |")
        lines.append("|---|---|---|---|---|")
        for entry in checkpoints:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(entry["step_id"]) if entry["step_id"] else "未提供",
                VERDICT_LABEL[entry["verdict"]], esc(entry["mode"]),
                esc("、".join(entry["reasons"])), esc(entry["action"])))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 失败回退")
    lines.append("")
    for entry in fallbacks:
        lines.append("- `%s`：%s" % (
            esc(entry["step_id"]) if entry["step_id"] else "未提供", esc(entry["fallback"])))
    lines.append("")

    lines.append("## 本工具不做什么")
    lines.append("")
    for limit in TOOL_LIMITS:
        lines.append("- %s" % esc(limit))
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
