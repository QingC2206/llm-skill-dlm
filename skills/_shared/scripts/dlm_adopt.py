#!/usr/bin/env python3
"""Mechanical import of a continued v1 Epic into a v2 ledger (``dlm adopt``).

Only deterministic facts are imported: title, status, principles with their
recorded confirmation, write boundary and subtask identity/status/files.
Obligations, decisions and inventories of unfinished work are added afterwards
by the executor; adoption never fabricates them.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import dlm_render as render
from dlm_config import DlmError, Host
from dlm_invariants import blank, check_ledger
from dlm_model import EPIC_DOC, LEDGER, README, Ledger, today

BOUNDARY_RE = re.compile(r"<modification_boundary\b[^>]*>(.*?)</modification_boundary>", re.S)
SCOPE_RE = re.compile(r"<scope\s+([^>]*?)\s*/>", re.S)
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


def _meta(text: str) -> dict[str, str]:
    meta = {}
    for line in text.splitlines():
        match = re.match(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$", line)
        if match and not set(match.group(1)) <= {"-", ":"}:
            meta.setdefault(match.group(1), match.group(2))
    return meta


def _section(text: str, title: str) -> str | None:
    match = re.search(rf"^##\s+{re.escape(title)}\s*$", text, re.M)
    if not match:
        return None
    rest = text[match.end():]
    end = re.search(r"^##\s+", rest, re.M)
    return (rest[: end.start()] if end else rest).strip()


def _principles(body: str) -> list[str]:
    parts = re.split(r"^###\s+", body, flags=re.M)
    if len(parts) > 1:
        return [re.sub(r"\s+", " ", p).strip() for p in parts[1:] if p.strip()]
    items = re.split(r"^\d+\.\s+", body, flags=re.M)
    if len(items) > 2:
        return [re.sub(r"\s+", " ", p).strip() for p in items[1:] if p.strip()]
    return [re.sub(r"\s+", " ", body).strip()] if body.strip() else []


def _boundary(text: str) -> dict[str, list[str]]:
    scopes: dict[str, list[str]] = {k: [] for k in ("write", "generated", "read_only", "forbidden")}
    match = BOUNDARY_RE.search(text)
    if not match:
        return scopes
    for raw in SCOPE_RE.findall(match.group(1)):
        attrs = dict(ATTR_RE.findall(raw))
        if attrs.get("kind") in scopes and attrs.get("path"):
            scopes[attrs["kind"]].append(attrs["path"])
    return scopes


def _task_status(raw: str) -> str:
    if re.search(r"已停止|停止|归档|取消|废弃|stopped|dropped|cancel", raw, re.I):
        return "dropped"
    if re.search(r"✅|完成|Verified|done", raw, re.I):
        return "done"
    if re.search(r"🔄|进行|待验收|active", raw, re.I):
        return "active"
    if re.search(r"⏸|暂停|阻塞|blocked", raw, re.I):
        return "blocked"
    return "draft"


def adopt(host: Host, args: Any) -> int:
    from dlm_model import epic_dirs
    match = re.match(r"^(E\d{2,})", Path(args.epic).name)
    hits = [p for p in epic_dirs(host) if match and re.match(rf"^{match.group(1)}(?:_|$)", p.name)]
    if len(hits) != 1:
        raise DlmError(f"Epic {args.epic} 定位结果 {len(hits)} 个")
    epic_dir = hits[0]
    if (epic_dir / LEDGER).exists():
        raise DlmError(f"{epic_dir.name} 已是 v2")
    if (epic_dir / EPIC_DOC).exists():
        raise DlmError(f"{epic_dir.name}/epic.md 已存在，无法安放旧 README")
    readme = (epic_dir / README).read_text(encoding="utf-8-sig")
    title_match = re.match(r"^#\s+(E\d+)[:：]\s*(.+)$", readme.splitlines()[0] if readme else "")
    if not title_match:
        raise DlmError("旧 README 首行不是 '# Exxx: 标题'")
    meta = _meta(readme)
    if re.search(r"✅|完成|Verified", meta.get("状态", "")):
        raise DlmError("已完成的历史 Epic 不需要导入；只读保留即可")
    goal = _section(readme, "核心目标") or ""
    outcome = args.outcome or goal.split("\n\n")[0].strip()
    if blank(outcome):
        raise DlmError("需要 --outcome（旧 README 没有可用的核心目标）")
    ledger = Ledger.new(host, epic_dir, {"id": title_match.group(1), "title": title_match.group(2).strip(),
                                         "outcome": outcome, "status": "active",
                                         "created": meta.get("创建日期", today()), "relations": [],
                                         "extra_gates": [], "adopted": today()})
    ledger.data["boundary"] = _boundary(readme)
    principles = _principles(_section(readme, "Epic 核心原则") or "")
    confirmation = args.confirmation or meta.get("原则确认", "")
    decision_id = None
    if principles:
        if blank(confirmation):
            raise DlmError("旧 Epic 有原则但没有确认记录；需要 --confirmation 提供用户确认原话")
        decision = ledger.add("D", {"source": "user", "kind": "principle", "quote": confirmation,
                                    "session": "adopted from v1 README", "date": today(),
                                    "summary": "v1 已确认的 Epic 原则", "supersedes": [], "affects": []})
        decision_id = decision["id"]
        for text in principles:
            ledger.add("P", {"text": text, "decision": decision_id})
    acceptance = [a for a in (args.acceptance or []) if not blank(a)] or [f"核心目标成立：{outcome}"]
    if decision_id is None:
        decision_id = ledger.add("D", {"source": "executor", "kind": "requirement",
                                       "quote": "", "summary": "由 v1 README 的核心目标导入 Epic 级验收",
                                       "session": "adopted from v1 README", "date": today(),
                                       "supersedes": [], "affects": []})["id"]
    for text in acceptance:
        ledger.add("O", {"text": text, "owner": "epic", "coverage": "single", "origin": decision_id,
                         "preservers": [], "status": "open"})
    unfinished = []
    for path in sorted((epic_dir / "subtasks").glob("S*.md")):
        text = path.read_text(encoding="utf-8-sig")
        id_match = re.match(r"^(S\d{2,})", path.name)
        if not id_match:
            continue
        first = text.splitlines()[0] if text else ""
        title = re.sub(r"^#\s+S\d+[a-z]?[:：]\s*", "", first).strip() or path.stem
        task_meta = _meta(text)
        status = _task_status(task_meta.get("状态", ""))
        boundary = _boundary(text)
        task = {"id": id_match.group(1), "title": title, "file": f"subtasks/{path.name}", "status": status,
                "kind": "deliver", "priority": task_meta.get("优先级", ""), "depth": task_meta.get("任务深度", ""),
                "depth_reason": "", "needs": [], "boundary": {"write": boundary["write"],
                                                              "generated": boundary["generated"]},
                "sources": [], "adopted": True, "legacy_deps": task_meta.get("依赖", "")}
        if any(t["id"] == task["id"] for t in ledger.collection("S")):
            task["id"] = f"{task['id']}_{path.stem[-4:]}"
        ledger.collection("S").append(task)
        if status == "dropped":
            task["drop"] = {"reason": task_meta.get("状态", ""), "date": today()}
        elif status != "done":
            unfinished.append(task["id"])
    report = check_ledger(ledger)
    for finding in report.findings:
        print(str(finding))
    if args.dry_run:
        print(f"[DRY-RUN] 将导入 {epic_dir.name}：原则 {len(principles)}，子任务 {len(ledger.collection('S'))}")
        return 0
    (epic_dir / README).rename(epic_dir / EPIC_DOC)
    doc = epic_dir / EPIC_DOC
    doc.write_text("<!-- dlm: adopted v1 README。状态、原则、写集、子任务索引与清单以 README.md / ledger.json 为准；"
                   "本文只作为背景与设计叙述。 -->\n" + doc.read_text(encoding="utf-8-sig"), encoding="utf-8")
    ledger.log("adopt", f"由 v1 导入；未完成子任务 {unfinished}")
    ledger.save()
    render.write_readme(ledger)
    render.write_backlog(host)
    print(f"[OK] {epic_dir.name} 已导入；旧 README 移为 epic.md")
    if unfinished:
        print(f"未完成子任务 {unfinished}：续接前用 dlm oblige 登记各自义务（关闭时必须至少承担一项）")
    if report.blocking:
        print(f"注意：导入后仍有 {len(report.blocking)} 项阻断（多为旧边界与路径问题），续接前修正")
    return 0
