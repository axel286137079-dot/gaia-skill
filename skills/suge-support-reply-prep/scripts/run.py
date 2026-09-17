#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客服应答准备包（suge-support-reply-prep）离线确定性引擎。

用法：
    python3 scripts/run.py <input.json>

只读取一个已经导出的工单 JSON，输出逐条应答准备件、政策覆盖情况与草稿合规检查。
不联网、不发送任何消息、不写任何文件、不编造任何政策事实。
同一输入必然产出逐字节相同的输出（全部排序显式固定，金额与时间用 Decimal）。
"""

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")
HUNDRED = Decimal("100")

# ---------------------------------------------------------------------------
# 隐私门禁
# ---------------------------------------------------------------------------

FORBIDDEN_KEYS = frozenset([
    "password", "passwd", "passphrase", "secret_value", "api_token",
    "access_token", "client_secret", "credential_value", "private_key",
    "private_key_pem", "dsn", "connection_string", "jdbc_url", "database_url",
])

SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"AKIA[A-Z0-9]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
    re.compile(r"-----BEGIN [A-Z ]*P[R]IVATE KEY-----"),
    re.compile(r"-----BEGIN [A-Z ]*CERTIFICAT[E]-----"),
)


def scan_privacy(node):
    """递归扫描整份输入；命中即拒绝，且不回显命中的字段名或字符串内容。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_KEYS:
                raise ValueError("输入包含疑似凭据字段名，已拒绝处理（不回显字段内容）。")
            scan_privacy(value)
    elif isinstance(node, list):
        for item in node:
            scan_privacy(item)
    elif isinstance(node, str):
        for pattern in SECRET_PATTERNS:
            if pattern.search(node):
                raise ValueError("输入包含疑似真实凭据字符串，已拒绝处理（不回显该内容）。")


# ---------------------------------------------------------------------------
# 文本归一化与基础工具
# ---------------------------------------------------------------------------

def normalize_text(value):
    """全角转半角 + 小写，用于关键词匹配。"""
    if not isinstance(value, str):
        return ""
    chars = []
    for char in value:
        code = ord(char)
        if code == 0x3000:
            chars.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            chars.append(chr(code - 0xFEE0))
        else:
            chars.append(char)
    return "".join(chars).lower()


def has_control_chars(value):
    if not isinstance(value, str):
        return False
    for char in value:
        code = ord(char)
        if code < 32 and char not in "\t\n\r":
            return True
        if code == 127:
            return True
    return False


def clean_field(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text else None


def minutes_decimal(later, earlier):
    delta = later - earlier
    seconds = Decimal(delta.days * 86400 + delta.seconds) + (
        Decimal(delta.microseconds) / Decimal(1000000))
    return (seconds / Decimal(60)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def format_minutes(value):
    if value is None:
        return None
    return format(value, ".2f")


def format_percent(numerator, denominator):
    if denominator == 0:
        return "0.00%"
    ratio = (Decimal(numerator) / Decimal(denominator) * HUNDRED).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP)
    return format(ratio, ".2f") + "%"


def parse_iso(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s 必须是含时区偏移的 ISO8601 字符串。" % label)
    raw = value.strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError(
            "%s 无法解析为 ISO8601 时间（需形如 2026-09-16T10:00:00+08:00）。" % label)
    if parsed.utcoffset() is None:
        raise ValueError("%s 缺少时区偏移，无法计算首响用时。" % label)
    return parsed


def soft_parse_iso(value):
    """记录级时间：解析失败返回 None，由调用方记标志位。"""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z") or raw.endswith("z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.utcoffset() is None:
        return None
    return parsed


def parse_date(value, label):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s 必须是 YYYY-MM-DD 形式的日期或 null。" % label)
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("%s 无法解析为 YYYY-MM-DD 日期。" % label)


# ---------------------------------------------------------------------------
# 固定规则表
# ---------------------------------------------------------------------------

CANONICAL_TOPICS = frozenset(
    ["REFUND", "RETURN", "DAMAGE", "INVOICE", "WARRANTY", "LOGISTICS"])

INTENT_RULES = (
    ("ACCOUNT_SECURITY", ("账号", "登录", "被盗", "验证码", "密码", "冻结", "解封")),
    ("REFUND_RETURN", ("退款", "退货", "退钱", "七天无理由", "退一赔")),
    ("DAMAGE_QUALITY", ("破损", "坏了", "质量问题", "少件", "漏发", "发错", "瑕疵", "与描述不符")),
    ("INVOICE_BILLING", ("发票", "开票", "收据", "账单", "抬头", "税号")),
    ("WARRANTY", ("保修", "保期", "质保", "维修", "售后维修")),
    ("PRICE_PROMO", ("价格", "优惠", "差价", "活动价", "补差价", "降价")),
    ("LOGISTICS", ("物流", "快递", "什么时候到", "没收到", "发货", "运费", "签收")),
)

REQUIRED_TOPICS = {
    "REFUND_RETURN": ["REFUND", "RETURN"],
    "DAMAGE_QUALITY": ["DAMAGE"],
    "INVOICE_BILLING": ["INVOICE"],
    "WARRANTY": ["WARRANTY"],
    "LOGISTICS": ["LOGISTICS"],
    "ACCOUNT_SECURITY": [],
    "PRICE_PROMO": [],
    "OTHER": [],
}

# 回复骨架里对客户问题的“安全摘要”：只写固定意图标签，绝不回显客户原文，
# 因此金额、货币单位、退款承诺与提示注入文本都不会进入可直接发送的骨架。
INTENT_SUMMARY_LABELS = {
    "LOGISTICS": "物流与配送",
    "REFUND_RETURN": "退款与退货",
    "DAMAGE_QUALITY": "破损与质量问题",
    "INVOICE_BILLING": "发票与账单",
    "WARRANTY": "保修与售后维修",
    "ACCOUNT_SECURITY": "账号安全",
    "PRICE_PROMO": "价格与优惠",
    "OTHER": "其他咨询",
}

BLOCKING_FIELDS = {
    "REFUND_RETURN": ["order_ref"],
    "LOGISTICS": ["order_ref"],
    "INVOICE_BILLING": ["order_ref"],
    "PRICE_PROMO": ["order_ref"],
    "DAMAGE_QUALITY": ["order_ref", "damage_evidence"],
    "ACCOUNT_SECURITY": ["customer_ref"],
    "WARRANTY": [],
    "OTHER": [],
}

MONEY_INTENTS = frozenset(["REFUND_RETURN", "PRICE_PROMO"])
MONEY_KEYWORDS = ("赔偿", "赔付", "补钱", "差价", "退一赔")
LEGAL_KEYWORDS = ("律师", "起诉", "法院", "仲裁", "12315", "工商", "消协", "投诉到")
PUBLIC_KEYWORDS = ("曝光", "发小红书", "差评", "微博", "维权", "曝光你们")
ABUSE_KEYWORDS = ("骗子", "垃圾", "傻", "滚", "死", "威胁", "举报你们")

INJECTION_PATTERNS = (
    re.compile(r"忽略(以上|之前|所有)?(指令|规则|要求)"),
    re.compile(r"忽略.{0,8}(指令|规则|要求)"),
    re.compile(r"ignore (all )?(previous|above) instructions"),
    re.compile(r"系统提示词"),
    re.compile(r"system prompt"),
    re.compile(r"你现在是"),
    re.compile(r"无条件"),
    re.compile(r"免费给我"),
    re.compile(r"直接把钱"),
)

STATUS_ORDER = ("READY", "ESCALATE", "POLICY_MISSING", "NEEDS_INFO", "INVALID")
PRIORITY_ORDER = ("P0", "P1", "P2", "P3")

ALWAYS_FORBIDDEN = ("保证", "一定", "绝对", "100%")
REASON_FORBIDDEN = {
    "MONEY_COMMITMENT_RISK": ("全额退款", "马上赔", "立刻处理"),
    "LEGAL_OR_REGULATOR": ("我们会承担法律责任",),
    "ACCOUNT_SECURITY": ("帮你重置密码",),
}

DRAFT_BANNED_TERMS = ("保证", "一定", "绝对", "100%", "免费", "全额退款", "马上赔",
                      "立刻退款", "永久", "无条件")
PLACEHOLDER_PATTERNS = (
    re.compile(r"【待补:[^】]*】"),
    re.compile(r"\{\{[^}]*\}\}"),
    re.compile(r"\[待确认\]"),
)

INVALID_FLAGS = frozenset([
    "DUPLICATE_MESSAGE_ID", "FUTURE_RECEIVED_AT", "MISSING_RECEIVED_AT",
    "INVALID_RECEIVED_AT", "EMPTY_TEXT", "CONTROL_CHARS",
])


def priority_rank(priority):
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[priority]


# ---------------------------------------------------------------------------
# 输入读取与校验
# ---------------------------------------------------------------------------

def load_input(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            raw = handle.read()
    except OSError as exc:
        raise ValueError("无法读取输入文件（%s）。" % exc.strerror)
    try:
        data = json.loads(raw)
    except ValueError:
        raise ValueError("输入文件不是合法 JSON，无法解析。")
    if not isinstance(data, dict):
        raise ValueError("输入 JSON 的顶层必须是对象。")
    scan_privacy(data)
    return data


def read_policies(data, as_of):
    raw_policies = data.get("policies", [])
    if raw_policies is None:
        raw_policies = []
    if not isinstance(raw_policies, list):
        raise ValueError("policies 必须是数组。")
    as_of_date = as_of.date()
    policies = []
    seen_ids = set()
    for index, entry in enumerate(raw_policies):
        if not isinstance(entry, dict):
            raise ValueError("policies 第 %d 项必须是对象。" % (index + 1))
        policy_id = clean_field(entry.get("policy_id"))
        if policy_id is None:
            raise ValueError("policies 第 %d 项缺少 policy_id。" % (index + 1))
        if policy_id in seen_ids:
            raise ValueError("policies 中出现重复的 policy_id。")
        seen_ids.add(policy_id)
        covers = entry.get("covers")
        if not isinstance(covers, list) or not covers:
            raise ValueError("policy %s 的 covers 必须是非空数组。" % policy_id)
        topics = []
        for topic in covers:
            if not isinstance(topic, str) or not topic.strip():
                raise ValueError("policy %s 的 covers 含空主题码。" % policy_id)
            code = topic.strip().upper()
            if code not in CANONICAL_TOPICS:
                raise ValueError("policy %s 的 covers 含非规范主题码（仅支持 %s）。" % (
                    policy_id, "、".join(sorted(CANONICAL_TOPICS))))
            topics.append(code)
        effective_from = parse_date(entry.get("effective_from"),
                                    "policy %s 的 effective_from" % policy_id)
        expires_at = parse_date(entry.get("expires_at"),
                                "policy %s 的 expires_at" % policy_id)
        active = True
        expired = False
        if effective_from is not None and effective_from > as_of_date:
            active = False
        if expires_at is not None and expires_at <= as_of_date:
            active = False
            expired = True
        policies.append({
            "policy_id": policy_id,
            "covers": sorted(set(topics)),
            "active": active,
            "expired": expired,
        })
    return policies


def read_sla(data, as_of):
    sla = data.get("sla")
    if sla is None:
        return None, "absent"
    if not isinstance(sla, dict):
        raise ValueError("sla 必须是对象。")
    minutes = sla.get("first_reply_minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        raise ValueError("sla.first_reply_minutes 必须是大于 0 的整数。")
    return minutes, "user"


# ---------------------------------------------------------------------------
# 逐条处理
# ---------------------------------------------------------------------------

def classify_intent(norm_text):
    for intent, keywords in INTENT_RULES:
        for keyword in keywords:
            if keyword in norm_text:
                return intent
    return "OTHER"


def collect_escalation_reasons(intent, norm_text):
    reasons = set()
    if intent in MONEY_INTENTS or any(k in norm_text for k in MONEY_KEYWORDS):
        reasons.add("MONEY_COMMITMENT_RISK")
    if intent == "ACCOUNT_SECURITY":
        reasons.add("ACCOUNT_SECURITY")
    if any(k in norm_text for k in LEGAL_KEYWORDS):
        reasons.add("LEGAL_OR_REGULATOR")
    if any(k in norm_text for k in PUBLIC_KEYWORDS):
        reasons.add("PUBLIC_RELATION_RISK")
    if any(k in norm_text for k in ABUSE_KEYWORDS):
        reasons.add("ABUSE_OR_THREAT")
    return sorted(reasons)


def collect_injection_flags(norm_text):
    for pattern in INJECTION_PATTERNS:
        if pattern.search(norm_text):
            return ["PROMPT_INJECTION_IGNORED"]
    return []


def build_must_not_say(intent, escalation_reasons):
    terms = set(ALWAYS_FORBIDDEN)
    for reason in escalation_reasons:
        for term in REASON_FORBIDDEN.get(reason, ()):
            terms.add(term)
    return sorted(terms)


def build_skeleton(record):
    lines = ["【应答准备 %s】" % record["message_id"]]
    lines.append("您好，感谢您的反馈。")
    lines.append("已记录您反馈的问题类型：%s（不回显客户原文）。" % INTENT_SUMMARY_LABELS.get(
        record["intent"], "其他咨询"))
    status = record["status"]
    if status == "INVALID":
        lines.append("本条记录无法处理，标记：%s。请先修正导出数据后再审。" % "、".join(
            record["review_flags"]))
    elif status == "ESCALATE":
        lines.append("本条必须转人工处理，升级原因：%s。" % "、".join(
            record["escalation_reasons"]))
        lines.append("人工接手前不得对金额、赔付或时效作任何表态。")
    elif status == "POLICY_MISSING":
        lines.append("缺失主题 %s 对应的生效政策，回复前必须先确认政策口径。" % "、".join(
            record["policy_gaps"]))
    elif status == "NEEDS_INFO":
        for field in record["blocking_fields"]:
            lines.append("请先向客户补充：【待补:%s】" % field)
    else:
        lines.append("本条信息齐全，可按内部政策直接回复。")
        if record["policy_refs"]:
            lines.append("[依据:%s]" % "、".join(record["policy_refs"]))
        else:
            lines.append("[依据:未提供政策]")
    lines.append("（本骨架只给结构与依据占位，不含金额、时限或政策结论。）")
    return "\n".join(lines)


def build_record(index, raw_message, as_of, sla_minutes, policies, seen_ids):
    if not isinstance(raw_message, dict):
        raise ValueError("messages 第 %d 项必须是对象。" % (index + 1))
    message_id = clean_field(raw_message.get("message_id"))
    if message_id is None:
        raise ValueError("messages 第 %d 项缺少 message_id。" % (index + 1))

    raw_text = raw_message.get("text")
    raw_text = raw_text if isinstance(raw_text, str) else ""
    norm_text = normalize_text(raw_text)

    flags = set()
    if has_control_chars(raw_text):
        flags.add("CONTROL_CHARS")
    if message_id in seen_ids:
        flags.add("DUPLICATE_MESSAGE_ID")
    else:
        seen_ids.add(message_id)

    raw_received = raw_message.get("received_at")
    received = None
    if not isinstance(raw_received, str) or not raw_received.strip():
        flags.add("MISSING_RECEIVED_AT")
    else:
        received = soft_parse_iso(raw_received)
        if received is None:
            flags.add("INVALID_RECEIVED_AT")
        elif received > as_of:
            flags.add("FUTURE_RECEIVED_AT")

    if not norm_text.strip():
        flags.add("EMPTY_TEXT")

    injection_flags = collect_injection_flags(norm_text)
    flags.update(injection_flags)

    is_invalid = bool(flags & INVALID_FLAGS)

    intent = classify_intent(norm_text)
    required_topics = sorted(REQUIRED_TOPICS.get(intent, []))
    active_policies = [policy for policy in policies if policy["active"]]
    covering = []
    if required_topics:
        for policy in active_policies:
            if all(topic in policy["covers"] for topic in required_topics):
                covering.append(policy["policy_id"])
    policy_refs = sorted(covering)
    policy_gaps = []
    for topic in required_topics:
        if not any(topic in policy["covers"] for policy in active_policies):
            policy_gaps.append(topic)
    policy_gaps = sorted(policy_gaps)

    order_ref = clean_field(raw_message.get("order_ref"))
    customer_ref = clean_field(raw_message.get("customer_ref"))
    intake = raw_message.get("intake")
    has_attachment = False
    if isinstance(intake, dict) and intake.get("has_attachment") is True:
        has_attachment = True

    available = {"order_ref": order_ref is not None,
                 "customer_ref": customer_ref is not None,
                 "damage_evidence": has_attachment}
    blocking_fields = [field for field in BLOCKING_FIELDS.get(intent, [])
                       if not available.get(field, False)]

    escalation_reasons = collect_escalation_reasons(intent, norm_text)

    if is_invalid:
        status = "INVALID"
    elif escalation_reasons:
        status = "ESCALATE"
    elif policy_gaps:
        status = "POLICY_MISSING"
    elif blocking_fields:
        status = "NEEDS_INFO"
    else:
        status = "READY"

    if is_invalid or received is None or sla_minutes is None:
        elapsed = None
        sla_state = "UNKNOWN"
        first_reply_due = None
    else:
        elapsed = minutes_decimal(as_of, received)
        threshold = (Decimal(sla_minutes) * Decimal(80) / HUNDRED).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP)
        if elapsed >= Decimal(sla_minutes):
            sla_state = "BREACH"
        elif elapsed >= threshold:
            sla_state = "AT_RISK"
        else:
            sla_state = "OK"
        first_reply_due = (received + timedelta(minutes=sla_minutes)).isoformat()

    if status in ("INVALID", "ESCALATE") or sla_state == "BREACH":
        priority = "P0"
    elif status in ("NEEDS_INFO", "POLICY_MISSING") or sla_state == "AT_RISK":
        priority = "P1"
    elif sla_state == "UNKNOWN":
        priority = "P2"
    else:
        priority = "P3"

    record = {
        "message_id": message_id,
        "row_index": index + 1,
        "channel": clean_field(raw_message.get("channel")),
        "customer_ref": customer_ref,
        "order_ref": order_ref,
        "intent": intent,
        "priority": priority,
        "status": status,
        "received_at": raw_received.strip() if isinstance(raw_received, str)
        and raw_received.strip() else None,
        "elapsed_minutes": format_minutes(elapsed),
        "first_reply_due": first_reply_due,
        "sla_state": sla_state,
        "required_topics": required_topics,
        "policy_refs": policy_refs,
        "policy_gaps": policy_gaps,
        "blocking_fields": blocking_fields,
        "escalation_reasons": escalation_reasons,
        "injection_flags": injection_flags,
        "review_flags": sorted(flags),
        "must_not_say": build_must_not_say(intent, escalation_reasons),
        "suggested_reply_skeleton": "",
    }
    record["_received"] = received
    record["suggested_reply_skeleton"] = build_skeleton(record)
    return record


def sort_records(records):
    def key(record):
        rank = priority_rank(record["priority"])
        if record["status"] == "INVALID":
            return (rank, 1, "", record["message_id"], record["row_index"])
        received = record["_received"]
        stamp = received.astimezone(timezone.utc).isoformat() if received else ""
        return (rank, 0, stamp, record["message_id"], record["row_index"])

    return sorted(records, key=key)


# ---------------------------------------------------------------------------
# 草稿检查
# ---------------------------------------------------------------------------

def check_draft(raw_draft, index, canonical_by_id):
    if not isinstance(raw_draft, dict):
        raise ValueError("drafts 第 %d 项必须是对象。" % (index + 1))
    message_id = clean_field(raw_draft.get("message_id"))
    if message_id is None:
        raise ValueError("drafts 第 %d 项缺少 message_id。" % (index + 1))
    if message_id not in canonical_by_id:
        raise ValueError(
            "drafts 第 %d 项引用了不存在的 message_id，无法做草稿检查。" % (index + 1))
    text = raw_draft.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("drafts 第 %d 项的 text 必须是非空字符串。" % (index + 1))

    record = canonical_by_id[message_id]
    norm_draft = normalize_text(text)

    banned = sorted(set(term for term in DRAFT_BANNED_TERMS if term in norm_draft))
    placeholders = set()
    for pattern in PLACEHOLDER_PATTERNS:
        for match in pattern.findall(text):
            placeholders.add(match)
    unresolved = sorted(placeholders)

    missing_basis = False
    if record["required_topics"]:
        has_basis = False
        for policy_id in record["policy_refs"]:
            if "[依据:%s]" % policy_id in text:
                has_basis = True
                break
        missing_basis = not has_basis

    reason_codes = set()
    if banned:
        reason_codes.add("BANNED_TERMS")
    if unresolved:
        reason_codes.add("UNRESOLVED_PLACEHOLDER")
    if missing_basis:
        reason_codes.add("MISSING_POLICY_BASIS")
    if record["status"] == "ESCALATE":
        reason_codes.add("MESSAGE_ESCALATED")
    if record["status"] == "INVALID":
        reason_codes.add("MESSAGE_INVALID")

    if record["status"] == "ESCALATE" or banned:
        status = "BLOCK"
    elif unresolved or missing_basis:
        status = "REVISE"
    else:
        status = "PASS"

    return {
        "message_id": message_id,
        "status": status,
        "banned_terms": banned,
        "unresolved_placeholders": unresolved,
        "reason_codes": sorted(reason_codes),
        "referenced_message_status": record["status"],
    }


# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------

def build_next_actions(records):
    actions = []
    for record in records:
        status = record["status"]
        if status == "INVALID":
            action = "修正原始记录后重新导出（无效标记：%s）" % "、".join(record["review_flags"])
        elif status == "ESCALATE":
            action = "转人工处理并复核升级原因：%s" % "、".join(record["escalation_reasons"])
        elif status == "POLICY_MISSING":
            action = "补充缺失主题的生效政策后再回复：%s" % "、".join(record["policy_gaps"])
        elif status == "NEEDS_INFO":
            action = "向客户追补缺失字段：%s" % "、".join(
                "【待补:%s】" % field for field in record["blocking_fields"])
        else:
            if record["policy_refs"]:
                action = "按骨架直接回复并附依据：[依据:%s]" % "、".join(record["policy_refs"])
            else:
                action = "按骨架直接回复（无需政策依据）"
        actions.append({"action": action, "priority": record["priority"],
                        "message_id": record["message_id"]})
    return actions


def build_markdown(result, records, draft_checks):
    status_counts = result["status_counts"]
    priority_counts = result["priority_counts"]
    coverage = result["policy_coverage"]
    valid_records = [record for record in records if record["status"] != "INVALID"]
    lines = []
    lines.append("## 客服应答准备摘要")
    lines.append("")
    lines.append("- 基准时间：%s；首响 SLA：%s" % (
        result["as_of"], result["_sla_label"]))
    lines.append("- 工单记录：%d 条（唯一 message_id：%d 个）" % (
        result["message_count"], result["unique_message_id_count"]))
    lines.append("- 处理状态：可直接回复 %d / 需人工升级 %d / 政策缺失 %d / 需补信息 %d / 记录无效 %d"
                 % (status_counts["READY"], status_counts["ESCALATE"],
                    status_counts["POLICY_MISSING"], status_counts["NEEDS_INFO"],
                    status_counts["INVALID"]))
    lines.append("- 优先级分布：P0 %d / P1 %d / P2 %d / P3 %d" % (
        priority_counts["P0"], priority_counts["P1"], priority_counts["P2"],
        priority_counts["P3"]))
    # 达标率只有在“用户确实提供了 SLA”且“存在有效记录”时才有意义；
    # 否则必须显示 N/A，绝不把“无法判断”渲染成百分比。
    if result["coverage"]["sla_source"] == "absent":
        sla_pass_rate = "N/A（未提供 SLA，无法计算）"
    elif not valid_records:
        sla_pass_rate = "N/A（无有效记录，无法计算）"
    else:
        sla_pass_rate = format_percent(len(valid_records) - result["sla_breach_count"],
                                       len(valid_records))
    lines.append("- 首响：超时 %d 条 / 临界 %d 条；有效记录首响达标率 %s" % (
        result["sla_breach_count"], result["sla_at_risk_count"], sla_pass_rate))
    lines.append("- 政策主题覆盖：%s（已覆盖 %d / 必需 %d），缺口 %s" % (
        format_percent(len(coverage["covered_topics"]), len(coverage["required_topics"])),
        len(coverage["covered_topics"]), len(coverage["required_topics"]),
        "、".join(coverage["gaps"]) if coverage["gaps"] else "无"))
    lines.append("- 已失效政策：%s；尚未生效政策：%s" % (
        "、".join(coverage["expired_policy_ids"]) if coverage["expired_policy_ids"] else "无",
        "、".join(coverage["inactive_policy_ids"]) if coverage["inactive_policy_ids"] else "无"))
    lines.append("- 提示注入命中记录：%s" % (
        "、".join(result["injection_flagged"]) if result["injection_flagged"] else "无"))
    lines.append("")
    lines.append("| 优先级 | message_id | 意图 | 状态 | 首响状态 | 用时(分) | 首响截止 | 下一步 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    actions = [item["action"] for item in result["next_actions"]]
    for position, record in enumerate(records):
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            record["priority"], record["message_id"], record["intent"],
            record["status"], record["sla_state"],
            record["elapsed_minutes"] if record["elapsed_minutes"] else "—",
            record["first_reply_due"] if record["first_reply_due"] else "—",
            actions[position].replace("|", "/")))
    lines.append("")
    escalate = [record["message_id"] for record in records if record["status"] == "ESCALATE"]
    lines.append("### 需要立刻人工接手")
    if escalate:
        for record in records:
            if record["status"] == "ESCALATE":
                lines.append("- %s（%s）：%s" % (
                    record["message_id"], record["priority"],
                    "、".join(record["escalation_reasons"])))
    else:
        lines.append("- 无：本批次没有触发升级条件的记录。")
    lines.append("")
    lines.append("### 草稿检查")
    lines.append("- 通过 %d / 待修改 %d / 禁止发送 %d" % (
        result["draft_status_counts"]["PASS"], result["draft_status_counts"]["REVISE"],
        result["draft_status_counts"]["BLOCK"]))
    for check in draft_checks:
        detail = "、".join(check["reason_codes"]) if check["reason_codes"] else "无问题"
        lines.append("- %s：%s（%s）" % (check["message_id"], check["status"], detail))
    lines.append("")
    lines.append("### 使用提醒")
    lines.append("- 本文件是回复前的准备材料，不构成法律意见或商业承诺。")
    lines.append("- 脚本不发送任何消息、不生成新政策、不批准退款、不计算任何金额。")
    lines.append("- 所有带【待补:…】或 [依据:…] 的位置必须由人工补齐后才能对外发送。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def analyse(data):
    scan_privacy(data)
    as_of = parse_iso(data.get("as_of"), "as_of")
    sla_minutes, sla_source = read_sla(data, as_of)
    policies = read_policies(data, as_of)

    raw_messages = data.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise ValueError("messages 必须是至少包含一条记录的数组。")

    seen_ids = set()
    records_in_input_order = []
    for index, raw_message in enumerate(raw_messages):
        records_in_input_order.append(build_record(index, raw_message, as_of, sla_minutes,
                                                   policies, seen_ids))
    records = sort_records(records_in_input_order)

    active_policies = [policy for policy in policies if policy["active"]]
    required_topics = set()
    for record in records:
        required_topics.update(record["required_topics"])
    covered_topics = sorted(
        topic for topic in required_topics
        if any(topic in policy["covers"] for policy in active_policies))
    gaps = sorted(topic for topic in required_topics if topic not in covered_topics)
    expired_ids = sorted(policy["policy_id"] for policy in policies
                         if policy["expired"] and not policy["active"])
    inactive_ids = sorted(policy["policy_id"] for policy in policies
                          if not policy["active"] and not policy["expired"])

    canonical_by_id = {}
    for record in records_in_input_order:
        if record["message_id"] not in canonical_by_id:
            canonical_by_id[record["message_id"]] = record

    raw_drafts = data.get("drafts", [])
    if raw_drafts is None:
        raw_drafts = []
    if not isinstance(raw_drafts, list):
        raise ValueError("drafts 必须是数组。")
    draft_checks = [check_draft(raw_draft, index, canonical_by_id)
                    for index, raw_draft in enumerate(raw_drafts)]

    status_counts = dict((status, 0) for status in STATUS_ORDER)
    for record in records:
        status_counts[record["status"]] += 1

    priority_counts = dict((priority, 0) for priority in PRIORITY_ORDER)
    for record in records:
        priority_counts[record["priority"]] += 1

    draft_status_counts = {"PASS": 0, "REVISE": 0, "BLOCK": 0}
    for check in draft_checks:
        draft_status_counts[check["status"]] += 1

    injection_flagged = sorted(set(
        record["message_id"] for record in records if record["injection_flags"]))

    valid_records = [record for record in records if record["status"] != "INVALID"]

    if not valid_records:
        top_status = "INVALID"
    elif any(record["status"] == "ESCALATE" for record in records):
        top_status = "ESCALATION_REQUIRED"
    elif any(record["sla_state"] == "BREACH" for record in records):
        top_status = "SLA_BREACH"
    elif any(record["status"] == "NEEDS_INFO" for record in records):
        top_status = "INFO_MISSING"
    elif any(record["status"] == "POLICY_MISSING" for record in records) or gaps:
        top_status = "POLICY_GAP"
    else:
        top_status = "READY"

    result = {
        "status": top_status,
        "as_of": as_of.isoformat(),
        "message_count": len(records),
        "unique_message_id_count": len(canonical_by_id),
        "status_counts": status_counts,
        "priority_counts": priority_counts,
        "escalation_count": status_counts["ESCALATE"],
        "info_missing_count": status_counts["NEEDS_INFO"],
        "policy_gap_count": status_counts["POLICY_MISSING"],
        "sla_breach_count": sum(
            1 for record in records if record["sla_state"] == "BREACH"),
        "sla_at_risk_count": sum(
            1 for record in records if record["sla_state"] == "AT_RISK"),
        "injection_flagged": injection_flagged,
        "policy_coverage": {
            "required_topics": sorted(required_topics),
            "covered_topics": covered_topics,
            "gaps": gaps,
            "expired_policy_ids": expired_ids,
            "inactive_policy_ids": inactive_ids,
        },
        "coverage": {"sla_source": sla_source},
        "messages": [],
        "draft_checks": draft_checks,
        "draft_status_counts": draft_status_counts,
        "next_actions": build_next_actions(records),
        "markdown_summary": "",
        "disclaimer": (
            "本输出是回复前的准备材料，不构成法律意见、合规结论或商业承诺；"
            "脚本不发送任何消息、不编造或修改政策、不批准退款、不计算任何金额，"
            "所有金额、时限与责任口径必须由有权限的人员按公司生效政策确认后对外表达。"
        ),
        "_sla_label": ("未提供（首响状态记 UNKNOWN）" if sla_source == "absent"
                       else "%d 分钟（用户提供）" % sla_minutes),
    }

    public_messages = []
    for record in records:
        public = dict((key, value) for key, value in record.items()
                      if not key.startswith("_"))
        public_messages.append(public)
    result["messages"] = public_messages
    result["markdown_summary"] = build_markdown(result, records, draft_checks)
    del result["_sla_label"]
    return result


# 与本项目既有多数 Skill 一致的公开入口名，便于审查脚本与测试统一调用。
analyze = analyse


def main(argv):
    if len(argv) != 2:
        raise ValueError("用法：python3 scripts/run.py <input.json>（需要且仅需要一个输入文件路径）。")
    data = load_input(argv[1])
    result = analyse(data)
    sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False))
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as exc:  # 结构性失败：非零退出，不伪装成正常输出
        sys.stderr.write("处理失败：%s\n" % exc)
        raise
