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
  5. 后端 Search Terms 字节上限（249 bytes US/UK/EU）
  6. 商品描述长度（2000 字符纯文本）与 HTML 残留

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

# ---------------- 2026 亚马逊政策提示（随自查结果输出） ----------------
POLICY_NOTES_2026 = [
    "标题：2026-07-27 起非 Media 类目 Item name ≤75 字符，新增 Item Highlights（≤125）承接溢出信息；"
    "旧标题不会自动下架，超长会在你提交更新时被平台拆分/改写。",
    "AI 生成人物图：含写实 AI 生成人物的商品图与 A+ 视频，须在上传前于 XMP dc:subject 字段写入 "
    "contains-synthetic-performer（2026-07-27 生效）；真人、非写实形象、无人物图免于此要求。",
    "后端 Search Terms 按字节计（US/UK/EU 249、JP 500、IN 200），超 1 字节即可能静默取消索引。",
    "商品描述 2000 字符纯文本（HTML 自 2021 起已移除）；五点建议 ≤200~255 字符，禁 emoji/保证性措辞/不可验证宣称。",
    "平台可对不合规的标题/五点/描述执行 AI 改写——违规不只是不排名，内容可能被替换成你未写的版本。",
    "Featured Offer（Buy Box）自 2026-07 起取消独立卖家资格门槛，改为持续评估价格/时效/绩效，"
    "竞争加剧，勿为抢位牺牲毛利。",
    "Seller Fulfilled Prime 门槛提高：标准尺寸 1 日达 40%、2 日达 75%（原 70%）、5 日达 90%，"
    "需周末发货且准时率约 93.5%。",
    "BSA 更新（2026-08-24 生效）：禁止转让协议权利义务，或将其（含未来亚马逊拨款）作为质押/担保。",
    "捆绑销售政策全品类化（2026-09-30 生效）：多件装/套装须由原厂或品牌方包装；自行拼装或第三方 prep 组装属不合规，"
    "listing 可能自 9-30 起被压制。例外仅三类：gifting 礼品篮、camera 相机套装、持品牌方 LOA 授权函的套装。",
    "B2B 营业时段送达率 BHDR（2026-09-30 生效）：FBM 发往 Amazon Business 客户的订单须滚动 14 天 ≥90% 在营业时段送达，"
    "FBA 豁免；9-30 未达标先警告，10-30 仍未改善则停用 B2B 的 FBM offer。"
    "同时启用 Automated Handling Time + Shipping Settings Automation + Amazon Buy Shipping 可按政策自动豁免。",
    "商业责任险（2026-11-02 生效）：增强安全类目（11 个类目组，截至 2026-09-04）按「上架即触发」——"
    "月销不足 $10,000 也不豁免，须持 $100 万责任险（单次+累计）、免赔 ≤$10,000、"
    "附加被保险人为 Amazon.com Services LLC and its affiliates and assignees，收到通知后 45 天内提交。",
    "保险渠道（2026-11-02 生效，中国大陆注册主体专属）：新提交保单必须经 Amazon Insurance Accelerator（AIA）获取，"
    "第三方渠道新单平台拒收；11-02 前已提交并审核通过的合规第三方保单可用至到期，续保同样须走 AIA。"
    "未合规则高危类目 listing 直接下架。路径：卖家后台 → 设置 → 账户信息 → 业务信息 → 商业保险。",
    "原本「月销超 $10,000 须 30 天内投保」规则继续有效——上述两项为叠加新增，不是替换。",
    "旺季附加费与超龄库存（2026-10-15 起）：假日旺季配送附加费执行至 2027-01-14，覆盖 FBA/远程配送/多渠道/Buy with Prime，"
    "按仓库出库时间判定、退货不退款；超龄库存附加费计费起点降至 181 天，超 456 天费用跳升。"
    "另：美国站 MCF（多渠道配送）同期加收旺季配送费，小标准件约 $7.71、含锂电池再加 $0.11，偏远地区附加费可达订单费 100%；"
    "原 3.5% 燃油与物流附加费继续叠加，多渠道（Walmart/独立站/Shopify）需重算毛利。",
    "危险品新增问询（2026-10-26 起，美国站）：在四类目组**新建或编辑** listing 时须回答新的危险品问题——"
    "制冷类（冰箱/空调/冷柜，涉冷媒气体性质）、悬挂减震类（减震器/举升支撑，涉机械机构）、"
    "书写工具类（马克笔/书写笔，涉墨水性质）、泳池与 SPA 水处理类（涉化学成分与包装）。"
    "答案须基于实际产品资料（规格书/SDS/成分与浓度/包装形态），不可按宽泛类目或标题推测；"
    "证据须与被上架的**具体型号版本**对应，供应商同类型号声明不能直接替代。"
    "建议先按 ASIN 级建清单并备齐证据包再动 listing。注意亚马逊通知中存在时间表述冲突（另一处提及 2026-08-31），"
    "以「10-26 + 新建或编辑触发」为可操作节点，并以目标站点实时流程复核。",
    "Handling Time 自动化（2026-10-20 起，英国站为首批）：卖家自配送 SKU 若在滚动 30 天内"
    "**设置的处理时间比实际履约表现长 1 天及以上**，平台将自动启用 90 天的自动化处理时间并据此重设，"
    "SKU 全程保持可售可见。两条路径：在配送设置中启用自动化处理时间（推荐，由历史履约持续维护），"
    "或在 SKU 级手动设置并确保与实际表现对齐。建议先查 Handling time report 对账。",
    "Buy Shipping 支持 LTL 大件标（2026-10 起，美国站）：厚重/大件订单可在 Buy Shipping 内购买 LTL 整车零担标，"
    "享亚马逊协议运价与**迟到配送保护**（此前自发货运费自理且延误易计账户健康）。"
    "配套变化：大件区域化配送定价预计 2027 年初推出（按收货地区而非统一费率）；"
    "本地承运商匹配与 Seller Flex 扩展至大件均在规划中。**FBA 费用不受此变化影响**。",
    "各国站点与类目规则不同，发布前请以目标站点 Seller Central 通知为准人工复核。",
]

# ---------------- 标题规范（2026-07-27 亚马逊新政） ----------------
# 非 Media 类目 Item name 上限 75 字符（含空格）；Media（图书/影音/CD）类目仍为 200（用 --title-max 调整）。
TITLE_MAX_LEN = 75
# Item Highlights：2026 新政新增字段，承接标题溢出信息，≤125 字符、可搜索、随标题展示。
HIGHLIGHTS_MAX_LEN = 125
BULLET_MAX_LEN = 500      # 单点描述建议上限（各站点/类目可能更严，可用 --bullet-max 调整）

# 亚马逊标题官方禁用符号（品牌名内含时可例外；2025-01 生效、2026 延续）
FORBIDDEN_SYMBOLS = ["!", "$", "?", "_", "{", "}", "^", "¬", "¦"]

# 后端 Search Terms 字节上限（按【字节】而非字符计；超出 1 字节即可能静默取消该词索引）
# 美国/英国/欧盟 249 bytes，日本 500 bytes，印度 200 bytes。默认取最严的 249。
SEARCH_TERMS_MAX_BYTES = 249
# 商品描述（Standard Product Description）上限：2000 字符、纯文本（HTML 自 2021 起已移除）
DESCRIPTION_MAX_LEN = 2000

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

    # 2.7 后端 Search Terms 字节上限（249 bytes US/UK/EU；JP 500；IN 200）
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
                           "suggest": f"→ 超过 {SEARCH_TERMS_MAX_BYTES} 字节（US/UK/EU 上限；JP 500 / IN 200）。"
                                      f"按字节计，超 1 字节即可能静默取消该词索引"})
        break

    # 2.8 商品描述长度（2000 字符、纯文本）
    for line in text.splitlines():
        m = re.match(r'^\s*(?:description|product\s*description|描述|商品描述)\s*[:：]\s*(.+)$',
                     line.strip(), re.I)
        if not m:
            continue
        desc = m.group(1).strip()
        if len(desc) > DESCRIPTION_MAX_LEN:
            issues.append({"type": "描述", "word": f"长度 {len(desc)} 字符",
                           "category": "商品描述",
                           "suggest": f"→ 超过 {DESCRIPTION_MAX_LEN} 字符上限（纯文本，HTML 自 2021 起已移除）"})
        if HTML_TAG_RE.search(desc):
            issues.append({"type": "描述", "word": "HTML 标签",
                           "category": "商品描述",
                           "suggest": "→ 描述字段仅支持纯文本，HTML 自 2021 起已移除"})
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
                          "count": len(issues),
                          "notes": POLICY_NOTES_2026}, ensure_ascii=False, indent=2))
        return

    print("=" * 56)
    print("跨境 Listing 合规自查")
    print("=" * 56)
    print(f"文件：{args.file}")
    print(f"问题：{len(issues)} 处\n")

    if not issues:
        print("✓ 未发现明显违规（仍建议人工对照目标站点最新规则复核）。")
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
