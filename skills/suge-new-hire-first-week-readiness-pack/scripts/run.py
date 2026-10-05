#!/usr/bin/env python3
"""新员工首周入职准备包 — offline new-hire first-week readiness engine.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes, no HRIS/e-mail/calendar reads, no automatic account provisioning or
recovery, no equipment purchase, no message sending, no hiring/evaluation
decision, no protected-attribute use and no legal conclusion: it only sorts the
anonymous facts the caller already supplied into a human-review readiness pack.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta

VERSION = "1.0.0"
SKILL = "suge-new-hire-first-week-readiness-pack"

# --------------------------------------------------------------------------
# fixed vocabulary (documented in references/guide.md)
# --------------------------------------------------------------------------
# The whole pack, every area and every item end in exactly one of five
# readiness states. The tuple order is the severity order used everywhere.
READINESS_STATES = (
    "READY_FOR_DAY_ONE", "ACTION_NEEDED", "WAITING_ON_OWNER",
    "INSUFFICIENT_EVIDENCE", "BLOCKED",
)
SEVERITY = {
    "READY_FOR_DAY_ONE": 0, "ACTION_NEEDED": 1, "WAITING_ON_OWNER": 2,
    "INSUFFICIENT_EVIDENCE": 3, "BLOCKED": 4,
}

# The explicit item status the caller supplies. Unknown/missing is kept unknown.
ITEM_STATES = (
    "DONE", "SCHEDULED", "PENDING", "WAITING_ON_OWNER", "BLOCKED",
    "NOT_APPLICABLE", "UNKNOWN",
)
STATE_ALIASES = {
    "DONE": "DONE", "READY": "DONE", "COMPLETE": "DONE", "COMPLETED": "DONE",
    "SCHEDULED": "SCHEDULED", "PLANNED": "SCHEDULED", "ARRANGED": "SCHEDULED",
    "PENDING": "PENDING", "TODO": "PENDING", "OPEN": "PENDING",
    "IN_PROGRESS": "PENDING",
    "WAITING": "WAITING_ON_OWNER", "WAITING_ON_OWNER": "WAITING_ON_OWNER",
    "BLOCKED": "BLOCKED", "NA": "NOT_APPLICABLE", "N/A": "NOT_APPLICABLE",
    "NOT_NEEDED": "NOT_APPLICABLE", "NOT_APPLICABLE": "NOT_APPLICABLE",
    "UNKNOWN": "UNKNOWN",
}
# Explicit item status -> readiness state, when nothing else changes it.
STATUS_STATE = {
    "DONE": "READY_FOR_DAY_ONE", "NOT_APPLICABLE": "READY_FOR_DAY_ONE",
    "SCHEDULED": "READY_FOR_DAY_ONE", "PENDING": "ACTION_NEEDED",
    "WAITING_ON_OWNER": "WAITING_ON_OWNER", "BLOCKED": "BLOCKED",
    "UNKNOWN": "INSUFFICIENT_EVIDENCE",
}

# (input field, area name, id prefix, list must be non-empty)
KIND_SPECS = (
    ("equipment", "equipment", "EQ", True),
    ("access_items", "access", "AC", True),
    ("training_items", "training", "TR", True),
    ("meetings", "meetings", "MT", False),
    ("first_week_outcomes", "outcomes", "OC", False),
    ("policy_acknowledgements", "policy", "PL", False),
)
KIND_FIELD = {spec[0]: spec for spec in KIND_SPECS}
AREA_ORDER = tuple(spec[1] for spec in KIND_SPECS)
# Pre-day-one kinds must be ready before the start date; training and the other
# kinds legitimately run during the first week.
BEFORE_START_KINDS = ("equipment", "access_items")

# Decision codes, in the fixed order findings are listed.
BLOCKER_ORDER = (
    "INVALID_ITEM_RECORD", "MISSING_ROLE_INSTANCE_ID", "MISSING_ITEM_ID",
    "DUPLICATE_ITEM_ID", "INVALID_ITEM_STATUS", "BLOCKED_ITEM",
    "INVALID_START_DATE", "START_DATE_IN_PAST", "INVALID_DUE_AT",
    "INVALID_MEETING_TIME", "INVALID_MEETING_RANGE", "INVALID_WORKING_DAYS",
    "PERSONAL_DATA_REFUSED", "SENSITIVE_ATTRIBUTE_REFUSED",
)
UNKNOWN_ORDER = (
    "MISSING_START_DATE", "MISSING_HIRE_TIMEZONE", "MISSING_WORKING_DAYS",
    "MISSING_FIRST_WEEK_AVAILABILITY", "MISSING_OWNERS",
    "MISSING_EQUIPMENT_ITEMS", "MISSING_ACCESS_ITEMS", "MISSING_TRAINING_ITEMS",
    "MISSING_ITEM_STATUS", "MISSING_OWNER", "UNKNOWN_OWNER", "MISSING_DUE_AT",
    "MISSING_MEETING_TIME", "MISSING_EVIDENCE",
)
WAITING_ORDER = ("WAITING_ON_OWNER",)
# Unknowns that live at the organisation/new-hire scope (no single item path).
ORG_LEVEL_UNKNOWN = frozenset({
    "MISSING_START_DATE", "MISSING_HIRE_TIMEZONE", "MISSING_WORKING_DAYS",
    "MISSING_FIRST_WEEK_AVAILABILITY", "MISSING_OWNERS",
})
ACTION_ORDER = (
    "OVERDUE_ITEM", "PENDING_ITEM", "DUE_AFTER_START", "MEETING_OVERLAP",
    "TIMEZONE_MISMATCH", "START_ON_NON_WORKING_DAY",
)
INCOMPLETE_CODES = ("MISSING_AS_OF", "INVALID_AS_OF", "MISSING_ORGANIZATION_ID",
                    "MISSING_TIMEZONE")

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
    "name", "fullname", "realname", "contactname", "firstname", "lastname",
    "phone", "mobile", "tel", "telephone", "email", "mail", "wechat", "weixin",
    "wechatid", "qq", "account", "accountid", "address", "homeaddress",
    "cookie", "sessionid", "token", "password", "idcard", "idnumber",
    "passport", "creditcard", "ssn", "bankcard", "salary", "wage", "pay",
    "compensation", "contactemail", "contactphone",
})
SENSITIVE_KEYS = frozenset({
    "gender", "sex", "age", "birthdate", "birthday", "race", "ethnicity",
    "religion", "health", "medical", "disability", "maritalstatus",
    "marriagestatus", "nationality", "politics", "politicalview",
    "pregnancy", "familystatus",
})
# These structured fields are time/id facts, not free text: skip value scanning
# so an anonymous numeric id or an ISO timestamp never looks like PII.
SKIP_VALUE_SCAN = frozenset({
    "as_of", "organization_id", "timezone", "working_days", "role_instance_id",
    "start_date", "owner_id", "id", "due_at", "start_at", "end_at", "kind",
})

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MOBILE_RE = re.compile(r"(?<![0-9A-Za-z-])1[3-9]\d{9}(?![0-9A-Za-z-])")
IDCARD_RE = re.compile(
    r"(?<![0-9A-Za-z-])(?:\d{17}[\dXx]|\d{15})(?![0-9A-Za-z-])")
ACCOUNT_RE = re.compile(r"(?<![0-9A-Za-z-])\d{7,20}(?![0-9A-Za-z-])")
PII_VALUE_RES = (EMAIL_RE, MOBILE_RE, IDCARD_RE, ACCOUNT_RE)

# Injection is flagged only when an action verb and an instruction word appear
# in the SAME sentence, so ordinary onboarding notes are not mislabelled.
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
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
WEEKDAY_NAME = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

DISCLAIMER = (
    "本输出是新员工首周入职准备的人工准备材料，不是录用、绩效、合规或法律结论；"
    "所有结论只由输入中明示的匿名事实推导，缺失信息一律保持未知；是否录用、如何评价、"
    "是否开通账号、是否采购设备、如何发送消息以及材料是否合规，必须由人工确认。"
)

NO_AUTOMATION_DECLARATION = (
    "本工具不读取任何 HRIS/邮箱/日历/账号系统、不自动开通或回收任何账号、"
    "不采购任何设备、不发送任何消息、不作出任何录用或绩效判断、"
    "不使用任何受保护属性、不给出法律结论；它只把已提供的匿名事实整理成供人工复核的准备包。"
)

HUMAN_CONFIRM_BASE = (
    "是否录用、是否通过试用、如何评价员工必须由人工决定，本工具不参与任何录用或评价",
    "账号开通、权限授予与账号回收必须由人工或受控系统执行，本工具不申请也不开通任何权限",
    "设备采购、报销与付款必须由人工执行，本工具不下单、不付款",
    "劳动合同、薪资与税务必须由人工/法务/财务处理，本工具不处理也不给法律或财税结论",
    "制度与政策材料是否合规必须由人工/法务判断，本工具只生成确认清单，不作合规判断",
    "任何对内或对外的消息发送必须由人工执行，草稿一律为 DRAFT_NOT_SENT",
)

HUMAN_CHECKLIST_BASE = (
    "逐项核对准备度：设备、访问、培训、会议与首周结果是否事实齐全",
    "先修正硬问题（非法日期、重复编号、未识别状态、个人或受保护属性字段）再重跑",
    "对缺失事实逐条向对应负责人追问，不替任何人编造状态或时间",
    "按工作日与你声明的工作方式核对首周日程，跨时区时人工确认换算",
    "确认每位负责人的分工与可用时段，等待中的事项由人工跟进",
    "只对明确同意的对象准备 DRAFT_NOT_SENT 内部提醒草稿，未同意者不生成任何内容",
    "人工执行所有开通、采购、发送与判断动作，并对最终决定负责",
)

CODE_QUESTIONS = {
    "INVALID_ITEM_RECORD": "该记录不是对象，请改成含 id/status/owner_id/due_at 的条目后重试。",
    "MISSING_ROLE_INSTANCE_ID": "缺少匿名 role_instance_id，请补一个不含个人信息的岗位实例编号。",
    "MISSING_ITEM_ID": "该条目缺少匿名编号，请补一个不含个人信息的 id。",
    "DUPLICATE_ITEM_ID": "该 id 重复，请人工确认后改成唯一编号。",
    "INVALID_ITEM_STATUS": "状态不在允许词表内，请用 DONE/SCHEDULED/PENDING/WAITING_ON_OWNER/"
                           "BLOCKED/NOT_APPLICABLE/UNKNOWN 之一。",
    "BLOCKED_ITEM": "该必需条目被显式标为 BLOCKED，请先人工解决其阻塞原因。",
    "INVALID_START_DATE": "开始日期无法解析，请用 YYYY-MM-DD 且为真实日期。",
    "START_DATE_IN_PAST": "开始日期早于当前时点，请确认是否已入职或日期是否写错。",
    "INVALID_DUE_AT": "到期时间无法解析（需带时区偏移），请提供合规时间。",
    "INVALID_MEETING_TIME": "会议开始/结束时间无法解析（需带时区偏移）。",
    "INVALID_MEETING_RANGE": "会议结束时间不晚于开始时间，请修正。",
    "INVALID_WORKING_DAYS": "工作日取值不合法（应为 1–7 的整数，周一=1）。",
    "PERSONAL_DATA_REFUSED": "该字段疑似个人信息（姓名/电话/邮箱/账号/地址/薪资等），"
                             "请删除后再提供；本工具不处理也不回显个人数据。",
    "SENSITIVE_ATTRIBUTE_REFUSED": "该字段属于受保护属性（性别/年龄/民族/宗教/健康等），"
                                   "本工具不使用，请删除。",
    "MISSING_START_DATE": "缺少开始日期（YYYY-MM-DD），缺失时无法排首周。",
    "MISSING_HIRE_TIMEZONE": "缺少新员工时区，跨时区判断无法进行。",
    "MISSING_WORKING_DAYS": "缺少工作日定义，首周日程无法计算；本工具不假设默认工作日。",
    "MISSING_FIRST_WEEK_AVAILABILITY": "缺少首周可用时段，日程建议无法计算。",
    "MISSING_OWNERS": "尚未提供任何负责人，无法形成负责人矩阵。",
    "MISSING_EQUIPMENT_ITEMS": "设备清单为空，无法确认首日设备准备度。",
    "MISSING_ACCESS_ITEMS": "访问/账号项为空，无法确认首日访问准备度。",
    "MISSING_TRAINING_ITEMS": "培训项为空，无法确认首周培训准备度。",
    "MISSING_ITEM_STATUS": "该条目缺少显式状态；未知保持未知，请补充。",
    "MISSING_OWNER": "该条目缺少负责人，请指定一位匿名 owner_id。",
    "UNKNOWN_OWNER": "该条目的 owner_id 不在负责人清单中，请补充该负责人或改编号。",
    "MISSING_DUE_AT": "该条目缺少到期时间（需带时区偏移）。",
    "MISSING_MEETING_TIME": "会议缺少开始/结束时间（需带时区偏移）。",
    "MISSING_EVIDENCE": "首周结果条目缺少证据引用（单一文件名）。",
    "WAITING_ON_OWNER": "该条目等待负责人处理；请人工跟进后更新状态。",
    "OVERDUE_ITEM": "该条目到期时间已过且未完成，请人工确认是否仍然有效。",
    "PENDING_ITEM": "该条目仍在进行中，请人工确认能否在首日前完成。",
    "DUE_AFTER_START": "该条目到期晚于入职日，请确认是否可接受或提前。",
    "MEETING_OVERLAP": "两个会议时间重叠，请人工调整其中之一。",
    "TIMEZONE_MISMATCH": "新员工时区与组织时区不同，请人工确认换算后安排。",
    "START_ON_NON_WORKING_DAY": "入职日不是你声明的工作日，请人工确认首日安排。",
}
DEFAULT_QUESTION = "请补充该事项所需的事实。"

NEXT_STEP = {
    "READY_FOR_DAY_ONE": "无需阻塞动作；仍由人工确认最终安排。",
    "ACTION_NEEDED": "存在待处理或已逾期条目，请人工在首日前处理。",
    "WAITING_ON_OWNER": "存在等待负责人的条目，请人工跟进对应负责人。",
    "INSUFFICIENT_EVIDENCE": "关键事实缺失或状态未知，请先补齐再做判断。",
    "BLOCKED": "存在硬问题（非法日期、重复编号、未识别状态、个人信息或受保护属性字段），请先修正输入。",
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


def parse_date(value):
    """Return a date for an exact YYYY-MM-DD, or None."""
    if not isinstance(value, str) or not DATE_RE.match(value.strip()):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def epoch(value):
    parsed = parse_dt(value)
    return parsed.timestamp() if parsed else None


def norm(value):
    """Normalise a term: strip whitespace and fold case."""
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

    Only the location and reason are returned, so a refusal can name where the
    problem is without ever echoing the value back to the caller.
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


def hidden_value(path, value, hidden):
    text = clean_text(value)
    if not text:
        return None
    if path in hidden:
        return PLACEHOLDER
    return text


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def normalise_status(raw):
    """Return (canonical_status_or_None, was_missing, was_invalid)."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None, True, False
    if not isinstance(raw, str):
        return None, False, True
    token = re.sub(r"[\s-]+", "_", raw.strip()).upper()
    if token in STATE_ALIASES:
        return STATE_ALIASES[token], False, False
    return None, False, True


def analyse(payload):
    as_of = payload.get("as_of")
    as_of_dt = parse_dt(as_of)
    organization = as_dict(payload.get("organization"))
    new_hire = as_dict(payload.get("new_hire"))
    owners_raw = as_list(payload.get("owners"))

    organization_id = clean_text(organization.get("organization_id"))
    timezone = clean_text(organization.get("timezone"))
    work_mode = clean_text(organization.get("work_mode"))
    availability = [clean_text(x) for x in as_list(
        organization.get("first_week_availability")) if has_text(x)]

    # --- working days (never defaulted) -----------------------------------
    working_days, wd_missing, wd_invalid = [], False, False
    wd_raw = organization.get("working_days")
    if not isinstance(wd_raw, list) or not wd_raw:
        wd_missing = True
    else:
        for entry in wd_raw:
            if isinstance(entry, bool) or not isinstance(entry, int) or not 1 <= entry <= 7:
                wd_invalid = True
            elif entry not in working_days:
                working_days.append(entry)
        if wd_invalid:
            working_days = []

    # --- owners (stable order) --------------------------------------------
    owners, owner_ids, owner_index = [], [], {}
    owner_blockers, owner_unknowns = [], []
    if not owners_raw:
        owner_unknowns.append("MISSING_OWNERS")
    for index, raw in enumerate(owners_raw):
        path = "owners[%d]" % index
        if not isinstance(raw, dict):
            owner_blockers.append({"path": path, "code": "INVALID_ITEM_RECORD"})
            continue
        oid = clean_text(raw.get("owner_id")) or None
        responsibilities = [clean_text(x) for x in as_list(raw.get("responsibilities"))
                            if has_text(x)]
        avail = [clean_text(x) for x in as_list(raw.get("availability"))
                 if has_text(x)]
        if oid is None:
            owner_blockers.append({"path": path, "code": "MISSING_ITEM_ID"})
        elif oid in owner_index:
            owner_blockers.append({"path": path, "code": "DUPLICATE_ITEM_ID"})
        else:
            owner_index[oid] = index
        owners.append({"path": path, "index": index, "owner_id": oid,
                       "responsibilities": responsibilities, "availability": avail,
                       "raw": raw})
        if oid:
            owner_ids.append(oid)

    # --- new hire facts ---------------------------------------------------
    role_instance_id = clean_text(new_hire.get("role_instance_id"))
    role_title = clean_text(new_hire.get("role_title"))
    start_raw = clean_text(new_hire.get("start_date"))
    hire_timezone = clean_text(new_hire.get("timezone"))
    work_location_mode = clean_text(new_hire.get("work_location_mode"))

    hire_blockers, hire_unknowns, hire_actions = [], [], []
    start_date = parse_date(start_raw)
    if not start_raw:
        hire_unknowns.append("MISSING_START_DATE")
    elif start_date is None:
        hire_blockers.append("INVALID_START_DATE")
    elif as_of_dt is not None and start_date < as_of_dt.date():
        hire_blockers.append("START_DATE_IN_PAST")
    if not role_instance_id:
        hire_blockers.append("MISSING_ROLE_INSTANCE_ID")
    if not hire_timezone:
        hire_unknowns.append("MISSING_HIRE_TIMEZONE")
    elif timezone and norm(hire_timezone) != norm(timezone):
        hire_actions.append("TIMEZONE_MISMATCH")

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

    free_paths = [("notes", payload.get("notes")),
                  ("organization/work_mode", organization.get("work_mode")),
                  ("new_hire/role_title", new_hire.get("role_title")),
                  ("new_hire/work_location_mode", new_hire.get("work_location_mode"))]
    for index, entry in enumerate(as_list(organization.get("first_week_availability"))):
        free_paths.append(("organization/first_week_availability[%d]" % index, entry))
    for index, owner in enumerate(owners):
        for key in ("responsibilities", "availability"):
            for k, entry in enumerate(as_list(owner["raw"].get(key))):
                free_paths.append(("owners[%d]/%s[%d]" % (index, key, k), entry))
        free_paths.append(("owners[%d]/owner_id" % index, owner["raw"].get("owner_id")))
    for field, _area, _prefix, _req in KIND_SPECS:
        for index, raw in enumerate(as_list(payload.get(field))):
            if isinstance(raw, dict):
                free_paths.append(("%s[%d]/label" % (field, index), raw.get("label")))
    for path, value in free_paths:
        if value is None:
            continue
        scan(path, value)
    hidden = set(injection_flagged) | forbidden_paths

    # --- items ------------------------------------------------------------
    items_by_area = {area: [] for area in AREA_ORDER}
    all_items = []
    duplicate_ids = []
    seen_ids = {}
    for field, area, prefix, required_nonempty in KIND_SPECS:
        raw_items = as_list(payload.get(field))
        if required_nonempty and not raw_items:
            code = "MISSING_%s" % {"equipment": "EQUIPMENT_ITEMS",
                                   "access_items": "ACCESS_ITEMS",
                                   "training_items": "TRAINING_ITEMS"}[field]
            items_by_area[area].append({
                "kind": field, "area": area, "path": field, "index": None,
                "item_id": None, "invalid": True, "placeholder": True,
                "state": "INSUFFICIENT_EVIDENCE", "findings": [code],
                "required": True, "status": None, "owner_id": None,
                "due_at": None, "start_at": None, "end_at": None,
                "label": None, "evidence_refs": [], "refused_refs": []})
            continue
        for index, raw in enumerate(raw_items):
            path = "%s[%d]" % (field, index)
            if not isinstance(raw, dict):
                entry = _invalid_item(field, area, path, index, "INVALID_ITEM_RECORD")
                items_by_area[area].append(entry)
                all_items.append(entry)
                continue
            entry = build_item(field, area, prefix, path, index, raw, owners,
                               owner_index, as_of_dt, start_date, hidden)
            items_by_area[area].append(entry)
            all_items.append(entry)
            if entry["item_id"]:
                if entry["item_id"] in seen_ids:
                    duplicate_ids.append({"id": entry["item_id"],
                                          "paths": [seen_ids[entry["item_id"]], path]})
                else:
                    seen_ids[entry["item_id"]] = path
    dup_ids = {entry["id"] for entry in duplicate_ids}
    for entry in all_items:
        if entry["item_id"] and entry["item_id"] in dup_ids:
            mark_duplicate(entry)
    attach_refusals(all_items, refused_fields)

    # --- meeting overlaps -------------------------------------------------
    meetings = [entry for entry in items_by_area["meetings"]
                if entry.get("_start_epoch") is not None
                and entry.get("_end_epoch") is not None
                and entry.get("status") != "NOT_APPLICABLE"]
    ordered = sorted(meetings, key=lambda e: (e["_start_epoch"], e["_end_epoch"],
                                              e["item_id"] or e["path"]))
    overlap_ids = set()
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            first, second = ordered[i], ordered[j]
            if first["_start_epoch"] < second["_end_epoch"] and \
                    second["_start_epoch"] < first["_end_epoch"]:
                overlap_ids.add(first["path"])
                overlap_ids.add(second["path"])
    for entry in items_by_area["meetings"]:
        if entry["path"] in overlap_ids:
            add_action(entry, "MEETING_OVERLAP")

    # --- cross-cutting org-level findings ---------------------------------
    org_blockers, org_unknowns, org_actions = [], [], []
    if wd_invalid:
        org_blockers.append("INVALID_WORKING_DAYS")
    if wd_missing:
        org_unknowns.append("MISSING_WORKING_DAYS")
    if not availability:
        org_unknowns.append("MISSING_FIRST_WEEK_AVAILABILITY")
    if start_date is not None and not wd_missing and working_days and \
            start_date.isoweekday() not in working_days:
        org_actions.append("START_ON_NON_WORKING_DAY")

    # --- schedule ---------------------------------------------------------
    schedule = build_schedule(start_date, working_days, wd_missing, items_by_area)

    # --- per-area aggregation ---------------------------------------------
    areas = []
    for area in AREA_ORDER:
        entries = items_by_area[area]
        required = [e for e in entries if e["required"]]
        state = "READY_FOR_DAY_ONE"
        for entry in required:
            if SEVERITY[entry["state"]] > SEVERITY[state]:
                state = entry["state"]
        areas.append({
            "area": area,
            "state": state,
            "item_count": len([e for e in entries if not e.get("placeholder")]),
            "required_count": len(required),
            "unresolved": [e["item_id"] or e["path"] for e in required
                           if e["state"] != "READY_FOR_DAY_ONE"],
        })
    area_state = {entry["area"]: entry["state"] for entry in areas}

    # --- overall aggregation ----------------------------------------------
    # The aggregation reads every entry in every area, including the
    # placeholder that marks a required list that arrived empty.
    aggregate_entries = [entry for area in AREA_ORDER
                         for entry in items_by_area[area]]
    blockers, unknowns, waitings, actions = [], [], [], []
    blockers.extend(org_blockers)
    unknowns.extend(org_unknowns)
    unknowns.extend(owner_unknowns)
    actions.extend(org_actions)
    blockers.extend(hire_blockers)
    unknowns.extend(hire_unknowns)
    actions.extend(hire_actions)
    for entry in aggregate_entries:
        if not entry["required"]:
            continue
        for code in entry["findings"]:
            if code in BLOCKER_ORDER:
                blockers.append(code)
            elif code in UNKNOWN_ORDER:
                unknowns.append(code)
            elif code in WAITING_ORDER:
                waitings.append(code)
            elif code in ACTION_ORDER:
                actions.append(code)
    blockers.extend(owner_blockers and [b["code"] for b in owner_blockers] or [])
    if any(item["reason"] in ("PERSONAL_DATA", "SENSITIVE_ATTRIBUTE")
           for item in refused_fields):
        blockers.append("PERSONAL_DATA_REFUSED" if any(
            item["reason"] == "PERSONAL_DATA" for item in refused_fields) else None)
        if any(item["reason"] == "SENSITIVE_ATTRIBUTE" for item in refused_fields):
            blockers.append("SENSITIVE_ATTRIBUTE_REFUSED")
    blockers = [c for c in BLOCKER_ORDER if c in set([b for b in blockers if b])]
    unknowns = [c for c in UNKNOWN_ORDER if c in set(unknowns)]
    waitings = [c for c in WAITING_ORDER if c in set(waitings)]
    actions = [c for c in ACTION_ORDER if c in set(actions)]

    readiness_state = "READY_FOR_DAY_ONE"
    for code in blockers:
        readiness_state = "BLOCKED"
        break
    if readiness_state == "READY_FOR_DAY_ONE":
        if unknowns:
            readiness_state = "INSUFFICIENT_EVIDENCE"
        elif waitings:
            readiness_state = "WAITING_ON_OWNER"
        elif actions:
            readiness_state = "ACTION_NEEDED"

    # --- day-one checklist ------------------------------------------------
    day_one = []
    if start_date is not None:
        for entry in all_items:
            if entry.get("placeholder"):
                continue
            due = entry.get("_due_date")
            if due is not None and due <= start_date:
                day_one.append(item_view(entry, hidden))
        day_one.sort(key=lambda e: (e["due_at"] or "", e["item_id"] or e["path"]))

    # --- owner matrix -----------------------------------------------------
    owner_matrix = []
    for owner in sorted(owners, key=lambda o: (o["owner_id"] or o["path"])):
        assigned = [e for e in all_items if not e.get("placeholder")
                    and e.get("owner_id") == owner["owner_id"]
                    and owner["owner_id"] is not None]
        unresolved = [e["item_id"] or e["path"] for e in assigned
                      if e["state"] != "READY_FOR_DAY_ONE"]
        owner_matrix.append({
            "owner_id": owner["owner_id"], "path": owner["path"],
            "responsibilities": owner["responsibilities"],
            "availability": owner["availability"],
            "assigned": [e["item_id"] or e["path"] for e in assigned],
            "unresolved": sorted(unresolved),
        })

    # --- policy confirmations (confirmation only, never a compliance call) --
    policy_confirmations = []
    for entry in items_by_area["policy"]:
        if entry.get("placeholder"):
            continue
        policy_confirmations.append({
            "id": entry["item_id"], "path": entry["path"],
            "owner_id": entry["owner_id"], "status": entry["status"],
            "confirmation": "由人工确认已阅知；本工具只登记确认状态，不作任何合规判断。",
        })

    # --- time conflicts ---------------------------------------------------
    time_conflicts = []
    for entry in all_items:
        if entry.get("placeholder"):
            continue
        for code in entry["findings"]:
            if code in ("OVERDUE_ITEM", "DUE_AFTER_START", "MEETING_OVERLAP"):
                time_conflicts.append({"code": code, "path": entry["path"],
                                       "item_id": entry["item_id"]})
    if start_date is not None and not wd_missing and working_days and \
            start_date.isoweekday() not in working_days:
        time_conflicts.append({"code": "START_ON_NON_WORKING_DAY", "path": "new_hire",
                               "item_id": None})
    if "TIMEZONE_MISMATCH" in hire_actions:
        time_conflicts.append({"code": "TIMEZONE_MISMATCH", "path": "new_hire",
                               "item_id": None})
    order = {code: i for i, code in enumerate(ACTION_ORDER)}
    time_conflicts.sort(key=lambda c: (order.get(c["code"], 99), c["item_id"] or "",
                                       c["path"]))

    # --- missing facts ----------------------------------------------------
    # Org/new-hire scoped unknowns have no item path and are recorded here;
    # item-scoped unknowns are recorded once against their own item below.
    missing_facts = []
    for code in unknowns:
        if code in ORG_LEVEL_UNKNOWN:
            missing_facts.append({"path": code_path(code), "code": code})
    for entry in aggregate_entries:
        for code in entry["findings"]:
            if code in UNKNOWN_ORDER:
                missing_facts.append({"path": entry["path"], "code": code})

    # --- refusal lists ----------------------------------------------------
    accepted_top, refused_top = collect_refs(
        as_list(payload.get("evidence_refs")), "evidence_refs", "")
    lead_accepted = [r for item in all_items for r in item.get("evidence_refs", [])]
    lead_refused = [r for item in all_items for r in item.get("refused_refs", [])]

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
    for entry in aggregate_entries:
        for code in entry["findings"]:
            add_question(entry["path"], code)
    for code in actions:
        add_question(code_path(code), code)

    # --- reminder draft (consent gated, never sent) -----------------------
    reminder_draft = None
    if payload.get("communication_consent") is True and \
            readiness_state in ("ACTION_NEEDED", "WAITING_ON_OWNER"):
        reminder_draft = build_draft(readiness_state, all_items, owner_matrix, hidden)

    # --- human checklist --------------------------------------------------
    human_checklist = list(HUMAN_CHECKLIST_BASE)
    if blockers:
        human_checklist.append("先修正硬问题：" + "、".join(sorted(set(blockers))))
    if duplicate_ids:
        human_checklist.append("重复 id 必须人工改成唯一编号：" +
                               "、".join(esc(e["id"]) for e in duplicate_ids))
    if refused_fields:
        human_checklist.append("删除被拒绝的字段（个人信息或受保护属性）后重跑：" +
                               "、".join(esc(item["path"]) for item in refused_fields))
    if "MISSING_WORKING_DAYS" in unknowns:
        human_checklist.append("先提供工作日定义（1–7，周一=1）再计算首周日程")
    if wd_missing:
        human_checklist.append("首周日程无法计算：缺少工作日定义，工具不假设默认值")

    project_state = readiness_state
    if (blockers and any(code in INCOMPLETE_CODES for code in blockers)) or \
            not has_text(as_of) or as_of_dt is None or not organization_id or \
            not timezone:
        status = "INPUT_INCOMPLETE"
        project_state = None
    elif readiness_state == "BLOCKED":
        status = "BLOCKED"
    else:
        status = readiness_state

    item_state_counts = {name: 0 for name in READINESS_STATES}
    for entry in all_items:
        item_state_counts[entry["state"]] = item_state_counts.get(entry["state"], 0) + 1

    markdown = render_markdown(payload, organization, new_hire, areas, readiness_state,
                               blockers, unknowns, waitings, actions, all_items,
                               owner_matrix, schedule, time_conflicts, refused_fields,
                               injection_flagged, hidden, reminder_draft,
                               policy_confirmations, accepted_top,

                               organization_id, timezone, start_date, working_days)

    result = {
        "skill": SKILL,
        "version": VERSION,
        "as_of": clean_text(as_of) or None,
        "as_of_date": as_of_dt.date().isoformat() if as_of_dt else None,
        "status": status,
        "readiness_state": project_state,
        "organization": {
            "organization_id": organization_id or None,
            "work_mode": work_mode or None,
            "timezone": timezone or None,
            "working_days": working_days,
            "first_week_availability": availability,
        },
        "new_hire": {
            "role_instance_id": role_instance_id or None,
            "role_title": hidden_value("new_hire/role_title", role_title, hidden),
            "start_date": start_date.isoformat() if start_date else None,
            "timezone": hire_timezone or None,
            "work_location_mode": hidden_value("new_hire/work_location_mode",
                                               work_location_mode, hidden),
            "day_one_is_today": bool(start_date and as_of_dt and
                                     start_date == as_of_dt.date()),
        },
        "readiness_areas": areas,
        "area_states": area_state,
        "duplicate_ids": duplicate_ids,
        "item_state_counts": item_state_counts,
        "items": [item_view(entry, hidden) for entry in all_items],
        "day_one_checklist": day_one,
        "first_week_schedule": schedule,
        "owner_matrix": owner_matrix,
        "blockers": blockers,
        "missing_facts": missing_facts,
        "time_conflicts": time_conflicts,
        "overdue_items": [e["item_id"] or e["path"] for e in all_items
                          if "OVERDUE_ITEM" in e["findings"]],
        "policy_confirmations": policy_confirmations,
        "refused_fields": refused_fields,
        "refused_refs": refused_top + lead_refused,
        "accepted_refs": accepted_top + lead_accepted,
        "injection_flagged": [{"path": path, "marker": "PROMPT_INJECTION"}
                              for path in injection_flagged],
        "communication_consent": payload.get("communication_consent")
        if isinstance(payload.get("communication_consent"), bool) else None,
        "reminder_draft": reminder_draft,
        "clarification_questions": clarification_questions,
        "human_checklist": human_checklist,
        "human_confirm_items": list(HUMAN_CONFIRM_BASE),
        "next_step": NEXT_STEP[readiness_state] if project_state else None,
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
        "automation_declaration": NO_AUTOMATION_DECLARATION,
        "read_only": True,
        "network": False,
        "writes_files": False,
    }
    return result


def _invalid_item(field, area, path, index, code):
    return {"kind": field, "area": area, "path": path, "index": index,
            "item_id": None, "invalid": True, "placeholder": False,
            "label": None, "status": None, "owner_id": None, "due_at": None,
            "start_at": None, "end_at": None, "evidence_refs": [],
            "refused_refs": [], "required": True, "blockers": [code],
            "unknown": [], "waiting": [], "actions": [], "findings": [code],
            "state": "BLOCKED", "_due_date": None, "_start_epoch": None,
            "_end_epoch": None}


def build_item(field, area, prefix, path, index, raw, owners, owner_index,
               as_of_dt, start_date, hidden):
    item_id = clean_text(raw.get("id")) or None
    label = clean_text(raw.get("label")) or None
    status, missing, invalid = normalise_status(raw.get("status"))
    owner_id = clean_text(raw.get("owner_id")) or None
    required_field = raw.get("required")
    required = True
    if isinstance(required_field, bool):
        required = required_field
    if status == "NOT_APPLICABLE":
        required = False

    refs, refused_refs = collect_refs(as_list(raw.get("evidence_refs")),
                                      "evidence_refs", path + "/")

    blockers, unknown, waiting, actions = [], [], [], []
    if missing:
        unknown.append("MISSING_ITEM_STATUS")
    elif invalid:
        blockers.append("INVALID_ITEM_STATUS")
    if not item_id:
        blockers.append("MISSING_ITEM_ID")
    if required and not owner_id:
        unknown.append("MISSING_OWNER")
    elif owner_id and owner_id not in owner_index:
        unknown.append("UNKNOWN_OWNER")

    due_at = clean_text(raw.get("due_at")) or None
    due_date = None
    if due_at:
        parsed = parse_dt(due_at)
        if parsed is None:
            blockers.append("INVALID_DUE_AT")
        else:
            due_date = parsed.date()
            if status not in ("DONE", "NOT_APPLICABLE") and as_of_dt is not None and \
                    parsed < as_of_dt:
                actions.append("OVERDUE_ITEM")
    elif status in ("SCHEDULED", "PENDING", "WAITING_ON_OWNER") and required \
            and field != "meetings":
        unknown.append("MISSING_DUE_AT")
    if required and due_date is not None and start_date is not None and \
            field in BEFORE_START_KINDS and due_date > start_date:
        actions.append("DUE_AFTER_START")
    if status == "PENDING":
        actions.append("PENDING_ITEM")
    if status == "WAITING_ON_OWNER":
        waiting.append("WAITING_ON_OWNER")
    if status == "BLOCKED" and required:
        blockers.append("BLOCKED_ITEM")

    if field == "first_week_outcomes" and required and not refs:
        unknown.append("MISSING_EVIDENCE")

    start_epoch = end_epoch = None
    if field == "meetings":
        start_at = clean_text(raw.get("start_at")) or None
        end_at = clean_text(raw.get("end_at")) or None
        start_dt = parse_dt(start_at) if start_at else None
        end_dt = parse_dt(end_at) if end_at else None
        if not start_at or not end_at:
            unknown.append("MISSING_MEETING_TIME")
        elif start_dt is None or end_dt is None:
            blockers.append("INVALID_MEETING_TIME")
        elif end_dt <= start_dt:
            blockers.append("INVALID_MEETING_RANGE")
        else:
            start_epoch, end_epoch = start_dt.timestamp(), end_dt.timestamp()
        due_at = start_at

    findings = order_findings(blockers, unknown, waiting, actions)
    if blockers:
        state = "BLOCKED"
    elif "MISSING_ITEM_STATUS" in unknown or unknown:
        state = "INSUFFICIENT_EVIDENCE"
    elif waiting:
        state = "WAITING_ON_OWNER"
    elif actions:
        state = "ACTION_NEEDED"
    else:
        state = STATUS_STATE.get(status, "INSUFFICIENT_EVIDENCE")

    entry = {"kind": field, "area": area, "path": path, "index": index,
             "item_id": item_id, "invalid": False, "placeholder": False,
             "label": label, "status": status, "owner_id": owner_id,
             "due_at": due_at, "start_at": clean_text(raw.get("start_at")) or None,
             "end_at": clean_text(raw.get("end_at")) or None,
             "evidence_refs": refs, "refused_refs": refused_refs,
             "required": required, "blockers": blockers, "unknown": unknown,
             "waiting": waiting, "actions": actions, "findings": findings,
             "state": state, "_due_date": due_date, "_start_epoch": start_epoch,
             "_end_epoch": end_epoch}
    return entry


def order_findings(blockers, unknown, waiting, actions):
    return ([c for c in BLOCKER_ORDER if c in set(blockers)] +
            [c for c in UNKNOWN_ORDER if c in set(unknown)] +
            [c for c in WAITING_ORDER if c in set(waiting)] +
            [c for c in ACTION_ORDER if c in set(actions)])


def refresh(entry):
    entry["findings"] = order_findings(entry["blockers"], entry["unknown"],
                                       entry["waiting"], entry["actions"])
    if entry["blockers"]:
        entry["state"] = "BLOCKED"
    elif entry["unknown"]:
        entry["state"] = "INSUFFICIENT_EVIDENCE"
    elif entry["waiting"]:
        entry["state"] = "WAITING_ON_OWNER"
    elif entry["actions"]:
        entry["state"] = "ACTION_NEEDED"
    else:
        entry["state"] = STATUS_STATE.get(entry["status"], "INSUFFICIENT_EVIDENCE")
    return entry


def mark_duplicate(entry):
    if "DUPLICATE_ITEM_ID" not in entry["blockers"]:
        entry["blockers"].append("DUPLICATE_ITEM_ID")
    refresh(entry)


def add_action(entry, code):
    if code not in entry["actions"] and code not in entry["blockers"]:
        entry["actions"].append(code)
    refresh(entry)


def attach_refusals(all_items, refused_fields):
    """Attach personal/protected refusals to the exact item that holds them."""
    by_path = {entry["path"]: entry for entry in all_items}
    for item in refused_fields:
        parent = item["path"].split("/")[0]
        entry = by_path.get(parent)
        if entry is None:
            continue
        code = ("SENSITIVE_ATTRIBUTE_REFUSED"
                if item["reason"] == "SENSITIVE_ATTRIBUTE"
                else "PERSONAL_DATA_REFUSED")
        if code not in entry["blockers"]:
            entry["blockers"].append(code)
        refresh(entry)


def code_path(code):
    if "WORKING_DAYS" in code or "AVAILABILITY" in code:
        return "organization"
    if code in ("MISSING_START_DATE", "INVALID_START_DATE", "START_DATE_IN_PAST",
                "MISSING_HIRE_TIMEZONE", "MISSING_ROLE_INSTANCE_ID",
                "TIMEZONE_MISMATCH", "START_ON_NON_WORKING_DAY"):
        return "new_hire"
    if code == "MISSING_OWNERS":
        return "owners"
    if code in ("PERSONAL_DATA_REFUSED", "SENSITIVE_ATTRIBUTE_REFUSED"):
        return "input"
    return "input"


def build_schedule(start_date, working_days, wd_missing, items_by_area):
    if start_date is None:
        return []
    schedule = []
    for offset in range(7):
        day = start_date + timedelta(days=offset)
        iso = day.isoweekday()
        is_working = None if wd_missing else iso in working_days
        meetings = []
        for entry in items_by_area["meetings"]:
            if entry.get("_start_epoch") is None:
                continue
            start_dt = datetime.fromtimestamp(entry["_start_epoch"])
            if start_dt.date() == day:
                meetings.append({"item_id": entry["item_id"], "path": entry["path"],
                                 "start_at": entry["start_at"],
                                 "end_at": entry["end_at"]})
        meetings.sort(key=lambda m: (m["start_at"] or "", m["item_id"] or ""))
        due_items = []
        for area in AREA_ORDER:
            for entry in items_by_area[area]:
                if entry.get("_due_date") == day:
                    due_items.append(entry["item_id"] or entry["path"])
        schedule.append({
            "date": day.isoformat(),
            "weekday": WEEKDAY_NAME[iso - 1],
            "is_working_day": is_working,
            "meetings": meetings,
            "due_items": sorted(due_items),
        })
    return schedule


def build_draft(readiness_state, all_items, owner_matrix, hidden):
    outstanding = [e for e in all_items if e["required"]
                   and e["state"] != "READY_FOR_DAY_ONE"]
    lines = ["【草稿，未发送】",
             "以下首周准备事项仍需人工跟进，请相关负责人确认（本消息不会自动发送）："]
    for entry in outstanding:
        lines.append("- [%s] %s → %s（%s）" % (
            esc(entry["item_id"] or entry["path"]),
            esc(entry["label"]) if entry["label"] else esc(entry["area"]),
            entry["state"], esc(entry["owner_id"] or "未指定负责人")))
    lines.append("本条消息仅为草稿，必须由人工审阅后再决定是否发送；本工具绝不外发。")
    recipients = sorted({entry["owner_id"] for entry in outstanding if entry["owner_id"]})
    return {
        "draft_id": "DRAFT-FIRST-WEEK-READINESS",
        "to_owner_ids": recipients,
        "subject": "新员工首周准备待办（草稿，未发送）",
        "body": "\n".join(lines),
        "status": "DRAFT_NOT_SENT",
    }


def item_view(entry, hidden):
    if entry.get("placeholder"):
        return {"item_id": None, "path": entry["path"], "area": entry["area"],
                "state": entry["state"], "findings": entry["findings"],
                "required": True, "placeholder": True}
    index = entry["index"]
    label = hidden_value("%s[%d]/label" % (entry["kind"], index),
                         entry["label"], hidden) if index is not None else None
    return {
        "item_id": entry["item_id"], "path": entry["path"], "kind": entry["kind"],
        "area": entry["area"], "label": label, "status": entry["status"],
        "owner_id": entry["owner_id"], "due_at": entry["due_at"],
        "start_at": entry["start_at"], "end_at": entry["end_at"],
        "required": entry["required"], "state": entry["state"],
        "findings": entry["findings"], "placeholder": False,
        "evidence_refs": [r["name"] for r in entry["evidence_refs"]],
    }


def render_markdown(payload, organization, new_hire, areas, readiness_state,
                    blockers, unknowns, waitings, actions, all_items, owner_matrix,
                    schedule, time_conflicts, refused_fields, injection_flagged,
                    hidden, reminder_draft, policy_confirmations, accepted_top,
                    organization_id, timezone, start_date, working_days):
    lines = ["# 新员工首周入职准备卡", ""]
    lines.append("- 组织编号：%s" % esc(organization_id or "（未提供）"))
    lines.append("- 时区：%s" % esc(timezone or "（未提供）"))
    lines.append("- 岗位实例：%s" % esc(new_hire.get("role_instance_id") or "（未提供）"))
    lines.append("- 入职日：%s" % esc(start_date.isoformat() if start_date else "（未提供）"))
    lines.append("- 工作日：%s" % (
        "、".join(WEEKDAY_NAME[d - 1] for d in sorted(working_days))
        if working_days else "**未提供**"))
    lines.append("- 就绪状态：%s" % readiness_state)
    lines.append("- 准备度分布：" + "、".join(
        "%s=%d" % (area["area"], SEVERITY[area["state"]]) for area in areas))
    lines.append("")

    lines.append("## 准备度分区")
    for area in areas:
        lines.append("- %s：%s（未解决 %d）" % (
            area["area"], area["state"], len(area["unresolved"])))
    lines.append("")

    lines.append("## 首周日程（按声明工作日）")
    if not schedule:
        lines.append("- （缺少开始日期，无法排程）")
    for day in schedule:
        flag = "" if day["is_working_day"] else "（非工作日）"
        lines.append("- %s %s%s：会议 %d，到期事项 %d" % (
            day["date"], day["weekday"], flag, len(day["meetings"]),
            len(day["due_items"])))
    lines.append("")

    lines.append("## 负责人矩阵")
    if not owner_matrix:
        lines.append("- （未提供负责人）")
    for owner in owner_matrix:
        lines.append("- %s：负责 %d 项，未解决 %d 项" % (
            esc(owner["owner_id"] or owner["path"]), len(owner["assigned"]),
            len(owner["unresolved"])))
    lines.append("")

    lines.append("## 待办与阻塞")
    if blockers:
        lines.append("- 硬问题：" + "、".join(sorted(set(blockers))))
    if unknowns:
        lines.append("- 缺失事实：" + "、".join(unknowns))
    if waitings:
        lines.append("- 等待负责人：" + "、".join(waitings))
    if actions:
        lines.append("- 待处理：" + "、".join(actions))
    if not (blockers or unknowns or waitings or actions):
        lines.append("- （无）")
    lines.append("")

    lines.append("## 时间冲突")
    if not time_conflicts:
        lines.append("- （无）")
    for conflict in time_conflicts:
        lines.append("- %s：%s" % (conflict["code"],
                                   esc(conflict["item_id"] or conflict["path"])))
    lines.append("")

    lines.append("## 政策确认（仅登记，不作合规判断）")
    if not policy_confirmations:
        lines.append("- （无政策确认项）")
    for item in policy_confirmations:
        lines.append("- %s：%s（负责人 %s）" % (
            esc(item["id"] or item["path"]), item["status"],
            esc(item["owner_id"] or "未指定")))
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

    lines.append("## 内部提醒草稿")
    if reminder_draft is None:
        lines.append("- 无草稿（未同意、未知或存在阻塞/证据不足）")
    else:
        lines.append("- %s [%s]（未发送）" % (esc(reminder_draft["draft_id"]),
                                            reminder_draft["status"]))
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
        "skill": SKILL, "version": VERSION, "status": "REJECTED",
        "code": "CREDENTIAL_LIKE_INPUT", "readiness_state": None,
        "detected_fields": sorted(set(paths)),
        "message": "输入疑似包含凭据（字段名或值形如密钥/令牌），已整体拒绝处理，未回显任何内容。",
        "read_only": True, "network": False, "writes_files": False,
    }


def incomplete(code, message):
    return {
        "skill": SKILL, "version": VERSION, "status": "INPUT_INCOMPLETE",
        "code": code, "readiness_state": None, "message": message,
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
                          "as_of 必须带时区偏移（如 2026-10-04T19:00:00+08:00）。")
    organization = as_dict(payload.get("organization"))
    if not has_text(organization.get("organization_id")):
        return incomplete("MISSING_ORGANIZATION_ID", "缺少 organization.organization_id。")
    if not has_text(organization.get("timezone")):
        return incomplete("MISSING_TIMEZONE", "缺少 organization.timezone。")
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
