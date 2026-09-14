---
name: smart-home-mcp
slug: smart-home-mcp
displayName: 智能家居控制
summary: 通过 Home Assistant REST API 查询实体并预演或执行服务调用；写操作默认 dry-run，高风险域需二次确认。
license: MIT
description: Home Assistant REST CLI，用于列出或查询实体、调用服务和执行本地场景模板。凭据只从 HA_URL/HA_TOKEN 读取；控制命令默认仅打印计划，必须显式传入 --execute，高风险门锁、安防及自动化域还需 --confirm-dangerous。它不是 MCP server，但可作为受控后端封装。
version: 0.1.3
homepage: https://github.com/axel286137079-dot/gaia-skill/tree/main/skills/smart-home-mcp
category: 智能家居
tags: [智能家居, HomeAssistant, MCP, 自动化]
platforms: [workbuddy, claude-code, cursor]
---

# 智能家居控制

让 agent 通过 **Home Assistant 本地网关**查询设备，并在明确授权后执行控制。

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
python3 bin/ha_cli.py call light turn_on --entity light.living_room             # 仅预演
python3 bin/ha_cli.py call light turn_on --entity light.living_room --execute   # 实际执行
python3 bin/ha_cli.py call automation trigger --entity automation.wakeup --execute --confirm-dangerous
```

### 3. 激活场景
```bash
python3 bin/ha_cli.py scene 回家             # 仅预演
python3 bin/ha_cli.py scene 回家 --execute   # 实际执行；含高风险域时还需 --confirm-dangerous
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

本目录没有实现 MCP server。需要 MCP 时，可使用 Home Assistant 官方支持的集成，或另行把 `call/states/get` 封装成工具，并保留本脚本的 dry-run 与确认策略。

## Home Assistant 版本校准（2026.9 核对）

本 skill 依赖的 REST 核心接口（`GET /api/states`、`POST /api/services/<domain>/<service>`、`GET /api/states/<entity_id>`）在 2026.9 中**未变更**，脚本可继续使用。但以下 2026.9 变化会影响周边集成与自动化，使用前需知悉：

| 变化 | 影响 | 应对 |
|---|---|---|
| **LLM API 工具名加域前缀** | `GetLiveContext` → `homeassistant__GetLiveContext`；`HassTurnOn` → `intent__HassTurnOn` | 若你在自定义 prompt 里直接点名工具，须改用带前缀的新名称 |
| **敏感操作收紧为管理员** | 安装/跳过更新、清除已跳过更新、Z-Wave 锁用户与凭证操作（`set_user`/`delete_user`/`set_credential` 等）需管理员账户 | 非管理员发起的脚本会失败；改由自动化触发或以管理员身份运行 |
| **Persistent Notification 语义变更** | 更新已存在的通知，`update_type` 由 `added` 变为 `updated` | 依赖 `added` 捕获所有通知的自动化，需把 `updated` 一并加入触发类型 |
| **InfluxDB YAML 配置移除** | 2026.9 起 `configuration.yaml` 里的 influxdb 连接/认证键完全失效（已自动迁移到 UI） | 从 YAML 中删除相关键，避免启动告警 |
| **Modbus 配置从 YAML 移到 UI** | 逆变器/电表/热泵类寄存器映射改由集成托管 | 在 UI 中重新配置，配置项由集成维护而非手写 YAML |
| **Configurator 集成弃用**；VLC 集成移除 | 相关集成不再可用（VLC via Telnet 不受影响） | 迁移到替代方案 |

> 提示：HA 每月发布都会收紧 API 契约。本 skill 只依赖稳定的 REST 核心接口，因此不受集成级 breaking change 影响；但如果你把本脚本包装成 MCP server 或接入 LLM 对话，请每季度对照一次 [Home Assistant 发布说明](https://www.home-assistant.io/blog/categories/release-notes/)。

## 边界与红线

- **安全**：HA_TOKEN 是完整控制权凭证，只走环境变量、绝不写入文件/提交仓库；只在局域网/可信网络使用，勿暴露公网。
- **谨慎控制**：涉及门锁、安防、大功率电器的操作，先确认再执行，避免误触发。
- 国产家电需先接入 HA（米家/涂鸦等有官方或社区集成），未接入的设备无法直接控制。

## 参考

- Home Assistant REST API: https://developers.home-assistant.io/docs/api/rest/
- 场景模板见 `examples/scenes.json`。
