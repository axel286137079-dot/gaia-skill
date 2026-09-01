# 黄金盘前分析 MCP Server（gold-premarket-mcp）

把盖亚-skill「黄金盘前分析」的数据引擎（`gold_premarket.py`）封装成标准 **MCP Server**，让 Claude Desktop / Cursor / WorkBuddy / dsh 等任意支持 MCP 的客户端，都能直接调用实时行情、技术指标、快讯和一键盘前简报。

**定位**：配置型盘前研判，只输出客观数据与指标，**不喊单、不荐金、不构成投资建议**。宏观定性研判与 🟢🟡🔴 三档决策由调用方（Agent）结合 web 搜索补充。

---

## 提供的 5 个工具

| 工具 | 说明 | 返回 |
|---|---|---|
| `get_quote` | 伦敦金/伦敦银/美元指数实时行情 | 现价、涨跌幅、日内高低、昨结 |
| `get_kline` | 黄金日 K 线（近 N 日）+ 技术指标 | K 线 + MA/RSI/ATR/布林/Donchian/支撑阻力 |
| `get_indicators` | 仅技术指标（不返回原始 K 线） | 趋势/超买超卖/支撑阻力/均线排列 |
| `get_news` | 金十数据黄金相关快讯 | 美联储/利率/地缘/购金等驱动 |
| `generate_report` | 一键盘前简报（Markdown） | 六段完整简报 |

---

## 安装

```bash
# 1. 克隆仓库（或直接下载本目录）
git clone https://github.com/axel286137079-dot/gaia-skill.git
cd gaia-skill/mcp/gold-premarket-mcp

# 2. 建虚拟环境并安装依赖（仅 mcp 一个第三方依赖，数据层是纯标准库）
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

> 数据层 `gold_premarket.py` 零第三方依赖，只有 `server.py` 需要 `mcp` 包。

---

## 接入各客户端

### Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "gold-premarket": {
      "command": "/绝对路径/.venv/bin/python",
      "args": ["/绝对路径/gold-premarket-mcp/server.py"]
    }
  }
}
```

### Cursor

在 Cursor 的 MCP 设置中添加 stdio server，同上（command 指向 venv 里的 python，args 指向 server.py）。

### WorkBuddy

在「连接器」管理页添加自定义 MCP server，类型选 stdio，命令与参数同上。

### dsh / 其他 MCP 客户端

只要是 stdio 传输，配置方式一致：`command = python 解释器`，`args = [server.py 路径]`。

---

## 手动运行 / 测试

```bash
# stdio（默认，供 MCP 客户端拉起）
python server.py

# 自检数据源连通性
python gold_premarket.py test

# 直接生成一份简报看看效果
python gold_premarket.py report
```

---

## 数据源（免费、国内直连、无需 key）

| 数据 | 来源 | 接口 |
|---|---|---|
| 现货行情 | 新浪财经 | `hq.sinajs.cn/list=hf_XAU,DINIW,hf_XAG` |
| 日 K 线 | 新浪财经 | `GlobalFuturesService.getGlobalFuturesDailyKLine` |
| 快讯流 | 金十数据 | `jin10.com/flash_newest.js` |

---

## 边界与红线

- **配置型，不喊单**：本服务只给数据与指标，不输出「买入/卖出」指令；🟢🟡🔴 三档决策由调用方结合宏观研判给出。
- **不构成投资建议**：所有输出仅供参考，黄金为高风险资产。
- **数据延迟**：行情可能有 15-60 秒延迟，成交以实际行情为准。
- **不捏造数据**：所有数据来自上述免费接口实时抓取，无任何硬编码行情。

---

## 目录结构

```
gold-premarket-mcp/
├── server.py            # FastMCP 入口，暴露 5 个工具
├── gold_premarket.py    # 数据引擎（纯标准库，零依赖）
├── pyproject.toml       # 项目元数据 + 依赖声明
├── README.md            # 本文件
├── LICENSE              # MIT
└── examples/
    └── claude_desktop_config.json  # Claude Desktop 配置示例
```

---

## License

[MIT](./LICENSE)

---

## 关于作者

苏格 —— 独立开发者，专注「国产场景 AI 技能 / 量化工具」孵化。

联系邮箱：43298568@qq.com
