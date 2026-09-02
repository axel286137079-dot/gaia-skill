#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
黄金盘前分析数据引擎（纯标准库，无第三方依赖）

数据源（均为免费、国内直连、无需 key）：
  1. 新浪财经 现货行情  —— hf_XAU 伦敦金 / DINIW 美元指数 / hf_XAG 伦敦银
  2. 新浪财经 日 K 线   —— GlobalFuturesService.getGlobalFuturesDailyKLine (XAU)
  3. 金十数据 快讯流    —— jin10.com/flash_newest.js

用法：
  python3 gold_premarket.py quote        # 实时行情（伦敦金/美元/白银）
  python3 gold_premarket.py kline [N]    # 近 N 日 K 线 + 技术指标（默认 120）
  python3 gold_premarket.py news [M]     # 金十快讯（黄金相关，默认 40 条）
  python3 gold_premarket.py report       # 一键生成完整盘前简报
  python3 gold_premarket.py test         # 自检各数据源连通性

设计原则：脚本只负责「稳定可脚本化」的数据与指标；宏观定性研判与最终
决策建议由调用方（Agent）结合 web 搜索补充，见 SKILL.md。
"""

import sys
import json
import math
import re
import datetime
import urllib.request
from zoneinfo import ZoneInfo

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
REFERER = "https://finance.sina.com.cn"
TIMEOUT = 12


# --------------------------------------------------------------------------- #
# 基础 HTTP
# --------------------------------------------------------------------------- #
def http_get(url, referer=True, decode="utf-8", timeout=TIMEOUT):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    if referer:
        req.add_header("Referer", REFERER)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return raw.decode(decode, errors="replace")


# --------------------------------------------------------------------------- #
# 1. 实时行情（新浪）
# --------------------------------------------------------------------------- #
# 新浪外盘期货 hf_ 字段序：现价, 昨结, 今开, 买价, 最高, 最低, 时间, 卖价, ...
# 美元指数 DINIW 走外汇格式，字段序不同，单独映射。
QUOTES = {
    "XAU": {"code": "hf_XAU", "name": "伦敦金（现货黄金）", "unit": "美元/盎司", "kind": "hf"},
    "XAG": {"code": "hf_XAG", "name": "伦敦银（现货白银）", "unit": "美元/盎司", "kind": "hf"},
    "DXY": {"code": "DINIW",  "name": "美元指数",           "unit": "点",       "kind": "fx"},
}


def _parse_hf(parts):
    # parts: 现价,昨结,今开,买价,最高,最低,时间,卖价,...
    return {
        "price": float(parts[0]),
        "prev_close": float(parts[1]),
        "open": float(parts[2]),
        "high": float(parts[4]),
        "low": float(parts[5]),
        "time": parts[6],
    }


def _parse_fx(parts):
    # parts: 时间,现价,今开,昨收,成交量,买价,卖价,最低,最高,名称,日期
    return {
        "price": float(parts[1]),
        "prev_close": float(parts[3]),
        "open": float(parts[2]),
        "high": float(parts[8]),
        "low": float(parts[7]),
        "time": parts[0],
    }


def fetch_quote():
    out = {}
    lines = http_get("https://hq.sinajs.cn/list=" + ",".join(q["code"] for q in QUOTES.values()),
                     decode="gbk").strip().split("\n")
    for line in lines:
        if "=" not in line:
            continue
        raw = line.split('="', 1)[1].rstrip('";')
        if not raw:
            continue
        parts = raw.split(",")
        # 反查代码
        for key, meta in QUOTES.items():
            if meta["code"] in line:
                try:
                    d = _parse_hf(parts) if meta["kind"] == "hf" else _parse_fx(parts)
                except (ValueError, IndexError):
                    d = None
                if d and d["price"] > 0:
                    prev = d["prev_close"] if d["prev_close"] > 0 else d["price"]
                    d["change_pct"] = round((d["price"] - prev) / prev * 100, 2)
                    d["name"] = meta["name"]
                    d["unit"] = meta["unit"]
                    out[key] = d
                break
    return out


# --------------------------------------------------------------------------- #
# 2. 日 K 线（新浪）
# --------------------------------------------------------------------------- #
def fetch_kline(symbol="XAU", days=120):
    url = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_=/"
           "GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=" + symbol)
    text = http_get(url)
    # 剥离 jsonp 包装：var _=( ... )
    start = text.find("([")
    end = text.rfind("])")
    if start == -1 or end == -1:
        raise RuntimeError("日K接口返回格式异常")
    arr = json.loads(text[start + 1:end + 1])
    rows = [{
        "date": r["date"],
        "open": float(r["open"]),
        "high": float(r["high"]),
        "low": float(r["low"]),
        "close": float(r["close"]),
    } for r in arr]
    return rows[-days:] if days else rows


# --------------------------------------------------------------------------- #
# 3. 金十快讯
# --------------------------------------------------------------------------- #
GOLD_KEYWORDS = ["黄金", "金价", "伦敦金", "现货金", "美联储", "利率", "降息", "加息",
                 "美元", "美元指数", "CPI", "非农", "通胀", "地缘", "央行", "避险",
                 "FOMC", "美债", "收益率", "ETF", "购金", "金条", "gold", "XAU"]


def fetch_news(limit=40, gold_only=True):
    text = http_get("https://www.jin10.com/flash_newest.js")
    start = text.find("= [")
    if start == -1:
        raise RuntimeError("金十快讯接口格式异常")
    # 该接口为 js 赋值，末尾无闭合括号保护；截到最后一个 }] 结束
    end = text.rfind("}]")
    arr = json.loads(text[start + 2:end + 2])
    items = []
    for it in arr:
        d = it.get("data") or {}
        content = re.sub(r"<[^>]+>", "", (d.get("content") or "")).strip()
        if not content:
            continue
        if gold_only and not any(k.lower() in content.lower() for k in GOLD_KEYWORDS):
            continue
        items.append({"time": it.get("time", ""), "content": content})
        if len(items) >= limit:
            break
    return items


# --------------------------------------------------------------------------- #
# 4. 技术指标（纯标准库实现）
# --------------------------------------------------------------------------- #
def _sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _rsi(closes, n=14):
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(rows, n=14):
    if len(rows) < n + 1:
        return None
    trs = []
    for i in range(1, len(rows)):
        h, l, pc = rows[i]["high"], rows[i]["low"], rows[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / n


def _std(vals):
    if len(vals) < 2:
        return 0.0
    m = sum(vals) / len(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def calc_indicators(rows):
    closes = [r["close"] for r in rows]
    last = rows[-1]
    px = last["close"]

    ma = {n: _sma(closes, n) for n in (5, 10, 20, 60, 120)}
    rsi = _rsi(closes)
    atr = _atr(rows)

    # 布林带（20 日）
    bb = {}
    if len(closes) >= 20:
        mid = _sma(closes, 20)
        sd = _std(closes[-20:])
        bb = {"mid": mid, "up": mid + 2 * sd, "low": mid - 2 * sd}

    # Donchian 通道（20 日）
    donch = {
        "high": max(r["high"] for r in rows[-20:]),
        "low": min(r["low"] for r in rows[-20:]),
    }

    # 近 5 / 20 日高低点（支撑阻力参考）
    rng5 = {"high": max(r["high"] for r in rows[-5:]),
            "low": min(r["low"] for r in rows[-5:])}
    rng20 = {"high": max(r["high"] for r in rows[-20:]),
             "low": min(r["low"] for r in rows[-20:])}

    # 均线排列判断
    if ma[5] and ma[20] and ma[60]:
        if ma[5] > ma[20] > ma[60]:
            align = "多头排列"
        elif ma[5] < ma[20] < ma[60]:
            align = "空头排列"
        else:
            align = "均线纠缠"
    else:
        align = "数据不足"

    # 相对 MA20 位置
    pos_vs_ma20 = round((px / ma[20] - 1) * 100, 2) if ma[20] else None

    return {
        "last_close": px,
        "ma": {k: (round(v, 2) if v else None) for k, v in ma.items()},
        "rsi": round(rsi, 1) if rsi else None,
        "atr": round(atr, 2) if atr else None,
        "boll": {k: round(v, 2) for k, v in bb.items()} if bb else None,
        "donch": donch,
        "range5": rng5,
        "range20": rng20,
        "ma_align": align,
        "pos_vs_ma20": pos_vs_ma20,
    }


# --------------------------------------------------------------------------- #
# 5. 时段与季节性
# --------------------------------------------------------------------------- #
SEASONAL = {
    1: ("偏多", "+2.3% 历史均值", "新年配置 + 实物需求"),
    2: ("中性", "+0.5%", "春节后需求回落"),
    3: ("偏空", "-1.2%", "季度末资金流动"),
    4: ("偏多", "+1.8%", "印度 Akshaya Tritiya 节"),
    5: ("中性", "—", "季节性疲软期"),
    6: ("中性", "—", "半年度调整"),
    7: ("中性", "—", "常规月"),
    8: ("偏多", "—", "实物需求回升"),
    9: ("强多", "+3.1%", "印度婚礼季需求高峰"),
    10: ("偏多", "+2.2%", "节日需求持续"),
    11: ("偏多", "+2.5%", "节日季 + 避险"),
    12: ("震荡", "+0.8%", "年终调整、流动性变化"),
}

# 服务器时段为 GMT+8 环境；黄金活跃时段按 UTC 映射（14-18 UTC = 22:00-02:00 北京时间）
def session_hint(hour_utc8):
    # 北京时间 -> 大致活跃时段提示
    if 8 <= hour_utc8 < 12:
        return "亚洲盘（流动性偏低）"
    if 12 <= hour_utc8 < 16:
        return "欧盘早段（流动性回升）"
    if 16 <= hour_utc8 < 21:
        return "伦敦盘（活跃）"
    if 21 <= hour_utc8 < 24 or 0 <= hour_utc8 < 3:
        return "伦敦-纽约重叠 / 纽约盘（最活跃，14-18 UTC）"
    return "纽约尾盘 / 亚盘前（流动性枯竭）"


# --------------------------------------------------------------------------- #
# 6. 简报生成
# --------------------------------------------------------------------------- #
def build_report():
    now = datetime.datetime.now(ZoneInfo("Asia/Shanghai"))
    quote = fetch_quote()
    kline = fetch_kline()
    ind = calc_indicators(kline)
    news = fetch_news()

    L = []
    L.append("# 黄金盘前分析简报")
    L.append("")
    L.append("> 数据时间：%s（北京时间）｜数据源：新浪财经 / 金十数据｜仅供参考，不构成投资建议"
             % now.strftime("%Y-%m-%d %H:%M:%S"))
    L.append("")

    # 一、今日行情
    L.append("## 一、今日行情")
    L.append("")
    L.append("| 品种 | 现价 | 涨跌幅 | 日内高 | 日内低 |")
    L.append("|---|---|---|---|---|")
    for key, name in (("XAU", "伦敦金"), ("XAG", "伦敦银"), ("DXY", "美元指数")):
        d = quote.get(key)
        if d:
            L.append("| %s | %.2f %s | %+.2f%% | %.2f | %.2f |"
                     % (name, d["price"], d["unit"], d["change_pct"], d["high"], d["low"]))
    L.append("")

    # 二、技术位置
    L.append("## 二、技术位置（日线）")
    L.append("")
    L.append("- 昨收：%.2f" % ind["last_close"])
    L.append("- 均线：MA5 %s / MA10 %s / MA20 %s / MA60 %s / MA120 %s（%s）"
             % (ind["ma"][5], ind["ma"][10], ind["ma"][20], ind["ma"][60], ind["ma"][120], ind["ma_align"]))
    if ind["pos_vs_ma20"] is not None:
        L.append("- 现价相对 MA20：%+.2f%%" % ind["pos_vs_ma20"])
    L.append("- RSI(14)：%s%s" % (ind["rsi"], "（超买）" if ind["rsi"] and ind["rsi"] > 70 else ("（超卖）" if ind["rsi"] and ind["rsi"] < 30 else "")))
    L.append("- ATR(14)：%s 美元（日内波动参考）" % ind["atr"])
    if ind["boll"]:
        L.append("- 布林带：上轨 %s / 中轨 %s / 下轨 %s" % (ind["boll"]["up"], ind["boll"]["mid"], ind["boll"]["low"]))
    L.append("- Donchian(20)：上轨 %s / 下轨 %s" % (round(ind["donch"]["high"], 2), round(ind["donch"]["low"], 2)))
    L.append("- 支撑参考：%s（5日低）/ %s（20日低）" % (round(ind["range5"]["low"], 2), round(ind["range20"]["low"], 2)))
    L.append("- 阻力参考：%s（5日高）/ %s（20日高）" % (round(ind["range5"]["high"], 2), round(ind["range20"]["high"], 2)))
    L.append("")

    # 三、宏观驱动（快讯）
    L.append("## 三、宏观驱动（金十快讯，黄金相关）")
    L.append("")
    if news:
        for it in news[:15]:
            L.append("- [%s] %s" % (it["time"][-8:], it["content"]))
    else:
        L.append("- 暂无相关快讯")
    L.append("")
    L.append("> 提示：以上为盘中即时快讯。盘前定性研判（美联储预期 / 地缘 / 美元趋势）"
             "需由调用方结合 web 搜索补充，见 SKILL.md 第 4 节。")
    L.append("")

    # 四、时段与季节性
    L.append("## 四、时段与季节性")
    L.append("")
    L.append("- 当前时段：%s" % session_hint(now.hour))
    s = SEASONAL.get(now.month)
    if s:
        L.append("- 月度季节性（%d 月）：%s｜历史 %s｜%s" % (now.month, s[0], s[1], s[2]))
    L.append("")

    # 五、决策框架
    L.append("## 五、持仓决策框架")
    L.append("")
    L.append("| 维度 | 现状 | 倾向 |")
    L.append("|---|---|---|")
    trend = "多头" if ind["ma_align"] == "多头排列" else ("空头" if ind["ma_align"] == "空头排列" else "震荡")
    L.append("| 趋势（均线排列） | %s | %s |" % (ind["ma_align"], "偏多" if trend == "多头" else ("偏空" if trend == "空头" else "中性")))
    rsi_state = "超买" if ind["rsi"] and ind["rsi"] > 70 else ("超卖" if ind["rsi"] and ind["rsi"] < 30 else "中性")
    L.append("| RSI 状态 | %s | %s |" % (ind["rsi"], "警惕回调" if rsi_state == "超买" else ("关注反弹" if rsi_state == "超卖" else "中性")))
    if ind["pos_vs_ma20"] is not None:
        L.append("| 相对 MA20 | %+.2f%% | %s |" % (ind["pos_vs_ma20"], "偏多" if ind["pos_vs_ma20"] > 0 else "偏空"))
    L.append("")
    L.append("> 决策三档（🟢 买入 / 🟡 观望 / 🔴 暂缓）需综合宏观研判后由调用方给出，"
             "技术面仅提供客观现状。详见 SKILL.md 判断标准。")
    L.append("")

    # 六、风险提示
    L.append("## 六、风险提示")
    L.append("")
    L.append("- 黄金为高风险资产，波动大，务必设置止损（生存第一：单笔风险 ≤ 1% 本金）。")
    L.append("- 若当日有非农 / CPI / FOMC 等重磅数据，事件前后波动放大，谨慎操作。")
    L.append("- 本简报数据可能存在 15-60 秒延迟，成交以实际行情为准。")
    L.append("")

    return "\n".join(L)


# --------------------------------------------------------------------------- #
# 主入口（手写 argv 分发）
# --------------------------------------------------------------------------- #
def _print_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd = argv[1]

    if cmd == "quote":
        _print_json(fetch_quote())
    elif cmd == "kline":
        n = int(argv[2]) if len(argv) > 2 else 120
        rows = fetch_kline(days=n)
        _print_json({"count": len(rows), "kline": rows, "indicators": calc_indicators(rows)})
    elif cmd == "news":
        m = int(argv[2]) if len(argv) > 2 else 40
        _print_json(fetch_news(limit=m))
    elif cmd == "report":
        print(build_report())
    elif cmd == "test":
        tests = {"quote": False, "kline": False, "news": False}
        try:
            tests["quote"] = bool(fetch_quote())
        except Exception as e:
            print("quote FAIL:", e)
        try:
            tests["kline"] = len(fetch_kline(days=5)) > 0
        except Exception as e:
            print("kline FAIL:", e)
        try:
            tests["news"] = len(fetch_news(limit=3)) > 0
        except Exception as e:
            print("news FAIL:", e)
        _print_json(tests)
    else:
        print("未知命令:", cmd)
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
