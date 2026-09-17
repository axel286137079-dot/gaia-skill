#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""接单范围与报价风险预检（只读 / 确定性 / 不联网 / 仅标准库）。

在接下创意或外包订单之前，把客户要求的使用范围、授权条款与商务条件，对照创作者
自己的价目表（rate_card）与自设阈值（policy）做一次离线预检，产出风险清单和一份
要发回客户的问题列表。

用法：
    python3 scripts/run.py <input.json>

输出：JSON（indent=2, ensure_ascii=False）写到 stdout，退出码 0。
结构性错误：抛出 ValueError（中文提示），CLI 下打印到 stderr 并以退出码 1 结束。

约束：
- 所有金额与百分比运算只用 decimal.Decimal + ROUND_HALF_UP，绝不用 float。
- 不使用网络，不读取输入文件之外的任何路径。
- 不发明价格，不下法律结论，缺失值一律保留 unknown，绝不套用默认值。
"""

import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# ---------------------------------------------------------------------------
# 固定枚举与常量
# ---------------------------------------------------------------------------

USAGE_VOCAB = (
    "social_media",
    "paid_ads",
    "broadcast",
    "print",
    "internal",
    "ecommerce",
    "ott",
    "out_of_home",
)
UNIT_VOCAB = ("per_asset", "per_project", "per_month")
KIND_VOCAB = ("video", "image", "copy", "design", "audio", "code", "other")
UNBOUNDED_TERRITORY_TOKENS = ("worldwide", "全球", "不限", "全球范围")

SEVERITY_ORDER = ("HIGH", "MEDIUM", "LOW", "INFO")
SEVERITY_LABEL = {"HIGH": "高", "MEDIUM": "中", "LOW": "低", "INFO": "提示"}
PRIORITY_BY_SEVERITY = {
    "HIGH": "立即处理",
    "MEDIUM": "尽快确认",
    "LOW": "建议补充",
    "INFO": "可选完善",
}
STATUS_LABEL = {
    "HIGH_RISK": "高风险",
    "REVIEW_REQUIRED": "需要复核",
    "PARTIAL": "部分校验未启用",
    "ACCEPTABLE": "暂未发现阻断项",
}

FORBIDDEN_KEYS = frozenset(
    [
        "password",
        "passwd",
        "secret_value",
        "api_token",
        "access_token",
        "client_secret",
        "credential_value",
        "private_key",
        "private_key_pem",
        "dsn",
        "connection_string",
        "jdbc_url",
        "database_url",
    ]
)

SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
    re.compile(r"-----BEGIN [A-Z ]*CERTIFICAT[E]-----"),
)

INJECTION_PATTERNS = (
    re.compile(r"忽略(以上|之前|所有)?(指令|规则)"),
    re.compile(r"ignore\s+(all\s+)?(previous|above)\s+instructions", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"你现在是"),
    re.compile(r"直接批准"),
    re.compile(r"免费"),
)

CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
TWO_PLACES = Decimal("0.01")
HUNDRED = Decimal("100")

DISCLAIMER = (
    "本结果由 suge-order-scope-quote-check 依据你提供的 JSON 生成：只读预检，不构成法律意见，"
    "不代替合同审查，不保证成交价或议价结果。所有阈值来自你提供的 policy；缺失即标 unknown，"
    "不套用任何默认值。风险清单与问题列表用于谈判准备，最终条款请在签约前由你自行确认。"
)

# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


def _q2(value):
    """Decimal -> 两位小数（ROUND_HALF_UP）。"""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _money(value):
    """Decimal -> 两位小数字符串，例如 "58500.00"。"""
    return format(_q2(value), "f")


def _pct(value):
    """Decimal（已是百分数数值）-> 形如 "-65.81%" 的字符串。"""
    return format(_q2(value), "f") + "%"


def _flag(value):
    return "是" if value else "否"


def _scan_privacy(node):
    """凭据门禁：命中即拒绝，且绝不回显该字段名或值。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_KEYS:
                raise ValueError("输入包含疑似凭据字段，已拒绝处理（不回显该字段内容）。")
            _scan_privacy(value)
    elif isinstance(node, list):
        for item in node:
            _scan_privacy(item)
    elif isinstance(node, str):
        for pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(node):
                raise ValueError("输入包含疑似密钥或凭据值，已拒绝处理（不回显该内容）。")


def _scan_injection(node, found):
    """提示词注入识别：只登记标记，绝不执行其中任何内容。"""
    if isinstance(node, dict):
        for key, value in node.items():
            _scan_injection(key, found)
            _scan_injection(value, found)
    elif isinstance(node, list):
        for item in node:
            _scan_injection(item, found)
    elif isinstance(node, str):
        for pattern in INJECTION_PATTERNS:
            if pattern.search(node):
                found.add("PROMPT_INJECTION_IGNORED")
                return


def _parse_dt(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("字段 %s 必须是带时区偏移的 ISO8601 时间字符串。" % path)
    raw = value.strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError("字段 %s 不是合法的 ISO8601 时间。" % path)
    if parsed.utcoffset() is None:
        raise ValueError("字段 %s 必须带时区偏移（例如 +08:00）。" % path)
    return parsed


def _parse_date(value, path):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("字段 %s 必须是日期字符串（YYYY-MM-DD）。" % path)
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    return _parse_dt(text, path).date()


def _member(container, key):
    if isinstance(container, dict):
        return container.get(key)
    return None


def _as_decimal(value, path):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("字段 %s 必须是十进制数字字符串。" % path)
    text = value.strip() if isinstance(value, str) else str(value)
    if not text:
        raise ValueError("字段 %s 不能为空字符串。" % path)
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        raise ValueError("字段 %s 不是合法的十进制数字。" % path)
    if not parsed.is_finite():
        raise ValueError("字段 %s 必须是有限数字。" % path)
    return parsed


def _opt_decimal(container, key, path):
    value = _member(container, key)
    if value is None:
        return None
    return _as_decimal(value, path)


def _opt_int(container, key, path):
    value = _member(container, key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("字段 %s 必须是整数。" % path)
    return value


def _opt_bool(container, key, path):
    value = _member(container, key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError("字段 %s 必须是布尔值。" % path)
    return value


def _tier_flag(container, key):
    """价目表档位上的权利标记：缺失或 null 一律按 false 读。"""
    value = _member(container, key)
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError("字段 rate_card[].%s 必须是布尔值。" % key)
    return value


def _opt_text(container, key, path, maximum=400):
    value = _member(container, key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("字段 %s 必须是字符串。" % path)
    text = value.strip()
    if not text:
        return None
    if len(text) > maximum:
        raise ValueError("字段 %s 超过长度上限。" % path)
    return text


def _required_text(container, key, path, maximum=200):
    text = _opt_text(container, key, path, maximum)
    if text is None:
        raise ValueError("缺少必填字段 %s（需为非空字符串）。" % path)
    return text


# ---------------------------------------------------------------------------
# 风险条目
# ---------------------------------------------------------------------------


def _finding(code, severity, title, evidence, question, term):
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "evidence": evidence,
        "question_to_client": question,
        "suggested_term": term,
        "blocking": severity == "HIGH",
    }


ACTION_TEXT = {
    "MISSING_TIER": "先确认按哪一档计价，或补全价目表中的对应档位后再回复客户。",
    "TIER_MEDIA_MISMATCH": "为未覆盖的使用媒介单独设定档位或加价条款。",
    "TIER_TERM_MISMATCH": "把档位期限与订单期限对齐，或改按更长/更短期限重新计价。",
    "TIER_EXCLUSIVITY_MISMATCH": "独家授权需要单独的独家档位或明确的独家溢价。",
    "TIER_FLAG_MISMATCH": "逐项为订单要求的权利（AI 训练 / 转授权 / 买断）补上计价口径。",
    "AI_TRAINING_UNPRICED": "先给 AI 训练用途定价或明确书面排除，再确认订单。",
    "SUBLICENSE_UNPRICED": "明确转授权是否允许，并给出转授权的单独价格。",
    "BUYOUT_WITHOUT_PREMIUM": "买断必须对应单独档位或明确溢价，不接受按普通授权价出货。",
    "UNLIMITED_TERM": "把授权期限写成明确的月数，拒绝无限期表述。",
    "TERM_OVER_LIMIT": "把期限压回自设上限之内，或由你本人重新核定上限。",
    "TERRITORY_UNBOUNDED": "把授权地域写成具体国家/地区列表。",
    "MEDIA_UNSPECIFIED": "让客户写清使用媒介列表。",
    "NO_DEPOSIT": "约定不低于自设下限的预付款比例，款到开工。",
    "DEPOSIT_BELOW_MIN": "把预付款比例提高到自设下限之上。",
    "PAYMENT_TERMS_MISSING": "补充付款节点（里程碑、比例、触发条件）。",
    "NO_KILL_FEE": "补充终止/弃单费条款，覆盖已投入工时。",
    "OPEN_REVISIONS": "给每个交付物写明包含的修改轮次上限。",
    "REVISIONS_OVER_LIMIT": "把修改轮次压回自设上限，超出部分按额外修改计价。",
    "ASSET_HANDOVER_UNDEFINED": "明确原始素材与源文件是否交付，以及额外收费口径。",
    "NO_REQUEST_WINDOW": "明确需求确认到交付所需的最短自然日，并写进订单。",
    "EXPENSES_UNBOUNDED": "给差旅、场地、素材等实报实销费用设定上限或封顶方式。",
    "NO_LATE_FEE": "补充逾期付款的处理方式（如逾期比例或暂停交付）。",
    "CREDIT_TERM_UNDEFINED": "明确是否需要署名以及署名形式。",
    "DELIVERY_FORMAT_UNDEFINED": "写明交付格式、分辨率与封装要求。",
    "REVISION_RATE_MISSING": "给出超出轮次后的单轮修改单价。",
    "PAST_DUE": "把交付时间改到评估时点之后，或改为明确的分批交付计划。",
    "LEAD_TIME_TIGHT": "把交付时间后移，或缩小范围，使其不短于自设需求窗口。",
    "EXPIRED_TIER": "更新已过期档位的价格与条款后再引用。",
    "CURRENCY_MISMATCH": "统一币种，或明确汇率与换汇成本由谁承担。",
    "PRICE_BELOW_TIER": "按档位应计金额重新报价，或削减交付量与使用范围。",
}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def evaluate(data):
    """对一份预检输入做完整评估，返回输出字典；结构性错误抛 ValueError。"""
    if not isinstance(data, dict):
        raise ValueError("输入 JSON 顶层必须是对象（object）。")

    _scan_privacy(data)
    injection = set()
    _scan_injection(data, injection)

    # --- 顶层 ---------------------------------------------------------------
    as_of = _parse_dt(_member(data, "as_of"), "as_of")

    top_currency = _member(data, "currency")
    if top_currency is not None:
        if not isinstance(top_currency, str) or not CURRENCY_RE.match(top_currency.strip()):
            raise ValueError("字段 currency 必须是 3 位大写字母的 ISO4217 代码。")

    policy_raw = _member(data, "policy")
    if policy_raw is not None and not isinstance(policy_raw, dict):
        raise ValueError("字段 policy 必须是对象。")
    policy = policy_raw if isinstance(policy_raw, dict) else {}
    p_max_term = _opt_int(policy, "max_term_months", "policy.max_term_months")
    p_min_deposit = _opt_decimal(policy, "min_deposit_pct", "policy.min_deposit_pct")
    p_max_revisions = _opt_int(policy, "max_revision_rounds", "policy.max_revision_rounds")
    p_require_kill_fee = _opt_bool(policy, "require_kill_fee", "policy.require_kill_fee")
    if p_max_term is not None and p_max_term < 0:
        raise ValueError("字段 policy.max_term_months 不能为负数。")
    if p_max_revisions is not None and p_max_revisions < 0:
        raise ValueError("字段 policy.max_revision_rounds 不能为负数。")

    # --- 价目表 -------------------------------------------------------------
    rate_raw = _member(data, "rate_card")
    if rate_raw is not None and not isinstance(rate_raw, list):
        raise ValueError("字段 rate_card 必须是数组。")
    tiers = []
    seen_tier_ids = set()
    for index, raw_tier in enumerate(rate_raw or []):
        path = "rate_card[%d]" % index
        if not isinstance(raw_tier, dict):
            raise ValueError("字段 %s 必须是对象。" % path)
        tier_id = _required_text(raw_tier, "tier_id", path + ".tier_id")
        if tier_id in seen_tier_ids:
            raise ValueError("rate_card 中存在重复的 tier_id，价目表档位必须唯一。")
        seen_tier_ids.add(tier_id)
        usage = _member(raw_tier, "usage")
        if not isinstance(usage, str) or usage.strip() not in USAGE_VOCAB:
            raise ValueError(
                "字段 %s.usage 必须是固定词表中的使用范围之一：%s。"
                % (path, "/".join(USAGE_VOCAB))
            )
        unit = _opt_text(raw_tier, "unit", path + ".unit", 40)
        if unit is not None and unit not in UNIT_VOCAB:
            raise ValueError(
                "字段 %s.unit 必须是 %s 之一。" % (path, "/".join(UNIT_VOCAB))
            )
        tiers.append(
            {
                "tier_id": tier_id,
                "usage": usage.strip(),
                "term_months": _opt_int(raw_tier, "term_months", path + ".term_months"),
                "exclusive": _tier_flag(raw_tier, "exclusive"),
                "buyout": _tier_flag(raw_tier, "buyout"),
                "ai_training": _tier_flag(raw_tier, "ai_training"),
                "sublicense": _tier_flag(raw_tier, "sublicense"),
                "price": _as_decimal(_member(raw_tier, "price"), path + ".price"),
                "currency": _required_text(raw_tier, "currency", path + ".currency", 8),
                "unit": unit,
                "effective_from": _opt_text(
                    raw_tier, "effective_from", path + ".effective_from", 40
                ),
                "expires_at": _opt_text(raw_tier, "expires_at", path + ".expires_at", 40),
            }
        )
    tier_by_id = {}
    for tier in tiers:
        tier_by_id[tier["tier_id"]] = tier

    # --- 订单 ---------------------------------------------------------------
    order = _member(data, "order")
    if not isinstance(order, dict):
        raise ValueError("缺少必填字段 order（必须是对象）。")
    order_id = _required_text(order, "order_id", "order.order_id")
    client_ref = _opt_text(order, "client_ref", "order.client_ref", 200)

    deliverables_raw = _member(order, "deliverables")
    if not isinstance(deliverables_raw, list) or not deliverables_raw:
        raise ValueError("缺少必填字段 order.deliverables 或该列表为空。")
    deliverables = []
    seen_deliverable_ids = set()
    total_quantity = 0
    for index, raw_item in enumerate(deliverables_raw):
        path = "order.deliverables[%d]" % index
        if not isinstance(raw_item, dict):
            raise ValueError("字段 %s 必须是对象。" % path)
        deliverable_id = _required_text(raw_item, "deliverable_id", path + ".deliverable_id")
        if deliverable_id in seen_deliverable_ids:
            raise ValueError("order.deliverables 中存在重复的 deliverable_id，交付物必须唯一。")
        seen_deliverable_ids.add(deliverable_id)
        quantity = _opt_int(raw_item, "quantity", path + ".quantity")
        if quantity is None or quantity < 1:
            raise ValueError("字段 %s.quantity 必须是大于等于 1 的整数。" % path)
        kind = _opt_text(raw_item, "kind", path + ".kind", 40)
        if kind is not None and kind not in KIND_VOCAB:
            raise ValueError("字段 %s.kind 必须是 %s 之一。" % (path, "/".join(KIND_VOCAB)))
        due_at_raw = _member(raw_item, "due_at")
        due_at = None
        if due_at_raw is not None:
            due_at = _parse_dt(due_at_raw, path + ".due_at")
        deliverables.append(
            {
                "deliverable_id": deliverable_id,
                "kind": kind,
                "quantity": quantity,
                "duration_seconds": _opt_int(
                    raw_item, "duration_seconds", path + ".duration_seconds"
                ),
                "revisions_included": _opt_int(
                    raw_item, "revisions_included", path + ".revisions_included"
                ),
                "due_at": due_at,
                "due_at_raw": due_at_raw if isinstance(due_at_raw, str) else None,
            }
        )
        total_quantity += quantity

    usage_raw = _member(order, "usage")
    if not isinstance(usage_raw, dict):
        raise ValueError("缺少必填字段 order.usage（必须是对象）。")
    media_raw = _member(usage_raw, "media")
    if not isinstance(media_raw, list) or not media_raw:
        raise ValueError("order.usage.media 必须是至少含一项的数组。")
    media = []
    for index, item in enumerate(media_raw):
        if not isinstance(item, str) or item.strip() not in USAGE_VOCAB:
            raise ValueError(
                "字段 order.usage.media[%d] 必须是固定词表中的使用范围之一：%s。"
                % (index, "/".join(USAGE_VOCAB))
            )
        media.append(item.strip())

    tier_id = _opt_text(usage_raw, "tier_id", "order.usage.tier_id", 200)
    territory_raw = _member(usage_raw, "territory")
    territory = None
    if territory_raw is not None:
        if not isinstance(territory_raw, list):
            raise ValueError("字段 order.usage.territory 必须是数组或 null。")
        territory = []
        for index, item in enumerate(territory_raw):
            if not isinstance(item, str):
                raise ValueError("字段 order.usage.territory[%d] 必须是字符串。" % index)
            territory.append(item.strip())
    term_months = _opt_int(usage_raw, "term_months", "order.usage.term_months")
    if term_months is not None and term_months < 0:
        raise ValueError("字段 order.usage.term_months 不能为负数。")
    exclusive = _opt_bool(usage_raw, "exclusive", "order.usage.exclusive")
    sublicense = _opt_bool(usage_raw, "sublicense", "order.usage.sublicense")
    buyout = _opt_bool(usage_raw, "buyout", "order.usage.buyout")
    ai_training = _opt_bool(usage_raw, "ai_training", "order.usage.ai_training")
    credit_required = _opt_bool(usage_raw, "credit_required", "order.usage.credit_required")

    commercial = _member(order, "commercial")
    if not isinstance(commercial, dict):
        raise ValueError("缺少必填字段 order.commercial（必须是对象）。")
    total_price = _as_decimal(
        _member(commercial, "total_price"), "order.commercial.total_price"
    )
    commercial_currency = _required_text(
        commercial, "currency", "order.commercial.currency", 8
    )
    if not CURRENCY_RE.match(commercial_currency):
        raise ValueError("字段 order.commercial.currency 必须是 3 位大写字母代码。")
    deposit_pct = _opt_decimal(commercial, "deposit_pct", "order.commercial.deposit_pct")
    payment_terms_raw = _member(commercial, "payment_terms")
    if payment_terms_raw is not None and not isinstance(payment_terms_raw, list):
        raise ValueError("字段 order.commercial.payment_terms 必须是数组或 null。")
    payment_terms = payment_terms_raw or []
    for index, item in enumerate(payment_terms):
        if not isinstance(item, dict):
            raise ValueError("字段 order.commercial.payment_terms[%d] 必须是对象。" % index)
        _opt_decimal(item, "pct", "order.commercial.payment_terms[%d].pct" % index)
    expenses_cap = _opt_decimal(commercial, "expenses_cap", "order.commercial.expenses_cap")
    kill_fee_pct = _opt_decimal(commercial, "kill_fee_pct", "order.commercial.kill_fee_pct")
    late_fee_pct = _opt_decimal(commercial, "late_fee_pct", "order.commercial.late_fee_pct")
    revision_rate = _opt_decimal(
        commercial, "revision_rate", "order.commercial.revision_rate"
    )

    constraints_raw = _member(order, "constraints")
    if constraints_raw is not None and not isinstance(constraints_raw, dict):
        raise ValueError("字段 order.constraints 必须是对象。")
    constraints = constraints_raw if isinstance(constraints_raw, dict) else {}
    request_window_days = _opt_int(
        constraints, "request_window_days", "order.constraints.request_window_days"
    )
    if request_window_days is not None and request_window_days < 0:
        raise ValueError("字段 order.constraints.request_window_days 不能为负数。")
    raw_files_included = _opt_bool(
        constraints, "raw_files_included", "order.constraints.raw_files_included"
    )
    source_files_included = _opt_bool(
        constraints, "source_files_included", "order.constraints.source_files_included"
    )
    delivery_format = _opt_text(
        constraints, "delivery_format", "order.constraints.delivery_format", 200
    )

    notes = _opt_text(order, "notes", "order.notes", 2000)
    if notes is None:
        notes = _opt_text(data, "notes", "notes", 2000)

    # --- 命名档位与金额前置 -------------------------------------------------
    named = tier_by_id.get(tier_id) if tier_id else None
    skip_price = not tier_id or not tiers or named is None
    if not skip_price and commercial_currency != named["currency"]:
        skip_price = True

    expected_total = None
    difference = None
    gap_pct = None
    if not skip_price:
        expected_total = named["price"] * Decimal(total_quantity)
        if expected_total == 0:
            skip_price = True
        else:
            difference = total_price - expected_total
            gap_pct = difference / expected_total * HUNDRED

    checks_skipped = set()
    if skip_price:
        checks_skipped.add("PRICE_COMPARISON")
    if request_window_days is None or not any(
        item["due_at"] is not None for item in deliverables
    ):
        checks_skipped.add("LEAD_TIME")
    if p_max_term is None:
        checks_skipped.add("TERM_LIMIT")
    if p_min_deposit is None:
        checks_skipped.add("DEPOSIT_MINIMUM")
    if p_max_revisions is None:
        checks_skipped.add("REVISION_LIMIT")
    if p_require_kill_fee is None:
        checks_skipped.add("KILL_FEE_REQUIREMENT")

    # --- 风险条目（固定顺序） -----------------------------------------------
    findings = []

    if tier_id and named is None:
        findings.append(
            _finding(
                "MISSING_TIER",
                "HIGH",
                "报价档位不存在于价目表",
                {
                    "order.usage.tier_id": tier_id,
                    "rate_card.tier_ids": [t["tier_id"] for t in tiers],
                },
                "订单指定按档位「%s」计价，但你的价目表里没有这一档，请问这单按哪一档、什么价格计？"
                % tier_id,
                "先确认计价档位并写入订单，或补全自设价目表后再回复客户。",
            )
        )

    if named is not None:
        uncovered = [item for item in media if item != named["usage"]]
        if uncovered:
            findings.append(
                _finding(
                    "TIER_MEDIA_MISMATCH",
                    "HIGH",
                    "档位未覆盖全部使用媒介",
                    {
                        "order.usage.tier_id": tier_id,
                        "rate_card.usage": named["usage"],
                        "order.usage.media": list(media),
                        "uncovered_media": uncovered,
                    },
                    "订单要求的使用媒介包含 %s，但你选用的档位只覆盖 %s，这部分使用是否另行计价？"
                    % ("、".join(uncovered), named["usage"]),
                    "为未覆盖的媒介单独设定档位或加价条款，不接受一价全包。",
                )
            )

        if (
            named["term_months"] is not None
            and term_months is not None
            and named["term_months"] != term_months
        ):
            findings.append(
                _finding(
                    "TIER_TERM_MISMATCH",
                    "HIGH",
                    "档位期限与订单期限不一致",
                    {
                        "rate_card.term_months": named["term_months"],
                        "order.usage.term_months": term_months,
                        "order.usage.tier_id": tier_id,
                    },
                    "订单的授权期限是 %d 个月，而你选用的档位对应 %d 个月，请问按哪个期限执行并计价？"
                    % (term_months, named["term_months"]),
                    "让档位期限与订单期限一致，或按实际期限重新核定价格。",
                )
            )

        if exclusive is True and named["exclusive"] is False:
            findings.append(
                _finding(
                    "TIER_EXCLUSIVITY_MISMATCH",
                    "HIGH",
                    "档位不含独家授权",
                    {
                        "order.usage.exclusive": True,
                        "rate_card.exclusive": False,
                        "order.usage.tier_id": tier_id,
                    },
                    "订单要求独家授权，但你选用的档位是非独家价，请问独家部分按什么价格补？",
                    "独家授权必须对应单独档位或明确的独家溢价。",
                )
            )

        mismatched_flags = []
        for flag_name in ("ai_training", "sublicense", "buyout"):
            order_value = {"ai_training": ai_training, "sublicense": sublicense, "buyout": buyout}[
                flag_name
            ]
            if order_value is True and named[flag_name] is False:
                mismatched_flags.append(flag_name)
        if mismatched_flags:
            findings.append(
                _finding(
                    "TIER_FLAG_MISMATCH",
                    "HIGH",
                    "档位不支持订单要求的权利项",
                    {
                        "order.usage.tier_id": tier_id,
                        "flags": mismatched_flags,
                        "order.usage.flags": {
                            "ai_training": ai_training,
                            "sublicense": sublicense,
                            "buyout": buyout,
                        },
                        "rate_card.flags": {
                            "ai_training": named["ai_training"],
                            "sublicense": named["sublicense"],
                            "buyout": named["buyout"],
                        },
                    },
                    "订单要求 %s，但你选用的档位这几项都标记为不支持，请问如何计价？"
                    % "、".join(mismatched_flags),
                    "逐项为这些权利补上计价口径，或书面排除。",
                )
            )

    if ai_training is True and not any(tier["ai_training"] for tier in tiers):
        findings.append(
            _finding(
                "AI_TRAINING_UNPRICED",
                "HIGH",
                "AI 训练用途未定价",
                {
                    "order.usage.ai_training": True,
                    "rate_card.ai_training_tiers": [
                        tier["tier_id"] for tier in tiers if tier["ai_training"]
                    ],
                },
                "订单包含 AI 训练用途，但你的价目表里没有任何档位覆盖这一项，请问这项按什么价格授权？",
                "先给 AI 训练用途定价或书面排除，再确认订单。",
            )
        )

    if sublicense is True and not any(tier["sublicense"] for tier in tiers):
        findings.append(
            _finding(
                "SUBLICENSE_UNPRICED",
                "MEDIUM",
                "转授权用途未定价",
                {
                    "order.usage.sublicense": True,
                    "rate_card.sublicense_tiers": [
                        tier["tier_id"] for tier in tiers if tier["sublicense"]
                    ],
                },
                "订单要求可转授权，但价目表里没有覆盖转授权的档位，请问转授权是否允许、按什么价格？",
                "明确转授权是否允许，并给出单独价格。",
            )
        )

    if buyout is True and not any(tier["buyout"] for tier in tiers):
        findings.append(
            _finding(
                "BUYOUT_WITHOUT_PREMIUM",
                "HIGH",
                "买断要求但价目表无买断档",
                {
                    "order.usage.buyout": True,
                    "rate_card.buyout_tiers": [
                        tier["tier_id"] for tier in tiers if tier["buyout"]
                    ],
                },
                "订单要求买断，但你的价目表里没有买断档位，请问买断总价是多少？",
                "买断必须对应单独档位或明确溢价。",
            )
        )

    if term_months is None:
        findings.append(
            _finding(
                "UNLIMITED_TERM",
                "HIGH",
                "授权期限未约定（无限期风险）",
                {"order.usage.term_months": None},
                "订单没有写授权期限，请问授权从交付起算持续多少个月？",
                "把期限写成明确的月数，拒绝永久或无限期表述。",
            )
        )

    if p_max_term is not None and term_months is not None and term_months > p_max_term:
        findings.append(
            _finding(
                "TERM_OVER_LIMIT",
                "HIGH",
                "授权期限超过自设上限",
                {
                    "order.usage.term_months": term_months,
                    "policy.max_term_months": p_max_term,
                },
                "订单要求 %d 个月授权，超过你自设的 %d 个月上限，请问是缩短期限还是按超期加价？"
                % (term_months, p_max_term),
                "把期限压回自设上限内，或由你本人重新核定上限。",
            )
        )

    unbounded_territory = territory is None or len(territory) == 0
    if not unbounded_territory:
        for item in territory:
            lowered = item.lower()
            if any(token in lowered for token in UNBOUNDED_TERRITORY_TOKENS):
                unbounded_territory = True
                break
    if unbounded_territory:
        findings.append(
            _finding(
                "TERRITORY_UNBOUNDED",
                "HIGH",
                "授权地域无边界",
                {"order.usage.territory": territory},
                "订单没有把授权地域限定到具体国家或地区，请问限定在哪些地区投放？",
                "把授权地域写成具体国家/地区列表。",
            )
        )

    if deposit_pct is None or deposit_pct == 0:
        findings.append(
            _finding(
                "NO_DEPOSIT",
                "HIGH",
                "未约定预付款",
                {"order.commercial.deposit_pct": None if deposit_pct is None else _money(deposit_pct)},
                "订单约定预付款为 %s，请问第一笔款什么时候到账、比例是多少？"
                % ("未知" if deposit_pct is None else "0%"),
                "约定不低于自设下限的预付款比例，款到开工。",
            )
        )

    if (
        p_min_deposit is not None
        and deposit_pct is not None
        and deposit_pct > 0
        and deposit_pct < p_min_deposit
    ):
        findings.append(
            _finding(
                "DEPOSIT_BELOW_MIN",
                "MEDIUM",
                "预付款低于自设下限",
                {
                    "order.commercial.deposit_pct": _money(deposit_pct),
                    "policy.min_deposit_pct": _money(p_min_deposit),
                },
                "客户给的预付款比例是 %s%%，低于你自设的 %s%% 下限，请问能否提高到下限？"
                % (_money(deposit_pct), _money(p_min_deposit)),
                "把预付款比例提高到自设下限之上。",
            )
        )

    if not payment_terms:
        findings.append(
            _finding(
                "PAYMENT_TERMS_MISSING",
                "MEDIUM",
                "付款节点缺失",
                {
                    "order.commercial.payment_terms": payment_terms if payment_terms else None,
                },
                "订单没有写付款节点，请问按什么里程碑、什么比例、什么时间付款？",
                "补充付款节点（里程碑、比例、触发条件）。",
            )
        )

    if kill_fee_pct is None and p_require_kill_fee is True:
        findings.append(
            _finding(
                "NO_KILL_FEE",
                "MEDIUM",
                "未约定终止/弃单费",
                {
                    "order.commercial.kill_fee_pct": None,
                    "policy.require_kill_fee": True,
                },
                "订单没有写客户中途取消时的终止费，请问取消后已投入的工时怎么结算？",
                "补充终止/弃单费条款，覆盖已投入工时。",
            )
        )

    open_revision_ids = [
        item["deliverable_id"] for item in deliverables if item["revisions_included"] is None
    ]
    if open_revision_ids:
        findings.append(
            _finding(
                "OPEN_REVISIONS",
                "MEDIUM",
                "修改轮次未设上限",
                {
                    "order.deliverables.revisions_included": [
                        {"deliverable_id": item["deliverable_id"], "revisions_included": None}
                        for item in deliverables
                        if item["revisions_included"] is None
                    ]
                },
                "有 %d 个交付物没有写包含几轮修改，请问每个交付物包含几轮、超出怎么收费？"
                % len(open_revision_ids),
                "给每个交付物写明包含的修改轮次上限。",
            )
        )

    if p_max_revisions is not None:
        over = [
            {"deliverable_id": item["deliverable_id"], "revisions_included": item["revisions_included"]}
            for item in deliverables
            if item["revisions_included"] is not None
            and item["revisions_included"] > p_max_revisions
        ]
        if over:
            findings.append(
                _finding(
                    "REVISIONS_OVER_LIMIT",
                    "LOW",
                    "修改轮次超过自设上限",
                    {
                        "policy.max_revision_rounds": p_max_revisions,
                        "order.deliverables.revisions_included": over,
                    },
                    "订单要求的修改轮次超过你自设的 %d 轮上限，超出部分按什么单价计？"
                    % p_max_revisions,
                    "把修改轮次压回自设上限，超出部分按额外修改计价。",
                )
            )

    if raw_files_included is None or source_files_included is None:
        findings.append(
            _finding(
                "ASSET_HANDOVER_UNDEFINED",
                "MEDIUM",
                "源文件/原始素材交付范围未约定",
                {
                    "order.constraints.raw_files_included": raw_files_included,
                    "order.constraints.source_files_included": source_files_included,
                },
                "订单没有写原始素材和源文件是否交付，请问这两项是否包含在内、额外收费多少？",
                "明确原始素材与源文件是否交付，以及额外收费口径。",
            )
        )

    if request_window_days is None:
        findings.append(
            _finding(
                "NO_REQUEST_WINDOW",
                "LOW",
                "未约定需求交付周期",
                {"order.constraints.request_window_days": None},
                "订单没有写从需求确认到交付需要多少天，请问你需要的排期是几天？",
                "明确需求确认到交付的最短自然日，并写进订单。",
            )
        )

    if expenses_cap is None:
        findings.append(
            _finding(
                "EXPENSES_UNBOUNDED",
                "LOW",
                "费用报销无上限",
                {"order.commercial.expenses_cap": None},
                "订单没有写差旅、场地、素材等费用的上限，请问实报实销是否封顶？",
                "给实报实销费用设定上限或封顶方式。",
            )
        )

    if late_fee_pct is None:
        findings.append(
            _finding(
                "NO_LATE_FEE",
                "LOW",
                "未约定逾期付款责任",
                {"order.commercial.late_fee_pct": None},
                "订单没有写逾期付款怎么处理，请问逾期后的处理方式是什么？",
                "补充逾期付款的处理方式。",
            )
        )

    if credit_required is None:
        findings.append(
            _finding(
                "CREDIT_TERM_UNDEFINED",
                "LOW",
                "署名要求未明确",
                {"order.usage.credit_required": None},
                "订单没有写是否需要署名，请问成片/成图是否需要署名，以什么形式？",
                "明确是否需要署名以及署名形式。",
            )
        )

    if delivery_format is None:
        findings.append(
            _finding(
                "DELIVERY_FORMAT_UNDEFINED",
                "INFO",
                "交付格式未定义",
                {"order.constraints.delivery_format": None},
                "订单没有写交付格式，请问要交付哪些格式、分辨率和封装要求？",
                "写明交付格式、分辨率与封装要求。",
            )
        )

    if revision_rate is None:
        findings.append(
            _finding(
                "REVISION_RATE_MISSING",
                "INFO",
                "额外修改单价缺失",
                {"order.commercial.revision_rate": None},
                "订单没有写超出轮次后的修改单价，请问单轮加改怎么收费？",
                "给出超出轮次后的单轮修改单价。",
            )
        )

    past_due = [
        {"deliverable_id": item["deliverable_id"], "due_at": item["due_at_raw"]}
        for item in deliverables
        if item["due_at"] is not None and item["due_at"] < as_of
    ]
    if past_due:
        findings.append(
            _finding(
                "PAST_DUE",
                "HIGH",
                "交付时间早于评估时点",
                {"as_of": as_of.isoformat(), "order.deliverables.due_at": past_due},
                "有 %d 个交付物的截止时间早于当前评估时点，请问交付时间是否需要重新约定？"
                % len(past_due),
                "把交付时间改到评估时点之后，或改为明确的分批交付计划。",
            )
        )

    if request_window_days is not None:
        due_values = [item["due_at"] for item in deliverables if item["due_at"] is not None]
        if due_values:
            earliest_due = min(due_values)
            lead_days = (earliest_due - as_of).days
            if lead_days < request_window_days:
                findings.append(
                    _finding(
                        "LEAD_TIME_TIGHT",
                        "MEDIUM",
                        "制作周期紧于自设需求窗口",
                        {
                            "order.constraints.request_window_days": request_window_days,
                            "order.deliverables.earliest_due_at": earliest_due.isoformat(),
                            "as_of": as_of.isoformat(),
                            "lead_days": lead_days,
                        },
                        "最早交付时间距现在只有 %d 天，短于你自设的 %d 天需求窗口，请问能否后移交付时间或缩减范围？"
                        % (lead_days, request_window_days),
                        "把交付时间后移，或缩小范围，使其不短于自设需求窗口。",
                    )
                )

    if named is not None and named["expires_at"] is not None:
        expires_at = _parse_date(named["expires_at"], "rate_card[].expires_at")
        if expires_at < as_of.date():
            findings.append(
                _finding(
                    "EXPIRED_TIER",
                    "MEDIUM",
                    "所选档位已过期",
                    {
                        "order.usage.tier_id": named["tier_id"],
                        "rate_card.expires_at": named["expires_at"],
                        "as_of": as_of.isoformat(),
                    },
                    "你选用的档位在 %s 已过期，请问这单按更新后的哪一档价格执行？"
                    % named["expires_at"],
                    "更新已过期档位的价格与条款后再引用。",
                )
            )

    if named is not None and commercial_currency != named["currency"]:
        findings.append(
            _finding(
                "CURRENCY_MISMATCH",
                "INFO",
                "订单币种与档位币种不一致",
                {
                    "order.commercial.currency": commercial_currency,
                    "rate_card.currency": named["currency"],
                },
                "订单以 %s 计价，你的档位以 %s 计价，请问汇率如何确定、换汇成本由谁承担？"
                % (commercial_currency, named["currency"]),
                "统一币种，或明确汇率与换汇成本承担方。",
            )
        )

    if (
        not skip_price
        and named is not None
        and named["unit"] == "per_asset"
        and total_price < expected_total
    ):
        findings.append(
            _finding(
                "PRICE_BELOW_TIER",
                "HIGH",
                "报价低于档位应计金额",
                {
                    "order.usage.tier_id": named["tier_id"],
                    "rate_card.unit": named["unit"],
                    "rate_card.price": _money(named["price"]),
                    "total_quantity": total_quantity,
                    "expected_total": _money(expected_total),
                    "quoted_total": _money(total_price),
                    "difference": _money(difference),
                    "order.commercial.currency": commercial_currency,
                },
                "按你自设档位单价 %s %s × %d 件，应计 %s %s；客户只给 %s %s，缺口 %s，请问按哪一版报价执行？"
                % (
                    named["currency"],
                    _money(named["price"]),
                    total_quantity,
                    named["currency"],
                    _money(expected_total),
                    named["currency"],
                    _money(total_price),
                    _money(difference),
                ),
                "按档位应计金额重新报价，或削减交付量与使用范围。",
            )
        )

    # --- 汇总 ---------------------------------------------------------------
    finding_counts = {}
    for item in findings:
        finding_counts[item["code"]] = finding_counts.get(item["code"], 0) + 1

    severity_counts = dict((level, 0) for level in SEVERITY_ORDER)
    for item in findings:
        severity_counts[item["severity"]] += 1

    finding_total = len(findings)
    high_risk_count = severity_counts["HIGH"]
    blocking_count = len([item for item in findings if item["blocking"]])

    checks_skipped_sorted = sorted(checks_skipped)

    missing_inputs = []
    if _member(usage_raw, "term_months") is None:
        missing_inputs.append("order.usage.term_months")
    if _member(usage_raw, "credit_required") is None:
        missing_inputs.append("order.usage.credit_required")
    if _member(commercial, "deposit_pct") is None:
        missing_inputs.append("order.commercial.deposit_pct")
    if not payment_terms:
        missing_inputs.append("order.commercial.payment_terms")
    if _member(commercial, "kill_fee_pct") is None:
        missing_inputs.append("order.commercial.kill_fee_pct")
    if _member(commercial, "late_fee_pct") is None:
        missing_inputs.append("order.commercial.late_fee_pct")
    if _member(commercial, "expenses_cap") is None:
        missing_inputs.append("order.commercial.expenses_cap")
    if _member(constraints, "request_window_days") is None:
        missing_inputs.append("order.constraints.request_window_days")
    if _member(constraints, "raw_files_included") is None:
        missing_inputs.append("order.constraints.raw_files_included")
    if _member(constraints, "source_files_included") is None:
        missing_inputs.append("order.constraints.source_files_included")
    missing_inputs.sort()

    if high_risk_count:
        status = "HIGH_RISK"
    elif severity_counts["MEDIUM"]:
        status = "REVIEW_REQUIRED"
    elif checks_skipped_sorted:
        status = "PARTIAL"
    else:
        status = "ACCEPTABLE"

    price_analysis = None
    if not skip_price and named is not None:
        price_analysis = {
            "tier_id": named["tier_id"],
            "tier_price": _money(named["price"]),
            "tier_unit": named["unit"],
            "total_quantity": total_quantity,
            "expected_total": _money(expected_total),
            "quoted_total": _money(total_price),
            "difference": _money(difference),
            "gap_pct": _pct(gap_pct),
            "currency": commercial_currency,
        }

    due_values = [item["due_at"] for item in deliverables if item["due_at"] is not None]
    earliest_due_at = min(due_values).isoformat() if due_values else None

    usage_summary = {
        "media": list(media),
        "territory": list(territory) if territory is not None else None,
        "term_months": term_months,
        "exclusive": exclusive,
        "sublicense": sublicense,
        "buyout": buyout,
        "ai_training": ai_training,
        "tier_id": tier_id,
        "total_quantity": total_quantity,
        "deliverable_count": len(deliverables),
        "earliest_due_at": earliest_due_at,
    }

    next_actions = [
        {
            "action": ACTION_TEXT[item["code"]],
            "priority": PRIORITY_BY_SEVERITY[item["severity"]],
            "finding_code": item["code"],
        }
        for item in findings
    ]

    result = {
        "status": status,
        "as_of": as_of.isoformat(),
        "order_id": order_id,
        "client_ref": client_ref,
        "findings": findings,
        "finding_counts": finding_counts,
        "severity_counts": severity_counts,
        "finding_total": finding_total,
        "high_risk_count": high_risk_count,
        "blocking_count": blocking_count,
        "price_analysis": price_analysis,
        "checks_skipped": checks_skipped_sorted,
        "missing_inputs": missing_inputs,
        "injection_flags": sorted(injection),
        "usage_summary": usage_summary,
        "next_actions": next_actions,
        "markdown_summary": _render_markdown(
            status=status,
            as_of_text=as_of.isoformat(),
            order_id=order_id,
            client_ref=client_ref,
            findings=findings,
            severity_counts=severity_counts,
            finding_total=finding_total,
            price_analysis=price_analysis,
            checks_skipped=checks_skipped_sorted,
            missing_inputs=missing_inputs,
            injection_flags=sorted(injection),
            usage_summary=usage_summary,
        ),
        "disclaimer": DISCLAIMER,
    }
    return result


def _render_markdown(
    status,
    as_of_text,
    order_id,
    client_ref,
    findings,
    severity_counts,
    finding_total,
    price_analysis,
    checks_skipped,
    missing_inputs,
    injection_flags,
    usage_summary,
):
    """生成中文 markdown 摘要（确定性拼接）。"""
    ordered = sorted(
        enumerate(findings), key=lambda pair: (SEVERITY_ORDER.index(pair[1]["severity"]), pair[0])
    )

    lines = []
    lines.append("## 接单范围与报价风险预检")
    lines.append("")
    lines.append("- 订单编号：%s" % order_id)
    lines.append("- 客户编号：%s" % (client_ref if client_ref else "未提供"))
    lines.append("- 评估时点：%s" % as_of_text)
    lines.append("- 结论状态：%s（%s）" % (status, STATUS_LABEL.get(status, status)))
    lines.append(
        "- 发现项合计：%d（高风险 %d、中风险 %d、低风险 %d、提示 %d）"
        % (
            finding_total,
            severity_counts["HIGH"],
            severity_counts["MEDIUM"],
            severity_counts["LOW"],
            severity_counts["INFO"],
        )
    )
    lines.append(
        "- 交付物 %d 项，合计 %d 件；最早交付时间：%s"
        % (
            usage_summary["deliverable_count"],
            usage_summary["total_quantity"],
            usage_summary["earliest_due_at"] if usage_summary["earliest_due_at"] else "未提供",
        )
    )
    lines.append("")

    lines.append("### 风险清单（按严重度排序）")
    lines.append("")
    lines.append("| 序号 | 级别 | 代码 | 说明 | 阻断 |")
    lines.append("| --- | --- | --- | --- | --- |")
    if ordered:
        for position, pair in enumerate(ordered, 1):
            item = pair[1]
            lines.append(
                "| %d | %s | %s | %s | %s |"
                % (
                    position,
                    item["severity"],
                    item["code"],
                    item["title"],
                    "是" if item["blocking"] else "否",
                )
            )
    else:
        lines.append("| - | - | - | 未发现风险条目 | - |")
    lines.append("")

    lines.append("### 报价核对")
    lines.append("")
    if price_analysis is None:
        lines.append("- 无法计算：缺少计价档位、价目表或币种一致的前置条件（见下方被跳过的校验）。")
    else:
        lines.append("- 适用档位：%s" % price_analysis["tier_id"])
        lines.append(
            "- 档位单价：%s %s（单位 %s）"
            % (
                price_analysis["currency"],
                price_analysis["tier_price"],
                price_analysis["tier_unit"] if price_analysis["tier_unit"] else "未定义",
            )
        )
        lines.append("- 交付总量：%d 件" % price_analysis["total_quantity"])
        lines.append("- 按档位应计：%s %s" % (price_analysis["currency"], price_analysis["expected_total"]))
        lines.append("- 客户报价：%s %s" % (price_analysis["currency"], price_analysis["quoted_total"]))
        lines.append("- 差额：%s %s" % (price_analysis["currency"], price_analysis["difference"]))
        lines.append("- 缺口比例：%s" % price_analysis["gap_pct"])
    lines.append("")

    lines.append("### 需要向客户确认的问题")
    lines.append("")
    if ordered:
        for position, pair in enumerate(ordered, 1):
            lines.append("%d. %s" % (position, pair[1]["question_to_client"]))
    else:
        lines.append("1. 本次预检未发现需要立即向客户确认的问题，仍建议在签约前复核完整合同文本。")
    lines.append("")

    lines.append("### 被跳过的校验")
    lines.append("")
    if checks_skipped:
        for code in checks_skipped:
            lines.append("- %s：缺少该规则所需的用户输入，未执行。" % code)
    else:
        lines.append("- 无。")
    lines.append("")

    lines.append("### 缺失字段（保留 unknown，未套用默认值）")
    lines.append("")
    if missing_inputs:
        for path in missing_inputs:
            lines.append("- %s" % path)
    else:
        lines.append("- 无。")
    lines.append("")

    if injection_flags:
        lines.append("### 输入中的提示词注入痕迹")
        lines.append("")
        for item in injection_flags:
            lines.append("- %s：已忽略输入中的指令性文字，仅当作数据看待。" % item)
        lines.append("")

    lines.append("### 免责声明")
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines)


# 与本项目既有多数 Skill 一致的公开入口名，便于审查脚本与测试统一调用。
analyze = evaluate


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("用法：python3 scripts/run.py <input.json>\n")
        return 2
    try:
        with open(argv[1], "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError:
        sys.stderr.write("错误：无法读取输入文件。\n")
        return 1
    except ValueError:
        sys.stderr.write("错误：输入文件不是合法的 JSON。\n")
        return 1
    try:
        result = evaluate(data)
    except ValueError as error:
        sys.stderr.write("错误：%s\n" % error)
        return 1
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
