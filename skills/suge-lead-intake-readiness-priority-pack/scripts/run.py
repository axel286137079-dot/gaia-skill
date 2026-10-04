#!/usr/bin/env python3
"""销售线索接入完整度与下一步优先包 — offline lead-intake readiness engine.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes, no scraping, no CRM/e-mail/message reads, no automatic contact, no
automatic customer disqualification, no price/discount/deadline promises and no
use of protected attributes: it only sorts the anonymous facts the caller
already supplied into a human-review queue.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta

VERSION = "1.0.0"

# --------------------------------------------------------------------------
# fixed vocabulary (documented in references/guide.md)
# --------------------------------------------------------------------------
# Every lead and the whole batch end in exactly one of six states. The order
# below is the severity order used for aggregation everywhere.
LEAD_STATES = (
    "READY_FOR_HUMAN_FOLLOWUP", "NEEDS_CLARIFICATION", "WAITING_FOR_CONSENT",
    "OUT_OF_DECLARED_SCOPE", "INSUFFICIENT_EVIDENCE", "BLOCKED",
)
SEVERITY = {
    "READY_FOR_HUMAN_FOLLOWUP": 0, "NEEDS_CLARIFICATION": 1,
    "WAITING_FOR_CONSENT": 2, "OUT_OF_DECLARED_SCOPE": 3,
    "INSUFFICIENT_EVIDENCE": 4, "BLOCKED": 5,
}

# The human-review priority band is derived ONLY from state, deadline urgency and
# declared capacity. It is deliberately not a "customer value score": value is
# never inferred, and no ranking is produced from protected attributes.
BAND_ORDER = (
    "BAND_URGENT", "BAND_STANDARD", "BAND_NEEDS_CLARIFICATION",
    "BAND_CONSENT_REVIEW", "BAND_SCOPE_REVIEW", "BAND_MISSING_FACTS",
    "BAND_BLOCKED_INPUT",
)
BAND_RANK = {name: i for i, name in enumerate(BAND_ORDER)}
STATE_BAND = {
    "READY_FOR_HUMAN_FOLLOWUP": "BAND_STANDARD",
    "NEEDS_CLARIFICATION": "BAND_NEEDS_CLARIFICATION",
    "WAITING_FOR_CONSENT": "BAND_CONSENT_REVIEW",
    "OUT_OF_DECLARED_SCOPE": "BAND_SCOPE_REVIEW",
    "INSUFFICIENT_EVIDENCE": "BAND_MISSING_FACTS",
    "BLOCKED": "BAND_BLOCKED_INPUT",
}

# Closed vocabularies.
BUDGET_STATUSES = ("provided", "not_provided", "not_applicable")

# Decision-code orders (the sequence in which findings are listed).
LEAD_BLOCKER_ORDER = (
    "INVALID_LEAD_RECORD", "MISSING_LEAD_ID", "DUPLICATE_LEAD_ID",
    "PERSONAL_DATA_REFUSED", "SENSITIVE_ATTRIBUTE_REFUSED",
    "INVALID_RECEIVED_AT", "INVALID_DEADLINE", "RECEIVED_IN_FUTURE",
    "DEADLINE_BEFORE_RECEIVED",
)
LEAD_UNKNOWN_ORDER = (
    "MISSING_REQUESTED_OUTCOME", "MISSING_PRODUCT_OR_SERVICE", "MISSING_SOURCE",
    "MISSING_RECEIVED_AT", "MISSING_REQUIRED_FACT",
)
LEAD_SCOPE_ORDER = (
    "PRODUCT_IN_EXCLUDED_SCOPE", "PRODUCT_OUT_OF_DECLARED_SCOPE",
    "AREA_OUT_OF_DECLARED_SCOPE", "AREA_BOUNDARY_REVIEW",
)
LEAD_CONSENT_ORDER = ("CONSENT_NOT_GRANTED", "CONSENT_UNKNOWN")
LEAD_CLARIFY_ORDER = (
    "DEADLINE_EXPIRED", "MISSING_DEADLINE", "MISSING_SERVICE_AREA",
    "BUDGET_NOT_PROVIDED", "BUDGET_STATUS_UNKNOWN", "MISSING_DECISION_TIMING",
)

PROJECT_BLOCKER_ORDER = ("MISSING_BUSINESS_ID", "MISSING_TIMEZONE")
PROJECT_UNKNOWN_ORDER = (
    "MISSING_QUALIFICATION_RULES", "MISSING_CAPACITY", "MISSING_BUSINESS_CHANNELS",
    "MISSING_PRODUCT_SCOPE", "MISSING_SERVICE_AREA_SCOPE", "MISSING_LEADS",
    "REFUSED_FIELDS_PRESENT",
)
INCOMPLETE_CODES = ("MISSING_AS_OF", "INVALID_AS_OF", "MISSING_BUSINESS_ID",
                    "MISSING_TIMEZONE")

CORE_FACTS = ("requested_outcome", "product_or_service", "source",
              "received_at", "service_area", "deadline", "budget_status",
              "decision_timing")

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

# Personal data and protected attributes are refused per field (never echoed).
PERSONAL_KEYS = frozenset({
    "name", "fullname", "contactname", "firstname", "lastname", "phone",
    "mobile", "tel", "telephone", "email", "mail", "wechat", "weixin",
    "wechatid", "qq", "account", "accountid", "address", "homeaddress",
    "cookie", "sessionid", "token", "password", "idcard", "idnumber",
    "passport", "creditcard", "ssn", "contactemail", "contactphone",
})
SENSITIVE_KEYS = frozenset({
    "gender", "sex", "age", "birthdate", "birthday", "race", "ethnicity",
    "religion", "health", "disability", "maritalstatus", "nationality",
    "politics", "politicalview",
})
# These structured fields are time/id facts, not free text: skip value scanning
# so an anonymous numeric lead id or an ISO timestamp never looks like PII.
SKIP_VALUE_SCAN = frozenset({
    "as_of", "business_id", "timezone", "lead_id", "received_at", "deadline",
})

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MOBILE_RE = re.compile(r"(?<![0-9A-Za-z-])1[3-9]\d{9}(?![0-9A-Za-z-])")
IDCARD_RE = re.compile(
    r"(?<![0-9A-Za-z-])(?:\d{17}[\dXx]|\d{15})(?![0-9A-Za-z-])")
ACCOUNT_RE = re.compile(r"(?<![0-9A-Za-z-])\d{7,20}(?![0-9A-Za-z-])")
PII_VALUE_RES = (EMAIL_RE, MOBILE_RE, IDCARD_RE, ACCOUNT_RE)

# Injection is flagged only when an action verb and an instruction word appear
# in the SAME sentence, so ordinary intake notes are not mislabelled.
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

DISCLAIMER = (
    "本输出是销售线索接入整理的人工准备材料，不是对客户的资格判定、价值判定、"
    "报价或承诺；所有结论只由输入中明示的匿名事实推导，缺失信息一律保持未知，"
    "范围与区域是否匹配、是否跟进以及是否联系，必须由人工确认。"
)

NO_AUTOMATION_DECLARATION = (
    "本工具不抓取任何线索、不读取 CRM/邮箱/私信、不自动联系任何线索、"
    "不自动淘汰任何客户、不承诺价格/折扣/交期、不使用受保护属性、"
    "不推断购买意愿；它只把已提供的匿名事实整理成供人工复核的优先队列。"
)

HUMAN_CONFIRM_BASE = (
    "是否跟进某条线索必须由人工决定，本工具只给出人工复核提示，不构成拒客结论",
    "范围与区域是否匹配必须由人工确认，边界地区只提示复核，不自动判为不服务",
    "联系同意必须由本人或线索方明确确认；未同意者不生成任何可发送内容",
    "报价、折扣与交期承诺必须由人工书面给出，本工具绝不承诺",
    "任何对客户的价值、意愿或信用判断必须由人工完成，本工具不输出此类结论",
)

HUMAN_CHECKLIST_BASE = (
    "逐条核对线索的接入完整度：需求、产品/服务、来源、接收时间是否齐全",
    "先修正硬阻塞（非法时间、重复编号、个人信息或受保护属性字段）再重跑",
    "按优先带人工复核范围与区域是否匹配（边界地区单独复核）",
    "对缺失事实逐条生成澄清问题，不替线索方或自己编造事实",
    "确认人工可处理容量与可用时段，容量只用于排队建议，不作承诺",
    "只对明确同意的对象准备 DRAFT_NOT_SENT 草稿，未同意者不生成任何可发送内容",
    "人工决定是否跟进、是否联系，并对最终判断负责",
)

CODE_QUESTIONS = {
    "MISSING_LEAD_ID": "这条线索缺少匿名编号，请补一个不含个人信息的 lead_id。",
    "DUPLICATE_LEAD_ID": "该 lead_id 重复，请人工确认后改成唯一编号。",
    "PERSONAL_DATA_REFUSED": "该字段疑似个人信息（姓名/电话/邮箱/账号/地址/凭据），"
                             "请删除后再提供；本工具不处理也不回显个人数据。",
    "SENSITIVE_ATTRIBUTE_REFUSED": "该字段属于受保护属性（性别/年龄/民族/宗教/健康等），"
                                   "本工具不使用，请删除。",
    "INVALID_RECEIVED_AT": "接收时间无法解析（需带时区偏移），请提供合规时间。",
    "INVALID_DEADLINE": "截止时间无法解析（需带时区偏移），请提供合规时间。",
    "RECEIVED_IN_FUTURE": "接收时间晚于当前时点，请确认时间是否正确。",
    "DEADLINE_BEFORE_RECEIVED": "截止时间早于接收时间，请确认哪个时间需要调整。",
    "MISSING_REQUESTED_OUTCOME": "线索方希望达成什么结果？请补充一句话需求。",
    "MISSING_PRODUCT_OR_SERVICE": "线索方咨询的是哪个产品/服务？请补充。",
    "MISSING_SOURCE": "这条线索来自哪个渠道？请补充来源。",
    "MISSING_RECEIVED_AT": "这条线索的接收时间是什么（需带时区偏移）？",
    "MISSING_REQUIRED_FACT": "该线索缺少你规则中要求的最少必需事实，请补充。",
    "PRODUCT_IN_EXCLUDED_SCOPE": "该产品命中你明确不服务的范围，请人工复核是否仍要跟进。",
    "PRODUCT_OUT_OF_DECLARED_SCOPE": "该产品不在你声明的服务范围内，请人工复核。",
    "AREA_OUT_OF_DECLARED_SCOPE": "该区域不在你声明的服务区域内，请人工复核。",
    "AREA_BOUNDARY_REVIEW": "该区域与你声明的服务区域相邻或部分重合，属边界地区，请人工复核。",
    "CONSENT_NOT_GRANTED": "线索方已明确不同意被联系，本工具不生成任何草稿。",
    "CONSENT_UNKNOWN": "是否同意被联系未知；确认同意前不生成任何可发送内容。",
    "DEADLINE_EXPIRED": "截止时间已过，请确认该线索是否仍然有效。",
    "MISSING_DEADLINE": "该线索的截止时间是什么？缺失时无法判断时效。",
    "MISSING_SERVICE_AREA": "该线索的服务区域缺失；没有区域时不猜，请补充。",
    "BUDGET_NOT_PROVIDED": "线索方未提供预算；未提供不等于低价值，请在澄清中询问金额范围。",
    "BUDGET_STATUS_UNKNOWN": "预算状态无法识别，请用 provided / not_provided / not_applicable 说明。",
    "MISSING_DECISION_TIMING": "线索方的决策时间点是什么（什么时候决定）？",
    "MISSING_QUALIFICATION_RULES": "尚未提供资格规则（范围适配/区域适配/最低必需事实/时效/优先顺序），"
                                   "本工具不替你发明销售策略。",
    "MISSING_CAPACITY": "尚未提供人工可处理容量与可用时段，排队建议无法计算。",
    "MISSING_BUSINESS_CHANNELS": "尚未提供可用渠道，无法核对渠道事实。",
    "MISSING_PRODUCT_SCOPE": "尚未提供产品/服务范围，无法判定产品是否在范围内。",
    "MISSING_SERVICE_AREA_SCOPE": "尚未提供服务区域，无法判定区域是否在范围内。",
    "MISSING_LEADS": "没有可整理的线索。",
    "REFUSED_FIELDS_PRESENT": "输入含被拒绝的字段（个人信息或受保护属性），请删除后重跑。",
}
DEFAULT_QUESTION = "请补充该事项所需的事实。"

NEXT_STEP = {
    "READY_FOR_HUMAN_FOLLOWUP": "进入人工跟进队列；本工具不联系、不承诺，请人工决定下一步。",
    "NEEDS_CLARIFICATION": "先补齐下列澄清项，再决定是否跟进。",
    "WAITING_FOR_CONSENT": "未获得联系同意，不得生成任何草稿；请人工先确认同意。",
    "OUT_OF_DECLARED_SCOPE": "疑似超出你声明范围，请人工复核范围与区域后再决定；这不是拒客结论。",
    "INSUFFICIENT_EVIDENCE": "关键事实缺失，请先补齐事实再做判定。",
    "BLOCKED": "存在硬问题（非法/冲突时间、重复编号、个人信息或受保护属性字段），请先修正输入。",
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


def date_part(value):
    parsed = parse_dt(value)
    return parsed.date().isoformat() if parsed else None


def epoch(value):
    parsed = parse_dt(value)
    return parsed.timestamp() if parsed else None


def norm(value):
    """Normalise a term for scope matching: strip whitespace and fold case."""
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


def scan_forbidden(node, path=""):
    """Return [{path, reason}] for personal-data or protected-attribute fields.

    Only the *location* and *reason* are returned, so a refusal can name where
    the problem is without ever echoing the value back to the caller.
    """
    hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + "/" + str(key) if path else str(key)
            if isinstance(key, str):
                flat = re.sub(r"[^a-z0-9]", "", key.casefold())
                if flat in PERSONAL_KEYS:
                    hits.append({"path": child, "reason": "PERSONAL_DATA"})
                elif flat in SENSITIVE_KEYS:
                    hits.append({"path": child, "reason": "SENSITIVE_ATTRIBUTE"})
                if isinstance(value, str) and flat not in SKIP_VALUE_SCAN and \
                        flat not in PERSONAL_KEYS and flat not in SENSITIVE_KEYS:
                    if any(pattern.search(clean_text(value))
                           for pattern in PII_VALUE_RES):
                        hits.append({"path": child, "reason": "PERSONAL_DATA"})
            hits.extend(scan_forbidden(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(scan_forbidden(value, "%s[%d]" % (path, index)))
    return hits


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


# --------------------------------------------------------------------------
# scope matching
# --------------------------------------------------------------------------
def product_fit(text, in_terms, excluded_terms):
    """Return (fit, code) where fit is IN / OUT / UNKNOWN."""
    if not has_text(text):
        return "UNKNOWN", None
    target = norm(text)
    if any(norm(term) == target for term in excluded_terms):
        return "OUT", "PRODUCT_IN_EXCLUDED_SCOPE"
    if any(norm(term) == target for term in in_terms):
        return "IN", None
    if in_terms:
        return "OUT", "PRODUCT_OUT_OF_DECLARED_SCOPE"
    return "UNKNOWN", None


def area_fit(text, area_terms):
    """Return (fit, code) where fit is IN / BOUNDARY / OUT / UNKNOWN."""
    if not has_text(text):
        return "UNKNOWN", None
    target = norm(text)
    for term in area_terms:
        candidate = norm(term)
        if candidate == target:
            return "IN", None
    for term in area_terms:
        candidate = norm(term)
        if candidate and (candidate in target or target in candidate):
            return "BOUNDARY", "AREA_BOUNDARY_REVIEW"
    if area_terms:
        return "OUT", "AREA_OUT_OF_DECLARED_SCOPE"
    return "UNKNOWN", None


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def analyse(payload):
    as_of = payload.get("as_of")
    as_of_dt = parse_dt(as_of)
    business = as_dict(payload.get("business"))
    rules = as_dict(payload.get("qualification_rules"))
    capacity = as_dict(payload.get("capacity"))

    business_id = clean_text(business.get("business_id"))
    timezone = clean_text(business.get("timezone"))
    product_scope = [clean_text(x) for x in as_list(business.get("product_scope"))
                     if has_text(x)]
    service_area = [clean_text(x) for x in as_list(business.get("service_area"))
                    if has_text(x)]
    excluded = [clean_text(x) for x in as_list(business.get("out_of_scope"))
                if has_text(x)]
    channels = [clean_text(x) for x in as_list(business.get("channels"))
                if has_text(x)]

    rule_products = [clean_text(x) for x in as_list(rules.get("product_service_terms"))
                     if has_text(x)]
    rule_areas = [clean_text(x) for x in as_list(rules.get("service_area_terms"))
                  if has_text(x)]
    in_terms = product_scope + [t for t in rule_products if t not in product_scope]
    area_terms = service_area + [t for t in rule_areas if t not in service_area]
    min_required = [clean_text(x) for x in as_list(rules.get("minimum_required_facts"))
                    if has_text(x)]
    timeliness = as_dict(rules.get("timeliness"))
    urgent_hours = timeliness.get("urgent_within_hours")
    if not isinstance(urgent_hours, (int, float)) or isinstance(urgent_hours, bool):
        urgent_hours = None
    priority_order = [clean_text(x) for x in as_list(rules.get("priority_order"))]
    priority_order = [x for x in priority_order
                      if x in ("deadline", "received_at", "lead_id")]

    # --- personal data / protected attributes (never echoed) --------------
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
        if scan_injection(value) and path not in injection_flagged:
            injection_flagged.append(path)

    free_paths = [("notes", payload.get("notes"))]
    for key in ("business_id", "timezone"):
        free_paths.append(("business/" + key, business.get(key)))
    for field, values in (("product_scope", business.get("product_scope")),
                          ("service_area", business.get("service_area")),
                          ("out_of_scope", business.get("out_of_scope")),
                          ("channels", business.get("channels"))):
        for index, item in enumerate(as_list(values)):
            free_paths.append(("business/%s[%d]" % (field, index), item))
    for key in ("product_service_terms", "service_area_terms",
                "minimum_required_facts", "priority_order"):
        for index, item in enumerate(as_list(rules.get(key))):
            free_paths.append(("qualification_rules/%s[%d]" % (key, index), item))
    free_paths.append(("qualification_rules/timeliness/note",
                       timeliness.get("note")))
    for index, item in enumerate(as_list(capacity.get("windows"))):
        free_paths.append(("capacity/windows[%d]" % index, item))
    for index, item in enumerate(as_list(payload.get("leads"))):
        if isinstance(item, dict):
            for key in ("requested_outcome", "product_or_service", "service_area",
                        "source", "decision_timing"):
                free_paths.append(("leads[%d]/%s" % (index, key), item.get(key)))
    hidden = set()
    for path, value in free_paths:
        if value is None:
            continue
        scan(path, value)
    for path in injection_flagged:
        hidden.add(path)
    # A field refused as personal data or a protected attribute is never echoed:
    # its value is replaced by the same placeholder wherever a value is rendered.
    hidden |= {item["path"] for item in refused_fields}

    # --- project level facts ---------------------------------------------
    project_blockers, project_unknowns = [], []
    if not business_id:
        project_blockers.append("MISSING_BUSINESS_ID")
    if not timezone:
        project_blockers.append("MISSING_TIMEZONE")
    project_blockers = [code for code in PROJECT_BLOCKER_ORDER
                        if code in set(project_blockers)]

    if not rules:
        project_unknowns.append("MISSING_QUALIFICATION_RULES")
    if not capacity:
        project_unknowns.append("MISSING_CAPACITY")
    if not channels:
        project_unknowns.append("MISSING_BUSINESS_CHANNELS")
    if not in_terms:
        project_unknowns.append("MISSING_PRODUCT_SCOPE")
    if not area_terms:
        project_unknowns.append("MISSING_SERVICE_AREA_SCOPE")
    raw_leads = as_list(payload.get("leads"))
    if not raw_leads:
        project_unknowns.append("MISSING_LEADS")
    if any(item["reason"] in ("PERSONAL_DATA", "SENSITIVE_ATTRIBUTE")
           for item in refused_fields):
        if not any(path.startswith("leads[") for path in forbidden_paths):
            project_unknowns.append("REFUSED_FIELDS_PRESENT")
    project_unknowns = [code for code in PROJECT_UNKNOWN_ORDER
                        if code in set(project_unknowns)]

    # --- lead parsing -----------------------------------------------------
    leads, duplicate_ids = [], []
    id_index = {}
    for index, raw in enumerate(raw_leads):
        path = "leads[%d]" % index
        lead_forbidden = [item for item in refused_fields
                          if item["path"].startswith(path + "/")]
        reasons = {item["reason"] for item in lead_forbidden}
        if not isinstance(raw, dict):
            leads.append({
                "path": path, "index": index, "lead_id": None, "invalid": True,
                "source": None, "received_at": None, "requested_outcome": None,
                "product_or_service": None, "service_area": None, "deadline": None,
                "budget_status": None, "decision_timing": None,
                "evidence_refs": [], "contact_consent": None,
                "forbidden_reasons": reasons,
                "state": "BLOCKED", "priority_band": "BAND_BLOCKED_INPUT",
                "blockers": ["INVALID_LEAD_RECORD"], "unknown": [], "scope": [],
                "consent": [], "clarify": [],
                "findings": ["INVALID_LEAD_RECORD"],
                "product_fit": "UNKNOWN", "area_fit": "UNKNOWN",
                "product_code": None, "area_code": None,
                "received_dt": None, "deadline_dt": None})
            continue
        did = clean_text(raw.get("lead_id")) or None
        lead_refs, lead_refused = collect_refs(
            as_list(raw.get("evidence_refs")), "evidence_refs", path + "/")
        row = {
            "path": path, "index": index, "lead_id": did, "invalid": False,
            "source": clean_text(raw.get("source")) or None,
            "received_at": clean_text(raw.get("received_at")) or None,
            "requested_outcome": clean_text(raw.get("requested_outcome")) or None,
            "product_or_service": clean_text(raw.get("product_or_service")) or None,
            "service_area": clean_text(raw.get("service_area")) or None,
            "deadline": clean_text(raw.get("deadline")) or None,
            "budget_status": clean_text(raw.get("budget_status")).lower() or None,
            "decision_timing": clean_text(raw.get("decision_timing")) or None,
            "evidence_refs": lead_refs, "refused_refs": lead_refused,
            "contact_consent": raw.get("contact_consent")
            if isinstance(raw.get("contact_consent"), bool) else None,
            "raw": raw, "forbidden_reasons": reasons,
        }
        leads.append(row)
        if did:
            if did in id_index:
                duplicate_ids.append({
                    "id": did,
                    "paths": [leads[id_index[did]]["path"], path]})
            else:
                id_index[did] = index

    dup_ids = {entry["id"] for entry in duplicate_ids}

    # --- per-lead evaluation ---------------------------------------------
    for row in leads:
        if row["invalid"]:
            row["effective_state"] = "BLOCKED"
            continue
        raw = row["raw"]
        blockers, unknown, scope, consent, clarify = [], [], [], [], []

        if row["forbidden_reasons"]:
            if "PERSONAL_DATA" in row["forbidden_reasons"]:
                blockers.append("PERSONAL_DATA_REFUSED")
            if "SENSITIVE_ATTRIBUTE" in row["forbidden_reasons"]:
                blockers.append("SENSITIVE_ATTRIBUTE_REFUSED")
        if not row["lead_id"]:
            blockers.append("MISSING_LEAD_ID")
        if row["lead_id"] and row["lead_id"] in dup_ids:
            blockers.append("DUPLICATE_LEAD_ID")

        if not row["source"]:
            unknown.append("MISSING_SOURCE")
        if not row["requested_outcome"]:
            unknown.append("MISSING_REQUESTED_OUTCOME")
        if not row["product_or_service"]:
            unknown.append("MISSING_PRODUCT_OR_SERVICE")
        if not row["received_at"]:
            unknown.append("MISSING_RECEIVED_AT")
        else:
            received_dt = parse_dt(row["received_at"])
            row["received_dt"] = received_dt
            if received_dt is None:
                blockers.append("INVALID_RECEIVED_AT")
            elif as_of_dt is not None and received_dt > as_of_dt:
                blockers.append("RECEIVED_IN_FUTURE")

        for fact in min_required:
            if fact in CORE_FACTS:
                continue
            if not has_text(raw.get(fact)):
                unknown.append("MISSING_REQUIRED_FACT")

        p_fit, p_code = product_fit(row["product_or_service"], in_terms, excluded)
        row["product_fit"], row["product_code"] = p_fit, p_code
        if p_code:
            scope.append(p_code)
        a_fit, a_code = area_fit(row["service_area"], area_terms)
        row["area_fit"], row["area_code"] = a_fit, a_code
        if a_code:
            scope.append(a_code)
        if not row["service_area"]:
            clarify.append("MISSING_SERVICE_AREA")

        if row["deadline"]:
            deadline_dt = parse_dt(row["deadline"])
            row["deadline_dt"] = deadline_dt
            if deadline_dt is None:
                blockers.append("INVALID_DEADLINE")
            else:
                if row.get("received_dt") and deadline_dt < row["received_dt"]:
                    blockers.append("DEADLINE_BEFORE_RECEIVED")
                if as_of_dt is not None and deadline_dt < as_of_dt:
                    clarify.append("DEADLINE_EXPIRED")
        else:
            clarify.append("MISSING_DEADLINE")

        if row["budget_status"] is None:
            clarify.append("BUDGET_STATUS_UNKNOWN")
        elif row["budget_status"] == "not_provided":
            clarify.append("BUDGET_NOT_PROVIDED")
        elif row["budget_status"] == "not_applicable":
            pass
        elif row["budget_status"] == "provided":
            pass
        else:
            clarify.append("BUDGET_STATUS_UNKNOWN")
        if not row["decision_timing"]:
            clarify.append("MISSING_DECISION_TIMING")

        if row["contact_consent"] is False:
            consent.append("CONSENT_NOT_GRANTED")
        elif row["contact_consent"] is None:
            consent.append("CONSENT_UNKNOWN")

        blockers = [c for c in LEAD_BLOCKER_ORDER if c in set(blockers)]
        unknown = [c for c in LEAD_UNKNOWN_ORDER if c in set(unknown)]
        scope = [c for c in LEAD_SCOPE_ORDER if c in set(scope)]
        consent = [c for c in LEAD_CONSENT_ORDER if c in set(consent)]
        clarify = [c for c in LEAD_CLARIFY_ORDER if c in set(clarify)]
        if blockers:
            state = "BLOCKED"
        elif unknown:
            state = "INSUFFICIENT_EVIDENCE"
        elif scope:
            state = "OUT_OF_DECLARED_SCOPE"
        elif consent:
            state = "WAITING_FOR_CONSENT"
        elif clarify:
            state = "NEEDS_CLARIFICATION"
        else:
            state = "READY_FOR_HUMAN_FOLLOWUP"
        row["blockers"], row["unknown"], row["scope"] = blockers, unknown, scope
        row["consent"], row["clarify"] = consent, clarify
        row["findings"] = blockers + unknown + scope + consent + clarify
        row["effective_state"] = state
        band = STATE_BAND[state]
        if state == "READY_FOR_HUMAN_FOLLOWUP" and row.get("deadline_dt") and \
                urgent_hours is not None and as_of_dt is not None:
            window = timedelta(hours=urgent_hours)
            if row["deadline_dt"] <= as_of_dt + window:
                band = "BAND_URGENT"
        row["priority_band"] = band

    # --- queue ordering (stable, deterministic) --------------------------
    order_keys = priority_order or ["deadline", "received_at", "lead_id"]

    def sort_key(row):
        keys = [BAND_RANK[row["priority_band"]]]
        for name in order_keys:
            if name == "deadline":
                value = epoch(row.get("deadline")) if not row["invalid"] else None
            elif name == "received_at":
                value = epoch(row.get("received_at")) if not row["invalid"] else None
            else:
                value = None
            keys.append((0, value) if value is not None else (1, 0))
        keys.append(row["lead_id"] or row["path"])
        return keys

    queue = []
    for position, row in enumerate(sorted(leads, key=sort_key), start=1):
        queue.append({"position": position,
                      "lead_id": row["lead_id"], "path": row["path"],
                      "state": row["effective_state"],
                      "priority_band": row["priority_band"]})
    row_order = {id(row): entry["position"] for entry, row in zip(queue, sorted(
        leads, key=sort_key))}
    for row in leads:
        row["queue_position"] = row_order[id(row)]

    slots = capacity.get("available_slots")
    if not isinstance(slots, int) or isinstance(slots, bool) or slots < 0:
        slots = None
    ready_rows = [r for r in sorted(leads, key=sort_key)
                  if r["effective_state"] == "READY_FOR_HUMAN_FOLLOWUP"]
    for index, row in enumerate(ready_rows, start=1):
        row["followup_rank"] = index
        row["within_capacity"] = (index <= slots) if slots is not None else None
    capacity_summary = {
        "available_slots": slots,
        "windows": [clean_text(x) for x in as_list(capacity.get("windows"))
                    if has_text(x)],
        "followup_candidates": len(ready_rows),
        "within_capacity": (min(slots, len(ready_rows))
                            if slots is not None else None),
        "overflow": (max(0, len(ready_rows) - slots) if slots is not None else None),
    }

    # --- status counts ----------------------------------------------------
    status_counts = {name: 0 for name in LEAD_STATES}
    for row in leads:
        status_counts[row["effective_state"]] = \
            status_counts.get(row["effective_state"], 0) + 1

    # --- consent split ----------------------------------------------------
    granted, refused_consent, unknown_consent = [], [], []
    for row in leads:
        if row["invalid"]:
            continue
        ident = row["lead_id"] or row["path"]
        if row["contact_consent"] is True:
            granted.append(ident)
        elif row["contact_consent"] is False:
            refused_consent.append(ident)
        else:
            unknown_consent.append(ident)

    # --- timeliness / scope / evidence outputs ---------------------------
    timeline = []
    for row in leads:
        if row["invalid"]:
            continue
        if "DEADLINE_EXPIRED" in row.get("clarify", []):
            timeline.append({"code": "DEADLINE_EXPIRED", "path": row["path"],
                             "lead_id": row["lead_id"],
                             "detail": "deadline 早于 as_of"})
        if "RECEIVED_IN_FUTURE" in row.get("blockers", []):
            timeline.append({"code": "RECEIVED_IN_FUTURE", "path": row["path"],
                             "lead_id": row["lead_id"],
                             "detail": "received_at 晚于 as_of"})
        if "DEADLINE_BEFORE_RECEIVED" in row.get("blockers", []):
            timeline.append({"code": "DEADLINE_BEFORE_RECEIVED",
                             "path": row["path"], "lead_id": row["lead_id"],
                             "detail": "deadline 早于 received_at"})

    scope_evidence = []
    for row in leads:
        if row["invalid"]:
            continue
        index = row["index"]
        scope_evidence.append({
            "lead_id": row["lead_id"], "path": row["path"],
            "product": {"text": hidden_value(
                "leads[%d]/product_or_service" % index,
                row["product_or_service"], hidden),
                "fit": row["product_fit"], "code": row["product_code"]},
            "area": {"text": hidden_value(
                "leads[%d]/service_area" % index, row["service_area"], hidden),
                "fit": row["area_fit"], "code": row["area_code"]},
        })

    missing_facts = []
    for code in project_unknowns:
        where = "qualification_rules" if "QUALIFICATION" in code else (
            "capacity" if "CAPACITY" in code else (
                "business" if code.startswith("MISSING_") and "LEADS" not in code
                else "input"))
        missing_facts.append({"path": where, "code": code})
    for row in leads:
        for code in row.get("unknown", []):
            missing_facts.append({"path": row["path"], "code": code})
        for code in row.get("clarify", []):
            missing_facts.append({"path": row["path"], "code": code})

    # --- clarification questions -----------------------------------------
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

    for item in missing_facts:
        add_question(item["path"], item["code"])
    for row in leads:
        if row["invalid"]:
            continue
        for code in row.get("scope", []) + row.get("consent", []):
            add_question(row["path"], code)

    # --- next-step cards --------------------------------------------------
    next_step_cards = []
    for row in leads:
        codes = (row.get("unknown", []) + row.get("clarify", []) +
                 row.get("scope", []) + row.get("consent", []))
        ask = [{"code": code,
                "question": CODE_QUESTIONS.get(code, DEFAULT_QUESTION)}
               for code in codes]
        next_step_cards.append({
            "lead_id": row["lead_id"], "path": row["path"],
            "state": row["effective_state"], "priority_band": row["priority_band"],
            "next_step": NEXT_STEP[row["effective_state"]],
            "scope_fit": {"product": row["product_fit"], "area": row["area_fit"]},
            "blocked_by": row.get("blockers", []),
            "ask": ask,
        })

    # --- drafts (consent gated, never sent) ------------------------------
    drafts = []
    for row in leads:
        if row["invalid"] or row["contact_consent"] is not True:
            continue
        if row["effective_state"] not in ("NEEDS_CLARIFICATION",
                                          "INSUFFICIENT_EVIDENCE"):
            continue
        codes = row.get("unknown", []) + row.get("clarify", [])
        if not codes:
            continue
        ident = row["lead_id"] or row["path"]
        lines = ["【草稿，未发送】", "为便于我们判断是否适合跟进，请补充以下信息："]
        for code in codes:
            lines.append("- " + CODE_QUESTIONS.get(code, DEFAULT_QUESTION))
        lines.append("本条消息仅为草稿，必须由人工审阅后再决定是否发送；本工具绝不外发。")
        drafts.append({
            "draft_id": "DRAFT-" + ident,
            "to_lead_id": ident,
            "subject": "线索入接信息确认（草稿，未发送）",
            "body": "\n".join(lines),
            "status": "DRAFT_NOT_SENT",
        })

    # --- human checklist --------------------------------------------------
    human_checklist = list(HUMAN_CHECKLIST_BASE)
    if project_blockers:
        human_checklist.append("先补齐项目级必需项：" +
                               "、".join(sorted(set(project_blockers))) + "（人工补齐后重跑）")
    if duplicate_ids:
        human_checklist.append("重复 lead_id 必须人工改成唯一编号：" +
                               "、".join(esc(e["id"]) for e in duplicate_ids))
    if refused_fields:
        human_checklist.append(
            "删除被拒绝的字段（个人信息或受保护属性）后重跑：" +
            "、".join(esc(item["path"]) for item in refused_fields))
    if unknown_consent:
        human_checklist.append("逐一确认联系同意：" +
                               "、".join(esc(x) for x in unknown_consent))
    if capacity.get("available_slots") is not None and \
            capacity_summary["overflow"]:
        human_checklist.append(
            "人工容量不足：%d 条可跟进线索超过 %d 个可处理名额，请人工排期（仅为建议，不是承诺）" % (
                capacity_summary["followup_candidates"], slots))
    if not rules:
        human_checklist.append("先提供资格规则（范围/区域适配、最少必需事实、时效、优先顺序）")

    # --- markdown ---------------------------------------------------------
    markdown = render_markdown(payload, business, leads, status_counts,
                               project_blockers, project_unknowns,
                               capacity_summary, timeline, refused_fields,
                               injection_flagged, hidden, drafts, channels,
                               in_terms, area_terms, unknown_consent)

    project_state = aggregate_project_state(
        project_blockers, project_unknowns, leads, status_counts)
    if project_blockers and any(code in INCOMPLETE_CODES
                                for code in project_blockers):
        status = "INPUT_INCOMPLETE"
        project_state = None
    elif project_state == "BLOCKED":
        status = "BLOCKED"
    elif project_state == "READY_FOR_HUMAN_FOLLOWUP":
        status = "READY"
    else:
        status = "GAPS_FOUND"

    accepted_top, refused_top = collect_refs(
        as_list(payload.get("evidence_refs")), "evidence_refs", "")
    lead_accepted = [r for row in leads for r in row.get("evidence_refs", [])]
    lead_refused = [r for row in leads for r in row.get("refused_refs", [])]

    result = {
        "skill": "suge-lead-intake-readiness-priority-pack",
        "version": VERSION,
        "as_of": clean_text(as_of) or None,
        "as_of_date": date_part(as_of),
        "status": status,
        "project_state": project_state,
        "project_status": project_state,
        "business": {
            "business_id": business_id or None,
            "timezone": timezone or None,
            "product_scope": in_terms,
            "service_area": area_terms,
            "out_of_scope": excluded,
            "channels": channels,
        },
        "status_counts": status_counts,
        "duplicate_ids": duplicate_ids,
        "leads": [lead_view(row, hidden) for row in leads],
        "queue": queue,
        "priority_order_used": order_keys,
        "capacity_summary": capacity_summary,
        "intake_completeness": [completeness_view(row) for row in leads],
        "scope_evidence": scope_evidence,
        "next_step_cards": next_step_cards,
        "missing_facts": missing_facts,
        "timeline_conflicts": timeline,
        "refused_fields": refused_fields,
        "refused_refs": refused_top + lead_refused,
        "accepted_refs": accepted_top + lead_accepted,
        "injection_flagged": [{"path": path, "marker": "PROMPT_INJECTION"}
                              for path in injection_flagged],
        "contact_consent": {"granted": granted, "refused": refused_consent,
                            "unknown": unknown_consent},
        "drafts": drafts,
        "clarification_questions": clarification_questions,
        "human_checklist": human_checklist,
        "human_confirm_items": list(HUMAN_CONFIRM_BASE),
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
        "automation_declaration": NO_AUTOMATION_DECLARATION,
        "read_only": True,
        "network": False,
        "writes_files": False,
    }
    return result


def lead_view(row, hidden):
    if row["invalid"]:
        return {"lead_id": None, "path": row["path"], "state": row["effective_state"],
                "priority_band": row["priority_band"],
                "findings": row.get("findings", ["INVALID_LEAD_RECORD"]),
                "invalid": True}
    index = row["index"]

    def shown(key, value):
        return hidden_value("leads[%d]/%s" % (index, key), value, hidden)

    return {
        "lead_id": row["lead_id"], "path": row["path"],
        "source": shown("source", row["source"]),
        "received_at": row["received_at"],
        "requested_outcome": shown("requested_outcome", row["requested_outcome"]),
        "product_or_service": shown("product_or_service", row["product_or_service"]),
        "service_area": shown("service_area", row["service_area"]),
        "deadline": row["deadline"],
        "budget_status": row["budget_status"],
        "decision_timing": shown("decision_timing", row["decision_timing"]),
        "evidence_refs": [r["name"] for r in row.get("evidence_refs", [])],
        "contact_consent": row["contact_consent"],
        "state": row["effective_state"], "priority_band": row["priority_band"],
        "product_fit": row["product_fit"], "area_fit": row["area_fit"],
        "blockers": row.get("blockers", []), "unknown": row.get("unknown", []),
        "scope": row.get("scope", []), "consent": row.get("consent", []),
        "clarify": row.get("clarify", []),
        "findings": row.get("findings", []),
        "queue_position": row.get("queue_position"),
        "within_capacity": row.get("within_capacity"),
        "invalid": False,
    }


def completeness_view(row):
    if row["invalid"]:
        return {"lead_id": None, "path": row["path"], "invalid": True, "items": []}
    fields = [
        ("lead_id", row["lead_id"]), ("source", row["source"]),
        ("received_at", row["received_at"]),
        ("requested_outcome", row["requested_outcome"]),
        ("product_or_service", row["product_or_service"]),
        ("service_area", row["service_area"]), ("deadline", row["deadline"]),
        ("budget_status", row["budget_status"]),
        ("decision_timing", row["decision_timing"]),
        ("contact_consent", row["contact_consent"]),
    ]
    items = [{"fact": name, "present": bool(value) if not isinstance(value, bool)
              else True} for name, value in fields]
    return {"lead_id": row["lead_id"], "path": row["path"], "invalid": False,
            "items": items}


def aggregate_project_state(project_blockers, project_unknowns, leads, status_counts):
    if project_blockers:
        if any(code in INCOMPLETE_CODES for code in project_blockers):
            return None
        return "BLOCKED"
    if status_counts.get("BLOCKED"):
        return "BLOCKED"
    if project_unknowns or status_counts.get("INSUFFICIENT_EVIDENCE"):
        return "INSUFFICIENT_EVIDENCE"
    if status_counts.get("OUT_OF_DECLARED_SCOPE"):
        return "OUT_OF_DECLARED_SCOPE"
    if status_counts.get("WAITING_FOR_CONSENT"):
        return "WAITING_FOR_CONSENT"
    if status_counts.get("NEEDS_CLARIFICATION"):
        return "NEEDS_CLARIFICATION"
    return "READY_FOR_HUMAN_FOLLOWUP"


def hidden_value(path, value, hidden):
    text = clean_text(value)
    if not text:
        return None
    if path in hidden:
        return PLACEHOLDER
    return text


def render_markdown(payload, business, leads, status_counts, project_blockers,
                    project_unknowns, capacity_summary, timeline, refused_fields,
                    injection_flagged, hidden, drafts, channels, in_terms,
                    area_terms, unknown_consent):
    lines = ["# 销售线索接入完整度与下一步优先卡", ""]
    lines.append("- 业务编号：%s" % esc(business.get("business_id") or "（未提供）"))
    lines.append("- 时区：%s" % esc(business.get("timezone") or "（未提供）"))
    lines.append("- 线索状态汇总：" + "、".join(
        "%s=%d" % (state, status_counts.get(state, 0))
        for state in LEAD_STATES))
    lines.append("- 人工容量：" + (
        "名额 %s，可跟进 %d 条，超出 %s 条" % (
            capacity_summary["available_slots"],
            capacity_summary["followup_candidates"],
            capacity_summary["overflow"])
        if capacity_summary["available_slots"] is not None
        else "**未提供容量，无法给出排队建议**"))
    lines.append("")

    lines.append("## 线索优先队列")
    if not leads:
        lines.append("- （未提供线索）")
    for row in leads:
        ident = row["lead_id"] or row["path"]
        lines.append("- [%s] %s → %s（%s）" % (
            row.get("queue_position", 0), esc(ident),
            row["effective_state"], row["priority_band"]))
    lines.append("")

    lines.append("## 逐条线索")
    for row in leads:
        if row["invalid"]:
            lines.append("- %s：状态=BLOCKED（%s）" % (
                row["path"], "、".join(row.get("findings", []))))
            continue
        ident = esc(row["lead_id"] or row["path"])
        outcome = esc(hidden_value("leads[%d]/requested_outcome" % row["index"],
                                   row["requested_outcome"], hidden) or "（未提供需求）")
        lines.append("- %s：需求=%s；状态=%s；优先带=%s" % (
            ident, outcome, row["effective_state"], row["priority_band"]))
        lines.append("  - 范围适配：产品=%s，区域=%s" % (
            row["product_fit"], row["area_fit"]))
        if row["findings"]:
            lines.append("  - 判定码：%s" % "、".join(row["findings"]))
    lines.append("")

    lines.append("## 范围声明（来自你的事实）")
    lines.append("- 产品/服务范围：%s" % (
        "、".join(esc(x) for x in in_terms) if in_terms else "**未提供**"))
    lines.append("- 服务区域：%s" % (
        "、".join(esc(x) for x in area_terms) if area_terms else "**未提供**"))
    lines.append("- 明确不服务：%s" % (
        "、".join(esc(x) for x in as_list(business.get("out_of_scope")))
        if as_list(business.get("out_of_scope")) else "**未提供**"))
    lines.append("- 可用渠道：%s" % (
        "、".join(esc(x) for x in channels) if channels else "**未提供**"))
    lines.append("")

    lines.append("## 时间冲突")
    if not timeline:
        lines.append("- （无）")
    for item in timeline:
        lines.append("- %s：%s（%s）" % (
            item["code"], esc(item["detail"]), esc(item["lead_id"] or item["path"])))
    lines.append("")

    lines.append("## 拒绝字段 / 危险引用 / 注入标记")
    if not refused_fields:
        lines.append("- 拒绝字段：无")
    for item in refused_fields:
        lines.append("- 拒绝字段：%s（%s）" % (esc(item["path"]), item["reason"]))
    if not injection_flagged:
        lines.append("- 注入标记：无")
    for path in injection_flagged:
        lines.append("- 注入标记：%s" % esc(path))
    lines.append("")

    lines.append("## 草稿（未发送）")
    if not drafts:
        lines.append("- 无草稿（未获得联系同意或无需澄清）")
    for draft in drafts:
        lines.append("- %s → %s [%s]" % (
            esc(draft["draft_id"]), esc(draft["to_lead_id"]), draft["status"]))
    lines.append("")

    lines.append("## 人工复核提示")
    if unknown_consent:
        lines.append("- 联系同意未知：" + "、".join(esc(x) for x in unknown_consent))
    if project_blockers:
        lines.append("- 项目级硬问题：" + "、".join(sorted(set(project_blockers))))
    if project_unknowns:
        lines.append("- 项目级缺口：" + "、".join(project_unknowns))
    lines.append("")

    lines.append("## 备注")
    note = hidden_value("notes", payload.get("notes"), hidden)
    lines.append("- 备注：%s" % (esc(note) if note else "**未提供**"))
    lines.append("")

    lines.append("## 免责声明")
    lines.append("- " + esc(DISCLAIMER))
    lines.append("- " + esc(NO_AUTOMATION_DECLARATION))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def refusal(paths):
    return {
        "skill": "suge-lead-intake-readiness-priority-pack",
        "version": VERSION,
        "status": "REJECTED",
        "code": "CREDENTIAL_LIKE_INPUT",
        "project_status": None,
        "detected_fields": sorted(set(paths)),
        "message": "输入疑似包含凭据（字段名或值形如密钥/令牌），已整体拒绝处理，未回显任何内容。",
        "read_only": True,
        "network": False,
        "writes_files": False,
    }


def incomplete(code, message):
    return {
        "skill": "suge-lead-intake-readiness-priority-pack",
        "version": VERSION,
        "status": "INPUT_INCOMPLETE",
        "code": code,
        "project_status": None,
        "message": message,
        "read_only": True,
        "network": False,
        "writes_files": False,
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
                          "as_of 必须带时区偏移（如 2026-10-03T19:00:00+08:00）。")
    business = as_dict(payload.get("business"))
    if not has_text(business.get("business_id")):
        return incomplete("MISSING_BUSINESS_ID", "缺少 business.business_id。")
    if not has_text(business.get("timezone")):
        return incomplete("MISSING_TIMEZONE", "缺少 business.timezone。")
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
