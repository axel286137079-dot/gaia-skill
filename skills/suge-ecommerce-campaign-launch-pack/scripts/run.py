#!/usr/bin/env python3
"""电商活动上线准备包 — offline e-commerce campaign launch readiness pack.

Pure Python 3.9+ standard library. Reads exactly one local JSON file, writes one
JSON document to stdout. No network, no filesystem writes, no image reading,
no command execution, no shop login.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = "1.0.0"

CHANNEL_ASSET_KINDS = ("main_image", "detail_page", "banner")
PROMO_TYPES = ("threshold_discount", "percent_discount", "coupon", "gift", "bundle")

BLOCKER = "BLOCKER"
REVIEW = "REVIEW"

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
# A due time is only usable when it carries an explicit offset. A local wall-clock
# string is kept as "unknown timezone", never silently assumed to be the campaign tz.
NAIVE_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")
# Asset file references must be bare file names: no separator, no parent segment,
# no scheme, no drive letter. Anything else is refused and never resolved.
UNSAFE_REF_RE = re.compile(r"[/\\]|\.\.|^[A-Za-z][A-Za-z0-9+.-]*:")

DISCLAIMER = (
    "本输出只依据你提交的活动资料做一致性核查与阻塞提示，不是上线效果、销量、利润或"
    "投放回报的预测或保证；缺失的库存、价格、素材与负责人一律保持未知，未跨币种合计，"
    "也未登录任何店铺或后台，未改价、未创建活动、未发送任何消息。最终活动规则以上线前"
    "人工确认为准。"
)

CODES = (
    "INVALID_CAMPAIGN_WINDOW", "PRICE_CONFLICT", "STOCK_CONFLICT",
    "CROSS_CHANNEL_PRICE_DIFF",
    "PROMOTION_CONFLICT", "PROMO_UNKNOWN_CHANNEL", "PROMO_UNKNOWN_PRODUCT",
    "PROMO_WINDOW_OUTSIDE_CAMPAIGN",
    "MISSING_STOCK", "INVALID_STOCK", "MISSING_PRICE", "INVALID_PRICE",
    "STOCK_BELOW_MIN",
    "MISSING_OWNER", "DEADLINE_AFTER_LAUNCH", "DUE_AT_TIMEZONE_UNKNOWN",
    "DONE_UNKNOWN",
    "MISSING_ASSET", "ASSET_NOT_APPROVED", "ASSET_APPROVAL_UNKNOWN",
    "ASSET_OVERDUE", "INVALID_ASSET_REF",
)

CHANNEL_STATES = ("BLOCKED", "AT_RISK", "READY")
SECTIONS = ("channels", "products", "promotions", "assets", "prep_items")


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    """Collapse control characters and whitespace. Chinese punctuation is preserved."""
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


def safe_basename(text):
    """Last path segment only, with the scheme or drive prefix stripped.

    A refused reference is never echoed back as a path: only this sanitised file
    name is ever recorded, so no directory structure leaves the process.
    """
    if not has_text(text):
        return None
    probe = re.split(r"[\\/]", clean_text(text))[-1]
    probe = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*:", "", probe)
    probe = probe.replace("..", "").strip()
    if not probe or len(probe) > 120:
        return None
    return probe


def injection_hit(value):
    """Action word and target word must co-occur inside one sentence."""
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
    """Fixed two-decimal money/number string."""
    return str(quant(value, 2)) if value is not None else None


def count_num(value):
    """Trimmed numeric string for quantities, so 420 prints as 420 not 420.00."""
    if value is None:
        return None
    text = format(quant(value, 3), "f")
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
    """Returns (Decimal|None, is_present, is_valid). Distinguishes absent / invalid / value."""
    if value is None:
        return None, False, True
    parsed = dec(value)
    if parsed is None:
        return None, True, False
    return parsed, True, True


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
        "campaign": None,
        "launch_readiness": {"state": "REJECTED", "blocker_count": 0, "review_count": 0,
                             "channel_status_counts": {s: 0 for s in CHANNEL_STATES},
                             "unknown_counts": {}, "channel_ready_count": 0,
                             "channel_total": 0},
        "channels": [], "products": [], "promotions": [], "assets": [], "prep_items": [],
        "price_conflicts": [], "stock_conflicts": [], "promotion_conflicts": [],
        "stock_by_unit": {}, "price_range_by_currency": {},
        "asset_gaps": {}, "owner_todos": [],
        "findings": [], "finding_counts": {code: 0 for code in CODES},
        "blocker_count": 0, "review_count": 0,
        "pre_send_checklist": [], "clarification_questions": [],
        "markdown_summary": "# 电商活动上线作战单\n\n- 状态：**REJECTED**\n- 已拒绝处理，未回显疑似凭据内容。\n",
        "injection_flagged": [], "input_warnings": ["CREDENTIAL_DETECTED"],
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
    findings = []

    def add(code, severity, channel_id, subject, detail):
        findings.append({
            "code": code, "severity": severity,
            "channel_id": channel_id, "subject": subject, "detail": detail,
        })

    as_of_raw = data.get("as_of")
    as_of = None
    if has_text(as_of_raw):
        text = clean_text(as_of_raw)
        if DT_RE.match(text):
            as_of = _parse_dt(text)
        else:
            warnings.append("AS_OF_TIMEZONE_MISSING_OR_INVALID")
    else:
        warnings.append("AS_OF_MISSING")

    campaign_raw = data.get("campaign") if isinstance(data.get("campaign"), dict) else {}
    campaign_id = clean_text(campaign_raw.get("campaign_id")) if has_text(campaign_raw.get("campaign_id")) else None
    campaign_name = clean_text(campaign_raw.get("name")) if has_text(campaign_raw.get("name")) else None
    tz_name = clean_text(campaign_raw.get("timezone")) if has_text(campaign_raw.get("timezone")) else None

    starts = _parse_dt(clean_text(campaign_raw.get("starts_at"))) if has_text(campaign_raw.get("starts_at")) and DT_RE.match(clean_text(campaign_raw.get("starts_at"))) else None
    ends = _parse_dt(clean_text(campaign_raw.get("ends_at"))) if has_text(campaign_raw.get("ends_at")) and DT_RE.match(clean_text(campaign_raw.get("ends_at"))) else None
    if campaign_raw.get("starts_at") is not None and starts is None:
        warnings.append("CAMPAIGN_START_TIMEZONE_MISSING_OR_INVALID")
    if campaign_raw.get("ends_at") is not None and ends is None:
        warnings.append("CAMPAIGN_END_TIMEZONE_MISSING_OR_INVALID")

    window_state = "UNKNOWN"
    duration_hours = None
    campaign_days = None
    if starts is not None and ends is not None:
        if ends <= starts:
            window_state = "INVALID"
            add("INVALID_CAMPAIGN_WINDOW", BLOCKER, None, "campaign",
                "活动结束时间不晚于开始时间，活动窗口无法确定。")
        else:
            window_state = "VALID"
            delta = ends - starts
            duration_hours = amount(Decimal(delta.total_seconds()) / Decimal(3600))
            campaign_days = int((ends.date() - starts.date()).days) + 1

    # ---- channels ----
    channels_raw = data.get("channels") if isinstance(data.get("channels"), list) else []
    channels = []
    channel_ids = []
    seen_channel = {}
    for index, entry in enumerate(channels_raw):
        if not isinstance(entry, dict):
            continue
        channel_id = clean_text(entry.get("channel_id")) if has_text(entry.get("channel_id")) else None
        owner = clean_text(entry.get("owner")) if has_text(entry.get("owner")) else None
        deadline_text = clean_text(entry.get("launch_deadline")) if has_text(entry.get("launch_deadline")) else None
        deadline = _parse_dt(deadline_text) if deadline_text and DT_RE.match(deadline_text) else None
        flags = []
        if channel_id is None:
            flags.append("MISSING_CHANNEL_ID")
        elif channel_id in seen_channel:
            flags.append("DUPLICATE_CHANNEL_ID")
        else:
            seen_channel[channel_id] = index
        if owner is None:
            flags.append("OWNER_MISSING")
            add("MISSING_OWNER", BLOCKER, channel_id, "channel:" + str(channel_id),
                "渠道没有指定负责人，上线当天无人可对账。")
        if deadline_text is None:
            flags.append("LAUNCH_DEADLINE_MISSING")
        elif deadline is None:
            flags.append("LAUNCH_DEADLINE_TIMEZONE_UNKNOWN")
            add("DUE_AT_TIMEZONE_UNKNOWN", REVIEW, channel_id, "channel:" + str(channel_id),
                "渠道上线截止时间没有时区偏移，按“时间未知”处理，不按活动时区猜测。")
        kinds_raw = entry.get("required_asset_kinds") if isinstance(entry.get("required_asset_kinds"), list) else []
        kinds = []
        for kind in kinds_raw:
            text = clean_text(kind).lower() if isinstance(kind, str) else ""
            if text and text not in kinds:
                kinds.append(text)
        channels.append({
            "channel_id": channel_id, "name": clean_text(entry.get("name")) if has_text(entry.get("name")) else "",
            "owner": owner, "launch_deadline": deadline_text, "launch_deadline_ts": deadline,
            "required_asset_kinds": kinds, "review_flags": sorted(set(flags)), "index": index,
        })
        if channel_id is not None:
            channel_ids.append(channel_id)

    for channel in channels:
        for kind in channel["required_asset_kinds"]:
            if kind not in CHANNEL_ASSET_KINDS:
                channel["review_flags"] = sorted(set(channel["review_flags"] + ["UNKNOWN_REQUIRED_ASSET_KIND"]))

    # ---- products ----
    products_raw = data.get("products") if isinstance(data.get("products"), list) else []
    products = []
    product_groups = {}
    for index, entry in enumerate(products_raw):
        if not isinstance(entry, dict):
            continue
        product_id = clean_text(entry.get("product_id")) if has_text(entry.get("product_id")) else None
        channel_id = clean_text(entry.get("channel_id")) if has_text(entry.get("channel_id")) else None
        stock_raw = entry.get("stock") if isinstance(entry.get("stock"), dict) else {}
        stock_value, stock_present, stock_valid = as_amount(stock_raw.get("value"))
        stock_unit = clean_text(stock_raw.get("unit")) if has_text(stock_raw.get("unit")) else None
        price_raw = entry.get("price") if isinstance(entry.get("price"), dict) else {}
        price_value, price_present, price_valid = as_amount(price_raw.get("value"))
        price_currency = clean_text(price_raw.get("currency")).upper() if has_text(price_raw.get("currency")) else None
        min_stock, min_present, min_valid = as_amount(entry.get("min_stock"))

        flags = []
        if product_id is None:
            flags.append("MISSING_PRODUCT_ID")
        if channel_id is None:
            flags.append("MISSING_CHANNEL_ID")
        elif channel_id not in channel_ids:
            flags.append("UNKNOWN_CHANNEL")

        stock_state = "UNKNOWN"
        if not stock_present:
            flags.append("STOCK_UNKNOWN")
            add("MISSING_STOCK", BLOCKER, channel_id, "product:%s" % product_id,
                "库存数量未提供，保持未知，不按 0 计算。")
        elif not stock_valid:
            stock_state = "INVALID"
            flags.append("STOCK_INVALID")
            add("INVALID_STOCK", BLOCKER, channel_id, "product:%s" % product_id,
                "库存不是有效数字，已排除在合计之外。")
        elif stock_value < 0:
            stock_state = "INVALID"
            flags.append("STOCK_NEGATIVE")
            add("INVALID_STOCK", BLOCKER, channel_id, "product:%s" % product_id,
                "库存为负数，已排除在合计之外。")
        else:
            stock_state = "KNOWN"

        price_state = "UNKNOWN"
        if not price_present:
            flags.append("PRICE_UNKNOWN")
            add("MISSING_PRICE", BLOCKER, channel_id, "product:%s" % product_id,
                "价格未提供，保持未知，不用其他渠道价格代替。")
        elif not price_valid:
            price_state = "INVALID"
            flags.append("PRICE_INVALID")
            add("INVALID_PRICE", BLOCKER, channel_id, "product:%s" % product_id,
                "价格不是有效数字，已排除在区间统计之外。")
        elif price_value < 0:
            price_state = "INVALID"
            flags.append("PRICE_NEGATIVE")
            add("INVALID_PRICE", BLOCKER, channel_id, "product:%s" % product_id,
                "价格为负数，已排除在区间统计之外。")
        elif price_currency is None:
            flags.append("PRICE_CURRENCY_UNKNOWN")
        else:
            price_state = "KNOWN"

        record = {
            "product_id": product_id, "name": clean_text(entry.get("name")) if has_text(entry.get("name")) else "",
            "channel_id": channel_id, "stock_state": stock_state, "stock_value": stock_value,
            "stock_unit": stock_unit, "price_state": price_state, "price_value": price_value,
            "price_currency": price_currency, "min_stock": min_stock,
            "review_flags": sorted(set(flags)), "index": index,
        }
        products.append(record)
        product_groups.setdefault((product_id, channel_id), []).append(record)

    price_conflicts = []
    stock_conflicts = []
    for key in sorted(product_groups, key=lambda k: (str(k[0]), str(k[1]))):
        group = product_groups[key]
        if len(group) < 2:
            continue
        price_keys = []
        for record in group:
            if record["price_state"] == "KNOWN":
                price_keys.append((str(quant(record["price_value"], 2)), record["price_currency"]))
            else:
                price_keys.append((record["price_state"], record["price_currency"]))
        if len(set(price_keys)) > 1:
            entry = {
                "product_id": key[0], "channel_id": key[1], "occurrences": len(group),
                "prices": sorted({"%s %s" % (k[1] or "?", k[0]) for k in price_keys}),
                "paths": sorted("products[%d]" % r["index"] for r in group),
            }
            price_conflicts.append(entry)
            add("PRICE_CONFLICT", BLOCKER, key[1], "product:%s" % key[0],
                "同一商品在同一渠道出现互相矛盾的价格，不替你猜最终价：%s。" % "、".join(entry["prices"]))
        stock_keys = []
        for record in group:
            if record["stock_state"] == "KNOWN":
                stock_keys.append("%s %s" % (count_num(record["stock_value"]), record["stock_unit"] or "?"))
            else:
                stock_keys.append(record["stock_state"])
        if len(set(stock_keys)) > 1:
            entry = {
                "product_id": key[0], "channel_id": key[1], "occurrences": len(group),
                "stock_values": sorted(set(stock_keys)),
                "paths": sorted("products[%d]" % r["index"] for r in group),
            }
            stock_conflicts.append(entry)
            add("STOCK_CONFLICT", BLOCKER, key[1], "product:%s" % key[0],
                "同一商品在同一渠道出现互相矛盾的库存记录：%s。" % "、".join(entry["stock_values"]))

    # cross-channel price differences are recorded, never treated as an error
    by_product = {}
    for record in products:
        if record["product_id"] and record["price_state"] == "KNOWN" and record["price_currency"]:
            by_product.setdefault((record["product_id"], record["price_currency"]), {})[record["channel_id"]] = record
    for key in sorted(by_product, key=lambda k: (str(k[0]), str(k[1]))):
        per_channel = by_product[key]
        values = sorted({str(quant(r["price_value"], 2)) for r in per_channel.values()})
        if len(values) > 1:
            add("CROSS_CHANNEL_PRICE_DIFF", REVIEW, ",".join(sorted(str(c) for c in per_channel)),
                "product:%s" % key[0],
                "同一商品在不同渠道价格不同（%s %s），这是经营事实而不是错误，请人工确认是否为有意定价。"
                % (key[1], "／".join(values)))

    # declared minimums only: the tool never invents a business threshold
    for record in products:
        if record["stock_state"] == "KNOWN" and record["min_stock"] is not None and record["stock_value"] < record["min_stock"]:
            add("STOCK_BELOW_MIN", REVIEW, record["channel_id"], "product:%s" % record["product_id"],
                "库存 %s %s 低于你自行声明的最低库存 %s，请确认是否需要下架或补货。"
                % (count_num(record["stock_value"]), record["stock_unit"] or "", count_num(record["min_stock"])))

    stock_by_unit = {}
    for record in products:
        unit = record["stock_unit"] or "未标注单位"
        bucket = stock_by_unit.setdefault(unit, {"known_total": Decimal("0"), "known_count": 0,
                                                 "unknown_count": 0, "invalid_count": 0})
        if record["stock_state"] == "KNOWN":
            bucket["known_total"] += record["stock_value"]
            bucket["known_count"] += 1
        elif record["stock_state"] == "UNKNOWN":
            bucket["unknown_count"] += 1
        else:
            bucket["invalid_count"] += 1
    stock_view = {}
    for unit in sorted(stock_by_unit):
        bucket = stock_by_unit[unit]
        stock_view[unit] = {
            "known_total": count_num(bucket["known_total"]) if bucket["known_count"] else None,
            "known_count": bucket["known_count"],
            "unknown_count": bucket["unknown_count"],
            "invalid_count": bucket["invalid_count"],
        }

    price_range_by_currency = {}
    for record in products:
        if record["price_state"] != "KNOWN" or not record["price_currency"]:
            continue
        bucket = price_range_by_currency.setdefault(
            record["price_currency"], {"min": None, "max": None, "count": 0})
        bucket["count"] += 1
        bucket["min"] = record["price_value"] if bucket["min"] is None else min(bucket["min"], record["price_value"])
        bucket["max"] = record["price_value"] if bucket["max"] is None else max(bucket["max"], record["price_value"])
    price_view = {}
    for currency in sorted(price_range_by_currency):
        bucket = price_range_by_currency[currency]
        price_view[currency] = {"min": amount(bucket["min"]), "max": amount(bucket["max"]),
                                "count": bucket["count"]}

    # ---- promotions ----
    promos_raw = data.get("promotions") if isinstance(data.get("promotions"), list) else []
    promotions = []
    for index, entry in enumerate(promos_raw):
        if not isinstance(entry, dict):
            continue
        promo_id = clean_text(entry.get("promo_id")) if has_text(entry.get("promo_id")) else None
        channel_id = clean_text(entry.get("channel_id")) if has_text(entry.get("channel_id")) else None
        promo_type = clean_text(entry.get("type")).lower() if has_text(entry.get("type")) else None
        window_raw = entry.get("window") if isinstance(entry.get("window"), dict) else {}
        w_start = _parse_dt(clean_text(window_raw.get("starts_at"))) if has_text(window_raw.get("starts_at")) and DT_RE.match(clean_text(window_raw.get("starts_at"))) else None
        w_end = _parse_dt(clean_text(window_raw.get("ends_at"))) if has_text(window_raw.get("ends_at")) and DT_RE.match(clean_text(window_raw.get("ends_at"))) else None
        value, value_present, value_valid = as_amount(entry.get("value"))
        product_ids = []
        if isinstance(entry.get("product_ids"), list):
            for raw in entry["product_ids"]:
                text = clean_text(raw) if has_text(raw) else ""
                if text and text not in product_ids:
                    product_ids.append(text)

        flags = []
        if promo_id is None:
            flags.append("MISSING_PROMO_ID")
        if channel_id is None:
            flags.append("MISSING_CHANNEL_ID")
        elif channel_id not in channel_ids:
            flags.append("UNKNOWN_CHANNEL")
            add("PROMO_UNKNOWN_CHANNEL", BLOCKER, channel_id, "promotion:%s" % promo_id,
                "优惠指向的渠道不在活动渠道列表中，无法确定适用范围。")
        if promo_type is None:
            flags.append("MISSING_PROMO_TYPE")
        elif promo_type not in PROMO_TYPES:
            flags.append("UNKNOWN_PROMO_TYPE")
        if not value_present or not value_valid:
            flags.append("PROMO_VALUE_UNKNOWN")
        known_products = {r["product_id"] for r in products if r["product_id"]}
        for pid in product_ids:
            if pid not in known_products:
                flags.append("UNKNOWN_PRODUCT")
                add("PROMO_UNKNOWN_PRODUCT", BLOCKER, channel_id, "promotion:%s" % promo_id,
                    "优惠指向的商品 %s 不在活动商品列表中。" % pid)
        if w_start is None or w_end is None:
            flags.append("PROMO_WINDOW_UNKNOWN")
        else:
            if starts is not None and ends is not None:
                if w_start < starts or w_end > ends:
                    flags.append("WINDOW_OUTSIDE_CAMPAIGN")
                    add("PROMO_WINDOW_OUTSIDE_CAMPAIGN", REVIEW, channel_id, "promotion:%s" % promo_id,
                        "优惠生效区间超出活动窗口，请确认是否是有意的预热或返场。")
        promotions.append({
            "promo_id": promo_id, "name": clean_text(entry.get("name")) if has_text(entry.get("name")) else "",
            "channel_id": channel_id, "type": promo_type, "product_ids": product_ids,
            "value": amount(value) if value is not None else None,
            "window_starts_at": clean_text(window_raw.get("starts_at")) if has_text(window_raw.get("starts_at")) else None,
            "window_ends_at": clean_text(window_raw.get("ends_at")) if has_text(window_raw.get("ends_at")) else None,
            "window_start_ts": w_start, "window_end_ts": w_end,
            "review_flags": sorted(set(flags)), "index": index,
        })

    promotion_conflicts = []
    for i in range(len(promotions)):
        for j in range(i + 1, len(promotions)):
            left, right = promotions[i], promotions[j]
            if not left["promo_id"] or not right["promo_id"]:
                continue
            if left["channel_id"] != right["channel_id"] or left["type"] != right["type"]:
                continue
            shared = sorted(set(left["product_ids"]) & set(right["product_ids"]))
            if not shared:
                continue
            if left["window_start_ts"] is None or right["window_start_ts"] is None:
                continue
            if left["window_start_ts"] > right["window_end_ts"] or right["window_start_ts"] > left["window_end_ts"]:
                continue
            if left["value"] is None or right["value"] is None or left["value"] == right["value"]:
                continue
            pair = sorted([left["promo_id"], right["promo_id"]])
            if any(set(pair) == set(c["promos"]) and c["product_ids"] == shared for c in promotion_conflicts):
                continue
            promotion_conflicts.append({
                "promos": pair, "channel_id": left["channel_id"], "type": left["type"],
                "product_ids": shared,
                "values": sorted([left["value"], right["value"]]),
            })
            add("PROMOTION_CONFLICT", BLOCKER, left["channel_id"], "promotion:%s" % pair[0],
                "同一渠道、同一类型、同一商品上存在两条生效区间重叠但优惠力度不同的规则（%s vs %s），"
                "不替你猜最终规则，必须先由人工统一。" % (pair[0], pair[1]))

    # ---- assets ----
    assets_raw = data.get("assets") if isinstance(data.get("assets"), list) else []
    assets = []
    asset_gaps = {"overdue": [], "not_approved": [], "approval_unknown": [],
                  "invalid_refs": [], "timezone_unknown": [], "missing_by_channel": []}
    for index, entry in enumerate(assets_raw):
        if not isinstance(entry, dict):
            continue
        asset_id = clean_text(entry.get("asset_id")) if has_text(entry.get("asset_id")) else None
        channel_id = clean_text(entry.get("channel_id")) if has_text(entry.get("channel_id")) else None
        kind = clean_text(entry.get("kind")).lower() if has_text(entry.get("kind")) else None
        filename = clean_text(entry.get("filename")) if has_text(entry.get("filename")) else None
        due_text = clean_text(entry.get("due_at")) if has_text(entry.get("due_at")) else None
        due = _parse_dt(due_text) if due_text and DT_RE.match(due_text) else None
        approved = entry.get("approved")

        flags = []
        refused = False
        if asset_id is None:
            flags.append("MISSING_ASSET_ID")
        if channel_id is None:
            flags.append("MISSING_CHANNEL_ID")
        elif channel_id not in channel_ids:
            flags.append("UNKNOWN_CHANNEL")
        if kind is None:
            flags.append("MISSING_ASSET_KIND")
        if filename is None:
            flags.append("MISSING_FILENAME")
        elif UNSAFE_REF_RE.search(filename) or len(filename) > 200:
            flags.append("INVALID_ASSET_REF")
            refused = True
            asset_gaps["invalid_refs"].append({
                "asset_id": asset_id, "channel_id": channel_id,
                "basename": safe_basename(filename),
            })
            add("INVALID_ASSET_REF", BLOCKER, channel_id, "asset:%s" % asset_id,
                "素材文件名含路径分隔符、上级目录、URL scheme 或盘符，已拒绝且不解析该路径；"
                "原引用未回显，只保留安全化后的文件名。")
        if due_text is None:
            flags.append("DUE_AT_MISSING")
        elif due is None:
            flags.append("DUE_AT_TIMEZONE_UNKNOWN")
            asset_gaps["timezone_unknown"].append({"asset_id": asset_id, "channel_id": channel_id,
                                                   "due_at": due_text})
            add("DUE_AT_TIMEZONE_UNKNOWN", REVIEW, channel_id, "asset:%s" % asset_id,
                "素材交付时间没有时区偏移，按“时间未知”处理，不按活动时区猜测。")
        elif as_of is not None and due < as_of:
            flags.append("ASSET_OVERDUE")
            asset_gaps["overdue"].append({"asset_id": asset_id, "channel_id": channel_id,
                                          "due_at": due_text})
            add("ASSET_OVERDUE", BLOCKER, channel_id, "asset:%s" % asset_id,
                "素材交付时间已过但仍未确认可用。")
        if approved is True:
            pass
        elif approved is False:
            flags.append("ASSET_NOT_APPROVED")
            asset_gaps["not_approved"].append({"asset_id": asset_id, "channel_id": channel_id})
            add("ASSET_NOT_APPROVED", BLOCKER, channel_id, "asset:%s" % asset_id,
                "素材尚未审核通过，不能按已就绪处理。")
        else:
            flags.append("ASSET_APPROVAL_UNKNOWN")
            asset_gaps["approval_unknown"].append({"asset_id": asset_id, "channel_id": channel_id})
            add("ASSET_APPROVAL_UNKNOWN", REVIEW, channel_id, "asset:%s" % asset_id,
                "素材没有审核状态，保持未知，不按已通过处理。")

        assets.append({
            "asset_id": asset_id, "name": clean_text(entry.get("name")) if has_text(entry.get("name")) else "",
            "channel_id": channel_id, "kind": kind,
            "product_id": clean_text(entry.get("product_id")) if has_text(entry.get("product_id")) else None,
            "filename": None if refused else filename,
            "filename_refused": refused,
            "basename": safe_basename(filename) if refused else filename,
            "due_at": due_text, "approved": approved if isinstance(approved, bool) else None,
            "review_flags": sorted(set(flags)), "index": index,
        })

    for channel in channels:
        for kind in channel["required_asset_kinds"]:
            exists = any(a["channel_id"] == channel["channel_id"] and a["kind"] == kind for a in assets)
            if not exists:
                asset_gaps["missing_by_channel"].append({"channel_id": channel["channel_id"], "kind": kind})
                add("MISSING_ASSET", BLOCKER, channel["channel_id"], "asset:%s" % kind,
                    "渠道要求的「%s」类素材没有任何记录，不能按已就绪处理。" % kind)

    # ---- prep items ----
    prep_raw = data.get("prep_items") if isinstance(data.get("prep_items"), list) else []
    prep_items = []
    channel_deadline = {c["channel_id"]: c["launch_deadline_ts"] for c in channels}
    for index, entry in enumerate(prep_raw):
        if not isinstance(entry, dict):
            continue
        item_id = clean_text(entry.get("item_id")) if has_text(entry.get("item_id")) else None
        channel_id = clean_text(entry.get("channel_id")) if has_text(entry.get("channel_id")) else None
        area = clean_text(entry.get("area")).lower() if has_text(entry.get("area")) else None
        title = clean_text(entry.get("title")) if has_text(entry.get("title")) else ""
        owner = clean_text(entry.get("owner")) if has_text(entry.get("owner")) else None
        due_text = clean_text(entry.get("due_at")) if has_text(entry.get("due_at")) else None
        due = _parse_dt(due_text) if due_text and DT_RE.match(due_text) else None
        done = entry.get("done")

        flags = []
        if item_id is None:
            flags.append("MISSING_ITEM_ID")
        if channel_id is None:
            flags.append("MISSING_CHANNEL_ID")
        elif channel_id not in channel_ids:
            flags.append("UNKNOWN_CHANNEL")
        if area is None:
            flags.append("AREA_MISSING")
        if not title:
            flags.append("TITLE_MISSING")
        if owner is None:
            flags.append("OWNER_MISSING")
            add("MISSING_OWNER", BLOCKER, channel_id, "prep_item:%s" % item_id,
                "准备事项没有负责人，上线当天无人可对账。")
        if due_text is None:
            flags.append("DUE_AT_MISSING")
        elif due is None:
            flags.append("DUE_AT_TIMEZONE_UNKNOWN")
            add("DUE_AT_TIMEZONE_UNKNOWN", REVIEW, channel_id, "prep_item:%s" % item_id,
                "准备事项截止时间没有时区偏移，按“时间未知”处理，不按活动时区猜测。")
        else:
            deadline = channel_deadline.get(channel_id)
            if deadline is not None and due > deadline:
                flags.append("DEADLINE_AFTER_LAUNCH")
                add("DEADLINE_AFTER_LAUNCH", BLOCKER, channel_id, "prep_item:%s" % item_id,
                    "准备事项截止时间晚于该渠道的上线截止时间，无法在上线前完成。")
        if isinstance(done, bool):
            done_state = "DONE" if done else "NOT_DONE"
        else:
            done_state = "UNKNOWN"
            flags.append("DONE_UNKNOWN")
            add("DONE_UNKNOWN", REVIEW, channel_id, "prep_item:%s" % item_id,
                "准备事项没有完成状态，保持未知，不按未完成或已完成处理。")

        prep_items.append({
            "item_id": item_id, "channel_id": channel_id, "area": area, "title": title,
            "owner": owner, "due_at": due_text, "done_state": done_state,
            "review_flags": sorted(set(flags)), "index": index,
        })

    # ---- per-channel readiness matrix ----
    for channel in channels:
        cid = channel["channel_id"]
        channel_findings = [f for f in findings if f["channel_id"] == cid]
        blockers = [f for f in channel_findings if f["severity"] == BLOCKER]
        reviews = [f for f in channel_findings if f["severity"] == REVIEW]
        if blockers:
            state = "BLOCKED"
        elif reviews:
            state = "AT_RISK"
        else:
            state = "READY"
        channel["state"] = state
        channel["blocker_count"] = len(blockers)
        channel["review_count"] = len(reviews)
        channel["blocker_codes"] = sorted({f["code"] for f in blockers})
        channel["review_codes"] = sorted({f["code"] for f in reviews})

    channel_status_counts = {s: 0 for s in CHANNEL_STATES}
    for channel in channels:
        if "state" in channel:
            channel_status_counts[channel["state"]] += 1

    # ---- owner todos ----
    owner_todos = []
    todo_groups = {}
    for item in prep_items:
        key = item["owner"] or "未指派"
        todo_groups.setdefault(key, []).append(item)
    for owner in sorted(todo_groups, key=lambda x: (x == "未指派", x)):
        entries = []
        for item in sorted(todo_groups[owner], key=lambda i: (str(i["channel_id"]), str(i["item_id"]))):
            entries.append({
                "item_id": item["item_id"], "channel_id": item["channel_id"],
                "area": item["area"], "title": item["title"],
                "due_at": item["due_at"], "done_state": item["done_state"],
                "review_flags": list(item["review_flags"]),
            })
        owner_todos.append({"owner": owner, "open_count": sum(1 for e in entries if e["done_state"] != "DONE"),
                            "items": entries})

    # ---- status ----
    blocker_count = sum(1 for f in findings if f["severity"] == BLOCKER)
    review_count = sum(1 for f in findings if f["severity"] == REVIEW)
    if as_of is None or not channels or not products:
        status = "INPUT_INCOMPLETE"
    elif blocker_count:
        status = "BLOCKED"
    elif review_count or warnings:
        status = "NOT_READY"
    else:
        status = "READY"

    unknown_counts = {
        "stock": sum(1 for r in products if r["stock_state"] == "UNKNOWN"),
        "price": sum(1 for r in products if r["price_state"] == "UNKNOWN"),
        "owner": sum(1 for c in channels if c["owner"] is None)
                 + sum(1 for i in prep_items if i["owner"] is None),
        "asset_approval": len(asset_gaps["approval_unknown"]),
        "due_at_timezone": len(asset_gaps["timezone_unknown"])
                           + sum(1 for i in prep_items if "DUE_AT_TIMEZONE_UNKNOWN" in i["review_flags"]),
        "done_state": sum(1 for i in prep_items if i["done_state"] == "UNKNOWN"),
    }

    readiness = {
        "state": status,
        "blocker_count": blocker_count,
        "review_count": review_count,
        "channel_status_counts": channel_status_counts,
        "channel_ready_count": channel_status_counts["READY"],
        "channel_total": len(channels),
        "unknown_counts": unknown_counts,
    }

    # ---- findings view ----
    finding_counts = {code: 0 for code in CODES}
    for finding in findings:
        finding_counts[finding["code"]] += 1
    finding_counts = {code: finding_counts[code] for code in CODES if finding_counts[code]}

    ordered_findings = sorted(
        findings,
        key=lambda f: (0 if f["severity"] == BLOCKER else 1,
                       str(f["channel_id"] or ""), f["code"], str(f["subject"] or "")),
    )

    # ---- checklist ----
    checklist = []

    def step(topic, action):
        checklist.append({"step": len(checklist) + 1, "topic": topic, "action": action})

    step("OWNER_SIGNOFF", "各渠道负责人已确认本渠道的价格、库存与准备事项与系统内一致。")
    if price_conflicts or stock_conflicts:
        step("CONFLICT_RESOLVED", "价格或库存冲突已由人工指定唯一版本，本工具不替你猜最终值。")
    if promotion_conflicts:
        step("PROMO_UNIFIED", "重叠的优惠规则已统一为一条，且已在活动页、客服话术与结算规则中同步。")
    if asset_gaps["not_approved"] or asset_gaps["approval_unknown"]:
        step("ASSET_APPROVED", "所有素材均已人工审核通过；审核状态未知的素材不得按已通过处理。")
    if asset_gaps["overdue"]:
        step("ASSET_OVERDUE", "已逾期未确认的素材已补齐或改期，避免上线当天才发现缺图。")
    if asset_gaps["invalid_refs"]:
        step("ASSET_REF", "含路径或链接的素材文件名已改为裸文件名，本工具未解析这些路径。")
    if asset_gaps["missing_by_channel"]:
        step("ASSET_MISSING", "渠道要求的素材类型已补全，缺失的类型不能按已就绪处理。")
    if any(i["done_state"] == "UNKNOWN" for i in prep_items):
        step("PREP_STATE", "完成状态未知的准备事项已逐条确认，不按已完成或未完成处理。")
    if any("DUE_AT_TIMEZONE_UNKNOWN" in i["review_flags"] for i in prep_items) or asset_gaps["timezone_unknown"]:
        step("TIMEZONE", "所有截止时间已补上时区偏移；未补的仍按时间未知处理。")
    if any("MISSING_OWNER" in i["review_flags"] for i in prep_items) or any(c["owner"] is None for c in channels):
        step("OWNER_ASSIGNED", "缺失的负责人已指派到具体的人。")
    if unknown_counts["stock"]:
        step("STOCK_FACT", "库存未知的商品已查询到真实数量，未按 0 处理。")
    if stock_view:
        step("CURRENCY_CHECK", "价格与库存按币种、按单位分别核对，未做跨币种合计。")
    step("NO_PLATFORM_ACTION", "本工具没有登录店铺、没有改价、没有创建活动、没有发送消息；上线动作由人工执行。")

    # ---- questions ----
    questions = []

    def ask(topic, text):
        questions.append({"id": "Q-%02d" % (len(questions) + 1), "topic": topic, "question": text})

    if as_of is None:
        ask("AS_OF", "请提供带时区偏移的 as_of（例如 2026-09-26T21:00:00+08:00）。")
    if "CAMPAIGN_START_TIMEZONE_MISSING_OR_INVALID" in warnings or "CAMPAIGN_END_TIMEZONE_MISSING_OR_INVALID" in warnings:
        ask("CAMPAIGN_WINDOW", "活动开始或结束时间缺少时区偏移或无法解析，请补充后再判断上线窗口。")
    if window_state == "INVALID":
        ask("CAMPAIGN_WINDOW_INVALID", "活动结束时间不晚于开始时间，请确认正确的活动窗口。")
    if not channels:
        ask("CHANNELS", "没有提供任何渠道，无法判断上线就绪度，请补充 channels[]。")
    if not products:
        ask("PRODUCTS", "没有提供任何活动商品，请补充 products[]。")
    if price_conflicts:
        ask("PRICE_CONFLICT", "以下商品在同一渠道出现两个价格，请指定唯一有效价："
            + "、".join("%s@%s" % (c["product_id"], c["channel_id"]) for c in price_conflicts))
    if stock_conflicts:
        ask("STOCK_CONFLICT", "以下商品在同一渠道出现两个库存数，请指定唯一有效值："
            + "、".join("%s@%s" % (c["product_id"], c["channel_id"]) for c in stock_conflicts))
    if promotion_conflicts:
        ask("PROMOTION_CONFLICT", "以下优惠规则重叠且力度不同，请确认最终保留哪一条："
            + "、".join("／".join(c["promos"]) for c in promotion_conflicts))
    if promotion_conflicts or any("UNKNOWN_CHANNEL" in p["review_flags"] or "UNKNOWN_PRODUCT" in p["review_flags"]
                                  for p in promotions):
        ask("PROMO_SCOPE", "优惠的适用渠道或商品无法确定，请补齐后再判断是否可上线。")
    if any("WINDOW_OUTSIDE_CAMPAIGN" in p["review_flags"] for p in promotions):
        ask("PROMO_WINDOW", "部分优惠的生效区间超出活动窗口，请确认是否为有意的预热或返场。")
    missing_owners = [str(c["channel_id"]) for c in channels if c["owner"] is None] + \
                     [str(i["item_id"]) for i in prep_items if i["owner"] is None]
    if missing_owners:
        ask("OWNER", "以下渠道或准备事项没有负责人，请指派到具体的人：" + "、".join(missing_owners))
    unknown_stock = [str(r["product_id"]) for r in products if r["stock_state"] == "UNKNOWN"]
    if unknown_stock:
        ask("STOCK_UNKNOWN", "以下商品库存未知，请补充真实数量（本工具不会按 0 计算）："
            + "、".join(sorted(set(unknown_stock))))
    if any("DEADLINE_AFTER_LAUNCH" in i["review_flags"] for i in prep_items):
        ask("DEADLINE_AFTER_LAUNCH", "以下准备事项的截止时间晚于渠道上线时间，请提前或改期："
            + "、".join(str(i["item_id"]) for i in prep_items
                        if "DEADLINE_AFTER_LAUNCH" in i["review_flags"]))
    if asset_gaps["missing_by_channel"]:
        ask("ASSET_MISSING", "以下渠道缺少要求的素材类型，请补齐："
            + "、".join("%s/%s" % (g["channel_id"], g["kind"]) for g in asset_gaps["missing_by_channel"]))
    if asset_gaps["not_approved"] or asset_gaps["approval_unknown"]:
        ask("ASSET_APPROVAL", "以下素材尚未审核通过或审核状态未知，请确认后才能按就绪处理："
            + "、".join(sorted({str(g["asset_id"]) for g in asset_gaps["not_approved"] + asset_gaps["approval_unknown"]})))
    if asset_gaps["overdue"]:
        ask("ASSET_OVERDUE", "以下素材已过交付时间，请确认是否可用或改期："
            + "、".join(str(g["asset_id"]) for g in asset_gaps["overdue"]))
    if asset_gaps["invalid_refs"]:
        ask("ASSET_REF", "以下素材文件名含路径或链接，请改为裸文件名（本工具未解析这些路径）："
            + "、".join(str(g["asset_id"]) for g in asset_gaps["invalid_refs"]))
    if asset_gaps["timezone_unknown"] or any("DUE_AT_TIMEZONE_UNKNOWN" in i["review_flags"] for i in prep_items):
        ask("TIMEZONE", "以下截止时间缺少时区偏移，请补充（本工具不按活动时区猜测）："
            + "、".join(sorted([str(g["asset_id"]) for g in asset_gaps["timezone_unknown"]]
                               + [str(i["item_id"]) for i in prep_items
                                  if "DUE_AT_TIMEZONE_UNKNOWN" in i["review_flags"]])))
    if any(i["done_state"] == "UNKNOWN" for i in prep_items):
        ask("PREP_STATE", "以下准备事项没有完成状态，请确认："
            + "、".join(str(i["item_id"]) for i in prep_items if i["done_state"] == "UNKNOWN"))
    if any("STOCK_BELOW_MIN" == f["code"] for f in findings):
        ask("STOCK_BELOW_MIN", "以下商品库存低于你声明的最低库存，请确认是否下架或补货："
            + "、".join(f["subject"].split(":", 1)[1] for f in findings if f["code"] == "STOCK_BELOW_MIN"))
    if any("CROSS_CHANNEL_PRICE_DIFF" == f["code"] for f in findings):
        ask("CROSS_CHANNEL_PRICE", "同一商品在不同渠道价格不同，请确认是否为有意定价（本工具不做比价结论）。")

    # ---- injection report ----
    injection_flagged = []
    for item in prep_items:
        if injection_hit(item["title"]):
            injection_flagged.append({"path": "prep_items[%d]/title" % item["index"],
                                      "marker": "PROMPT_INJECTION"})
    for asset in assets:
        if injection_hit(asset["name"]):
            injection_flagged.append({"path": "assets[%d]/name" % asset["index"],
                                      "marker": "PROMPT_INJECTION"})
    for product in products:
        if injection_hit(product["name"]):
            injection_flagged.append({"path": "products[%d]/name" % product["index"],
                                      "marker": "PROMPT_INJECTION"})
    for promo in promotions:
        if injection_hit(promo["name"]):
            injection_flagged.append({"path": "promotions[%d]/name" % promo["index"],
                                      "marker": "PROMPT_INJECTION"})

    markdown = render_markdown(
        status=status, campaign_id=campaign_id, campaign_name=campaign_name, tz_name=tz_name,
        starts_at=campaign_raw.get("starts_at"), ends_at=campaign_raw.get("ends_at"),
        window_state=window_state, duration_hours=duration_hours, campaign_days=campaign_days,
        as_of=as_of_raw, channels=channels, products=products, promotions=promotions,
        assets=assets, prep_items=prep_items, findings=ordered_findings,
        finding_counts=finding_counts, readiness=readiness, owner_todos=owner_todos,
        checklist=checklist, questions=questions, warnings=warnings,
        price_conflicts=price_conflicts, stock_conflicts=stock_conflicts,
        promotion_conflicts=promotion_conflicts, asset_gaps=asset_gaps,
        stock_view=stock_view, price_view=price_view, injection_flagged=injection_flagged,
    )

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(as_of_raw) if has_text(as_of_raw) else None,
        "campaign": {
            "campaign_id": campaign_id, "name": campaign_name, "timezone": tz_name,
            "starts_at": clean_text(campaign_raw.get("starts_at")) if has_text(campaign_raw.get("starts_at")) else None,
            "ends_at": clean_text(campaign_raw.get("ends_at")) if has_text(campaign_raw.get("ends_at")) else None,
            "window_state": window_state,
            "duration_hours": duration_hours,
            "campaign_days": campaign_days,
        },
        "launch_readiness": readiness,
        "counts": {
            "channels": len(channels), "products": len(products),
            "unique_product_count": len({r["product_id"] for r in products if r["product_id"]}),
            "promotions": len(promotions), "assets": len(assets), "prep_items": len(prep_items),
            "duplicate_product_channel_groups": sum(1 for g in product_groups.values() if len(g) > 1),
        },
        "channels": [{
            "channel_id": c["channel_id"], "name": c["name"], "owner": c["owner"],
            "launch_deadline": c["launch_deadline"], "required_asset_kinds": c["required_asset_kinds"],
            "state": c.get("state"), "blocker_count": c.get("blocker_count", 0),
            "review_count": c.get("review_count", 0), "blocker_codes": c.get("blocker_codes", []),
            "review_codes": c.get("review_codes", []), "review_flags": c["review_flags"],
        } for c in channels],
        "products": [{
            "product_id": r["product_id"], "name": r["name"], "channel_id": r["channel_id"],
            "stock_state": r["stock_state"],
            "stock_value": count_num(r["stock_value"]) if r["stock_state"] == "KNOWN" else None,
            "stock_unit": r["stock_unit"],
            "price_state": r["price_state"],
            "price_value": amount(r["price_value"]) if r["price_state"] == "KNOWN" else None,
            "price_currency": r["price_currency"],
            "min_stock": count_num(r["min_stock"]),
            "review_flags": r["review_flags"],
        } for r in products],
        "promotions": [{
            "promo_id": p["promo_id"], "name": p["name"], "channel_id": p["channel_id"],
            "type": p["type"], "product_ids": p["product_ids"], "value": p["value"],
            "window_starts_at": p["window_starts_at"], "window_ends_at": p["window_ends_at"],
            "review_flags": p["review_flags"],
        } for p in promotions],
        "assets": [{
            "asset_id": a["asset_id"], "name": a["name"], "channel_id": a["channel_id"],
            "kind": a["kind"], "product_id": a["product_id"], "filename": a["filename"],
            "filename_refused": a["filename_refused"],
            "due_at": a["due_at"], "approved": a["approved"], "review_flags": a["review_flags"],
        } for a in assets],
        "prep_items": [{
            "item_id": i["item_id"], "channel_id": i["channel_id"], "area": i["area"],
            "title": i["title"], "owner": i["owner"], "due_at": i["due_at"],
            "done_state": i["done_state"], "review_flags": i["review_flags"],
        } for i in prep_items],
        "price_conflicts": price_conflicts,
        "stock_conflicts": stock_conflicts,
        "promotion_conflicts": promotion_conflicts,
        "stock_by_unit": stock_view,
        "price_range_by_currency": price_view,
        "asset_gaps": asset_gaps,
        "owner_todos": owner_todos,
        "findings": ordered_findings,
        "finding_counts": finding_counts,
        "blocker_count": blocker_count,
        "review_count": review_count,
        "pre_send_checklist": checklist,
        "clarification_questions": questions,
        "markdown_summary": markdown,
        "injection_flagged": injection_flagged,
        "input_warnings": sorted(set(warnings)),
        "disclaimer": DISCLAIMER,
    }


def _parse_dt(text):
    from datetime import datetime
    if not DT_RE.match(text):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


STATE_LABEL = {"BLOCKED": "阻塞（不得上线）", "AT_RISK": "有风险（需人工确认）", "READY": "就绪"}


def render_markdown(status, campaign_id, campaign_name, tz_name, starts_at, ends_at, window_state,
                    duration_hours, campaign_days, as_of, channels, products, promotions, assets,
                    prep_items, findings, finding_counts, readiness, owner_todos, checklist,
                    questions, warnings, price_conflicts, stock_conflicts, promotion_conflicts,
                    asset_gaps, stock_view, price_view, injection_flagged):
    lines = []
    lines.append("# 电商活动上线作战单 — %s" % esc(campaign_name or campaign_id or "未命名活动"))
    lines.append("")
    lines.append("- 上线就绪度：**%s**" % status)
    lines.append("- 活动窗口：%s → %s（%s）" % (
        esc(starts_at) if has_text(starts_at) else "未提供",
        esc(ends_at) if has_text(ends_at) else "未提供",
        esc(window_state)))
    lines.append("- 活动时区：%s ／ 时长：%s 小时 ／ 跨 %s 个自然日" % (
        esc(tz_name) if tz_name else "未提供",
        duration_hours if duration_hours is not None else "未知",
        campaign_days if campaign_days is not None else "未知"))
    lines.append("- 生成基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 阻塞项：**%d** ／ 待确认项：**%d**" % (readiness["blocker_count"], readiness["review_count"]))
    lines.append("")

    lines.append("## 渠道准备矩阵")
    lines.append("")
    if channels:
        lines.append("| 渠道 | 名称 | 负责人 | 上线截止 | 状态 | 阻塞 | 待确认 |")
        lines.append("|---|---|---|---|---|---:|---:|")
        for channel in channels:
            lines.append("| %s | %s | %s | %s | %s | %d | %d |" % (
                esc(channel["channel_id"]) if channel["channel_id"] else "未提供",
                hidden(channel["name"]) if channel["name"] else "未提供",
                esc(channel["owner"]) if channel["owner"] else "**未指派**",
                esc(channel["launch_deadline"]) if channel["launch_deadline"] else "**时间未知**",
                STATE_LABEL.get(channel.get("state"), "未判定"),
                channel.get("blocker_count", 0), channel.get("review_count", 0)))
    else:
        lines.append("- 未提供渠道")
    lines.append("")

    lines.append("## 阻塞项与待确认项")
    lines.append("")
    if findings:
        lines.append("| 严重度 | 代码 | 渠道 | 对象 | 说明 |")
        lines.append("|---|---|---|---|---|")
        for finding in findings:
            lines.append("| %s | %s | %s | %s | %s |" % (
                "阻塞" if finding["severity"] == BLOCKER else "待确认",
                esc(finding["code"]),
                esc(finding["channel_id"]) if finding["channel_id"] else "全局",
                esc(finding["subject"]) if finding["subject"] else "—",
                hidden(finding["detail"])))
    else:
        lines.append("- 无")
    lines.append("")

    if price_conflicts or stock_conflicts or promotion_conflicts:
        lines.append("## 冲突明细")
        lines.append("")
        for conflict in price_conflicts:
            lines.append("- 价格冲突：`%s@%s` 出现 %d 条记录（%s）" % (
                esc(conflict["product_id"]) if conflict["product_id"] else "未提供",
                esc(conflict["channel_id"]) if conflict["channel_id"] else "未提供",
                conflict["occurrences"], esc("、".join(conflict["prices"]))))
        for conflict in stock_conflicts:
            lines.append("- 库存冲突：`%s@%s` 出现 %d 条记录（%s）" % (
                esc(conflict["product_id"]) if conflict["product_id"] else "未提供",
                esc(conflict["channel_id"]) if conflict["channel_id"] else "未提供",
                conflict["occurrences"], esc("、".join(conflict["stock_values"]))))
        for conflict in promotion_conflicts:
            lines.append("- 优惠冲突：`%s` 与 `%s` 在渠道 %s 的同类型规则上力度不同（%s）" % (
                esc(conflict["promos"][0]), esc(conflict["promos"][1]),
                esc(conflict["channel_id"]) if conflict["channel_id"] else "未提供",
                esc(" vs ".join(conflict["values"]))))
        lines.append("")

    lines.append("## 商品价格与库存事实")
    lines.append("")
    if products:
        lines.append("| 商品 | 渠道 | 库存 | 价格 | 备注 |")
        lines.append("|---|---|---|---|---|")
        for product in products:
            stock_text = ("%s %s" % (product["stock_value"], product["stock_unit"] or "")
                          if product["stock_state"] == "KNOWN" else
                          ("**未知**" if product["stock_state"] == "UNKNOWN" else "**无效**"))
            price_text = ("%s %s" % (product["price_value"], product["price_currency"] or "?")
                          if product["price_state"] == "KNOWN" else
                          ("**未知**" if product["price_state"] == "UNKNOWN" else "**无效**"))
            lines.append("| %s | %s | %s | %s | %s |" % (
                hidden(product["name"]) if product["name"] else esc(product["product_id"]),
                esc(product["channel_id"]) if product["channel_id"] else "未提供",
                stock_text, price_text,
                esc("、".join(product["review_flags"])) if product["review_flags"] else "—"))
    else:
        lines.append("- 未提供商品")
    lines.append("")
    if stock_view:
        lines.append("**按单位统计库存（不做跨单位合计）**")
        lines.append("")
        for unit in sorted(stock_view):
            bucket = stock_view[unit]
            lines.append("- %s：已知合计 %s（%d 项）／未知 %d 项／无效 %d 项" % (
                esc(unit), bucket["known_total"] if bucket["known_total"] is not None else "无法合计",
                bucket["known_count"], bucket["unknown_count"], bucket["invalid_count"]))
        lines.append("")
    if price_view:
        lines.append("**按币种统计价格区间（不做跨币种合计、不计算成交额或利润）**")
        lines.append("")
        for currency in sorted(price_view):
            bucket = price_view[currency]
            lines.append("- %s：%s – %s（%d 项）" % (
                esc(currency), bucket["min"], bucket["max"], bucket["count"]))
        lines.append("")

    lines.append("## 优惠规则")
    lines.append("")
    if promotions:
        lines.append("| 优惠 | 渠道 | 类型 | 力度 | 生效区间 | 备注 |")
        lines.append("|---|---|---|---|---|---|")
        for promo in promotions:
            lines.append("| %s | %s | %s | %s | %s → %s | %s |" % (
                hidden(promo["name"]) if promo["name"] else esc(promo["promo_id"]),
                esc(promo["channel_id"]) if promo["channel_id"] else "未提供",
                esc(promo["type"]) if promo["type"] else "未提供",
                esc(promo["value"]) if promo["value"] is not None else "**未知**",
                esc(promo["window_starts_at"]) if promo["window_starts_at"] else "未提供",
                esc(promo["window_ends_at"]) if promo["window_ends_at"] else "未提供",
                esc("、".join(promo["review_flags"])) if promo["review_flags"] else "—"))
    else:
        lines.append("- 未提供优惠规则")
    lines.append("")

    lines.append("## 素材清单（只按文件名与审核状态，不读取文件内容）")
    lines.append("")
    if assets:
        lines.append("| 素材 | 渠道 | 类型 | 文件名 | 交付时间 | 审核 | 备注 |")
        lines.append("|---|---|---|---|---|---|---|")
        for asset in assets:
            approved = ("已通过" if asset["approved"] is True else
                        ("未通过" if asset["approved"] is False else "**未知**"))
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                hidden(asset["name"]) if asset["name"] else esc(asset["asset_id"]),
                esc(asset["channel_id"]) if asset["channel_id"] else "未提供",
                esc(asset["kind"]) if asset["kind"] else "未提供",
                ("**已拒绝（未解析%s）**" % (
                    "，安全化文件名：" + esc(asset["basename"]) if asset["basename"] else "")
                 if asset["filename_refused"] else
                 (esc(asset["filename"]) if asset["filename"] else "未提供")),
                esc(asset["due_at"]) if asset["due_at"] else "**未提供**",                approved,
                esc("、".join(asset["review_flags"])) if asset["review_flags"] else "—"))
    else:
        lines.append("- 未提供素材")
    lines.append("")

    lines.append("## 准备事项与负责人待办")
    lines.append("")
    if prep_items:
        lines.append("| 事项 | 渠道 | 环节 | 内容 | 负责人 | 截止 | 完成状态 |")
        lines.append("|---|---|---|---|---|---|---|")
        for item in prep_items:
            done_label = {"DONE": "已完成", "NOT_DONE": "未完成", "UNKNOWN": "**未知**"}[item["done_state"]]
            lines.append("| %s | %s | %s | %s | %s | %s | %s |" % (
                esc(item["item_id"]) if item["item_id"] else "未提供",
                esc(item["channel_id"]) if item["channel_id"] else "未提供",
                esc(item["area"]) if item["area"] else "未提供",
                hidden(item["title"]) if item["title"] else "未填写",
                esc(item["owner"]) if item["owner"] else "**未指派**",
                esc(item["due_at"]) if item["due_at"] else "**未提供**",
                done_label))
    else:
        lines.append("- 未提供准备事项")
    lines.append("")
    if owner_todos:
        lines.append("### 按负责人汇总")
        lines.append("")
        for todo in owner_todos:
            lines.append("- **%s**：待办 %d 项 —— %s" % (
                esc(todo["owner"]), todo["open_count"],
                esc("、".join(str(i["item_id"]) for i in todo["items"])) or "无"))

        lines.append("")

    lines.append("## 发送前确认单")
    lines.append("")
    for entry in checklist:
        lines.append("%d. [ ] %s" % (entry["step"], esc(entry["action"])))
    lines.append("")

    lines.append("## 待确认问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- `%s` %s" % (question["id"], esc(question["question"])))
    else:
        lines.append("- 无")
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
