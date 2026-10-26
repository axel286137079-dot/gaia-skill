#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
listing_check.py — 跨境 Listing 离线格式与措辞检查

发布前把 Listing 过一遍，检查：
  1. 需人工复核的措辞（绝对化/夸大、健康疗效宣称、促销/引流）
  2. 标题长度及重复词、符号、全大写启发式检查
  3. Item Highlights 长度提示
  4. 五点长度、全大写
  5. 后端 Search Terms 默认 249 字节预警值
  6. 商品描述默认 2000 字符预警值与 HTML 残留

纯标准库，无 API key，离线可跑。

用法：
    python3 listing_check.py listing.md
    python3 listing_check.py listing.md --json
    # 其他站点和类目须按实时规则自行调整阈值
"""
import argparse
import json
import re
import sys
from collections import Counter

# ---------------- 需复核措辞库（不是平台违规结论） ----------------
RULES = {
    "绝对化/夸大词（需复核）": [
        "best", "best-selling", "bestseller", "#1", "no.1", "number one",
        "top-rated", "top rated", "top seller", "guaranteed", "100%",
        "perfect", "amazing", "incredible", "ultimate", "revolutionary",
        "miracle", "world's best", "best in the world",
    ],
    "健康疗效宣称（需核对资质）": [
        "cure", "cures", "treat", "treats", "heal", "heals", "prevent",
        "prevents", "anti-cancer", "antibacterial", "antifungal",
        "antiviral", "fda approved", "fda-approved", "medical grade",
        "clinical", "therapeutic", "pain relief", "kills bacteria",
        "kills virus", "boosts immunity", "detox",
    ],
    "促销/引流（需复核字段规则）": [
        "sale", "discount", "discounted", "cheap", "cheapest", "clearance",
        "free shipping", "best price", "lowest price", "promo", "coupon",
        "contact us", "visit our website", ".com", "money back",
        "satisfaction guaranteed",
    ],
}

# ---------------- 2026 亚马逊政策提示（随自查结果输出） ----------------
POLICY_NOTES_2026 = [
    "Amazon 美国站非 Media 标题与 Item Highlights：官方 2026-07-27 更新分别为 75 与 125 字符；"
    "其他站点/类目请按实时规则配置阈值。"
    "来源：https://sellercentral.amazon.com/seller-forums/discussions/t/145b6d0f-999c-4555-896c-c694bda2e470",
    "写实 AI 生成人物图片或视频上传 Amazon 前应检查 XMP dc:subject 的 "
    "contains-synthetic-performer 标记；真人或不含写实 AI 人物的素材不适用。"
    "来源：https://sellercentral.amazon.com/seller-forums/discussions/t/aa0aee06-aff4-497a-a4b6-9b2ebe06f715",
    "美国 Amazon Business FBM 营业时段送达率 BHDR：官方自 2026-09-30 起要求 14 天滚动至少 90%；"
    "实际账户状态与适用范围以 Seller Central 为准。"
    "来源：https://sellercentral.amazon.com/seller-forums/discussions/t/57222b70-40df-4574-aa0f-9f815c197987",
    "捆绑销售扩展规则（2027-01-11 生效）：届时多数单 ASIN 捆绑品须由原厂或品牌方包装；"
    "合格 LOA 授权重包装、gifting 浏览节点、camera 产品类型和符合条件的 Virtual Bundles 可例外。"
    "现行消耗品等类目规则另需核对；不要把 2026-09-30 当作扩展规则生效日，也不要仅凭本提示提前下架。"
    "来源：https://sellercentral.amazon.com/seller-forums/discussions/t/da850a57-839d-4e38-a96f-298dbc450b69",
    "美国站四组危险品相关类目新建或编辑 Listing：官方公告自 2026-10-26 起增加问询；"
    "实际产品资料、型号与 Seller Central 问题须逐项对应。"
    "来源：https://sellercentral.amazon.com/seller-forums/discussions/t/1dd5ad3d-52e6-4e75-ad05-8ae21cdc84f6",
    "增强安全类目责任险与中国大陆主体 AIA 新保单要求：官方公告生效日为 2026-11-02；"
    "具体类目和保单条件须以账户通知为准，本工具不提供投保决策。"
    "来源：https://sellercentral.amazon.com/seller-forums/discussions/t/287d5c33-8278-4802-ba69-2f58f7dda408",
    "欧盟 FBA/MCF 燃油与物流附加费上调：2026-11-02 起英国、法国、德国、意大利、西班牙、波兰、瑞典、荷兰、爱尔兰、比利时"
    "的 FBA 配送费附加费由 1.5% 升至 2%；2026-11-19 起同幅度适用于上述前五国的 MCF。"
    "附加费按配送费而非售价计算，欧盟 FBA 平均约每件 +£0.02/€0.02，随尺寸重量浮动——自行估算利润时须计入，"
    "不能只看商品售价与基础配送费。"
    "来源：https://sellercentral.amazon.com/seller-forums/discussions/t/7273b612-67c1-44db-bfba-24eb6f3a46d9",
    "英国站自配送处理时间（Handling Time）自动化：30 天滚动窗口内设置的备货时间比实际履约长 1 天及以上时，"
    "系统自动启用 90 天处理时间调整。填备货时间应按真实出库能力，不要留安全余量。",
    "美国站危险品（Hazmat）新增问询：制冷/悬挂减震/书写工具/泳池与 SPA 四个相关类目组，"
    "在新建或编辑 listing 时须依实际产品资料（规格书、SDS、成分、包装）作答，不得按类目或标题推测。",
    "这些是 2026-10-26 核对的部分官方公告，不包含全部站点、类目和费用；"
    "发布前必须复核目标站点 Seller Central、承运商和海关官方页面。"
]

# ---------------- 本地检查阈值 ----------------
# 默认 75 仅针对已核实公告中的美国站非 Media；其他范围请用 --title-max 调整。
TITLE_MAX_LEN = 75
# 美国站相应公告中的 Item Highlights 参考值；其余范围须重核。
HIGHLIGHTS_MAX_LEN = 125
BULLET_MAX_LEN = 500      # 单点描述建议上限（各站点/类目可能更严，可用 --bullet-max 调整）

# 容易造成字段问题的符号；是否允许仍由目标站点决定。
FORBIDDEN_SYMBOLS = ["!", "$", "?", "_", "{", "}", "^", "¬", "¦"]

# 默认预警值，不宣称覆盖所有站点或类目的实际上限。
SEARCH_TERMS_MAX_BYTES = 249
# 默认预警值，须按目标字段重核。
DESCRIPTION_MAX_LEN = 2000

# emoji / 装饰字符启发式检查
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
# 重复标点：!!!  ...  ??? 等
REPEATED_PUNCT_RE = re.compile(r"([!?.])\1{2,}")
# HTML 标签
HTML_TAG_RE = re.compile(r"<[a-zA-Z/][^>]*>")

# 对实词重复三次及以上给出启发式提示；冠词/介词/连词豁免
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
    # 1) 需复核措辞（大小写不敏感，整词匹配）
    lower = text.lower()
    for cat, words in RULES.items():
        for w in words:
            # 整词边界匹配，避免 "best" 命中 "bestbuy" 之类
            pattern = r'(?<![a-z0-9])' + re.escape(w) + r'(?![a-z0-9])'
            if re.search(pattern, lower):
                issues.append({"type": "需复核措辞", "word": w, "category": cat,
                               "suggest": "→ 核对事实依据、上下文和目标站点规则；必要时改写"})

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
        # 2.1 标题长度：当前设定由调用者负责确认适用范围
        if len(title_line) > title_max:
            issues.append({"type": "标题", "word": f"长度 {len(title_line)} 字符",
                           "category": "标题规范",
                           "suggest": f"→ 超过当前设定 {title_max} 字符；请核对目标站点及类目"})
        # 2.2 需复核的符号
        for sym in FORBIDDEN_SYMBOLS:
            if sym in title_line:
                issues.append({"type": "标题", "word": f"符号「{sym}」",
                               "category": "标题规范",
                               "suggest": "→ 核对目标站点的标题字段要求（品牌名可能例外）"})
        # 2.3 emoji / HTML / 重复标点
        if EMOJI_RE.search(title_line):
            issues.append({"type": "标题", "word": "emoji/装饰字符",
                           "category": "标题规范",
                           "suggest": "→ 检查目标站点是否允许 emoji 或装饰字符"})
        if HTML_TAG_RE.search(title_line):
            issues.append({"type": "标题", "word": "HTML 标签",
                           "category": "标题规范", "suggest": "→ 删除标题中的 HTML 并核对字段要求"})
        for m in REPEATED_PUNCT_RE.finditer(title_line):
            issues.append({"type": "标题", "word": f"重复标点「{m.group(0)}」",
                           "category": "标题规范",
                           "suggest": "→ 删除无意义的重复标点并核对字段要求"})
        # 2.4 同一实词重复 ≥3 次
        for w, n in _dup_word_issues(title_line):
            issues.append({"type": "标题", "word": f"实词「{w}」×{n}",
                           "category": "标题规范",
                           "suggest": f"→ 实词重复 {n} 次；检查是否需要精简"})
        # 2.5 全大写检测（排除常见缩写）
        words = [w for w in re.findall(r'[A-Za-z]+', title_line) if len(w) > 2]
        if words and all(w.isupper() for w in words):
            issues.append({"type": "标题", "word": "全大写",
                           "category": "标题规范",
                           "suggest": "→ 核对目标站点大小写规则（缩写可能例外）"})
    else:
        issues.append({"type": "标题", "word": "未找到标题",
                       "category": "标题规范",
                       "suggest": "→ 请用「Title: xxx」或首行写标题以便检查"})

    # 2.6 Item Highlights 长度
    if highlights_line is not None:
        if len(highlights_line) > highlights_max:
            issues.append({"type": "亮点", "word": f"长度 {len(highlights_line)} 字符",
                           "category": "Item Highlights",
                           "suggest": f"→ 超过当前设定 {highlights_max} 字符；请核对目标字段"})
    elif title_line:
        issues.append({"type": "提示", "word": "未发现 Item Highlights 字段",
                       "category": "字段提示",
                       "suggest": "→ 标题溢出/材质/兼容/场景信息可写入 Item Highlights "
                                  "。用「Highlights: xxx」行可启用其长度检查；先核对站点规则"})

    # 2.7 后端 Search Terms 字节预警值
    for line in text.splitlines():
        m = re.match(r'^\s*(?:search\s*terms?|backend\s*keywords?|后端搜索词|搜索词)\s*[:：]\s*(.+)$',
                     line.strip(), re.I)
        if not m:
            continue
        terms = m.group(1).strip()
        nbytes = len(terms.encode("utf-8"))
        if nbytes > SEARCH_TERMS_MAX_BYTES:
            issues.append({"type": "搜索词", "word": f"{nbytes} 字节",
                           "category": "后端 Search Terms",
                           "suggest": f"→ 超过默认预警值 {SEARCH_TERMS_MAX_BYTES} 字节；"
                                      f"请核对目标站点的实际上限"})
        break

    # 2.8 商品描述默认长度预警值
    for line in text.splitlines():
        m = re.match(r'^\s*(?:description|product\s*description|描述|商品描述)\s*[:：]\s*(.+)$',
                     line.strip(), re.I)
        if not m:
            continue
        desc = m.group(1).strip()
        if len(desc) > DESCRIPTION_MAX_LEN:
            issues.append({"type": "描述", "word": f"长度 {len(desc)} 字符",
                           "category": "商品描述",
                           "suggest": f"→ 超过默认预警值 {DESCRIPTION_MAX_LEN} 字符；请核对目标字段"})
        if HTML_TAG_RE.search(desc):
            issues.append({"type": "描述", "word": "HTML 标签",
                           "category": "商品描述",
                           "suggest": "→ 描述中包含 HTML；请核对目标字段是否允许"})
        break

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
    ap = argparse.ArgumentParser(description="跨境 Listing 离线格式与措辞检查")
    ap.add_argument("file", help="Listing 文件路径（.md/.txt）")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--title-max", type=int, default=TITLE_MAX_LEN,
                    help="标题长度预警值（默认 75；其他站点和类目请按实时规则设置）")
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
                          "count": len(issues),
                          "notes": POLICY_NOTES_2026}, ensure_ascii=False, indent=2))
        return

    print("=" * 56)
    print("跨境 Listing 离线格式与措辞检查")
    print("=" * 56)
    print(f"文件：{args.file}")
    print(f"问题：{len(issues)} 处\n")

    if not issues:
        print("✓ 未命中本地检查项；这不代表平台审核或法律合规。")
    else:
        cat_counter = Counter(i["category"] for i in issues)
        for cat, n in cat_counter.most_common():
            print(f"【{cat}】{n} 处")
            for i in issues:
                if i["category"] != cat:
                    continue
                print(f"  · {i['word']} {i['suggest']}")
            print()

    print("-" * 56)
    print("【2026 亚马逊政策提示】")
    for note in POLICY_NOTES_2026:
        print(f"  · {note}")


if __name__ == "__main__":
    main()
