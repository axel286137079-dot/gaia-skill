#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""客户名单清洗与去重引擎（离线 / 只读 / 确定性）。

用法::

    python3 scripts/run.py <input.json>

设计原则：
- 只做规范化、校验与分组，绝不写入源数据、绝不删除任何记录、绝不补全未知字段。
- 修正建议（邮箱域名纠错、公司别名）只写入 issues.suggested_value，
  永远不参与去重键，因此“建议修正”不是合并依据。
- 相同输入必然产生逐字节一致的输出（无随机数、无当前时间、无网络）。
- 仅依赖 Python 3.9 标准库（含 difflib）。
"""

import difflib
import json
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

# 凭据门禁：名单里出现疑似账号/密钥的字段名或值时必须拒绝处理且不回显。
FORBIDDEN_KEYS = frozenset([
    "password", "passwd", "passphrase", "secret_value", "api_token",
    "access_token", "client_secret", "credential_value", "private_key",
    "private_key_pem", "dsn", "connection_string", "jdbc_url", "database_url",
    "cookie", "session_token", "refresh_token",
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
    """递归扫描整份输入；命中即抛错，且异常信息不回显命中的字段名或字符串。"""
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


TEXT_FIELDS = ("record_id", "name", "company", "phone", "email", "city", "source", "updated_at")
SCAN_FIELDS = ("record_id", "name", "company", "phone", "email", "city", "source", "updated_at")
CONFLICT_FIELDS = ("name", "company", "phone", "email", "city")
MISSING_FIELD_TARGETS = ("name", "company", "city")
EXACT_KEY_KINDS = ("email", "phone")
STATUSES = ("VALID", "NEEDS_REVIEW", "INVALID", "DUPLICATE")

SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}

SEVERITY = {
    "MISSING_RECORD_ID": "HIGH",
    "DUPLICATE_RECORD_ID": "HIGH",
    "NO_CONTACT_KEY": "HIGH",
    "INVALID_PHONE": "HIGH",
    "INVALID_EMAIL": "HIGH",
    "NON_TEXT_FIELD": "HIGH",
    "EMAIL_DOMAIN_TYPO": "MEDIUM",
    "FUTURE_UPDATED_AT": "MEDIUM",
    "INJECTION_TEXT": "MEDIUM",
    "CONTROL_CHARS_REMOVED": "MEDIUM",
    "SUSPICIOUS_NAME": "LOW",
    "COMPANY_ALIAS": "INFO",
    "FULLWIDTH_NORMALIZED": "INFO",
    "WHITESPACE_NORMALIZED": "INFO",
    "MISSING_FIELD": "INFO",
}

INVALID_STATUS_CODES = ("MISSING_RECORD_ID", "DUPLICATE_RECORD_ID", "NON_TEXT_FIELD")

# 提示注入检测（只报告、不执行；命中即 NEEDS_REVIEW）
# “忽略指令”必须覆盖 SKILL.md 明确承诺的“忽略以上所有指令”，以及空格、标点、
# “的”等插入词变体（如“忽略 以上，所有。指令”）。允许的词只在
# {以上,之前,上面,上述,前面,所有,的} 内，因此普通业务文本
# （如“请忽略以上物流信息”“忽略这条消息，按内部指令处理”）不会被误报。
INJECTION_GAP = r"[\s，,。、；;：:！!？?…·\.\-—\"'“”‘’（）()【】\[\]]*"
INJECTION_PATTERNS = (
    ("忽略指令", re.compile(
        r"忽略" + INJECTION_GAP
        + r"(?:(?:以上|之前|上面|上述|前面|所有|的)+" + INJECTION_GAP + r")+"
        + r"(?:指令|规则|要求)")),
    ("ignore-previous-instructions", re.compile(r"ignore (all )?(previous|above) instructions", re.IGNORECASE)),
    ("删除所有", re.compile(r"删除所有")),
    ("delete-all", re.compile(r"delete all", re.IGNORECASE)),
    ("system-prompt", re.compile(r"system prompt", re.IGNORECASE)),
    ("你现在是", re.compile(r"你现在是")),
)

FULLWIDTH_MAP = {}
for _code in range(0xFF01, 0xFF5F):
    FULLWIDTH_MAP[_code] = _code - 0xFEE0
FULLWIDTH_MAP[0x3000] = 0x20  # 表意空格 -> 半角空格

WHITESPACE_CONTROL = "\t\n\r\v\f"
NON_DIGIT_SEPARATORS = re.compile(r"[\s\-()+.]+")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
DIGIT_RE = re.compile(r"\d")
ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.(\d{1,6}))?"
    r"(Z|z|[+-]\d{2}:?\d{2})$"
)

DISCLAIMER = (
    "本文件是离线生成的清洗工作清单，不是经过核实的客户名单。引擎全程只读：不会修改源数据、"
    "不会删除任何记录、不会自动合并任何重复项。issues 中的 suggested_value（邮箱域名纠错、公司别名）"
    "只是待人工确认的建议，引擎不会把它们当作重复判定依据——即“建议修正”不构成合并证据。"
    "输入中没有提供的字段一律保持未知，引擎不会推测、填充或补全任何缺失值。"
    "所有 DUPLICATE / WEAK 分组都只是候选，需由人工核对后再决定是否合并。"
)


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------


def parse_iso8601(value, label):
    """解析必须带时区偏移的 ISO8601 时间；失败时抛出中文 ValueError。"""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s 必须是非空的 ISO8601 文本，例如 2026-09-16T10:00:00+08:00" % label)
    match = ISO_RE.match(value.strip())
    if match is None:
        raise ValueError(
            "%s 必须是带时区偏移的 ISO8601 时间，例如 2026-09-16T10:00:00+08:00" % label
        )
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    hour, minute = int(match.group(4)), int(match.group(5))
    second = int(match.group(6) or 0)
    micro = int((match.group(7) or "0").ljust(6, "0"))
    zone = match.group(8)
    if zone in ("Z", "z"):
        offset = timedelta(0)
    else:
        sign = 1 if zone[0] == "+" else -1
        body = zone[1:].replace(":", "")
        offset = sign * timedelta(hours=int(body[:2]), minutes=int(body[2:]))
    try:
        return datetime(year, month, day, hour, minute, second, micro, tzinfo=timezone(offset))
    except ValueError as exc:
        raise ValueError("%s 不是合法时间：%s" % (label, value)) from exc


def normalize_text(value):
    """全角转半角 -> 控制字符处理 -> 空白折叠与去首尾。返回 (文本, 标志集合)。"""
    flags = set()
    converted = []
    for char in value:
        mapped = FULLWIDTH_MAP.get(ord(char))
        if mapped is None:
            converted.append(char)
        else:
            converted.append(chr(mapped))
            flags.add("fullwidth")
    text = "".join(converted)

    kept = []
    for char in text:
        category = unicodedata.category(char)
        if category in ("Cc", "Cf", "Cs", "Co"):
            if char in WHITESPACE_CONTROL:
                kept.append(" ")
            else:
                flags.add("control")
        else:
            kept.append(char)
    text = "".join(kept)

    collapsed = " ".join(text.split())
    if collapsed != text:
        flags.add("whitespace")
    return collapsed, flags


def normalized_field(raw, key):
    """返回 (文本或 None, 是否非文本, 标志集合)。空白文本按缺失处理。"""
    if key not in raw or raw[key] is None:
        return None, False, set()
    value = raw[key]
    if not isinstance(value, str):
        return None, True, set()
    text, flags = normalize_text(value)
    if not text:
        return None, False, flags
    return text, False, flags


def clean_phone_digits(text):
    """去掉空格与 - ( ) + . 分隔符；全为数字时返回数字串，否则返回 None。"""
    if text is None:
        return None
    core = NON_DIGIT_SEPARATORS.sub("", text)
    if core and core.isdigit():
        return core
    return None


def join_ids(values, limit=5):
    items = [str(v) for v in values]
    if len(items) <= limit:
        return "、".join(items)
    return "、".join(items[:limit]) + " 等%d条" % len(items)


def fmt_pct(value):
    return "%.2f" % value


# --------------------------------------------------------------------------
# 输入校验
# --------------------------------------------------------------------------


def load_input(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = handle.read()
    except FileNotFoundError:
        raise ValueError("输入文件不存在：%s" % path)
    except OSError as exc:
        raise ValueError("无法读取输入文件：%s" % exc)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("输入文件不是合法 JSON：第 %d 行 %s" % (exc.lineno, exc.msg))
    if not isinstance(data, dict):
        raise ValueError("输入 JSON 顶层必须是对象")
    return data


def read_normalization(data):
    config = data.get("normalization")
    if config is None:
        return None, None
    if not isinstance(config, dict):
        raise ValueError("normalization 必须是对象")
    region = config.get("default_region")
    if region is not None and region not in ("CN", "INTL"):
        raise ValueError("normalization.default_region 只能是 CN 或 INTL")
    phone_format = config.get("phone_format")
    if phone_format is not None and phone_format not in ("E164", "NATIONAL"):
        raise ValueError("normalization.phone_format 只能是 E164 或 NATIONAL")
    if phone_format is None and region == "CN":
        phone_format = "E164"
    return region, phone_format


def read_dedup(data):
    config = data.get("dedup")
    empty = ([], False, None, None)
    if config is None:
        return empty
    if not isinstance(config, dict):
        raise ValueError("dedup 必须是对象")

    raw_keys = config.get("exact_keys", [])
    if not isinstance(raw_keys, list):
        raise ValueError("dedup.exact_keys 必须是数组")
    exact_keys = []
    for item in raw_keys:
        if item not in EXACT_KEY_KINDS:
            raise ValueError("dedup.exact_keys 只支持 email 与 phone")
        if item not in exact_keys:
            exact_keys.append(item)

    fuzzy_name = config.get("fuzzy_name", False)
    if not isinstance(fuzzy_name, bool):
        raise ValueError("dedup.fuzzy_name 必须是布尔值")
    if not fuzzy_name:
        return (exact_keys, False, None, None)

    threshold_text = config.get("fuzzy_threshold")
    if threshold_text is None:
        raise ValueError('dedup.fuzzy_name 为 true 时必须提供 dedup.fuzzy_threshold（小数字符串，如 "0.86"）')
    if not isinstance(threshold_text, str):
        raise ValueError("dedup.fuzzy_threshold 必须是字符串形式的小数")
    try:
        threshold = float(threshold_text)
    except ValueError:
        raise ValueError("dedup.fuzzy_threshold 必须是 0 到 1 之间的小数")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("dedup.fuzzy_threshold 必须在 0 到 1 之间")

    blocking_field = config.get("blocking_field", "company")
    if blocking_field not in ("company", "city", None):
        raise ValueError("dedup.blocking_field 只能是 company、city 或 null")
    return (exact_keys, True, threshold_text, blocking_field)


def read_email_corrections(data):
    records = data.get("email_domain_corrections")
    mapping = {}
    if records is None:
        return mapping
    if not isinstance(records, list):
        raise ValueError("email_domain_corrections 必须是数组")
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("email_domain_corrections 的每一项都必须是对象")
        source, target = item.get("from"), item.get("to")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("email_domain_corrections.from 必须是非空文本")
        if not isinstance(target, str) or not target.strip():
            raise ValueError("email_domain_corrections.to 必须是非空文本")
        mapping[source.strip().lower()] = target.strip().lower()
    return mapping


def read_company_aliases(data):
    records = data.get("company_aliases")
    mapping = {}
    if records is None:
        return mapping
    if not isinstance(records, list):
        raise ValueError("company_aliases 必须是数组")
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("company_aliases 的每一项都必须是对象")
        alias, canonical = item.get("alias"), item.get("canonical")
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("company_aliases.alias 必须是非空文本")
        if not isinstance(canonical, str) or not canonical.strip():
            raise ValueError("company_aliases.canonical 必须是非空文本")
        mapping[alias.strip().lower()] = canonical.strip()
    return mapping


# --------------------------------------------------------------------------
# 单条记录处理
# --------------------------------------------------------------------------


def process_record(row_index, raw, ctx):
    issues = []
    non_text = False
    flags_by_field = {}

    def add(code, field, detail, suggested=None):
        issues.append(
            {
                "code": code,
                "field": field,
                "severity": SEVERITY[code],
                "detail": detail,
                "suggested_value": suggested,
            }
        )

    def field_norm(key):
        text, is_non_text, flags = normalized_field(raw, key)
        if flags:
            flags_by_field[key] = flags
        if is_non_text:
            add("NON_TEXT_FIELD", key, "字段值不是文本，已按缺失处理")
        return text, is_non_text

    # -- 记录编号 ---------------------------------------------------------
    record_id, rid_non_text = field_norm("record_id")
    if rid_non_text:
        non_text = True
    if record_id is None and not rid_non_text:
        add("MISSING_RECORD_ID", "record_id", "缺少记录编号或记录编号为空")

    # -- 文本字段 ---------------------------------------------------------
    name, name_non_text = field_norm("name")
    company, company_non_text = field_norm("company")
    phone_text, phone_non_text = field_norm("phone")
    email_text, email_non_text = field_norm("email")
    city, city_non_text = field_norm("city")
    field_norm("source")
    updated_text, updated_non_text = field_norm("updated_at")
    for broken in (name_non_text, company_non_text, phone_non_text, email_non_text, city_non_text, updated_non_text):
        if broken:
            non_text = True

    # -- 标签 -------------------------------------------------------------
    tags = []
    raw_tags = raw.get("tags")
    if raw_tags is None:
        tags = []
    elif not isinstance(raw_tags, list):
        add("NON_TEXT_FIELD", "tags", "tags 必须是文本数组")
        non_text = True
    else:
        for tag in raw_tags:
            if not isinstance(tag, str):
                add("NON_TEXT_FIELD", "tags", "tags 中存在非文本元素，已忽略")
                non_text = True
                continue
            tag_text, _tag_flags = normalize_text(tag)
            if tag_text:
                tags.append(tag_text)

    # -- 手机号 -----------------------------------------------------------
    region = ctx["region"]
    phone_format = ctx["phone_format"]
    phone = None
    phone_e164 = None
    phone_valid = False
    digits = clean_phone_digits(phone_text)
    if phone_text is not None:
        if digits is None:
            phone = phone_text
            add("INVALID_PHONE", "phone", "PHONE_CONTAINS_NON_DIGITS")
        elif region == "CN":
            national = digits
            if national.startswith("86") and len(national) == 13:
                national = national[2:]
            phone = national
            if CN_MOBILE_RE.match(national):
                phone_valid = True
                if phone_format == "E164":
                    phone_e164 = "+86" + national
            else:
                add("INVALID_PHONE", "phone", "NON_MOBILE_OR_MALFORMED")
        else:
            phone = digits
            if 6 <= len(digits) <= 15:
                phone_valid = True
                if phone_text.strip().startswith("+"):
                    phone_e164 = "+" + digits
            else:
                add("INVALID_PHONE", "phone", "NOT_6_TO_15_DIGITS")

    # -- 邮箱 -------------------------------------------------------------
    email = email_text.lower() if email_text is not None else None
    email_valid = False
    if email is not None:
        if EMAIL_RE.match(email):
            email_valid = True
        else:
            add("INVALID_EMAIL", "email", "EMAIL_FORMAT_INVALID")
        if "@" in email:
            domain = email.rsplit("@", 1)[1]
            corrected = ctx["email_corrections"].get(domain)
            if corrected is not None and corrected != domain:
                add(
                    "EMAIL_DOMAIN_TYPO",
                    "email",
                    "邮箱域名疑似笔误（建议值仅供参考，不作为合并依据）",
                    email.rsplit("@", 1)[0] + "@" + corrected,
                )

    # -- 公司别名 ---------------------------------------------------------
    canonical_company = company
    if company is not None:
        canonical = ctx["company_aliases"].get(company.lower())
        if canonical is not None:
            add("COMPANY_ALIAS", "company", "公司名称命中别名表（建议值仅供参考）", canonical)
            canonical_company = canonical

    # -- 姓名可疑 ---------------------------------------------------------
    if name is not None:
        has_digit = DIGIT_RE.search(name) is not None
        has_text = False
        for char in name:
            if unicodedata.category(char).startswith("L") or unicodedata.category(char) == "Nd":
                has_text = True
        if has_digit or not has_text:
            add("SUSPICIOUS_NAME", "name", "姓名包含数字或仅由符号组成")

    # -- 缺失字段 ---------------------------------------------------------
    values = {"name": name, "company": company, "city": city}
    for key in MISSING_FIELD_TARGETS:
        if values[key] is None:
            add("MISSING_FIELD", key, "字段缺失或为空，保持未知")

    # -- 联系方式 ---------------------------------------------------------
    if phone_text is None and email is None:
        add("NO_CONTACT_KEY", "contact_key", "手机号与邮箱均为空，无法作为联系人使用")

    # -- 更新时间 ---------------------------------------------------------
    updated_dt = None
    if updated_text is not None:
        try:
            updated_dt = parse_iso8601(updated_text, "records[%d].updated_at" % row_index)
        except ValueError:
            updated_dt = None
        if updated_dt is not None and updated_dt > ctx["as_of"]:
            add("FUTURE_UPDATED_AT", "updated_at", "更新时间晚于 as_of 基准时间")

    # -- 规范化痕迹 -------------------------------------------------------
    affected = {"fullwidth": [], "control": [], "whitespace": []}
    for key in TEXT_FIELDS:
        for flag in flags_by_field.get(key, ()):
            if key not in affected[flag]:
                affected[flag].append(key)
    if affected["fullwidth"]:
        add(
            "FULLWIDTH_NORMALIZED",
            affected["fullwidth"][0],
            "已做全角转半角：%s" % "、".join(affected["fullwidth"]),
        )
    if affected["control"]:
        add(
            "CONTROL_CHARS_REMOVED",
            affected["control"][0],
            "已移除控制字符：%s" % "、".join(affected["control"]),
        )
    if affected["whitespace"]:
        add(
            "WHITESPACE_NORMALIZED",
            affected["whitespace"][0],
            "已折叠/去首尾空白：%s" % "、".join(affected["whitespace"]),
        )

    # -- 提示注入 ---------------------------------------------------------
    injection_flags = []
    injection_fields = []
    for key in SCAN_FIELDS:
        value = raw.get(key)
        if not isinstance(value, str):
            continue
        for label, pattern in INJECTION_PATTERNS:
            if pattern.search(value):
                if label not in injection_flags:
                    injection_flags.append(label)
                if key not in injection_fields:
                    injection_fields.append(key)
    if raw_tags is not None and isinstance(raw_tags, list):
        for tag in raw_tags:
            if not isinstance(tag, str):
                continue
            for label, pattern in INJECTION_PATTERNS:
                if pattern.search(tag):
                    if label not in injection_flags:
                        injection_flags.append(label)
                    if "tags" not in injection_fields:
                        injection_fields.append("tags")
    if injection_flags:
        add(
            "INJECTION_TEXT",
            injection_fields[0],
            "字段文本疑似提示注入（命中 %d 类特征），引擎只记录不执行" % len(injection_flags),
        )

    # -- 完整度 -----------------------------------------------------------
    present = 0
    if name:
        present += 1
    if company:
        present += 1
    if phone_valid:
        present += 1
    if email_valid:
        present += 1
    completeness = present / 4.0 * 100.0

    issues.sort(key=lambda item: (SEVERITY_RANK[item["severity"]], item["code"], item["field"]))

    return {
        "record_id": record_id,
        "row_index": row_index,
        "status": "VALID",
        "normalized": {
            "name": name,
            "company": company,
            "phone": phone,
            "phone_e164": phone_e164,
            "email": email,
            "city": city,
            "tags": list(tags),
        },
        "completeness_pct": fmt_pct(completeness),
        "issues": issues,
        "issue_codes": sorted({item["code"] for item in issues}),
        "injection_flags": sorted(injection_flags),
        "in_duplicate_group": None,
        "_completeness": completeness,
        "_updated_dt": updated_dt,
        "_eligible": not ({"MISSING_RECORD_ID", "NON_TEXT_FIELD"} & {item["code"] for item in issues}),
        "_email_key": email,
        "_phone_key": phone_e164 if phone_e164 is not None else phone,
        "_company_canonical": (canonical_company or "").lower(),
        "_name": name or "",
        "_city": city or "",
    }


# --------------------------------------------------------------------------
# 去重分组
# --------------------------------------------------------------------------


class DisjointSet(object):
    """最小下标为根的并查集，保证顺序确定。"""

    def __init__(self, size):
        self._parent = list(range(size))

    def find(self, item):
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left, right):
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return False
        if right_root < left_root:
            left_root, right_root = right_root, left_root
        self._parent[right_root] = left_root
        return True

    def components(self, indices):
        buckets = {}
        for index in indices:
            buckets.setdefault(self.find(index), []).append(index)
        return [sorted(members) for members in buckets.values()]


def build_exact_groups(records, exact_keys):
    size = len(records)
    forest = DisjointSet(size)
    key_owners = {}
    for kind in exact_keys:
        buckets = {}
        for index, record in enumerate(records):
            if not record["_eligible"]:
                continue
            value = record["_email_key"] if kind == "email" else record["_phone_key"]
            if not value:
                continue
            buckets.setdefault(value, []).append(index)
        key_owners[kind] = buckets
        for value in sorted(buckets):
            members = buckets[value]
            for other in members[1:]:
                forest.union(members[0], other)

    groups = []
    for members in forest.components([i for i in range(size) if records[i]["_eligible"]]):
        if len(members) < 2:
            continue
        matched_on = []
        for kind in sorted(exact_keys):
            buckets = key_owners.get(kind, {})
            hit = False
            for value in sorted(buckets):
                if len([i for i in buckets[value] if i in members]) > 1:
                    hit = True
                    break
            if hit:
                matched_on.append(kind)
        groups.append(
            {
                "members": members,
                "confidence": "EXACT",
                "matched_on": matched_on,
                "blocking_field": None,
                "needs_confirmation": False,
            }
        )
    groups.sort(key=lambda group: (group["members"][0], group["members"]))
    return groups


def build_weak_groups(records, exact_groups, dedup):
    size = len(records)
    exact_members = set()
    for group in exact_groups:
        exact_members.update(group["members"])

    candidates = [i for i in range(size) if records[i]["_eligible"] and i not in exact_members]
    blocking_field = dedup["blocking_field"]
    blocks = {}
    for index in candidates:
        if blocking_field is None:
            block = "*"
        elif blocking_field == "company":
            block = records[index]["_company_canonical"]
        else:
            block = records[index]["_city"]
        if not block:
            continue
        blocks.setdefault(block, []).append(index)

    threshold = float(dedup["fuzzy_threshold"])
    forest = DisjointSet(size)
    for block in sorted(blocks):
        members = blocks[block]
        for left_pos in range(len(members)):
            for right_pos in range(left_pos + 1, len(members)):
                left, right = members[left_pos], members[right_pos]
                left_name, right_name = records[left]["_name"], records[right]["_name"]
                if not left_name or not right_name:
                    continue
                ratio = difflib.SequenceMatcher(None, left_name, right_name).ratio()
                if ratio >= threshold:
                    forest.union(left, right)

    groups = []
    for members in forest.components(candidates):
        if len(members) < 2:
            continue
        block = "*"
        if blocking_field == "company":
            block = records[members[0]]["_company_canonical"]
        elif blocking_field == "city":
            block = records[members[0]]["_city"]
        groups.append(
            {
                "members": members,
                "confidence": "WEAK",
                "matched_on": ["fuzzy_name"],
                "blocking_field": blocking_field,
                "needs_confirmation": True,
                "_block": block,
            }
        )
    groups.sort(key=lambda group: (group["members"][0], group["members"]))
    return groups


def keep_sort_key(record):
    stamp = record["_updated_dt"]
    if stamp is None:
        return (-record["_completeness"], 1, 0.0, record["row_index"])
    return (-record["_completeness"], 0, -stamp.timestamp(), record["row_index"])


def finalize_group(group, group_id, records, threshold_text):
    members = group["members"]
    first = records[members[0]]
    conflicts = {}
    for field in CONFLICT_FIELDS:
        values = sorted({records[i]["normalized"][field] for i in members if records[i]["normalized"][field]})
        conflicts[field] = values if len(values) > 1 else []

    ranked = sorted(members, key=lambda index: keep_sort_key(records[index]))
    keep = ranked[0]
    reason_ids = join_ids([records[i]["record_id"] for i in members], limit=8)

    if group["confidence"] == "EXACT":
        matched = "、".join("邮箱" if item == "email" else "手机号" for item in group["matched_on"])
        reason = "记录 %s 在%s上规范化后完全一致，判定为精确重复；建议人工核对后合并，本次建议保留 %s。" % (
            reason_ids,
            matched or "联系方式",
            records[keep]["record_id"],
        )
        needs_confirmation = False
        suggested_remove = [records[i]["record_id"] for i in members if i != keep]
    else:
        if group["blocking_field"] == "company":
            block_text = "公司「%s」" % first["_company_canonical"]
        elif group["blocking_field"] == "city":
            block_text = "城市「%s」" % first["_city"]
        else:
            block_text = "全部记录"
        reason = "记录 %s 在%s内姓名相似度达到 %s 阈值，属于疑似重复；需人工确认后才能判定，引擎不会自动合并。" % (
            reason_ids,
            block_text,
            threshold_text,
        )
        needs_confirmation = True
        suggested_remove = []

    return {
        "group_id": group_id,
        "confidence": group["confidence"],
        "matched_on": list(group["matched_on"]),
        "blocking_field": group["blocking_field"],
        "record_ids": [records[i]["record_id"] for i in members],
        "conflicts": conflicts,
        "suggested_keep": records[keep]["record_id"],
        "suggested_remove": suggested_remove,
        "needs_confirmation": needs_confirmation,
        "reason": reason,
    }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def build_result(data):
    scan_privacy(data)
    as_of_text = data.get("as_of")
    as_of = parse_iso8601(as_of_text, "as_of")
    region, phone_format = read_normalization(data)
    exact_keys, fuzzy_name, threshold_text, blocking_field = read_dedup(data)
    email_corrections = read_email_corrections(data)
    company_aliases = read_company_aliases(data)

    raw_records = data.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("records 必须是非空数组")
    for position, item in enumerate(raw_records):
        if not isinstance(item, dict):
            raise ValueError("records[%d] 必须是对象" % position)

    ctx = {
        "as_of": as_of,
        "region": region,
        "phone_format": phone_format,
        "email_corrections": email_corrections,
        "company_aliases": company_aliases,
    }

    records = []
    for position, raw in enumerate(raw_records):
        records.append(process_record(position + 1, raw, ctx))

    # 重复记录编号：保留首次出现，其余标记（确定性）
    seen = {}
    for record in records:
        record_id = record["record_id"]
        if not record_id:
            continue
        if record_id in seen:
            record["issues"].append(
                {
                    "code": "DUPLICATE_RECORD_ID",
                    "field": "record_id",
                    "severity": SEVERITY["DUPLICATE_RECORD_ID"],
                    "detail": "记录编号与第 %d 行重复，首次出现处保留该编号" % seen[record_id],
                    "suggested_value": None,
                }
            )
            record["issues"].sort(key=lambda item: (SEVERITY_RANK[item["severity"]], item["code"], item["field"]))
            record["issue_codes"] = sorted({item["code"] for item in record["issues"]})
        else:
            seen[record_id] = record["row_index"]

    # 精确分组
    exact_groups = build_exact_groups(records, exact_keys)

    # 弱分组
    dedup = {"fuzzy_name": fuzzy_name, "fuzzy_threshold": threshold_text, "blocking_field": blocking_field}
    weak_groups = build_weak_groups(records, exact_groups, dedup) if fuzzy_name else []

    # 编号与定稿
    duplicate_groups = []
    for raw_group in exact_groups + weak_groups:
        group_id = "G-%03d" % (len(duplicate_groups) + 1)
        final = finalize_group(raw_group, group_id, records, threshold_text)
        duplicate_groups.append(final)
        for index in raw_group["members"]:
            records[index]["in_duplicate_group"] = group_id

    # 状态判定
    exact_keep = {}
    weak_member = set()
    for raw_group, final in zip(exact_groups + weak_groups, duplicate_groups):
        if raw_group["confidence"] == "EXACT":
            for index in raw_group["members"]:
                exact_keep[index] = final["suggested_keep"] == records[index]["record_id"]
        else:
            weak_member.update(raw_group["members"])

    for index, record in enumerate(records):
        codes = {item["code"] for item in record["issues"]}
        severities = {item["severity"] for item in record["issues"]}
        if codes & set(INVALID_STATUS_CODES):
            status = "INVALID"
        elif index in exact_keep and not exact_keep[index]:
            status = "DUPLICATE"
        elif index in weak_member or (severities & {"HIGH", "MEDIUM"}):
            status = "NEEDS_REVIEW"
        else:
            status = "VALID"
        record["status"] = status

    # 统计
    status_counts = {name: 0 for name in STATUSES}
    for record in records:
        status_counts[record["status"]] += 1

    issue_counts = {}
    severity_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0}
    for record in records:
        for issue in record["issues"]:
            issue_counts[issue["code"]] = issue_counts.get(issue["code"], 0) + 1
            severity_counts[issue["severity"]] += 1

    record_ids = [record["record_id"] for record in records if record["record_id"]]
    unique_record_id_count = len(set(record_ids))
    duplicate_record_id_count = sum(1 for record in records if "DUPLICATE_RECORD_ID" in record["issue_codes"])

    if all(record["status"] == "INVALID" for record in records):
        status = "INVALID"
    elif duplicate_groups:
        status = "DUPLICATES_FOUND"
    elif status_counts["NEEDS_REVIEW"]:
        status = "NEEDS_REVIEW"
    else:
        status = "CLEAN"

    completeness_values = [record["_completeness"] for record in records]
    completeness_avg = sum(completeness_values) / len(completeness_values)

    missing_contact_key_count = sum(1 for record in records if "NO_CONTACT_KEY" in record["issue_codes"])
    suggested_removal_count = sum(len(group["suggested_remove"]) for group in duplicate_groups)

    def label(record):
        """用户可见的定位标签：没有记录编号时退回行号，避免出现 'None'。"""
        return record["record_id"] if record["record_id"] else "第%d行" % record["row_index"]

    def ids_of(code):
        return [label(record) for record in records if code in record["issue_codes"]]

    next_actions = []
    invalid_ids = [label(record) for record in records if record["status"] == "INVALID"]
    if invalid_ids:
        next_actions.append(
            {
                "action": "修复无效记录：补齐唯一记录编号，或把非文本字段改为文本后重新导出",
                "priority": "HIGH",
                "target": join_ids(invalid_ids),
            }
        )
    if missing_contact_key_count:
        next_actions.append(
            {
                "action": "为缺少手机号与邮箱的记录补充联系方式，否则无法用于触达",
                "priority": "HIGH",
                "target": join_ids(ids_of("NO_CONTACT_KEY")),
            }
        )
    if issue_counts.get("INVALID_PHONE"):
        next_actions.append(
            {
                "action": "按 normalization 规则核对手机号（CN 需为 11 位大陆手机号），无效值保持未知不猜测",
                "priority": "HIGH",
                "target": "INVALID_PHONE ×%d：%s" % (issue_counts["INVALID_PHONE"], join_ids(ids_of("INVALID_PHONE"))),
            }
        )
    if issue_counts.get("INVALID_EMAIL"):
        next_actions.append(
            {
                "action": "核对邮箱格式，人工确认后再入库",
                "priority": "HIGH",
                "target": "INVALID_EMAIL ×%d：%s" % (issue_counts["INVALID_EMAIL"], join_ids(ids_of("INVALID_EMAIL"))),
            }
        )
    if exact_groups:
        next_actions.append(
            {
                "action": "人工确认精确重复组并按需合并（引擎不会自动删除任何记录）",
                "priority": "HIGH",
                "target": "、".join(group["group_id"] for group in duplicate_groups if group["confidence"] == "EXACT"),
            }
        )
    if issue_counts.get("EMAIL_DOMAIN_TYPO"):
        next_actions.append(
            {
                "action": "确认邮箱域名疑似笔误（建议值仅供参考，不作为合并依据）",
                "priority": "MEDIUM",
                "target": "EMAIL_DOMAIN_TYPO ×%d：%s"
                % (issue_counts["EMAIL_DOMAIN_TYPO"], join_ids(ids_of("EMAIL_DOMAIN_TYPO"))),
            }
        )
    if issue_counts.get("FUTURE_UPDATED_AT"):
        next_actions.append(
            {
                "action": "核对更新时间，修正晚于 as_of 的记录",
                "priority": "MEDIUM",
                "target": join_ids(ids_of("FUTURE_UPDATED_AT")),
            }
        )
    if issue_counts.get("INJECTION_TEXT"):
        next_actions.append(
            {
                "action": "人工隔离并核对疑似提示注入文本，引擎只读且不会执行其中内容",
                "priority": "MEDIUM",
                "target": join_ids(ids_of("INJECTION_TEXT")),
            }
        )
    if weak_groups:
        next_actions.append(
            {
                "action": "人工确认姓名相似的疑似重复组，必要时补充唯一标识后再判定",
                "priority": "MEDIUM",
                "target": "、".join(group["group_id"] for group in duplicate_groups if group["confidence"] == "WEAK"),
            }
        )
    if status_counts["NEEDS_REVIEW"]:
        next_actions.append(
            {
                "action": "复核其余待复核记录并记录处理结论",
                "priority": "LOW",
                "target": "NEEDS_REVIEW ×%d" % status_counts["NEEDS_REVIEW"],
            }
        )
    if not next_actions:
        next_actions.append(
            {"action": "无需处理，当前名单未发现结构性问题", "priority": "LOW", "target": "全量记录"}
        )

    markdown_summary = build_markdown(
        as_of_text=as_of_text,
        records=records,
        status_counts=status_counts,
        issue_counts=issue_counts,
        duplicate_groups=duplicate_groups,
        completeness_avg=completeness_avg,
        duplicate_record_id_count=duplicate_record_id_count,
        unique_record_id_count=unique_record_id_count,
    )

    public_records = []
    for record in records:
        public_records.append(
            {
                "record_id": record["record_id"],
                "row_index": record["row_index"],
                "status": record["status"],
                "normalized": record["normalized"],
                "completeness_pct": record["completeness_pct"],
                "issues": record["issues"],
                "issue_codes": record["issue_codes"],
                "injection_flags": record["injection_flags"],
                "in_duplicate_group": record["in_duplicate_group"],
            }
        )

    result = {
        "status": status,
        "as_of": as_of_text,
        "record_count": len(records),
        "unique_record_id_count": unique_record_id_count,
        "duplicate_record_id_count": duplicate_record_id_count,
        "status_counts": {name: status_counts[name] for name in STATUSES},
        "issue_counts": {code: issue_counts[code] for code in sorted(issue_counts)},
        "severity_counts": severity_counts,
        "records": public_records,
        "duplicate_groups": duplicate_groups,
        "duplicate_group_count": len(duplicate_groups),
        "exact_group_count": sum(1 for group in duplicate_groups if group["confidence"] == "EXACT"),
        "weak_group_count": sum(1 for group in duplicate_groups if group["confidence"] == "WEAK"),
        "suggested_removal_count": suggested_removal_count,
        "summary": {
            "valid_records": status_counts["VALID"],
            "needs_review_records": status_counts["NEEDS_REVIEW"],
            "invalid_records": status_counts["INVALID"],
            "duplicate_records": status_counts["DUPLICATE"],
            "missing_contact_key_count": missing_contact_key_count,
            "completeness_avg_pct": fmt_pct(completeness_avg),
            "exact_groups": sum(1 for group in duplicate_groups if group["confidence"] == "EXACT"),
            "weak_groups": sum(1 for group in duplicate_groups if group["confidence"] == "WEAK"),
        },
        "normalization_applied": {
            "default_region": region,
            "phone_format": phone_format,
            "fullwidth_conversion": True,
        },
        "dedup_applied": {
            "exact_keys": list(exact_keys),
            "fuzzy_name": fuzzy_name,
            "fuzzy_threshold": threshold_text,
            "blocking_field": blocking_field if fuzzy_name else None,
        },
        "next_actions": next_actions,
        "markdown_summary": markdown_summary,
        "disclaimer": DISCLAIMER,
    }
    return result


def build_markdown(
    as_of_text,
    records,
    status_counts,
    issue_counts,
    duplicate_groups,
    completeness_avg,
    duplicate_record_id_count,
    unique_record_id_count,
):
    lines = []
    lines.append("## 客户名单清洗结果（只读工作清单）")
    lines.append("")
    lines.append("- 数据基准时间（as_of）：%s" % as_of_text)
    lines.append("- 记录总数：%d 条" % len(records))
    lines.append("- 唯一记录编号：%d 个；编号重复的记录：%d 条" % (unique_record_id_count, duplicate_record_id_count))
    lines.append(
        "- 状态分布：有效 %d / 待复核 %d / 无效 %d / 重复 %d"
        % (
            status_counts["VALID"],
            status_counts["NEEDS_REVIEW"],
            status_counts["INVALID"],
            status_counts["DUPLICATE"],
        )
    )
    lines.append("- 平均完整度：%s%%（完整度 = 姓名/公司/有效手机号/有效邮箱 四项齐全度）" % fmt_pct(completeness_avg))
    lines.append("")
    lines.append("### 问题分布")
    lines.append("")
    if issue_counts:
        lines.append("| 问题代码 | 严重度 | 次数 |")
        lines.append("| --- | --- | --- |")
        for code in sorted(issue_counts):
            lines.append("| %s | %s | %d |" % (code, SEVERITY[code], issue_counts[code]))
    else:
        lines.append("本次未发现任何问题。")
    lines.append("")
    lines.append("### 重复候选组（共 %d 组）" % len(duplicate_groups))
    lines.append("")
    if duplicate_groups:
        for group in duplicate_groups:
            lines.append(
                "- %s（%s）：%s；建议保留 %s%s"
                % (
                    group["group_id"],
                    "精确匹配" if group["confidence"] == "EXACT" else "姓名相似",
                    "、".join(str(item) for item in group["record_ids"]),
                    group["suggested_keep"],
                    "；需人工确认" if group["needs_confirmation"] else "；其余 %d 条建议人工确认后合并" % len(group["suggested_remove"]),
                )
            )
    else:
        lines.append("- 未发现重复组。")
    lines.append("")
    lines.append("### 阅读顺序")
    lines.append("")
    lines.append("1. 先看 status 与 status_counts，判断本次名单整体质量。")
    lines.append("2. 再看 records 中 status=INVALID 的记录，修复编号或字段类型后重新导出。")
    lines.append("3. 然后看 duplicate_groups，逐组人工核对 conflicts 后再决定是否合并。")
    lines.append("4. 最后看 NEEDS_REVIEW 记录的 issues，按严重度从高到低处理。")
    lines.append("")
    lines.append("### 边界说明")
    lines.append("")
    lines.append("- 本清单由离线引擎生成，引擎只读：不修改源数据、不删除记录、不自动合并。")
    lines.append("- issues 中的 suggested_value 只是建议，绝不参与去重键，因此不是合并证据。")
    lines.append("- 缺失值一律保持未知，引擎不会推测或补全。")
    return "\n".join(lines)


# 与本项目既有多数 Skill 一致的公开入口名，便于审查脚本与测试统一调用。
analyze = build_result


def main(argv):
    if len(argv) != 1:
        raise ValueError("用法：python3 scripts/run.py <input.json>")
    data = load_input(argv[0])
    result = build_result(data)
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
