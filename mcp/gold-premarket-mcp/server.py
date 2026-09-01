#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
黄金盘前分析 MCP Server（FastMCP）

把 gold_premarket.py 数据引擎的能力暴露为标准 MCP 工具，
供 Claude Desktop / Cursor / WorkBuddy / dsh 等任意 MCP 客户端调用。

数据源（免费、国内直连、无需 key）：
  1. 新浪财经 现货行情  —— 伦敦金 / 美元指数 / 伦敦银
  2. 新浪财经 日 K 线   —— GlobalFuturesService (XAU)
  3. 金十数据 快讯流    —— jin10.com/flash_newest.js

定位：配置型盘前简报，不喊单、不荐金。脚本只提供「稳定可脚本化」
的数据与指标；宏观定性研判与 🟢🟡🔴 三档决策由调用方（Agent）结合
web 搜索补充（见 gold-premarket skill 的 SKILL.md）。

运行：
  python server.py            # stdio 传输（默认，Claude Desktop / Cursor 用）
  python server.py --sse      # SSE/HTTP 传输（远程 / 团队共享场景）
"""

import sys

# mcp 2.x 将 FastMCP 重命名为 MCPServer；此处做双版本兼容，1.x/2.x 均可运行
try:
    from mcp.server.mcpserver import MCPServer as _Server  # mcp >= 2.0
except ImportError:  # pragma: no cover - mcp 1.x 回退
    from mcp.server.fastmcp import FastMCP as _Server

import gold_premarket as gp

mcp = _Server(
    "gold-premarket",
    instructions=(
        "黄金盘前分析数据服务。提供实时行情、日K与技术指标、金十快讯、"
        "一键盘前简报。定位为「配置型」研判：只输出客观数据与指标，"
        "不喊单、不荐金、不构成投资建议。宏观定性研判需调用方自行结合"
        " web 搜索（美联储预期/地缘/美元趋势）补充后给出 🟢🟡🔴 三档决策。"
    ),
)


@mcp.tool()
def get_quote() -> dict:
    """获取实时行情：伦敦金(XAU)、伦敦银(XAG)、美元指数(DXY) 的现价、涨跌幅、日内高低、昨结。

    返回 dict，键为 XAU/XAG/DXY，值为 {name, price, prev_close, open, high, low, change_pct, unit, time}。
    用于回答「黄金现在多少钱」「今天金价涨跌多少」「美元指数多少」等实时行情问题。"""
    return gp.fetch_quote()


@mcp.tool()
def get_kline(days: int = 120) -> dict:
    """获取黄金日 K 线（近 N 日，默认 120 日）及技术指标。

    参数 days: 返回的交易日数量，默认 120，最大建议 500。
    返回 dict: {count, kline: [{date, open, high, low, close}], indicators: {...}}。
    indicators 含 MA5/10/20/60/120、RSI14、ATR14、布林带(20)、Donchian(20)、
    5/20 日支撑阻力、均线排列、相对 MA20 位置。用于技术面分析。"""
    rows = gp.fetch_kline(days=days)
    return {"count": len(rows), "kline": rows, "indicators": gp.calc_indicators(rows)}


@mcp.tool()
def get_indicators(days: int = 120) -> dict:
    """仅计算黄金技术指标（不返回原始 K 线）。

    参数 days: 参与计算的交易日数量，默认 120。
    返回 dict: {last_close, ma{5,10,20,60,120}, rsi, atr, boll{mid,up,low},
    donch{high,low}, range5{high,low}, range20{high,low}, ma_align, pos_vs_ma20}。
    用于快速判断趋势/超买超卖/支撑阻力。"""
    rows = gp.fetch_kline(days=days)
    return gp.calc_indicators(rows)


@mcp.tool()
def get_news(limit: int = 40, gold_only: bool = True) -> dict:
    """获取金十数据快讯（默认只保留黄金相关，按时间倒序）。

    参数 limit: 返回条数，默认 40；gold_only: 是否只保留黄金/美元/利率/地缘等关键驱动快讯，默认 True。
    返回 dict: {count, news: [{time, content}]}。用于捕捉盘中宏观驱动（美联储讲话/CPI/非农/地缘/购金等）。"""
    items = gp.fetch_news(limit=limit, gold_only=gold_only)
    return {"count": len(items), "news": items}


@mcp.tool()
def generate_report() -> str:
    """一键生成完整黄金盘前分析简报（Markdown 文本）。

    六段结构：①今日行情 ②技术位置 ③宏观驱动(快讯) ④时段与季节性 ⑤持仓决策框架 ⑥风险提示。
    适合作为「黄金盘前分析」「今日金价简报」的完整底稿，直接返回 markdown 供展示/转发。"""
    return gp.build_report()


def main():
    # --sse / --http 等非 stdio 场景：从 FastMCP 2.x 的 CLI 参数解析
    if "--sse" in sys.argv:
        mcp.run(transport="sse")
    elif "--streamable-http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
