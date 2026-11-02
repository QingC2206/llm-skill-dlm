#!/usr/bin/env python3
"""Epic granularity signals for ledgers and not-yet-written drafts.

Signals advise; only structural rules (E1-E3) are errors. Sizing remains a
judgement that the user confirms when Epic candidates are presented.
"""

from __future__ import annotations

import re
from typing import Any

from dlm_invariants import Report, blank
from dlm_model import RELATION_KINDS, Ledger

GROUP_TITLE_RE = re.compile(
    r"^\s*(阶段|第[一二三四五六七八九十\d]+[组阶段期批]|phase\b|stage\b|group\b|milestone\b|wave\b)",
    re.IGNORECASE)
GROUP_KEYS = ("group", "phase", "stage", "milestone", "wave")


def _components(nodes: list[str], edges: list[tuple[str, str]]) -> int:
    parent = {n: n for n in nodes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    return len({find(n) for n in nodes})


def _depth(nodes: list[str], deps: dict[str, set[str]]) -> int:
    memo: dict[str, int] = {}

    def walk(node: str, stack: frozenset[str]) -> int:
        if node in memo:
            return memo[node]
        if node in stack:
            return 0
        best = 1 + max((walk(d, stack | {node}) for d in deps.get(node, set()) if d in deps), default=0)
        memo[node] = best
        return best

    return max((walk(n, frozenset()) for n in nodes), default=0)


def signals(name: str, tasks: list[dict[str, Any]], policy: dict[str, Any], report: Report) -> None:
    limits = policy["epic"]
    ids = [t["id"] for t in tasks]
    count = len(ids)
    if count > limits.get("max_subtasks", 10):
        report.warn("SIGNAL", f"{name}: {count} 个子任务 > {limits.get('max_subtasks', 10)}，可能是路线图而不是中等规模成果")
    if count == 1:
        report.warn("SIGNAL", f"{name}: 只有 1 个子任务，考虑作为局部任务或已有 Epic 的子任务")
    edges: list[tuple[str, str]] = []
    by_obligation: dict[str, list[str]] = {}
    for t in tasks:
        for o in t.get("obligations", set()):
            by_obligation.setdefault(o, []).append(t["id"])
        for d in t.get("deps", set()):
            edges.append((t["id"], d))
    for owners in by_obligation.values():
        edges.extend((owners[0], other) for other in owners[1:])
    if count > 1:
        # Tasks without any recorded obligation or dependency (e.g. freshly adopted
        # history) carry no relationship facts; they cannot prove disconnection.
        known = [t for t in tasks if t.get("obligations") or t.get("deps")]
        components = _components([t["id"] for t in known], edges) if len(known) > 1 else 1
        if components > 1:
            report.warn("SIGNAL", f"{name}: 任务图可拆成 {components} 组互不相连的工作，可能装了多个独立成果")
    depth = _depth(ids, {t["id"]: set(t.get("deps", set())) for t in tasks})
    if depth > limits.get("max_chain_depth", 4):
        report.warn("SIGNAL", f"{name}: 依赖链深度 {depth} > {limits.get('max_chain_depth', 4)}，呈阶段流水线")
    grouped = [t["id"] for t in tasks if t.get("grouped")]
    if grouped:
        report.warn("SIGNAL", f"{name}: 任务带阶段/分组标记 {grouped[:5]}，Epic 不应按阶段或技术层组织")


DATE_RE = re.compile(r"\b20\d\d-\d\d-\d\d\b")


def narrative_signals(ledger: Ledger, report: Report) -> None:
    """Prose must not become a second ledger: pasted paragraphs and accumulated logs."""
    limits = ledger.host.policy["epic"]
    files = [ledger.dir / "epic.md"] + [ledger.dir / t["file"] for t in ledger.collection("S") if t.get("file")]
    seen: dict[str, set[str]] = {}
    for path in files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        rel = path.relative_to(ledger.dir).as_posix()
        lines = text.splitlines()
        if path.name != "epic.md" and len(lines) > limits.get("max_task_body_lines", 200):
            report.warn("SIGNAL", f"{ledger.epic_id}/{rel}: 正文 {len(lines)} 行；只保留会改变行动的设计，"
                                  "进度与证据进账本，历史由 journal 记录")
        dated = sum(1 for line in lines if DATE_RE.search(line))
        if dated > limits.get("max_dated_lines", 12):
            report.warn("SIGNAL", f"{ledger.epic_id}/{rel}: {dated} 行带日期，正文在积累日志；"
                                  "决议用 dlm decide，进展用 checkpoint/evidence")
        for block in re.split(r"\n\s*\n", text):
            normalized = re.sub(r"\s+", " ", block).strip()
            if len(normalized) >= 80 and not normalized.startswith(("#", "|", "<!--")):
                seen.setdefault(normalized, set()).add(rel)
    repeated = [(text, where) for text, where in seen.items() if len(where) >= 3]
    for text, where in repeated[:5]:
        report.warn("SIGNAL", f"{ledger.epic_id}: 同一段落出现在 {len(where)} 个文件（{text[:40]}…）；"
                              "共享决定写成决议 D，由简报渲染，不要复制")


def evidence_signals(ledger: Ledger, report: Report) -> None:
    counts: dict[tuple[str, str], int] = {}
    for v in ledger.collection("V"):
        if v.get("status", "valid") != "valid" or v.get("scope") != "full":
            continue
        for o in v.get("obligations", []) or []:
            key = (v.get("task") or "", o)
            counts[key] = counts.get(key, 0) + 1
    for (task, obligation), number in counts.items():
        if number >= 3:
            report.warn("SIGNAL", f"{ledger.epic_id}/{obligation}: 已登记 {number} 次全量验证"
                                  + (f"（{task}）" if task else "")
                                  + "；每次补验应回答新问题（--answers），微调只验所改部分与直接回归")


def ledger_signals(ledger: Ledger, report: Report) -> None:
    narrative_signals(ledger, report)
    evidence_signals(ledger, report)
    producers = {a["id"]: a.get("producer") for a in ledger.collection("A")}
    tasks = []
    for t in ledger.collection("S"):
        obligations = {o["id"] for o in ledger.collection("O")
                       if o.get("owner") == t["id"] or t["id"] in (o.get("preservers") or [])}
        deps = {producers.get(n.get("artifact", "")) for n in t.get("needs", []) or []}
        tasks.append({"id": t["id"], "obligations": obligations, "deps": {d for d in deps if d},
                      "grouped": bool(GROUP_TITLE_RE.match(t.get("title", "")))})
    signals(ledger.epic_id, tasks, ledger.host.policy, report)
    external = ledger_external_epics(ledger)
    limit = ledger.host.policy["epic"].get("max_external_epics", 2)
    if len(external) > limit:
        report.warn("SIGNAL", f"{ledger.epic_id}: 依赖 {len(external)} 个其他 Epic 的产物 > {limit}（{sorted(external)}）；"
                              "它的成果可能无法独立验收")


def _web_signal(names: list[str], external: dict[str, set[str]], policy: dict[str, Any], report: Report,
                per_epic: bool = True) -> None:
    """Epics that mostly depend on each other were sliced by layer or stage, not by outcome."""
    limit = policy["epic"].get("max_external_epics", 2)
    for name in (names if per_epic else []):
        if len(external.get(name, set())) > limit:
            report.warn("SIGNAL", f"{name}: 依赖 {len(external[name])} 个其他 Epic 的产物 > {limit}；"
                                  "它的成果可能无法独立验收（常见于按技术层/阶段切分）")
    if len(names) >= 3:
        coupled = [n for n in names if len(external.get(n, set())) >= 2]
        if len(coupled) * 2 > len(names):
            report.warn("SIGNAL", f"{len(coupled)}/{len(names)} 个 Epic 各依赖 ≥2 个其他 Epic，Epic 之间已连成依赖网："
                                  "通常是按算法阶段、技术层或交付物类型切分。改按用户可验收的成果纵向切分，"
                                  "基础能力随首个消费者交付，整体验收用 verifies")


def draft_report(draft: dict[str, Any], policy: dict[str, Any], triggers: list[str]) -> Report:
    report = Report()
    epics = draft.get("epics")
    if not isinstance(epics, list) or not epics:
        report.error("DRAFT", "草案需要 epics 数组")
        return report
    lowered = [t.lower() for t in triggers if t]
    names = [str(e.get("id") or e.get("title") or f"#{i}") for i, e in enumerate(epics, 1)]
    producer_epic: dict[str, str] = {}
    for name, epic in zip(names, epics):
        for t in epic.get("tasks", []) or []:
            for artifact in t.get("produces", []) or []:
                producer_epic.setdefault(artifact, name)
            if t.get("id"):
                producer_epic.setdefault(f"{name}/{t['id']}", name)
    external: dict[str, set[str]] = {}
    for name, epic in zip(names, epics):
        if blank(epic.get("outcome")):
            report.error("E1", f"{name}: 缺少可观察成果 outcome")
        if not epic.get("acceptance"):
            report.error("E1", f"{name}: 缺少 Epic 级验收 acceptance")
        for key in ("depends_on", "after", "blocked_by", "prerequisites"):
            if epic.get(key):
                report.error("E2", f"{name}: Epic 之间没有“等待完成”依赖（{key}）；改为 verifies 或具名产物 artifact")
        deps_out = external.setdefault(name, set())
        for relation in epic.get("relations", []) or []:
            if relation.get("kind") not in RELATION_KINDS:
                report.error("E2", f"{name}: 关系类型 {relation.get('kind')!r} 不允许，只能 verifies / artifact")
            elif relation.get("kind") == "artifact":
                target = relation.get("artifact") or relation.get("target")
                if blank(target):
                    report.error("E2", f"{name}: artifact 关系须指明具体产物")
                elif producer_epic.get(target, target) != name:
                    deps_out.add(producer_epic.get(target, str(target).split("/")[0]))
        produced: dict[str, str] = {}
        for t in epic.get("tasks", []) or []:
            for artifact in t.get("produces", []) or []:
                produced[artifact] = t.get("id", "")
        tasks = []
        for t in epic.get("tasks", []) or []:
            tid = t.get("id") or t.get("title")
            obligations = set(t.get("obligations", []) or [])
            if not obligations and not (t.get("kind") == "investigation" and t.get("question") and t.get("stop")):
                report.error("E3", f"{name}/{tid}: 子任务需要具体推进结果（obligations），或为带 question/stop 的 investigation")
            deps = set()
            for need in t.get("needs", []) or []:
                if need in produced:
                    deps.add(produced[need])
                elif need in producer_epic and producer_epic[need] != name:
                    deps_out.add(producer_epic[need])
                else:
                    deps.add(need)
            grouped = any(t.get(k) for k in GROUP_KEYS) or bool(GROUP_TITLE_RE.match(str(t.get("title", ""))))
            tasks.append({"id": tid, "obligations": obligations, "deps": deps, "grouped": grouped})
            text = " ".join(map(str, obligations)).lower()
            hit = next((w for w in lowered if w in text), None)
            if hit and not t.get("population"):
                report.warn("D7", f"{name}/{tid}: 含覆盖型用词“{hit}”，需先盘点全集（population）")
        if any(epic.get(k) for k in GROUP_KEYS) or GROUP_TITLE_RE.match(str(epic.get("title", ""))):
            report.warn("SIGNAL", f"{name}: Epic 本身是阶段/分组，应按可独立验收的成果划分")
        signals(name, tasks, policy, report)
    _web_signal(names, external, policy, report)
    return report


def ledger_external_epics(ledger: Ledger) -> set[str]:
    """Other Epics this Epic consumes, from task needs and artifact relations."""
    found = set()
    for t in ledger.collection("S"):
        for need in t.get("needs", []) or []:
            match = re.match(r"^(E\d{2,})/", need.get("artifact", ""))
            if match and match.group(1) != ledger.epic_id:
                found.add(match.group(1))
    for relation in ledger.epic.get("relations", []) or []:
        if relation.get("kind") == "artifact":
            match = re.match(r"^(E\d{2,})/", relation.get("target", ""))
            if match and match.group(1) != ledger.epic_id:
                found.add(match.group(1))
    return found


def host_web_report(host: Any, ledgers: list[Ledger], report: Report) -> None:
    names = [l.epic_id for l in ledgers if l.epic.get("status") != "done"]
    external = {l.epic_id: ledger_external_epics(l) for l in ledgers}
    _web_signal(names, external, host.policy, report, per_epic=False)
