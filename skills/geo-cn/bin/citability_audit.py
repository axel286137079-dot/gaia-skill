#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
citability_audit.py — 可引用度审计（GEO 九法打分）

纯 Python 标准库实现，无需任何第三方依赖，离线可跑。
复刻 Princeton GEO-bench 的九方法（arXiv:2311.09735），
对一段中文/英文内容打分，输出「被生成式引擎引用的概率」报告。

用法：
    python3 citability_audit.py 文件.md
    python3 citability_audit.py 文件.txt
    echo "内容" | python3 citability_audit.py -     # 从 stdin 读
"""
import re
import sys
from collections import Counter

# ---------------- 九法检测规则 ----------------
# 每项：(名称, 满分, 检测函数, 缺失时的补救建议)

# ① 专家引言：引号内成句 + 专家/权威头衔词
QUOTATION_PAT = re.compile(r'["“”『』「」]([^"“”『』「」]{4,60})["“”『』「」]')
AUTHORITY_TITLES = ["专家", "教授", "博士", "院士", "研究员", "创始人", "CEO", "作者", "学者",
                    "主任", "分析师", "首席", "导师", "表示", "指出", "认为", "强调", "曾说", "说过"]

# ② 数据：数字/百分比/年份
NUMBER_PAT = re.compile(r'\d+(\.\d+)?')
PERCENT_PAT = re.compile(r'\d+(\.\d+)?%')
YEAR_PAT = re.compile(r'(19|20)\d{2}年?')

# ③ 引用来源：链接/来源标注/参考文献
URL_PAT = re.compile(r'https?://[^\s)\]）】]+')
SOURCE_WORDS = ["来源", "参考", "据", "根据", "数据来源", "报告", "白皮书", "论文", "参考文献",
                "引用", "出处", "援引", "披露", "发布", "统计", "调查", "研究表明"]

# ⑤ 权威语气词
AUTHORITATIVE_WORDS = ["研究显示", "研究表明", "数据显示", "证据表明", "结论是", "明确", "证实",
                       "毫无疑问", "实际上", "关键在于", "本质", "核心", "可以确定", "确凿"]

# ⑥ 术语：中英专业词（启发式，可扩展）
TECH_TERMS = ["算法", "架构", "模型", "指标", "参数", "引擎", "协议", "接口", "框架", "机制",
              "阈值", "权重", "训练", "推理", "检索", "归因", "转化率", "留存", "复利", "杠杆",
              "熵", "边际", "效应", "回归", "样本", "变量", "量化", "衰减", "迭代", "闭环"]

# ⑦ 简化词
SIMPLIFY_WORDS = ["也就是说", "换句话说", "简单来说", "打个比方", "比如", "举个例子", "类比",
                  "可以理解为", "通俗地说", "说白了"]

# ⑨ 关键词堆砌：同一 2-4 字词超高频重复
KEYWORD_STUFFING_THRESHOLD = 8


def read_text(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_markdown(text: str) -> str:
    # 去掉 markdown 语法符号，保留正文语义
    text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)   # 图片
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)  # 链接文字
    text = re.sub(r'[#>*_`~|\[\]-]', ' ', text)
    return text


def score_quotation(text: str) -> tuple:
    """① 专家引言"""
    quotes = QUOTATION_PAT.findall(text)
    if not quotes:
        return 0, "开头放一句有出处的权威引言（如「某研究院某主任指出：……」），引用概率可提升约三成"
    # 引言中是否含权威头衔/人名
    has_authority = any(w in text for w in AUTHORITY_TITLES)
    # 引言的完整度
    full = sum(1 for q in quotes if len(q) >= 8)
    score = 8
    if has_authority:
        score += 6
        score += min(6, full * 2)
    else:
        score += min(4, full)
    score = min(score, 20)
    if score >= 16:
        return score, None
    if not has_authority:
        return score, "引言已存在但缺「出处/头衔」，补上「谁说的」让 AI 可归因"
    return score, "引言有出处，可再补 1-2 处不同来源的引言，提升可信度"


def score_statistics(text: str) -> tuple:
    """② 数据"""
    numbers = NUMBER_PAT.findall(text)
    percents = PERCENT_PAT.findall(text)
    years = YEAR_PAT.findall(text)
    score = 0
    if len(numbers) >= 3:
        score += 6
    if percents:
        score += 7
    if years:
        score += 7
    score = min(score, 20)
    if score >= 16:
        return score, None
    return score, "植入 3 处以上可验证数字（百分比/年份/金额），数据是被 AI 转述的头号素材"


def score_sources(text: str) -> tuple:
    """③ 引用来源"""
    urls = URL_PAT.findall(text)
    source_words = sum(1 for w in SOURCE_WORDS if w in text)
    score = 0
    if urls:
        score += 12
    score += min(13, source_words * 2)
    score = min(score, 25)
    if score >= 20:
        return score, None
    return score, "文末加「参考来源」：链接 + 报告/机构名，让 AI 有据可循、敢引用你"


def score_fluency(text: str) -> tuple:
    """④ 流畅度：句长分布"""
    sentences = [s for s in re.split(r'[。！？!?\n]', text) if len(s.strip()) >= 2]
    if not sentences:
        return 0, "内容过短，补足正文后再评"
    avg_len = sum(len(s) for s in sentences) / len(sentences)
    short_ratio = sum(1 for s in sentences if len(s) <= 40) / len(sentences)
    score = 0
    if 15 <= avg_len <= 45:
        score += 3
    elif avg_len < 15:
        score += 1  # 过碎
    if short_ratio >= 0.5:
        score += 3
    if score >= 5:
        return score, None
    return score, "长句拆短、加衔接词（因此/于是/从而），消除歧义，AI 更易转述"


def score_authoritative(text: str) -> tuple:
    """⑤ 权威语气"""
    hits = sum(1 for w in AUTHORITATIVE_WORDS if w in text)
    score = min(6, hits * 2)
    if score >= 4:
        return score, None
    return score, "把「可能/大概/也许」改成肯定式结论，用「数据显示/研究证实」开头"


def score_terms(text: str) -> tuple:
    """⑥ 术语"""
    hits = sum(1 for w in TECH_TERMS if w in text)
    score = min(6, hits)
    if score >= 4:
        return score, None
    return score, "用准确专业名词（如「归因/检索/转化率」）替代大白话，提升权威感"


def score_simplify(text: str) -> tuple:
    """⑦ 简化"""
    hits = sum(1 for w in SIMPLIFY_WORDS if w in text)
    score = min(4, hits)
    if score >= 2:
        return score, None
    return score, "给 1-2 处难点加类比（「说白了，X 就像 Y」），兼顾小白读者"


def score_stuffing(text: str) -> tuple:
    """⑨ 关键词堆砌（有害，扣分）"""
    clean = strip_markdown(text)
    # 提取 2-4 字连续词频
    words = re.findall(r'[\u4e00-\u9fa5]{2,4}', clean)
    counter = Counter(words)
    worst = counter.most_common(5)
    stuffing = [w for w, c in worst if c >= KEYWORD_STUFFING_THRESHOLD]
    if stuffing:
        penalty = -10
        msg = f"检测到关键词堆砌：{', '.join(f'{w}(×{c})' for w, c in worst if c >= KEYWORD_STUFFING_THRESHOLD)} —— 堆砌会降低 AI 引用意愿，请替换同义词"
        return penalty, msg
    return 0, None


def main():
    if len(sys.argv) < 2:
        print("用法：python3 citability_audit.py <文件>  或  echo '内容' | python3 citability_audit.py -")
        sys.exit(1)
    path = sys.argv[1]
    text = read_text(path)
    clean = strip_markdown(text)

    checks = [
        ("① 专家引言", 20, score_quotation(clean)),
        ("② 数据植入", 20, score_statistics(clean)),
        ("③ 引用来源", 25, score_sources(clean)),
        ("④ 流畅度", 12, score_fluency(clean)),
        ("⑤ 权威语气", 10, score_authoritative(clean)),
        ("⑥ 专业术语", 8, score_terms(clean)),
        ("⑦ 通俗简化", 5, score_simplify(clean)),
    ]

    total = 0
    print("=" * 56)
    print("可引用度审计报告（GEO 九法 · 国产引擎版）")
    print("=" * 56)
    for name, full, (score, advice) in checks:
        total += score
        bar = "█" * score + "░" * (full - score)
        print(f"\n{name}  [{score}/{full}] {bar}")
        if advice:
            print(f"   ↳ 建议：{advice}")

    # ⑨ 关键词堆砌（扣分项）
    stuff_score, stuff_msg = score_stuffing(clean)
    if stuff_msg:
        total += stuff_score
        print(f"\n⑨ 关键词堆砌  [{stuff_score}]  ⚠ 有害")
        print(f"   ↳ {stuff_msg}")

    # 等级判定
    total = max(0, min(100, total))
    if total >= 70:
        grade = "🟢 高可引用"
    elif total >= 45:
        grade = "🟡 中等可引用"
    else:
        grade = "🔴 低可引用"

    print("\n" + "=" * 56)
    print(f"总分：{total} / 100   等级：{grade}")
    print("=" * 56)
    print("提示：可验证性 > 风格；引用 + 数据 + 来源 三者叠加效果最佳。")
    print("下一步：运行 multi_engine_rewrite.py 按目标引擎做分诊式改写。")


if __name__ == "__main__":
    main()
