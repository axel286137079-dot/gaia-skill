#!/usr/bin/env python3
"""采购收货差异与待处理清单 — offline purchase-order receiving exception engine.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes, no WMS/ERP/accounting reads. It never treats the ordered quantity as
received, never treats damaged or wrong-item stock as sellable, never guesses a
unit conversion, never judges quality or contract liability, never creates a
return/claim/purchase order, never updates inventory, never pays and never
contacts a supplier or carrier: it only sorts the anonymous receiving facts the
caller already supplied into a human-review exception pack.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

VERSION = "1.0.0"
SKILL = "suge-purchase-order-receiving-exception-pack"

# --------------------------------------------------------------------------
# fixed vocabulary (documented in references/guide.md)
# --------------------------------------------------------------------------
# Every ordered line ends in exactly one of nine line states. The tuple order is
# the severity order used everywhere: the batch takes the most severe line.
LINE_STATES = (
    "MATCHED", "PARTIALLY_RECEIVED", "SHORTAGE", "OVER_RECEIVED", "DAMAGED",
    "WRONG_ITEM", "QUANTITY_CONFLICT", "INSUFFICIENT_EVIDENCE", "BLOCKED",
)
SEVERITY = {
    "MATCHED": 0, "PARTIALLY_RECEIVED": 1, "SHORTAGE": 2, "OVER_RECEIVED": 3,
    "DAMAGED": 4, "WRONG_ITEM": 5, "QUANTITY_CONFLICT": 6,
    "INSUFFICIENT_EVIDENCE": 7, "BLOCKED": 8,
}
# The explicit order status the caller supplies. Unknown/missing stays unknown.
ORDER_STATE_ALIASES = {
    "OPEN": "OPEN", "PENDING": "OPEN", "IN_PROGRESS": "OPEN", "ACTIVE": "OPEN",
    "PARTIALLY_RECEIVED": "PARTIALLY_RECEIVED", "PARTIAL": "PARTIALLY_RECEIVED",
    "CLOSED": "CLOSED", "COMPLETED": "CLOSED", "DONE": "CLOSED",
    "RECEIVED": "CLOSED", "FULFILLED": "CLOSED",
    "CANCELLED": "CANCELLED", "CANCELED": "CANCELLED", "VOID": "CANCELLED",
    "UNKNOWN": "UNKNOWN",
}
# Line states that are "clean" for the still-outstanding logic.
DISPOSITION_FIELDS = ("accepted_qty", "damaged_qty", "wrong_item_qty",
                      "rejected_qty", "unaccounted_qty")

# Decision codes, in the fixed order findings are listed.
BLOCKER_ORDER = (
    "INVALID_LINE_RECORD", "MISSING_LINE_ID", "DUPLICATE_LINE_ID",
    "INVALID_ORDERED_QTY", "INVALID_EXPECTED_UNIT_COST", "INVALID_EXPECTED_BY",
    "INVALID_UNIT", "UNIT_CONFLICT", "INVALID_RECEIPT_RECORD",
    "MISSING_RECEIPT_ID", "DUPLICATE_RECEIPT_ID", "UNKNOWN_LINE_ID",
    "INVALID_RECEIVED_AT", "RECEIPT_BEFORE_ORDER", "INVALID_RECEIPT_QTY",
    "CURRENCY_CONFLICT", "UNKNOWN_CURRENCY", "ORDER_CANCELLED",
    "INVALID_ORDER_STATUS", "ORDER_CURRENCY_CONFLICT",
    "RESTRICTED_DATA_REFUSED", "PERSONAL_DATA_REFUSED",
    "SENSITIVE_ATTRIBUTE_REFUSED",
)
UNKNOWN_ORDER = (
    "MISSING_SKU_ID", "MISSING_UNIT", "MISSING_ORDERED_QTY",
    "MISSING_RECEIVED_AT", "MISSING_RECEIPT_QTY", "MISSING_OWNER",
    "UNKNOWN_OWNER", "MISSING_EVIDENCE",
)
CONFLICT_ORDER = ("DISPOSITION_MISMATCH", "ACCEPTED_EXCEEDS_RECEIVED")
REMINDER_ORDER = ("OVERDUE_NOT_ARRIVED", "LATE_DELIVERY")

# Batch-level codes (no single line path).
BATCH_BLOCKERS = ("ORDER_CANCELLED", "INVALID_ORDER_STATUS",
                  "ORDER_CURRENCY_CONFLICT", "UNKNOWN_CURRENCY")
BATCH_UNKNOWNS = ("MISSING_ORDER_STATUS", "MISSING_HANDLING_RULES",
                  "MISSING_OWNERS", "MISSING_ORDERED_LINES")
INCOMPLETE_CODES = ("MISSING_AS_OF", "INVALID_AS_OF", "MISSING_PO_ID",
                    "MISSING_PURCHASE_ORDER")

PLACEHOLDER = "已隐藏疑似提示注入文本"

# Credentials are refused outright (whole input), never echoed.
CRED_KEY = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|auth[_-]?token|"
    r"passphrase|bearer)",
    re.I,
)
CRED_VALUE = (
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE " + r"KEY-----"),
    re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
               r"[A-Za-z0-9_-]{10,}"),
)

# Personal data, restricted business content and protected attributes are
# refused per field (never echoed). Three reasons, one fixed vocabulary.
PERSONAL_KEYS = frozenset({
    "name", "fullname", "realname", "contactname", "firstname", "lastname",
    "phone", "mobile", "tel", "telephone", "email", "mail", "wechat", "weixin",
    "wechatid", "qq", "address", "homeaddress", "cookie", "sessionid",
    "idcard", "idnumber", "passport", "ssn", "contactemail", "contactphone",
    "suppliercontact", "buyername", "receivername", "phoneumber",
})
RESTRICTED_KEYS = frozenset({
    "account", "accountid", "bankaccount", "bankcard", "cardnumber", "iban",
    "swift", "invoice", "invoiceno", "invoicetext", "invoiceoriginal",
    "contract", "contracttext", "contractbody", "salary", "wage", "pay",
    "compensation", "payment", "amountpaid", "taxid", "vatnumber",
})
SENSITIVE_KEYS = frozenset({
    "gender", "sex", "age", "birthdate", "birthday", "race", "ethnicity",
    "religion", "health", "medical", "disability", "maritalstatus",
    "marriagestatus", "nationality", "politics", "politicalview",
    "pregnancy", "familystatus",
})
REASON_BY_GROUP = {
    "PERSONAL": "PERSONAL_DATA",
    "RESTRICTED": "RESTRICTED_DATA",
    "SENSITIVE": "SENSITIVE_ATTRIBUTE",
}
REFUSAL_BLOCKER = {
    "PERSONAL_DATA": "PERSONAL_DATA_REFUSED",
    "RESTRICTED_DATA": "RESTRICTED_DATA_REFUSED",
    "SENSITIVE_ATTRIBUTE": "SENSITIVE_ATTRIBUTE_REFUSED",
}
# Structured fields are id/qty/time facts, not free text: skip value scanning so
# an anonymous id or an ISO timestamp never looks like PII.
SKIP_VALUE_SCAN = frozenset({
    "as_of", "po_id", "supplier_id", "destination_id", "ordered_at", "currency",
    "order_status", "line_id", "sku_id", "ordered_qty", "unit",
    "expected_unit_cost", "expected_by", "receipt_id", "received_at",
    "received_qty", "accepted_qty", "damaged_qty", "wrong_item_qty",
    "rejected_qty", "unaccounted_qty", "owner_id", "id", "rule_id",
})
FREE_TEXT_FIELDS = ("notes", "label", "description", "text", "reason", "comment")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MOBILE_RE = re.compile(r"(?<![0-9A-Za-z-])1[3-9]\d{9}(?![0-9A-Za-z-])")
IDCARD_RE = re.compile(
    r"(?<![0-9A-Za-z-])(?:\d{17}[\dXx]|\d{15})(?![0-9A-Za-z-])")
CARD_RE = re.compile(r"(?<![0-9A-Za-z-])(?:\d[ -]?){15,19}(?![0-9A-Za-z-])")
ACCOUNT_RE = re.compile(r"(?<![0-9A-Za-z-])\d{9,20}(?![0-9A-Za-z-])")
PII_VALUE_RES = (EMAIL_RE, MOBILE_RE, IDCARD_RE, CARD_RE, ACCOUNT_RE)

# Injection is flagged only when an action verb and an instruction word appear
# in the SAME sentence, so ordinary receiving notes are not mislabelled.
INJ_ACTION = (
    "忽略", "无视", "跳过", "覆盖", "改写", "删除", "执行", "服从", "绕过",
    "ignore", "disregard", "override", "bypass", "forget",
)
INJ_TARGET = (
    "指令", "规则", "提示", "系统", "要求", "约束",
    "instruction", "rule", "prompt", "system", "constraint",
)

MD_ESCAPE = "\\`*_{}[]()#+-|<>~!"

DT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
CUR_RE = re.compile(r"^[A-Z]{3}$")

DISCLAIMER = (
    "本输出是采购收货差异的人工核对材料，不是质量判定、合同责任、索赔、付款或入库结论；"
    "缺少的记录或数量一律保持未知，绝不以订购量充抵实收量或把损坏品当作可售库存；"
    "是否索赔、是否退货、是否付款、是否入库、如何与供应商沟通，必须由人工确认。"
)

NO_AUTOMATION_DECLARATION = (
    "本工具不读取任何 WMS/ERP/库存/财务/邮箱/账号系统、不创建或修改采购单与收货单、"
    "不更新任何库存、不判断商品质量或合同责任、不发起索赔或退货、不付款、"
    "不联系供应商或承运商、不发送任何消息；它只把已提供的匿名收货事实整理成供人工复核的差异包。"
)

HUMAN_CONFIRM_BASE = (
    "是否接受到货、如何处理损坏或错货必须由人工决定，本工具不作任何质量或接受判断",
    "是否索赔、是否退货、是否让步接收必须由人工/法务决定，本工具不给合同或法律结论",
    "任何库存入库、上架、报废或调拨必须由人工或受控系统执行，本工具不写入任何库存系统",
    "任何付款、对账与发票处理必须由人工/财务执行，本工具不付款、不记账",
    "与供应商或承运商的任何沟通必须由人工执行，草稿一律为 DRAFT_NOT_SENT",
    "单位换算与币种换算必须由人工确认，本工具不换算也不跨单位/跨币种合并",
)

HUMAN_CHECKLIST_BASE = (
    "逐行核对数量桥：已订、实收、接受、损坏、错货、拒收、未解释与仍未到是否守恒",
    "先修正硬问题（重复编号、未知 line_id、非法数量或时间、单位/币种冲突、个人或受保护属性字段）再重跑",
    "对差异行补齐证据（照片或描述的文件名），缺失证据保持未知而不是当作已澄清",
    "对逾期未到与迟到到货逐条向负责人确认，不替任何人编造到货时间或数量",
    "核对每位负责人的待办；等待中的事项由人工跟进",
    "只对明确同意的对象准备 DRAFT_NOT_SENT 供应商沟通草稿，未同意或存在阻塞/证据不足时不生成任何内容",
    "人工执行所有入库、索赔、退货、付款与沟通动作，并对最终决定负责",
)

CODE_QUESTIONS = {
    "INVALID_LINE_RECORD": "该采购行不是对象，请改成含 line_id / sku_id / ordered_qty / unit 的记录后重试。",
    "MISSING_LINE_ID": "该采购行缺少匿名 line_id，请补一个不含个人信息的编号。",
    "DUPLICATE_LINE_ID": "该 line_id 重复，请人工确认后改成唯一编号。",
    "INVALID_ORDERED_QTY": "ordered_qty 非法（负数或非数值），请提供非负数值。",
    "INVALID_EXPECTED_UNIT_COST": "expected_unit_cost 非法，请提供非负数值或留空。",
    "INVALID_EXPECTED_BY": "expected_by 无法解析（需带时区偏移），请提供合规时间。",
    "INVALID_UNIT": "unit 非法，请提供单一计量单位字符串。",
    "UNIT_CONFLICT": "收货单单位与采购行单位不一致；本工具不换算单位，请人工核对后再提供。",
    "INVALID_RECEIPT_RECORD": "该收货单不是对象，请改成含 receipt_id / line_id / received_qty 的记录。",
    "MISSING_RECEIPT_ID": "该收货单缺少匿名 receipt_id，请补一个编号。",
    "DUPLICATE_RECEIPT_ID": "该 receipt_id 重复，请人工确认后改成唯一编号。",
    "UNKNOWN_LINE_ID": "该收货单引用的 line_id 不在采购行清单中，请人工确认或补该行。",
    "INVALID_RECEIVED_AT": "received_at 无法解析（需带时区偏移），请提供合规时间。",
    "RECEIPT_BEFORE_ORDER": "收货时间早于下单时间，疑似数据错误，请人工确认。",
    "INVALID_RECEIPT_QTY": "收货数量非法（负数或非数值），请提供非负数值。",
    "CURRENCY_CONFLICT": "收货单币种与采购单币种不一致；本工具不跨币种合并，请人工核对。",
    "UNKNOWN_CURRENCY": "币种不是三字母代码，无法识别；请提供标准币种代码或留空。",
    "ORDER_CANCELLED": "采购单已取消，不应继续处理其收货；请人工确认。",
    "INVALID_ORDER_STATUS": "订单状态不在允许词表内，请用 OPEN/PARTIALLY_RECEIVED/CLOSED/CANCELLED/UNKNOWN 之一。",
    "ORDER_CURRENCY_CONFLICT": "采购行/收货单币种与采购单币种不一致，本工具不跨币种合并。",
    "PERSONAL_DATA_REFUSED": "该字段疑似个人信息（姓名/电话/邮箱/地址等），请删除后再提供；本工具不处理也不回显。",
    "RESTRICTED_DATA_REFUSED": "该字段疑似受限业务内容（银行/发票原文/合同文本等），请删除后再提供；本工具不处理也不回显。",
    "SENSITIVE_ATTRIBUTE_REFUSED": "该字段属于受保护属性（性别/年龄/民族/宗教/健康等），本工具不使用，请删除。",
    "MISSING_SKU_ID": "缺少匿名 sku_id，请补齐后重试。",
    "MISSING_UNIT": "缺少计量单位，数量桥无法守恒；本工具不假设默认单位。",
    "MISSING_ORDERED_QTY": "缺少 ordered_qty，数量桥无法计算。",
    "MISSING_RECEIVED_AT": "收货单缺少收货时间（需带时区偏移）。",
    "MISSING_RECEIPT_QTY": "收货单缺少某项数量（received/accepted/damaged/wrong_item/rejected/unaccounted），数量桥无法守恒。",
    "MISSING_OWNER": "该采购行缺少负责人，请指定一位匿名 owner_id。",
    "UNKNOWN_OWNER": "该采购行的 owner_id 不在负责人清单中，请补充该负责人或改编号。",
    "MISSING_EVIDENCE": "记录损坏/错货/拒收的收货单缺少证据引用（单一文件名）。",
    "MISSING_ORDER_STATUS": "缺少订单状态，无法判断是否仍在等到货。",
    "MISSING_HANDLING_RULES": "尚未提供处理规则；本工具不内置平台或法律规则，请显式提供。",
    "MISSING_OWNERS": "尚未提供任何负责人，无法形成负责人待办。",
    "MISSING_ORDERED_LINES": "采购行清单为空，无内容可核对。",
    "DISPOSITION_MISMATCH": "该行数量分配之和（接受+损坏+错货+拒收+未解释）不等于实收，数量桥不守恒；请人工核对。",
    "ACCEPTED_EXCEEDS_RECEIVED": "接受数量大于实收数量，数量桥不自洽；请人工核对。",
    "OVERDUE_NOT_ARRIVED": "该行仍未到货且已过 expected_by，请人工确认是否仍然有效。",
    "LATE_DELIVERY": "该次收货晚于 expected_by，请人工确认。",
}
DEFAULT_QUESTION = "请补充该事项所需的事实。"

NEXT_STEP = {
    "MATCHED": "全部采购行数量桥守恒且无差异；仍由人工确认最终入库。",
    "PARTIALLY_RECEIVED": "存在部分到货且仍有未到数量，请人工跟进后续到货。",
    "SHORTAGE": "存在已过预期仍未到齐的短缺，请人工确认后续安排。",
    "OVER_RECEIVED": "存在超收，请人工确认超收部分的处理。",
    "DAMAGED": "存在损坏数量，请人工确认与供应商的后续处理。",
    "WRONG_ITEM": "存在错货数量，请人工确认与供应商的后续处理。",
    "QUANTITY_CONFLICT": "存在数量桥不守恒，请先人工核对数量再做任何判断。",
    "INSUFFICIENT_EVIDENCE": "关键事实或证据缺失，请先补齐再做判断。",
    "BLOCKED": "存在硬问题（重复编号、未知 line_id、非法数量/时间、单位/币种冲突、受限数据），请先修正输入。",
}


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


def as_list(value):
    return value if isinstance(value, list) else []


def as_dict(value):
    return value if isinstance(value, dict) else {}


def parse_dt(value):
    """Return a tz-aware datetime, or None. A value without an offset is invalid."""
    if not isinstance(value, str) or not DT_RE.match(value.strip()):
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def parse_qty(value):
    """Return (Decimal_or_None, status). status in ok/missing/invalid.

    Negative, non-numeric, boolean, NaN and infinite values are invalid so the
    caller can block them instead of silently correcting them.
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None, "missing"
    if isinstance(value, bool):
        return None, "invalid"
    if isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None, "invalid"
        result = Decimal(str(value))
    elif isinstance(value, str):
        try:
            result = Decimal(value.strip())
        except (InvalidOperation, ValueError):
            return None, "invalid"
        if not result.is_finite():
            return None, "invalid"
    else:
        return None, "invalid"
    if result < 0:
        return None, "invalid"
    return result, "ok"


def dec_str(value):
    """Plain, human-stable decimal string (no exponent, no trailing zeros)."""
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def norm(value):
    return re.sub(r"\s+", "", clean_text(value)).casefold()


def scan_injection(text):
    """True only when an action verb and an instruction word share one sentence."""
    cleaned = clean_text(text)
    if not cleaned:
        return False
    for sentence in re.split(r"[。！？!?;；\n]", cleaned):
        low = sentence.lower()
        if any(word.lower() in low for word in INJ_ACTION) and \
                any(word.lower() in low for word in INJ_TARGET):
            return True
    return False


def find_credentials(node, path=""):
    """Return field paths of credential-shaped keys or values (never the value)."""
    hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + "/" + str(key) if path else str(key)
            if isinstance(key, str) and CRED_KEY.search(key):
                hits.append(child)
            hits.extend(find_credentials(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(find_credentials(value, "%s[%d]" % (path, index)))
    elif isinstance(node, str):
        if any(pattern.search(node) for pattern in CRED_VALUE):
            hits.append(path or "value")
    seen, out = set(), []
    for item in hits:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _flat_key(key):
    return re.sub(r"[^a-z0-9]", "", key.casefold())


def scan_forbidden(node, path=""):
    """Return [{path, reason}] for personal / restricted / protected fields.

    Only the location and reason are returned, so a refusal can name where the
    problem is without ever echoing the value back to the caller.
    """
    hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + "/" + str(key) if path else str(key)
            if isinstance(key, str):
                flat = _flat_key(key)
                group = None
                if flat in PERSONAL_KEYS:
                    group = "PERSONAL"
                elif flat in RESTRICTED_KEYS:
                    group = "RESTRICTED"
                elif flat in SENSITIVE_KEYS:
                    group = "SENSITIVE"
                if group:
                    hits.append({"path": child,
                                 "reason": REASON_BY_GROUP[group]})
                if isinstance(value, str) and flat not in SKIP_VALUE_SCAN and \
                        not group and _free_text_key(flat):
                    if any(pattern.search(clean_text(value))
                           for pattern in PII_VALUE_RES):
                        hits.append({"path": child, "reason": "PERSONAL_DATA"})
            hits.extend(scan_forbidden(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(scan_forbidden(value, "%s[%d]" % (path, index)))
    return hits


def _free_text_key(flat):
    return any(marker in flat for marker in
               ("note", "label", "description", "text", "reason", "comment",
                "rule", "responsib", "name", "title", "message"))


def safe_ref(value):
    """Return (sanitised_basename_or_None, refusal_reason_or_None) for a reference."""
    text = clean_text(value)
    if not text:
        return None, None
    if URL_RE.match(text):
        return None, "URL_REFERENCE"
    if "/" in text or "\\" in text or ":" in text or ".." in text:
        return None, "PATH_REFERENCE"
    if not BASENAME_RE.match(text):
        return None, "UNSAFE_REFERENCE"
    return text, None


def collect_refs(values, field, path_prefix):
    accepted, refused = [], []
    for index, raw in enumerate(values):
        name, reason = safe_ref(raw)
        location = "%s%s[%d]" % (path_prefix, field, index)
        if reason:
            refused.append({"path": location, "reason": reason})
        elif name:
            accepted.append({"path": location, "name": name})
    return accepted, refused


def hidden_value(path, value, hidden):
    text = clean_text(value)
    if not text:
        return None
    if path in hidden:
        return PLACEHOLDER
    return text


def normalise_order_status(raw):
    """Return (canonical_status_or_None, was_missing, was_invalid)."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None, True, False
    if not isinstance(raw, str):
        return None, False, True
    token = re.sub(r"[\s-]+", "_", raw.strip()).upper()
    if token in ORDER_STATE_ALIASES:
        return ORDER_STATE_ALIASES[token], False, False
    return None, False, True


# --------------------------------------------------------------------------
# per-record builders
# --------------------------------------------------------------------------
def _blank_line(field, path, index, code):
    return {"path": path, "index": index, "line_id": None, "sku_id": None,
            "ordered": None, "unit": None, "expected_unit_cost": None,
            "expected_by": None, "owner_id": None, "currency": None,
            "evidence_refs": [], "refused_refs": [],
            "blockers": [code], "unknowns": [], "conflicts": [], "reminders": [],
            "findings": [code], "state": "BLOCKED", "receipts": [],
            "bridge": None, "late": False, "overdue": False,
            "invalid": True, "placeholder": False}


def build_line(path, index, raw, owners_index, order_currency, order_status,
               as_of_dt, hidden):
    line_id = clean_text(raw.get("line_id")) or None
    sku_id = clean_text(raw.get("sku_id")) or None
    unit = clean_text(raw.get("unit")) or None
    owner_id = clean_text(raw.get("owner_id")) or None
    currency = clean_text(raw.get("currency")) or None
    refs, refused_refs = collect_refs(as_list(raw.get("evidence_refs")),
                                      "evidence_refs", path + "/")

    blockers, unknowns, conflicts, reminders = [], [], [], []
    if not line_id:
        blockers.append("MISSING_LINE_ID")
    if not sku_id:
        unknowns.append("MISSING_SKU_ID")
    if not unit:
        unknowns.append("MISSING_UNIT")
    elif not isinstance(raw.get("unit"), str):
        blockers.append("INVALID_UNIT")
    if not owner_id:
        unknowns.append("MISSING_OWNER")
    elif owner_id not in owners_index:
        unknowns.append("UNKNOWN_OWNER")

    ordered, ostatus = parse_qty(raw.get("ordered_qty"))
    if ostatus == "missing":
        unknowns.append("MISSING_ORDERED_QTY")
    elif ostatus == "invalid":
        blockers.append("INVALID_ORDERED_QTY")

    unit_cost = None
    if raw.get("expected_unit_cost") not in (None, ""):
        unit_cost, cstatus = parse_qty(raw.get("expected_unit_cost"))
        if cstatus != "ok":
            blockers.append("INVALID_EXPECTED_UNIT_COST")

    expected_by = None
    if raw.get("expected_by") not in (None, ""):
        expected_by = parse_dt(raw.get("expected_by"))
        if expected_by is None:
            blockers.append("INVALID_EXPECTED_BY")

    if currency:
        if not CUR_RE.match(currency):
            blockers.append("UNKNOWN_CURRENCY")
        elif order_currency and currency.upper() != order_currency.upper():
            blockers.append("ORDER_CURRENCY_CONFLICT")

    entry = {"path": path, "index": index, "line_id": line_id,
             "sku_id": sku_id, "ordered": ordered, "unit": unit,
             "expected_unit_cost": unit_cost, "expected_by": expected_by,
             "owner_id": owner_id, "currency": currency,
             "evidence_refs": refs, "refused_refs": refused_refs,
             "blockers": blockers, "unknowns": unknowns, "conflicts": conflicts,
             "reminders": reminders, "findings": [], "state": "MATCHED",
             "receipts": [], "bridge": None, "late": False, "overdue": False,
             "invalid": False, "placeholder": False}
    refresh_line(entry)
    return entry


def build_receipt(path, index, raw, known_line_ids, line_units,
                  order_currency, order_started, hidden):
    receipt_id = clean_text(raw.get("receipt_id")) or None
    line_id = clean_text(raw.get("line_id")) or None
    unit = clean_text(raw.get("unit")) or None
    currency = clean_text(raw.get("currency")) or None
    refs, refused_refs = collect_refs(as_list(raw.get("evidence_refs")),
                                      "evidence_refs", path + "/")

    blockers, unknowns = [], []
    if not receipt_id:
        blockers.append("MISSING_RECEIPT_ID")
    if not line_id:
        blockers.append("UNKNOWN_LINE_ID")
    elif line_id not in known_line_ids:
        blockers.append("UNKNOWN_LINE_ID")

    received_at = None
    if raw.get("received_at") in (None, ""):
        unknowns.append("MISSING_RECEIVED_AT")
    else:
        received_at = parse_dt(raw.get("received_at"))
        if received_at is None:
            blockers.append("INVALID_RECEIVED_AT")
        elif order_started is not None and received_at < order_started:
            blockers.append("RECEIPT_BEFORE_ORDER")

    quantities = {"received_qty": None, "accepted_qty": None,
                  "damaged_qty": None, "wrong_item_qty": None,
                  "rejected_qty": None, "unaccounted_qty": None}
    for field in quantities:
        value, status = parse_qty(raw.get(field))
        if status == "missing":
            unknowns.append("MISSING_RECEIPT_QTY")
        elif status == "invalid":
            blockers.append("INVALID_RECEIPT_QTY")
        else:
            quantities[field] = value

    if unit and line_id in line_units and line_units[line_id] and \
            unit != line_units[line_id]:
        blockers.append("UNIT_CONFLICT")

    if currency:
        if not CUR_RE.match(currency):
            blockers.append("UNKNOWN_CURRENCY")
        elif order_currency and currency.upper() != order_currency.upper():
            blockers.append("CURRENCY_CONFLICT")

    damaged = quantities.get("damaged_qty") or Decimal(0)
    wrong = quantities.get("wrong_item_qty") or Decimal(0)
    rejected = quantities.get("rejected_qty") or Decimal(0)
    if (damaged > 0 or wrong > 0 or rejected > 0) and not refs:
        unknowns.append("MISSING_EVIDENCE")

    entry = {"path": path, "index": index, "receipt_id": receipt_id,
             "line_id": line_id, "received_at": received_at,
             "quantities": quantities, "unit": unit, "currency": currency,
             "evidence_refs": refs, "refused_refs": refused_refs,
             "blockers": blockers, "unknowns": unknowns}
    refresh_receipt(entry)
    return entry


def refresh_receipt(entry):
    if entry["blockers"]:
        entry["state"] = "BLOCKED"
    elif entry["unknowns"]:
        entry["state"] = "INSUFFICIENT_EVIDENCE"
    else:
        entry["state"] = "RECORDED"
    return entry


def _ordered(codes, order):
    return [code for code in order if code in set(codes)]


def refresh_line(entry):
    """Recompute findings and the single line state from the four buckets."""
    entry["findings"] = (_ordered(entry["blockers"], BLOCKER_ORDER) +
                         _ordered(entry["unknowns"], UNKNOWN_ORDER) +
                         _ordered(entry["conflicts"], CONFLICT_ORDER) +
                         _ordered(entry["reminders"], REMINDER_ORDER))
    entry["state"] = derive_line_state(entry)
    return entry


def derive_line_state(entry):
    if entry["blockers"]:
        return "BLOCKED"
    if entry["unknowns"]:
        return "INSUFFICIENT_EVIDENCE"
    if entry["conflicts"]:
        return "QUANTITY_CONFLICT"
    bridge = entry["bridge"]
    if bridge:
        damaged = bridge.get("damaged_qty") or Decimal(0)
        wrong = bridge.get("wrong_item_qty") or Decimal(0)
        if wrong > 0:
            return "WRONG_ITEM"
        if damaged > 0:
            return "DAMAGED"
        ordered = bridge.get("ordered_qty")
        received = bridge.get("received_qty")
        if ordered is not None and received is not None and received > ordered:
            return "OVER_RECEIVED"
        outstanding = bridge.get("still_outstanding_qty")
        if outstanding is not None and outstanding > 0:
            return "SHORTAGE" if entry["overdue"] else "PARTIALLY_RECEIVED"
    return "MATCHED"


def add_code(entry, bucket, code):
    if code not in entry[bucket]:
        entry[bucket].append(code)


def mark_duplicate_line(entry):
    add_code(entry, "blockers", "DUPLICATE_LINE_ID")
    refresh_line(entry)


def mark_duplicate_receipt(entry):
    add_code(entry, "blockers", "DUPLICATE_RECEIPT_ID")
    refresh_receipt(entry)


def compute_bridge(entry):
    """Fold receipts into the conserved quantity bridge for one line.

    A blocked line is never bridged: an unusable record must not look
    quantified, and the still-outstanding figure is only trustworthy once every
    quantity on the line is present and non-negative.
    """
    if entry["invalid"] or entry["blockers"] or \
            "MISSING_ORDERED_QTY" in entry["unknowns"] or \
            any(r["state"] != "RECORDED" for r in entry["receipts"]):
        entry["bridge"] = None
        return
    ordered = entry["ordered"]
    received = sum((r["quantities"]["received_qty"] for r in entry["receipts"]),
                   Decimal(0))
    accepted = sum((r["quantities"]["accepted_qty"] for r in entry["receipts"]),
                   Decimal(0))
    damaged = sum((r["quantities"]["damaged_qty"] for r in entry["receipts"]),
                  Decimal(0))
    wrong = sum((r["quantities"]["wrong_item_qty"] for r in entry["receipts"]),
                Decimal(0))
    rejected = sum((r["quantities"]["rejected_qty"] for r in entry["receipts"]),
                   Decimal(0))
    unaccounted = sum((r["quantities"]["unaccounted_qty"] for r in entry["receipts"]),
                      Decimal(0))
    entry["bridge"] = {
        "ordered_qty": ordered, "received_qty": received, "accepted_qty": accepted,
        "damaged_qty": damaged, "wrong_item_qty": wrong, "rejected_qty": rejected,
        "unaccounted_qty": unaccounted,
        "still_outstanding_qty": ordered - received,
    }


def check_conservation(entry):
    """Disposition sum must equal received; accepted may not exceed received."""
    if entry["bridge"] is None:
        return
    bridge = entry["bridge"]
    disposition = (bridge["accepted_qty"] + bridge["damaged_qty"] +
                   bridge["wrong_item_qty"] + bridge["rejected_qty"] +
                   bridge["unaccounted_qty"])
    if bridge["accepted_qty"] > bridge["received_qty"]:
        add_code(entry, "conflicts", "ACCEPTED_EXCEEDS_RECEIVED")
    if disposition != bridge["received_qty"]:
        add_code(entry, "conflicts", "DISPOSITION_MISMATCH")


def attach_refusals(lines, receipts_by_path, refused_fields, batch_blockers):
    """Attach personal/restricted/protected refusals to the exact record."""
    line_by_path = {entry["path"]: entry for entry in lines}
    receipt_by_path = receipts_by_path
    for item in refused_fields:
        head = item["path"].split("/")[0]
        blocker = REFUSAL_BLOCKER[item["reason"]]
        if head in line_by_path:
            add_code(line_by_path[head], "blockers", blocker)
            refresh_line(line_by_path[head])
        elif head in receipt_by_path:
            add_code(receipt_by_path[head], "blockers", blocker)
            refresh_receipt(receipt_by_path[head])
        else:
            batch_blockers.append(blocker)


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def analyse(payload):
    as_of = payload.get("as_of")
    as_of_dt = parse_dt(as_of)
    order = as_dict(payload.get("purchase_order"))
    owners_raw = as_list(payload.get("owners"))
    lines_raw = as_list(payload.get("ordered_lines"))
    receipts_raw = as_list(payload.get("receipts"))

    po_id = clean_text(order.get("po_id"))
    supplier_id = clean_text(order.get("supplier_id"))
    destination_id = clean_text(order.get("destination_id"))
    ordered_at = parse_dt(order.get("ordered_at"))
    currency = clean_text(order.get("currency")) or None
    status_raw = order.get("order_status")
    order_status, status_missing, status_invalid = normalise_order_status(status_raw)
    if currency and not CUR_RE.match(currency):
        currency_unknown = True
        currency = currency
    else:
        currency_unknown = False

    # handling_rules must be supplied by the user; none are built in.
    handling_rules, rules_missing = [], False
    for index, raw in enumerate(as_list(payload.get("handling_rules"))):
        if isinstance(raw, dict):
            rule_id = clean_text(raw.get("rule_id")) or ("RULE-%d" % (index + 1))
            text = clean_text(raw.get("text")) or clean_text(raw.get("rule"))
        else:
            rule_id = "RULE-%d" % (index + 1)
            text = clean_text(raw)
        if text:
            handling_rules.append({"rule_id": rule_id, "text": text,
                                   "source": "USER_PROVIDED"})
    if not handling_rules:
        rules_missing = True

    # owners (stable order)
    owners, owner_ids, owner_index = [], [], {}
    owner_blockers, owner_unknowns = [], []
    if not owners_raw:
        owner_unknowns.append("MISSING_OWNERS")
    for index, raw in enumerate(owners_raw):
        path = "owners[%d]" % index
        if not isinstance(raw, dict):
            owner_blockers.append({"path": path, "code": "INVALID_LINE_RECORD"})
            continue
        oid = clean_text(raw.get("owner_id")) or None
        responsibilities = [clean_text(x) for x in as_list(raw.get("responsibilities"))
                            if has_text(x)]
        if oid is None:
            owner_blockers.append({"path": path, "code": "MISSING_LINE_ID"})
        elif oid in owner_index:
            owner_blockers.append({"path": path, "code": "DUPLICATE_LINE_ID"})
        else:
            owner_index[oid] = index
        owners.append({"path": path, "owner_id": oid,
                       "responsibilities": responsibilities, "raw": raw})
        if oid:
            owner_ids.append(oid)

    # --- personal / restricted / protected (never echoed) -----------------
    forbidden = scan_forbidden(payload)
    refused_fields, seen_fields = [], set()
    for item in forbidden:
        key = (item["path"], item["reason"])
        if key in seen_fields:
            continue
        seen_fields.add(key)
        refused_fields.append({"path": item["path"], "reason": item["reason"]})
    forbidden_paths = {item["path"] for item in refused_fields}

    # --- injection scan over free text ------------------------------------
    injection_flagged = []

    def scan(path, value):
        if value is None:
            return
        if scan_injection(value) and path not in injection_flagged:
            injection_flagged.append(path)

    scan("notes", payload.get("notes"))
    scan("purchase_order/order_status", status_raw if isinstance(status_raw, str) else None)
    for index, rule in enumerate(handling_rules):
        scan("handling_rules[%d]/text" % index, rule["text"])
    for index, owner in enumerate(owners):
        scan("owners[%d]/owner_id" % index, owner["raw"].get("owner_id"))
        for k, text in enumerate(as_list(owner["raw"].get("responsibilities"))):
            scan("owners[%d]/responsibilities[%d]" % (index, k), text)
    for index, raw in enumerate(lines_raw):
        if isinstance(raw, dict):
            for key in FREE_TEXT_FIELDS:
                scan("ordered_lines[%d]/%s" % (index, key), raw.get(key))
            scan("ordered_lines[%d]/sku_id" % index, raw.get("sku_id"))
    for index, raw in enumerate(receipts_raw):
        if isinstance(raw, dict):
            for key in FREE_TEXT_FIELDS:
                scan("receipts[%d]/%s" % (index, key), raw.get(key))
    hidden = set(injection_flagged) | forbidden_paths

    # --- duplicate line / receipt ids -------------------------------------
    line_ids_all = [clean_text(as_dict(raw).get("line_id"))
                    for raw in lines_raw if isinstance(raw, dict)]
    dup_line_ids = {lid for lid in line_ids_all
                    if lid and line_ids_all.count(lid) > 1}
    receipt_ids_all = [clean_text(as_dict(raw).get("receipt_id"))
                       for raw in receipts_raw if isinstance(raw, dict)]
    dup_receipt_ids = {rid for rid in receipt_ids_all
                       if rid and receipt_ids_all.count(rid) > 1}

    line_units = {}
    known_line_ids = set()
    for raw in lines_raw:
        if isinstance(raw, dict):
            lid = clean_text(raw.get("line_id"))
            if lid:
                known_line_ids.add(lid)
                line_units[lid] = clean_text(raw.get("unit")) or None

    # --- lines -------------------------------------------------------------
    lines = []
    for index, raw in enumerate(lines_raw):
        path = "ordered_lines[%d]" % index
        if not isinstance(raw, dict):
            lines.append(_blank_line("ordered_lines", path, index,
                                     "INVALID_LINE_RECORD"))
            continue
        entry = build_line(path, index, raw, owner_index, currency,
                           order_status, as_of_dt, hidden)
        if entry["line_id"] and entry["line_id"] in dup_line_ids:
            add_code(entry, "blockers", "DUPLICATE_LINE_ID")
        refresh_line(entry)
        lines.append(entry)
    if not lines_raw:
        batch_pre_unknowns = ["MISSING_ORDERED_LINES"]
    else:
        batch_pre_unknowns = []

    # --- receipts ----------------------------------------------------------
    line_by_id = {}
    for entry in lines:
        if entry["line_id"]:
            line_by_id.setdefault(entry["line_id"], entry)
    receipts, unassigned = [], []
    receipt_by_path = {}
    for index, raw in enumerate(receipts_raw):
        path = "receipts[%d]" % index
        if not isinstance(raw, dict):
            entry = {"path": path, "index": index, "receipt_id": None,
                     "line_id": None, "received_at": None, "quantities": {},
                     "unit": None, "currency": None, "evidence_refs": [],
                     "refused_refs": [], "blockers": ["INVALID_RECEIPT_RECORD"],
                     "unknowns": [], "state": "BLOCKED", "invalid": True}
            refresh_receipt(entry)
            unassigned.append(entry)
            receipt_by_path[path] = entry
            continue
        entry = build_receipt(path, index, raw, known_line_ids, line_units,
                              currency, ordered_at, hidden)
        if entry["receipt_id"] and entry["receipt_id"] in dup_receipt_ids:
            add_code(entry, "blockers", "DUPLICATE_RECEIPT_ID")
            refresh_receipt(entry)
        receipt_by_path[path] = entry
        if entry["line_id"] and entry["line_id"] in line_by_id:
            line_by_id[entry["line_id"]]["receipts"].append(entry)
            receipts.append(entry)
        else:
            unassigned.append(entry)
    all_receipts = receipts + unassigned

    # --- refusals attached to the exact record ----------------------------
    batch_blockers, batch_unknowns = [], list(batch_pre_unknowns)
    attach_refusals(lines, receipt_by_path, refused_fields, batch_blockers)

    # --- order-level findings ---------------------------------------------
    if order_status == "CANCELLED":
        batch_blockers.append("ORDER_CANCELLED")
    elif status_invalid:
        batch_blockers.append("INVALID_ORDER_STATUS")
    elif status_missing:
        batch_unknowns.append("MISSING_ORDER_STATUS")
    if currency_unknown:
        batch_blockers.append("UNKNOWN_CURRENCY")
    if rules_missing:
        batch_unknowns.append("MISSING_HANDLING_RULES")
    batch_unknowns.extend(owner_unknowns)
    for item in owner_blockers:
        batch_blockers.append(item["code"])

    # --- fold receipts into lines and bridge -------------------------------
    for entry in lines:
        if entry["invalid"]:
            continue
        for receipt in entry["receipts"]:
            for code in receipt["blockers"]:
                add_code(entry, "blockers", code)
            for code in receipt["unknowns"]:
                add_code(entry, "unknowns", code)
        compute_bridge(entry)
        if entry["bridge"] is not None:
            expected_by = entry["expected_by"]
            for receipt in entry["receipts"]:
                received_at = receipt["received_at"]
                if expected_by is not None and received_at is not None and \
                        received_at > expected_by:
                    entry["late"] = True
            outstanding = entry["bridge"]["still_outstanding_qty"]
            if outstanding > 0:
                overdue = (expected_by is not None and expected_by < as_of_dt) or \
                          order_status in ("CLOSED", "CANCELLED")
                entry["overdue"] = overdue
                if overdue:
                    add_code(entry, "reminders", "OVERDUE_NOT_ARRIVED")
            if entry["late"]:
                add_code(entry, "reminders", "LATE_DELIVERY")
            check_conservation(entry)
        refresh_line(entry)

    # --- aggregation -------------------------------------------------------
    blockers = list(batch_blockers)
    unknowns = list(batch_unknowns)
    for entry in lines:
        if entry["placeholder"]:
            continue
        for code in entry["blockers"]:
            if code in BLOCKER_ORDER:
                blockers.append(code)
        for code in entry["unknowns"]:
            if code in UNKNOWN_ORDER:
                unknowns.append(code)
    for receipt in unassigned:
        for code in receipt["blockers"]:
            if code in BLOCKER_ORDER:
                blockers.append(code)
        for code in receipt["unknowns"]:
            if code in UNKNOWN_ORDER:
                unknowns.append(code)
    blockers = _ordered(blockers, BLOCKER_ORDER)
    unknowns = [c for c in UNKNOWN_ORDER if c in set(unknowns)] + \
               [c for c in BATCH_UNKNOWNS if c in set(unknowns)]

    if blockers:
        receiving_state = "BLOCKED"
    elif unknowns:
        receiving_state = "INSUFFICIENT_EVIDENCE"
    else:
        receiving_state = "MATCHED"
        for entry in lines:
            if entry["placeholder"]:
                continue
            if SEVERITY[entry["state"]] > SEVERITY[receiving_state]:
                receiving_state = entry["state"]

    # --- totals (per unit; never merged across units or currencies) --------
    # Only lines whose bridge conserves are consolidated, so every figure in
    # `quantity_bridge_totals` satisfies accepted+damaged+wrong+rejected+
    # unaccounted == received. Non-conserving lines are named explicitly.
    totals = {}
    excluded_lines = []
    for entry in lines:
        if entry["bridge"] is None or not entry["unit"]:
            if not entry["placeholder"] and entry["bridge"] is None:
                excluded_lines.append(entry["line_id"] or entry["path"])
            continue
        if entry["conflicts"]:
            excluded_lines.append(entry["line_id"] or entry["path"])
            continue
        key = entry["unit"]
        bucket = totals.setdefault(key, {
            "unit": key, "currency": currency,
            "ordered_qty": Decimal(0), "received_qty": Decimal(0),
            "accepted_qty": Decimal(0), "damaged_qty": Decimal(0),
            "wrong_item_qty": Decimal(0), "rejected_qty": Decimal(0),
            "unaccounted_qty": Decimal(0), "still_outstanding_qty": Decimal(0),
            "line_count": 0})
        bridge = entry["bridge"]
        for field in ("ordered_qty", "received_qty", "accepted_qty", "damaged_qty",
                      "wrong_item_qty", "rejected_qty", "unaccounted_qty",
                      "still_outstanding_qty"):
            bucket[field] += bridge[field]
        bucket["line_count"] += 1
    totals_by_unit = []
    for key in sorted(totals):
        bucket = totals[key]
        totals_by_unit.append({field: (dec_str(value) if isinstance(value, Decimal)
                                       else value)
                               for field, value in bucket.items()})

    # --- discrepancy / evidence / overdue lists ----------------------------
    discrepancies = []
    for entry in lines:
        if entry["placeholder"] or entry["state"] in ("MATCHED",):
            continue
        discrepancies.append({"line_id": entry["line_id"] or entry["path"],
                              "path": entry["path"], "state": entry["state"],
                              "findings": entry["findings"]})
    missing_evidence = []
    for receipt in all_receipts:
        if "MISSING_EVIDENCE" in receipt["unknowns"]:
            missing_evidence.append({"path": receipt["path"],
                                     "receipt_id": receipt["receipt_id"],
                                     "line_id": receipt["line_id"]})
    overdue_or_not_arrived = []
    for entry in lines:
        if entry["placeholder"]:
            continue
        if "OVERDUE_NOT_ARRIVED" in entry["reminders"]:
            overdue_or_not_arrived.append({
                "line_id": entry["line_id"] or entry["path"], "path": entry["path"],
                "code": "OVERDUE_NOT_ARRIVED",
                "still_outstanding_qty": dec_str(
                    entry["bridge"]["still_outstanding_qty"])
                if entry["bridge"] else None})
        if "LATE_DELIVERY" in entry["reminders"]:
            overdue_or_not_arrived.append({"line_id": entry["line_id"] or entry["path"],
                                           "path": entry["path"],
                                           "code": "LATE_DELIVERY",
                                           "still_outstanding_qty": None})

    # --- owner todos -------------------------------------------------------
    owner_todos = []
    for owner in sorted(owners, key=lambda o: (o["owner_id"] or o["path"])):
        assigned = [e for e in lines if not e["placeholder"]
                    and e["owner_id"] == owner["owner_id"]
                    and owner["owner_id"] is not None]
        unresolved = [{"line_id": e["line_id"] or e["path"], "state": e["state"],
                       "findings": e["findings"]}
                      for e in assigned if e["state"] != "MATCHED"]
        owner_todos.append({"owner_id": owner["owner_id"], "path": owner["path"],
                            "responsibilities": owner["responsibilities"],
                            "assigned": [e["line_id"] or e["path"] for e in assigned],
                            "unresolved": sorted(unresolved,
                                                 key=lambda x: x["line_id"])})

    # --- discrepancy counters ---------------------------------------------
    line_state_counts = {name: 0 for name in LINE_STATES}
    for entry in lines:
        if entry["placeholder"]:
            continue
        line_state_counts[entry["state"]] += 1

    # --- supplier draft (consent gated, never sent) ------------------------
    DRAFTABLE = ("PARTIALLY_RECEIVED", "SHORTAGE", "OVER_RECEIVED", "DAMAGED",
                 "WRONG_ITEM", "QUANTITY_CONFLICT")
    supplier_draft = None
    if payload.get("communication_consent") is True and \
            receiving_state in DRAFTABLE:
        supplier_draft = build_draft(lines, owner_todos, hidden)

    # --- clarification questions ------------------------------------------
    clarification_questions = []
    seen_questions = set()

    def add_question(path, code):
        key = (code, path)
        if key in seen_questions:
            return
        seen_questions.add(key)
        clarification_questions.append({
            "path": path, "code": code,
            "question": CODE_QUESTIONS.get(code, DEFAULT_QUESTION)})

    for code in blockers + unknowns:
        add_question(code_path(code), code)
    for entry in lines:
        if entry["placeholder"]:
            continue
        for code in entry["findings"]:
            add_question(entry["path"], code)
    for receipt in all_receipts:
        for code in receipt["blockers"] + receipt["unknowns"]:
            add_question(receipt["path"], code)

    # --- human checklist ---------------------------------------------------
    human_checklist = list(HUMAN_CHECKLIST_BASE)
    if blockers:
        human_checklist.append("先修正硬问题：" + "、".join(sorted(set(blockers))))
    if dup_line_ids or dup_receipt_ids:
        human_checklist.append("重复编号必须人工改成唯一编号：" +
                               "、".join(sorted(dup_line_ids | dup_receipt_ids)))
    if refused_fields:
        human_checklist.append("删除被拒绝的字段（个人/受限/受保护属性）后重跑：" +
                               "、".join(esc(item["path"]) for item in refused_fields))
    if rules_missing:
        human_checklist.append("先显式提供 handling_rules；本工具不内置平台或法律规则")

    # --- status ------------------------------------------------------------
    project_state = receiving_state
    if not has_text(as_of) or as_of_dt is None or not po_id or \
            not isinstance(payload.get("purchase_order"), dict):
        status = "INPUT_INCOMPLETE"
        project_state = None
    elif receiving_state == "BLOCKED":
        status = "BLOCKED"
    else:
        status = receiving_state

    markdown = render_markdown(payload, order, po_id, supplier_id, destination_id,
                               currency, order_status, ordered_at,
                               handled_as_of=as_of,
                               as_of_dt=as_of_dt, lines=lines, totals=totals_by_unit,
                               discrepancies=discrepancies,
                               missing_evidence=missing_evidence,
                               overdue_or_not_arrived=overdue_or_not_arrived,
                               owner_todos=owner_todos, handling_rules=handling_rules,
                               supplier_draft=supplier_draft,
                               refused_fields=refused_fields,
                               injection_flagged=injection_flagged, hidden=hidden,
                               receiving_state=receiving_state)

    all_refused, all_accepted = top_refs(lines, all_receipts, payload)

    result = {
        "skill": SKILL,
        "version": VERSION,
        "as_of": clean_text(as_of) or None,
        "as_of_date": as_of_dt.date().isoformat() if as_of_dt else None,
        "status": status,
        "receiving_state": project_state,
        "purchase_order": {
            "po_id": po_id or None,
            "supplier_id": supplier_id or None,
            "destination_id": destination_id or None,
            "ordered_at": clean_text(order.get("ordered_at")) or None,
            "currency": currency or None,
            "order_status": order_status,
            "status_missing": status_missing,
            "status_invalid": status_invalid,
        },
        "handling_rules": [dict(rule, text=hidden_value(
            "handling_rules[%d]/text" % index, rule["text"], hidden))
            for index, rule in enumerate(handling_rules)],
        "lines": [line_view(entry, hidden) for entry in lines],
        "receipts": [receipt_view(r, hidden) for r in all_receipts],
        "unassigned_receipts": [receipt_view(r, hidden) for r in unassigned],
        "quantity_bridge_totals": totals_by_unit,
        "quantity_bridge_excluded_lines": excluded_lines,
        "line_state_counts": line_state_counts,
        "discrepancies": discrepancies,
        "missing_evidence": missing_evidence,
        "overdue_or_not_arrived": overdue_or_not_arrived,
        "owner_todos": owner_todos,
        "blockers": blockers,
        "missing_facts": missing_facts(lines, blockers, unknowns, refused_fields),
        "duplicate_line_ids": sorted(dup_line_ids),
        "duplicate_receipt_ids": sorted(dup_receipt_ids),
        "refused_fields": refused_fields,
        "refused_refs": all_refused,
        "accepted_refs": all_accepted,
        "injection_flagged": [{"path": path, "marker": "PROMPT_INJECTION"}
                              for path in injection_flagged],
        "communication_consent": payload.get("communication_consent")
        if isinstance(payload.get("communication_consent"), bool) else None,
        "supplier_draft": supplier_draft,
        "clarification_questions": clarification_questions,
        "human_checklist": human_checklist,
        "human_confirm_items": list(HUMAN_CONFIRM_BASE),
        "next_step": NEXT_STEP[receiving_state] if project_state else None,
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
        "automation_declaration": NO_AUTOMATION_DECLARATION,
        "read_only": True,
        "network": False,
        "writes_files": False,
    }
    return result


def top_refs(lines, all_receipts, payload):
    accepted_top, refused_top = collect_refs(
        as_list(payload.get("evidence_refs")), "evidence_refs", "")
    accepted = accepted_top + [r for entry in lines for r in entry["evidence_refs"]] + \
               [r for r in all_receipts for r in r.get("evidence_refs", [])]
    refused = refused_top + [r for entry in lines for r in entry["refused_refs"]] + \
              [r for r in all_receipts for r in r.get("refused_refs", [])]
    return refused, accepted


def missing_facts(lines, blockers, unknowns, refused_fields):
    facts = []
    for code in unknowns:
        if code in BATCH_UNKNOWNS:
            facts.append({"path": code_path(code), "code": code})
    for entry in lines:
        if entry["placeholder"]:
            continue
        for code in entry["findings"]:
            if code in UNKNOWN_ORDER:
                facts.append({"path": entry["path"], "code": code})
    return facts


def code_path(code):
    if code in ("MISSING_ORDER_STATUS", "MISSING_PURCHASE_ORDER"):
        return "purchase_order"
    if code in ("MISSING_HANDLING_RULES",):
        return "handling_rules"
    if code in ("MISSING_OWNERS",):
        return "owners"
    if code in ("ORDER_CANCELLED", "INVALID_ORDER_STATUS", "ORDER_CURRENCY_CONFLICT",
                "UNKNOWN_CURRENCY"):
        return "purchase_order"
    if code in ("RESTRICTED_DATA_REFUSED", "PERSONAL_DATA_REFUSED",
                "SENSITIVE_ATTRIBUTE_REFUSED"):
        return "input"
    if code == "MISSING_ORDERED_LINES":
        return "ordered_lines"
    return "input"


def bridge_view(bridge):
    if bridge is None:
        return None
    return {field: dec_str(value) for field, value in bridge.items()}


def line_view(entry, hidden):
    if entry["placeholder"]:
        return {"line_id": None, "path": entry["path"], "state": entry["state"],
                "findings": entry["findings"], "placeholder": True,
                "quantity_bridge": None}
    index = entry["index"]
    sku = hidden_value("ordered_lines[%d]/sku_id" % index, entry["sku_id"], hidden) \
        if index is not None else None
    return {
        "line_id": entry["line_id"], "path": entry["path"], "sku_id": sku,
        "unit": entry["unit"], "owner_id": entry["owner_id"],
        "expected_by": clean_text(entry["expected_by"].isoformat())
        if entry["expected_by"] else None,
        "expected_unit_cost": dec_str(entry["expected_unit_cost"]),
        "state": entry["state"], "findings": entry["findings"],
        "overdue": entry["overdue"], "late_delivery": entry["late"],
        "receipt_count": len(entry["receipts"]),
        "quantity_bridge": bridge_view(entry["bridge"]),
        "evidence_refs": [r["name"] for r in entry["evidence_refs"]],
        "placeholder": False,
    }


def receipt_view(entry, hidden):
    quantities = {}
    for field in ("received_qty", "accepted_qty", "damaged_qty",
                  "wrong_item_qty", "rejected_qty", "unaccounted_qty"):
        quantities[field] = dec_str(entry["quantities"].get(field))
    return {
        "receipt_id": entry["receipt_id"], "path": entry["path"],
        "line_id": entry["line_id"],
        "received_at": clean_text(entry["received_at"].isoformat())
        if entry["received_at"] else None,
        "unit": entry["unit"], "currency": entry["currency"],
        "state": entry["state"], "findings": entry["blockers"] + entry["unknowns"],
        "quantities": quantities,
        "evidence_refs": [r["name"] for r in entry["evidence_refs"]],
    }


def build_draft(lines, owner_todos, hidden):
    outstanding = [e for e in lines if not e["placeholder"]
                   and e["state"] != "MATCHED"]
    body = ["【草稿，未发送】",
            "以下采购收货差异仍需人工核对，请相关负责人确认（本消息不会自动发送）："]
    for entry in outstanding:
        bridge = entry["bridge"]
        if bridge is not None and bridge["still_outstanding_qty"] > 0:
            detail = "仍未到 %s %s" % (dec_str(bridge["still_outstanding_qty"]),
                                      esc(entry["unit"] or ""))
        else:
            detail = "差异需人工核对"
        body.append("- [%s] %s：%s（负责人 %s）" % (
            esc(entry["line_id"] or entry["path"]), entry["state"], detail,
            esc(entry["owner_id"] or "未指定负责人")))
    body.append("本条消息仅为草稿，必须由人工审阅后再决定是否发送；本工具绝不外发。")
    recipients = sorted({entry["owner_id"] for entry in outstanding
                         if entry["owner_id"]})
    return {
        "draft_id": "DRAFT-RECEIVING-EXCEPTION",
        "to_owner_ids": recipients,
        "subject": "采购收货差异待办（草稿，未发送）",
        "body": "\n".join(body),
        "status": "DRAFT_NOT_SENT",
    }


def render_markdown(payload, order, po_id, supplier_id, destination_id, currency,
                    order_status, ordered_at, handled_as_of, as_of_dt, lines, totals,
                    discrepancies, missing_evidence, overdue_or_not_arrived,
                    owner_todos, handling_rules, supplier_draft, refused_fields,
                    injection_flagged, hidden, receiving_state):
    out = ["# 采购收货差异与待处理清单", ""]
    out.append("- 采购单：%s" % esc(po_id or "（未提供）"))
    out.append("- 供应商：%s" % esc(supplier_id or "（未提供）"))
    out.append("- 收货点：%s" % esc(destination_id or "（未提供）"))
    out.append("- 下单时间：%s" % esc(clean_text(order.get("ordered_at")) or "（未提供）"))
    out.append("- 币种：%s" % esc(currency or "（未提供）"))
    out.append("- 订单状态：%s" % esc(order_status or "（未提供）"))
    out.append("- 基准时间：%s" % esc(clean_text(handled_as_of) or "（未提供）"))
    out.append("- 收货状态：%s" % receiving_state)
    out.append("")

    out.append("## 数量桥（按单位，不跨单位或币种合并）")
    if not totals:
        out.append("- （无可核对的数量桥）")
    for total in totals:
        out.append("- %s：已订 %s / 实收 %s / 接受 %s / 损坏 %s / 错货 %s / 拒收 %s / "
                   "未解释 %s / 仍未到 %s" % (
                       esc(total["unit"]), total["ordered_qty"],
                       total["received_qty"], total["accepted_qty"],
                       total["damaged_qty"], total["wrong_item_qty"],
                       total["rejected_qty"], total["unaccounted_qty"],
                       total["still_outstanding_qty"]))
    out.append("")

    out.append("## 逐行状态")
    if not lines:
        out.append("- （无采购行）")
    for entry in lines:
        if entry["placeholder"]:
            out.append("- %s：%s" % (esc(entry["path"]), entry["state"]))
            continue
        bridge = entry["bridge"]
        tail = ""
        if bridge is not None:
            tail = "（仍未到 %s）" % dec_str(bridge["still_outstanding_qty"])
        out.append("- %s：%s%s" % (esc(entry["line_id"] or entry["path"]),
                                   entry["state"], tail))
    out.append("")

    out.append("## 差异清单")
    if not discrepancies:
        out.append("- （无差异）")
    for item in discrepancies:
        codes = "、".join(item["findings"]) if item["findings"] else "无差异码"
        out.append("- %s：%s（%s）" % (esc(item["line_id"]), item["state"], codes))
    out.append("")

    out.append("## 缺失证据")
    if not missing_evidence:
        out.append("- （无）")
    for item in missing_evidence:
        out.append("- %s（收货单 %s）" % (esc(item["path"]),
                                        esc(item["receipt_id"] or "未编号")))
    out.append("")

    out.append("## 逾期 / 未到提醒")
    if not overdue_or_not_arrived:
        out.append("- （无）")
    for item in overdue_or_not_arrived:
        out.append("- %s：%s" % (esc(item["line_id"]), item["code"]))
    out.append("")

    out.append("## 负责人待办")
    if not owner_todos:
        out.append("- （未提供负责人）")
    for owner in owner_todos:
        out.append("- %s：未解决 %d 项" % (esc(owner["owner_id"] or owner["path"]),
                                          len(owner["unresolved"])))
    out.append("")

    out.append("## 处理规则（用户提供，非本工具建议）")
    if not handling_rules:
        out.append("- （未提供；本工具不内置平台或法律规则）")
    for index, rule in enumerate(handling_rules):
        text = hidden_value("handling_rules[%d]/text" % index, rule["text"], hidden)
        out.append("- %s：%s" % (esc(rule["rule_id"]), esc(text)))
    out.append("")

    out.append("## 拒绝字段 / 危险引用 / 注入标记")
    if not refused_fields:
        out.append("- 拒绝字段：无")
    for item in refused_fields:
        out.append("- 拒绝字段：%s（%s）" % (esc(item["path"]), item["reason"]))
    if not injection_flagged:
        out.append("- 注入标记：无")
    for path in injection_flagged:
        out.append("- 注入标记：%s" % esc(path))
    out.append("")

    out.append("## 供应商沟通草稿")
    if supplier_draft is None:
        out.append("- 无草稿（未同意、未知或存在阻塞/证据不足）")
    else:
        out.append("- %s [%s]（未发送）" % (esc(supplier_draft["draft_id"]),
                                            supplier_draft["status"]))
    out.append("")

    out.append("## 备注")
    note = hidden_value("notes", payload.get("notes"), hidden)
    out.append("- 备注：%s" % (esc(note) if note else "**未提供**"))
    out.append("")

    out.append("## 免责声明")
    out.append("- " + esc(DISCLAIMER))
    out.append("- " + esc(NO_AUTOMATION_DECLARATION))
    return "\n".join(out)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def refusal(paths):
    return {
        "skill": SKILL, "version": VERSION, "status": "REJECTED",
        "code": "CREDENTIAL_LIKE_INPUT", "receiving_state": None,
        "detected_fields": sorted(set(paths)),
        "message": "输入疑似包含凭据（字段名或值形如密钥/令牌），已整体拒绝处理，未回显任何内容。",
        "read_only": True, "network": False, "writes_files": False,
    }


def incomplete(code, message):
    return {
        "skill": SKILL, "version": VERSION, "status": "INPUT_INCOMPLETE",
        "code": code, "receiving_state": None, "message": message,
        "read_only": True, "network": False, "writes_files": False,
    }


def run(payload):
    """Guarded entry point: refuse, stop, or analyse. Never echoes secrets."""
    if not isinstance(payload, dict):
        return incomplete("NON_OBJECT_INPUT", "顶层输入必须是 JSON 对象。")
    credential_paths = find_credentials(payload)
    if credential_paths:
        return refusal(credential_paths)
    as_of = payload.get("as_of")
    if not has_text(as_of):
        return incomplete("MISSING_AS_OF", "缺少带时区偏移的 as_of，无法判断当前时点。")
    if parse_dt(as_of) is None:
        return incomplete("INVALID_AS_OF",
                          "as_of 必须带时区偏移（如 2026-10-05T19:00:00+08:00）。")
    order = payload.get("purchase_order")
    if not isinstance(order, dict):
        return incomplete("MISSING_PURCHASE_ORDER", "缺少 purchase_order 对象。")
    if not has_text(order.get("po_id")):
        return incomplete("MISSING_PO_ID", "缺少 purchase_order.po_id。")
    return analyse(payload)


def main(argv):
    if len(argv) != 2:
        print(json.dumps({"status": "USAGE_ERROR",
                          "usage": "python3 scripts/run.py <input.json>"},
                         ensure_ascii=False, indent=2))
        return 2
    try:
        with open(argv[1], "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "INPUT_ERROR",
                          "message": clean_text(str(exc))},
                         ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(run(payload), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
