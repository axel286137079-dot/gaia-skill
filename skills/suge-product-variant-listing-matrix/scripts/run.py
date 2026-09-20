#!/usr/bin/env python3
"""商品变体与上架信息矩阵 — offline, deterministic, read-only.

Reads one JSON file, writes one JSON document to stdout. No network, no
third-party packages, no file writes, no child processes. Python 3.9+.
"""
import json
import re
import sys
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

SKILL = "suge-product-variant-listing-matrix"
VERSION = "1.0.2"

D = Decimal
CENT = D("0.01")

STATUS_RANK = {"OK": 0, "REVIEW": 1, "CONFLICT": 2, "INVALID": 3}

# ---- untrusted-input rules (deterministic, finite vocabularies) -------------

# "override action" verbs that only make sense when they target a rule/instruction
OVERRIDE_VERBS = [
    "忽略", "忽视", "无视", "不要理会", "不用理会", "忘记", "无视掉", "跳过",
    "ignore", "disregard", "override", "forget", "bypass",
]
# "instruction / rule / system / prompt" nouns
INSTRUCTION_NOUNS = [
    "指令", "要求", "规则", "设定", "系统提示", "系统消息", "提示词", "提示",
    "上述", "以上", "之前", "所有",
    "instruction", "instructions", "rule", "rules", "prompt", "system",
    "previous", "above", "earlier",
]

CREDENTIAL_KEYS = (
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "access_key", "private_key", "client_secret", "cookie", "session_id",
    "auth_code", "授权码", "密码", "密钥", "令牌",
)
# Split so this source never contains a literal PEM header that a release
# scanner (or a reader) could mistake for a real key.
_PEM_HEAD = "-----BEGIN "
_PEM_TAIL = "PRIVATE KEY-----"
CREDENTIAL_VALUE_PATTERNS = [
    re.compile(_PEM_HEAD + r"[A-Z ]*" + _PEM_TAIL),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?<![A-Za-z0-9])ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}"),
]

PLACEHOLDER = "已隐藏疑似提示注入文本"

# ---- formatting helpers ----------------------------------------------------


def dec(value):
    """Parse a JSON scalar into Decimal, or None when it is not a number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return D(value)
    if isinstance(value, float):
        return D(str(value))
    if isinstance(value, str):
        raw = value.strip()
        if raw == "":
            return None
        try:
            return D(raw)
        except InvalidOperation:
            return None
    return None


def money(value):
    return None if value is None else str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def normalize_text(value):
    """NFKC + strip control chars + collapse whitespace. Never destructive for display."""
    if not isinstance(value, str):
        return value
    text = unicodedata.normalize("NFKC", value)
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return " ".join(text.split())


def key_of(value):
    return normalize_text(value).casefold() if isinstance(value, str) else value


def safe(value):
    """Escape one value so it cannot create Markdown structure.

    A value that itself carries prompt-injection text is replaced by a fixed
    placeholder, so no flagged raw text can reach a human-readable deliverable.
    """
    if isinstance(value, str) and clause_flags_injection(value):
        return PLACEHOLDER
    text = "" if value is None else str(value)
    text = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in text)
    text = " ".join(text.split())
    for ch in ("\\", "|", "`", "[", "]", "(", ")", "#", "!", "<", ">", "*", "_"):
        text = text.replace(ch, "\\" + ch)
    return text


def clause_flags_injection(text):
    """True only when an override verb and a rule/instruction noun share a clause."""
    if not isinstance(text, str):
        return False
    low = unicodedata.normalize("NFKC", text).casefold()
    for clause in re.split(r"[。！？；;!?\n\r]+", low):
        if len(clause) > 200:
            clause = clause[:200]
        has_verb = any(verb in clause for verb in OVERRIDE_VERBS)
        has_noun = any(noun in clause for noun in INSTRUCTION_NOUNS)
        if has_verb and has_noun:
            return True
    return False


def strings_in(node, path, out):
    """Collect every string leaf with its JSON-ish path."""
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            strings_in(v, path + "." + str(k) if path else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            strings_in(v, "%s[%d]" % (path, i), out)


def find_credentials(node):
    """Return paths of credential-shaped keys/values. Values are never echoed."""
    hits = []
    stack = [("", node)]
    while stack:
        path, current = stack.pop()
        if isinstance(current, dict):
            for k, v in current.items():
                child = path + "." + str(k) if path else str(k)
                if isinstance(v, (dict, list)):
                    stack.append((child, v))
                    continue
                if any(token in str(k).casefold() for token in CREDENTIAL_KEYS):
                    hits.append(child)
                elif isinstance(v, str) and any(p.search(v) for p in CREDENTIAL_VALUE_PATTERNS):
                    hits.append(child)
        elif isinstance(current, list):
            for i, v in enumerate(current):
                child = "%s[%d]" % (path, i)
                if isinstance(v, (dict, list)):
                    stack.append((child, v))
                elif isinstance(v, str) and any(p.search(v) for p in CREDENTIAL_VALUE_PATTERNS):
                    hits.append(child)
    return sorted(set(hits))


def bad_credential_key(node):
    """Detect credential-named top-level-ish keys even when the value is blank."""
    hits = []
    if isinstance(node, dict):
        for k, v in node.items():
            if any(token in str(k).casefold() for token in CREDENTIAL_KEYS):
                hits.append(str(k))
            if isinstance(v, (dict, list)):
                hits.extend(bad_credential_key(v))
    elif isinstance(node, list):
        for v in node:
            hits.extend(bad_credential_key(v))
    return sorted(set(hits))


# ---- analysis --------------------------------------------------------------


def build_output(doc):
    as_of = doc.get("as_of") if isinstance(doc.get("as_of"), str) else None
    currency_default = normalize_text(doc.get("currency_default")) if isinstance(doc.get("currency_default"), str) else None
    parent = doc.get("parent") if isinstance(doc.get("parent"), dict) else {}
    dimensions = doc.get("spec_dimensions") if isinstance(doc.get("spec_dimensions"), list) else []
    skus_in = doc.get("skus") if isinstance(doc.get("skus"), list) else []
    fields_in = doc.get("platform_fields") if isinstance(doc.get("platform_fields"), list) else []
    images_in = doc.get("images") if isinstance(doc.get("images"), list) else []

    # ---- untrusted text -----------------------------------------------------
    leaves = []
    strings_in(doc, "", leaves)
    injection_flagged = sorted({p for p, v in leaves if clause_flags_injection(v)})

    # ---- credentials --------------------------------------------------------
    cred_paths = sorted(set(find_credentials(doc)) | set(bad_credential_key(doc)))
    if cred_paths:
        return {
            "skill": SKILL,
            "version": VERSION,
            "as_of": as_of,
            "status": "REJECTED",
            "rejected_reason": "CREDENTIAL_DETECTED",
            "rejected_fields": cred_paths,
            "sku_count": len(skus_in),
            "injection_flagged": [],
            "markdown_summary": (
                "# 输入被拒绝\n\n检测到疑似真实凭据字段（共 %d 处）。为保护隐私，**本工具不会回显这些内容**，"
                "也不会继续处理该文件。请移除凭据后重新提交商品事实。字段位置：%s。"
                % (len(cred_paths), ", ".join(safe(p) for p in cred_paths))
            ),
            "disclaimer": "本输出为商品上架信息核对材料，不构成平台合规结论；平台规则以平台后台为准。",
        }

    # ---- spec dimensions ----------------------------------------------------
    dim_names = []
    dim_values = {}
    seen_dim = {}
    dimension_rows = []
    for i, dim in enumerate(dimensions):
        if not isinstance(dim, dict):
            continue
        name = normalize_text(dim.get("name")) if isinstance(dim.get("name"), str) else None
        raw_values = dim.get("values") if isinstance(dim.get("values"), list) else []
        normalized = []
        for v in raw_values:
            if not isinstance(v, str):
                continue
            normalized.append(normalize_text(v))
        # One comparison key: NFKC + whitespace-collapsed + casefolded. "Red" and
        # "red" are therefore ONE spec value, so `normalized_values`,
        # `duplicate_values` and the cartesian count can never contradict each
        # other. The first spelling encountered is kept as the display value.
        display_of = {}
        unique = []
        dupes = []
        for v in normalized:
            if not v:
                continue
            k = key_of(v)
            if k in display_of:
                first = display_of[k]
                if first not in dupes:
                    dupes.append(first)
                continue
            display_of[k] = v
            unique.append(v)
        if name:
            if name in seen_dim:
                continue
            seen_dim[name] = True
            dim_names.append(name)
            dim_values[name] = {key_of(v): v for v in unique}
        else:
            continue
        dimension_rows.append({
            "name": name,
            "raw_values": [normalize_text(v) for v in raw_values if isinstance(v, str)],
            "normalized_values": unique,
            "duplicate_values": dupes,
        })

    cartesian_count = 1
    for name in dim_names:
        cartesian_count *= max(1, len(dim_values[name]))

    # ---- SKUs ---------------------------------------------------------------
    rows = []
    id_counter = {}
    for s in skus_in:
        if isinstance(s, dict) and isinstance(s.get("sku_id"), str):
            id_counter[normalize_text(s["sku_id"])] = id_counter.get(normalize_text(s["sku_id"]), 0) + 1
    duplicate_ids = sorted([k for k, c in id_counter.items() if c > 1])

    barcode_counter = {}
    for s in skus_in:
        if not isinstance(s, dict):
            continue
        bc = s.get("barcode")
        if isinstance(bc, str) and normalize_text(bc):
            barcode_counter[normalize_text(bc)] = barcode_counter.get(normalize_text(bc), 0) + 1
    duplicate_barcodes = sorted([k for k, c in barcode_counter.items() if c > 1])

    declared_images = set()
    image_roles = {}
    for img in images_in:
        if isinstance(img, dict) and isinstance(img.get("image_ref"), str):
            ref = normalize_text(img["image_ref"])
            declared_images.add(ref)
            image_roles[ref] = normalize_text(img.get("role")) if isinstance(img.get("role"), str) else None

    combo_seen = {}
    for idx, s in enumerate(skus_in):
        if not isinstance(s, dict):
            continue
        flags = []
        row = {"row_index": idx + 1}

        raw_id = s.get("sku_id")
        sku_id = normalize_text(raw_id) if isinstance(raw_id, str) else None
        row["sku_id"] = sku_id

        specs = s.get("specs") if isinstance(s.get("specs"), dict) else {}
        norm_specs = {}
        for name in dim_names:
            if name in specs:
                norm_specs[name] = normalize_text(specs[name]) if isinstance(specs[name], str) else None
        row["specs"] = dict(norm_specs)

        if sku_id is None:
            flags.append("MISSING_SKU_ID")
        elif sku_id in duplicate_ids:
            flags.append("DUPLICATE_SKU_ID")

        absent_dims = []
        unknown_dims = []
        for name in dim_names:
            if name not in specs:
                flags.append("INCOMPLETE_SPEC")
                absent_dims.append(name)
            else:
                value = norm_specs.get(name)
                if value is None or key_of(value) not in dim_values.get(name, {}):
                    flags.append("UNKNOWN_SPEC_VALUE")
                    unknown_dims.append(name)
        # specs outside declared dimensions
        for name in specs:
            if name not in dim_names:
                flags.append("UNDECLARED_SPEC_DIMENSION")

        row["missing_dimensions"] = absent_dims
        row["unknown_value_dimensions"] = unknown_dims
        combo_key = None
        if not absent_dims and not unknown_dims and all(norm_specs.get(name) for name in dim_names):
            combo_key = tuple(key_of(norm_specs[name]) for name in dim_names)
            row["combination"] = [norm_specs[name] for name in dim_names]
            combo_seen.setdefault(combo_key, []).append(row["sku_id"])
        else:
            row["combination"] = None

        price = dec(s.get("price"))
        if "price" not in s or s.get("price") is None or (isinstance(s.get("price"), str) and not s["price"].strip()):
            flags.append("PRICE_UNKNOWN")
            row["price"] = None
        elif price is None:
            flags.append("INVALID_PRICE")
            row["price"] = None
        elif price < 0:
            flags.append("INVALID_PRICE")
            row["price"] = None
        else:
            row["price"] = money(price)

        currency = s.get("currency")
        if isinstance(currency, str) and normalize_text(currency):
            row["currency"] = normalize_text(currency)
        elif currency_default:
            row["currency"] = currency_default
            row["currency_source"] = "currency_default"
        else:
            row["currency"] = None
            flags.append("CURRENCY_UNKNOWN")

        stock = s.get("stock")
        if "stock" not in s or stock is None:
            flags.append("STOCK_UNKNOWN")
            row["stock"] = None
        elif isinstance(stock, bool) or dec(stock) is None:
            flags.append("INVALID_STOCK")
            row["stock"] = None
        else:
            value = dec(stock)
            if value < 0 or value != value.to_integral_value():
                flags.append("INVALID_STOCK")
                row["stock"] = None
            else:
                row["stock"] = int(value)
                row["stock_state"] = "OUT_OF_STOCK" if row["stock"] == 0 else "IN_STOCK"

        barcode = s.get("barcode")
        if barcode is None:
            row["barcode"] = None
            row["barcode_type"] = None
            row["barcode_leading_zero"] = None
        elif isinstance(barcode, str):
            norm = normalize_text(barcode)
            row["barcode"] = norm
            row["barcode_type"] = "TEXT"
            row["barcode_leading_zero"] = bool(norm) and len(norm) > 1 and norm.startswith("0")
        else:
            flags.append("BARCODE_NUMERIC_TYPE")
            row["barcode"] = str(barcode)
            row["barcode_type"] = "NUMERIC"
            row["barcode_leading_zero"] = None
        if row.get("barcode") and row["barcode"] in duplicate_barcodes:
            flags.append("DUPLICATE_BARCODE")

        image_ref = s.get("image_ref")
        if isinstance(image_ref, str) and normalize_text(image_ref):
            ref = normalize_text(image_ref)
            row["image_ref"] = ref
            if ref not in declared_images:
                flags.append("MISSING_IMAGE_REF")
                row["image_ref_declared"] = False
            else:
                row["image_ref_declared"] = True
        else:
            row["image_ref"] = None
            row["image_ref_declared"] = None
            flags.append("MISSING_IMAGE_REF")

        row_flags = sorted(set(flags))
        if "INVALID_PRICE" in row_flags or "INVALID_STOCK" in row_flags or "MISSING_SKU_ID" in row_flags or "DUPLICATE_SKU_ID" in row_flags:
            row["status"] = "INVALID"
        elif "DUPLICATE_SPEC_COMBINATION" in row_flags or "UNKNOWN_SPEC_VALUE" in row_flags or "INCOMPLETE_SPEC" in row_flags or "UNDECLARED_SPEC_DIMENSION" in row_flags:
            row["status"] = "CONFLICT"
        elif row_flags:
            row["status"] = "REVIEW"
        else:
            row["status"] = "OK"
        row["review_flags"] = row_flags
        rows.append(row)

    # mark duplicate spec combinations (needs the full pass above)
    duplicate_combo_keys = sorted([k for k, v in combo_seen.items() if len(v) > 1])
    duplicate_spec_groups = []
    for key in duplicate_combo_keys:
        members = combo_seen[key]
        duplicate_spec_groups.append({
            "combination": [dim_values[dim_names[i]].get(k, k) for i, k in enumerate(key)],
            "sku_ids": members,
            "count": len(members),
        })
        for row in rows:
            if row.get("combination") and tuple(key_of(v) for v in row["combination"]) == key:
                row["review_flags"] = sorted(set(row["review_flags"]) | {"DUPLICATE_SPEC_COMBINATION"})
                if row["status"] == "OK" or row["status"] == "REVIEW":
                    row["status"] = "CONFLICT"

    # ---- cartesian coverage -------------------------------------------------
    # Without a declared spec_dimensions list there is no combination space to
    # compare against, so the analysis stays empty instead of inventing the
    # "no spec at all" tuple as a combination that is beyond the dimensions.
    expected = set()
    actual = set()
    if dim_names:
        acc = [()]
        for name in dim_names:
            acc = [prefix + (value,) for prefix in acc for value in sorted(dim_values[name].keys())]
        expected = set(acc)
        actual = set(combo_seen.keys())
    missing = sorted(expected - actual)
    beyond = sorted(actual - expected)
    missing_labels = [[dim_values[dim_names[i]].get(k, k) for i, k in enumerate(key)] for key in missing]
    beyond_labels = [[k for k in key] for key in beyond]

    # ---- currency totals ----------------------------------------------------
    totals = {}
    for row in rows:
        if row["price"] is None or row["currency"] is None:
            continue
        bucket = totals.setdefault(row["currency"], {
            "sku_count": 0, "stock_known_total": 0, "stock_unknown_count": 0,
            "price_min": None, "price_max": None, "barcode_count": 0,
        })
        bucket["sku_count"] += 1
        if row["stock"] is None:
            bucket["stock_unknown_count"] += 1
        else:
            bucket["stock_known_total"] += row["stock"]
        value = dec(row["price"])
        if value is not None:
            if bucket["price_min"] is None or value < dec(bucket["price_min"]):
                bucket["price_min"] = money(value)
            if bucket["price_max"] is None or value > dec(bucket["price_max"]):
                bucket["price_max"] = money(value)
        if row.get("barcode"):
            bucket["barcode_count"] += 1
    totals_by_currency = {k: totals[k] for k in sorted(totals)}

    # ---- image mapping ------------------------------------------------------
    referenced = sorted({r["image_ref"] for r in rows if r.get("image_ref")})
    missing_refs = sorted({r["image_ref"] for r in rows if r.get("image_ref") and r.get("image_ref_declared") is False})
    unreferenced = sorted(declared_images - set(referenced))

    # ---- platform field coverage -------------------------------------------
    field_gaps = []
    for i, f in enumerate(fields_in):
        if not isinstance(f, dict) or not isinstance(f.get("field"), str):
            continue
        name = normalize_text(f["field"])
        required = bool(f.get("required"))
        max_length = f.get("max_length")
        severity = None
        code = None
        detail = None
        if name in ("title", "brand", "category_ref", "description"):
            value = parent.get(name)
            if not isinstance(value, str) or not normalize_text(value):
                severity, code, detail = "BLOCKER", "MISSING_REQUIRED_FIELD", "父商品缺失该字段"
            elif isinstance(max_length, int) and len(normalize_text(value)) > max_length:
                severity, code, detail = "WARNING", "EXCEEDS_MAX_LENGTH", "长度 %d 超过上限 %d" % (len(normalize_text(value)), max_length)
        elif name == "price":
            bad = [r["sku_id"] for r in rows if r["price"] is None]
            if bad:
                severity, code, detail = "BLOCKER", "SKU_FIELD_GAP", "%d 个 SKU 价格缺失或非法" % len(bad)
        elif name == "stock":
            bad = [r["sku_id"] for r in rows if r["stock"] is None]
            if bad:
                severity, code, detail = "WARNING", "SKU_FIELD_GAP", "%d 个 SKU 库存未知（未按 0 处理）" % len(bad)
        elif name == "barcode":
            bad = [r["sku_id"] for r in rows if not r.get("barcode")]
            numeric = [r["sku_id"] for r in rows if r.get("barcode_type") == "NUMERIC"]
            if bad and required:
                severity, code, detail = "WARNING", "SKU_FIELD_GAP", "%d 个 SKU 缺条码" % len(bad)
            elif bad or numeric:
                severity, code, detail = "INFO", "BARCODE_ATTENTION", "存在缺条码 %d 个或数值型条码 %d 个（前导零不可保留）" % (len(bad), len(numeric))
        elif name == "main_image":
            if missing_refs or unreferenced:
                severity, code, detail = "WARNING", "IMAGE_COVERAGE_GAP", "未声明图片引用 %d 处，未被引用图片 %d 张" % (len(missing_refs), len(unreferenced))
        elif name.startswith("spec_"):
            dim = name[len("spec_"):]
            if dim not in dim_names:
                severity, code, detail = "WARNING", "UNKNOWN_SPEC_DIMENSION", "字段声明的规格维度 %s 未在 spec_dimensions 中定义" % dim
            else:
                bad = [r["sku_id"] for r in rows
                       if dim in r.get("missing_dimensions", []) or dim in r.get("unknown_value_dimensions", [])]
                if bad:
                    severity, code, detail = "BLOCKER", "SKU_FIELD_GAP", "%d 个 SKU 的规格「%s」缺失或取值未定义" % (len(bad), dim)
        else:
            severity, code, detail = "INFO", "UNKNOWN_FIELD", "无法离线核对该平台字段，请人工确认"
        if severity:
            field_gaps.append({
                "field": name,
                "required": required,
                "severity": severity,
                "code": code,
                "detail": detail,
            })
    field_gaps.sort(key=lambda g: ({"BLOCKER": 0, "WARNING": 1, "INFO": 2}[g["severity"]], g["field"]))

    # ---- status -------------------------------------------------------------
    status_counts = {"OK": 0, "REVIEW": 0, "CONFLICT": 0, "INVALID": 0}
    for row in rows:
        status_counts[row["status"]] += 1
    blocker_gaps = [g for g in field_gaps if g["severity"] == "BLOCKER"]
    if status_counts["INVALID"] or blocker_gaps:
        status = "BLOCKED"
    elif not rows or not dim_names:
        # A matrix with no declared spec_dimensions cannot be reviewed at all:
        # variant coverage is undefined, so this is never a clean result.
        status = "GAPS_FOUND"
    elif status_counts["CONFLICT"] or missing or status_counts["REVIEW"] or field_gaps:
        status = "GAPS_FOUND"
    else:
        status = "READY"

    # ---- questions ----------------------------------------------------------
    questions = []
    qn = [0]

    def ask(topic, text):
        qn[0] += 1
        questions.append({"id": "Q-%02d" % qn[0], "topic": topic, "question": text})

    if rows and not dim_names:
        ask("SPEC_DIMENSIONS_NOT_PROVIDED",
            "未提供规格维度（spec_dimensions），无法核对变体矩阵：既不能判断 SKU 的规格组合是否完整，"
            "也不能判断是否存在缺失组合。请补充每个规格维度的名称与取值范围。")
    if duplicate_ids:
        ask("DUPLICATE_SKU_ID", "SKU 编号 %s 重复出现，请确认真实 SKU 是否被拆分或误录。" % safe(", ".join(duplicate_ids)))
    if duplicate_spec_groups:
        ask("DUPLICATE_SPEC_COMBINATION", "以下规格组合出现多个 SKU（%s），请确认是否其中一个应为独立规格值。" % safe("; ".join(",".join(g["combination"]) for g in duplicate_spec_groups)))
    if missing_labels:
        ask("MISSING_COMBINATION", "以下规格组合没有任何 SKU（%s），请确认是未上架还是应补齐。" % safe("; ".join("/".join(x) for x in missing_labels)))
    unknown_values = sorted({"/".join(r["combination"]) if r.get("combination") else r["sku_id"] for r in rows if "UNKNOWN_SPEC_VALUE" in r["review_flags"]})
    if unknown_values:
        ask("UNKNOWN_SPEC_VALUE", "部分 SKU 使用了未在规格维度中定义的规格值（%s），请确认维度值清单是否需要补充。" % safe(", ".join(str(x) for x in unknown_values)))
    if any("INCOMPLETE_SPEC" in r["review_flags"] for r in rows):
        ask("INCOMPLETE_SPEC", "部分 SKU 缺少规格维度，请补齐后再上架。")
    if any(r["stock"] is None for r in rows):
        ask("STOCK_UNKNOWN", "部分 SKU 库存未知（工具未按 0 处理），请提供真实库存或明确标注预售。")
    if any(r["price"] is None for r in rows):
        ask("PRICE_UNKNOWN", "部分 SKU 价格缺失或非法，请提供可上架的价格。")
    if duplicate_barcodes:
        ask("DUPLICATE_BARCODE", "条码 %s 被多个 SKU 复用，请确认是否录入错误。" % safe(", ".join(duplicate_barcodes)))
    if missing_refs:
        ask("MISSING_IMAGE_REF", "SKU 引用了未声明的图片编号（%s），请补充图片或修正引用。" % safe(", ".join(missing_refs)))
    if unreferenced:
        ask("UNREFERENCED_IMAGE", "已声明但未被任何 SKU 引用的图片（%s），请确认是否遗漏绑定。" % safe(", ".join(unreferenced)))
    for gap in field_gaps:
        if gap["severity"] == "BLOCKER":
            ask("FIELD_BLOCKER", "必填字段 %s 未满足：%s" % (gap["field"], safe(gap["detail"])))
    if not questions:
        if not rows:
            ask("INPUT_INCOMPLETE", "未提供任何 SKU，请至少提供一行 SKU 数据（sku_id 与 specs）。")
        else:
            ask("NONE", "未发现必须澄清的问题，可直接使用人工上架检查表。")

    # ---- checklist ----------------------------------------------------------
    checklist = []
    checklist.append({"topic": "SPEC_DIMENSIONS", "action": "确认规格维度与规格值的展示顺序、命名与平台类目一致（本工具已做 NFKC 规范化，但平台可能另有规则）。"})
    checklist.append({"topic": "CARTESIAN", "action": "对照 SKU 矩阵逐行确认每个规格组合都有对应 SKU，或确认为何不上架。"})
    checklist.append({"topic": "DUPLICATE", "action": "处理重复 SKU 编号与重复规格组合，避免上架后被平台判重。"})
    checklist.append({"topic": "BARCODE", "action": "条码一律以文本形式提交，保留前导零；数值型条码需转成字符串。"})
    checklist.append({"topic": "CURRENCY", "action": "分币种分别核对价格，不要跨币种相加。"})
    checklist.append({"topic": "STOCK", "action": "库存未知的 SKU 不要按 0 上架，先补齐或改标预售。"})
    checklist.append({"topic": "IMAGES", "action": "绑定 SKU 与图片编号，补齐未声明引用并清理未被引用图片。"})
    checklist.append({"topic": "FIELDS", "action": "按 platform_fields 逐条补齐必填字段与长度限制。"})
    checklist.append({"topic": "HUMAN_UPLOAD", "action": "本工具不登录店铺、不创建或发布商品；请在平台后台人工完成上架。"})

    # ---- parent fact card ---------------------------------------------------
    def norm_or_none(value):
        return normalize_text(value) if isinstance(value, str) else None

    fact_card = {
        "parent_id": norm_or_none(parent.get("parent_id")),
        "title": norm_or_none(parent.get("title")),
        "brand": norm_or_none(parent.get("brand")),
        "category_ref": norm_or_none(parent.get("category_ref")),
        "description": norm_or_none(parent.get("description")),
        "currency_default": currency_default,
    }

    # ---- markdown -----------------------------------------------------------
    lines = []
    lines.append("# 商品变体与上架信息矩阵")
    lines.append("")
    lines.append("> 基准时间 as_of：%s ｜ 状态：**%s**" % (safe(as_of), status))
    lines.append("")
    lines.append("## 1. 父商品事实卡")
    lines.append("")
    lines.append("| 字段 | 值 |")
    lines.append("| --- | --- |")
    for key in ("parent_id", "title", "brand", "category_ref"):
        lines.append("| %s | %s |" % (safe(key), safe(fact_card[key])))
    lines.append("")
    lines.append("## 2. 规格值规范化")
    lines.append("")
    lines.append("| 维度 | 规范化后取值 | 重复值 |")
    lines.append("| --- | --- | --- |")
    for d in dimension_rows:
        lines.append("| %s | %s | %s |" % (
            safe(d["name"]), safe(", ".join(d["normalized_values"])), safe(", ".join(d["duplicate_values"]) or "无")))
    if rows and not dim_names:
        lines.append("")
        lines.append("未提供 `spec_dimensions`，无法核对规格维度与组合覆盖（因此本表为空，且结论不为 READY）。")
    lines.append("")
    lines.append("笛卡尔组合应有 **%d** 个；实际有效组合 **%d** 个；缺失 **%d** 个。" % (cartesian_count, len(actual), len(missing)))
    if missing_labels:
        lines.append("")
        lines.append("缺失组合：" + safe("; ".join("/".join(x) for x in missing_labels)))
    lines.append("")
    lines.append("## 3. SKU 矩阵（状态：OK %d / REVIEW %d / CONFLICT %d / INVALID %d）" % (
        status_counts["OK"], status_counts["REVIEW"], status_counts["CONFLICT"], status_counts["INVALID"]))
    lines.append("")
    lines.append("| # | SKU | 规格 | 价格 | 币种 | 库存 | 条码 | 状态 |")
    lines.append("| ---: | --- | --- | --- | --- | --- | --- | --- |")
    for row in rows:
        if row.get("combination"):
            spec_text = "/".join(str(x) for x in row["combination"])
        else:
            problems = row.get("missing_dimensions", []) + row.get("unknown_value_dimensions", [])
            spec_text = "规格不完整（%s）" % ",".join(str(x) for x in problems) if problems else "规格不完整"
        lines.append("| %d | %s | %s | %s | %s | %s | %s | %s |" % (
            row["row_index"], safe(row["sku_id"]), safe(spec_text), safe(row["price"] or "未知"),
            safe(row["currency"] or "未知"),
            "未知" if row["stock"] is None else str(row["stock"]),
            safe(row.get("barcode") or "—"), row["status"]))
    lines.append("")
    lines.append("## 4. 分币种合计（不跨币种相加）")
    lines.append("")
    lines.append("| 币种 | SKU 数 | 已知库存合计 | 库存未知 | 价格区间 |")
    lines.append("| --- | ---: | ---: | ---: | --- |")
    for cur in totals_by_currency:
        b = totals_by_currency[cur]
        lines.append("| %s | %d | %d | %d | %s ~ %s |" % (
            safe(cur), b["sku_count"], b["stock_known_total"], b["stock_unknown_count"],
            safe(b["price_min"] or "—"), safe(b["price_max"] or "—")))
    lines.append("")
    lines.append("## 5. 字段缺口")
    lines.append("")
    if field_gaps:
        lines.append("| 字段 | 必填 | 级别 | 说明 |")
        lines.append("| --- | --- | --- | --- |")
        for gap in field_gaps:
            lines.append("| %s | %s | %s | %s |" % (gap["field"], "是" if gap["required"] else "否", gap["severity"], safe(gap["detail"])))
    else:
        lines.append("未发现字段缺口。")
    lines.append("")
    lines.append("## 6. 待确认问题")
    lines.append("")
    for q in questions:
        lines.append("- %s（%s）%s" % (q["id"], safe(q["topic"]), safe(q["question"])))
    lines.append("")
    lines.append("## 7. 人工上架检查表")
    lines.append("")
    for item in checklist:
        lines.append("- [ ] %s：%s" % (safe(item["topic"]), safe(item["action"])))
    lines.append("")
    lines.append("## 8. 边界")
    lines.append("")
    lines.append("本工具只读输入、离线计算：不抓取平台、不登录店铺、不创建或发布商品、不猜测功效/材质/认证/库存/价格，平台规则必须由用户提供。")
    if injection_flagged:
        lines.append("输入中检测到疑似提示注入文本，已按不可信数据处理并隐藏，不影响上述判定（位置：%s）。" % safe(", ".join(injection_flagged)))
    markdown = "\n".join(lines) + "\n"

    return {
        "skill": SKILL,
        "version": VERSION,
        "as_of": as_of,
        "status": status,
        "status_counts": status_counts,
        "sku_count": len(rows),
        "unique_sku_id_count": len(id_counter),
        "duplicate_sku_id_count": len(duplicate_ids),
        "valid_sku_count": len(rows) - status_counts["INVALID"],
        "invalid_sku_count": status_counts["INVALID"],
        "parent_fact_card": fact_card,
        "spec_normalization": {
            "dimension_count": len(dim_names),
            "dimensions": dimension_rows,
            "cartesian_combination_count": cartesian_count,
        },
        "cartesian": {
            "expected_combination_count": len(expected),
            "actual_combination_count": len(actual),
            "missing_combinations": missing_labels,
            "beyond_dimension_combinations": beyond_labels,
        },
        "skus": rows,
        "duplicate_spec_groups": duplicate_spec_groups,
        "barcode_issues": {
            "duplicate_barcodes": duplicate_barcodes,
            "numeric_type_sku_ids": [r["sku_id"] for r in rows if "BARCODE_NUMERIC_TYPE" in r["review_flags"]],
        },
        "image_mapping": {
            "declared_images": sorted(declared_images),
            "referenced_images": referenced,
            "missing_image_refs": [{"sku_id": r["sku_id"], "image_ref": r["image_ref"]} for r in rows if r.get("image_ref_declared") is False],
            "unreferenced_images": unreferenced,
        },
        "totals_by_currency": totals_by_currency,
        "field_gaps": field_gaps,
        "blocker_count": len(blocker_gaps),
        "evidence_gap_count": len(field_gaps) + len(missing) + len(duplicate_spec_groups) + len(duplicate_ids),
        "injection_flagged": injection_flagged,
        "clarification_questions": questions,
        "listing_checklist": checklist,
        "markdown_summary": markdown,
        "disclaimer": "本输出为商品上架信息核对材料，不构成平台合规结论；平台规则、类目与字段要求最终以平台后台为准。",
    }


# Public engine entry point used by the batch tests.
analyse = build_output


def main():
    if len(sys.argv) != 2:
        sys.stderr.write("usage: run.py <input.json>\n")
        return 2
    try:
        with open(sys.argv[1], "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as exc:
        json.dump({"skill": SKILL, "version": VERSION, "status": "REJECTED",
                   "rejected_reason": "INPUT_UNREADABLE", "detail": str(exc)}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    if not isinstance(doc, dict):
        json.dump({"skill": SKILL, "version": VERSION, "status": "REJECTED",
                   "rejected_reason": "INPUT_NOT_OBJECT"}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    output = build_output(doc)
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
