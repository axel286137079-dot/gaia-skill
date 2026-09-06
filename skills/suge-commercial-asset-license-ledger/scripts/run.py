#!/usr/bin/env python3
"""Offline commercial asset license ledger. Never fetches, never rules on infringement."""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

ASSET_TYPES = {"image", "font", "music", "video", "template", "other"}


def text(value, label, maximum=160):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(label + " must be nonempty text within length limit")
    return value.strip()


def iso_or_none(value, label):
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None, None
    if isinstance(value, str) and value.strip().lower() == "perpetual":
        return "perpetual", None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        raise ValueError(label + " must be ISO YYYY-MM-DD or perpetual")
    try:
        return "date", date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(label + " is not a valid calendar date") from None


def strlist(value, label):
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise ValueError(label + " must be a list of short text items")
    return [x.strip() for x in value]


def tri(value, label):
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(label + " must be boolean or null")
    return value


def md_table(rows):
    head = rows[0]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join(["---"] * len(head)) + "|"]
    for row in rows[1:]:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def audit_asset(item, as_of):
    asset_id = text(item.get("asset_id") or "asset-?", "asset_id")
    asset_type = text(item.get("asset_type") or "other", "asset_type", maximum=30)
    if asset_type not in ASSET_TYPES:
        raise ValueError("asset_type must be one of " + ",".join(sorted(ASSET_TYPES)))
    source = text(item.get("source") or "来源未知", "source")
    license_type = item.get("license_type")
    license_type = text(license_type, "license_type") if license_type is not None and str(license_type).strip() else None
    proof = item.get("proof_reference")
    proof = text(proof, "proof_reference", maximum=200) if proof is not None and str(proof).strip() else None
    permitted_channels = strlist(item.get("permitted_channels"), "permitted_channels")
    actual_channels = strlist(item.get("actual_channels"), "actual_channels") or []
    territories = strlist(item.get("territories"), "territories")
    actual_territory = item.get("actual_territory")
    actual_territory = text(actual_territory, "actual_territory", maximum=40) \
        if actual_territory is not None and str(actual_territory).strip() else None
    expires_kind, expires_val = iso_or_none(item.get("expires_on"), "expires_on")
    derivative_allowed = tri(item.get("derivative_allowed"), "derivative_allowed")
    attribution_required = tri(item.get("attribution_required"), "attribution_required")
    attribution_present = tri(item.get("attribution_present"), "attribution_present")

    status = "PASS"
    reasons = []
    tasks = []
    due_in_days = None

    # Missing evidence / scope -> cannot PASS
    if license_type is None:
        status = "REVIEW" if status == "PASS" else status
        reasons.append("license_type_missing（授权类型缺失，未声明为可商用类型）")
        tasks.append("补充 license_type 与授权条款")
    if proof is None:
        status = "REVIEW" if status == "PASS" else status
        reasons.append("proof_reference_missing（缺少凭证引用，无法追溯购买/授权记录）")
        tasks.append("补充购买凭证或授权文件引用")
    if actual_channels is None or len(actual_channels) == 0:
        status = "REVIEW" if status == "PASS" else status
        reasons.append("usage_channels_empty（未提供实际使用渠道，无法核对渠道范围）")
        tasks.append("登记该素材实际投放/使用渠道")
    if territories is None or len(territories) == 0:
        status = "REVIEW" if status == "PASS" else status
        reasons.append("territory_scope_missing（授权地域范围缺失）")
        tasks.append("补充授权地域")
    elif actual_territory is not None and actual_territory not in territories:
        status = "BLOCK"
        reasons.append("territory_out_of_scope（实际使用地域 %s 不在授权地域 %s）" % (actual_territory, ",".join(territories)))
        tasks.append("停用该地域投放或补地域授权")

    # Expiry
    if expires_kind == "perpetual":
        pass
    elif expires_kind is None:
        status = "REVIEW" if status == "PASS" else status
        reasons.append("expires_on_missing（未声明授权期限，不能默认永久）")
        tasks.append("补充分类期限或授权到期日")
    else:
        days = (expires_val - as_of).days
        due_in_days = days
        if days < 0:
            status = "BLOCK"
            reasons.append("expired（授权已于 %s 到期）" % expires_val.isoformat())
            tasks.append("立即停用或重新取得授权")
        elif days == 0:
            status = "REVIEW" if status == "PASS" else status
            reasons.append("expires_on_as_of（授权基准日当天到期，视为边界风险）")
            tasks.append("当天内确认续期或停用")
        elif days <= 30:
            reasons.append("due_in_30d（%d 天内到期）" % days)
            tasks.append("优先处理续期/补证")

    # Channels
    if permitted_channels and actual_channels:
        extra = sorted(set(actual_channels) - set(permitted_channels))
        if extra:
            status = "BLOCK"
            reasons.append("channel_out_of_scope（实际渠道超出许可：" + ",".join(extra) + "）")
            tasks.append("下架越界渠道素材或补渠道授权")
    if derivative_allowed is None:
        status = "REVIEW" if status == "PASS" else status
        reasons.append("derivative_unknown（是否允许改编未声明）")
        tasks.append("确认改编权限并登记")
    if attribution_required is True and attribution_present is not True:
        status = "REVIEW" if status == "PASS" else status
        reasons.append("attribution_gap（授权要求署名但未见署名落实）")
        tasks.append("补齐署名或核对署名位置")
    if attribution_required is None:
        status = "REVIEW" if status == "PASS" else status
        reasons.append("attribution_requirement_unknown（署名要求未声明）")
        tasks.append("确认是否要求署名")

    window = None
    if due_in_days is not None and 1 <= due_in_days:
        if due_in_days <= 30:
            window = "due_30"
        elif due_in_days <= 60:
            window = "due_60"
        elif due_in_days <= 90:
            window = "due_90"
    return {
        "asset_id": asset_id, "asset_type": asset_type, "source": source,
        "license_type": license_type, "proof_reference": proof,
        "permitted_channels": permitted_channels, "actual_channels": actual_channels,
        "territories": territories, "actual_territory": actual_territory,
        "expires_on": None if expires_kind is None else ("perpetual" if expires_kind == "perpetual" else expires_val.isoformat()),
        "derivative_allowed": derivative_allowed, "attribution_required": attribution_required,
        "attribution_present": attribution_present, "status": status,
        "reasons": reasons, "tasks": tasks, "due_window": window,
        "note": "本技能不联网核验授权、不读取凭证指向文件、不打开 URL、不下侵权结论。"}


def analyze(data):
    if not isinstance(data, dict) or "assets" not in data:
        raise ValueError("input must contain an assets array")
    as_of_raw = data.get("as_of_date")
    if not isinstance(as_of_raw, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of_raw.strip()):
        raise ValueError("as_of_date must be ISO YYYY-MM-DD")
    as_of = date.fromisoformat(as_of_raw.strip())
    project = text(str(data.get("project") or "未命名项目"), "project", maximum=120)
    assets = data["assets"]
    if not isinstance(assets, list) or not 1 <= len(assets) <= 1000:
        raise ValueError("assets must be a list of 1-1000 items")
    results = [audit_asset(item, as_of) for item in assets]
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    due30 = [r["asset_id"] for r in results if r["due_window"] == "due_30"]
    due60 = [r["asset_id"] for r in results if r["due_window"] == "due_60"]
    due90 = [r["asset_id"] for r in results if r["due_window"] == "due_90"]
    tasks = [{"asset_id": r["asset_id"], "task": t} for r in results for t in r["tasks"]]
    rows = [["asset", "type", "license", "proof", "channels", "territory", "expires", "attribution", "status"]]
    for r in results:
        rows.append([r["asset_id"], r["asset_type"], r["license_type"] or "?",
                     "有" if r["proof_reference"] else "缺",
                     ("%s→%s" % ("/".join(r["permitted_channels"] or []), "/".join(r["actual_channels"] or []))),
                     r["actual_territory"] or "?", r["expires_on"] or "?",
                     ("%s/%s" % (r["attribution_required"], r["attribution_present"])) if r["attribution_required"] is not None else "?",
                     r["status"]])
    summary = {
        "as_of_date": as_of.isoformat(), "project": project, "asset_count": len(results),
        "status_counts": counts,
        "due_30_days": due30 or None, "due_60_days": due60 or None, "due_90_days": due90 or None,
        "follow_up_tasks": tasks, "assets": results}
    summary["markdown_summary"] = (
        "# 商用素材授权台账（" + project + " / " + as_of.isoformat() + "）\n\n"
        "共 " + str(len(results)) + " 项素材：BLOCK " + str(counts.get("BLOCK", 0)) + "，REVIEW " +
        str(counts.get("REVIEW", 0)) + "，PASS " + str(counts.get("PASS", 0)) + "，UNKNOWN " +
        str(counts.get("UNKNOWN", 0)) + "。\n\n" + md_table(rows) +
        "\n\n口径：BLOCK=已到期或渠道/地域越界（需停用或补授权）；REVIEW=缺证、未声明期限/改编/署名、到期当日或 30 天内到期；"
        "PASS=凭证与范围齐备且未过期；UNKNOWN=数据缺失。本台账不联网核验、不下侵权结论，30/60/90 天到期与补件任务见输出明细。")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="UTF-8 JSON file; maximum 2 MB")
    args = parser.parse_args()
    try:
        path = Path(args.input)
        with path.open("rb") as handle:
            raw = handle.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("input exceeds 2 MB")
        data = json.loads(raw.decode("utf-8-sig"))
        print(json.dumps(analyze(data), ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError):
        print(json.dumps({"error": "invalid_input",
                          "message": "请对照 references/guide.md 检查素材字段、许可集合与 ISO 日期。"}),
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
