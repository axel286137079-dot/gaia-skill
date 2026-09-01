#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
source_audit.py — 中文深度研究报告的信源可信度审计 + 反幻觉检查

对一份 Markdown 研究报告做两件事：
1. 信源分级：抽出报告里所有 URL/链接，按域名归入「四级信源」（官方/学术=一级，
   权威媒体/垂直平台=二级，行业自媒体=三级，匿名论坛=四级），统计各级占比。
2. 反幻觉警示：找出「含具体数字却无出处」的句子、未分级域名、疑似单一信源。

纯标准库，无 API key，离线可跑。

用法：
    python3 source_audit.py 报告.md
    python3 source_audit.py 报告.md --min-sources 3   # 自定义「单一信源」阈值
"""
import argparse
import re
import sys
from collections import Counter
from urllib.parse import urlparse

# ---------------- 信源域名 → 级别映射 ----------------
# 匹配规则：先精确匹配，再按后缀匹配（子域名归属）。
# tier 1=最高(官方/学术/一手) 2=权威媒体/垂直权威 3=行业自媒体/社区 4=匿名论坛(不作论据)

TIER_1 = [
    # 官方 / 政府（含各子域）
    "gov.cn", "stats.gov.cn", "pbc.gov.cn", "mof.gov.cn", "ndrc.gov.cn",
    "mofcom.gov.cn", "miit.gov.cn", "court.gov.cn", "wenshu.court.gov.cn",
    "csrc.gov.cn", "cbirc.gov.cn", "samr.gov.cn", "customs.gov.cn",
    "npc.gov.cn", "sc.gov.cn", "english.gov.cn",
    # 学术
    "cnki.net", "wanfangdata.com.cn", "cqvip.com", "arxiv.org",
    "edu.cn", "sciencedirect.com", "ieee.org", "nature.com", "springer.com",
    "jstor.org", "semanticscholar.org",
    # 一手数据 / 公告
    "cninfo.com.cn", "sse.com.cn", "szse.cn", "bse.cn", "chinabond.com.cn",
    "sec.gov", "hkex.com.hk", "ndrc.gov.cn",
]

TIER_2 = [
    # 权威媒体
    "xinhuanet.com", "news.cn", "people.com.cn", "cctv.com", "cctvnews.cn",
    "chinanews.com", "chinadaily.com.cn", "caixin.com", "yicai.com",
    "thepaper.cn", "jiemian.com", "21jingji.com", "stcn.com", "cs.com.cn",
    "nbd.com.cn", "eastmoney.com", "10jqka.com.cn", "cls.cn",
    "finance.sina.com.cn", "money.163.com", "business.sohu.com",
    # 垂直权威平台
    "tianyancha.com", "qcc.com", "iresearch.com.cn", "idc.com", "199it.com",
    "statista.com", "wind.com.cn", "choice.eastmoney.com",
]

TIER_3 = [
    "zhihu.com", "mp.weixin.qq.com", "xueqiu.com", "huxiu.com", "36kr.com",
    "jiqizhixin.com", "leiphone.com", "ithome.com", "geekpark.net",
    "sina.com.cn", "163.com", "sohu.com", "toutiao.com", "bilibili.com",
    "douyin.com", "xiaohongshu.com", "weibo.com", "juejin.cn", "csdn.net",
    "zhihu.com",
]

TIER_4 = [
    "tieba.baidu.com", "douban.com", "zhidao.baidu.com", "baijiahao.baidu.com",
]


def classify_domain(domain):
    """按域名返回 (tier, label)。domain 形如 'www.stats.gov.cn'。"""
    domain = (domain or "").lower().strip()
    if not domain:
        return (0, "空域名")
    # 精确匹配
    for d in TIER_1:
        if domain == d or domain.endswith("." + d):
            return (1, "官方/学术/一手")
    for d in TIER_2:
        if domain == d or domain.endswith("." + d):
            return (2, "权威媒体/垂直平台")
    for d in TIER_3:
        if domain == d or domain.endswith("." + d):
            return (3, "行业自媒体/社区")
    for d in TIER_4:
        if domain == d or domain.endswith("." + d):
            return (4, "匿名论坛/社交片段")
    return (0, "未分级")


def extract_urls(text):
    """抽出所有 URL（markdown 链接 + 裸 URL），去重保序，返回 [url]"""
    urls = []
    seen = set()
    # markdown 链接 [text](url)
    for m in re.finditer(r'\[[^\]]*\]\((https?://[^\s\)]+)\)', text):
        u = m.group(1).rstrip('.,;:，。；：')
        if u not in seen:
            seen.add(u)
            urls.append(u)
    # 裸 URL
    for m in re.finditer(r'https?://[^\s<>"\'\)\]，。；：]+', text):
        u = m.group(0).rstrip('.,;:，。；：')
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def split_sentences(text):
    """按中文句末标点/换行切句，去掉 markdown 结构行。"""
    # 去掉代码块、表格、标题行
    lines = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        s = line.strip()
        if not s or s.startswith(("#", "|", "-", "*", ">", "```")):
            continue
        lines.append(s)
    blob = " ".join(lines)
    parts = re.split(r'(?<=[。！？；!?;])', blob)
    return [p.strip() for p in parts if p.strip()]


def has_number(s):
    """句子是否含具体数字/百分比/年份（作为事实性陈述的启发式信号）

    覆盖「1.2 亿」「8 亿」「35%」「1200 元」「2025 年」等常见量化表述，
    避免漏掉单数字 + 单位（亿/万/倍/元/家/人/台/个）的情况。
    """
    if re.search(r'\d+(?:\.\d+)?\s*[%亿万倍元家人员台个斤吨亩兆]', s):
        return True
    if re.search(r'\d{3,}', s):
        return True
    if re.search(r'(?:20|19)\d{2}\s*年', s):
        return True
    return False


def has_citation(s):
    """句子是否带出处标记：[来源N] / [N] / [来源:xx] / (来源:xx) / URL"""
    if re.search(r'\[(来源|source|ref)?\s*:?[^\]]{0,12}\]', s, re.I):
        return True
    if re.search(r'[（(]\s*来源[:：]', s):
        return True
    if re.search(r'https?://', s):
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description="中文深度研究报告·信源可信度审计")
    ap.add_argument("report", help="Markdown 报告文件路径")
    ap.add_argument("--min-sources", type=int, default=2,
                    help="独立信源低于此数即警示「疑似单一信源」(默认2)")
    args = ap.parse_args()

    try:
        with open(args.report, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"✗ 找不到文件：{args.report}", file=sys.stderr)
        sys.exit(1)

    # ---- 1. 信源分级 ----
    urls = extract_urls(text)
    tier_counter = Counter()
    tier_detail = []
    for u in urls:
        dom = urlparse(u).netloc
        tier, label = classify_domain(dom)
        tier_counter[tier] += 1
        tier_detail.append((dom, tier, label, u))

    total_urls = len(urls)
    print("=" * 56)
    print("信源可信度审计报告")
    print("=" * 56)
    print(f"报告文件：{args.report}")
    print(f"检测到 URL：{total_urls} 个（去重后）\n")

    if total_urls == 0:
        print("⚠ 报告里没有任何可点击链接——所有结论都「无出处」，请补引用。")
    else:
        labels = {1: "一级·官方/学术/一手", 2: "二级·权威媒体/垂直",
                  3: "三级·行业自媒体/社区", 4: "四级·匿名论坛", 0: "未分级"}
        print("[信源分布]")
        for tier in (1, 2, 3, 4, 0):
            n = tier_counter.get(tier, 0)
            if n:
                pct = n * 100.0 / total_urls
                print(f"  {labels[tier]:<20} {n:>3} 个  ({pct:>5.1f}%)")

        # 未分级域名
        unknown = [d for d, t, l, u in tier_detail if t == 0]
        if unknown:
            print("\n[未分级域名]（建议人工确认可信度）")
            for d in sorted(set(unknown)):
                print(f"  · {d}")

        # 四级提示
        tier4 = [d for d, t, l, u in tier_detail if t == 4]
        if tier4:
            print("\n⚠ 出现四级信源（匿名论坛/社交片段）——只可作线索，不可作论据：")
            for d in sorted(set(tier4)):
                print(f"  · {d}")

        # 公众号提示
        weixin = [d for d, t, l, u in tier_detail if "weixin" in d]
        if weixin:
            print("\nℹ 公众号链接（mp.weixin.qq.com）归为三级，但实际可信度取决于主体：")
            print("  官方机构/权威媒体公众号可升二级，个人号需交叉验证。")

    # ---- 2. 反幻觉警示 ----
    print("\n" + "-" * 56)
    print("[反幻觉检查]")
    sentences = split_sentences(text)
    uncited_num = [s for s in sentences if has_number(s) and not has_citation(s)]
    if uncited_num:
        print(f"⚠ 发现 {len(uncited_num)} 句「含具体数字但无出处」的陈述（建议补引用或标待核实）：")
        for s in uncited_num[:8]:
            print(f"  · {s[:60]}{'…' if len(s) > 60 else ''}")
        if len(uncited_num) > 8:
            print(f"  … 其余 {len(uncited_num) - 8} 句略")
    else:
        print("✓ 含数字的陈述基本都有出处标注。")

    if total_urls and total_urls < args.min_sources:
        print(f"⚠ 独立信源仅 {total_urls} 个（<{args.min_sources}），核心结论疑似单一信源，需交叉验证。")
    elif total_urls:
        print(f"✓ 独立信源 {total_urls} 个，达到最低交叉验证门槛。")

    # 三级信源占比提醒
    t3 = tier_counter.get(3, 0)
    if total_urls and t3 * 100.0 / total_urls > 50:
        print("⚠ 三级信源占比 >50%，整体可信度偏低，建议补充一/二级信源。")

    print("\n" + "=" * 56)
    # 综合结论
    score = 0
    if total_urls >= 2:
        score += 2
    t1 = tier_counter.get(1, 0)
    t2 = tier_counter.get(2, 0)
    if t1 + t2 > 0:
        score += 2
    if not uncited_num:
        score += 2
    if not tier_counter.get(4, 0):
        score += 1
    verdict = {7: "优秀", 6: "良好", 5: "合格", 4: "勉强", 3: "偏弱", 2: "弱", 1: "弱", 0: "不合格"}
    print(f"综合可信度评分：{score}/7（{verdict.get(score, '待改进')}）")
    print("评分维度：信源数≥2、含一/二级信源、数字有出处、无四级信源。")


if __name__ == "__main__":
    main()
