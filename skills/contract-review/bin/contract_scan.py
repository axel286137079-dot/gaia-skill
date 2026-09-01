#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
contract_scan.py — 中文合同高风险条款扫描（规则化）

扫描合同文本里的高风险/中风险条款，按等级输出 + 白话建议。
纯规则匹配，本地离线，无 API key。

⚠️ 本工具仅做风险提示，不构成法律意见，不替代律师。

用法：
    python3 contract_scan.py 合同.txt
    python3 contract_scan.py 合同.txt --json
    python3 contract_scan.py 合同.txt --context 30   # 命中词前后各 30 字
"""
import argparse
import json
import re
import sys

# ---------------- 风险词库 ----------------
# 每类 = (等级, [(关键词, 白话解释+建议)])
# 等级：高 / 中 / 低
HIGH_RISK = [
    ("单方解除权", [
        ("无条件解除", "对方可无理由终止合同，你没有主动权 → 改为「双方协商一致可解除」"),
        ("随时解除", "同上，对方随时可走 → 加解除须提前通知+承担违约责任"),
        ("任意解除", "同上 → 限制解除条件"),
        ("单方解除", "单方即可解除 → 明确解除情形，对等设置"),
        ("单方面终止", "同上 → 对等设置终止权"),
    ]),
    ("无限/连带责任", [
        ("连带责任", "你需与对方一起对外担责 → 争取改为按份/一般保证"),
        ("无限责任", "责任无上限，风险敞口大 → 约定赔偿上限或比例"),
        ("不设上限", "赔偿不设上限 → 设置违约金上限"),
        ("全额赔偿", "可能要求全额赔 → 限定赔偿范围"),
    ]),
    ("免责条款", [
        ("概不负责", "对方全部免责，风险全由你扛 → 删或限定免责范围"),
        ("不承担任何责任", "同上 → 明确例外情形"),
        ("不承担一切责任", "同上 → 删除「一切/任何」绝对化表述"),
        ("免责", "免责范围可能过宽 → 明确哪些情形不免责"),
    ]),
    ("自动续约", [
        ("自动续约", "到期不主动停就自动续 → 改为「到期需双方书面确认续约」"),
        ("自动顺延", "同上 → 加提前通知解除条款"),
        ("自动延期", "同上 → 明确到期日+提前通知"),
    ]),
    ("独占/排他", [
        ("独家", "独家授权把你锁死 → 明确独家范围/期限/对价"),
        ("排他", "排他条款限制你与他人合作 → 争取非排他或加对价"),
        ("独占", "同上 → 限定范围和期限"),
        ("权利归甲方", "成果/权利单方归对方 → 争取共有或明确归属对价"),
        ("知识产权归", "知识产权归属需明确 → 看清归谁、是否合理"),
    ]),
    ("管辖/争议", [
        ("仲裁", "仲裁一裁终局、费用高 → 确认仲裁机构/规则是否对等"),
        ("管辖法院", "管辖地对你不利会增加诉讼成本 → 争取己方所在地或被告所在地"),
        ("争议解决", "看清争议解决方式是否对等 → 争取中立/己方便利"),
    ]),
]

MID_RISK = [
    ("模糊表述", [
        ("合理期限", "「合理」不可量化，纠纷时说不清 → 明确具体天数"),
        ("及时", "「及时」含糊 → 明确 X 个工作日内"),
        ("尽快", "「尽快」含糊 → 明确具体时限"),
        ("尽力", "「尽力」非义务，难追责 → 改为明确义务"),
        ("适当", "「适当」含糊 → 明确标准/比例"),
        ("必要时", "「必要时」含糊 → 明确触发条件"),
    ]),
    ("保证金/押金", [
        ("保证金", "看清保证金金额与退还条件 → 明确退还时限和条件"),
        ("押金", "同上 → 明确退还情形"),
        ("定金", "注意「定金」与「订金」法律后果不同 → 定金适用定金罚则"),
    ]),
    ("付款/账期", [
        ("预付款", "预付款比例过高 → 争取降低或分期"),
        ("先付款", "先款后货有风险 → 争取货到/验收后付款"),
        ("账期", "账期过长占用资金 → 争取缩短"),
        ("逾期", "逾期责任是否对等 → 明确逾期罚则"),
    ]),
]

LOW_RISK = [
    ("格式提示", [
        ("最终解释权", "「最终解释权归XX」多为无效格式条款 → 可要求删除"),
        ("以合同为准", "注意「以合同为准」可能排除前期承诺 → 重要承诺写入正文"),
    ]),
]


def scan(text, context=24):
    """扫描风险，返回命中列表。"""
    hits = []
    for level, groups in (("高", HIGH_RISK), ("中", MID_RISK), ("低", LOW_RISK)):
        for cat, words in groups:
            for kw, advise in words:
                for m in re.finditer(re.escape(kw), text):
                    start = max(0, m.start() - context)
                    end = min(len(text), m.end() + context)
                    ctx = text[start:end].replace("\n", " ").strip()
                    hits.append({
                        "level": level, "category": cat, "keyword": kw,
                        "advice": advise, "pos": m.start(),
                        "context": ctx,
                    })
    return hits


def main():
    ap = argparse.ArgumentParser(description="中文合同高风险条款扫描（非法律意见）")
    ap.add_argument("file", help="合同文件路径（.txt/.md）")
    ap.add_argument("--context", type=int, default=24, help="命中词前后字数")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    try:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"✗ 找不到文件：{args.file}", file=sys.stderr)
        sys.exit(1)

    hits = scan(text, args.context)

    if args.json:
        print(json.dumps({"file": args.file, "hits": hits,
                          "count": len(hits)}, ensure_ascii=False, indent=2))
        return

    print("=" * 60)
    print("合同高风险条款扫描（非法律意见，不替代律师）")
    print("=" * 60)
    print(f"文件：{args.file}")
    print(f"命中：{len(hits)} 处\n")

    if not hits:
        print("✓ 未命中已知高风险表述。")
        print("  仍需注意：本工具只覆盖常见风险词，不能替代人工逐条审阅。")
        return

    order = {"高": 0, "中": 1, "低": 2}
    hits_sorted = sorted(hits, key=lambda h: (order[h["level"]], h["pos"]))
    from collections import Counter
    cat_counter = Counter(h["level"] for h in hits)
    for lv in ("高", "中", "低"):
        if cat_counter.get(lv):
            print(f"【{lv}风险】{cat_counter[lv]} 处")

    print()
    for h in hits_sorted:
        print(f"[{h['level']}] {h['category']} · 「{h['keyword']}」")
        print(f"  语境：…{h['context']}…")
        print(f"  建议：{h['advice']}")
        print()

    print("-" * 60)
    print("⚠️ 本结果仅作风险提示，不构成法律意见；重大合同请咨询执业律师。")


if __name__ == "__main__":
    main()
