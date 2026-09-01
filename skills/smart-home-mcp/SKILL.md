---
name: smart-home-mcp
slug: smart-home-mcp
displayName: 智能家居控制
summary: 让 agent 通过 Home Assistant 本地网关控制家电——列出/查询实体、开关灯、激活场景，内置中文场景模板（回家/离家/晚安），支持 MCP 化接入。
license: MIT
description: 智能家居控制（Home Assistant 本地网关）。让 agent 通过 Home Assistant 的 REST API 控制家电：列出/查询实体状态、开关灯、调亮度、激活场景、触发自动化（bin/ha_cli.py 纯标准库封装，凭据走环境变量 HA_URL/HA_TOKEN，零密钥落盘）。内置中文场景模板（回家/离家/晚安/早安，examples/scenes.json），支持把 HA 接成 MCP server 供任意 agent 调用。用于：控制智能家居、开灯关灯、智能家居自动化、Home Assistant、HA 控制、场景模式、语音控制家电、家居设备查询。触发词：智能家居、开灯、关灯、Home Assistant、控制家电、家居自动化、场景模式、语音控制家电、家居设备、调节灯光。联系邮箱：43298568@qq.com。
version: 0.1.0
category: 智能家居
tags: [智能家居, HomeAssistant, MCP, 自动化]
platforms: [workbuddy, claude-code, cursor]
---

# 智能家居控制

让 agent 通过 **Home Assistant 本地网关**控制家电——列出/查询实体、开关灯、激活场景、触发自动化，支持 MCP 化接入。

## 何时使用

- 用户要「开灯/关灯/调亮度/开空调/拉窗帘」等语音/自然语言控制家居
- 用户要查询家里设备状态（温度、开关、电量）
- 用户要把 HA 接成 MCP server 供 agent 调用
- 用户要建「回家/离家/晚安」等一键场景

## 前置条件

- 已运行 Home Assistant（本地网关，默认端口 8123）
- 已生成 **长期访问令牌**（HA 页面 → 个人资料 → 安全 → 长期访问令牌）
- 凭据走环境变量，**不落盘**：

```bash
export HA_URL="http://homeassistant.local:8123"
export HA_TOKEN="你的长期访问令牌"
```

## 工作流

### 1. 列出/查询设备
```bash
python3 bin/ha_cli.py states                 # 所有实体
python3 bin/ha_cli.py states light           # 只看灯
python3 bin/ha_cli.py get light.living_room  # 单设备状态
```

### 2. 控制设备（自然语言 → 服务调用）
```bash
python3 bin/ha_cli.py call light turn_on --entity light.living_room
python3 bin/ha_cli.py call light turn_on --entity light.living_room --data '{"brightness": 180}'
python3 bin/ha_cli.py call scene turn_on --entity scene.movie
python3 bin/ha_cli.py call automation trigger --entity automation.wakeup
```

### 3. 激活场景
```bash
python3 bin/ha_cli.py scene 回家   # 用 examples/scenes.json 里的场景别名
```

## 中文场景模板（examples/scenes.json）

| 场景 | 动作 |
|---|---|
| 回家 | 开客厅灯 + 开空调 26° + 开窗帘 |
| 离家 | 关所有灯 + 关空调 + 关窗帘 |
| 晚安 | 只留夜灯 + 关电视 + 锁门 |
| 早安 | 开卧室灯 + 开窗帘 + 开热水器 |

> 场景里的 entity_id 需按你家的实际实体改（先 `states` 查名字）。

## MCP 化接入（可选）

想把它接成标准 MCP server 供任意 agent 调用，可用 Home Assistant 官方 MCP 集成，或用本脚本的 `call/states/get` 命令封装成 MCP 工具。核心是：`call <domain> <service> --entity <id>` 已覆盖 90% 的设备控制需求。

## 边界与红线

- **安全**：HA_TOKEN 是完整控制权凭证，只走环境变量、绝不写入文件/提交仓库；只在局域网/可信网络使用，勿暴露公网。
- **谨慎控制**：涉及门锁、安防、大功率电器的操作，先确认再执行，避免误触发。
- 国产家电需先接入 HA（米家/涂鸦等有官方或社区集成），未接入的设备无法直接控制。

## 参考

- Home Assistant REST API: https://developers.home-assistant.io/docs/api/rest/
- 场景模板见 `examples/scenes.json`。
