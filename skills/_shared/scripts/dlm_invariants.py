#!/usr/bin/env python3
"""Deterministic invariants shared by every command (create, check, close, brief).

Each finding is (level, code, message). ``unknown`` means the tool could not
establish a fact; gates treat it as failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dlm_paths as paths
from dlm_model import (ARTIFACT_STATES, COVERAGE, DECISION_KINDS, DECISION_SOURCES, DEPTHS,
                       EPIC_STATUS, EVIDENCE_SCOPES, EVIDENCE_STATUS, HYPOTHESIS_STATUS, ITEM_STATUS,
                       LIMIT_KINDS, OBLIGATION_STATUS, ON_EXCEED, RELATION_KINDS, TASK_KINDS, TASK_STATUS,
                       Ledger, resolve_external)
from dlm_status import TERMINAL_OBLIGATION, obligation_status, owned

HISTORICAL_MARKERS = ("dlm: historical", "<!-- dlm:historical -->")
PLACEHOLDER_RE = re.compile(r"^\s*(\{[^{}]*\}|TODO|TBD|待填|待补|\.\.\.|…)?\s*$", re.IGNORECASE)


@dataclass
class Finding:
    level: str  # error | warn | unknown
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.code}: {self.message}"


class Report:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def add(self, level: str, code: str, message: str) -> None:
        self.findings.append(Finding(level, code, message))

    def error(self, code: str, message: str) -> None:
        self.add("error", code, message)

    def warn(self, code: str, message: str) -> None:
        self.add("warn", code, message)

    def unknown(self, code: str, message: str) -> None:
        self.add("unknown", code, message)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.level in {"error", "unknown"}]

    def extend(self, other: "Report") -> None:
        self.findings.extend(other.findings)


def blank(value: Any) -> bool:
    return not isinstance(value, str) or bool(PLACEHOLDER_RE.match(value))


EXTERNAL_LOCATOR = ("note:", "http://", "https://")


def locator_state(root: Path, locator: str) -> str:
    """external (declared non-file evidence), ok (file exists) or missing."""
    value = (locator or "").strip()
    if value.lower().startswith(EXTERNAL_LOCATOR):
        return "external"
    if re.match(r"^V\d+$", value):
        return "ok"
    path = Path(value)
    candidate = path if path.is_absolute() else root / value
    return "ok" if candidate.exists() else "missing"


def is_historical(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8-sig", errors="replace")[:600]
    except OSError:
        return False
    return any(marker in head for marker in HISTORICAL_MARKERS)


def markdown_headings(path: Path) -> list[str]:
    headings = []
    fence = False
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fence = not fence
            continue
        if not fence:
            match = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
            if match:
                headings.append(match.group(1).strip())
    return headings


def user_decision(ledger: Ledger, ref: str | None) -> dict[str, Any] | None:
    if not ref:
        return None
    decision = ledger.find(ref, "D")
    if decision and decision.get("source") == "user" and not blank(decision.get("quote")):
        return decision
    return None


def _enum(report: Report, where: str, value: Any, allowed: tuple[str, ...], code: str) -> None:
    if value not in allowed:
        report.error(code, f"{where}: 取值 {value!r} 不在 {'/'.join(allowed)} 中")


def check_ledger(ledger: Ledger) -> Report:
    report = Report()
    host = ledger.host
    epic = ledger.epic
    eid = ledger.epic_id

    # ---- epic (E1/E2)
    if blank(epic.get("title")):
        report.error("E1", f"{eid}: 缺少标题")
    if blank(epic.get("outcome")):
        report.error("E1", f"{eid}: 缺少可观察成果 outcome")
    _enum(report, f"{eid}.status", epic.get("status"), EPIC_STATUS, "SCHEMA")
    if not [o for o in ledger.collection("O") if o.get("owner") == "epic"]:
        report.error("E1", f"{eid}: 至少需要一条 owner=epic 的 Epic 级验收义务")
    for relation in epic.get("relations", []) or []:
        kind, target = relation.get("kind"), relation.get("target", "")
        if kind not in RELATION_KINDS:
            report.error("E2", f"{eid}: Epic 关系只允许 verifies / artifact，发现 {kind!r}")
            continue
        if kind == "verifies":
            if not re.match(r"^E\d{2,}$", target) or not _epic_exists(ledger, target):
                report.error("E2", f"{eid}: verifies 目标 {target!r} 不是存在的 Epic")
        else:
            resolved = resolve_external(host, target, ledger)
            if not re.match(r"^E\d{2,}/A\d+$", target) or resolved is None:
                report.error("E2", f"{eid}: artifact 关系须指向存在的具名产物 Exxx/Ay，得到 {target!r}")
    # The external-dependency signal (relations + task needs) lives in dlm_signals.

    # ---- uniqueness
    for prefix in ("P", "D", "O", "N", "A", "H", "L", "V", "J", "S"):
        ids = [e.get("id") for e in ledger.collection(prefix)]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            report.error("I1", f"{eid}: 编号重复 {sorted(dupes)}")

    tasks = {t["id"]: t for t in ledger.collection("S")}

    # ---- decisions
    for d in ledger.collection("D"):
        where = f"{eid}/{d['id']}"
        _enum(report, where + ".source", d.get("source"), DECISION_SOURCES, "SCHEMA")
        _enum(report, where + ".kind", d.get("kind"), DECISION_KINDS, "SCHEMA")
        if d.get("source") == "user" and blank(d.get("quote")):
            report.error("I8", f"{where}: 用户决议必须记录原话 quote")
        for ref in d.get("supersedes", []) or []:
            if ledger.find(ref, "D") is None:
                report.error("I1", f"{where}: supersedes {ref} 不存在")
        for ref in d.get("affects", []) or []:
            if resolve_external(host, ref, ledger) is None:
                report.error("I1", f"{where}: affects {ref} 无法解析")
        for ref in d.get("basis", []) or []:
            hypo = ledger.find(ref, "H") if ref.startswith("H") else None
            if ref.startswith("H") and hypo is None:
                report.error("I1", f"{where}: basis {ref} 不存在")
            elif hypo is not None and hypo.get("status") != "confirmed" and d.get("kind") in {"scope", "gate", "limit"}:
                report.error("I6", f"{where}: {d['kind']} 决议以未确认假设 {ref} 为依据")

    # ---- principles
    for p in ledger.collection("P"):
        if blank(p.get("text")):
            report.error("SCHEMA", f"{eid}/{p['id']}: 原则正文为空")
        if user_decision(ledger, p.get("decision")) is None:
            report.error("I8", f"{eid}/{p['id']}: 原则必须引用记录原话的用户决议")

    # ---- populations
    full_bound = {o.get("population") for o in ledger.collection("O") if o.get("coverage") == "full"}
    for n in ledger.collection("N"):
        keys = [i.get("key") for i in n.get("items", [])]
        if len(keys) != len(set(keys)):
            report.error("INV", f"{eid}/{n['id']}: 条目 key 重复")
        for item in n.get("items", []):
            _enum(report, f"{eid}/{item.get('id')}.status", item.get("status"), ITEM_STATUS, "SCHEMA")
            if item.get("status") == "na" and blank(item.get("reason")):
                report.error("INV", f"{eid}/{item['id']}: na 必须写原因")
            if (item.get("status") == "na" and n["id"] in full_bound and item.get("side") != "target"
                    and user_decision(ledger, item.get("decision")) is None):
                report.error("I8", f"{eid}/{item['id']}: 全量义务的全集中，把参照条目判为 na 需要用户决议（inv mark --decision D#）")
            if item.get("status") == "pass" and not item.get("evidence"):
                report.error("I4", f"{eid}/{item['id']}: pass 必须有列出该条目的证据")
        if not n.get("sources") and not n.get("items"):
            report.warn("INV", f"{eid}/{n['id']}: 全集尚无来源与条目")
        if n.get("sample") and user_decision(ledger, n.get("sample", {}).get("decision")) is None:
            report.error("I8", f"{eid}/{n['id']}: 抽检必须引用用户决议")

    # ---- obligations
    triggers = [t.lower() for t in host.triggers]
    for o in ledger.collection("O"):
        where = f"{eid}/{o['id']}"
        if blank(o.get("text")):
            report.error("SCHEMA", f"{where}: 义务正文为空")
        _enum(report, where + ".coverage", o.get("coverage", "single"), COVERAGE, "SCHEMA")
        _enum(report, where + ".status", o.get("status", "open"), OBLIGATION_STATUS, "SCHEMA")
        owner = o.get("owner")
        if owner != "epic" and owner not in tasks:
            report.error("I2", f"{where}: owner {owner!r} 不是本 Epic 任务或 epic")
        if o.get("origin") and ledger.find(o["origin"], "D") is None:
            report.error("I6" if str(o["origin"]).startswith("H") else "I1",
                         f"{where}: origin 必须是本 Epic 的决议，得到 {o['origin']!r}")
        derives = o.get("derives")
        if derives and (derives == o["id"] or ledger.find(derives, "O") is None):
            report.error("I1", f"{where}: derives {derives!r} 不是本 Epic 的其他义务")
        if not o.get("origin") and not derives:
            report.error("I12", f"{where}: 义务需要来源——用户决议（origin D#）或派生自既有义务（derives O#）；"
                                "执行者不能凭空增加收口条件")
        elif not derives:
            origin = ledger.find(o.get("origin") or "", "D")
            if origin is not None and origin.get("source") != "user":
                report.warn("I12", f"{where}: 义务来源是执行者决议；若它扩大了验收范围，需要用户确认")
        for ref in o.get("preservers", []) or []:
            if ref not in tasks:
                report.error("I1", f"{where}: preserver {ref} 不存在")
        if o.get("coverage") == "full":
            if ledger.find(o.get("population") or "", "N") is None:
                report.error("I4", f"{where}: coverage=full 必须关联全集 N")
        elif o.get("coverage") == "sample":
            if user_decision(ledger, o.get("sample_decision")) is None:
                report.error("I8", f"{where}: coverage=sample 必须引用用户决议")
        else:
            text = (o.get("text") or "").lower()
            hit = next((t for t in triggers if t and t in text), None)
            if hit:
                report.warn("D7", f"{where}: 义务含覆盖型用词“{hit}”但未关联全集，先盘点（dlm inv）")
        status = o.get("status", "open")
        if status == "transferred":
            transfer = o.get("transfer") or {}
            target = transfer.get("to")
            if target not in tasks:
                report.error("I3", f"{where}: transfer 目标 {target!r} 不存在")
            elif tasks[target].get("status") == "done":
                report.error("I3", f"{where}: transfer 目标 {target} 已完成，义务无人承接")
            if blank(transfer.get("entry")):
                report.error("I3", f"{where}: transfer 必须写进入条件")
        if status in {"waived", "superseded"}:
            if user_decision(ledger, (o.get("resolution") or {}).get("decision")) is None:
                report.error("I8", f"{where}: {status} 必须引用用户决议")

    # ---- artifacts
    for a in ledger.collection("A"):
        where = f"{eid}/{a['id']}"
        _enum(report, where + ".state", a.get("state"), ARTIFACT_STATES, "SCHEMA")
        if a.get("producer") and a["producer"] not in tasks:
            report.error("I1", f"{where}: producer {a['producer']} 不存在")

    # ---- hypotheses
    for h in ledger.collection("H"):
        where = f"{eid}/{h['id']}"
        _enum(report, where + ".status", h.get("status"), HYPOTHESIS_STATUS, "SCHEMA")
        if h.get("status") in {"confirmed", "refuted"} and not h.get("evidence"):
            report.error("I6", f"{where}: {h['status']} 必须附证据")

    # ---- limits
    for lim in ledger.collection("L"):
        where = f"{eid}/{lim['id']}"
        _enum(report, where + ".kind", lim.get("kind"), LIMIT_KINDS, "SCHEMA")
        _enum(report, where + ".on_exceed", lim.get("on_exceed"), ON_EXCEED, "SCHEMA")
        if lim.get("kind") == "product" and user_decision(ledger, lim.get("basis")) is None:
            report.error("I10", f"{where}: 产品限制必须以用户决议为依据")
        if lim.get("kind") == "budget" and lim.get("on_exceed") == "reject":
            report.error("I10", f"{where}: 实现预算不能拒绝业务输入；改为 chunk / degrade-with-record / unmet")
        if lim.get("kind") == "resource-fact" and blank(lim.get("basis")):
            report.error("I10", f"{where}: 资源事实必须给出依据")

    # ---- evidence
    gate_names = {g.get("name") for g in host.policy.get("gates", [])} | {
        g.get("name") for g in epic.get("extra_gates", []) or []}
    for v in ledger.collection("V"):
        where = f"{eid}/{v['id']}"
        _enum(report, where + ".scope", v.get("scope"), EVIDENCE_SCOPES, "SCHEMA")
        _enum(report, where + ".status", v.get("status", "valid"), EVIDENCE_STATUS, "SCHEMA")
        if blank(v.get("locator")):
            report.error("SCHEMA", f"{where}: 证据必须有 locator")
        elif v.get("status", "valid") == "valid" and locator_state(host.root, v["locator"]) == "missing":
            report.warn("I14", f"{where}: 证据 {v['locator']} 不存在；若已丢失用 reopen --cause evidence-lost，"
                               "非文件证据用 note: 前缀写明")
        for ref in v.get("obligations", []) or []:
            if ledger.find(ref, "O") is None:
                report.error("I1", f"{where}: 义务 {ref} 不存在")
        for ref in v.get("items", []) or []:
            if ledger.find(ref) is None:
                report.error("I1", f"{where}: 条目 {ref} 不存在")
        if v.get("gate") and v["gate"] not in gate_names:
            report.warn("I12", f"{where}: 门禁 {v['gate']!r} 未在 policy 或 Epic 门禁中声明")
        if v.get("task") and v["task"] not in tasks:
            report.error("I1", f"{where}: task {v['task']} 不存在")
    for gate in epic.get("extra_gates", []) or []:
        if user_decision(ledger, gate.get("decision")) is None:
            report.error("I8", f"{eid}: 额外门禁 {gate.get('name')!r} 必须引用用户决议")

    # ---- justifications
    fields = host.policy.get("public_layer_justification", [])
    for j in ledger.collection("J"):
        where = f"{eid}/{j['id']}"
        if j.get("task") not in tasks:
            report.error("I1", f"{where}: task 不存在")
        for name in fields:
            if blank((j.get("fields") or {}).get(name)):
                report.error("I9", f"{where}: 职责说明缺少 {name}")
        if user_decision(ledger, j.get("decision")) is None:
            report.error("I9", f"{where}: 触及公共层必须引用用户决议")

    # ---- boundary
    boundary = ledger.data.get("boundary", {})
    for kind in paths.BOUNDARY_KINDS:
        for pattern in boundary.get(kind, []) or []:
            try:
                paths.validate_pattern(pattern)
            except ValueError as exc:
                report.error("I9", f"{eid}: {exc}")
    if not (boundary.get("write") or boundary.get("generated")):
        report.warn("I9", f"{eid}: Epic 尚无写集；实施前须按用户授权写明")

    # ---- tasks
    for t in tasks.values():
        _check_task(ledger, t, report)

    # ---- completed states (I3/I5) cannot be produced by hand edits
    if epic.get("status") == "done":
        for o in ledger.collection("O"):
            if obligation_status(ledger, o) not in TERMINAL_OBLIGATION:
                report.error("I5", f"{eid}: Epic 已标完成但 {o['id']} 仍为 {obligation_status(ledger, o)}")
    return report


def _epic_exists(ledger: Ledger, epic_id: str) -> bool:
    from dlm_model import epic_dirs
    return any(re.match(rf"^{epic_id}(?:_|$)", p.name) for p in epic_dirs(ledger.host))


def _check_task(ledger: Ledger, t: dict[str, Any], report: Report) -> None:
    eid = ledger.epic_id
    where = f"{eid}/{t['id']}"
    host = ledger.host
    _enum(report, where + ".status", t.get("status"), TASK_STATUS, "SCHEMA")
    _enum(report, where + ".kind", t.get("kind", "deliver"), TASK_KINDS, "SCHEMA")
    if not t.get("adopted"):
        _enum(report, where + ".depth", t.get("depth"), DEPTHS, "SCHEMA")
        if blank(t.get("depth_reason")):
            report.error("SCHEMA", f"{where}: 任务深度需要评估依据 depth_reason")
    mine = owned(ledger, t["id"])
    if t.get("kind", "deliver") == "investigation":
        if blank(t.get("question")) or blank(t.get("stop")):
            report.error("E3", f"{where}: investigation 必须写问题 question 与停止线 stop")
    elif not mine and not t.get("adopted"):
        report.error("E3", f"{where}: 任务未承担任何义务")
    if t.get("file"):
        if not (ledger.dir / t["file"]).is_file():
            report.error("I1", f"{where}: 任务正文 {t['file']} 不存在")
    for need in t.get("needs", []) or []:
        ref = need.get("artifact", "")
        if re.match(r"^(E\d+/)?H\d+$", ref):
            report.error("I6", f"{where}: 假设 {ref} 不能作为前置；先验证或改为具名产物")
            continue
        resolved = resolve_external(host, ref, ledger)
        if resolved is None or not resolved[1].get("id", "").startswith("A"):
            report.error("I1", f"{where}: 前置 {ref!r} 必须是存在的产物 A（可跨 Epic：Exxx/Ay）")
        elif need.get("state") not in ARTIFACT_STATES:
            report.error("SCHEMA", f"{where}: 前置 {ref} 的要求状态非法")
    # boundary subset (I9)
    epic_boundary = ledger.data.get("boundary", {})
    task_boundary = t.get("boundary") or {}
    for kind in ("write", "generated"):
        for pattern in task_boundary.get(kind, []) or []:
            try:
                pattern = paths.validate_pattern(pattern)
            except ValueError as exc:
                report.error("I9", f"{where}: {exc}")
                continue
            parents = epic_boundary.get(kind, []) or []
            if not any(paths.within(pattern, parent) for parent in parents):
                report.error("I9", f"{where}: {kind} {pattern} 超出 Epic {kind} 写集")
                continue
            effective, rule = paths.classify(paths.sample_path(pattern), epic_boundary)
            if effective != kind:
                report.error("I9", f"{where}: {kind} {pattern} 被 Epic 的 {effective} 规则 {rule} 覆盖")
    owner_limit = host.policy["epic"].get("max_task_owner_roots", 3)
    roots = paths.top_roots(task_boundary.get("write", []) or [])
    if len(roots) > owner_limit:
        report.warn("SIGNAL", f"{where}: 写集跨越 {len(roots)} 个模块根，检查是否混装多个结果")
    origins = {o.get("origin") for o in mine if o.get("origin")}
    if len(origins) > 2:
        report.warn("SIGNAL", f"{where}: 承担的义务来自 {len(origins)} 个不同决议，检查是否混装")
    obligation_limit = host.policy["epic"].get("max_task_obligations", 5)
    if len(mine) > obligation_limit:
        report.warn("SIGNAL", f"{where}: 承担 {len(mine)} 项义务 > {obligation_limit}，检查是否混装了多个独立结果"
                              "（提高深度不能替代拆分）")
    for source in t.get("sources", []) or []:
        path = host.root / source.get("path", "")
        if not path.is_file():
            report.error("I1", f"{where}: 必读来源 {source.get('path')} 不存在")
            continue
        if is_historical(path):
            report.error("I13", f"{where}: 历史文件 {source.get('path')} 不能作为必读来源")
        heading = source.get("heading")
        if heading and path.suffix.lower() in {".md", ".markdown"} and heading not in markdown_headings(path):
            report.error("I1", f"{where}: {source.get('path')} 中不存在标题 {heading!r}")
    if t.get("status") == "dropped":
        if not t.get("adopted") and user_decision(ledger, (t.get("drop") or {}).get("decision")) is None:
            report.error("I8", f"{where}: 停止任务必须引用用户决议")
        for o in mine:
            if obligation_status(ledger, o) not in {"waived", "superseded", "transferred"}:
                report.error("I3", f"{where}: 已停止但承担的 {o['id']} 未被替代、豁免或转交")
    if t.get("status") == "done" and not t.get("adopted"):
        for o in mine:
            status = obligation_status(ledger, o)
            # stale = evidence later became unverifiable; the completion fact stands (not reopened).
            if status not in TERMINAL_OBLIGATION and status not in {"transferred", "stale"}:
                report.error("I3", f"{where}: 已完成但承担的 {o['id']} 为 {status}")
