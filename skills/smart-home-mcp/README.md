# 智能家居控制

通过 **Home Assistant REST API** 查询实体，并以默认 dry-run 的方式预演服务调用。

## 一句话定位

本 skill 提供「HA REST API 封装 + 中文场景模板 + 明确执行确认」，便于 agent 安全地查询和控制家居。它本身不是 MCP server。

## 为什么值得做

- Home Assistant 是本地智能家居的事实标准，但 REST API 上手有门槛（token、路径、服务调用格式）。
- 把「开客厅灯」翻译成 `call light turn_on --entity light.living_room`，是 agent 控制家居的关键一步。
- 中文场景模板（回家/离家/晚安）贴合国内使用习惯，海外工具没有。

## 快速开始

```bash
export HA_URL="http://homeassistant.local:8123"
export HA_TOKEN="你的长期访问令牌"

python3 bin/ha_cli.py ping                    # 测试连接
python3 bin/ha_cli.py states                  # 列出所有实体
python3 bin/ha_cli.py states light            # 只看灯
python3 bin/ha_cli.py get light.living_room   # 单设备
python3 bin/ha_cli.py call light turn_on --entity light.living_room             # 仅预演
python3 bin/ha_cli.py call light turn_on --entity light.living_room --execute   # 实际执行
python3 bin/ha_cli.py scene 回家              # 仅预演
python3 bin/ha_cli.py scene 回家 --execute    # 实际执行
```

## 目录结构

```
smart-home-mcp/
├── SKILL.md               # 路由逻辑 + 场景模板 + MCP 化指引 + 安全红线
├── README.md              # 本文件
├── bin/ha_cli.py          # HA REST API 封装（纯标准库，凭据走环境变量）
└── examples/scenes.json   # 中文场景模板（回家/离家/晚安/早安）
```

## 安全红线

- **HA_TOKEN 是完整控制权凭证**：只走环境变量、绝不写入文件/仓库。
- 只在局域网/可信网络使用，勿暴露公网。
- 门锁、安防、自动化等高风险域还必须传 `--confirm-dangerous`；大功率电器操作前人工复核。

## License

MIT
