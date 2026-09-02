#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
listing_check.py — 跨境 Listing（亚马逊）合规自查

发布前把 Listing 过一遍，检查：
  1. 违禁词（绝对化/夸大、健康疗效宣称、促销/引流）
  2. 标题规范（长度 ≤200、特殊符号、全大写）

纯标准库，无 API key，离线可跑。

用法：
    python3 listing_check.py listing.md
    python3 listing_check.py listing.md --json
"""
import argparse
import json
import re
import sys

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

# 标题硬性规范
TITLE_MAX_LEN = 200       # 亚马逊标题硬限制
BULLET_MAX_LEN = 500      # 单点描述硬限制
# 亚马逊标题禁止的特殊符号
FORBIDDEN_SYMBOLS = ["!", "?", "$", "&", "~", "*", "<", ">", "|", "{", "}",
                     "[", "]", "#", "@", "^", "%", "=", "+"]


def check(text, title_max=TITLE_MAX_LEN, bullet_max=BULLET_MAX_LEN):
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

    # 2) 标题规范（识别 "Title:" 或 "标题:" 开头的行）
    title_line = None
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r'^(title|标题)\s*[:：]\s*(.+)$', s, re.I)
        if m:
            title_line = m.group(2).strip()
            break
    if title_line is None:
        # 退而求其次：把第一行非空、非 # 开头当标题
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "Title", "标题")):
                title_line = s
                break

    if title_line:
        if len(title_line) > title_max:
            issues.append({"type": "标题", "word": f"长度 {len(title_line)} 字符",
                           "category": "标题规范",
                           "suggest": f"→ 超过 {title_max} 字符，需精简"})
        for sym in FORBIDDEN_SYMBOLS:
            if sym in title_line:
                issues.append({"type": "标题", "word": f"符号「{sym}」",
                               "category": "标题规范",
                               "suggest": "→ 亚马逊标题禁用该符号"})
        # 全大写检测（排除常见缩写）
        words = [w for w in re.findall(r'[A-Za-z]+', title_line) if len(w) > 2]
        if words and all(w.isupper() for w in words):
            issues.append({"type": "标题", "word": "全大写",
                           "category": "标题规范",
                           "suggest": "→ 亚马逊标题禁全大写（除缩写）"})
    else:
        issues.append({"type": "标题", "word": "未找到标题",
                       "category": "标题规范",
                       "suggest": "→ 请用「Title: xxx」或首行写标题以便检查"})

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
                    help="标题长度阈值（默认 200，请按站点/类目调整）")
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

    from collections import Counter
    cat_counter = Counter(i["category"] for i in issues)
    for cat, n in cat_counter.most_common():
        print(f"【{cat}】{n} 处")
        for i in issues:
            if i["category"] != cat:
                continue
            print(f"  · {i['word']} {i['suggest']}")
        print()

    print("-" * 56)
    print("提示：本库为「提醒」非「免责」，各国站点规则不同，发布前请人工复核。")


if __name__ == "__main__":
    main()
