#!/usr/bin/env python3
"""自由职业项目启动对齐包 — offline freelancer kickoff alignment engine.

Pure Python 3.9+ standard library. Reads exactly one local JSON file and writes
one JSON document to stdout. No network, no filesystem writes, no child
processes, no contract signing, no change acceptance, no price or deadline
commitment, no client contact, no PM/CRM writes, no file upload and no inferred
legal terms: evidence references are handled as file *basenames*.

Usage:
    python3 scripts/run.py <input.json>
"""
import json
import re
import sys
import unicodedata
from datetime import datetime

VERSION = "1.0.0"

# --------------------------------------------------------------------------
# fixed vocabulary (documented in references/guide.md)
# --------------------------------------------------------------------------
# A deliverable and the project both end in exactly one of five states. The
# order below is the severity order used everywhere for aggregation.
DELIVERABLE_STATES = (
    "READY", "ACTION_NEEDED", "WAITING_ON_CLIENT",
    "INSUFFICIENT_EVIDENCE", "BLOCKED",
)
SEVERITY = {"READY": 0, "ACTION_NEEDED": 1, "WAITING_ON_CLIENT": 2,
            "INSUFFICIENT_EVIDENCE": 3, "BLOCKED": 4}

# Approval status is a closed vocabulary: a value outside it cannot be mapped
# onto any exact behaviour, so it becomes an unknown rather than a guess.
APPROVAL_STATUSES = ("approved", "pending", "not_requested", "rejected")
# What the client owes. `received` and `waived` are the only terminal facts; a
# missing or off-vocabulary status is never treated as received.
CLIENT_INPUT_STATUSES = ("received", "not_received", "partial", "waived")
CLIENT_INPUT_DONE = ("received", "waived")

CHANGE_PROCESS_STEPS = ("propose", "estimate", "approve", "schedule")

DELIVERABLE_BLOCKER_ORDER = (
    "INVALID_DELIVERABLE_RECORD", "MISSING_DELIVERABLE_ID",
    "DUPLICATE_DELIVERABLE_ID", "UNKNOWN_APPROVER", "UNKNOWN_DEPENDENCY",
    "INVALID_DEPENDENCY_ENTRY", "DEPENDENCY_CYCLE", "INVALID_DUE_AT",
    "DUE_BEFORE_START",
)
DELIVERABLE_UNKNOWN_ORDER = (
    "MISSING_DESCRIPTION", "MISSING_FORMAT", "MISSING_QUANTITY",
    "MISSING_ACCEPTANCE_CRITERIA", "MISSING_APPROVER",
    "MISSING_APPROVAL_RECORD", "UNKNOWN_APPROVAL_STATUS",
    "MISSING_OUT_OF_SCOPE", "MISSING_DUE_AT",
)
DELIVERABLE_WAITING_ORDER = ("CLIENT_INPUT_PENDING", "UNKNOWN_INPUT_STATUS")
DELIVERABLE_ACTION_ORDER = (
    "APPROVAL_PENDING", "APPROVAL_NOT_REQUESTED", "APPROVAL_REJECTED",
    "DUE_AFTER_TARGET",
)

PROJECT_UNKNOWN_ORDER = (
    "MISSING_PROJECT_NAME", "MISSING_TARGET_DELIVERY_AT",
    "MISSING_COMMUNICATION", "MISSING_COMMUNICATION_CHANNEL",
    "MISSING_COMMUNICATION_CADENCE", "MISSING_COMMUNICATION_RESPONSE_TARGET",
    "MISSING_COMMUNICATION_ESCALATION", "MISSING_CHANGE_PROCESS",
    "MISSING_CHANGE_PROCESS_STEP", "MISSING_DELIVERABLES", "MISSING_PARTIES",
    "CONTACT_CONSENT_UNKNOWN",
)
PROJECT_BLOCKER_ORDER = (
    "MISSING_PROJECT_ID", "MISSING_START_AT", "MISSING_TIMEZONE",
    "INVALID_START_AT", "INVALID_TARGET_DELIVERY_AT", "TARGET_BEFORE_START",
)

INCOMPLETE_CODES = ("MISSING_PROJECT_ID", "MISSING_START_AT", "MISSING_TIMEZONE",
                    "INVALID_START_AT", "INVALID_TARGET_DELIVERY_AT")

PLACEHOLDER = "已隐藏疑似提示注入文本"

CRED_KEY = re.compile(
    r"(api[_-]?key|secret|password|passwd|token|credential|"
    r"private[_-]?key|access[_-]?key|client[_-]?secret|auth[_-]?token|"
    r"passphrase|bearer)",
    re.I,
)
CRED_VALUE = (
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE " + r"KEY-----"),
    re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(?<![A-Za-z0-9])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."
               r"[A-Za-z0-9_-]{10,}"),
)

# Injection is flagged only when an action verb and an instruction word appear
# in the SAME sentence, so ordinary project notes are not mislabelled.
INJ_ACTION = (
    "忽略", "无视", "跳过", "覆盖", "改写", "删除", "执行", "服从", "绕过",
    "ignore", "disregard", "override", "bypass", "forget",
)
INJ_TARGET = (
    "指令", "规则", "提示", "系统", "要求", "约束",
    "instruction", "rule", "prompt", "system", "constraint",
)

MD_ESCAPE = "\\`*_{}[]()#+-|<>~!"

DT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
BASENAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,180}$")
URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

DISCLAIMER = (
    "本输出是自由职业项目启动对齐的人工准备材料，不是合同、报价、工期承诺、"
    "法律或税务结论；所有结论只由输入中明示的事实推导，缺失信息一律保持未知，"
    "真实范围、验收与审批必须由双方人工确认。"
)

NO_AUTOMATION_DECLARATION = (
    "本工具不签合同、不接受任何范围或价格或工期的变更、不承诺价格与交期、"
    "不自动联系客户、不写任何 PM/CRM 或项目管理系统、不上传任何文件、"
    "不推断法律条款；它只输出一份供人工确认的对齐材料。"
)

HUMAN_CONFIRM_BASE = (
    "合同条款、签署与法律效力必须由人工与双方确认，本工具不生成合同、不代签",
    "任何范围变更、价格变更与交期变更必须由人工书面确认，本工具只列出待确认步骤",
    "对外沟通必须由人工审阅并亲自发送，本工具只生成 DRAFT_NOT_SENT 草稿、绝不外发",
    "验收结论与付款条件必须由双方人工确认，本工具不认定已验收、不认定已批准",
)

HUMAN_CHECKLIST_BASE = (
    "逐条对齐范围内交付物与范围外事项，双方对「不做什么」达成一致",
    "为每个交付物确认格式、数量与可核对的验收标准",
    "为每个交付物指定审批人，并确认审批状态（存在审批人 ≠ 已批准）",
    "确认客户需提供的素材/账号/文本/反馈及其负责人与到期时间",
    "确认沟通渠道、节奏、响应目标与紧急升级路径",
    "确认变更流程的提出、估算、批准与排期四个步骤",
    "确认依赖顺序与根阻塞项，先解决根阻塞再排期",
    "确认联系同意：仅对明确同意的对象准备草稿，未同意者不生成任何可发送内容",
)

CODE_QUESTIONS = {
    "MISSING_DESCRIPTION": "这个交付物具体交付什么？请补充一句话描述。",
    "MISSING_FORMAT": "这个交付物的交付格式是什么（文件类型/尺寸/语言等）？",
    "MISSING_QUANTITY": "这个交付物的数量是多少（件/页/版等）？",
    "MISSING_ACCEPTANCE_CRITERIA": "这个交付物怎样算通过验收？请给出可核对的验收标准。",
    "MISSING_APPROVER": "这个交付物由谁审批？请指定审批人。",
    "MISSING_APPROVAL_RECORD": "这个交付物的审批状态是什么？目前只有审批人、没有审批记录。",
    "UNKNOWN_APPROVAL_STATUS": "这条审批的状态无法识别，请用 approved/pending/not_requested/rejected 之一说明。",
    "MISSING_OUT_OF_SCOPE": "这个交付物明确不包含哪些内容？请声明范围外事项。",
    "MISSING_DUE_AT": "这个交付物的到期时间是什么（需带时区偏移）？",
    "UNKNOWN_DEPENDENCY": "这个交付物依赖的另一项交付物编号不存在，请给出正确的编号。",
    "INVALID_DEPENDENCY_ENTRY": "依赖项里有无法识别的条目，请只填写交付物编号。",
    "DEPENDENCY_CYCLE": "依赖出现循环，请人工打破循环后重新排期。",
    "INVALID_DUE_AT": "到期时间无法解析（需带时区偏移），请给出合规时间。",
    "DUE_BEFORE_START": "该交付物的到期时间早于项目启动时间，请确认时间是否正确。",
    "DUE_AFTER_TARGET": "该交付物的到期时间晚于项目目标交付时间，请确认哪个时间需要调整。",
    "MISSING_PROJECT_NAME": "项目名称是什么？",
    "MISSING_TARGET_DELIVERY_AT": "项目的目标交付时间是什么（需带时区偏移）？",
    "MISSING_COMMUNICATION": "项目沟通方式是什么？需要渠道、节奏、响应目标与升级路径。",
    "MISSING_COMMUNICATION_CHANNEL": "项目沟通使用什么渠道？",
    "MISSING_COMMUNICATION_CADENCE": "项目沟通的节奏是什么（多久同步一次）？",
    "MISSING_COMMUNICATION_RESPONSE_TARGET": "期望的响应目标是什么（多久内回复）？",
    "MISSING_COMMUNICATION_ESCALATION": "紧急情况下的升级路径是什么？",
    "MISSING_CHANGE_PROCESS": "变更流程是什么？需要提出、估算、批准、排期四步。",
    "MISSING_CHANGE_PROCESS_STEP": "变更流程缺少步骤，请补齐提出/估算/批准/排期。",
    "MISSING_DELIVERABLES": "项目包含哪些交付物？目前没有可对齐的交付物。",
    "MISSING_PARTIES": "项目涉及哪些参与方？目前没有可对齐的参与方。",
    "CONTACT_CONSENT_UNKNOWN": "该参与方是否同意被联系？未明确同意前不会生成任何可发送内容。",
    "MISSING_OWNER": "该事项没有负责人，请指定负责人。",
    "UNKNOWN_PARTY": "该负责人编号在参与方列表中不存在，请给出正确的参与方编号。",
}
DEFAULT_QUESTION = "请补充该事项所需的事实。"


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------
def clean_text(value):
    """Collapse control characters and whitespace; never raises."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = "".join(
        ch for ch in text
        if ch == "\n" or ch == "\t" or unicodedata.category(ch)[0] != "C"
    )
    return re.sub(r"\s+", " ", text).strip()


def esc(value):
    """Escape Markdown structure characters so untrusted text cannot forge layout."""
    text = clean_text(value)
    return "".join("\\" + ch if ch in MD_ESCAPE else ch for ch in text)


def has_text(value):
    return isinstance(value, str) and value.strip() != ""


def as_list(value):
    return value if isinstance(value, list) else []


def as_dict(value):
    return value if isinstance(value, dict) else {}


def parse_dt(value):
    """Return a tz-aware datetime, or None. A value without an offset is invalid."""
    if not isinstance(value, str) or not DT_RE.match(value.strip()):
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def date_part(value):
    parsed = parse_dt(value)
    return parsed.date().isoformat() if parsed else None


def scan_injection(text):
    """True only when an action verb and an instruction word share one sentence."""
    cleaned = clean_text(text)
    if not cleaned:
        return False
    for sentence in re.split(r"[。！？!?;；\n]", cleaned):
        low = sentence.lower()
        if any(word.lower() in low for word in INJ_ACTION) and \
                any(word.lower() in low for word in INJ_TARGET):
            return True
    return False


def iter_strings(node, path=""):
    """Yield (path, key_or_None, value) for every string leaf in a JSON tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + "/" + str(key) if path else str(key)
            yield from iter_strings(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from iter_strings(value, "%s[%d]" % (path, index))
    elif isinstance(node, str):
        yield path, None, node


def find_credentials(node, path=""):
    """Return the field paths of credential-shaped keys or values.

    Only the *location* is returned (never the value), so a refusal can name
    where the problem is without echoing any secret back to the caller.
    """
    hits = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = path + "/" + str(key) if path else str(key)
            if isinstance(key, str) and CRED_KEY.search(key):
                hits.append(child)
            hits.extend(find_credentials(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(find_credentials(value, "%s[%d]" % (path, index)))
    elif isinstance(node, str):
        if any(pattern.search(node) for pattern in CRED_VALUE):
            hits.append(path or "value")
    seen, out = set(), []
    for item in hits:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def safe_ref(value):
    """Return (sanitised_basename_or_None, refusal_reason_or_None) for a reference.

    Only a single filename is accepted. Path separators and URL schemes are
    refused outright, and the original value is never echoed anywhere.
    """
    text = clean_text(value)
    if not text:
        return None, None
    if URL_RE.match(text):
        return None, "URL_REFERENCE"
    if "/" in text or "\\" in text or ":" in text or ".." in text:
        return None, "PATH_REFERENCE"
    if not BASENAME_RE.match(text):
        return None, "UNSAFE_REFERENCE"
    return text, None


# --------------------------------------------------------------------------
# reference / record parsing
# --------------------------------------------------------------------------
def collect_refs(records, field):
    """Scan a list of records for a reference field; report refusals and basenames."""
    accepted, refused = [], []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        raw = record.get(field)
        name, reason = safe_ref(raw)
        if reason:
            refused.append({"path": "%s[%d]/%s" % (field, index, field),
                            "reason": reason})
        elif name:
            accepted.append({"path": "%s[%d]/%s" % (field, index, field),
                             "name": name})
    return accepted, refused


def collect_refs_from_list(values, field):
    accepted, refused = [], []
    for index, raw in enumerate(values):
        name, reason = safe_ref(raw)
        if reason:
            refused.append({"path": "%s[%d]" % (field, index), "reason": reason})
        elif name:
            accepted.append({"path": "%s[%d]" % (field, index), "name": name})
    return accepted, refused


# --------------------------------------------------------------------------
# dependency cycles (Tarjan, iterative)
# --------------------------------------------------------------------------
def strongly_connected(ids, edges):
    index = {name: i for i, name in enumerate(ids)}
    adjacency = {name: [d for d in edges.get(name, []) if d in index]
                 for name in ids}
    counter = [0]
    stack, on_stack, low, num, result = [], set(), {}, {}, []

    def visit(root):
        work = [(root, 0)]
        while work:
            node, pointer = work.pop()
            if pointer == 0:
                num[node] = counter[0]
                low[node] = counter[0]
                counter[0] += 1
                stack.append(node)
                on_stack.add(node)
            recurse = False
            neighbours = adjacency[node]
            while pointer < len(neighbours):
                nxt = neighbours[pointer]
                pointer += 1
                if nxt not in num:
                    work.append((node, pointer))
                    work.append((nxt, 0))
                    recurse = True
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], num[nxt])
            if recurse:
                continue
            if low[node] == num[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                result.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])

    for name in ids:
        if name not in num:
            visit(name)
    return result


def detect_cycles(ids, edges):
    cycles = []
    for component in strongly_connected(ids, edges):
        if len(component) > 1:
            cycles.append(sorted(component))
        elif component and component[0] in edges.get(component[0], []):
            cycles.append([component[0]])
    cycles.sort(key=lambda item: item[0] if item else "")
    return cycles


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def party_maps(payload):
    parties = as_list(payload.get("parties"))
    rows, by_id, duplicates, unknown_refs = [], {}, [], []
    for index, party in enumerate(parties):
        if not isinstance(party, dict):
            rows.append({"path": "parties[%d]" % index, "party_id": None,
                         "role": None, "decision_authority": None,
                         "contact_consent": None, "invalid": True})
            continue
        pid = clean_text(party.get("party_id"))
        consent = party.get("contact_consent")
        row = {
            "path": "parties[%d]" % index,
            "party_id": pid or None,
            "role": clean_text(party.get("role")) or None,
            "decision_authority": party.get("decision_authority")
            if isinstance(party.get("decision_authority"), bool) else None,
            "contact_consent": consent if isinstance(consent, bool) else None,
            "invalid": False,
        }
        rows.append(row)
        if pid:
            if pid in by_id:
                duplicates.append({"id": pid, "kind": "DUPLICATE_PARTY_ID",
                                   "paths": [by_id[pid]["path"], row["path"]]})
            else:
                by_id[pid] = row
    return rows, by_id, duplicates


def parse_approvals(payload, parties):
    rows, refs, refused = [], [], []
    for index, raw in enumerate(as_list(payload.get("approvals"))):
        path = "approvals[%d]" % index
        if not isinstance(raw, dict):
            rows.append({"path": path, "approval_id": None, "invalid": True,
                         "subject": None, "owner_party_id": None,
                         "deliverable_id": None, "status": "invalid",
                         "due_at": None, "evidence_ref": None,
                         "owner_known": False, "status_known": False})
            continue
        owner = clean_text(raw.get("owner_party_id")) or None
        status = clean_text(raw.get("status")).lower()
        name, reason = safe_ref(raw.get("evidence_ref"))
        if reason:
            refused.append({"path": path + "/evidence_ref", "reason": reason})
        elif name:
            refs.append({"path": path + "/evidence_ref", "name": name})
        rows.append({
            "path": path,
            "approval_id": clean_text(raw.get("approval_id")) or None,
            "subject": clean_text(raw.get("subject")) or None,
            "owner_party_id": owner,
            "deliverable_id": clean_text(raw.get("deliverable_id")) or None,
            "status": status if status in APPROVAL_STATUSES else "unknown",
            "due_at": clean_text(raw.get("due_at")) or None,
            "evidence_ref": name,
            "owner_known": owner in parties if owner else False,
            "status_known": status in APPROVAL_STATUSES,
            "invalid": False,
        })
    return rows, refs, refused


def parse_client_inputs(payload, parties):
    rows, refused, accepted = [], [], []
    for index, raw in enumerate(as_list(payload.get("client_inputs"))):
        path = "client_inputs[%d]" % index
        if not isinstance(raw, dict):
            rows.append({"path": path, "input_id": None, "invalid": True,
                         "kind": None, "description": None,
                         "owner_party_id": None, "deliverable_id": None,
                         "status": "invalid", "due_at": None,
                         "owner_known": False, "status_known": False})
            continue
        owner = clean_text(raw.get("owner_party_id")) or None
        status = clean_text(raw.get("status")).lower()
        name, reason = safe_ref(raw.get("evidence_ref"))
        if reason:
            refused.append({"path": path + "/evidence_ref", "reason": reason})
        elif name:
            accepted.append({"path": path + "/evidence_ref", "name": name})
        rows.append({
            "path": path,
            "input_id": clean_text(raw.get("input_id")) or None,
            "kind": clean_text(raw.get("kind")) or None,
            "description": clean_text(raw.get("description")) or None,
            "owner_party_id": owner,
            "deliverable_id": clean_text(raw.get("deliverable_id")) or None,
            "status": status if status in CLIENT_INPUT_STATUSES else "unknown",
            "due_at": clean_text(raw.get("due_at")) or None,
            "evidence_ref": name,
            "owner_known": owner in parties if owner else False,
            "status_known": status in CLIENT_INPUT_STATUSES,
            "invalid": False,
        })
    return rows, accepted, refused


def analyse(payload):
    as_of = payload.get("as_of")
    as_of_dt = parse_dt(as_of)
    project_raw = payload.get("project")
    project = as_dict(project_raw)

    parties, party_by_id, party_dups = party_maps(payload)
    approvals, approval_refs, approval_refused = parse_approvals(payload, party_by_id)
    client_inputs, input_refs, input_refused = parse_client_inputs(payload, party_by_id)

    refused_refs = approval_refused + input_refused
    accepted_top, refused_top = collect_refs_from_list(
        as_list(payload.get("evidence_refs")), "evidence_refs")
    injection_flagged = []

    # --- injection scan over free text -----------------------------------
    def scan(path, value):
        if scan_injection(value) and path not in injection_flagged:
            injection_flagged.append(path)
            return True
        return False

    free_paths = [("project/name", project.get("name"))]
    communication = as_dict(payload.get("communication"))
    for key in ("channel", "cadence", "response_target", "urgent_escalation"):
        free_paths.append(("communication/" + key, communication.get(key)))
    change_process = as_dict(payload.get("change_process"))
    for key in CHANGE_PROCESS_STEPS:
        free_paths.append(("change_process/" + key, change_process.get(key)))
    free_paths.append(("notes", payload.get("notes")))
    for index, raw in enumerate(as_list(payload.get("deliverables"))):
        if isinstance(raw, dict):
            free_paths.append(("deliverables[%d]/description" % index,
                               raw.get("description")))
            for item in as_list(raw.get("out_of_scope")):
                free_paths.append(("deliverables[%d]/out_of_scope" % index, item))
            for item in as_list(raw.get("acceptance_criteria")):
                free_paths.append(("deliverables[%d]/acceptance_criteria" % index,
                                   item))
    for index, raw in enumerate(as_list(payload.get("approvals"))):
        if isinstance(raw, dict):
            free_paths.append(("approvals[%d]/subject" % index, raw.get("subject")))
    for index, raw in enumerate(as_list(payload.get("client_inputs"))):
        if isinstance(raw, dict):
            free_paths.append(("client_inputs[%d]/description" % index,
                               raw.get("description")))
    hidden = set()
    for path, value in free_paths:
        if scan(path, value):
            hidden.add(path)

    # --- project level facts ---------------------------------------------
    project_blockers, project_unknowns = [], []
    project_id = clean_text(project.get("project_id"))
    start_dt = parse_dt(project.get("start_at"))
    target_dt = parse_dt(project.get("target_delivery_at"))
    timezone = clean_text(project.get("timezone"))

    if not project_id:
        project_blockers.append("MISSING_PROJECT_ID")
    if not has_text(project.get("start_at")):
        project_blockers.append("MISSING_START_AT")
    elif start_dt is None:
        project_blockers.append("INVALID_START_AT")
    if not timezone:
        project_blockers.append("MISSING_TIMEZONE")
    if has_text(project.get("target_delivery_at")) and target_dt is None:
        project_blockers.append("INVALID_TARGET_DELIVERY_AT")
    if start_dt and target_dt and target_dt < start_dt:
        project_blockers.append("TARGET_BEFORE_START")
    project_blockers = [code for code in PROJECT_BLOCKER_ORDER
                        if code in set(project_blockers)]

    if not has_text(project.get("name")):
        project_unknowns.append("MISSING_PROJECT_NAME")
    if not has_text(project.get("target_delivery_at")):
        project_unknowns.append("MISSING_TARGET_DELIVERY_AT")

    if not communication:
        project_unknowns.append("MISSING_COMMUNICATION")
    else:
        if not has_text(communication.get("channel")):
            project_unknowns.append("MISSING_COMMUNICATION_CHANNEL")
        if not has_text(communication.get("cadence")):
            project_unknowns.append("MISSING_COMMUNICATION_CADENCE")
        if not has_text(communication.get("response_target")):
            project_unknowns.append("MISSING_COMMUNICATION_RESPONSE_TARGET")
        if not has_text(communication.get("urgent_escalation")):
            project_unknowns.append("MISSING_COMMUNICATION_ESCALATION")

    if not change_process:
        project_unknowns.append("MISSING_CHANGE_PROCESS")
    else:
        missing_steps = [step for step in CHANGE_PROCESS_STEPS
                         if not has_text(change_process.get(step))]
        if missing_steps:
            project_unknowns.append("MISSING_CHANGE_PROCESS_STEP")

    raw_deliverables = as_list(payload.get("deliverables"))
    if not raw_deliverables:
        project_unknowns.append("MISSING_DELIVERABLES")
    if not parties:
        project_unknowns.append("MISSING_PARTIES")
    project_unknowns = [code for code in PROJECT_UNKNOWN_ORDER
                        if code in set(project_unknowns)]

    # --- deliverables -----------------------------------------------------
    deliverables, duplicate_ids = [], list(party_dups)
    id_index = {}
    for index, raw in enumerate(raw_deliverables):
        path = "deliverables[%d]" % index
        if not isinstance(raw, dict):
            deliverables.append({
                "path": path, "index": index, "deliverable_id": None,
                "description": None, "format": None, "quantity": None,
                "due_at": None, "acceptance_criteria": [],
                "out_of_scope": [], "dependencies": [],
                "approver_party_id": None, "invalid": True,
                "state": "BLOCKED", "findings": ["INVALID_DELIVERABLE_RECORD"],
                "blockers": ["INVALID_DELIVERABLE_RECORD"], "unknown": [],
                "waiting": [], "actions": [], "root_blocker": True,
                "target_exceeded": False})
            continue
        did = clean_text(raw.get("deliverable_id")) or None
        row = {
            "path": path, "index": index, "deliverable_id": did,
            "description": clean_text(raw.get("description")) or None,
            "format": clean_text(raw.get("format")) or None,
            "quantity": None,
            "due_at": clean_text(raw.get("due_at")) or None,
            "acceptance_criteria": [clean_text(item)
                                    for item in as_list(raw.get("acceptance_criteria"))
                                    if has_text(item)],
            "out_of_scope": [clean_text(item)
                             for item in as_list(raw.get("out_of_scope"))
                             if has_text(item)],
            "dependencies": [],
            "approver_party_id": clean_text(raw.get("approver_party_id")) or None,
            "invalid": False,
        }
        quantity = raw.get("quantity")
        if isinstance(quantity, bool):
            row["quantity"] = None
        elif isinstance(quantity, (int, float)):
            row["quantity"] = quantity
        elif has_text(quantity):
            row["quantity"] = clean_text(quantity)
        deliverables.append(row)
        if did:
            if did in id_index:
                duplicate_ids.append({"id": did,
                                      "kind": "DUPLICATE_DELIVERABLE_ID",
                                      "paths": [deliverables[id_index[did]]["path"],
                                                path]})
            else:
                id_index[did] = index

    # dependency edges with per-entry validation
    edges = {}
    for row in deliverables:
        raw = raw_deliverables[row["index"]] if not row["invalid"] else {}
        deps = []
        for item in as_list(raw.get("dependencies")):
            name = clean_text(item)
            if name:
                deps.append(name)
        row["dependencies"] = deps
        if row["deliverable_id"]:
            edges[row["deliverable_id"]] = deps

    cycles = detect_cycles([d["deliverable_id"] for d in deliverables
                            if d["deliverable_id"]], edges)
    cycle_members = {member for cycle in cycles for member in cycle}

    duplicates_by_id = {}
    for entry in duplicate_ids:
        if entry["kind"] == "DUPLICATE_DELIVERABLE_ID":
            duplicates_by_id.setdefault(entry["id"], []).append(entry)

    unknown_parties = []
    for row in approvals:
        if row.get("owner_party_id") and not row.get("owner_known"):
            unknown_parties.append({"path": row["path"] + "/owner_party_id",
                                    "party_id": row["owner_party_id"]})
    for row in client_inputs:
        if row.get("owner_party_id") and not row.get("owner_known"):
            unknown_parties.append({"path": row["path"] + "/owner_party_id",
                                    "party_id": row["owner_party_id"]})
    for row in deliverables:
        if row.get("approver_party_id") and \
                row["approver_party_id"] not in party_by_id:
            unknown_parties.append({"path": row["path"] + "/approver_party_id",
                                    "party_id": row["approver_party_id"]})

    approvals_by_deliverable = {}
    for row in approvals:
        if row.get("deliverable_id"):
            approvals_by_deliverable.setdefault(row["deliverable_id"], []).append(row)
    inputs_by_deliverable = {}
    for row in client_inputs:
        if row.get("deliverable_id"):
            inputs_by_deliverable.setdefault(row["deliverable_id"], []).append(row)

    known_ids = set(id_index)
    for row in deliverables:
        if row["invalid"]:
            continue
        blockers, unknown, waiting, actions = [], [], [], []
        did = row["deliverable_id"]
        if not did:
            blockers.append("MISSING_DELIVERABLE_ID")
        if did and did in duplicates_by_id:
            blockers.append("DUPLICATE_DELIVERABLE_ID")
        approver = row["approver_party_id"]
        if approver and approver not in party_by_id:
            blockers.append("UNKNOWN_APPROVER")
        for dep in row["dependencies"]:
            if dep not in known_ids:
                blockers.append("UNKNOWN_DEPENDENCY")
        if did and did in cycle_members:
            blockers.append("DEPENDENCY_CYCLE")
        if has_text(row["due_at"]):
            due_dt = parse_dt(row["due_at"])
            if due_dt is None:
                blockers.append("INVALID_DUE_AT")
            else:
                if start_dt and due_dt < start_dt:
                    blockers.append("DUE_BEFORE_START")
                if target_dt and due_dt > target_dt:
                    actions.append("DUE_AFTER_TARGET")
        else:
            unknown.append("MISSING_DUE_AT")

        if not has_text(row["description"]):
            unknown.append("MISSING_DESCRIPTION")
        if not has_text(row["format"]):
            unknown.append("MISSING_FORMAT")
        if row["quantity"] is None:
            unknown.append("MISSING_QUANTITY")
        if not row["acceptance_criteria"]:
            unknown.append("MISSING_ACCEPTANCE_CRITERIA")
        if not approver:
            unknown.append("MISSING_APPROVER")
        if not row["out_of_scope"]:
            unknown.append("MISSING_OUT_OF_SCOPE")

        linked_approvals = approvals_by_deliverable.get(did, []) if did else []
        if approver and not linked_approvals:
            unknown.append("MISSING_APPROVAL_RECORD")
        for record in linked_approvals:
            if record.get("invalid") or not record.get("status_known"):
                unknown.append("UNKNOWN_APPROVAL_STATUS")
            elif record["status"] == "approved":
                pass
            elif record["status"] == "pending":
                actions.append("APPROVAL_PENDING")
            elif record["status"] == "not_requested":
                actions.append("APPROVAL_NOT_REQUESTED")
            elif record["status"] == "rejected":
                actions.append("APPROVAL_REJECTED")

        for record in inputs_by_deliverable.get(did, []) if did else []:
            if record.get("invalid") or not record.get("status_known"):
                waiting.append("UNKNOWN_INPUT_STATUS")
            elif record["status"] not in CLIENT_INPUT_DONE:
                waiting.append("CLIENT_INPUT_PENDING")

        blockers = [code for code in DELIVERABLE_BLOCKER_ORDER
                    if code in set(blockers)]
        unknown = [code for code in DELIVERABLE_UNKNOWN_ORDER
                   if code in set(unknown)]
        waiting = [code for code in DELIVERABLE_WAITING_ORDER
                   if code in set(waiting)]
        actions = [code for code in DELIVERABLE_ACTION_ORDER
                   if code in set(actions)]
        if blockers:
            state = "BLOCKED"
        elif unknown:
            state = "INSUFFICIENT_EVIDENCE"
        elif waiting:
            state = "WAITING_ON_CLIENT"
        elif actions:
            state = "ACTION_NEEDED"
        else:
            state = "READY"
        row["blockers"] = blockers
        row["unknown"] = unknown
        row["waiting"] = waiting
        row["actions"] = actions
        row["findings"] = blockers + unknown + waiting + actions
        row["state"] = state
        row["target_exceeded"] = "DUE_AFTER_TARGET" in actions

    # root blockers: blocked nodes with no blocked dependency
    blocked_ids = {d["deliverable_id"] for d in deliverables
                   if d.get("state") == "BLOCKED" and d.get("deliverable_id")}
    for row in deliverables:
        if row.get("state") != "BLOCKED":
            row["root_blocker"] = False
            continue
        upstream_blocked = any(dep in blocked_ids for dep in row["dependencies"])
        row["root_blocker"] = not upstream_blocked
    root_blockers = [{"deliverable_id": d["deliverable_id"], "path": d["path"],
                      "findings": d["findings"],
                      "dependencies": d["dependencies"]}
                     for d in deliverables if d.get("root_blocker")]

    # --- aggregation ------------------------------------------------------
    status_counts = {name: 0 for name in DELIVERABLE_STATES}
    for row in deliverables:
        status_counts[row["state"]] = status_counts.get(row["state"], 0) + 1

    # --- waiting on client / timeline / responsibility / missing ----------
    waiting_on_client = []
    for row in client_inputs:
        if row.get("invalid"):
            continue
        if not row.get("status_known") or row["status"] not in CLIENT_INPUT_DONE:
            due_dt = parse_dt(row.get("due_at"))
            waiting_on_client.append({
                "kind": "CLIENT_INPUT", "id": row.get("input_id") or row["path"],
                "path": row["path"],
                "owner_party_id": row.get("owner_party_id"),
                "deliverable_id": row.get("deliverable_id"),
                "due_at": row.get("due_at"),
                "state": row["status"] if row.get("status_known") else "unknown",
                "overdue": bool(due_dt and as_of_dt and due_dt < as_of_dt),
            })
    for row in approvals:
        if row.get("invalid"):
            continue
        if row.get("status") in ("pending", "not_requested", "unknown"):
            due_dt = parse_dt(row.get("due_at"))
            waiting_on_client.append({
                "kind": "APPROVAL", "id": row.get("approval_id") or row["path"],
                "path": row["path"],
                "owner_party_id": row.get("owner_party_id"),
                "deliverable_id": row.get("deliverable_id"),
                "due_at": row.get("due_at"),
                "state": row["status"],
                "overdue": bool(due_dt and as_of_dt and due_dt < as_of_dt),
            })

    timeline_conflicts = []
    for row in deliverables:
        if "DUE_BEFORE_START" in row.get("blockers", []):
            timeline_conflicts.append({
                "code": "DUE_BEFORE_START", "path": row["path"],
                "deliverable_id": row["deliverable_id"],
                "detail": "due_at 早于项目 start_at"})
        if "DUE_AFTER_TARGET" in row.get("actions", []):
            timeline_conflicts.append({
                "code": "DUE_AFTER_TARGET", "path": row["path"],
                "deliverable_id": row["deliverable_id"],
                "detail": "due_at 晚于项目 target_delivery_at"})
    if "TARGET_BEFORE_START" in project_blockers:
        timeline_conflicts.append({
            "code": "TARGET_BEFORE_START", "path": "project/target_delivery_at",
            "deliverable_id": None, "detail": "目标交付时间早于启动时间"})

    responsibility_gaps = []
    for row in approvals:
        if not row.get("invalid") and not row.get("owner_party_id"):
            responsibility_gaps.append({"path": row["path"] + "/owner_party_id",
                                        "code": "MISSING_OWNER"})
    for row in client_inputs:
        if not row.get("invalid") and not row.get("owner_party_id"):
            responsibility_gaps.append({"path": row["path"] + "/owner_party_id",
                                        "code": "MISSING_OWNER"})

    missing_facts = []
    for code in project_unknowns:
        if code == "MISSING_CHANGE_PROCESS_STEP" or code == "MISSING_CHANGE_PROCESS":
            missing_facts.append({"path": "change_process", "code": code})
        elif code.startswith("MISSING_COMMUNICATION"):
            missing_facts.append({"path": "communication", "code": code})
        else:
            missing_facts.append({"path": "project", "code": code})
    for row in deliverables:
        for code in row.get("unknown", []):
            missing_facts.append({"path": row["path"], "code": code})

    # contact consent
    granted, refused_consent, unknown_consent = [], [], []
    for row in parties:
        pid = row.get("party_id")
        if not pid:
            continue
        if row.get("contact_consent") is True:
            granted.append(pid)
        elif row.get("contact_consent") is False:
            refused_consent.append(pid)
        else:
            unknown_consent.append(pid)
    if unknown_consent:
        project_unknowns = list(project_unknowns) + ["CONTACT_CONSENT_UNKNOWN"]
        for pid in unknown_consent:
            missing_facts.append({"path": "parties",
                                  "code": "CONTACT_CONSENT_UNKNOWN"})

    # --- clarification questions -----------------------------------------
    clarification_questions = []
    seen_questions = set()
    for item in missing_facts:
        key = (item["code"], item["path"])
        if key in seen_questions:
            continue
        seen_questions.add(key)
        clarification_questions.append({
            "path": item["path"], "code": item["code"],
            "question": CODE_QUESTIONS.get(item["code"], DEFAULT_QUESTION)})
    for item in waiting_on_client:
        code = "CLIENT_INPUT_PENDING" if item["kind"] == "CLIENT_INPUT" \
            else "APPROVAL_PENDING"
        key = (code, item["path"])
        if key in seen_questions:
            continue
        seen_questions.add(key)
        question = ("客户尚未提供该项，请确认何时可以提供。" if code ==
                    "CLIENT_INPUT_PENDING" else "该审批尚未完成，请确认审批人能否在截止前给出结论。")
        clarification_questions.append({"path": item["path"], "code": code,
                                        "question": question})

    # --- drafts (consent gated) ------------------------------------------
    pending_by_party = {}
    for item in waiting_on_client:
        owner = item.get("owner_party_id")
        if owner:
            pending_by_party.setdefault(owner, []).append(item)
    drafts = []
    for pid in sorted(pending_by_party):
        if pid not in granted:
            continue
        role = (party_by_id.get(pid) or {}).get("role") or ""
        lines = ["【草稿，未发送】", "以下事项需要与您确认："]
        for item in pending_by_party[pid]:
            lines.append("- %s（截止 %s）" % (
                esc(item.get("id")), esc(item.get("due_at") or "未提供")))
        lines.append("这些都是待确认事项，本消息仅为草稿，须由人工审阅后再决定是否发送。")
        drafts.append({
            "draft_id": "DRAFT-" + pid,
            "to_party_id": pid,
            "to_role": role,
            "subject": "项目%s启动对齐待确认事项" % (
                "《" + esc(project.get("name")) + "》"
                if has_text(project.get("name")) else ""),
            "body": "\n".join(lines),
            "status": "DRAFT_NOT_SENT",
        })

    # --- markdown summary -------------------------------------------------
    markdown = render_markdown(payload, project, deliverables, status_counts,
                               project_blockers, project_unknowns, waiting_on_client,
                               timeline_conflicts, responsibility_gaps,
                               clarification_questions, communication,
                               change_process, hidden, drafts)

    # --- project status ---------------------------------------------------
    project_state = aggregate_project_state(
        project_blockers, project_unknowns, deliverables, waiting_on_client,
        status_counts)

    if project_blockers and any(code in INCOMPLETE_CODES
                                for code in project_blockers):
        status = "INPUT_INCOMPLETE"
        project_state = None
    elif project_state == "BLOCKED":
        status = "BLOCKED"
    elif project_state == "READY":
        status = "READY"
    else:
        status = "GAPS_FOUND"

    result = {
        "skill": "suge-freelance-project-kickoff-alignment-pack",
        "version": VERSION,
        "as_of": clean_text(as_of) or None,
        "as_of_date": date_part(as_of),
        "status": status,
        "project_state": project_state,
        "project_status": project_state,
        "project": {
            "project_id": project_id or None,
            "name": clean_text(project.get("name")) or None,
            "start_at": clean_text(project.get("start_at")) or None,
            "target_delivery_at": clean_text(project.get("target_delivery_at"))
            or None,
            "timezone": timezone or None,
        },
        "status_counts": status_counts,
        "deliverables": [deliverable_view(row) for row in deliverables],
        "alignment_card": {
            "in_scope": [{"deliverable_id": d["deliverable_id"],
                          "description": hidden_value(
                              "deliverables[%d]/description" % d["index"],
                              d["description"], hidden)}
                         for d in deliverables if not d["invalid"]],
            "out_of_scope": [
                {"deliverable_id": d["deliverable_id"],
                 "item": hidden_value("deliverables[%d]/out_of_scope" % d["index"],
                                      item, hidden)}
                for d in deliverables if not d["invalid"]
                for item in d["out_of_scope"]],
            "acceptance_criteria": [
                {"deliverable_id": d["deliverable_id"],
                 "criterion": hidden_value(
                     "deliverables[%d]/acceptance_criteria" % d["index"],
                     item, hidden)}
                for d in deliverables if not d["invalid"]
                for item in d["acceptance_criteria"]],
            "approvers": [
                {"deliverable_id": d["deliverable_id"],
                 "approver_party_id": d["approver_party_id"],
                 "approval_status": (
                     statuses[0] if (statuses := [
                         a["status"] for a in
                         approvals_by_deliverable.get(d["deliverable_id"], [])])
                     else "no_record"),
                 "approver_known": bool(d["approver_party_id"]
                                        and d["approver_party_id"] in party_by_id)}
                for d in deliverables if not d["invalid"]],
            "client_inputs": [client_input_view(row) for row in client_inputs],
            "communication": {
                "channel": clean_text(communication.get("channel")) or None,
                "cadence": clean_text(communication.get("cadence")) or None,
                "response_target": clean_text(
                    communication.get("response_target")) or None,
                "urgent_escalation": clean_text(
                    communication.get("urgent_escalation")) or None,
            },
            "escalation_path": (
                [clean_text(communication.get("urgent_escalation"))]
                if has_text(communication.get("urgent_escalation")) else []),
            "change_process": {
                step: (clean_text(change_process.get(step)) or None)
                for step in CHANGE_PROCESS_STEPS} if change_process else None,
        },
        "dependency_graph": {
            "nodes": [d["deliverable_id"] for d in deliverables
                      if d["deliverable_id"]],
            "edges": sorted(
                [{"from": dep, "to": d["deliverable_id"]}
                 for d in deliverables if d["deliverable_id"]
                 for dep in d["dependencies"] if dep in known_ids],
                key=lambda item: (item["to"], item["from"])),
        },
        "root_blockers": root_blockers,
        "dependency_cycles": cycles,
        "waiting_on_client": waiting_on_client,
        "pending_approvals": [approval_view(row) for row in approvals
                              if row.get("status") in
                              ("pending", "not_requested", "unknown")
                              and not row.get("invalid")],
        "approvals": [approval_view(row) for row in approvals],
        "client_inputs": [client_input_view(row) for row in client_inputs],
        "parties": [party_view(row) for row in parties],
        "missing_facts": missing_facts,
        "timeline_conflicts": timeline_conflicts,
        "responsibility_gaps": responsibility_gaps,
        "duplicate_ids": duplicate_ids,
        "unknown_parties": unknown_parties,
        "refused_refs": refused_refs + refused_top,
        "accepted_refs": approval_refs + input_refs + accepted_top,
        "injection_flagged": [{"path": path, "marker": "PROMPT_INJECTION"}
                              for path in injection_flagged],
        "contact_consent": {"granted": granted, "refused": refused_consent,
                            "unknown": unknown_consent},
        "drafts": drafts,
        "clarification_questions": clarification_questions,
        "human_checklist": build_checklist(project_blockers, project_unknowns,
                                           deliverables, cycles, refused_refs,
                                           unknown_consent),
        "human_confirm_items": list(HUMAN_CONFIRM_BASE),
        "markdown_summary": markdown,
        "disclaimer": DISCLAIMER,
        "automation_declaration": NO_AUTOMATION_DECLARATION,
        "read_only": True,
        "network": False,
        "writes_files": False,
    }
    return result


def deliverable_view(row):
    return {
        "deliverable_id": row.get("deliverable_id"),
        "path": row["path"],
        "description": row.get("description"),
        "format": row.get("format"),
        "quantity": row.get("quantity"),
        "due_at": row.get("due_at"),
        "acceptance_criteria": row.get("acceptance_criteria", []),
        "out_of_scope": row.get("out_of_scope", []),
        "dependencies": row.get("dependencies", []),
        "approver_party_id": row.get("approver_party_id"),
        "state": row["state"],
        "blockers": row.get("blockers", []),
        "unknown": row.get("unknown", []),
        "waiting": row.get("waiting", []),
        "actions": row.get("actions", []),
        "findings": row.get("findings", []),
        "root_blocker": row.get("root_blocker", False),
    }


def approval_view(row):
    return {
        "approval_id": row.get("approval_id"),
        "path": row["path"],
        "subject": row.get("subject"),
        "owner_party_id": row.get("owner_party_id"),
        "deliverable_id": row.get("deliverable_id"),
        "status": row.get("status"),
        "due_at": row.get("due_at"),
        "evidence_ref": row.get("evidence_ref"),
        "invalid": row.get("invalid", False),
    }


def client_input_view(row):
    return {
        "input_id": row.get("input_id"),
        "path": row["path"],
        "kind": row.get("kind"),
        "description": row.get("description"),
        "owner_party_id": row.get("owner_party_id"),
        "deliverable_id": row.get("deliverable_id"),
        "status": row.get("status"),
        "due_at": row.get("due_at"),
        "evidence_ref": row.get("evidence_ref"),
        "invalid": row.get("invalid", False),
    }


def party_view(row):
    return {
        "path": row["path"],
        "party_id": row.get("party_id"),
        "role": row.get("role"),
        "decision_authority": row.get("decision_authority"),
        "contact_consent": row.get("contact_consent"),
    }


def hidden_value(path, value, hidden):
    text = clean_text(value)
    if not text:
        return None
    if path in hidden:
        return PLACEHOLDER
    return text


def aggregate_project_state(project_blockers, project_unknowns, deliverables,
                            waiting_on_client, status_counts):
    if project_blockers:
        if any(code in INCOMPLETE_CODES for code in project_blockers):
            return None
        return "BLOCKED"
    if status_counts.get("BLOCKED"):
        return "BLOCKED"
    if project_unknowns or status_counts.get("INSUFFICIENT_EVIDENCE"):
        return "INSUFFICIENT_EVIDENCE"
    if status_counts.get("WAITING_ON_CLIENT") or waiting_on_client:
        return "WAITING_ON_CLIENT"
    if status_counts.get("ACTION_NEEDED"):
        return "ACTION_NEEDED"
    return "READY"


def build_checklist(project_blockers, project_unknowns, deliverables, cycles,
                    refused_refs, unknown_consent):
    items = list(HUMAN_CHECKLIST_BASE)
    if project_blockers:
        items.append("先修正项目级硬性问题：" +
                     "、".join(sorted(set(project_blockers))) + "（人工修正后再重跑）")
    if cycles:
        items.append("打破依赖循环后再排期：" +
                     "；".join("、".join(cycle) for cycle in cycles))
    for row in deliverables:
        if row.get("root_blocker"):
            items.append("先解决根阻塞交付物 %s（%s）" % (
                esc(row.get("deliverable_id")), "、".join(row["findings"])))
    if refused_refs:
        items.append("被拒绝的引用（含路径或链接）请改为单一文件名后重新提供")
    if unknown_consent:
        items.append("逐一确认联系同意：" + "、".join(esc(p) for p in unknown_consent))
    return items


def render_markdown(payload, project, deliverables, status_counts,
                    project_blockers, project_unknowns, waiting_on_client,
                    timeline_conflicts, responsibility_gaps,
                    clarification_questions, communication, change_process,
                    hidden, drafts):
    name = hidden_value("project/name", project.get("name"), hidden) or "（未提供项目名）"
    lines = ["# 自由职业项目启动对齐卡", ""]
    lines.append("- 项目：%s" % esc(name))
    lines.append("- 交付物状态汇总：" + "、".join(
        "%s=%d" % (state, status_counts.get(state, 0))
        for state in DELIVERABLE_STATES))
    lines.append("")

    lines.append("## 交付物状态")
    if not deliverables:
        lines.append("- （未提供交付物）")
    for row in deliverables:
        ident = esc(row.get("deliverable_id") or row["path"])
        lines.append("- %s：%s（%s）" % (
            ident, row["state"],
            "、".join(row["findings"]) if row["findings"] else "无待办"))
    lines.append("")

    lines.append("## 范围内 / 范围外")
    in_scope = [d for d in deliverables if not d["invalid"]]
    if not in_scope:
        lines.append("- （未提供交付物）")
    for row in in_scope:
        desc = hidden_value("deliverables[%d]/description" % row["index"],
                            row["description"], hidden) or "（未提供描述）"
        lines.append("- 范围内 %s：%s" % (
            esc(row.get("deliverable_id") or row["path"]), esc(desc)))
    out_rows = [(d, item) for d in in_scope for item in d["out_of_scope"]]
    if not out_rows:
        lines.append("- 范围外：**未提供任何范围外声明**")
    for row, item in out_rows:
        lines.append("- 范围外 %s：%s" % (
            esc(row.get("deliverable_id") or row["path"]),
            esc(hidden_value("deliverables[%d]/out_of_scope" % row["index"],
                             item, hidden))))
    lines.append("")

    lines.append("## 依赖与根阻塞")
    edges = [(dep, d.get("deliverable_id") or d["path"])
             for d in deliverables if not d["invalid"]
             for dep in d["dependencies"]]
    if not edges:
        lines.append("- （无已声明的交付物依赖）")
    for dep, target in edges:
        lines.append("- %s ← 依赖 ← %s" % (esc(target), esc(dep)))
    roots = [d for d in deliverables if d.get("root_blocker")]
    if not roots:
        lines.append("- 根阻塞项：无")
    for row in roots:
        lines.append("- 根阻塞项：%s（%s）" % (
            esc(row.get("deliverable_id") or row["path"]),
            "、".join(row["findings"])))
    lines.append("")

    lines.append("## 等待客户")
    if not waiting_on_client:
        lines.append("- （无等待客户事项）")
    for item in waiting_on_client:
        lines.append("- [%s] %s 负责人=%s 截止=%s%s" % (
            item["kind"], esc(item.get("id")), esc(item.get("owner_party_id")
                                                or "未指定"),
            esc(item.get("due_at") or "未提供"),
            "（已逾期）" if item.get("overdue") else ""))
    lines.append("")

    lines.append("## 时间线冲突")
    if not timeline_conflicts:
        lines.append("- （无）")
    for item in timeline_conflicts:
        lines.append("- %s：%s" % (item["code"], esc(item.get("detail"))))
    lines.append("")

    lines.append("## 缺失事实与责任缺口")
    if not project_unknowns and not responsibility_gaps and \
            not any(d["unknown"] for d in deliverables):
        lines.append("- （无）")
    for code in project_unknowns:
        lines.append("- 项目级：%s" % code)
    for row in deliverables:
        for code in row["unknown"]:
            lines.append("- %s：%s" % (
                esc(row.get("deliverable_id") or row["path"]), code))
    for gap in responsibility_gaps:
        lines.append("- 责任缺口：%s（%s）" % (gap["path"], gap["code"]))
    lines.append("")

    lines.append("## 沟通与升级路径")
    for key, label in (("channel", "渠道"), ("cadence", "节奏"),
                       ("response_target", "响应目标"),
                       ("urgent_escalation", "升级路径")):
        value = hidden_value("communication/" + key, communication.get(key), hidden)
        lines.append("- %s：%s" % (label, esc(value) if value else "**未提供**"))
    lines.append("")

    lines.append("## 变更流程")
    if not change_process:
        lines.append("- **未提供变更流程**")
    else:
        for step, label in (("propose", "提出"), ("estimate", "估算"),
                            ("approve", "批准"), ("schedule", "排期")):
            value = hidden_value("change_process/" + step,
                                 change_process.get(step), hidden)
            lines.append("- %s：%s" % (label, esc(value) if value else "**未提供**"))
    lines.append("")

    lines.append("## 澄清问题")
    if not clarification_questions:
        lines.append("- （无）")
    for item in clarification_questions:
        lines.append("- [%s] %s（%s）" % (
            item["code"], esc(item["question"]), esc(item["path"])))
    lines.append("")

    lines.append("## 草稿（未发送）")
    if not drafts:
        lines.append("- 无草稿（未获得联系同意或没有待确认事项）")
    for draft in drafts:
        lines.append("- %s → %s [%s]" % (
            esc(draft["draft_id"]), esc(draft["to_party_id"]), draft["status"]))
    lines.append("")

    lines.append("## 备注")
    note = hidden_value("notes", payload.get("notes"), hidden)
    lines.append("- 备注：%s" % (esc(note) if note else "**未提供**"))
    lines.append("")

    lines.append("## 免责声明")
    lines.append("- " + esc(DISCLAIMER))
    lines.append("- " + esc(NO_AUTOMATION_DECLARATION))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def refusal(payload, paths):
    return {
        "skill": "suge-freelance-project-kickoff-alignment-pack",
        "version": VERSION,
        "status": "REJECTED",
        "code": "CREDENTIAL_LIKE_INPUT",
        "project_status": None,
        "detected_fields": sorted(set(paths)),
        "message": "输入疑似包含凭据（字段名或值形如密钥/令牌），已整体拒绝处理，未回显任何内容。",
        "read_only": True,
        "network": False,
        "writes_files": False,
    }


def incomplete(code, message):
    return {
        "skill": "suge-freelance-project-kickoff-alignment-pack",
        "version": VERSION,
        "status": "INPUT_INCOMPLETE",
        "code": code,
        "project_status": None,
        "message": message,
        "read_only": True,
        "network": False,
        "writes_files": False,
    }


def run(payload):
    """Guarded entry point: refuse, stop, or analyse. Never echoes secrets."""
    if not isinstance(payload, dict):
        return incomplete("NON_OBJECT_INPUT", "顶层输入必须是 JSON 对象。")
    credential_paths = find_credentials(payload)
    if credential_paths:
        return refusal(payload, credential_paths)
    as_of = payload.get("as_of")
    if not has_text(as_of):
        return incomplete("MISSING_AS_OF", "缺少带时区偏移的 as_of，无法判断当前时点。")
    if parse_dt(as_of) is None:
        return incomplete("INVALID_AS_OF",
                          "as_of 必须带时区偏移（如 2026-10-02T19:00:00+08:00）。")
    return analyse(payload)


def main(argv):
    if len(argv) != 2:
        print(json.dumps({"status": "USAGE_ERROR",
                          "usage": "python3 scripts/run.py <input.json>"},
                         ensure_ascii=False, indent=2))
        return 2
    try:
        with open(argv[1], "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "INPUT_ERROR",
                          "message": clean_text(str(exc))},
                         ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(run(payload), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
