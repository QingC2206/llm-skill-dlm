#!/usr/bin/env python3
"""Generated views: Epic README, backlog block, task brief and close checklist."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import dlm_paths as paths
from dlm_config import Host
from dlm_model import README, Ledger, epic_dirs
from dlm_status import (artifact_ready, obligation_status, owned, population_counts, preserved)

LOCALE_DIR = Path(__file__).resolve().parent.parent / "locales"
HEADER_RE = re.compile(r"^<!-- dlm:generated sha256=([0-9a-f]{64})")
BACKLOG_START = "<!-- dlm:backlog:start -->"
BACKLOG_END = "<!-- dlm:backlog:end -->"
BRIEF_BODY_LINES = 60
# v1 sections whose current truth now lives in the ledger; showing them would
# create a second "current state" (status, principle copies, manifests, logs).
V1_LEDGER_SECTIONS = re.compile(
    r"^(Meta|Epic 核心原则|Subtask 原则|授权边界|修改边界|上下文构建协议|本轮原则核对|完成前原则复盘|执行日志|"
    r"任务深度评估|子任务索引)\s*$")


def _strip_v1_sections(lines: list[str]) -> list[str]:
    kept: list[str] = []
    skipping = False
    for line in lines:
        match = re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            skipping = bool(V1_LEDGER_SECTIONS.match(match.group(1)))
        if not skipping:
            kept.append(line)
    return kept


def locale(host: Host) -> dict[str, str]:
    path = LOCALE_DIR / f"{host.locale}.json"
    if not path.is_file():
        path = LOCALE_DIR / "en.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _cell(value: Any) -> str:
    return str(value if value not in (None, "") else "—").replace("|", "\\|").replace("\n", " ")


def _wrap(host: Host, body: str) -> str:
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return f"<!-- dlm:generated sha256={digest} — {locale(host)['generated_note']} -->\n{body}"


def verify_generated(path: Path) -> bool | None:
    """True if untouched, False if hand-edited, None if not a generated file."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    first, _, body = text.partition("\n")
    match = HEADER_RE.match(first)
    if not match:
        return None
    return hashlib.sha256(body.encode("utf-8")).hexdigest() == match.group(1)


def _progress(ledger: Ledger) -> str:
    tasks = ledger.collection("S")
    done = sum(1 for t in tasks if t.get("status") == "done")
    return f"{done}/{len(tasks)}"


def obligation_label(ledger: Ledger, o: dict[str, Any]) -> str:
    status = obligation_status(ledger, o)
    if o.get("coverage") == "full":
        population = ledger.find(o.get("population") or "", "N")
        if population is not None:
            c = population_counts(population)
            return f"{status}（{o['population']}: pass {c['pass']}/{c['total']}，gap {c['gap']}，fail {c['fail']}，pending {c['pending']}）"
    return status


def render_readme(ledger: Ledger) -> str:
    L = locale(ledger.host)
    e = ledger.epic
    lines = [f"# {e['id']}: {e.get('title', '')}", "", f"| {L['field']} | {L['value']} |", "|---|---|",
             f"| {L['status']} | {_cell(e.get('status'))} |", f"| {L['outcome']} | {_cell(e.get('outcome'))} |",
             f"| {L['created']} | {_cell(e.get('created'))} |", f"| {L['progress']} | {_progress(ledger)} |"]
    for p in ledger.collection("P")[:1]:
        d = ledger.find(p.get("decision", ""), "D") or {}
        lines.append(f"| {L['confirmation']} | {_cell(d.get('id'))}：“{_cell(d.get('quote'))}”（{_cell(d.get('session'))}） |")
    lines += ["", f"## {L['principles']}", ""]
    lines += [f"{i}. **{p['id']}** {p.get('text', '')}" for i, p in enumerate(ledger.collection("P"), 1)] or [L["none"]]
    if e.get("relations"):
        lines += ["", f"## {L['relations']}", ""]
        lines += [f"- {r.get('kind')} → {r.get('target')}" + (f"：{r['note']}" if r.get("note") else "")
                  for r in e["relations"]]
    lines += ["", f"## {L['boundary']}", ""]
    for kind in paths.BOUNDARY_KINDS:
        values = ledger.data.get("boundary", {}).get(kind) or []
        lines.append(f"- {kind}: " + (", ".join(f"`{v}`" for v in values) if values else L["none"]))
    lines += ["", f"## {L['obligations']}", "", f"| {L['id']} | {L['obligations']} | {L['owner']} | {L['coverage']} | {L['status']} |",
              "|---|---|---|---|---|"]
    for o in ledger.collection("O"):
        lines.append(f"| {o['id']} | {_cell(o.get('text'))} | {_cell(o.get('owner'))} | {_cell(o.get('coverage', 'single'))} | {_cell(obligation_label(ledger, o))} |")
    lines += ["", f"## {L['tasks']}", "",
              f"| {L['id']} | {L['title']} | {L['priority']} | {L['depth']} | {L['status']} | {L['owns']} | {L['needs']} | {L['file']} |",
              "|---|---|---|---|---|---|---|---|"]
    for t in ledger.collection("S"):
        owns = ", ".join(o["id"] for o in owned(ledger, t["id"]))
        needs = ", ".join(f"{n.get('artifact')}≥{n.get('state')}" for n in t.get("needs", []) or [])
        link = f"[{t['id']}]({t['file']})" if t.get("file") else "—"
        lines.append(f"| {t['id']} | {_cell(t.get('title'))} | {_cell(t.get('priority'))} | {_cell(t.get('depth'))} | {_cell(t.get('status'))} | {_cell(owns)} | {_cell(needs)} | {link} |")
    if ledger.collection("N"):
        lines += ["", f"## {L['populations']}", ""]
        for n in ledger.collection("N"):
            c = population_counts(n)
            lines.append(f"- {n['id']} {n.get('name')}：total {c['total']} / pass {c['pass']} / fail {c['fail']} / gap {c['gap']} / na {c['na']} / pending {c['pending']}")
    if ledger.collection("A"):
        lines += ["", f"## {L['artifacts']}", ""]
        lines += [f"- {a['id']} [{a.get('state')}] {a.get('text', '')}（{a.get('producer') or '—'}）" + (f" `{a['locator']}`" if a.get("locator") else "")
                  for a in ledger.collection("A")]
    open_h = [h for h in ledger.collection("H") if h.get("status") == "unverified"]
    if open_h:
        lines += ["", f"## {L['hypotheses']}", ""] + [f"- {h['id']} {h.get('claim', '')}" for h in open_h]
    if ledger.collection("L"):
        lines += ["", f"## {L['limits']}", ""]
        lines += [f"- {x['id']} [{x.get('kind')}] {x.get('what', '')}；超出时 {x.get('on_exceed')}" for x in ledger.collection("L")]
    decisions = _effective_decisions(ledger)[-8:]
    if decisions:
        lines += ["", f"## {L['decisions']}", ""]
        lines += [f"- {d['id']} [{d.get('source')}/{d.get('kind')}] {d.get('summary') or d.get('quote', '')}" for d in decisions]
    lines += ["", f"## {L['design']}", "", f"{L['design_link']} [epic.md](epic.md)", ""]
    return _wrap(ledger.host, "\n".join(lines))


def _effective_decisions(ledger: Ledger) -> list[dict[str, Any]]:
    superseded = {ref for d in ledger.collection("D") for ref in d.get("supersedes", []) or []}
    return [d for d in ledger.collection("D") if d["id"] not in superseded]


def write_readme(ledger: Ledger, dry_run: bool = False) -> bool:
    content = render_readme(ledger)
    path = ledger.dir / README
    old = path.read_text(encoding="utf-8-sig") if path.exists() else None
    if old is not None and old.replace("\r\n", "\n") == content:
        return False
    if not dry_run:
        path.write_text(content, encoding="utf-8", newline="\n")
    return True


def write_backlog(host: Host, dry_run: bool = False) -> bool:
    L = locale(host)
    rows = [BACKLOG_START, "", f"| Epic | {L['outcome']} | {L['status']} | {L['progress']} |", "|---|---|---|---|"]
    for directory in epic_dirs(host):
        if not (directory / "ledger.json").is_file():
            continue
        try:
            ledger = Ledger.load(host, directory)
        except Exception:  # noqa: BLE001 - a broken ledger is reported by check
            rows.append(f"| {directory.name} | （ledger 无法读取） | — | — |")
            continue
        rel = directory.relative_to(host.tasks_dir).as_posix()
        rows.append(f"| [{ledger.epic_id}]({rel}/README.md) {_cell(ledger.epic.get('title'))} | {_cell(ledger.epic.get('outcome'))} | {_cell(ledger.epic.get('status'))} | {_progress(ledger)} |")
    rows += ["", BACKLOG_END]
    block = "\n".join(rows)
    path = host.tasks_dir / "backlog.md"
    if path.exists():
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        if BACKLOG_START in text and BACKLOG_END in text:
            new = re.sub(re.escape(BACKLOG_START) + r".*?" + re.escape(BACKLOG_END), lambda _m: block, text, flags=re.S)
        else:
            new = text.rstrip("\n") + "\n\n" + block + "\n"
    else:
        text = ""
        new = f"# {L['backlog_title']}\n\n{block}\n"
    if new == text:
        return False
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8", newline="\n")
    return True


def static_gates(host: Host, task_boundary: dict[str, list[str]]) -> list[dict[str, Any]]:
    writes = (task_boundary.get("write") or []) + (task_boundary.get("generated") or [])
    hits = []
    for gate in host.policy.get("gates", []) or []:
        for g in gate.get("paths", []):
            if any(paths.within(w, g) or paths.within(g, w) or paths.matches(paths.sample_path(w), g) for w in writes):
                hits.append(gate)
                break
    return hits


def render_brief(ledger: Ledger, task: dict[str, Any]) -> str:
    from dlm_model import resolve_external
    host = ledger.host
    L = locale(host)
    out = [f"# {L['brief']} {ledger.epic_id}/{task['id']}: {task.get('title', '')}",
           f"Epic：{ledger.epic.get('title', '')} — {L['outcome']}：{ledger.epic.get('outcome', '')}",
           f"{L['status']}：{task.get('status')} | kind：{task.get('kind', 'deliver')} | {L['depth']}：{task.get('depth') or '—'}"]
    cp = task.get("checkpoint") or {}
    if cp.get("verdict") in {"deviated", "pending"}:
        out.insert(1, f"⚠ {L['last_checkpoint']}：{cp['verdict']} — {cp.get('note', '')}（{cp.get('date', '')}）")
    if task.get("kind") == "investigation":
        out.append(f"question：{task.get('question', '')} | stop：{task.get('stop', '')}")
    out += ["", f"## {L['goal']}"]
    for o in owned(ledger, task["id"]):
        transfer = o.get("transfer") or {}
        extra = f" → {transfer.get('to')}（{transfer.get('entry')}）" if o.get("status") == "transferred" else ""
        out.append(f"- {L['owns']} {o['id']} [{obligation_label(ledger, o)}] {o.get('text', '')}{extra}")
    for o in preserved(ledger, task["id"]):
        out.append(f"- {L['preserves']} {o['id']} [{obligation_label(ledger, o)}] {o.get('text', '')}")
    out += ["", f"## {L['effective_principles']}"]
    out += [f"- {p['id']} {p.get('text', '')}" for p in ledger.collection("P")] or [f"- {L['none']}"]
    my_ids = {task["id"]} | {o["id"] for o in owned(ledger, task["id"]) + preserved(ledger, task["id"])}
    related = [d for d in _effective_decisions(ledger)
               if d.get("kind") != "principle" and (not d.get("affects") or set(d.get("affects")) & my_ids)]
    if related:
        out += ["", f"## {L['latest_decisions']}"]
        out += [f"- {d['id']} [{d.get('source')}/{d.get('kind')}] {d.get('summary') or d.get('quote', '')}" for d in related[-10:]]
    if task.get("needs"):
        out += ["", f"## {L['inputs']}"]
        for need in task["needs"]:
            resolved = resolve_external(host, need.get("artifact", ""), ledger)
            if resolved is None:
                out.append(f"- {need.get('artifact')} ≥ {need.get('state')}：未知（无法解析）")
                continue
            artifact = resolved[1]
            mark = "✓" if artifact_ready(artifact.get("state", ""), need.get("state", "")) else "✗"
            out.append(f"- {mark} {need.get('artifact')} [{artifact.get('state')} ≥ {need.get('state')}] {artifact.get('text', '')}"
                       + (f" `{artifact['locator']}`" if artifact.get("locator") else ""))
    hypotheses = [h for h in ledger.collection("H") if h.get("status") == "unverified"]
    limits = ledger.collection("L")
    if hypotheses or limits:
        out += ["", f"## {L['open_hypotheses']} / {L['limits']}"]
        out += [f"- {h['id']} （未证实）{h.get('claim', '')}" for h in hypotheses]
        out += [f"- {x['id']} [{x.get('kind')}] {x.get('what', '')}；超出时 {x.get('on_exceed')}" for x in limits]
    boundary = task.get("boundary") or {}
    out += ["", f"## {L['scope_and_gates']}"]
    for kind in ("write", "generated"):
        out.append(f"- {kind}: " + (", ".join(f"`{p}`" for p in boundary.get(kind, []) or []) or L["none"]))
    epic_boundary = ledger.data.get("boundary", {})
    for kind in ("read_only", "forbidden"):
        if epic_boundary.get(kind):
            out.append(f"- Epic {kind}: " + ", ".join(f"`{p}`" for p in epic_boundary[kind]))
    gates = static_gates(host, boundary) + list(ledger.epic.get("extra_gates") or [])
    if gates:
        out.append(f"- {L['gates']}: " + "; ".join(f"{g.get('name')}" + (f"（{g['command']}）" if g.get("command") else "") for g in gates))
    if task.get("file") and (ledger.dir / task["file"]).is_file():
        body = (ledger.dir / task["file"]).read_text(encoding="utf-8-sig").splitlines()
        body = [line for line in body[1:] if not line.startswith("<!-- dlm:task")]
        if task.get("adopted"):
            body = _strip_v1_sections(body)
        while body and not body[0].strip():
            body.pop(0)
        out += ["", f"## {L['task_body']}（{task['file']}）"]
        if task.get("adopted"):
            out.append("> 导入的 v1 正文：状态、原则、写集、清单与日志以本简报（账本）为准，下文只保留设计与目标叙述。")
        out += body[:BRIEF_BODY_LINES]
        if len(body) > BRIEF_BODY_LINES:
            out.append(L["truncated"])
    if task.get("sources"):
        out += ["", f"## {L['sources']}"]
        out += [f"- {s.get('path')}" + (f"#{s['heading']}" if s.get("heading") else "") + f" — {s.get('intent', '')}"
                for s in task["sources"]]
    return "\n".join(out) + "\n"


def checklist(ledger: Ledger, task: dict[str, Any]) -> dict[str, str]:
    keys = [p["id"] for p in ledger.collection("P")] + [o["id"] for o in owned(ledger, task["id"])]
    if task.get("kind") == "investigation":
        keys.append("Q")  # the answer to the investigation question and its evidence
    return {key: "" for key in keys}
