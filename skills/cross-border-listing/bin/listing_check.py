#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
listing_check.py — 跨境 Listing（亚马逊）合规自查

发布前把 Listing 过一遍，检查：
  1. 违禁词（绝对化/夸大、健康疗效宣称、促销/引流）
  2. 标题规范（2026-07-27 亚马逊新政：非 Media 类目 Item name ≤75 字符、
     同一实词不超 2 次、禁 emoji/重复标点/官方禁用符号）
  3. Item Highlights 字段（2026 新政新增，≤125 字符，可搜索）
  4. 五点长度、全大写

纯标准库，无 API key，离线可跑。

用法：
    python3 listing_check.py listing.md
    python3 listing_check.py listing.md --json
    # Media（图书/影音）类目标题仍为 200 字符：--title-max 200
"""
import argparse
import json
import re
import sys
from collections import Counter

# ---------------- 违禁词库 ----------------
RULES = {
    "绝对化/夸大词（亚马逊禁）": [
        "best", "best-selling", "bestseller", "#1", "no.1", "number one",
        "top-rated", "top rated", "top seller", "guaranteed", "100%",
        "perfect", "amazing", "incredible", "ultimate", "revolutionary",
        "miracle", "world's best", "best in the world",
    ],
    "健康疗效宣称（FDA 严查）": [
        "cure", "cures", "treat", "treats", "heal", "heals", "prevent",
        "prevents", "anti-cancer", "antibacterial", "antifungal",
        "antiviral", "fda approved", "fda-approved", "medical grade",
        "clinical", "therapeutic", "pain relief", "kills bacteria",
        "kills virus", "boosts immunity", "detox",
    ],
    "促销/引流（亚马逊禁）": [
        "sale", "discount", "discounted", "cheap", "cheapest", "clearance",
        "free shipping", "best price", "lowest price", "promo", "coupon",
        "contact us", "visit our website", ".com", "money back",
        "satisfaction guaranteed",
    ],
}

# ---------------- 标题规范（2026-07-27 亚马逊新政） ----------------
# 非 Media 类目 Item name 上限 75 字符（含空格）；Media（图书/影音/CD）类目仍为 200（用 --title-max 调整）。
TITLE_MAX_LEN = 75
# Item Highlights：2026 新政新增字段，承接标题溢出信息，≤125 字符、可搜索、随标题展示。
HIGHLIGHTS_MAX_LEN = 125
BULLET_MAX_LEN = 500      # 单点描述建议上限（各站点/类目可能更严，可用 --bullet-max 调整）

# 亚马逊标题官方禁用符号（品牌名内含时可例外；2025-01 生效、2026 延续）
FORBIDDEN_SYMBOLS = ["!", "$", "?", "_", "{", "}", "^", "¬", "¦"]

# emoji / 装饰字符（2026 明确禁止 emoji 与 ASCII art）
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
# 重复标点：!!!  ...  ??? 等（2026 明确禁止）
REPEATED_PUNCT_RE = re.compile(r"([!?.])\1{2,}")
# HTML 标签（2026 明确禁止）
HTML_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")

# 标题同词规则：同一实词最多出现 2 次；冠词/介词/连词豁免
TITLE_STOP_WORDS = {
    "the", "and", "for", "with", "from", "that", "this", "these", "those",
    "your", "you", "are", "was", "were", "not", "but", "its", "all", "can",
    "our", "has", "have", "had", "into", "onto", "upon", "per", "via", "than",
}


def _dup_word_issues(title):
    """返回标题中重复 ≥3 次的实词列表（[(词, 次数)]）。"""
    words = re.findall(r"[a-zA-Z]{3,}", title.lower())
    c = Counter(w for w in words if w not in TITLE_STOP_WORDS)
    return [(w, n) for w, n in c.items() if n >= 3]


def check(text, title_max=TITLE_MAX_LEN, bullet_max=BULLET_MAX_LEN,
          highlights_max=HIGHLIGHTS_MAX_LEN):
    """扫描文本，返回问题列表。"""
    issues = []
    # 1) 违禁词（大小写不敏感，整词匹配）
    lower = text.lower()
    for cat, words in RULES.items():
        for w in words:
            # 整词边界匹配，避免 "best" 命中 "bestbuy" 之类
            pattern = r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])'
            if re.search(pattern, lower):
                issues.append({"type": "违禁词", "word": w, "category": cat,
                               "suggest": "→ 删除或改写"})

    # 2) 标题 / Item Highlights 识别
    title_line = None
    highlights_line = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r'^(title|item\s*name|标题)\s*[:：]\s*(.+)$', s, re.I)
        if m and title_line is None:
            title_line = m.group(2).strip()
            continue
        m = re.match(r'^(item\s*highlights|highlights|亮点)\s*[:：]\s*(.+)$', s, re.I)
        if m and highlights_line is None:
            highlights_line = m.group(2).strip()
    if title_line is None:
        # 退而求其次：把第一行非空、非 # 开头当标题
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "Title", "Item", "标题")):
                title_line = s
                break

    if title_line:
        # 2.1 长度：2026-07-27 起非 Media ≤75（含空格）
        if len(title_line) > title_max:
            issues.append({"type": "标题", "word": f"长度 {len(title_line)} 字符",
                           "category": "标题规范",
                           "suggest": f"→ 超过 {title_max} 字符（2026-07-27 新政，非 Media 类目；"
                                      f"Media 类目请用 --title-max 200）。溢出信息移入 Item Highlights"})
        # 2.2 官方禁用符号
        for sym in FORBIDDEN_SYMBOLS:
            if sym in title_line:
                issues.append({"type": "标题", "word": f"符号「{sym}」",
                               "category": "标题规范",
                               "suggest": "→ 亚马逊标题禁用该符号（品牌名内含时例外）"})
        # 2.3 emoji / HTML / 重复标点（2026 新政明令禁止）
        if EMOJI_RE.search(title_line):
            issues.append({"type": "标题", "word": "emoji/装饰字符",
                           "category": "标题规范",
                           "suggest": "→ 2026 新政禁止标题含 emoji 或装饰字符"})
        if HTML_TAG_RE.search(title_line):
            issues.append({"type": "标题", "word": "HTML 标签",
                           "category": "标题规范", "suggest": "→ 标题禁止任何 HTML"})
        for m in REPEATED_PUNCT_RE.finditer(title_line):
            issues.append({"type": "标题", "word": f"重复标点「{m.group(0)}」",
                           "category": "标题规范",
                           "suggest": "→ 2026 新政禁止 !!! / ... / ??? 等重复标点"})
        # 2.4 同一实词重复 ≥3 次（2026 新政：单词不超 2 次）
        for w, n in _dup_word_issues(title_line):
            issues.append({"type": "标题", "word": f"实词「{w}」×{n}",
                           "category": "标题规范",
                           "suggest": f"→ 同一实词最多出现 2 次，删除第 {n} 次重复"})
        # 2.5 全大写检测（排除常见缩写）
        words = [w for w in re.findall(r'[A-Za-z]+', title_line) if len(w) > 2]
        if words and all(w.isupper() for w in words):
            issues.append({"type": "标题", "word": "全大写",
                           "category": "标题规范",
                           "suggest": "→ 亚马逊标题禁全大写（除缩写）"})
    else:
        issues.append({"type": "标题", "word": "未找到标题",
                       "category": "标题规范",
                       "suggest": "→ 请用「Title: xxx」或首行写标题以便检查"})

    # 2.6 Item Highlights 长度（2026 新政新增字段 ≤125，可搜索）
    if highlights_line is not None:
        if len(highlights_line) > highlights_max:
            issues.append({"type": "亮点", "word": f"长度 {len(highlights_line)} 字符",
                           "category": "Item Highlights",
                           "suggest": f"→ 超过 {highlights_max} 字符，需精简到 125 以内"})
    elif title_line:
        issues.append({"type": "提示", "word": "未发现 Item Highlights 字段",
                       "category": "提示(2026新政)",
                       "suggest": "→ 标题溢出/材质/兼容/场景信息可写入 Item Highlights "
                                  "（≤125 字符、可搜索）。用「Highlights: xxx」行可启用其长度检查"})

    # 3) 五点描述长度
    for lineno, line in enumerate(text.splitlines(), 1):
        match = re.match(r'^\s*(?:[-*]\s+|(?:bullet\s*)?\d+[.)：:]\s*)(.+)$', line, re.I)
        if not match:
            continue
        bullet = match.group(1).strip()
        if len(bullet) > bullet_max:
            issues.append({"type": "五点", "word": f"第 {lineno} 行长度 {len(bullet)}",
                           "category": "长度启发式规则",
                           "suggest": f"→ 超过当前设定 {bullet_max} 字符"})
    return issues


def main():
    ap = argparse.ArgumentParser(description="跨境 Listing（亚马逊）合规自查")
    ap.add_argument("file", help="Listing 文件路径（.md/.txt）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--title-max", type=int, default=TITLE_MAX_LEN,
                    help="标题长度阈值（默认 75，2026-07-27 新政；Media 类目用 200）")
    ap.add_argument("--bullet-max", type=int, default=BULLET_MAX_LEN,
                    help="单条五点长度阈值（默认 500）")
    args = ap.parse_args()

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"✗ 找不到文件：{args.file}", file=sys.stderr)
        sys.exit(1)

    issues = check(text, args.title_max, args.bullet_max)

    if args.json:
        print(json.dumps({"file": args.file, "issues": issues,
                          "count": len(issues)}, ensure_ascii=False, indent=2))
        return

    print("=" * 56)
    print("跨境 Listing 合规自查")
    print("=" * 56)
    print(f"文件：{args.file}")
    print(f"问题：{len(issues)} 处\n")

    if not issues:
        print("✓ 未发现明显违规（仍建议人工对照目标站点最新规则复核）。")
        return

    cat_counter = Counter(i["category"] for i in issues)
    for cat, n in cat_counter.most_common():
        print(f"【{cat}】{n} 处")
        for i in issues:
            if i["category"] != cat:
                continue
            print(f"  · {i['word']} {i['suggest']}")
        print()

    print("-" * 56)
    print("提示：标题规则按 2026-07-27 亚马逊新政（Item name ≤75 / Item Highlights ≤125）；")
    print("      各国站点与类目规则不同，发布前请以目标站点 Seller Central 通知为准人工复核。")


if __name__ == "__main__":
    main()
