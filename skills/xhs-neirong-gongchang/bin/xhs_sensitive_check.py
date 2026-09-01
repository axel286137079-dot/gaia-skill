#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xhs_sensitive_check.py — 小红书笔记敏感词 / 违禁词自查

发布前把笔记过一遍，查出五类高危词，降低限流 / 封号风险：
  1. 广告法极限词（最/第一/顶级/国家级…）
  2. 医疗绝对化（根治/治愈/永不复发…）
  3. 金融投资诱导（稳赚/保本/零风险/月入过万…）
  4. 引流导流（加微信/私我/淘宝/二维码…）
  5. 教育培训夸大（包过/保过/押题…）

纯标准库，无 API key，离线可跑。

用法：
    python3 xhs_sensitive_check.py 笔记.md
    python3 xhs_sensitive_check.py 笔记.md --json     # JSON 输出，便于 agent 消费
"""
import argparse
import json
import re
import sys

# ---------------- 违禁词库 ----------------
# 每类 = [(词, 替换建议)]
RULES = {
    "广告法极限词": [
        ("最好", "→ 较好/更好"), ("最佳", "→ 优选"), ("最大", "→ 较大"),
        ("最小", "→ 较小"), ("最高", "→ 较高"), ("最低", "→ 较低"),
        ("最强", "→ 更强"), ("最全", "→ 较全"), ("最优", "→ 较优"),
        ("最棒", "→ 很棒"), ("最先进", "→ 较先进"), ("顶级", "→ 高端"),
        ("极品", "→ 优质"), ("极致", "→ 出色"), ("国家级", "→ 行业标杆"),
        ("世界级", "→ 领先"), ("第一", "→ 领先/前列"), ("首选", "→ 推荐"),
        ("唯一", "→ 少数"), ("独家", "→ 自有"), ("万能", "→ 通用"),
        ("永久", "→ 长期"), ("100%", "→ 绝大部分"), ("百分百", "→ 绝大部分"),
        ("史无前例", "→ 少见"), ("绝无仅有", "→ 少有"), ("销量第一", "→ 销量领先"),
        ("全网第一", "→ 广受好评"), ("全球第一", "→ 国际领先"), ("冠军", "→ 前列"),
        ("No.1", "→ 前列"), ("NO.1", "→ 前列"),
    ],
    "医疗绝对化": [
        ("根治", "→ 改善"), ("治愈", "→ 缓解"), ("根除", "→ 减轻"),
        ("永不复发", "→ 减少复发"), ("药到病除", "→ 有帮助"), ("包治百病", "→ 不可取"),
        ("无副作用", "→ 副作用少"), ("零副作用", "→ 副作用少"), ("一针见效", "→ 效果因人而异"),
        ("彻底治愈", "→ 改善"), ("保证治好", "→ 不可保证"), ("断根", "→ 改善"),
        ("三天见效", "→ 效果因人而异"), ("7天见效", "→ 效果因人而异"),
        ("无效退款", "→ 请以实际为准"), ("立竿见影", "→ 效果因人而异"),
    ],
    "金融投资诱导": [
        ("稳赚", "→ 收益有波动"), ("稳赚不赔", "→ 收益有波动"), ("保本", "→ 不保证本金"),
        ("保收益", "→ 不保证收益"), ("零风险", "→ 有风险"), ("无风险", "→ 有风险"),
        ("高收益", "→ 收益有波动"), ("躺赚", "→ 需投入"), ("躺着赚钱", "→ 需投入"),
        ("月入过万", "→ 收入因人而异"), ("日入过千", "→ 收入因人而异"), ("日赚", "→ 收入因人而异"),
        ("一夜暴富", "→ 不可取"), ("必涨", "→ 可能上涨"), ("稳涨", "→ 或上涨"),
        ("翻倍", "→ 可能增长"), ("包赚", "→ 不保证"), ("躺赢", "→ 需投入"),
        ("内部消息", "→ 不可取"), ("内幕", "→ 不可取"),
    ],
    "引流导流": [
        ("加微信", "→ 删除"), ("加V", "→ 删除"), ("加v", "→ 删除"),
        ("➕V", "→ 删除"), ("+V", "→ 删除"), ("私我", "→ 删除"),
        ("私聊", "→ 删除"), ("私信我", "→ 删除"), ("主页链接", "→ 删除"),
        ("链接在主页", "→ 删除"), ("VX", "→ 删除"), ("vx", "→ 删除"),
        ("淘宝", "→ 删除"), ("拼多多", "→ 删除"), ("闲鱼", "→ 删除"),
        ("微店", "→ 删除"), ("二维码", "→ 删除"), ("扫码", "→ 删除"),
        ("关注公众号", "→ 删除"), ("公众号", "→ 谨慎/删除"),
    ],
    "教育培训夸大": [
        ("包过", "→ 助你通过"), ("保过", "→ 助你通过"), ("必过", "→ 助你通过"),
        ("必考", "→ 常考"), ("押题", "→ 高频考点"), ("押中", "→ 命中考点"),
        ("命中率100%", "→ 高频考点"), ("原题", "→ 真题"), ("不过退费", "→ 请以合同为准"),
        ("一次通过", "→ 助你通过"), ("零基础包会", "→ 零基础也能学"),
    ],
}

# 「最 + 形容词」前缀模式（广告法绝对化，需人工判断语境）
SUPERLATIVE_PREFIX = re.compile(r'最(?:好|佳|优|高|强|低|大|多|少|快|慢|棒|牛|全|精|厉害|先进|顶级|便宜|划算)')


def check(text):
    """扫描文本，返回命中列表 [{word, category, suggest, line, col}]"""
    hits = []
    lines = text.splitlines()
    for lineno, line in enumerate(lines, 1):
        # 1) 词库精确命中
        for cat, words in RULES.items():
            for word, suggest in words:
                if word in line:
                    col = line.index(word) + 1
                    hits.append({
                        "word": word, "category": cat, "suggest": suggest,
                        "line": lineno, "col": col,
                    })
        # 2) 「最 + 形容词」前缀
        for m in SUPERLATIVE_PREFIX.finditer(line):
            hits.append({
                "word": m.group(0), "category": "广告法极限词",
                "suggest": "→ 若表绝对化，改为「较/更」；若为日常口语（如「最近」）可忽略",
                "line": lineno, "col": m.start() + 1,
            })
    # 去重（同词同行只报一次）
    seen = set()
    uniq = []
    for h in hits:
        key = (h["word"], h["category"], h["line"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(h)
    return uniq


def main():
    ap = argparse.ArgumentParser(description="小红书笔记敏感词/违禁词自查")
    ap.add_argument("file", help="笔记文件路径（.md/.txt）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 便于 agent 消费")
    args = ap.parse_args()

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"✗ 找不到文件：{args.file}", file=sys.stderr)
        sys.exit(1)

    hits = check(text)

    if args.json:
        print(json.dumps({"file": args.file, "hits": hits,
                          "count": len(hits)}, ensure_ascii=False, indent=2))
        return

    print("=" * 56)
    print("小红书敏感词自查")
    print("=" * 56)
    print(f"文件：{args.file}")
    print(f"命中：{len(hits)} 处\n")

    if not hits:
        print("✓ 未命中已知违禁词（仍建议人工复核平台最新规则）。")
        return

    from collections import Counter
    cat_counter = Counter(h["category"] for h in hits)
    for cat, n in cat_counter.most_common():
        print(f"【{cat}】{n} 处")
        for h in hits:
            if h["category"] != cat:
                continue
            print(f"  第{h['line']}行 「{h['word']}」 {h['suggest']}")
        print()

    print("-" * 56)
    print("提示：本库为「提醒」非「免责」，发布前仍建议对照平台最新规则人工复核。")


if __name__ == "__main__":
    main()
