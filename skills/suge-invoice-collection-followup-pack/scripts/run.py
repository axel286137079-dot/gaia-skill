#!/usr/bin/env python3
"""应收款跟进准备包 (accounts-receivable collection follow-up prep).

Offline, read-only JSON in / JSON out. It reconciles an invoice book from
exactly the facts the user supplies and emits a follow-up queue, per-currency
outstanding balances, evidence gaps, numbered clarification questions and
human-confirm drafts.

It never sends anything, never contacts a customer, never charges interest or
fees, never gives legal advice and never guesses a payment term that the input
does not state. Missing values stay ``null`` and become gaps/questions.

Python 3.9+, standard library only, no network access.
"""
import json
import re
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

DUE_SOON_DAYS = 7
TWO_PLACES = Decimal("0.01")
PLACEHOLDER = "已隐藏疑似提示注入文本"

STATUS_INVALID = "INVALID"
STATUS_DISPUTED = "DISPUTED"
STATUS_OVERPAID = "OVERPAID"
STATUS_PAID = "PAID"
STATUS_PROMISED = "PROMISED"
STATUS_DATE_UNKNOWN = "DATE_UNKNOWN"
STATUS_OVERDUE = "OVERDUE"
STATUS_DUE_SOON = "DUE_SOON"
STATUS_CURRENT = "CURRENT"

STATUS_ORDER = [
    STATUS_INVALID, STATUS_DISPUTED, STATUS_OVERPAID, STATUS_PAID,
    STATUS_PROMISED, STATUS_DATE_UNKNOWN, STATUS_OVERDUE, STATUS_DUE_SOON,
    STATUS_CURRENT,
]

ACTIONABLE_STATUSES = {
    STATUS_DISPUTED, STATUS_PROMISED, STATUS_DATE_UNKNOWN,
    STATUS_OVERDUE, STATUS_DUE_SOON,
}

STATUS_RANK = {
    STATUS_DISPUTED: 1, STATUS_OVERDUE: 2, STATUS_PROMISED: 3,
    STATUS_DATE_UNKNOWN: 4, STATUS_DUE_SOON: 5, STATUS_OVERPAID: 6,
    STATUS_PAID: 7, STATUS_CURRENT: 8, STATUS_INVALID: 9,
}

PRIORITY_BY_STATUS = {
    STATUS_DISPUTED: "P1", STATUS_OVERDUE: "P1",
    STATUS_PROMISED: "P2", STATUS_DATE_UNKNOWN: "P2",
    STATUS_DUE_SOON: "P3", STATUS_OVERPAID: "P3",
    STATUS_PAID: "P4", STATUS_CURRENT: "P4", STATUS_INVALID: "P4",
}

GLOBAL_STATUS_ORDER = [
    ("INVALID", lambda counts, valid: valid == 0),
    ("DISPUTE_HOLD", lambda counts, valid: counts[STATUS_DISPUTED] > 0),
    ("OVERPAID_REVIEW", lambda counts, valid: counts[STATUS_OVERPAID] > 0),
    ("OVERDUE_ACTION", lambda counts, valid: counts[STATUS_OVERDUE] > 0),
    ("PROMISE_TRACKING", lambda counts, valid: counts[STATUS_PROMISED] > 0),
    ("DATE_UNKNOWN", lambda counts, valid: counts[STATUS_DATE_UNKNOWN] > 0),
    ("DUE_SOON", lambda counts, valid: counts[STATUS_DUE_SOON] > 0),
    ("CURRENT", lambda counts, valid: True),
]

QUESTION_TOPIC_ORDER = [
    "DUE_DATE", "CONTRACT_TERMS", "CONTACT", "DISPUTE_OWNER",
    "FOLLOWUP_STAGES", "PAYMENT_STATUS", "PROMISE_CONFIRMATION",
    "COMMUNICATION_LOG", "RECORD_INVALID",
]

GAP_LABELS = {
    "CONTRACT_TERMS_MISSING": "未提供合同账期/宽限期",
    "DUE_DATE_MISSING": "未提供到期日",
    "ISSUED_DATE_MISSING": "未提供开票日",
    "CONTACT_MISSING": "未提供客户联系人",
    "COMMUNICATION_LOG_MISSING": "未提供与该发票的沟通记录",
    "PENDING_PAYMENT_UNVERIFIED": "存在未到账（PENDING）付款",
    "FOLLOWUP_STAGES_MISSING": "逾期但未提供跟进阶段规则",
    "DISPUTE_OWNER_UNKNOWN": "争议处理负责人/结论未提供",
    "PROMISE_UNCONFIRMED": "承诺付款缺少书面确认记录",
}

QUESTION_TEXT = {
    "DUE_DATE": "以下发票缺少到期日，无法计算逾期天数，请补充合同约定的到期日：",
    "CONTRACT_TERMS": "以下发票未提供合同账期/宽限期，请补充后再排跟进节奏：",
    "CONTACT": "以下发票缺少客户联系人，无法准备人工发送对象，请补充：",
    "DISPUTE_OWNER": "以下发票处于争议状态，请确认争议负责人、处理结论与是否暂停跟进：",
    "FOLLOWUP_STAGES": "以下发票已逾期但未提供跟进阶段规则，请给出各阶段起始天数：",
    "PAYMENT_STATUS": "以下发票存在未到账或状态不明的付款，请确认是否已实际到账：",
    "PROMISE_CONFIRMATION": "以下发票有付款承诺但缺少书面确认记录，请补充承诺来源：",
    "COMMUNICATION_LOG": "以下发票没有任何沟通记录，请补充历史跟进或确认从未联系：",
    "RECORD_INVALID": "以下记录存在结构性问题，已标记为不可用，请先修正原始数据：",
}

# Prompt-injection detection: deterministic and low-false-positive.
#
# A match requires BOTH an *override action* (忽略 / 無視 / ignore / disregard /
# forget / override / leak / reveal …) AND a *dangerous target* (指令 / 规则 /
# 要求 / 系统 / 提示词 / instruction / rule / system / prompt …) inside one
# clause. A bare verb or a bare noun never triggers on its own: an ordinary
# receivables remark such as 「请忽略小额尾差，财务已核销。」 or "Ignore the
# previous invoice status" pairs the verb with an ordinary noun and stays
# clean, while "Ignore the previous instructions" and 「忽略规则」 still match.
_CN_OVERRIDE = r"(?:忽略|忽視|無視|无视|洩露|泄露|覆蓋|覆盖|繞過|绕过|解除)"
_CN_TARGET = (r"(?:指令|规则|規則|要求|系统|系統|提示词|提示詞|提示|设定|設定|"
              r"限制|命令|角色|人设|人設|安全策略)")
# One clause only: never bridge a comma/period/newline, so an override verb in
# one sentence cannot be paired with a target in the next.
_CN_GAP = r"[^，,。.；;！!？?\n]{0,12}?"
_EN_OVERRIDE = r"(?:ignore|disregard|forget|override|bypass|leak|reveal|expose)"
_EN_MODIFIER = (r"(?:\s+(?:the|all|any|these|those|my|your|its|our|previous|prior|"
                r"above|earlier|preceding))*")
_EN_TARGET = (r"(?:instruction|instructions|rule|rules|system|prompt|prompts|"
              r"command|commands|directive|directives|guideline|guidelines)")

INJECTION_PATTERNS = re.compile(
    # Chinese / Traditional Chinese: override verb + dangerous target
    _CN_OVERRIDE + _CN_GAP + _CN_TARGET
    # English: override verb + (modifiers) + dangerous target
    + r"|" + _EN_OVERRIDE + _EN_MODIFIER + r"\s+" + _EN_TARGET
    # Explicit LLM-injection vocabulary that is implausible in a receivables
    # note, and therefore safe to flag on its own.
    + r"|系统提示词|系統提示詞|system prompt|approve now",
    re.IGNORECASE)

SENSITIVE_KEYS = {
    "password", "passwd", "pwd", "token", "access_token", "refresh_token",
    "secret", "client_secret", "api_key", "apikey", "private_key", "cookie",
    "authorization", "auth_code", "session_id",
}

# Assembled so this file itself carries no contiguous PEM header literal.
PEM_PATTERN = r"-----BEGIN [A-Z ]*" + "PRIVATE " + "KEY-----"
SECRET_VALUE_PATTERNS = [
    PEM_PATTERN,
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"AIza[0-9A-Za-z_-]{35}",
]

DISCLAIMER = (
    "本输出是应收款跟进的信息核对材料，不构成法律意见、催收建议或收款承诺，"
    "不含滞纳金、利息或费用计算；任何消息都必须由人工核对后发送。"
)


class InputRejected(ValueError):
    """Raised when the input carries credential-shaped data or敏感字段名."""


def _scan_credentials(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.strip().lower() in SENSITIVE_KEYS:
                raise InputRejected("敏感字段名已拒绝处理。")
            _scan_credentials(value)
    elif isinstance(node, list):
        for value in node:
            _scan_credentials(value)
    elif isinstance(node, str):
        for pattern in SECRET_VALUE_PATTERNS:
            if re.search(pattern, node):
                raise InputRejected("疑似凭据内容已拒绝处理。")


def _parse_timestamp(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " 必须是带时区的 ISO8601 字符串")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(label + " 无法解析为 ISO8601 时间")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(label + " 必须带时区偏移，不做本地时区猜测")
    return parsed


def _parse_date(value):
    """Return (date|None, ok). Blank -> (None, True). Unparseable -> (None, False)."""
    if value is None:
        return None, True
    if not isinstance(value, str) or not value.strip():
        return None, True
    text = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None, False
    try:
        return date.fromisoformat(text), True
    except ValueError:
        return None, False


def _parse_money(value):
    if value is None or isinstance(value, bool):
        return None, False
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        return None, False
    text = value.strip().replace(",", "")
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return None, False
    if not parsed.is_finite():
        return None, False
    return parsed, True


def _money(value):
    return str(value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def _is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value):
    return value if isinstance(value, str) else None


def _detect_injection(value):
    if not isinstance(value, str) or not value:
        return False
    return INJECTION_PATTERNS.search(value) is not None


class UntrustedTextRegistry:
    """Deterministic register of untrusted-text locations that matched an
    injection pattern.

    A location is a precise, reproducible path such as
    ``company.display_name`` or ``invoices[3].notes`` so a reviewer can point
    at the exact field; the registry never stores or echoes the matched value.
    """

    def __init__(self):
        self._flagged = set()

    def flag(self, location):
        self._flagged.add(location)

    def locations(self):
        return sorted(self._flagged)


def _invoice_loc(index, field):
    return "invoices[%d].%s" % (index, field)


def _comm_loc(index, field):
    return "communications[%d].%s" % (index, field)


def _stage_loc(index, field):
    return "followup_stages[%d].%s" % (index, field)


def _register(registry, location, value):
    """Flag `location` when `value` looks like a prompt injection. Returns
    whether it was flagged."""
    if _detect_injection(value):
        registry.flag(location)
        return True
    return False


def _display(value, injected):
    """Human-readable rendering of an untrusted value: the fixed placeholder
    when it matched an injection pattern, otherwise the markdown-escaped value.
    The raw value stays available in the structured output."""
    if injected:
        return PLACEHOLDER
    return _escape(value)


def _escape(value):
    """Neutralise any markdown structure an untrusted value could forge."""
    if value is None:
        return ""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    out = []
    for char in text:
        if char in "\\|`[]()#!<>":
            out.append("\\" + char)
        else:
            out.append(char)
    return "".join(out)


def _digits(days):
    return days


def _normalise_stages(raw, registry):
    stages = []
    if not isinstance(raw, list):
        return stages
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        name = _text(entry.get("name"))
        # Detect on every stage entry, even one that is later dropped as
        # structurally invalid: the documented position is the raw entry.
        _register(registry, _stage_loc(index, "name"), name)
        stage_id = _text(entry.get("stage_id"))
        if _is_blank(stage_id):
            continue
        minimum = entry.get("min_days_overdue")
        maximum = entry.get("max_days_overdue")
        if not isinstance(minimum, int) or isinstance(minimum, bool):
            continue
        if maximum is not None and (not isinstance(maximum, int)
                                   or isinstance(maximum, bool)):
            continue
        stages.append({
            "stage_id": stage_id,
            "name": name,
            "min_days_overdue": minimum,
            "max_days_overdue": maximum,
        })
    stages.sort(key=lambda item: (item["min_days_overdue"], item["stage_id"]))
    return stages


def _match_stages(stages, days_overdue):
    current = None
    for stage in stages:
        if days_overdue < stage["min_days_overdue"]:
            continue
        if stage["max_days_overdue"] is not None and \
                days_overdue > stage["max_days_overdue"]:
            continue
        current = stage
    following = None
    for stage in stages:
        if stage["min_days_overdue"] > days_overdue:
            following = stage
            break
    return current, following


def _process_payments(raw, currency, seen_ids):
    payments = []
    confirmed = Decimal("0")
    pending = Decimal("0")
    flags = []
    if not isinstance(raw, list):
        raw = []
    for entry in raw:
        record = {
            "payment_id": None, "paid_on": None, "amount": None,
            "currency": None, "status": None, "counted": False,
            "review_flags": [],
        }
        if not isinstance(entry, dict):
            record["review_flags"].append("INVALID_PAYMENT_RECORD")
            payments.append(record)
            flags.append("INVALID_PAYMENT_RECORD")
            continue
        payment_id = _text(entry.get("payment_id"))
        record["payment_id"] = payment_id
        duplicate = False
        if not _is_blank(payment_id):
            if payment_id in seen_ids:
                duplicate = True
            else:
                seen_ids.add(payment_id)
        if duplicate:
            record["review_flags"].append("DUPLICATE_PAYMENT_ID")
            flags.append("DUPLICATE_PAYMENT_ID")
        amount, amount_ok = _parse_money(entry.get("amount"))
        record["amount"] = _money(amount) if amount_ok else None
        if not amount_ok or amount <= 0:
            record["review_flags"].append("INVALID_PAYMENT_AMOUNT")
            flags.append("INVALID_PAYMENT_AMOUNT")
        paid_on, date_ok = _parse_date(entry.get("paid_on"))
        record["paid_on"] = paid_on.isoformat() if paid_on else None
        if not date_ok or paid_on is None:
            record["review_flags"].append("INVALID_PAYMENT_DATE")
            flags.append("INVALID_PAYMENT_DATE")
        pay_currency = _text(entry.get("currency"))
        record["currency"] = pay_currency
        cross = (not _is_blank(pay_currency) and not _is_blank(currency)
                 and pay_currency != currency)
        if cross:
            record["review_flags"].append("CROSS_CURRENCY_PAYMENT")
            flags.append("CROSS_CURRENCY_PAYMENT")
        status = _text(entry.get("status"))
        record["status"] = status
        if not cross and amount_ok and amount > 0 and paid_on is not None and not duplicate:
            normalised = (status or "").upper()
            if normalised == "CONFIRMED":
                confirmed += amount
                record["counted"] = True
            elif normalised == "PENDING":
                pending += amount
            elif normalised == "FAILED":
                pass
            else:
                if "PAYMENT_STATUS_UNKNOWN" not in record["review_flags"]:
                    record["review_flags"].append("PAYMENT_STATUS_UNKNOWN")
                    flags.append("PAYMENT_STATUS_UNKNOWN")
        payments.append(record)
    return payments, confirmed, pending, flags


def _process_credits(raw, seen_ids):
    credits = []
    total = Decimal("0")
    flags = []
    if not isinstance(raw, list):
        raw = []
    for entry in raw:
        record = {
            "credit_id": None, "issued_on": None, "amount": None,
            "counted": False, "review_flags": [],
        }
        if not isinstance(entry, dict):
            record["review_flags"].append("INVALID_CREDIT_RECORD")
            flags.append("INVALID_CREDIT_RECORD")
            credits.append(record)
            continue
        credit_id = _text(entry.get("credit_id"))
        record["credit_id"] = credit_id
        duplicate = False
        if not _is_blank(credit_id):
            if credit_id in seen_ids:
                duplicate = True
            else:
                seen_ids.add(credit_id)
        if duplicate:
            record["review_flags"].append("DUPLICATE_CREDIT_ID")
            flags.append("DUPLICATE_CREDIT_ID")
        amount, amount_ok = _parse_money(entry.get("amount"))
        record["amount"] = _money(amount) if amount_ok else None
        if not amount_ok or amount <= 0:
            record["review_flags"].append("INVALID_CREDIT_AMOUNT")
            flags.append("INVALID_CREDIT_AMOUNT")
        issued_on, date_ok = _parse_date(entry.get("issued_on"))
        record["issued_on"] = issued_on.isoformat() if issued_on else None
        if not date_ok:
            record["review_flags"].append("INVALID_CREDIT_DATE")
            flags.append("INVALID_CREDIT_DATE")
        if amount_ok and amount > 0 and date_ok and not duplicate:
            total += amount
            record["counted"] = True
        credits.append(record)
    return credits, total, flags


def _process_comms(raw, known_ids, registry):
    comms = []
    unknown = []
    if not isinstance(raw, list):
        raw = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        comm_id = _text(entry.get("comm_id"))
        summary = _text(entry.get("summary"))
        invoice_id = _text(entry.get("invoice_id"))
        # `comm_id` is untrusted text in its own right, not merely a label.
        id_injected = _register(registry, _comm_loc(index, "comm_id"), comm_id)
        summary_injected = _register(
            registry, _comm_loc(index, "summary"), summary)
        locations = sorted(
            location for location, hit in (
                (_comm_loc(index, "comm_id"), id_injected),
                (_comm_loc(index, "summary"), summary_injected)) if hit)
        if id_injected:
            reference = PLACEHOLDER
        elif not _is_blank(comm_id):
            reference = comm_id
        else:
            reference = "communications[%d]" % index
        known = (not _is_blank(invoice_id)) and invoice_id in known_ids
        if not _is_blank(invoice_id) and not known:
            unknown.append(invoice_id)
        record = {
            "comm_id": comm_id,
            "invoice_id": invoice_id,
            "sent_on": _text(entry.get("sent_on")),
            "direction": _text(entry.get("direction")),
            "channel": _text(entry.get("channel")),
            "summary": summary,
            "known_invoice": known,
            "injection_flags": locations,
            "reference": reference,
        }
        comms.append(record)
    return comms, sorted(set(unknown))


def analyse(data):
    if not isinstance(data, dict):
        raise ValueError("输入必须是 JSON 对象")
    _scan_credentials(data)

    as_of = _parse_timestamp(data.get("as_of"), "as_of")
    as_of_date = as_of.date()

    raw_invoices = data.get("invoices")
    if not isinstance(raw_invoices, list):
        raise ValueError("invoices 必须是数组")

    currency_default = _text(data.get("currency_default"))

    registry = UntrustedTextRegistry()
    stages = _normalise_stages(data.get("followup_stages"), registry)

    company_raw = data.get("company")
    if isinstance(company_raw, dict):
        company_ref = _text(company_raw.get("company_ref"))
        display_name = _text(company_raw.get("display_name"))
        company_locations = []
        if _register(registry, "company.company_ref", company_ref):
            company_locations.append("company.company_ref")
        if _register(registry, "company.display_name", display_name):
            company_locations.append("company.display_name")
        company = {
            "company_ref": company_ref,
            "display_name": display_name,
            "injection_flags": sorted(company_locations),
        }
    else:
        company = None

    seen_invoice_ids = set()
    invoices = []

    for index, raw in enumerate(raw_invoices):
        if not isinstance(raw, dict):
            raise ValueError("invoices[%d] 必须是对象" % index)
        invoice_id = _text(raw.get("invoice_id"))
        if _is_blank(invoice_id):
            raise ValueError("invoices[%d] 缺少 invoice_id" % index)

        flags = []
        if invoice_id in seen_invoice_ids:
            flags.append("DUPLICATE_INVOICE_ID")
        else:
            seen_invoice_ids.add(invoice_id)

        invoice_locations = []
        id_injected = _register(
            registry, _invoice_loc(index, "invoice_id"), invoice_id)
        if id_injected:
            invoice_locations.append(_invoice_loc(index, "invoice_id"))
        for field in ("customer_ref", "contact_ref", "notes"):
            if _register(registry, _invoice_loc(index, field), raw.get(field)):
                invoice_locations.append(_invoice_loc(index, field))
        invoice_locations.sort()
        if invoice_locations:
            flags.append("PROMPT_INJECTION_IGNORED")
        # A flagged identifier is never used as a human-readable label.
        reference = PLACEHOLDER if id_injected else invoice_id

        currency = _text(raw.get("currency"))
        currency_source = "INPUT"
        if _is_blank(currency):
            if not _is_blank(currency_default):
                currency = currency_default
                currency_source = "DEFAULT"
                flags.append("CURRENCY_FROM_DEFAULT")
            else:
                currency = None
                currency_source = None
                flags.append("CURRENCY_MISSING")

        issued_on, issued_ok = _parse_date(raw.get("issued_on"))
        due_on, due_ok = _parse_date(raw.get("due_on"))
        if not issued_ok:
            flags.append("INVALID_ISSUED_ON")
        if not due_ok:
            flags.append("INVALID_DUE_ON")

        net_amount, net_ok = _parse_money(raw.get("net_amount"))
        if not net_ok or net_amount is None or net_amount <= 0:
            flags.append("INVALID_AMOUNT")
            net_amount = None

        payments, confirmed, pending, payment_flags = _process_payments(
            raw.get("payments"), currency, set())
        credits, credit_total, credit_flags = _process_credits(
            raw.get("credit_notes"), set())
        flags.extend(payment_flags)
        flags.extend(credit_flags)

        disputed = raw.get("disputed")
        if not isinstance(disputed, bool):
            if disputed is not None:
                flags.append("DISPUTED_FLAG_INVALID")
            disputed = None

        promised, promised_ok = _parse_date(raw.get("promised_payment_on"))
        if not promised_ok:
            flags.append("INVALID_PROMISE_DATE")
            promised = None
        promise_active = (promised is not None and promised >= as_of_date)
        if promised is not None and promised < as_of_date:
            flags.append("PROMISE_IN_PAST")

        if issued_on is not None and due_on is not None and due_on < issued_on:
            flags.append("DUE_BEFORE_ISSUED")

        terms = raw.get("terms") if isinstance(raw.get("terms"), dict) else None

        if net_amount is None:
            outstanding = None
        else:
            outstanding = net_amount - confirmed - credit_total
        if outstanding is not None and outstanding < 0:
            flags.append("NEGATIVE_OUTSTANDING")

        hard_invalid = any(flag in flags for flag in (
            "DUPLICATE_INVOICE_ID", "INVALID_AMOUNT", "CURRENCY_MISSING",
            "INVALID_DUE_ON", "INVALID_ISSUED_ON"))

        if due_on is None:
            days_until_due = None
            days_overdue = None
        else:
            days_until_due = (due_on - as_of_date).days
            days_overdue = max(0, (as_of_date - due_on).days)

        if hard_invalid:
            status = STATUS_INVALID
        elif disputed is True:
            status = STATUS_DISPUTED
        elif outstanding is not None and outstanding < 0:
            status = STATUS_OVERPAID
        elif outstanding is not None and outstanding == 0:
            status = STATUS_PAID
        elif promise_active:
            status = STATUS_PROMISED
        elif due_on is None:
            status = STATUS_DATE_UNKNOWN
        elif due_on < as_of_date:
            status = STATUS_OVERDUE
        elif days_until_due <= DUE_SOON_DAYS:
            status = STATUS_DUE_SOON
        else:
            status = STATUS_CURRENT

        current_stage = None
        following_stage = None
        if status == STATUS_OVERDUE and days_overdue is not None:
            current_stage, following_stage = _match_stages(stages, days_overdue)

        if status == STATUS_OVERDUE:
            if stages:
                next_action = "SEND_STAGE_DRAFT"
            else:
                next_action = "REVIEW_OVERDUE_MANUALLY"
            if following_stage is not None and due_on is not None:
                next_action_on = (due_on + timedelta(
                    days=following_stage["min_days_overdue"])).isoformat()
            else:
                next_action_on = None
        elif status == STATUS_DISPUTED:
            next_action, next_action_on = "AWAIT_DISPUTE_RESOLUTION", None
        elif status == STATUS_PROMISED:
            next_action = "VERIFY_PROMISED_PAYMENT"
            next_action_on = promised.isoformat() if promised else None
        elif status == STATUS_DATE_UNKNOWN:
            next_action, next_action_on = "CONFIRM_DUE_DATE", None
        elif status == STATUS_DUE_SOON:
            next_action = "SEND_PRE_DUE_REMINDER"
            next_action_on = due_on.isoformat() if due_on else None
        elif status == STATUS_OVERPAID:
            next_action, next_action_on = "REVIEW_OVERPAYMENT", None
        else:
            next_action, next_action_on = "NONE", None

        invoices.append({
            "invoice_id": invoice_id,
            "input_index": index,
            "reference": reference,
            "customer_ref": _text(raw.get("customer_ref")),
            "currency": currency,
            "currency_source": currency_source,
            "issued_on": issued_on.isoformat() if issued_on else None,
            "due_on": due_on.isoformat() if due_on else None,
            "net_amount": _money(net_amount) if net_amount is not None else None,
            "confirmed_paid": _money(confirmed),
            "pending_paid": _money(pending),
            "credits": _money(credit_total),
            "outstanding": _money(outstanding) if outstanding is not None else None,
            "days_overdue": days_overdue,
            "days_until_due": days_until_due,
            "status": status,
            "priority": PRIORITY_BY_STATUS[status],
            "promised_payment_on": promised.isoformat() if promised else None,
            "promise_active": promise_active,
            "disputed": disputed,
            "terms_provided": terms is not None,
            "contact_ref": _text(raw.get("contact_ref")),
            "current_stage_id": current_stage["stage_id"] if current_stage else None,
            "next_stage_id": following_stage["stage_id"] if following_stage else None,
            "next_action": next_action,
            "next_action_on": next_action_on,
            "review_flags": sorted(set(flags)),
            "injection_flags": invoice_locations,
            "payments": payments,
            "credit_notes": credits,
        })

    known_ids = {row["invoice_id"] for row in invoices}
    communications, unknown_refs = _process_comms(
        data.get("communications"), known_ids, registry)

    comms_by_invoice = {}
    for comm in communications:
        if comm["known_invoice"]:
            comms_by_invoice.setdefault(comm["invoice_id"], []).append(comm)

    valid = [row for row in invoices if row["status"] != STATUS_INVALID]
    invalid = [row for row in invoices if row["status"] == STATUS_INVALID]

    status_counts = {name: 0 for name in STATUS_ORDER}
    for row in invoices:
        status_counts[row["status"]] += 1

    priority_counts = {"P1": 0, "P2": 0, "P3": 0, "P4": 0}
    for row in invoices:
        priority_counts[row["priority"]] += 1

    global_status = "CURRENT"
    for name, predicate in GLOBAL_STATUS_ORDER:
        if predicate(status_counts, len(valid)):
            global_status = name
            break

    def queue_key(row):
        overdue = row["days_overdue"] if row["days_overdue"] is not None else 0
        outstanding = Decimal(row["outstanding"]) if row["outstanding"] else Decimal("0")
        return (STATUS_RANK[row["status"]], -overdue, -outstanding, row["invoice_id"])

    queue_rows = [row for row in invoices if row["priority"] in ("P1", "P2", "P3")]
    queue_rows.sort(key=queue_key)
    followup_queue = [row["invoice_id"] for row in queue_rows]

    totals = {}
    for row in valid:
        currency = row["currency"]
        if currency is None:
            continue
        bucket = totals.setdefault(currency, {
            "invoice_count": 0,
            "outstanding": Decimal("0"),
            "overdue_outstanding": Decimal("0"),
        })
        bucket["invoice_count"] += 1
        if row["outstanding"] is not None:
            bucket["outstanding"] += Decimal(row["outstanding"])
            if row["status"] == STATUS_OVERDUE:
                bucket["overdue_outstanding"] += Decimal(row["outstanding"])
    totals_by_currency = {}
    for currency in sorted(totals):
        bucket = totals[currency]
        totals_by_currency[currency] = {
            "invoice_count": bucket["invoice_count"],
            "outstanding": _money(bucket["outstanding"]),
            "overdue_outstanding": _money(bucket["overdue_outstanding"]),
        }

    evidence_gaps = []
    topic_buckets = {topic: [] for topic in QUESTION_TOPIC_ORDER}
    if invalid:
        topic_buckets["RECORD_INVALID"] = [row["invoice_id"] for row in invalid]

    for row in invoices:
        if row["status"] == STATUS_INVALID:
            continue
        invoice_id = row["invoice_id"]
        status = row["status"]
        actionable = status in ACTIONABLE_STATUSES
        comms = comms_by_invoice.get(invoice_id, [])
        has_inbound = any((comm.get("direction") or "").upper() == "INBOUND"
                          for comm in comms)
        pending = Decimal(row["pending_paid"] or "0")

        if not row["terms_provided"]:
            evidence_gaps.append({"invoice_id": invoice_id,
                                  "code": "CONTRACT_TERMS_MISSING"})
            topic_buckets["CONTRACT_TERMS"].append(invoice_id)
        if row["due_on"] is None:
            evidence_gaps.append({"invoice_id": invoice_id,
                                  "code": "DUE_DATE_MISSING"})
            topic_buckets["DUE_DATE"].append(invoice_id)
        if row["issued_on"] is None:
            evidence_gaps.append({"invoice_id": invoice_id,
                                  "code": "ISSUED_DATE_MISSING"})
        if actionable and _is_blank(row["contact_ref"]):
            evidence_gaps.append({"invoice_id": invoice_id,
                                  "code": "CONTACT_MISSING"})
            topic_buckets["CONTACT"].append(invoice_id)
        if actionable and not comms:
            evidence_gaps.append({"invoice_id": invoice_id,
                                  "code": "COMMUNICATION_LOG_MISSING"})
            topic_buckets["COMMUNICATION_LOG"].append(invoice_id)
        if pending > 0:
            evidence_gaps.append({"invoice_id": invoice_id,
                                  "code": "PENDING_PAYMENT_UNVERIFIED"})
            topic_buckets["PAYMENT_STATUS"].append(invoice_id)
        if status == STATUS_OVERDUE and not stages:
            evidence_gaps.append({"invoice_id": invoice_id,
                                  "code": "FOLLOWUP_STAGES_MISSING"})
            topic_buckets["FOLLOWUP_STAGES"].append(invoice_id)
        if row["disputed"] is True:
            evidence_gaps.append({"invoice_id": invoice_id,
                                  "code": "DISPUTE_OWNER_UNKNOWN"})
            topic_buckets["DISPUTE_OWNER"].append(invoice_id)
        if row["promise_active"] and not has_inbound:
            evidence_gaps.append({"invoice_id": invoice_id,
                                  "code": "PROMISE_UNCONFIRMED"})
            topic_buckets["PROMISE_CONFIRMATION"].append(invoice_id)
        for flag in row["review_flags"]:
            if flag == "PAYMENT_STATUS_UNKNOWN":
                topic_buckets["PAYMENT_STATUS"].append(invoice_id)
                break

    evidence_gaps.sort(key=lambda item: (item["invoice_id"], item["code"]))

    clarification_questions = []
    for topic in QUESTION_TOPIC_ORDER:
        ids = sorted(set(topic_buckets.get(topic, [])))
        if not ids:
            continue
        clarification_questions.append({
            "question_id": "Q-%02d" % (len(clarification_questions) + 1),
            "topic": topic,
            "invoice_ids": ids,
            "question": QUESTION_TEXT[topic],
        })

    drafts = []
    for row in queue_rows:
        status = row["status"]
        if status not in (STATUS_OVERDUE, STATUS_DUE_SOON, STATUS_PROMISED,
                          STATUS_DATE_UNKNOWN):
            continue
        placeholders = []
        if _is_blank(row["customer_ref"]):
            placeholders.append("客户名称")
        if _is_blank(row["contact_ref"]):
            placeholders.append("客户联系人")
        if row["due_on"] is None and status in (STATUS_OVERDUE, STATUS_DUE_SOON):
            placeholders.append("到期日")
        currency = row["currency"] or "【待确认：币种】"
        outstanding = row["outstanding"] or "【待确认：未收余额】"
        due_text = row["due_on"] or "【待确认：到期日】"
        head = "【人工确认后发送】关于发票 %s（%s %s）" % (
            row["reference"], currency, outstanding)
        if status == STATUS_OVERDUE:
            body = "，到期日 %s，当前逾期 %s 天。请协助确认付款安排；如已付款，请提供付款凭证。" % (
                due_text, row["days_overdue"])
        elif status == STATUS_DUE_SOON:
            body = "，将于 %s 到期（还有 %s 天）。请协助确认付款安排。" % (
                due_text, row["days_until_due"])
        elif status == STATUS_PROMISED:
            body = "，客户已承诺于 %s 付款，请核对到账情况。" % (
                row["promised_payment_on"] or "【待确认：承诺付款日】")
        else:
            body = "，到期日尚未确认，请先核实账期与到期日。"
        drafts.append({
            "draft_id": "D-%03d" % (len(drafts) + 1),
            "invoice_id": row["invoice_id"],
            "stage_id": row["current_stage_id"],
            "draft_status": "DRAFT_HUMAN_CONFIRM",
            "send_allowed": False,
            "placeholders": placeholders,
            "text": head + body + "本消息由人工核对后发送。",
        })

    review_flag_counts = {}
    for row in invoices:
        for flag in row["review_flags"]:
            review_flag_counts[flag] = review_flag_counts.get(flag, 0) + 1

    injection_flagged = registry.locations()

    # Human-readable id labels: a flagged identifier is never shown verbatim.
    label_by_id = {}
    for row in invoices:
        label_by_id.setdefault(row["invoice_id"], row["reference"])

    overdue_ids = sorted(row["invoice_id"] for row in invoices
                         if row["status"] == STATUS_OVERDUE)
    disputed_ids = sorted(row["invoice_id"] for row in invoices
                          if row["status"] == STATUS_DISPUTED)
    promised_ids = sorted(row["invoice_id"] for row in invoices
                          if row["status"] == STATUS_PROMISED)
    unknown_due_ids = sorted(row["invoice_id"] for row in invoices
                             if row["status"] == STATUS_DATE_UNKNOWN)
    invalid_ids = sorted(row["invoice_id"] for row in invoices
                         if row["status"] == STATUS_INVALID)

    markdown = _render_markdown(
        as_of=data.get("as_of"), global_status=global_status,
        status_counts=status_counts, currency_default=currency_default,
        totals=totals_by_currency, queue_rows=queue_rows, invoices=valid,
        evidence_gaps=evidence_gaps, questions=clarification_questions,
        draft_count=len(drafts), injection_flagged=injection_flagged,
        review_flag_counts=review_flag_counts,
        label_by_id=label_by_id,
        unique_invoice_id_count=len(known_ids))

    return {
        "status": global_status,
        "as_of": data.get("as_of"),
        "company": company,
        "currency_default": currency_default,
        "invoice_count": len(invoices),
        "unique_invoice_id_count": len(known_ids),
        "valid_invoice_count": len(valid),
        "invalid_invoice_count": len(invalid),
        "status_counts": status_counts,
        "priority_counts": priority_counts,
        "review_flag_counts": review_flag_counts,
        "followup_queue": followup_queue,
        "overdue_invoice_ids": overdue_ids,
        "disputed_invoice_ids": disputed_ids,
        "promised_invoice_ids": promised_ids,
        "unknown_due_date_invoice_ids": unknown_due_ids,
        "invalid_invoice_ids": invalid_ids,
        "totals_by_currency": totals_by_currency,
        "followup_stage_count": len(stages),
        "communication_count": len(communications),
        "unknown_communication_refs": unknown_refs,
        "invoices": invoices,
        "communications": communications,
        "evidence_gap_count": len(evidence_gaps),
        "evidence_gaps": evidence_gaps,
        "draft_count": len(drafts),
        "drafts": drafts,
        "injection_flagged": injection_flagged,
        "clarification_questions": clarification_questions,
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
    }


def _table(header, rows):
    lines = ["| " + " | ".join(header) + " |",
             "|" + "---|" * len(header)]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def _render_markdown(as_of, global_status, status_counts, currency_default,
                     totals, queue_rows, invoices, evidence_gaps, questions,
                     draft_count, injection_flagged, review_flag_counts,
                     label_by_id, unique_invoice_id_count):
    invoice_total = sum(status_counts.values())
    invalid = status_counts[STATUS_INVALID]
    valid = invoice_total - invalid

    def label(invoice_id):
        """Safe human-readable id label; flagged ids become the placeholder."""
        return label_by_id.get(invoice_id, invoice_id)

    def customer_label(row):
        flagged = _invoice_loc(row["input_index"], "customer_ref") \
            in row["injection_flags"]
        return _display(row["customer_ref"], flagged)

    lines = ["# 应收款跟进准备包", ""]
    lines.append("- 基准时间：" + _escape(as_of))
    lines.append("- 整体状态：" + _escape(global_status))
    if _is_blank(currency_default):
        lines.append("- 基准币种：未提供")
    else:
        lines.append("- 基准币种：" + _escape(currency_default))
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines += _table(["指标", "值", "指标", "值"], [
        ["发票数", str(invoice_total), "唯一发票号", str(unique_invoice_id_count)],
        ["可用发票", str(valid), "不可用发票", str(invalid)],
        ["逾期", str(status_counts[STATUS_OVERDUE]),
         "争议挂起", str(status_counts[STATUS_DISPUTED])],
        ["承诺付款", str(status_counts[STATUS_PROMISED]),
         "到期日未知", str(status_counts[STATUS_DATE_UNKNOWN])],
        ["即将到期", str(status_counts[STATUS_DUE_SOON]),
         "已结清", str(status_counts[STATUS_PAID])],
        ["跟进草稿", str(draft_count), "证据缺口", str(len(evidence_gaps))],
    ])
    lines.append("")
    lines.append("## 分币种未收余额")
    lines.append("")
    if totals:
        rows = []
        for currency in sorted(totals):
            bucket = totals[currency]
            rows.append([_escape(currency), str(bucket["invoice_count"]),
                         _escape(bucket["outstanding"]),
                         _escape(bucket["overdue_outstanding"])])
        lines += _table(["币种", "发票数", "未收余额", "逾期未收"], rows)
    else:
        lines.append("- 无可用发票，未生成任何币种合计。")
    lines.append("")
    lines.append("## 跟进队列")
    lines.append("")
    if queue_rows:
        rows = [[_escape(label(row["invoice_id"])), _escape(row["status"]),
                 _escape(row["priority"]), _escape(row["outstanding"])]
                for row in queue_rows]
        lines += _table(["发票", "状态", "优先级", "未收余额"], rows)
    else:
        lines.append("- 无需要人工跟进的发票。")
    lines.append("")
    lines.append("## 跟进明细")
    lines.append("")
    if invoices:
        for row in invoices:
            lines.append("- %s 客户：%s，状态 %s，优先级 %s，未收余额 %s" % (
                _escape(label(row["invoice_id"])), customer_label(row),
                _escape(row["status"]), _escape(row["priority"]),
                _escape(row["outstanding"])))
    else:
        lines.append("- 无可用发票记录。")
    lines.append("")
    lines.append("## 证据缺口")
    lines.append("")
    if evidence_gaps:
        rows = []
        for gap in evidence_gaps:
            rows.append([
                _escape(label(gap["invoice_id"])), _escape(gap["code"]),
                _escape(GAP_LABELS.get(gap["code"], gap["code"])), "人工补齐",
            ])
        lines += _table(["发票", "缺口代码", "说明", "建议动作"], rows)
    else:
        lines.append("- 未发现证据缺口。")
    lines.append("")
    lines.append("## 待澄清问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- %s [%s] %s %s" % (
                _escape(question["question_id"]), _escape(question["topic"]),
                _escape(question["question"]),
                "、".join(_escape(label(i)) for i in question["invoice_ids"])))
    else:
        lines.append("- 无需澄清的问题。")
    lines.append("")
    lines.append("## 风险与提示注入")
    lines.append("")
    if injection_flagged:
        # Locations are engine-generated identifiers, never untrusted text.
        lines.append("- 提示注入来源：%s（原文已隐藏：%s）" % (
            "、".join(injection_flagged), PLACEHOLDER))
    else:
        lines.append("- 未检测到提示注入。")
    if review_flag_counts:
        lines.append("- 风险标记：" + "、".join(
            "%s×%d" % (_escape(name), count)
            for name, count in sorted(review_flag_counts.items())))
    else:
        lines.append("- 无风险标记。")
    lines.append("- 本输出不含滞纳金、利息或费用计算；所有草稿均需人工确认后发送。")
    return "\n".join(lines) + "\n"


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("用法：run.py <input.json>\n")
        return 2
    try:
        data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except Exception:
        sys.stderr.write("输入 JSON 无法解析。\n")
        return 2
    try:
        result = analyse(data)
    except InputRejected:
        sys.stderr.write("检测到疑似凭据或敏感字段，已拒绝处理，未回显输入内容。\n")
        return 2
    except ValueError as exc:
        sys.stderr.write("输入结构不合法：%s\n" % exc)
        return 2
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
