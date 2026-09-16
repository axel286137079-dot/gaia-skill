#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""品牌合作简报整理（suge-creator-sponsorship-brief-prep）离线确定性引擎。

用法：
    python3 scripts/run.py <input.json>

只读取一个已经导出的品牌合作简报 JSON，输出开工前的信息核对件：
交付物排期、义务与时间窗口、审批路径、素材与合规缺口、待澄清问题与开工前检查表。

不联网、不发送任何消息、不写任何文件、不做报价、不做合同或法律判断。
同一输入必然产出逐字节相同的输出（全部排序显式固定，天数用 Decimal）。
"""

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

TWO_PLACES = Decimal("0.01")
MINUTES_PER_DAY = Decimal(1440)
DUE_SOON_DAYS = Decimal("3")

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
    """递归扫描整份输入；命中即拒绝处理，且不回显命中的字段名或字符串内容。"""
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
# 文本与时间工具
# ---------------------------------------------------------------------------

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


def sanitize(value):
    """去除控制字符并裁剪空白；空串一律返回 None（未知，不补 0）。"""
    if not isinstance(value, str):
        return None
    text = "".join(
        char for char in value
        if not (ord(char) < 32 and char not in "\t\n\r") and ord(char) != 127)
    text = text.strip()
    return text or None


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
            "%s 无法解析为 ISO8601 时间（需形如 2026-09-17T10:00:00+08:00）。" % label)
    if parsed.utcoffset() is None:
        raise ValueError("%s 缺少时区偏移，无法计算天数。" % label)
    return parsed


def soft_parse_iso(value):
    """记录级时间：解析失败或缺少时区返回 None，由调用方记标志位。"""
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


def soft_parse_date(value):
    """YYYY-MM-DD；其它形式一律返回 None（不猜测日/月顺序）。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def days_between(later, earlier):
    delta = later - earlier
    seconds = Decimal(delta.days * 86400 + delta.seconds) + (
        Decimal(delta.microseconds) / Decimal(1000000))
    return (seconds / Decimal(60) / MINUTES_PER_DAY).quantize(
        TWO_PLACES, rounding=ROUND_HALF_UP)


def format_days(value):
    return None if value is None else format(value, ".2f")


# ---------------------------------------------------------------------------
# 固定规则表
# ---------------------------------------------------------------------------

DELIVERABLE_KINDS = frozenset(["VIDEO", "IMAGE", "ARTICLE", "LIVESTREAM", "STORY"])

OBLIGATION_KINDS = frozenset([
    "EXCLUSIVITY", "EMBARGO", "CONTENT_APPROVAL", "REVISION_ROUNDS",
    "DISCLOSURE", "DELIVERABLE_WINDOW", "OTHER"])
WINDOW_REQUIRED_KINDS = frozenset(["EXCLUSIVITY", "EMBARGO"])

CLAIM_CATEGORIES = frozenset([
    "SUPERLATIVE", "MEDICAL", "GUARANTEE", "COMPARATIVE", "PRICE", "OTHER"])
RISKY_CLAIM_CATEGORIES = frozenset(["SUPERLATIVE", "MEDICAL", "GUARANTEE"])

RIGHTS_FIELDS = (
    "channels", "territory", "term_start", "term_end",
    "exclusivity", "paid_media", "whitelisting")

DELIVERABLE_INVALID_FLAGS = frozenset([
    "KIND_INVALID", "QUANTITY_INVALID", "INVALID_DUE_AT", "DUPLICATE_DELIVERABLE_ID"])

TOPIC_ORDER = (
    "DELIVERABLE_GAP", "APPROVAL_PATH", "USAGE_RIGHTS", "DISCLOSURE",
    "BRAND_MATERIAL", "PAYMENT", "OBLIGATION_WINDOW",
    "CLAIM_SUBSTANTIATION", "RECORD_INVALID")

TOPIC_PRIORITY = {
    "DELIVERABLE_GAP": "P0",
    "APPROVAL_PATH": "P1",
    "USAGE_RIGHTS": "P1",
    "DISCLOSURE": "P1",
    "BRAND_MATERIAL": "P1",
    "PAYMENT": "P1",
    "OBLIGATION_WINDOW": "P1",
    "CLAIM_SUBSTANTIATION": "P2",
    "RECORD_INVALID": "P0",
}
PRIORITY_RANK = {"P0": 0, "P1": 1, "P2": 2}

INJECTION_PATTERNS = (
    re.compile(r"忽略(以上|之前|所有)?(指令|规则|要求)"),
    re.compile(r"忽略.{0,8}(指令|规则|要求)"),
    re.compile(r"ignore (all )?(previous|above) instructions", re.IGNORECASE),
    re.compile(r"系统提示词"),
    re.compile(r"system prompt", re.IGNORECASE),
    re.compile(r"你现在是"),
    re.compile(r"无条件"),
    re.compile(r"直接把钱"),
)

# 命中注入的字段在 markdown_summary 中用这个固定占位说明替代原文。
INJECTION_PLACEHOLDER = "已隐藏疑似提示注入文本"

# 能把一行文本变成 Markdown 结构（标题、列表、链接/图片、表格列、原始 HTML）
# 的字符。反斜杠与竖线必须始终转义：竖线会切开表格列，反斜杠会破坏转义本身。
MD_STRUCTURE_CHARS = frozenset("\\|`[]()#!<>")

TIMELINE_KINDS = {
    "DELIVERABLE_DUE": 0,
    "OBLIGATION_WINDOW_START": 1,
    "OBLIGATION_WINDOW_END": 2,
    "MATERIAL_DUE": 3,
    "BRIEF_RESPONSE_DUE": 4,
    "RIGHTS_TERM_START": 5,
    "RIGHTS_TERM_END": 6,
    "PAYMENT_DUE": 7,
}


def find_injection(text):
    if not isinstance(text, str):
        return False
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return True
    return False


def any_injection(*values):
    """任一字段命中提示注入即返回 True（用于逐条记录的风险标记）。"""
    return any(find_injection(value) for value in values)


def safe_ref(identifier, fallback):
    """注入来源的引用名。

    引用名会被写进 injection_flagged，因此标识符本身命中注入时不能拿它当引用
    （否则等于把注入原文回显到结构化输出），改用结构性定位名。
    """
    if isinstance(identifier, str) and identifier and not find_injection(identifier):
        return identifier
    return fallback


def md_escape(value):
    """不可信文本 → 单行、结构中性的 Markdown 文本。

    控制字符（含换行）一律压成空格，连续空白折叠；所有 Markdown 结构字符前加
    反斜杠。这样任何一个输入值都无法创建新标题、新列表、链接/图片或表格列。
    """
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    text = "".join(
        " " if (ord(char) < 32 or ord(char) == 127) else char for char in text)
    text = re.sub(r"\s+", " ", text).strip()
    return "".join(
        ("\\" + char) if char in MD_STRUCTURE_CHARS else char for char in text)


def md_field(value, fallback="—"):
    """把不可信值渲染进 markdown_summary 的唯一入口。

    命中提示注入的字段永远不落进摘要，改用固定占位说明；其余文本统一经
    md_escape 做结构转义。结构化 JSON 仍是原始值 + PROMPT_INJECTION_IGNORED
    风险标记。
    """
    if value is None:
        return fallback
    text = value if isinstance(value, str) else str(value)
    if find_injection(text):
        return INJECTION_PLACEHOLDER
    escaped = md_escape(text)
    return escaped if escaped else fallback


# ---------------------------------------------------------------------------
# 输入读取
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


def require_list(data, key):
    if key not in data or data[key] is None:
        return []
    value = data[key]
    if not isinstance(value, list):
        raise ValueError("%s 必须是数组。" % key)
    return value


# ---------------------------------------------------------------------------
# 简报主体
# ---------------------------------------------------------------------------

def read_brief(data, as_of):
    raw = data.get("brief")
    if not isinstance(raw, dict):
        raise ValueError("brief 必须是对象，且包含 brief_id。")
    brief_id = sanitize(raw.get("brief_id"))
    if brief_id is None:
        raise ValueError("brief.brief_id 不能为空。")

    flags = set()
    received_raw = raw.get("received_at")
    if received_raw is None or (isinstance(received_raw, str) and not received_raw.strip()):
        flags.add("MISSING_RECEIVED_AT")
    else:
        received = soft_parse_iso(received_raw)
        if received is None:
            flags.add("INVALID_RECEIVED_AT")
        elif received > as_of:
            flags.add("FUTURE_RECEIVED_AT")

    response_due = None
    response_raw = raw.get("response_due_at")
    if response_raw is not None and (not isinstance(response_raw, str) or not response_raw.strip()):
        flags.add("INVALID_RESPONSE_DUE_AT")
    elif response_raw is not None:
        response_due = soft_parse_iso(response_raw)
        if response_due is None:
            flags.add("INVALID_RESPONSE_DUE_AT")

    # brief 的标识字段会原样出现在结构化 JSON 与摘要里，命中注入时必须带风险标记。
    # source_text 不进结构化 JSON，它的风险由 injection_flagged 单独承载。
    if any_injection(brief_id, raw.get("brand_ref"), received_raw, response_raw):
        flags.add("PROMPT_INJECTION_IGNORED")

    return {
        "brief_id": brief_id,
        "brand_ref": sanitize(raw.get("brand_ref")),
        "received_at": sanitize(received_raw),
        "response_due": response_due,
        "source_text": raw.get("source_text"),
        "source_injected": find_injection(raw.get("source_text")),
        "review_flags": sorted(flags),
    }


def read_creator(data):
    raw = data.get("creator")
    if not isinstance(raw, dict):
        return None
    flags = set()
    if any_injection(raw.get("creator_ref"), raw.get("display_name"),
                     raw.get("default_platform")):
        flags.add("PROMPT_INJECTION_IGNORED")
    return {
        "creator_ref": sanitize(raw.get("creator_ref")),
        "display_name": sanitize(raw.get("display_name")),
        "default_platform": sanitize(raw.get("default_platform")),
        "review_flags": sorted(flags),
    }


# ---------------------------------------------------------------------------
# 交付物
# ---------------------------------------------------------------------------

def read_deliverables(data, as_of):
    rows = data.get("deliverables")
    if not isinstance(rows, list) or not rows:
        raise ValueError("deliverables 必须是至少包含一条记录的数组。")

    seen_ids = set()
    records = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("deliverables 第 %d 项必须是对象。" % (index + 1))
        deliverable_id = sanitize(raw.get("deliverable_id"))
        if deliverable_id is None:
            raise ValueError("deliverables 第 %d 项缺少 deliverable_id。" % (index + 1))

        flags = set()
        raw_name = raw.get("name")
        raw_notes = raw.get("notes")
        if has_control_chars(raw_name) or has_control_chars(raw_notes):
            flags.add("CONTROL_CHARS")

        name = sanitize(raw_name)
        kind = sanitize(raw.get("kind"))
        if kind is None or kind not in DELIVERABLE_KINDS:
            flags.add("KIND_INVALID")

        quantity = raw.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            flags.add("QUANTITY_INVALID")

        due_raw = raw.get("due_at")
        due_at = None
        due_missing = False
        if due_raw is None or (isinstance(due_raw, str) and not due_raw.strip()):
            due_missing = True
        else:
            due_at = soft_parse_iso(due_raw)
            if due_at is None:
                flags.add("INVALID_DUE_AT")

        if deliverable_id in seen_ids:
            flags.add("DUPLICATE_DELIVERABLE_ID")
        else:
            seen_ids.add(deliverable_id)

        platform = sanitize(raw.get("platform"))
        if platform is None:
            flags.add("PLATFORM_MISSING")

        approval = raw.get("approval_required")
        if not isinstance(approval, bool):
            approval = None
            flags.add("APPROVAL_UNKNOWN")

        if find_injection(raw_notes) or find_injection(raw_name) \
                or any_injection(deliverable_id, raw.get("kind"), raw.get("due_at"),
                                 raw.get("platform")):
            flags.add("PROMPT_INJECTION_IGNORED")

        invalid = bool(flags & DELIVERABLE_INVALID_FLAGS)
        # OVERDUE 由精确时间戳判定，不由四舍五入后的天数判定：
        # 逾期 30 秒的交付物四舍五入后是 -0.00 天，但状态必须仍是 OVERDUE。
        if invalid:
            schedule_state = "INVALID"
            days = None
        elif due_at is None:
            schedule_state = "UNKNOWN"
            days = None
        else:
            days = days_between(due_at, as_of)
            if due_at < as_of:
                schedule_state = "OVERDUE"
            elif days <= DUE_SOON_DAYS:
                schedule_state = "DUE_SOON"
            else:
                schedule_state = "OK"

        blocking = []
        if not invalid:
            if name is None:
                blocking.append("NAME_MISSING")
            if due_missing:
                blocking.append("DUE_AT_MISSING")
            if "APPROVAL_UNKNOWN" in flags:
                blocking.append("APPROVAL_UNKNOWN")

        records.append({
            "row_index": index + 1,
            "deliverable_id": deliverable_id,
            "name": name,
            "kind": kind,
            "quantity": quantity if isinstance(quantity, int) and not isinstance(quantity, bool) else None,
            "due_at": sanitize(due_raw),
            "platform": platform,
            "approval_required": approval,
            "days_until_due": format_days(days),
            "schedule_state": schedule_state,
            "status": "INVALID" if invalid else "VALID",
            "review_flags": sorted(flags),
            "blocking_gaps": sorted(blocking),
            "_due": due_at,
        })
    return records


# ---------------------------------------------------------------------------
# 义务
# ---------------------------------------------------------------------------

def read_obligations(data, as_of, known_deliverable_ids):
    rows = require_list(data, "obligations")
    seen_ids = set()
    records = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("obligations 第 %d 项必须是对象。" % (index + 1))
        obligation_id = sanitize(raw.get("obligation_id"))
        if obligation_id is None:
            raise ValueError("obligations 第 %d 项缺少 obligation_id。" % (index + 1))
        if obligation_id in seen_ids:
            raise ValueError("obligations 中出现重复的 obligation_id。")
        seen_ids.add(obligation_id)

        flags = set()
        kind = sanitize(raw.get("kind"))
        if kind is None or kind not in OBLIGATION_KINDS:
            flags.add("OBLIGATION_KIND_INVALID")

        start_raw = raw.get("window_start")
        end_raw = raw.get("window_end")
        start = None
        end = None
        if start_raw is not None:
            start = soft_parse_date(start_raw)
            if start is None:
                flags.add("INVALID_OBLIGATION_DATE")
        if end_raw is not None:
            end = soft_parse_date(end_raw)
            if end is None:
                flags.add("INVALID_OBLIGATION_DATE")

        as_of_date = as_of.date()
        if "OBLIGATION_KIND_INVALID" in flags:
            window_state = "NOT_APPLICABLE"
        elif kind in WINDOW_REQUIRED_KINDS:
            if end is None:
                window_state = "UNKNOWN"
                flags.add("OBLIGATION_WINDOW_UNKNOWN")
            elif end < as_of_date:
                window_state = "EXPIRED"
                flags.add("OBLIGATION_WINDOW_EXPIRED")
            elif start is not None and start > as_of_date:
                window_state = "UPCOMING"
            else:
                window_state = "ACTIVE"
        else:
            window_state = "NOT_APPLICABLE"

        refs = raw.get("deliverable_refs")
        if refs is None:
            refs = []
        if not isinstance(refs, list):
            raise ValueError("obligation %s 的 deliverable_refs 必须是数组。" % obligation_id)
        clean_refs = []
        for ref in refs:
            text = sanitize(ref)
            if text is None:
                raise ValueError("obligation %s 的 deliverable_refs 含空值。" % obligation_id)
            clean_refs.append(text)
        unknown_refs = sorted(set(ref for ref in clean_refs
                                  if ref not in known_deliverable_ids))
        if unknown_refs:
            flags.add("UNKNOWN_DELIVERABLE_REF")

        if any_injection(obligation_id, raw.get("kind"), raw.get("detail"),
                         start_raw, end_raw, *clean_refs):
            flags.add("PROMPT_INJECTION_IGNORED")

        records.append({
            "row_index": index + 1,
            "obligation_id": obligation_id,
            "kind": kind,
            "detail": sanitize(raw.get("detail")),
            "window_start": sanitize(start_raw),
            "window_end": sanitize(end_raw),
            "window_state": window_state,
            "deliverable_refs": sorted(set(clean_refs)),
            "unknown_refs": unknown_refs,
            "review_flags": sorted(flags),
            "_start": start,
            "_end": end,
        })
    return records


# ---------------------------------------------------------------------------
# 品牌方素材
# ---------------------------------------------------------------------------

def read_materials(data):
    rows = require_list(data, "brand_materials")
    seen_ids = set()
    records = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("brand_materials 第 %d 项必须是对象。" % (index + 1))
        material_id = sanitize(raw.get("material_id"))
        if material_id is None:
            raise ValueError("brand_materials 第 %d 项缺少 material_id。" % (index + 1))
        if material_id in seen_ids:
            raise ValueError("brand_materials 中出现重复的 material_id。")
        seen_ids.add(material_id)

        required = raw.get("required")
        if not isinstance(required, bool):
            raise ValueError("brand_materials 的 required 必须是布尔值（material %s）。"
                             % material_id)
        provided = raw.get("provided")
        if provided is not None and not isinstance(provided, bool):
            raise ValueError("brand_materials 的 provided 必须是布尔值或 null（material %s）。"
                             % material_id)

        flags = set()
        due_raw = raw.get("due_at")
        due = None
        if due_raw is not None:
            due = soft_parse_date(due_raw)
            if due is None:
                flags.add("INVALID_MATERIAL_DATE")

        if any_injection(material_id, raw.get("name"), due_raw):
            flags.add("PROMPT_INJECTION_IGNORED")

        gap = None
        if required:
            if provided is True:
                state = "PROVIDED"
            elif provided is False:
                state = "NOT_PROVIDED"
                gap = "REQUIRED_MATERIAL_NOT_PROVIDED"
            else:
                state = "STATUS_UNKNOWN"
                gap = "REQUIRED_MATERIAL_STATUS_UNKNOWN"
        else:
            if provided is True:
                state = "PROVIDED"
            elif provided is False:
                state = "OPTIONAL_NOT_PROVIDED"
            else:
                state = "OPTIONAL_STATUS_UNKNOWN"

        records.append({
            "row_index": index + 1,
            "material_id": material_id,
            "name": sanitize(raw.get("name")),
            "required": required,
            "provided": provided,
            "due_at": sanitize(due_raw),
            "state": state,
            "gaps": [gap] if gap else [],
            "review_flags": sorted(flags),
            "_due": due,
        })
    return records


# ---------------------------------------------------------------------------
# 审批路径
# ---------------------------------------------------------------------------

def read_approvals(data, obligations):
    rows = require_list(data, "approvals")
    seen_ids = set()
    steps = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("approvals 第 %d 项必须是对象。" % (index + 1))
        step_id = sanitize(raw.get("step_id"))
        if step_id is None:
            raise ValueError("approvals 第 %d 项缺少 step_id。" % (index + 1))
        if step_id in seen_ids:
            raise ValueError("approvals 中出现重复的 step_id。")
        seen_ids.add(step_id)

        order = raw.get("order")
        if isinstance(order, bool) or not isinstance(order, int):
            raise ValueError("approvals 的 order 必须是整数（step %s）。" % step_id)

        sla = raw.get("sla_hours")
        if sla is None:
            sla_state = "UNKNOWN"
        elif isinstance(sla, bool) or not isinstance(sla, int) or sla < 1:
            sla_state = "INVALID"
        else:
            sla_state = "KNOWN"

        step_flags = []
        if any_injection(step_id, raw.get("role")):
            step_flags.append("PROMPT_INJECTION_IGNORED")

        steps.append({
            "step_id": step_id,
            "role": sanitize(raw.get("role")),
            "order": order,
            "sla_hours": sla if sla_state == "KNOWN" else None,
            "sla_state": sla_state,
            "review_flags": sorted(step_flags),
        })

    steps.sort(key=lambda item: (item["order"], item["step_id"]))

    gaps = set()
    if any(step["sla_state"] == "UNKNOWN" for step in steps):
        gaps.add("APPROVAL_SLA_UNKNOWN")
    if any(step["sla_state"] == "INVALID" for step in steps):
        gaps.add("APPROVAL_SLA_INVALID")
    needs_approval = any(row["kind"] == "CONTENT_APPROVAL" for row in obligations)
    if needs_approval and not steps:
        gaps.add("APPROVAL_PATH_MISSING")

    if steps and all(step["sla_state"] == "KNOWN" for step in steps):
        total = sum(step["sla_hours"] for step in steps)
    else:
        total = None

    return {
        "step_count": len(steps),
        "steps": steps,
        "ordered_step_ids": [step["step_id"] for step in steps],
        "gaps": sorted(gaps),
        "total_sla_hours": total,
    }


# ---------------------------------------------------------------------------
# 授权范围与披露
# ---------------------------------------------------------------------------

def read_usage_rights(data):
    raw = data.get("usage_rights")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("usage_rights 必须是对象。")

    values = {}
    for field in RIGHTS_FIELDS:
        value = raw.get(field)
        if field == "channels" and value is not None:
            if not isinstance(value, list):
                raise ValueError("usage_rights.channels 必须是数组。")
            cleaned = []
            for item in value:
                text = sanitize(item)
                if text is None:
                    raise ValueError("usage_rights.channels 含空值。")
                cleaned.append(text)
            if not cleaned:
                value = None
            else:
                value = sorted(set(cleaned))
        values[field] = value

    unknown = sorted(field for field in RIGHTS_FIELDS if values[field] is None)
    result = dict(values)
    result["unknown_fields"] = unknown
    result["provided_field_count"] = len(RIGHTS_FIELDS) - len(unknown)
    result["required_field_count"] = len(RIGHTS_FIELDS)
    result["gaps"] = ["USAGE_RIGHTS_FIELD_UNKNOWN"] if unknown else []
    rights_texts = []
    for field in RIGHTS_FIELDS:
        value = values[field]
        if isinstance(value, list):
            rights_texts.extend(value)
        else:
            rights_texts.append(value)
    result["review_flags"] = (
        ["PROMPT_INJECTION_IGNORED"] if any_injection(*rights_texts) else [])
    return result


def read_disclosure(data):
    raw = data.get("disclosure")
    if raw is None:
        return {"required": None, "text": None, "platform_tool": None,
                "state": "UNKNOWN", "gaps": ["DISCLOSURE_REQUIREMENT_UNKNOWN"],
                "review_flags": []}
    if not isinstance(raw, dict):
        raise ValueError("disclosure 必须是对象。")

    required = raw.get("required")
    text = sanitize(raw.get("text"))
    tool = raw.get("platform_tool")
    gaps = []
    if required is None:
        state = "UNKNOWN"
        gaps.append("DISCLOSURE_REQUIREMENT_UNKNOWN")
    elif required is True:
        if text is None:
            gaps.append("DISCLOSURE_TEXT_MISSING")
        if tool is None:
            gaps.append("DISCLOSURE_TOOL_UNKNOWN")
        state = "INCOMPLETE" if gaps else "COMPLETE"
    else:
        state = "NOT_REQUIRED"
    flags = ["PROMPT_INJECTION_IGNORED"] if any_injection(text, tool) else []
    return {"required": required, "text": text, "platform_tool": tool,
            "state": state, "gaps": sorted(gaps), "review_flags": flags}


# ---------------------------------------------------------------------------
# 宣传声明与付款条款
# ---------------------------------------------------------------------------

def read_claims(data):
    rows = require_list(data, "claims")
    seen_ids = set()
    records = []
    flags = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ValueError("claims 第 %d 项必须是对象。" % (index + 1))
        claim_id = sanitize(raw.get("claim_id"))
        if claim_id is None:
            raise ValueError("claims 第 %d 项缺少 claim_id。" % (index + 1))
        if claim_id in seen_ids:
            raise ValueError("claims 中出现重复的 claim_id。")
        seen_ids.add(claim_id)

        category = sanitize(raw.get("category"))
        substantiated = raw.get("substantiated")
        review = []
        if category is None or category not in CLAIM_CATEGORIES:
            review.append("CLAIM_CATEGORY_INVALID")
        elif category in RISKY_CLAIM_CATEGORIES and substantiated is not True:
            review.append("FLAGGED_FOR_HUMAN_REVIEW")
            flags.append({"claim_id": claim_id,
                          "flag": "%s_UNSUBSTANTIATED" % category})
        if any_injection(claim_id, raw.get("text"), category):
            review.append("PROMPT_INJECTION_IGNORED")

        records.append({
            "row_index": index + 1,
            "claim_id": claim_id,
            "text": sanitize(raw.get("text")),
            "category": category,
            "substantiated": substantiated if isinstance(substantiated, bool) else None,
            "review_flags": sorted(review),
            "rewritten": False,
        })
    return records, flags


def read_payment(data):
    raw = data.get("payment")
    empty = {"currency": None, "amount_text": None, "amount_provided": False,
             "amount_interpreted": False, "milestone_count": 0, "milestones": [],
             "unknown_fields": [], "gaps": [], "review_flags": []}
    if raw is None:
        return empty
    if not isinstance(raw, dict):
        raise ValueError("payment 必须是对象。")

    gaps = set()
    amount = raw.get("amount")
    if amount is None:
        amount_text = None
        gaps.add("PAYMENT_AMOUNT_UNKNOWN")
    elif isinstance(amount, str):
        amount_text = amount.strip() or None
        if amount_text is None:
            gaps.add("PAYMENT_AMOUNT_UNKNOWN")
    else:
        # 原样回显，绝不解析成数字、绝不参与任何合计。
        amount_text = str(amount)

    milestones_raw = raw.get("milestones")
    if milestones_raw is None:
        milestones_raw = []
    if not isinstance(milestones_raw, list):
        raise ValueError("payment.milestones 必须是数组。")

    seen_ids = set()
    milestones = []
    date_unknown = False
    percent_unknown = False
    for index, item in enumerate(milestones_raw):
        if not isinstance(item, dict):
            raise ValueError("payment.milestones 第 %d 项必须是对象。" % (index + 1))
        milestone_id = sanitize(item.get("milestone_id"))
        if milestone_id is None:
            raise ValueError("payment.milestones 第 %d 项缺少 milestone_id。" % (index + 1))
        if milestone_id in seen_ids:
            raise ValueError("payment.milestones 中出现重复的 milestone_id。")
        seen_ids.add(milestone_id)

        due_raw = item.get("due_at")
        due = None
        if due_raw is not None:
            due = soft_parse_date(due_raw)
            if due is None:
                raise ValueError("payment.milestones 的 due_at 必须是 YYYY-MM-DD（%s）。"
                                 % milestone_id)
        if due is None:
            date_unknown = True

        percent = item.get("percent")
        if percent is None:
            percent_unknown = True
        elif isinstance(percent, bool) or not isinstance(percent, int):
            raise ValueError("payment.milestones 的 percent 必须是整数或 null（%s）。"
                             % milestone_id)

        milestones.append({
            "milestone_id": milestone_id,
            "trigger": sanitize(item.get("trigger")),
            "percent": percent,
            "due_at": sanitize(due_raw),
            "state": "UNKNOWN" if due is None else "DATED",
            "review_flags": (
                ["PROMPT_INJECTION_IGNORED"]
                if any_injection(milestone_id, item.get("trigger"), due_raw) else []),
            "_due": due,
        })

    unknown_fields = []
    if date_unknown:
        unknown_fields.append("milestone_dates")
        gaps.add("PAYMENT_MILESTONE_DATE_UNKNOWN")
    if percent_unknown:
        unknown_fields.append("milestone_percent")
        gaps.add("PAYMENT_MILESTONE_PERCENT_UNKNOWN")

    payment_flags = []
    if any_injection(raw.get("currency"), raw.get("amount"), amount_text):
        payment_flags.append("PROMPT_INJECTION_IGNORED")

    return {
        "currency": sanitize(raw.get("currency")),
        "amount_text": amount_text,
        "amount_provided": amount_text is not None,
        "amount_interpreted": False,
        "milestone_count": len(milestones),
        "milestones": milestones,
        "unknown_fields": sorted(unknown_fields),
        "gaps": sorted(gaps),
        "review_flags": sorted(payment_flags),
    }


# ---------------------------------------------------------------------------
# 时间线
# ---------------------------------------------------------------------------

def build_timeline(brief, deliverables, obligations, materials, rights, payment, as_of):
    offset = as_of.utcoffset()
    items = []

    def add(value, precision, kind, ref, state):
        if precision == "datetime":
            sort_key = value
            text = value.isoformat()
        else:
            sort_key = datetime.combine(value, datetime.min.time(),
                                        tzinfo=timezone(offset))
            text = value.isoformat()
        items.append({"at": text, "precision": precision, "kind": kind,
                      "ref": ref, "state": state, "_sort": sort_key})

    if brief["response_due"] is not None:
        add(brief["response_due"], "datetime", "BRIEF_RESPONSE_DUE",
            brief["brief_id"], "INFO")

    for row in deliverables:
        if row["status"] == "VALID" and row["_due"] is not None:
            add(row["_due"], "datetime", "DELIVERABLE_DUE",
                row["deliverable_id"], row["schedule_state"])

    for row in obligations:
        if row["_start"] is not None:
            add(row["_start"], "date", "OBLIGATION_WINDOW_START",
                row["obligation_id"], row["window_state"])
        if row["_end"] is not None:
            add(row["_end"], "date", "OBLIGATION_WINDOW_END",
                row["obligation_id"], row["window_state"])

    for row in materials:
        if row["_due"] is not None:
            add(row["_due"], "date", "MATERIAL_DUE", row["material_id"], row["state"])

    term_start = rights.get("term_start")
    if term_start is not None:
        parsed = soft_parse_date(term_start)
        if parsed is not None:
            add(parsed, "date", "RIGHTS_TERM_START", "usage_rights", "INFO")
    term_end = rights.get("term_end")
    if term_end is not None:
        parsed = soft_parse_date(term_end)
        if parsed is not None:
            add(parsed, "date", "RIGHTS_TERM_END", "usage_rights", "INFO")

    for milestone in payment["milestones"]:
        if milestone["_due"] is not None:
            add(milestone["_due"], "date", "PAYMENT_DUE",
                milestone["milestone_id"], milestone["state"])

    items.sort(key=lambda item: (item["_sort"], TIMELINE_KINDS[item["kind"]], item["ref"]))
    for item in items:
        del item["_sort"]
    return items


# ---------------------------------------------------------------------------
# 待澄清问题与开工前检查表
# ---------------------------------------------------------------------------

QUESTION_TEXT = {
    "DELIVERABLE_GAP":
        "以下交付物缺少关键信息（%s）：截止时间、名称或是否需要品牌方审批。"
        "请品牌方补齐后再排期，不要自行推断时间。",
    "APPROVAL_PATH":
        "简报要求内容需品牌方确认，但审批路径不完整（%s）。"
        "请确认每个审批环节的角色、顺序与各自需要的时间。",
    "USAGE_RIGHTS":
        "以下授权范围字段未在简报中给出（%s）：媒介渠道、地域、期限、是否排他、"
        "是否可用于付费投放、是否允许白名单授权。请品牌方明确后再开始制作。",
    "DISCLOSURE":
        "简报要求标注广告，但披露要求不完整（%s）。"
        "请确认披露文案、标注位置与平台工具口径。",
    "BRAND_MATERIAL":
        "以下品牌方素材尚未提供或状态不明（%s）。缺少素材会阻塞制作，请品牌方给出交付时间。",
    "PAYMENT":
        "付款条款缺少必要信息（%s）。请确认付款节点的时间点与金额比例；"
        "本工具只记录事实、不做报价、不计算金额。",
    "OBLIGATION_WINDOW":
        "以下义务的时间窗口缺失或已过期（%s）。"
        "请确认该义务当前是否仍然适用、起止时间是什么。",
    "CLAIM_SUBSTANTIATION":
        "以下宣传声明的证据状态不明（%s）。请品牌方提供依据；"
        "本工具只做标记，不改写为可对外发布的表述。",
    "RECORD_INVALID":
        "以下记录存在编号重复、字段非法或引用了不存在的交付物（%s）。"
        "请修正简报数据后重新运行。",
}

CHECK_ACTION = {
    "OVERDUE_DELIVERABLE": "先处理已逾期交付物：%s",
    "DELIVERABLE_GAP": "补齐交付物缺失字段：%s",
    "APPROVAL_PATH": "确认审批路径与各环节时限：%s",
    "USAGE_RIGHTS": "向品牌方确认授权范围字段：%s",
    "DISCLOSURE": "确认广告披露文案与平台工具：%s",
    "BRAND_MATERIAL": "催收品牌方素材或确认状态：%s",
    "PAYMENT": "确认付款节点的时间与比例（不做报价）：%s",
    "OBLIGATION_WINDOW": "确认义务时间窗口是否仍然适用：%s",
    "CLAIM_SUBSTANTIATION": "向品牌方索取声明证据：%s",
    "RECORD_INVALID": "修正无效记录后再运行：%s",
}


def build_questions(deliverables, approvals, rights, disclosure, materials,
                    payment, obligations, claim_flags):
    collected = {}

    def add(topic, refs):
        collected.setdefault(topic, set()).update(refs)

    gap_rows = [row for row in deliverables
                if row["status"] == "VALID" and row["blocking_gaps"]]
    if gap_rows:
        add("DELIVERABLE_GAP", [row["deliverable_id"] for row in gap_rows])

    if approvals["gaps"]:
        add("APPROVAL_PATH", [step["step_id"] for step in approvals["steps"]
                              if step["sla_state"] != "KNOWN"] or ["approval_path"])

    if rights["unknown_fields"]:
        add("USAGE_RIGHTS", ["usage_rights." + field for field in rights["unknown_fields"]])

    if disclosure["gaps"]:
        refs = []
        if "DISCLOSURE_TEXT_MISSING" in disclosure["gaps"]:
            refs.append("disclosure.text")
        if "DISCLOSURE_TOOL_UNKNOWN" in disclosure["gaps"]:
            refs.append("disclosure.platform_tool")
        if "DISCLOSURE_REQUIREMENT_UNKNOWN" in disclosure["gaps"]:
            refs.append("disclosure.required")
        add("DISCLOSURE", refs)

    material_gaps = [row["material_id"] for row in materials if row["gaps"]]
    if material_gaps:
        add("BRAND_MATERIAL", material_gaps)

    if payment["gaps"]:
        add("PAYMENT", [item["milestone_id"] for item in payment["milestones"]] or ["payment"])

    window_rows = [row["obligation_id"] for row in obligations
                   if row["window_state"] in ("UNKNOWN", "EXPIRED")]
    if window_rows:
        add("OBLIGATION_WINDOW", window_rows)

    if claim_flags:
        add("CLAIM_SUBSTANTIATION", [item["claim_id"] for item in claim_flags])

    invalid_refs = [row["deliverable_id"] for row in deliverables
                    if row["status"] == "INVALID"]
    invalid_refs += [row["obligation_id"] for row in obligations if row["unknown_refs"]]
    if invalid_refs:
        add("RECORD_INVALID", invalid_refs)

    questions = []
    for position, topic in enumerate(TOPIC_ORDER):
        if topic not in collected:
            continue
        refs = sorted(collected[topic])
        detail = "、".join(refs) if refs else "见上"
        questions.append({
            "question_id": "Q-%02d" % (position + 1),
            "topic": topic,
            "related_refs": refs,
            "question": QUESTION_TEXT[topic] % detail,
        })
    for number, item in enumerate(questions, start=1):
        item["question_id"] = "Q-%02d" % number
    return questions


def build_checklist(questions, overdue_ids):
    items = []
    if overdue_ids:
        items.append({
            "topic": "OVERDUE_DELIVERABLE",
            "priority": "P0",
            "related_refs": sorted(overdue_ids),
            "action": CHECK_ACTION["OVERDUE_DELIVERABLE"] % "、".join(sorted(overdue_ids)),
            "_rank": 0,
        })
    for topic in TOPIC_ORDER:
        match = [item for item in questions if item["topic"] == topic]
        if not match:
            continue
        refs = match[0]["related_refs"]
        detail = "、".join(refs) if refs else "见上"
        items.append({
            "topic": topic,
            "priority": TOPIC_PRIORITY[topic],
            "related_refs": refs,
            "action": CHECK_ACTION[topic] % detail,
            "_rank": TOPIC_ORDER.index(topic) + 1,
        })
    items.sort(key=lambda item: (PRIORITY_RANK[item["priority"]], item["_rank"]))
    for number, item in enumerate(items, start=1):
        item["item_id"] = "C-%02d" % number
        del item["_rank"]
    return [{"item_id": item["item_id"], "priority": item["priority"],
             "topic": item["topic"], "action": item["action"],
             "related_refs": item["related_refs"]} for item in items]


# ---------------------------------------------------------------------------
# Markdown 摘要
# ---------------------------------------------------------------------------

def build_markdown(result, deliverables, obligations, materials, questions, checklist):
    rights = result["usage_rights"]
    payment = result["payment"]
    counts = result["status_counts"]
    lines = []
    lines.append("## 品牌合作简报整理")
    lines.append("")
    lines.append("- 简报：%s；品牌：%s；基准时间：%s" % (
        md_field(result["brief_id"]), md_field(result["brand_ref"], "未提供"),
        md_field(result["as_of"])))
    lines.append("- 整体状态：%s" % md_field(result["status"]))
    lines.append("- 交付物：%d 条（唯一编号 %d 个，可排期 %d 条）" % (
        result["deliverable_count"], result["unique_deliverable_id_count"],
        result["valid_deliverable_count"]))
    lines.append("- 排期分布：正常 %d / 临近 %d / 已逾期 %d / 时间未知 %d / 记录无效 %d" % (
        counts["OK"], counts["DUE_SOON"], counts["OVERDUE"],
        counts["UNKNOWN"], counts["INVALID"]))
    lines.append("- 义务：%d 条；素材缺口：%d 项；授权范围缺口：%d 项" % (
        result["obligation_count"], result["material_gap_count"],
        len(rights["unknown_fields"])))
    lines.append("- 待澄清问题：%d 条；开工前检查项：%d 条" % (
        len(questions), len(checklist)))
    lines.append("- 提示注入命中：%s" % (
        "、".join(md_field(ref) for ref in result["injection_flagged"])
        if result["injection_flagged"] else "无"))
    lines.append("")
    lines.append("### 交付物")
    lines.append("")
    lines.append("| 状态 | 编号 | 名称 | 类型 | 数量 | 截止 | 剩余天数 | 需审批 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in deliverables:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            md_field(row["schedule_state"]), md_field(row["deliverable_id"]),
            md_field(row["name"]), md_field(row["kind"]),
            md_field(row["quantity"]), md_field(row["due_at"]),
            md_field(row["days_until_due"]),
            "是" if row["approval_required"] is True
            else "否" if row["approval_required"] is False else "未知"))
    lines.append("")
    lines.append("### 义务与排他")
    lines.append("")
    if obligations:
        for row in obligations:
            if row["window_state"] == "NOT_APPLICABLE":
                lines.append("- %s（%s）：%s" % (
                    md_field(row["obligation_id"]), md_field(row["kind"], "未知"),
                    md_field(row["detail"], "未提供细节")))
            else:
                window = "%s ~ %s" % (md_field(row["window_start"], "未知"),
                                      md_field(row["window_end"], "未知"))
                lines.append("- %s（%s，%s）：%s；窗口 %s" % (
                    md_field(row["obligation_id"]), md_field(row["kind"], "未知"),
                    md_field(row["window_state"]),
                    md_field(row["detail"], "未提供细节"), window))
            if row["unknown_refs"]:
                lines.append("  - 引用了不存在的交付物：%s"
                             % "、".join(md_field(ref) for ref in row["unknown_refs"]))
    else:
        lines.append("- 简报未列出任何附加义务。")
    lines.append("")
    lines.append("### 审批路径")
    lines.append("")
    approval = result["approval_path"]
    if approval["steps"]:
        for step in approval["steps"]:
            lines.append("- 第 %s 步 %s（%s）：时限 %s" % (
                md_field(step["order"]), md_field(step["role"], "未注明角色"),
                md_field(step["step_id"]),
                "%d 小时" % step["sla_hours"] if step["sla_hours"] is not None else "未知"))
        lines.append("- 全流程合计：%s" % (
            "%d 小时" % approval["total_sla_hours"]
            if approval["total_sla_hours"] is not None
            else "无法合计（存在未知时限，不按已给出部分求和）"))
    else:
        lines.append("- 简报未提供审批路径。")
    lines.append("")
    lines.append("### 素材与合规缺口")
    lines.append("")
    lines.append("- 品牌方素材：")
    if materials:
        for row in materials:
            lines.append("  - %s（%s）：%s%s" % (
                md_field(row["material_id"]), md_field(row["name"], "未命名"),
                md_field(row["state"]),
                "；截止 %s" % md_field(row["due_at"]) if row["due_at"] else ""))
    else:
        lines.append("  - 简报未列出品牌方素材。")
    lines.append("- 授权范围缺口：%s" % (
        "、".join(md_field(field) for field in rights["unknown_fields"])
        if rights["unknown_fields"] else "无"))
    lines.append("- 披露要求：%s（%s）" % (
        md_field(result["disclosure"]["state"]),
        "、".join(md_field(gap) for gap in result["disclosure_gaps"])
        if result["disclosure_gaps"] else "无缺口"))
    lines.append("- 声明标记：%s" % (
        "、".join("%s=%s" % (md_field(item["claim_id"]), md_field(item["flag"]))
                  for item in result["claim_flags"]) if result["claim_flags"] else "无"))
    lines.append("- 付款条款：金额口径 %s（原样回显，未做任何解释或合计）；缺口 %s" % (
        md_field(payment["amount_text"], "未知"),
        "、".join(md_field(gap) for gap in payment["gaps"]) if payment["gaps"] else "无"))
    lines.append("")
    lines.append("### 待澄清问题")
    lines.append("")
    if questions:
        for item in questions:
            lines.append("- %s [%s] %s" % (
                md_field(item["question_id"]), md_field(item["topic"]),
                md_field(item["question"])))
    else:
        lines.append("- 无：简报信息完整，可直接开工。")
    lines.append("")
    lines.append("### 开工前检查表")
    lines.append("")
    for item in checklist:
        lines.append("- [%s] %s" % (md_field(item["priority"]), md_field(item["action"])))
    lines.append("")
    lines.append("### 使用提醒")
    lines.append("- 本文件是开工前的信息核对件，不构成法律意见、合规结论或商业承诺。")
    lines.append("- 脚本不发送任何消息、不回复品牌方、不做报价、不计算金额、不代替人工确认条款。")
    lines.append("- 所有标注为「未知」的日期、金额、时限与权利范围必须由品牌方书面补齐后才能开工。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

ORDER_STATE = ("ACTIVE", "UPCOMING", "NOT_APPLICABLE", "EXPIRED", "UNKNOWN")
DELIVERABLE_ORDER = ("OK", "DUE_SOON", "OVERDUE", "UNKNOWN", "INVALID")


def analyse(data):
    scan_privacy(data)
    if not isinstance(data, dict):
        raise ValueError("输入 JSON 的顶层必须是对象。")

    as_of = parse_iso(data.get("as_of"), "as_of")
    brief = read_brief(data, as_of)
    creator = read_creator(data)

    deliverables = read_deliverables(data, as_of)
    known_ids = set(row["deliverable_id"] for row in deliverables)
    obligations = read_obligations(data, as_of, known_ids)
    materials = read_materials(data)
    approvals = read_approvals(data, obligations)
    rights = read_usage_rights(data)
    disclosure = read_disclosure(data)
    claims, claim_flags = read_claims(data)
    payment = read_payment(data)

    # injection_flagged 必须能定位每一条命中来源，而不只是 brief 与交付物。
    # 引用名取自记录自身的编号；编号本身命中注入时改用结构性定位名，避免回显原文。
    injection_sources = []
    if brief["source_injected"] or "PROMPT_INJECTION_IGNORED" in brief["review_flags"]:
        injection_sources.append(safe_ref(brief["brief_id"], "brief"))
    if creator is not None and "PROMPT_INJECTION_IGNORED" in creator["review_flags"]:
        injection_sources.append("creator")
    for row in deliverables:
        if "PROMPT_INJECTION_IGNORED" in row["review_flags"]:
            injection_sources.append(safe_ref(
                row["deliverable_id"], "deliverables[%d]" % row["row_index"]))
    for row in obligations:
        if "PROMPT_INJECTION_IGNORED" in row["review_flags"]:
            injection_sources.append(safe_ref(
                row["obligation_id"], "obligations[%d]" % row["row_index"]))
    for row in materials:
        if "PROMPT_INJECTION_IGNORED" in row["review_flags"]:
            injection_sources.append(safe_ref(
                row["material_id"], "brand_materials[%d]" % row["row_index"]))
    for step in approvals["steps"]:
        if "PROMPT_INJECTION_IGNORED" in step["review_flags"]:
            injection_sources.append(safe_ref(step["step_id"], "approvals"))
    for row in claims:
        if "PROMPT_INJECTION_IGNORED" in row["review_flags"]:
            injection_sources.append(safe_ref(row["claim_id"], "claims"))
    for milestone in payment["milestones"]:
        if "PROMPT_INJECTION_IGNORED" in milestone["review_flags"]:
            injection_sources.append(safe_ref(
                milestone["milestone_id"], "payment.milestones"))
    if payment["review_flags"]:
        injection_sources.append("payment")
    if rights["review_flags"]:
        injection_sources.append("usage_rights")
    if disclosure["review_flags"]:
        injection_sources.append("disclosure")
    injection_flagged = sorted(set(injection_sources))

    status_counts = dict((key, 0) for key in DELIVERABLE_ORDER)
    for row in deliverables:
        status_counts[row["schedule_state"]] += 1

    obligation_state_counts = dict((key, 0) for key in ORDER_STATE)
    for row in obligations:
        obligation_state_counts[row["window_state"]] += 1

    questions = build_questions(deliverables, approvals, rights, disclosure,
                                materials, payment, obligations, claim_flags)

    overdue_ids = sorted(row["deliverable_id"] for row in deliverables
                         if row["schedule_state"] == "OVERDUE")
    due_soon_ids = sorted(row["deliverable_id"] for row in deliverables
                          if row["schedule_state"] == "DUE_SOON")

    valid_rows = [row for row in deliverables if row["status"] == "VALID"]
    if not valid_rows:
        top_status = "INVALID"
    elif overdue_ids:
        top_status = "BLOCKED"
    elif questions:
        top_status = "CLARIFICATION_REQUIRED"
    else:
        top_status = "READY_TO_START"

    result = {
        "status": top_status,
        "as_of": as_of.isoformat(),
        "brief_id": brief["brief_id"],
        "brand_ref": brief["brand_ref"],
        "creator": creator,
        "brief_review_flags": brief["review_flags"],
        "deliverable_count": len(deliverables),
        "unique_deliverable_id_count": len(known_ids),
        "valid_deliverable_count": len(valid_rows),
        "status_counts": status_counts,
        "overdue_deliverable_ids": overdue_ids,
        "due_soon_deliverable_ids": due_soon_ids,
        "obligation_count": len(obligations),
        "obligation_state_counts": obligation_state_counts,
        "material_gap_count": sum(1 for row in materials if row["gaps"]),
        "rights_gap_fields": list(rights["unknown_fields"]),
        "disclosure_gaps": list(disclosure["gaps"]),
        "claim_flags": claim_flags,
        "injection_flagged": injection_flagged,
        "deliverables": [],
        "obligations": [],
        "brand_materials": [],
        "approval_path": approvals,
        "usage_rights": rights,
        "disclosure": disclosure,
        "claims": claims,
        "payment": payment,
        "timeline": [],
        "clarification_questions": questions,
        "preflight_checklist": build_checklist(questions, overdue_ids),
        "markdown_summary": "",
        "disclaimer": (
            "本输出是开工前的信息核对件，不构成法律意见、合规结论或商业承诺；"
            "脚本不发送任何消息、不回复品牌方、不做报价、不计算金额，"
            "所有未在简报中出现的时间、金额、审批时限与权利范围一律保持未知，"
            "必须由品牌方书面确认后才能开工。"
        ),
    }

    # 时间线必须在内部分字段被剥离前构建。
    result["timeline"] = build_timeline(
        brief, deliverables, obligations, materials, rights, payment, as_of)
    result["deliverables"] = [
        dict((key, value) for key, value in row.items() if not key.startswith("_"))
        for row in deliverables]
    result["obligations"] = [
        dict((key, value) for key, value in row.items() if not key.startswith("_"))
        for row in obligations]
    result["brand_materials"] = [
        dict((key, value) for key, value in row.items() if not key.startswith("_"))
        for row in materials]
    result["payment"]["milestones"] = [
        dict((key, value) for key, value in item.items() if not key.startswith("_"))
        for item in payment["milestones"]]
    result["markdown_summary"] = build_markdown(
        result, result["deliverables"], result["obligations"],
        result["brand_materials"], questions, result["preflight_checklist"])
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
