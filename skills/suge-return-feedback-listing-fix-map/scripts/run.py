#!/usr/bin/env python3
"""退货反馈到详情页修正地图 — offline return-feedback to listing-fix mapper.

Pure Python 3.9+ standard library. Reads exactly one local JSON file, writes one
JSON document to stdout. No network, no filesystem writes, no shop login,
no order data, no customer PII handling.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

VERSION = "1.0.1"

REASON_TEXT_MAX = 500
DEFAULT_MIN_SAMPLE = 3
HIGH_SHARE = Decimal("0.20")

PLACEHOLDER = "已隐藏疑似提示注入文本"

# --------------------------------------------------------------------------
# fixed reason taxonomy. Order matters: the FIRST matching rule wins, so the
# more specific phrases are listed before the broader ones.
# --------------------------------------------------------------------------
REASON_RULES = (
    ("MEDICAL_CLAIM", (
        "没效果", "没有效果", "不管用", "没治好", "无效", "疗效", "治愈", "治好",
        "缓解", "止痛", "镇痛", "消炎", "医用", "治疗",
        "no effect", "didn't work", "does not work", "didn't help", "cure",
    )),
    ("LOGISTICS_DAMAGE", (
        "运输破损", "运输中破", "快递摔", "物流损坏", "压坏", "磕碰", "in transit",
    )),
    ("DAMAGED_ON_ARRIVAL", (
        "到货破损", "开箱破损", "收到就破", "破损", "坏了", "断裂", "碎了", "裂了",
        "damaged", "broken", "cracked", "torn",
    )),
    ("WRONG_ITEM", (
        "发错货", "发错", "寄错", "不是我要的", "型号不对", "wrong item", "wrong color sent",
    )),
    ("MISSING_PART", (
        "缺件", "少件", "少了配件", "没有配件", "漏发", "missing part", "missing accessory",
    )),
    ("FUNCTION_DEFECT", (
        "不好用", "不能用", "故障", "不工作", "漏水", "不亮", "失灵",
        "defective", "not working", "malfunction",
    )),
    ("QUALITY_DEFECT", (
        "开线", "掉色", "起球", "变形", "掉毛", "褪色", "质量差", "做工",
        "pilling", "fading",
    )),
    ("SHIPPING_DELAY", (
        "物流慢", "发货慢", "等太久", "太慢", "未按时", "迟到", "late", "slow shipping",
    )),
    ("SIZE_MISMATCH", (
        "尺寸不符", "尺寸不对", "尺码不对", "尺码不符", "size mismatch",
    )),
    ("SIZE_TOO_SMALL", (
        "尺码偏小", "偏小", "太小", "穿不上", "挤脚", "太紧", "勒得", "小了",
        "too small", "tight",
    )),
    ("SIZE_TOO_LARGE", (
        "尺码偏大", "偏大", "太大", "太松", "宽松", "大了", "too large", "too big", "loose",
    )),
    ("COLOR_MISMATCH", (
        "色差", "颜色不符", "颜色偏", "颜色不", "实物颜色", "和图片不一样", "和详情页不一样",
        "color differ", "different color", "wrong shade",
    )),
    ("MATERIAL_SPEC_MISMATCH", (
        "成分不符", "成分不", "不是纯棉", "含棉量", "材质标注", "composition",
    )),
    ("MATERIAL_FEEL", (
        "材质不符", "手感", "料子", "面料", "太薄", "太厚", "不是真皮", "粗糙",
        "fabric", "material feel",
    )),
    ("COMPATIBILITY", (
        "装不上", "不兼容", "接口不符", "不适配", "装不了", "型号不适", "配不上",
        "compatible", "doesn't fit", "does not fit",
    )),
    ("USAGE_INSTRUCTION_UNCLEAR", (
        "不知道怎么用", "不会用", "说明不清", "没有说明", "不会操作", "说明书",
        "instructions unclear", "no instructions",
    )),
    ("PACKAGING", (
        "包装简陋", "包装破损", "没有保护", "包装差", "packaging",
    )),
    ("EXPECTATION_MISMATCH_GENERIC", (
        "和想象不一样", "不值", "失望", "和描述不同", "不如预期", "不太值",
        "not as expected", "disappointed",
    )),
)

REASON_ORDER = [name for name, _ in REASON_RULES] + ["NO_TEXT", "OTHER_UNCLASSIFIED"]

# Candidate correction targets per reason: (listing field, required known fact).
# A reason is ATTRIBUTABLE only when at least one target's field is declared by the
# user AND the fact backing that field is actually known. Otherwise the tool will
# not point at a field it cannot justify.
REASON_TARGETS = {
    "SIZE_MISMATCH": (("size_chart", "size_system"),),
    "SIZE_TOO_SMALL": (("size_chart", "size_system"), ("fit_note", "fit_note")),
    "SIZE_TOO_LARGE": (("size_chart", "size_system"), ("fit_note", "fit_note")),
    "COLOR_MISMATCH": (("image_caption", "color_name"), ("color_note", "color_name")),
    "MATERIAL_FEEL": (("material_note", "material"), ("material", "material")),
    "MATERIAL_SPEC_MISMATCH": (("composition", "composition"),),
    "COMPATIBILITY": (("compatibility", "compatible_models"), ("spec_table", "spec")),
    "USAGE_INSTRUCTION_UNCLEAR": (("usage_condition", "usage_condition"), ("instruction", "usage_condition")),
    "PACKAGING": (("package_content", "package_content"),),
    "EXPECTATION_MISMATCH_GENERIC": (("title", "title"),),
}

# Reasons a listing edit cannot honestly address. They are reported, never turned
# into a "fix the detail page" task.
NOT_ATTRIBUTABLE_REASONS = {
    "DAMAGED_ON_ARRIVAL", "LOGISTICS_DAMAGE", "WRONG_ITEM", "MISSING_PART",
    "FUNCTION_DEFECT", "QUALITY_DEFECT", "SHIPPING_DELAY", "MEDICAL_CLAIM",
    "NO_TEXT", "OTHER_UNCLASSIFIED",
}

# Too vague to map onto one field even when the field is declared.
GENERIC_REASONS = {"EXPECTATION_MISMATCH_GENERIC"}

EFFICACY_WORDS = (
    "疗效", "治愈", "治好", "治疗", "缓解", "消炎", "止痛", "镇痛", "医用",
    "康复", "没效果", "无效", "不管用", "没有效果",
    "cure", "heal", "relieve pain",
)

CLAIM_WORDS = (
    "最好", "第一", "唯一", "国家级", "最有效", "彻底", "根治", "百分百", "100%",
    "best", "number one", "#1",
)

SUGGESTED_EXPERIMENT = {
    "SIZE_MISMATCH": "在尺码表上方加一行“先量再选”的示意图，并在两周窗口内对比该字段的退货原因分布是否变化。",
    "SIZE_TOO_SMALL": "在尺码表与首图附近补充实测围度区间与“偏紧 / 偏松”提示，两周后对比尺码类退货占比。",
    "SIZE_TOO_LARGE": "在尺码表与首图附近补充实测围度区间与“偏紧 / 偏松”提示，两周后对比尺码类退货占比。",
    "COLOR_MISMATCH": "把首图换成未经调色的自然光实拍，并在图片说明中标注拍摄光源，两周后对比颜色类退货占比。",
    "MATERIAL_FEEL": "在材质说明中补充厚度、克重或手感描述，两周后对比材质类退货占比。",
    "MATERIAL_SPEC_MISMATCH": "补齐成分比例后再改文案；在成分未知前不要填写任何成分表述。",
    "COMPATIBILITY": "补一张适配型号对照表，并在顶部标注“不适配的型号”，两周后对比适配类退货占比。",
    "USAGE_INSTRUCTION_UNCLEAR": "补一段三步使用说明与常见误用提示，两周后对比使用类咨询与退货占比。",
    "PACKAGING": "在包装说明中写清随包装内容物与保护方式，两周后对比包装类反馈占比。",
    "EXPECTATION_MISMATCH_GENERIC": "先用开放式问询补齐具体不满点，再决定改哪个字段；本条不足以直接改详情页。",
}

CRED_KEY = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|auth[_-]?token)",
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
    "指令", "规则", "提示", "系统", "要求", "约束",
    "instruction", "rule", "prompt", "system", "constraint",
)

MD_ESCAPE = "\\`*_{}[]()#+-|<>~!"

DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

PRIORITY_RANK = {
    "HIGH": 0, "MEDIUM": 1, "LOW": 2,
    "INSUFFICIENT_SAMPLE": 3, "NOT_ACTIONABLE": 4,
}

DISCLAIMER = (
    "本输出是把退货与差评反馈整理成“可能可以改写的详情页字段”的候选清单，**不主张详情页导致了退货**；"
    "归因分层只是提示证据强弱，不是因果结论。所有事实性描述必须来自你提供的商品事实，"
    "工具不会替你补充成分、功效、认证或适配信息。"
)


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    """Collapse control characters and whitespace. Chinese punctuation preserved."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = "".join(
        ch for ch in text
        if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C"
    )
    return re.sub(r"\s+", " ", text).strip()


def esc(value):
    text = clean_text(value)
    return "".join("\\" + ch if ch in MD_ESCAPE else ch for ch in text)


def has_text(value):
    return isinstance(value, str) and value.strip() != ""


def norm_key(value):
    """Comparison key for reason text: NFKC, case-folded, punctuation stripped."""
    text = unicodedata.normalize("NFKC", clean_text(value)).casefold()
    return re.sub(r"[\s\u3000!-/:-@\[-`{-~！-／：-＠［-｀｛-～、。，；：？！（）【】《》「」“”‘’·]+", "", text)


def quant(value, places=2):
    exponent = Decimal(1).scaleb(-places)
    out = Decimal(value).quantize(exponent, rounding=ROUND_HALF_UP)
    return abs(out) if out == 0 else out


def parse_dt(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not DT_RE.match(text):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_date(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not DATE_RE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


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
        "analysis_population": 0,
        "clusters": [],
        "cluster_count": 0,
        "priority_counts": {},
        "affected_skus": [],
        "affected_batches": [],
        "unknown_facts": [],
        "not_to_rewrite": [],
        "undeclared_skus": [],
        "outside_window_records": [],
        "duplicates": [],
        "conflicts": [],
        "invalid_records": [],
        "revision_tasks": [],
        "evidence_collection_tasks": [],
        "verification_experiments": [],
        "claim_flags": [],
        "material_summary": "",
        "markdown_summary": ("# 退货反馈到详情页修正地图\n\n- 状态：**REJECTED**\n"
                             "- 已拒绝处理，未回显疑似凭据内容。\n"),
        "injection_flagged": [],
        "input_warnings": ["CREDENTIAL_DETECTED"],
        "disclaimer": DISCLAIMER,
    }


# --------------------------------------------------------------------------
# reason classification
# --------------------------------------------------------------------------
def classify(reason_code, reason_text):
    if has_text(reason_code):
        code = clean_text(reason_code).upper()
        if code in REASON_ORDER:
            return code, "REASON_CODE"
    key = norm_key(reason_text)
    if not key:
        return "NO_TEXT", "NO_TEXT"
    for name, needles in REASON_RULES:
        for needle in needles:
            if norm_key(needle) and norm_key(needle) in key:
                return name, "KEYWORD"
    return "OTHER_UNCLASSIFIED", "FALLBACK"


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
    as_of = parse_dt(data.get("as_of"))
    if as_of is None:
        warnings.append("AS_OF_MISSING_OR_INVALID")

    window_raw = data.get("window") if isinstance(data.get("window"), dict) else {}
    win_from = parse_date(window_raw.get("from"))
    win_to = parse_date(window_raw.get("to"))
    window_valid = win_from is not None and win_to is not None and win_from <= win_to
    if not window_valid:
        warnings.append("WINDOW_MISSING_OR_INVALID")

    min_sample_raw = data.get("min_sample")
    min_sample = DEFAULT_MIN_SAMPLE
    if min_sample_raw is not None:
        parsed = None
        if isinstance(min_sample_raw, int) and not isinstance(min_sample_raw, bool):
            parsed = min_sample_raw
        elif isinstance(min_sample_raw, str) and min_sample_raw.strip().isdigit():
            parsed = int(min_sample_raw.strip())
        if parsed is None or parsed < 1:
            warnings.append("MIN_SAMPLE_INVALID")
        else:
            min_sample = parsed

    facts_raw = data.get("listing_facts") if isinstance(data.get("listing_facts"), dict) else {}
    facts = {}
    for key, value in facts_raw.items():
        facts[str(key)] = clean_text(value) if has_text(value) else None

    declared_skus = set()
    skus_raw = facts_raw.get("skus")
    if isinstance(skus_raw, list):
        for entry in skus_raw:
            if has_text(entry):
                declared_skus.add(clean_text(entry))

    fields_raw = data.get("listing_fields") if isinstance(data.get("listing_fields"), list) else []
    declared_fields = []
    for entry in fields_raw:
        if has_text(entry):
            name = clean_text(entry)
            if name not in declared_fields:
                declared_fields.append(name)

    # ---- records ----
    records_raw = data.get("records") if isinstance(data.get("records"), list) else []
    resolved = []
    for index, entry in enumerate(records_raw):
        path = "records[%d]" % index
        if not isinstance(entry, dict):
            resolved.append({
                "record_id": None, "path": path, "index": index, "invalid": True,
                "flags": ["INVALID_RECORD"], "sku": None, "batch": None,
                "channel": None, "occurred_at": None, "occurred_date": None,
                "reason_code_raw": None, "reason_text_raw": "", "reason_text": "",
                "reason_key": None, "reason_source": None, "truncated": False,
                "efficacy": False, "injection": False, "outside_window": False,
                "rating": None,
            })
            continue

        record_id = clean_text(entry.get("record_id")) if has_text(entry.get("record_id")) else None
        sku = clean_text(entry.get("sku")) if has_text(entry.get("sku")) else None
        batch = clean_text(entry.get("batch")) if has_text(entry.get("batch")) else None
        channel = clean_text(entry.get("channel")) if has_text(entry.get("channel")) else None

        occurred_raw = entry.get("occurred_at")
        occurred = parse_dt(occurred_raw)
        occurred_date = None
        flags = []
        if occurred is not None:
            occurred_date = occurred.date()
        elif has_text(occurred_raw):
            # A date-only value is accepted as a calendar date with no timezone.
            occurred_date = parse_date(occurred_raw)
            if occurred_date is None:
                flags.append("INVALID_OCCURRED_AT")

        reason_raw = entry.get("reason_text")
        reason_text = clean_text(reason_raw) if has_text(reason_raw) else ""
        truncated = len(reason_text) > REASON_TEXT_MAX
        display = reason_text[:REASON_TEXT_MAX] + "…" if truncated else reason_text
        if truncated:
            flags.append("REASON_TEXT_TRUNCATED")
        if not reason_text:
            flags.append("NO_REASON_TEXT")

        reason_code, reason_source = classify(entry.get("reason_code"), reason_text)
        efficacy = bool(reason_text) and any(w in reason_text for w in EFFICACY_WORDS)

        rating = entry.get("rating")
        if rating is not None and not isinstance(rating, (int, float, str)):
            rating = None

        outside = False
        if window_valid and occurred_date is not None:
            outside = occurred_date < win_from or occurred_date > win_to
        if outside:
            flags.append("RECORD_OUTSIDE_WINDOW")

        if record_id is None:
            flags.append("MISSING_RECORD_ID")

        resolved.append({
            "record_id": record_id,
            "path": path,
            "index": index,
            "invalid": record_id is None,
            "flags": flags,
            "sku": sku,
            "batch": batch,
            "channel": channel,
            "occurred_at": clean_text(occurred_raw) if has_text(occurred_raw) else None,
            "occurred_date": occurred_date,
            "reason_code_raw": clean_text(entry.get("reason_code")) if has_text(entry.get("reason_code")) else None,
            "reason_text_raw": reason_text,
            "reason_text": display,
            "reason_key": norm_key(reason_text),
            "reason_source": reason_source,
            "reason_code": reason_code,
            "truncated": truncated,
            "efficacy": efficacy,
            "injection": injection_hit(reason_raw),
            "outside_window": outside,
            "rating": rating,
        })

    records_present = len(resolved) > 0

    # ---- duplicates / conflicts ----
    grouped = {}
    order = []
    for record in resolved:
        key = record["record_id"]
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(record)

    duplicates = []
    conflicts = []
    unique_records = []
    for key in order:
        group = grouped[key]
        if len(group) == 1:
            unique_records.append(group[0])
            continue
        signatures = sorted({(g["sku"], g["reason_key"], g["occurred_at"]) for g in group}, key=str)
        entry = {
            "record_id": key,
            "occurrences": len(group),
            "paths": sorted(g["path"] for g in group),
        }
        if len(signatures) > 1:
            conflicts.append(dict(entry, variants=len(signatures)))
        else:
            duplicates.append(entry)
        unique_records.append(group[0])

    # ---- population and exclusions ----
    invalid_records = [{
        "record_id": r["record_id"], "path": r["path"],
        "flags": sorted(set(r["flags"])),
    } for r in unique_records if r["invalid"]]
    outside_window_records = [{
        "record_id": r["record_id"], "occurred_at": r["occurred_at"],
        "reason_code": r["reason_code"], "flag": "RECORD_OUTSIDE_WINDOW",
    } for r in unique_records if r["outside_window"]]
    population = [r for r in unique_records if not r["invalid"] and not r["outside_window"]]

    undeclared_skus = []
    if declared_skus:
        seen = []
        for record in population:
            if record["sku"] and record["sku"] not in declared_skus and record["sku"] not in seen:
                seen.append(record["sku"])
        undeclared_skus = sorted(seen)

    # ---- clusters ----
    by_reason = {}
    for record in population:
        by_reason.setdefault(record["reason_code"], []).append(record)

    total = len(population)
    clusters = []
    for code in REASON_ORDER:
        group = by_reason.get(code)
        if not group:
            continue
        count = len(group)
        share = (Decimal(count) / Decimal(total)) if total else Decimal("0")
        targets = REASON_TARGETS.get(code, ())
        if code in NOT_ATTRIBUTABLE_REASONS:
            attribution = "NOT_ATTRIBUTABLE"
        elif code in GENERIC_REASONS:
            attribution = "POSSIBLY_RELATED"
        else:
            usable = []
            for field, fact in targets:
                if field in declared_fields and facts.get(fact):
                    usable.append({"field": field, "fact": fact})
            attribution = "ATTRIBUTABLE" if usable else "POSSIBLY_RELATED"

        # "Not addressable by a listing edit" outranks sample size: no amount of
        # extra records would ever turn "arrived damaged" into a detail-page fix.
        if attribution == "NOT_ATTRIBUTABLE":
            priority = "NOT_ACTIONABLE"
        elif count < min_sample:
            priority = "INSUFFICIENT_SAMPLE"
        elif attribution == "ATTRIBUTABLE" and share >= HIGH_SHARE:
            priority = "HIGH"
        elif attribution == "ATTRIBUTABLE":
            priority = "MEDIUM"
        else:
            priority = "MEDIUM" if share >= HIGH_SHARE else "LOW"

        skus = sorted({r["sku"] for r in group if r["sku"]})
        batches = sorted({r["batch"] for r in group if r["batch"]})
        channels = sorted({r["channel"] for r in group if r["channel"]})
        variants = sorted({r["reason_text"] for r in group if r["reason_text"]})
        record_ids = sorted(r["record_id"] for r in group if r["record_id"])

        usable_targets = [{"field": f, "fact": k} for f, k in targets
                          if f in declared_fields and facts.get(k)]
        missing_facts = sorted({k for f, k in targets if not facts.get(k)})
        undeclared_target_fields = sorted({f for f, k in targets if f not in declared_fields})

        flags = []
        if count < min_sample:
            flags.append("LOW_SAMPLE")
        if code in NOT_ATTRIBUTABLE_REASONS:
            flags.append("NOT_ADDRESSABLE_BY_LISTING")
        if code in GENERIC_REASONS:
            flags.append("TOO_GENERIC_TO_MAP")
        if any(r["efficacy"] for r in group):
            flags.append("EFFICACY_LANGUAGE")
        if missing_facts:
            flags.append("FACT_UNKNOWN")
        if undeclared_target_fields:
            flags.append("FIELD_NOT_DECLARED")
        if len(batches) > 1:
            flags.append("SPANS_MULTIPLE_BATCHES")
        if any(r["truncated"] for r in group):
            flags.append("REASON_TEXT_TRUNCATED")

        clusters.append({
            "reason_code": code,
            "record_count": count,
            "share": str(quant(share * 100, 2)) + "%",
            "attribution": attribution,
            "priority": priority,
            "affected_skus": skus,
            "affected_batches": batches,
            "channels": channels,
            "record_ids": record_ids,
            "reason_variants": variants,
            "usable_targets": usable_targets,
            "candidate_fields": [f for f, _ in targets],
            "declared_target_fields": [f for f in (f for f, _ in targets) if f in declared_fields],
            "missing_facts": missing_facts,
            "undeclared_target_fields": undeclared_target_fields,
            "flags": sorted(flags),
        })

    clusters.sort(key=lambda c: (PRIORITY_RANK[c["priority"]], -c["record_count"], c["reason_code"]))

    priority_counts = {}
    for cluster in clusters:
        priority_counts[cluster["priority"]] = priority_counts.get(cluster["priority"], 0) + 1

    affected_skus = sorted({r["sku"] for r in population if r["sku"]})
    affected_batches = sorted({r["batch"] for r in population if r["batch"]})

    # ---- facts that must never be invented ----
    # Only facts that back a reason ACTUALLY observed in this data set are reported
    # as unknown. Flagging every conceivable missing fact would drown the signal and
    # would make a clean listing impossible to mark READY.
    relevant_targets = set()
    for code in by_reason:
        for field, fact in REASON_TARGETS.get(code, ()):
            if field in declared_fields:
                relevant_targets.add((field, fact))

    FACT_NOTES = {
        "composition": "成分比例未知；在补齐之前不得在详情页写入任何成分表述。",
        "compatible_models": "适配型号未知；不得声称“通用适配”。",
        "spec": "规格参数未知；不得凭推测填写规格对照。",
        "usage_condition": "使用条件未知；不得写入未确认的使用场景。",
        "package_content": "随包装内容物未知；不得写入未确认的清单。",
        "color_name": "颜色名称未知；不得写入未确认的颜色表述。",
        "material": "材质构成未知；不得写入未确认的材质表述。",
        "size_system": "尺码体系未知；不得写入未确认的尺码建议。",
        "fit_note": "版型说明未知；不得写入未确认的松紧建议。",
        "title": "标题事实未知；改写前请先确认可主张的事实。",
    }
    unknown_facts = [{
        "field": field,
        "fact": fact,
        "flag": "FACT_UNKNOWN",
        "note": FACT_NOTES.get(fact, "该事实未知，请补齐后再改写该字段；不得凭推测填写。"),
    } for field, fact in sorted(relevant_targets) if not facts.get(fact)]
    unknown_facts.sort(key=lambda x: (x["fact"], x["field"]))

    not_to_rewrite = sorted({
        "功效 / 疗效类表述（如“缓解”“治好”“有效”）",
        "医疗、康复或治疗承诺",
        "未经证实的绝对化用语（如“最好”“第一”“唯一”）",
        "未提供的认证、检测或成分结论",
    })

    # ---- claim flags ----
    claim_flags = []
    for record in population:
        if record["efficacy"]:
            claim_flags.append({
                "record_id": record["record_id"],
                "flag": "EFFICACY_LANGUAGE",
                "action": "记录但不改写；不得据此类反馈修改详情页功效表述。",
            })
        text = record["reason_text"]
        for word in CLAIM_WORDS:
            if word in text:
                claim_flags.append({
                    "record_id": record["record_id"],
                    "flag": "ABSOLUTE_CLAIM_LANGUAGE",
                    "action": "记录；改写文案时不得复制绝对化用语。",
                })
                break
    claim_flags.sort(key=lambda x: (x["record_id"] or "", x["flag"]))

    # ---- revision tasks ----
    # Two disjoint lists, never mixed: a cluster below the sample line must not appear
    # in the revision work order at all - not with a reassuring action note, not with
    # target fields. It goes to the evidence-collection list instead, which carries no
    # rewrite action.
    revision_tasks = []
    evidence_collection_tasks = []
    for cluster in clusters:
        if cluster["priority"] in ("HIGH", "MEDIUM", "LOW") and cluster["attribution"] != "NOT_ATTRIBUTABLE":
            scope = "、".join(cluster["affected_batches"]) or "全部批次"
            fields = cluster["declared_target_fields"] or ["（尚无已声明字段，需先补齐）"]
            revision_tasks.append({
                "task_id": "RT-%02d" % (len(revision_tasks) + 1),
                "reason_code": cluster["reason_code"],
                "priority": cluster["priority"],
                "target_fields": fields,
                "scope_batches": scope,
                "affected_skus": cluster["affected_skus"],
                "action": "按证据修正上述字段的表述，事实未知的部分留空并标记待补。",
                "evidence_record_ids": cluster["record_ids"],
            })
        elif cluster["priority"] == "INSUFFICIENT_SAMPLE":
            evidence_collection_tasks.append({
                "task_id": "EV-%02d" % (len(evidence_collection_tasks) + 1),
                "reason_code": cluster["reason_code"],
                "record_count": cluster["record_count"],
                "min_sample": min_sample,
                "missing": min_sample - cluster["record_count"],
                "priority": "INSUFFICIENT_SAMPLE",
                "observe": "继续收集同类反馈，达到样本线后再评估是否改写字段；本次不加任何改写动作。",
                "candidate_fields": cluster["declared_target_fields"],
                "scope_batches": "、".join(cluster["affected_batches"]) or "全部批次",
                "affected_skus": cluster["affected_skus"],
                "evidence_record_ids": cluster["record_ids"],
            })

    experiments = [{
        "reason_code": cluster["reason_code"],
        "experiment": SUGGESTED_EXPERIMENT.get(
            cluster["reason_code"],
            "先补齐该原因对应的商品事实，再设计字段级对比实验。"),
        "success_metric": "同一时间窗口内该原因占比下降，且其他原因占比不显著上升。",
    } for cluster in clusters if cluster["priority"] in ("HIGH", "MEDIUM", "LOW")]

    # ---- state ----
    if conflicts or not window_valid:
        status = "BLOCKED"
    elif as_of is None or not records_present or total == 0:
        status = "INPUT_INCOMPLETE"
    elif (unknown_facts or undeclared_skus or outside_window_records
          or invalid_records or warnings or not declared_fields):
        status = "GAPS_FOUND"
    else:
        status = "READY"

    # ---- questions ----
    questions = []

    def ask(topic, text):
        questions.append({"id": "Q-%02d" % (len(questions) + 1), "topic": topic, "question": text})

    if as_of is None:
        ask("AS_OF", "请提供带时区偏移的 as_of。")
    if not window_valid:
        ask("WINDOW", "请提供合法的统计窗口（from 与 to 都是 YYYY-MM-DD，且 from 不晚于 to）。")
    if not records_present:
        ask("RECORDS", "没有提供任何退货或差评记录，无法形成修正地图。")
    if not declared_fields:
        ask("LISTING_FIELDS", "没有声明当前详情页字段清单，无法指出该改哪一处。")
    if unknown_facts:
        ask("UNKNOWN_FACTS", "以下商品事实未知，请补齐后再改写对应字段：" +
            "、".join(sorted({u["fact"] for u in unknown_facts})))
    if outside_window_records:
        ask("OUTSIDE_WINDOW", "有记录的时间落在统计窗口之外，请确认窗口是否正确：" +
            "、".join(str(r["record_id"]) for r in outside_window_records))
    if undeclared_skus:
        ask("UNDECLARED_SKU", "以下 SKU 不在商品事实声明的清单内，请确认是否属于本品：" +
            "、".join(undeclared_skus))
    if conflicts:
        ask("RECORD_CONFLICT", "以下记录编号出现了互相矛盾的版本，请确认哪一条为准：" +
            "、".join(str(c["record_id"]) for c in conflicts))
    if duplicates:
        ask("DUPLICATE_RECORD", "以下记录编号重复出现，已按一条计入：" +
            "、".join(str(d["record_id"]) for d in duplicates))
    insufficient = [c["reason_code"] for c in clusters if c["priority"] == "INSUFFICIENT_SAMPLE"]
    if insufficient:
        ask("SAMPLE_SIZE", "以下原因样本少于 %d 条，暂不作为改写依据：" % min_sample +
            "、".join(insufficient))
    if any(c["attribution"] == "POSSIBLY_RELATED" for c in clusters):
        ask("POSSIBLY_RELATED", "以下原因若要落到字段，还需补充对应商品事实或字段声明：" +
            "、".join(c["reason_code"] for c in clusters if c["attribution"] == "POSSIBLY_RELATED"))
    if claim_flags:
        ask("CLAIM_LANGUAGE", "反馈中出现功效或绝对化用语，已标记为“记录但不改写”：不改详情页功效表述。")
    if invalid_records:
        ask("INVALID_RECORD", "以下记录缺少编号，无法引用证据：" +
            "、".join(r["path"] for r in invalid_records))

    # ---- injection ----
    injection_flagged = []
    for record in unique_records:
        if record["injection"]:
            injection_flagged.append({"path": record["path"] + "/reason_text",
                                      "marker": "PROMPT_INJECTION"})

    markdown = render_markdown(
        status=status, as_of=data.get("as_of"), win_from=window_raw.get("from"),
        win_to=window_raw.get("to"), total=total, min_sample=min_sample,
        clusters=clusters, revision_tasks=revision_tasks,
        evidence_collection_tasks=evidence_collection_tasks, experiments=experiments,
        unknown_facts=unknown_facts, not_to_rewrite=not_to_rewrite,
        outside_window_records=outside_window_records, undeclared_skus=undeclared_skus,
        conflicts=conflicts, duplicates=duplicates, questions=questions,
        claim_flags=claim_flags, warnings=warnings, injection_flagged=injection_flagged,
    )

    return {
        "version": VERSION,
        "status": status,
        "as_of": clean_text(data.get("as_of")) if has_text(data.get("as_of")) else None,
        "window": {
            "from": clean_text(window_raw.get("from")) if has_text(window_raw.get("from")) else None,
            "to": clean_text(window_raw.get("to")) if has_text(window_raw.get("to")) else None,
            "valid": window_valid,
        },
        "min_sample": min_sample,
        "record_count_raw": len(resolved),
        "analysis_population": total,
        "declared_listing_fields": declared_fields,
        "declared_skus": sorted(declared_skus),
        "clusters": clusters,
        "cluster_count": len(clusters),
        "priority_counts": priority_counts,
        "affected_skus": affected_skus,
        "affected_batches": affected_batches,
        "unknown_facts": unknown_facts,
        "not_to_rewrite": not_to_rewrite,
        "undeclared_skus": undeclared_skus,
        "outside_window_records": outside_window_records,
        "duplicates": duplicates,
        "conflicts": conflicts,
        "invalid_records": invalid_records,
        "revision_tasks": revision_tasks,
        "evidence_collection_tasks": evidence_collection_tasks,
        "verification_experiments": experiments,
        "claim_flags": claim_flags,
        "clarification_questions": questions,
        "markdown_summary": markdown,
        "injection_flagged": injection_flagged,
        "input_warnings": warnings,
        "disclaimer": DISCLAIMER,
    }


ATTRIBUTION_LABEL = {
    "ATTRIBUTABLE": "可归因（字段已声明且事实已知）",
    "POSSIBLY_RELATED": "可能相关（需先补字段或事实）",
    "NOT_ATTRIBUTABLE": "不可归因（详情页改写无法解决）",
}

PRIORITY_LABEL = {
    "HIGH": "高",
    "MEDIUM": "中",
    "LOW": "低",
    "INSUFFICIENT_SAMPLE": "样本不足",
    "NOT_ACTIONABLE": "不建议据此改写",
}


def render_markdown(status, as_of, win_from, win_to, total, min_sample, clusters,
                    revision_tasks, evidence_collection_tasks, experiments,
                    unknown_facts, not_to_rewrite,
                    outside_window_records, undeclared_skus, conflicts, duplicates,
                    questions, claim_flags, warnings, injection_flagged):
    lines = []
    lines.append("# 退货反馈到详情页修正地图")
    lines.append("")
    lines.append("- 状态：**%s**" % status)
    lines.append("- 统计窗口：%s → %s" % (
        esc(win_from) if has_text(win_from) else "未提供",
        esc(win_to) if has_text(win_to) else "未提供"))
    lines.append("- 生成基准时间（as_of）：%s" % (esc(as_of) if has_text(as_of) else "未提供"))
    lines.append("- 纳入分析记录：%d 条（样本线：%d）" % (total, min_sample))
    lines.append("")
    lines.append("> **本表不主张详情页导致了退货。** 归因分层只表示“这条反馈能不能落到一个可改写的字段上”，"
                 "以及证据够不够。")
    lines.append("")

    lines.append("## 原因簇与归因分层")
    lines.append("")
    if clusters:
        lines.append("| 原因 | 条数 | 占比 | 归因 | 优先级 | 涉及 SKU | 涉及批次 |")
        lines.append("|---|---:|---:|---|---|---|---|")
        for cluster in clusters:
            lines.append("| %s | %d | %s | %s | %s | %s | %s |" % (
                esc(cluster["reason_code"]), cluster["record_count"], cluster["share"],
                ATTRIBUTION_LABEL[cluster["attribution"]],
                PRIORITY_LABEL[cluster["priority"]],
                esc("、".join(cluster["affected_skus"])) if cluster["affected_skus"] else "未提供",
                esc("、".join(cluster["affected_batches"])) if cluster["affected_batches"] else "未提供",
            ))
    else:
        lines.append("- 无可分析的原因簇")
    lines.append("")

    actionable = [c for c in clusters if c["priority"] in ("HIGH", "MEDIUM", "LOW")]
    if actionable:
        lines.append("## 建议修正位置")
        lines.append("")
        for cluster in actionable:
            lines.append("### %s（%s）" % (esc(cluster["reason_code"]), PRIORITY_LABEL[cluster["priority"]]))
            lines.append("")
            lines.append("- 可改字段：%s" % (
                esc("、".join(cluster["declared_target_fields"])) or "（尚无已声明字段）"))
            if cluster["missing_facts"]:
                lines.append("- 待补事实（补齐前不得填写）：%s" % esc("、".join(cluster["missing_facts"])))
            if cluster["undeclared_target_fields"]:
                lines.append("- 建议先声明的字段：%s" % esc("、".join(cluster["undeclared_target_fields"])))
            lines.append("- 证据记录：%s" % esc("、".join(cluster["record_ids"])))
            lines.append("- 反馈原话示例：%s" % (
                hidden(cluster["reason_variants"][0]) if cluster["reason_variants"] else "无文本原因"))
            lines.append("")

    blocked = [c for c in clusters if c["attribution"] == "NOT_ATTRIBUTABLE"]
    if blocked:
        lines.append("## 不可归因：不要写进详情页修正清单")
        lines.append("")
        for cluster in blocked:
            lines.append("- %s" % esc(cluster["reason_code"]))
        lines.append("")
        lines.append("> 上述原因属于物流、发错货、缺件、功能或质量缺陷、以及功效类主张，"
                     "**改写详情页不会解决**，应按各自流程处理。")
        lines.append("")

    lines.append("## 人工详情页改版任务单")
    lines.append("")
    if revision_tasks:
        lines.append("| 任务 | 原因 | 优先级 | 目标字段 | 范围 |")
        lines.append("|---|---|---|---|---|")
        for task in revision_tasks:
            lines.append("| %s | %s | %s | %s | %s |" % (
                esc(task["task_id"]), esc(task["reason_code"]),
                PRIORITY_LABEL[task["priority"]],
                esc("、".join(task["target_fields"])) if task["target_fields"] else "待补字段",
                esc(task["scope_batches"]),
            ))
        lines.append("")
        lines.append("> 上表只包含**达到样本线（%d 条）且可落到字段**的原因。样本不足的原因不在本表中，"
                     "见下一节，**本次不要据此改写详情页**。" % min_sample)
    else:
        lines.append("- 无（没有达到样本线且可落到字段的原因簇）")
    lines.append("")

    # Deliberately a SEPARATE list with its own task-id prefix: nothing here carries a
    # rewrite action, and nothing here may be read as a fix instruction.
    if evidence_collection_tasks:
        lines.append("## 证据不足：先收集，暂不改写")
        lines.append("")
        for task in evidence_collection_tasks:
            lines.append("- `%s` %s：已有 %d 条，距样本线 %d 条，还差 %d 条。%s" % (
                esc(task["task_id"]), esc(task["reason_code"]),
                task["record_count"], task["min_sample"], task["missing"], esc(task["observe"])))
        lines.append("")
        lines.append("> 上述原因**不在改版任务单中**，也不产生任何改写动作；"
                     "补齐证据后再按同一套规则重新评估。")
        lines.append("")

    if experiments:
        lines.append("## 验证实验")
        lines.append("")
        for item in experiments:
            lines.append("- **%s**：%s" % (esc(item["reason_code"]), esc(item["experiment"])))
        lines.append("")

    lines.append("## 不得改写的未知事实")
    lines.append("")
    if unknown_facts:
        for fact in unknown_facts:
            lines.append("- `%s`（字段 `%s`）：%s" % (
                esc(fact["fact"]), esc(fact["field"]), esc(fact["note"])))
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 禁止在详情页出现")
    lines.append("")
    for item in not_to_rewrite:
        lines.append("- %s" % esc(item))
    lines.append("")

    if claim_flags:
        lines.append("## 已标记的敏感表述（记录但不改写）")
        lines.append("")
        for flag in claim_flags:
            lines.append("- `%s` %s：%s" % (
                esc(flag["record_id"]) if flag["record_id"] else "未提供",
                esc(flag["flag"]), esc(flag["action"])))
        lines.append("")

    if injection_flagged:
        lines.append("## 已隐藏的疑似提示注入")
        lines.append("")
        for hit in injection_flagged:
            lines.append("- `%s` 的反馈原话疑似试图给出指令，已隐藏原文，不作为改写依据：%s" % (
                esc(hit["path"]), PLACEHOLDER))
        lines.append("")

    if conflicts or duplicates or outside_window_records or undeclared_skus:
        lines.append("## 数据问题")
        lines.append("")
        for item in conflicts:
            lines.append("- 记录冲突：`%s` 出现 %d 次，内容不一致" % (
                esc(item["record_id"]) if item["record_id"] else "未提供", item["occurrences"]))
        for item in duplicates:
            lines.append("- 重复记录：`%s` 出现 %d 次，已按一条计入" % (
                esc(item["record_id"]) if item["record_id"] else "未提供", item["occurrences"]))
        for item in outside_window_records:
            lines.append("- 窗口外记录：`%s`（%s）" % (
                esc(item["record_id"]) if item["record_id"] else "未提供",
                esc(item["occurred_at"]) if item["occurred_at"] else "未提供"))
        for sku in undeclared_skus:
            lines.append("- 未声明的 SKU：`%s`" % esc(sku))
        lines.append("")

    lines.append("## 待确认问题")
    lines.append("")
    if questions:
        for question in questions:
            lines.append("- `%s` %s" % (question["id"], esc(question["question"])))
    else:
        lines.append("- 无")
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
