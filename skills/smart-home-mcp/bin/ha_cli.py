#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ha_cli.py — Home Assistant 本地网关控制（REST API 封装）

通过 Home Assistant 的 REST API 控制家电：列出/查询实体、调用服务（开关灯等）、
激活场景。纯标准库，凭据走环境变量（不落盘）。

用法：
    export HA_URL="http://homeassistant.local:8123"
    export HA_TOKEN="你的长期访问令牌"

    python3 ha_cli.py ping                          # 测试连接
    python3 ha_cli.py states [过滤词]                # 列出实体状态
    python3 ha_cli.py get light.living_room          # 单实体
    python3 ha_cli.py call light turn_on --entity light.living_room
    python3 ha_cli.py call light turn_on --entity light.living_room --data '{"brightness":180}'
    python3 ha_cli.py scene 回家 [--scenes scenes.json]  # 激活场景（用别名）
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_SCENES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "examples", "scenes.json")


def _cfg():
    url = os.environ.get("HA_URL")
    token = os.environ.get("HA_TOKEN")
    if not url or not token:
        print("✗ 未配置 HA_URL / HA_TOKEN。", file=sys.stderr)
        print("  export HA_URL=\"http://homeassistant.local:8123\"", file=sys.stderr)
        print("  export HA_TOKEN=\"你的长期访问令牌\"", file=sys.stderr)
        sys.exit(1)
    return url.rstrip("/"), token


def _request(method, path, token, body=None):
    url = _cfg()[0] + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print(f"✗ HTTP {e.code}：{e.read().decode('utf-8', 'replace')[:200]}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"✗ 连接失败：{e.reason}（检查 HA 是否运行、URL/网络是否正确）", file=sys.stderr)
        sys.exit(1)


def cmd_ping(args):
    url, token = _cfg()
    try:
        _request("GET", "/api/", token)
        print("✓ 连接成功：", url)
    except SystemExit:
        raise


def cmd_states(args):
    _, token = _cfg()
    data = _request("GET", "/api/states", token)
    items = data if isinstance(data, list) else data.get("result", [])
    if args.filter:
        items = [i for i in items if args.filter.lower() in i.get("entity_id", "").lower()]
    for i in items:
        eid = i.get("entity_id", "")
        state = i.get("state", "")
        attrs = i.get("attributes", {})
        friendly = attrs.get("friendly_name", "")
        print(f"{eid:<40} {state:<12} {friendly}")
    print(f"\n共 {len(items)} 个实体")


def cmd_get(args):
    _, token = _cfg()
    data = _request("GET", "/api/states/" + args.entity, token)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_call(args):
    _, token = _cfg()
    body = {"entity_id": args.entity} if args.entity else {}
    if args.data:
        try:
            extra = json.loads(args.data)
        except json.JSONDecodeError:
            print("✗ --data 不是合法 JSON", file=sys.stderr)
            sys.exit(1)
        body.update(extra)
    path = f"/api/services/{args.domain}/{args.service}"
    _request("POST", path, token, body=body)
    print(f"✓ 已调用 {args.domain}.{args.service}"
          + (f" → {args.entity}" if args.entity else ""))


def cmd_scene(args):
    # 读场景模板，把中文别名映射到 scene entity
    scenes_path = args.scenes or DEFAULT_SCENES
    if not os.path.exists(scenes_path):
        print(f"✗ 找不到场景模板：{scenes_path}", file=sys.stderr)
        sys.exit(1)
    with open(scenes_path, "r", encoding="utf-8") as f:
        scenes = json.load(f)
    if args.name not in scenes:
        print(f"✗ 场景「{args.name}」不存在。可用：{', '.join(scenes.keys())}", file=sys.stderr)
        sys.exit(1)
    scene = scenes[args.name]
    _, token = _cfg()
    # 场景 = 一个 scene entity，或多个服务调用序列
    if "scene" in scene:
        _request("POST", "/api/services/scene/turn_on", token,
                 body={"entity_id": scene["scene"]})
        print(f"✓ 已激活场景：{args.name}（{scene['scene']}）")
        return
    # 序列化动作
    for step in scene.get("actions", []):
        body = {"entity_id": step["entity"]}
        if "data" in step:
            body.update(step["data"])
        _request("POST", f"/api/services/{step['domain']}/{step['service']}",
                 token, body=body)
        print(f"  ✓ {step['domain']}.{step['service']} → {step['entity']}")
    print(f"✓ 场景「{args.name}」执行完成")


def main():
    ap = argparse.ArgumentParser(description="Home Assistant 本地网关控制")
    sub = ap.add_subparsers(dest="cmd")

    p_ping = sub.add_parser("ping", help="测试连接")
    p_ping.set_defaults(func=cmd_ping)

    p_states = sub.add_parser("states", help="列出实体状态")
    p_states.add_argument("filter", nargs="?", help="按 entity_id 过滤")
    p_states.set_defaults(func=cmd_states)

    p_get = sub.add_parser("get", help="查询单实体")
    p_get.add_argument("entity", help="如 light.living_room")
    p_get.set_defaults(func=cmd_get)

    p_call = sub.add_parser("call", help="调用服务")
    p_call.add_argument("domain", help="如 light / switch / scene / automation")
    p_call.add_argument("service", help="如 turn_on / turn_off")
    p_call.add_argument("--entity", help="entity_id")
    p_call.add_argument("--data", help="JSON 附加参数")
    p_call.set_defaults(func=cmd_call)

    p_scene = sub.add_parser("scene", help="激活场景（用中文别名）")
    p_scene.add_argument("name", help="场景名（回家/离家/晚安/早安）")
    p_scene.add_argument("--scenes", help="场景模板 JSON 路径")
    p_scene.set_defaults(func=cmd_scene)

    args = ap.parse_args()
    if not getattr(args, "cmd", None):
        ap.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
