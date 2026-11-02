#!/usr/bin/env python3
"""dlm — the single CLI of the DLM pack.

Exit codes: 0 ok; 1 deterministic rule failure (or unknown at a gate); 2 bad usage.
State only changes through these commands; every mutation is checked by the
shared invariants before it is written. Run ``dlm <command> -h`` for details.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

sys.dont_write_bytecode = True

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

import dlm_inventory as inventory  # noqa: E402
import dlm_paths as paths  # noqa: E402
import dlm_render as render  # noqa: E402
import dlm_scope as scope  # noqa: E402
from dlm_config import DlmError, DlmUsageError, Host, load_host  # noqa: E402
from dlm_invariants import Report, blank, check_ledger, locator_state, user_decision  # noqa: E402
from dlm_model import (ARTIFACT_STATES, COVERAGE, DECISION_KINDS, DEPTHS, EPIC_DOC, HYPOTHESIS_STATUS,  # noqa: E402
                       ITEM_STATUS, LEDGER, LIMIT_KINDS, ON_EXCEED, EVIDENCE_SCOPES, Ledger, find_epic_dir,
                       next_epic_id, slugify, split_list, today)
from dlm_signals import draft_report, ledger_signals  # noqa: E402
from dlm_status import TERMINAL_OBLIGATION, obligation_status, owned  # noqa: E402

REOPEN_CAUSES = ("regression", "omission", "new-requirement", "superseded", "evidence-lost")


# ------------------------------------------------------------------ helpers
def emit(lines: list[str] | str) -> None:
    if isinstance(lines, str):
        lines = [lines]
    for line in lines:
        print(line)


def print_report(report: Report, *, only_blocking: bool = False) -> None:
    for finding in report.findings:
        if only_blocking and finding.level == "warn":
            continue
        print(str(finding))


def commit(ledger: Ledger, args: argparse.Namespace, mutate: Callable[[], Any], *, label: str) -> Any:
    """Apply a mutation; refuse it if it introduces new blocking findings."""
    before_report = check_ledger(ledger)
    ledger_signals(ledger, before_report)
    before = {str(f) for f in before_report.blocking}
    before_warn = {str(f) for f in before_report.findings if f.level == "warn"}
    result = mutate()
    after = check_ledger(ledger)
    ledger_signals(ledger, after)
    introduced = [f for f in after.blocking if str(f) not in before]
    if introduced:
        print_report_list(introduced)
        raise DlmError(f"{label}：会引入 {len(introduced)} 项规则违例，未写入")
    for finding in after.findings:
        if finding.level == "warn" and str(finding) not in before_warn:
            print(str(finding))
    if args.dry_run:
        emit(f"[DRY-RUN] {label}：校验通过，未写入")
        return result
    ledger.save()
    render.write_readme(ledger)
    render.write_backlog(ledger.host)
    emit(f"[OK] {label}")
    return result


def print_report_list(findings: list[Any]) -> None:
    for finding in findings:
        print(str(finding))


def parse_boundary_args(args: argparse.Namespace) -> dict[str, list[str]]:
    result = {}
    for kind, attr in (("write", "write"), ("generated", "generated"), ("read_only", "read_only"),
                       ("forbidden", "forbidden")):
        values = [paths.validate_pattern(v) for v in (getattr(args, attr, None) or [])]
        result[kind] = values
    return result


def parse_needs(values: list[str] | None) -> list[dict[str, str]]:
    needs = []
    for raw in values or []:
        ref, _, state = raw.partition(":")
        state = state or "contract"
        if state not in ARTIFACT_STATES:
            raise DlmUsageError(f"前置 {raw}：状态须为 {'/'.join(ARTIFACT_STATES)}")
        needs.append({"artifact": ref.strip(), "state": state})
    return needs


def parse_source(raw: str) -> dict[str, str]:
    location, _, intent = raw.partition("::")
    path, _, heading = location.partition("#")
    if blank(intent):
        raise DlmUsageError(f"来源 {raw} 需要用 ::intent 写明本任务消费什么")
    return {"path": paths.validate_pattern(path), "heading": heading.strip(), "intent": intent.strip()}


def require_user_decision(ledger: Ledger, ref: str | None, what: str) -> dict[str, Any]:
    decision = user_decision(ledger, ref)
    if decision is None:
        raise DlmError(f"{what}需要引用记录原话的用户决议（--decision D#，先用 dlm decide --source user 记录）")
    return decision


def write_text(path: Path, text: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# --------------------------------------------------------------- epic group
def cmd_epic_create(host: Host, args: argparse.Namespace) -> int:
    principles = [p for p in (args.principle or []) if not blank(p)]
    if not principles:
        raise DlmError("新建 Epic 需要已向用户展示并获确认的具体原则（--principle，可重复）")
    if blank(args.confirmation):
        raise DlmError("新建 Epic 需要用户确认原话（--confirmation）；参数只记录既有确认，不能代替确认")
    if blank(args.outcome):
        raise DlmError("新建 Epic 需要一个可观察成果（--outcome）")
    if not [a for a in (args.acceptance or []) if not blank(a)]:
        raise DlmError("新建 Epic 至少需要一条 Epic 级验收（--acceptance）")
    epic_id = next_epic_id(host)
    epic_dir = host.tasks_dir / f"{epic_id}_{slugify(args.slug or args.title)}"
    relations = []
    for raw in args.relation or []:
        kind, _, target = raw.partition(":")
        relations.append({"kind": kind.strip(), "target": target.strip()})
    ledger = Ledger.new(host, epic_dir, {
        "id": epic_id, "title": args.title, "outcome": args.outcome, "status": "active",
        "created": today(), "domains": split_list(args.domain), "relations": relations, "extra_gates": []})
    ledger.data["boundary"] = parse_boundary_args(args)
    decision = ledger.add("D", {"source": "user", "kind": "principle", "quote": args.confirmation,
                                "session": args.session or "", "date": today(),
                                "summary": "Epic 原则与创建授权", "supersedes": [], "affects": []})
    for text in principles:
        ledger.add("P", {"text": text, "decision": decision["id"]})
    for text in args.acceptance:
        if not blank(text):
            ledger.add("O", {"text": text, "owner": "epic", "coverage": "single", "origin": decision["id"],
                             "preservers": [], "status": "open"})
    report = check_ledger(ledger)
    ledger_signals(ledger, report)
    print_report(report)
    if report.blocking:
        raise DlmError("Epic 未创建")
    if args.dry_run:
        emit(f"[DRY-RUN] 将创建 {host.rel(epic_dir)}（未写入）")
        return 0
    L = render.locale(host)
    write_text(epic_dir / EPIC_DOC, f"# {epic_id}: {args.title}\n\n## {L['epic_background']}\n\n{args.background or ''}\n\n"
               f"## {L['epic_design']}\n\n{args.design or ''}\n\n## {L['epic_scope']}\n\n{args.scope or ''}\n", False)
    (epic_dir / "tasks").mkdir(parents=True, exist_ok=True)
    ledger.log("epic create", f"{epic_id} {args.title}；原则 {len(principles)} 条，确认：{args.confirmation}")
    ledger.save()
    render.write_readme(ledger)
    render.write_backlog(host)
    emit(f"[OK] 已创建 {host.rel(epic_dir)}")
    return 0


def cmd_epic_relate(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)

    def mutate() -> None:
        relations = ledger.epic.setdefault("relations", [])
        if args.verifies:
            relations.append({"kind": "verifies", "target": args.verifies, "note": args.note or ""})
        if args.artifact:
            relations.append({"kind": "artifact", "target": args.artifact, "note": args.note or ""})
        ledger.log("epic relate", json.dumps(relations[-1], ensure_ascii=False))

    if not (args.verifies or args.artifact):
        raise DlmUsageError("需要 --verifies Exxx 或 --artifact Exxx/Ay")
    commit(ledger, args, mutate, label=f"{ledger.epic_id} 关系已更新")
    return 0


def cmd_epic_boundary(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    expands = bool(args.add_write or args.add_generated or args.remove)
    if expands:
        require_user_decision(ledger, args.decision, "扩大写集或移除限制")

    def mutate() -> None:
        boundary = ledger.data.setdefault("boundary", {})
        for kind, values in (("write", args.add_write), ("generated", args.add_generated),
                             ("read_only", args.add_read_only), ("forbidden", args.add_forbidden)):
            for value in values or []:
                value = paths.validate_pattern(value)
                if value not in boundary.setdefault(kind, []):
                    boundary[kind].append(value)
        for value in args.remove or []:
            value = paths.norm(value)
            for kind in paths.BOUNDARY_KINDS:
                if value in boundary.get(kind, []):
                    boundary[kind].remove(value)
        ledger.log("epic boundary", f"决议 {args.decision or '—'}：{json.dumps(boundary, ensure_ascii=False)}")

    commit(ledger, args, mutate, label=f"{ledger.epic_id} 修改边界已更新")
    return 0


def cmd_epic_gate(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    require_user_decision(ledger, args.decision, "新增 Epic 门禁")

    def mutate() -> None:
        ledger.epic.setdefault("extra_gates", []).append(
            {"name": args.name, "command": args.command or "", "decision": args.decision})
        ledger.log("epic gate", f"{args.name}（{args.decision}）")

    commit(ledger, args, mutate, label=f"{ledger.epic_id} 门禁 {args.name} 已登记")
    return 0


def cmd_epic_status(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    if args.set == "done":
        raise DlmUsageError("完成请使用 dlm epic close")

    def mutate() -> None:
        ledger.epic["status"] = args.set
        ledger.log("epic status", f"{args.set}：{args.note or ''}")

    commit(ledger, args, mutate, label=f"{ledger.epic_id} 状态 {args.set}")
    return 0


def cmd_epic_close(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    report = check_ledger(ledger)
    problems = [str(f) for f in report.blocking]
    for t in ledger.collection("S"):
        if t.get("status") not in {"done", "dropped"}:
            problems.append(f"[ERROR] I5: 子任务 {t['id']} 状态 {t.get('status')}")
    for o in ledger.collection("O"):
        status = obligation_status(ledger, o)
        if status not in TERMINAL_OBLIGATION:
            problems.append(f"[ERROR] I5: {o['id']} 为 {status}：{o.get('text', '')}")
    referenced = set()
    for d in ledger.collection("D"):
        referenced.update(d.get("basis", []) or [])
    for x in ledger.collection("L"):
        if x.get("basis"):
            referenced.add(x["basis"])
    for h in ledger.collection("H"):
        if h.get("status") == "unverified" and h["id"] in referenced:
            problems.append(f"[ERROR] I5: 被引用的假设 {h['id']} 仍未证实")
    if problems:
        emit(problems)
        raise DlmError(f"{ledger.epic_id} 不能关闭（{len(problems)} 项）")
    if args.dry_run:
        emit(f"[DRY-RUN] {ledger.epic_id} 满足关闭的机械条件；语义完成仍须执行者判断")
        return 0
    ledger.epic["status"] = "done"
    ledger.log("epic close", "全部义务已终结")
    ledger.save()
    render.write_readme(ledger)
    render.write_backlog(host)
    emit(f"[OK] {ledger.epic_id} 已关闭（机械条件满足是必要条件，不代替语义判断）")
    return 0


# --------------------------------------------------------------- task group
def cmd_task_create(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    task_id = ledger.next_id("S")
    file_rel = f"tasks/{task_id}_{slugify(args.slug or args.title)}.md"

    def mutate() -> dict[str, Any]:
        task = {"id": task_id, "title": args.title, "file": file_rel, "status": "draft",
                "kind": args.kind, "priority": args.priority, "depth": args.depth,
                "depth_reason": args.depth_reason or "", "needs": parse_needs(args.needs),
                "boundary": {"write": [paths.validate_pattern(v) for v in args.write or []],
                             "generated": [paths.validate_pattern(v) for v in args.generated or []]},
                "sources": [parse_source(s) for s in args.source or []]}
        if args.kind == "investigation":
            task["question"] = args.question or ""
            task["stop"] = args.stop or ""
        known, digests = scope.baseline_for(host, task["boundary"])
        if known:
            task["baseline"] = digests
        ledger.collection("S").append(task)
        for ref in split_list(args.owns):
            obligation = ledger.get(ref, "O")
            obligation["owner"] = task_id
        for text in args.oblige or []:
            ledger.add("O", {"text": text, "owner": task_id, "coverage": args.coverage,
                             "population": args.population, "origin": args.origin, "derives": args.derives,
                             "sample_decision": args.sample_decision, "preservers": [], "status": "open"})
        ledger.log("task create", f"{task_id} {args.title}")
        return task

    L = render.locale(host)
    body_path = ledger.dir / file_rel
    created_body = not body_path.exists()
    write_text(body_path,
               f"# {task_id}: {args.title}\n\n<!-- dlm:task {ledger.epic_id}/{task_id} — 状态、义务与边界见 ledger.json；用 dlm brief 查看 -->\n\n"
               f"## {L['task_design']}\n\n{args.design or ''}\n\n## {L['task_paths']}\n\n", False)
    try:
        commit(ledger, args, mutate, label=f"{ledger.epic_id}/{task_id} 已创建")
    except Exception:
        if created_body:
            body_path.unlink(missing_ok=True)
        raise
    if args.dry_run and created_body:
        body_path.unlink(missing_ok=True)
    return 0


def cmd_task_edit(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    task = ledger.task(args.task)

    def mutate() -> None:
        for field in ("title", "priority", "depth", "depth_reason", "question", "stop"):
            value = getattr(args, field, None)
            if value is not None:
                task[field] = value
        task["needs"] = [n for n in task.get("needs", []) if n.get("artifact") not in set(args.remove_need or [])]
        task["needs"].extend(parse_needs(args.add_need))
        boundary = task.setdefault("boundary", {"write": [], "generated": []})
        for kind, values in (("write", args.add_write), ("generated", args.add_generated)):
            for value in values or []:
                value = paths.validate_pattern(value)
                if value not in boundary.setdefault(kind, []):
                    boundary[kind].append(value)
        for value in args.remove_scope or []:
            for kind in ("write", "generated"):
                if paths.norm(value) in boundary.get(kind, []):
                    boundary[kind].remove(paths.norm(value))
        sources = task.setdefault("sources", [])
        sources[:] = [s for s in sources if s.get("path") not in set(args.remove_source or [])]
        sources.extend(parse_source(s) for s in args.add_source or [])
        ledger.log("task edit", f"{task['id']}")

    commit(ledger, args, mutate, label=f"{ledger.epic_id}/{task['id']} 已更新")
    return 0


def cmd_task_start(host: Host, args: argparse.Namespace) -> int:
    from dlm_model import resolve_external
    from dlm_status import artifact_ready
    ledger = Ledger.load(host, args.epic)
    task = ledger.task(args.task)
    missing = []
    for need in task.get("needs", []) or []:
        resolved = resolve_external(host, need["artifact"], ledger)
        if resolved is None:
            missing.append(f"{need['artifact']}（无法解析）")
        elif not artifact_ready(resolved[1].get("state", ""), need["state"]):
            missing.append(f"{need['artifact']}（{resolved[1].get('state')} < {need['state']}）")

    def mutate() -> None:
        if missing:
            task["status"] = "blocked"
            task["blocked_on"] = missing
            ledger.log("task start", f"{task['id']} blocked：{'; '.join(missing)}")
        else:
            first = task.get("status") == "draft"
            task["status"] = "active"
            task.pop("blocked_on", None)
            if first or "baseline" not in task:
                known, digests = scope.baseline_for(host, task.get("boundary") or {})
                if known:
                    task["baseline"] = digests
            ledger.log("task start", f"{task['id']} active")

    commit(ledger, args, mutate, label=f"{ledger.epic_id}/{task['id']} → {'blocked' if missing else 'active'}")
    if missing:
        emit([f"  缺少产物：{m}" for m in missing])
        return 1
    emit(render.render_brief(ledger, task))
    return 0


def cmd_task_drop(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    task = ledger.task(args.task)
    require_user_decision(ledger, args.decision, "停止任务")

    def mutate() -> None:
        task["status"] = "dropped"
        task["drop"] = {"decision": args.decision, "reason": args.reason or "", "date": today()}
        ledger.log("task drop", f"{task['id']}（{args.decision}）{args.reason or ''}")

    commit(ledger, args, mutate, label=f"{ledger.epic_id}/{task['id']} → dropped")
    return 0


def cmd_task_block(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    task = ledger.task(args.task)

    def mutate() -> None:
        task["status"] = "blocked"
        task["blocked_on"] = [args.on]
        ledger.log("task block", f"{task['id']}：{args.on}")

    commit(ledger, args, mutate, label=f"{ledger.epic_id}/{task['id']} → blocked")
    return 0


# ---------------------------------------------------------- semantic objects
def cmd_decide(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    if args.source == "user" and blank(args.quote):
        raise DlmError("用户决议必须记录原话 --quote")

    def mutate() -> dict[str, Any]:
        decision = ledger.add("D", {"source": args.source, "kind": args.kind, "quote": args.quote or "",
                                    "summary": args.summary or "", "session": args.session or "",
                                    "date": today(), "supersedes": split_list(args.supersedes),
                                    "affects": split_list(args.affects), "basis": split_list(args.basis)})
        ledger.log("decide", f"{decision['id']} [{args.source}/{args.kind}] {args.summary or args.quote}")
        emit(decision["id"])
        return decision

    commit(ledger, args, mutate, label=f"{ledger.epic_id} 决议已记录")
    return 0


def cmd_principle(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    require_user_decision(ledger, args.decision, "修改 Epic 原则")

    def mutate() -> None:
        if args.remove:
            principle = ledger.get(args.remove, "P")
            ledger.collection("P").remove(principle)
            ledger.log("principle", f"移除 {args.remove}（{args.decision}）")
        elif args.edit:
            principle = ledger.get(args.edit, "P")
            principle.update({"text": args.text, "decision": args.decision})
            ledger.log("principle", f"修改 {args.edit}（{args.decision}）")
        else:
            principle = ledger.add("P", {"text": args.text, "decision": args.decision})
            ledger.log("principle", f"新增 {principle['id']}（{args.decision}）")

    if not args.remove and blank(args.text):
        raise DlmUsageError("需要 --text")
    commit(ledger, args, mutate, label=f"{ledger.epic_id} 原则已更新（受影响任务的复盘需按实际影响重评）")
    return 0


def cmd_oblige(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)

    def mutate() -> None:
        obligation = ledger.add("O", {"text": args.text, "owner": args.owner, "coverage": args.coverage,
                                      "population": args.population, "origin": args.origin,
                                      "derives": args.derives,
                                      "sample_decision": args.sample_decision,
                                      "preservers": split_list(args.preservers), "status": "open"})
        ledger.log("oblige", f"{obligation['id']} → {args.owner}：{args.text}")
        emit(obligation["id"])

    commit(ledger, args, mutate, label=f"{ledger.epic_id} 义务已登记")
    return 0


def cmd_obligation_edit(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    obligation = ledger.get(args.obligation, "O")
    narrowing = (args.text is not None) or (args.coverage and args.coverage != obligation.get("coverage")
                                            and obligation.get("coverage") == "full") or (
        args.population is not None and args.population != obligation.get("population"))
    if narrowing:
        require_user_decision(ledger, args.decision, "改写义务、降低覆盖或更换全集")

    def mutate() -> None:
        for field in ("text", "owner", "coverage", "population", "origin", "derives", "sample_decision"):
            value = getattr(args, field, None)
            if value is not None:
                obligation[field] = value
        preservers = set(obligation.get("preservers") or [])
        preservers |= set(split_list(args.add_preserver))
        preservers -= set(split_list(args.remove_preserver))
        obligation["preservers"] = sorted(preservers)
        ledger.log("obligation edit", f"{obligation['id']}（{args.decision or '—'}）")

    commit(ledger, args, mutate, label=f"{ledger.epic_id}/{obligation['id']} 已更新")
    return 0


def cmd_artifact(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)

    def mutate() -> None:
        if args.set:
            artifact = ledger.get(args.set, "A")
            for field in ("state", "locator", "version", "text"):
                value = getattr(args, field, None)
                if value is not None:
                    artifact[field] = value
            ledger.log("artifact", f"{artifact['id']} → {artifact.get('state')}")
        else:
            if blank(args.text):
                raise DlmUsageError("新产物需要 --text")
            artifact = ledger.add("A", {"text": args.text, "producer": args.producer, "state": args.state or "planned",
                                        "locator": args.locator or "", "version": args.version or ""})
            ledger.log("artifact", f"{artifact['id']} {args.text}")
            emit(artifact["id"])

    commit(ledger, args, mutate, label=f"{ledger.epic_id} 产物已更新")
    return 0


def cmd_hypo(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)

    def mutate() -> None:
        if args.set:
            hypothesis = ledger.get(args.set, "H")
            hypothesis["status"] = args.status
            if args.evidence:
                hypothesis.setdefault("evidence", []).append(args.evidence)
            ledger.log("hypo", f"{hypothesis['id']} → {args.status}")
        else:
            hypothesis = ledger.add("H", {"claim": args.claim, "status": "unverified", "evidence": []})
            ledger.log("hypo", f"{hypothesis['id']} {args.claim}")
            emit(hypothesis["id"])

    if not args.set and blank(args.claim):
        raise DlmUsageError("新假设需要 --claim")
    commit(ledger, args, mutate, label=f"{ledger.epic_id} 假设已更新")
    return 0


def cmd_limit(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)

    def mutate() -> None:
        limit = ledger.add("L", {"what": args.what, "kind": args.kind, "basis": args.basis or "",
                                 "on_exceed": args.on_exceed})
        ledger.log("limit", f"{limit['id']} [{args.kind}] {args.what}")
        emit(limit["id"])

    commit(ledger, args, mutate, label=f"{ledger.epic_id} 限制已登记")
    return 0


def cmd_evidence(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    obligations = split_list(args.obligations)
    items = split_list(args.items)
    if not obligations and not items and not args.gate:
        raise DlmUsageError("证据至少关联 --obligations、--items 或 --gate 之一")

    def mutate() -> None:
        evidence = ledger.add("V", {"obligations": obligations, "items": items, "scope": args.scope,
                                    "locator": args.locator, "method": args.method or "",
                                    "answers": args.answers or "", "gate": args.gate or "",
                                    "duration": args.duration or "",
                                    "task": args.task or "", "date": today(), "status": "valid"})
        for ref in items:
            item = ledger.find(ref)
            if item is not None and args.item_status:
                item["status"] = args.item_status
                item.setdefault("evidence", []).append(evidence["id"])
        if not args.gate and not args.answers and not obligations:
            print("[WARN] I12: 该验证未说明回答的问题（--answers）或关联义务")
        ledger.log("evidence", f"{evidence['id']} [{args.scope}] {args.locator}")
        emit(evidence["id"])

    commit(ledger, args, mutate, label=f"{ledger.epic_id} 证据已登记")
    return 0


def cmd_transfer(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    obligation = ledger.get(args.obligation, "O")

    def mutate() -> None:
        obligation["status"] = "transferred"
        obligation["transfer"] = {"from": obligation.get("owner"), "to": args.to, "entry": args.entry,
                                  "date": today()}
        ledger.log("transfer", f"{obligation['id']} {obligation.get('owner')} → {args.to}：{args.entry}")

    commit(ledger, args, mutate, label=f"{ledger.epic_id}/{obligation['id']} 已转交 {args.to}")
    return 0


def cmd_accept_transfer(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    obligation = ledger.get(args.obligation, "O")
    if obligation.get("status") != "transferred":
        raise DlmError(f"{obligation['id']} 不是转交状态")

    def mutate() -> None:
        obligation["owner"] = obligation["transfer"]["to"]
        obligation["status"] = "open"
        obligation.setdefault("history", []).append(obligation.pop("transfer"))
        ledger.log("accept", f"{obligation['id']} 由 {obligation['owner']} 接手")

    commit(ledger, args, mutate, label=f"{ledger.epic_id}/{obligation['id']} 已接手")
    return 0


def cmd_resolve(host: Host, args: argparse.Namespace, status: str) -> int:
    ledger = Ledger.load(host, args.epic)
    obligation = ledger.get(args.obligation, "O")
    require_user_decision(ledger, args.decision, "豁免或替代义务")

    def mutate() -> None:
        obligation["status"] = status
        obligation["resolution"] = {"decision": args.decision, "date": today()}
        if status == "superseded" and args.text:
            new = ledger.add("O", {"text": args.text, "owner": args.owner or obligation.get("owner"),
                                   "coverage": obligation.get("coverage", "single"), "origin": args.decision,
                                   "preservers": obligation.get("preservers", []), "status": "open"})
            obligation["resolution"]["by"] = new["id"]
        ledger.log(status, f"{obligation['id']}（{args.decision}）")

    commit(ledger, args, mutate, label=f"{ledger.epic_id}/{obligation['id']} → {status}")
    return 0


def cmd_reopen(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    obligation = ledger.get(args.obligation, "O")
    cause = args.cause

    def evidence_of(o: dict[str, Any]) -> list[dict[str, Any]]:
        chosen = set(split_list(args.evidence))
        return [v for v in ledger.collection("V") if o["id"] in v.get("obligations", [])
                and v.get("status", "valid") == "valid" and (not chosen or v["id"] in chosen)]

    def reset_items(o: dict[str, Any]) -> None:
        population = ledger.find(o.get("population") or "", "N")
        if population is None:
            return
        wanted = set(split_list(args.items))
        for item in population.get("items", []):
            if item.get("status") == "pass" and (not wanted or item["id"] in wanted):
                item["status"] = "pending"

    if cause in {"new-requirement", "superseded"}:
        require_user_decision(ledger, args.decision, f"{cause}")
    if cause == "regression" and not args.by:
        raise DlmUsageError("regression 需要 --by <引入回归的任务>")

    def mutate() -> None:
        if cause == "regression":
            for v in evidence_of(obligation):
                v["status"] = "invalidated"
            reset_items(obligation)
            obligation["owner"] = args.by
            obligation["status"] = "open"
        elif cause == "omission":
            for v in evidence_of(obligation):
                v["status"] = "overclaimed"
            reset_items(obligation)
            obligation["status"] = "open"
            owner = ledger.find(obligation.get("owner") or "")
            if owner is not None and owner.get("id", "").startswith("S") and owner.get("status") == "done":
                owner["status"] = "active"
        elif cause == "evidence-lost":
            for v in evidence_of(obligation):
                v["status"] = "unverifiable"
        elif cause == "new-requirement":
            if blank(args.text) or not args.owner:
                raise DlmUsageError("new-requirement 需要 --text 与 --owner")
            ledger.add("O", {"text": args.text, "owner": args.owner, "coverage": "single", "origin": args.decision,
                             "preservers": [], "status": "open"})
        elif cause == "superseded":
            obligation["status"] = "superseded"
            obligation["resolution"] = {"decision": args.decision, "date": today()}
            if args.text:
                new = ledger.add("O", {"text": args.text, "owner": args.owner or obligation.get("owner"),
                                       "coverage": obligation.get("coverage", "single"), "origin": args.decision,
                                       "preservers": [], "status": "open"})
                obligation["resolution"]["by"] = new["id"]
        ledger.log("reopen", f"{obligation['id']} cause={cause} {args.note or ''}")

    commit(ledger, args, mutate, label=f"{ledger.epic_id}/{obligation['id']} reopen（{cause}）")
    return 0


def cmd_justify(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    ledger.task(args.task)
    fields = {}
    for raw in args.field or []:
        name, sep, value = raw.partition("=")
        if not sep:
            raise DlmUsageError(f"--field 需要 name=value：{raw}")
        fields[name.strip()] = value.strip()

    def mutate() -> None:
        j = ledger.add("J", {"task": args.task, "layer": args.layer, "fields": fields, "decision": args.decision,
                             "date": today()})
        ledger.log("justify", f"{j['id']} {args.task} {args.layer}")

    commit(ledger, args, mutate, label=f"{ledger.epic_id} 职责说明已登记")
    return 0


# ------------------------------------------------------- checkpoint / close
def _summary(ledger: Ledger, task: dict[str, Any]) -> dict[str, str]:
    return {o["id"]: obligation_status(ledger, o) for o in owned(ledger, task["id"])}


def cmd_checkpoint(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    task = ledger.task(args.task)
    if args.verdict in {"deviated", "pending"} and blank(args.note):
        raise DlmError("deviated / pending 必须写 --note（偏离或待验证内容、纠偏与下一步）")
    current = _summary(ledger, task)
    previous = (task.get("checkpoint") or {}).get("summary", {})
    delta = [f"{k}: {previous.get(k, '—')} → {v}" for k, v in current.items() if previous.get(k) != v]
    result = scope.attribute(host, ledger.data.get("boundary", {}), task.get("boundary") or {}, args.changed,
                             task.get("baseline"))

    def mutate() -> None:
        task["checkpoint"] = {"verdict": args.verdict, "note": args.note or "", "date": today(), "summary": current}
        ledger.log("checkpoint", f"{task['id']} {args.verdict}；义务变化：{'; '.join(delta) or '无'}；"
                                 f"{scope.describe(result)[0]}；{args.note or ''}")

    commit(ledger, args, mutate, label=f"{ledger.epic_id}/{task['id']} checkpoint {args.verdict}")
    mine = [v for v in ledger.collection("V") if v.get("task") == task["id"]
            or set(v.get("obligations", [])) & set(current)]
    full = [v for v in mine if v.get("scope") == "full"]
    durations = [v["duration"] for v in mine if v.get("duration")]
    emit(["义务变化：" + ("; ".join(delta) or "无"),
          f"已登记验证 {len(mine)} 次（全量 {len(full)} 次）" + (f"，耗时 {', '.join(durations)}" if durations else "")]
         + scope.describe(result, limit=10))
    return 0


def _review_lines(args: argparse.Namespace) -> dict[str, str]:
    lines: dict[str, str] = {}
    if args.review:
        data = json.loads(Path(args.review).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise DlmUsageError("--review 文件必须是 {ID: 判断} 对象")
        lines.update({str(k): str(v) for k, v in data.items()})
    for raw in args.line or []:
        key, sep, value = raw.partition("=")
        if not sep:
            raise DlmUsageError(f"--line 需要 ID=判断：{raw}")
        lines[key.strip()] = value.strip()
    return lines


def _evidence_locators(ledger: Ledger, obligations: list[dict[str, Any]]) -> list[str]:
    locators: list[str] = []
    ids = {o["id"] for o in obligations}
    for v in ledger.collection("V"):
        if v.get("status", "valid") == "valid" and ids & set(v.get("obligations", [])):
            locators.append(v.get("locator", ""))
    for o in obligations:
        population = ledger.find(o.get("population") or "", "N")
        for item in (population or {}).get("items", []):
            if item.get("status") == "pass":
                for ref in item.get("evidence", []):
                    v = ledger.find(ref, "V") if re.match(r"^V\d+$", ref) else None
                    locators.append(v.get("locator", "") if v else ref)
    return list(dict.fromkeys(l for l in locators if l))


def cmd_close(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    task = ledger.task(args.task)
    if args.print_checklist:
        emit(json.dumps(render.checklist(ledger, task), ensure_ascii=False, indent=2))
        return 0
    if task.get("status") == "done":
        raise DlmError(f"{task['id']} 已完成")
    problems = [str(f) for f in check_ledger(ledger).blocking]
    if task.get("kind", "deliver") == "deliver" and not owned(ledger, task["id"]):
        problems.append(f"[ERROR] E3: {task['id']} 未承担任何义务，无法证明完成（dlm oblige 登记）")
    for o in owned(ledger, task["id"]):
        status = obligation_status(ledger, o)
        if status not in TERMINAL_OBLIGATION | {"transferred"}:
            problems.append(f"[ERROR] I3: {o['id']} 为 {render.obligation_label(ledger, o)}：{o.get('text', '')}"
                            "（继续实现、dlm transfer 到具名任务，或取得用户豁免）")
    for o in ledger.collection("O"):
        if o.get("status") == "transferred" and (o.get("transfer") or {}).get("to") == task["id"]:
            problems.append(f"[ERROR] I3: {o['id']} 已转交给 {task['id']} 但未接手（dlm accept 后完成，或转交给其他具名任务）")
    for locator in _evidence_locators(ledger, owned(ledger, task["id"])):
        if locator_state(host.root, locator) == "missing":
            problems.append(f"[UNKNOWN] I14: 证明本任务义务的证据 {locator} 不存在（丢失时用 reopen --cause evidence-lost）")
    verdict = (task.get("checkpoint") or {}).get("verdict")
    if verdict != "aligned":
        problems.append(f"[ERROR] K3: 最近一次 checkpoint 为 {verdict or '无'}；先处理偏离/待验证并记录 aligned")
    boundary = task.get("boundary") or {}
    result = None
    if boundary.get("write") or boundary.get("generated") or args.changed:
        result = scope.attribute(host, ledger.data.get("boundary", {}), boundary, args.changed, task.get("baseline"))
        if not result.known:
            problems.append(f"[UNKNOWN] I14: {result.reason}")
        else:
            for path, kind, rule in result.conflicts:
                if kind in {"read_only", "forbidden"}:
                    problems.append(f"[ERROR] I9: 变更 {path} 落在 Epic {kind} 规则 {rule}")
                else:
                    problems.append(f"[ERROR] I9: 变更 {path} 越出写集（{rule}）")
            for name, hits in result.gates.items():
                ok = [v for v in ledger.collection("V") if v.get("gate") == name and v.get("status", "valid") == "valid"
                      and v.get("task") in {"", task["id"]}]
                if not ok:
                    problems.append(f"[ERROR] I12: 门禁 {name} 由 {hits[0]} 等 {len(hits)} 个变更触发，缺少证据"
                                    f"（{scope.gate_command(host, name) or '见宿主 policy'}；完成后 dlm evidence --gate {name}）")
            for layer in result.public:
                if not [j for j in ledger.collection("J") if j.get("task") == task["id"] and j.get("layer") == layer]:
                    problems.append(f"[ERROR] I9: 变更触及公共层 {layer}，缺少职责说明与用户决议（dlm justify）")
    for gate in ledger.epic.get("extra_gates") or []:
        if not [v for v in ledger.collection("V") if v.get("gate") == gate.get("name") and v.get("status", "valid") == "valid"]:
            problems.append(f"[ERROR] I12: Epic 门禁 {gate.get('name')} 缺少证据")
    expected = render.checklist(ledger, task)
    lines = _review_lines(args)
    missing = [k for k in expected if blank(lines.get(k)) or len(lines.get(k, "").strip()) < 4]
    if missing:
        problems.append(f"[ERROR] K4: 复盘缺少逐条判断 {missing}（dlm close ... --print-checklist 获取清单）")
    if problems:
        emit(problems)
        raise DlmError(f"{ledger.epic_id}/{task['id']} 不能关闭（{len(problems)} 项）")
    if args.dry_run:
        emit(f"[DRY-RUN] {task['id']} 满足关闭的机械条件")
        return 0
    task["status"] = "done"
    task["review"] = {"date": today(), "lines": {k: lines[k] for k in expected}}
    ledger.log("close", f"{task['id']} 完成；复盘 {len(expected)} 条")
    ledger.save()
    render.write_readme(ledger)
    render.write_backlog(host)
    emit(f"[OK] {ledger.epic_id}/{task['id']} 已关闭（必要条件满足，不代替语义判断）")
    if result is not None and result.known and result.attributed:
        try:
            import dlm_kb
            candidates = dlm_kb.sediment_candidates(host, result.attributed)
            if candidates:
                emit(["知识候选（按复用价值判断是否回写，非门禁）："] + [f"  {c}" for c in candidates[:15]])
        except Exception:  # noqa: BLE001
            pass
    return 0


# ---------------------------------------------------------------- views
def cmd_brief(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    emit(render.render_brief(ledger, ledger.task(args.task)))
    return 0


def cmd_render(host: Host, args: argparse.Namespace) -> int:
    from dlm_model import epic_dirs
    targets = [d for d in epic_dirs(host) if (d / LEDGER).is_file()] if args.all else [find_epic_dir(host, e) for e in args.epic]
    for directory in targets:
        ledger = Ledger.load(host, directory)
        changed = render.write_readme(ledger, args.dry_run)
        emit(f"{'[UPDATE]' if changed else '[OK]'} {host.rel(directory / 'README.md')}")
    if render.write_backlog(host, args.dry_run):
        emit("[UPDATE] backlog")
    return 0


def cmd_check(host: Host, args: argparse.Namespace) -> int:
    from dlm_model import epic_dirs
    if args.epic:
        targets = [find_epic_dir(host, e) for e in args.epic]
    else:
        targets = [d for d in epic_dirs(host) if (d / LEDGER).is_file()]
    blocking = 0
    for directory in targets:
        ledger = Ledger.load(host, directory)
        report = check_ledger(ledger)
        ledger_signals(ledger, report)
        state = render.verify_generated(directory / "README.md")
        if state is False:
            report.error("I13", f"{ledger.epic_id}: README.md 被手改（生成视图）；改 ledger 后 dlm render")
        elif state is None:
            report.warn("I13", f"{ledger.epic_id}: README.md 不是生成视图；运行 dlm render")
        print_report(report)
        blocking += len(report.blocking)
        emit(f"{ledger.epic_id}: {'OK' if not report.blocking else f'{len(report.blocking)} 项阻断'}")
    if not args.epic and len(targets) >= 3:
        from dlm_signals import host_web_report
        web = Report()
        host_web_report(host, [Ledger.load(host, d) for d in targets], web)
        print_report(web)
    if not targets:
        emit("没有 v2 Epic（ledger.json）；历史记录用 dlm legacy check")
    return 1 if blocking else 0


def cmd_scope(host: Host, args: argparse.Namespace) -> int:
    ledger = Ledger.load(host, args.epic)
    task = ledger.task(args.task)
    result = scope.attribute(host, ledger.data.get("boundary", {}), task.get("boundary") or {}, args.changed,
                             task.get("baseline"))
    emit(scope.describe(result, limit=200))
    for name in result.gates:
        command = scope.gate_command(host, name)
        if command:
            emit(f"  门禁 {name} 命令：{command}")
    return 0 if result.known and not result.conflicts else 1


def cmd_plan_check(host: Host, args: argparse.Namespace) -> int:
    if args.draft:
        data = json.loads(Path(args.draft).read_text(encoding="utf-8-sig"))
        report = draft_report(data, host.policy, host.triggers)
    else:
        if not args.epic:
            raise DlmUsageError("需要 Epic 或 --draft")
        ledger = Ledger.load(host, args.epic)
        report = check_ledger(ledger)
        ledger_signals(ledger, report)
        for o in ledger.collection("O"):
            if o.get("coverage") == "full":
                population = ledger.find(o.get("population") or "", "N")
                if population is not None and not population.get("sources") and not population.get("items"):
                    report.error("D7", f"{ledger.epic_id}/{o['id']}: 全集 {population['id']} 尚无机械来源")
    print_report(report)
    signals = [f for f in report.findings if f.code == "SIGNAL"]
    emit(f"阻断 {len(report.blocking)}，粒度信号 {len(signals)}（信号需与 Epic 候选一起交用户判断）")
    return 1 if report.blocking else 0


# ---------------------------------------------------------------- inventory
def cmd_inv(host: Host, args: argparse.Namespace) -> int:
    action = args.inv_action
    if action == "new":
        store = inventory.create(host, args.name, args.reference, args.target, args.epic, split_list(args.items),
                                 args.sample_quote, args.sample_decision)
        _save_store(store, args)
        emit(f"[OK] 盘点 {store.label} 已创建")
        return 0
    store = inventory.open_store(host, args.name, args.epic)
    before = {str(f) for f in check_ledger(store.ledger).blocking} if store.ledger is not None else set()
    args._before = before
    if action == "source":
        total, added, merged = inventory.add_source(host, store, args.side, args.kind, args.spec, args.within, args.key)
        _save_store(store, args)
        emit(f"[OK] 来源枚举 {total} 项：新增 {added}，两侧合并 {merged}")
    elif action == "add":
        added, _ = inventory.add_keys(store, args.side, split_list(args.key_value), manual=True, source_index=None,
                                      reason=args.reason)
        _save_store(store, args)
        emit(f"[OK] 手工补充 {added} 项（已标记，复审重点）")
    elif action == "diff":
        counts = inventory.diff(store)
        _save_store(store, args)
        emit(f"[OK] 两侧都有 {counts['both']}，目标缺失 {counts['ref']}（gap），目标多出 {counts['target']}（记录）")
    elif action == "mark":
        items = store.resolve_ids(split_list(args.ids)) if args.ids else []
        if args.filter:
            pattern = re.compile(args.filter)
            items += [i for i in store.items if pattern.search(i["key"]) and i not in items]
        if not items:
            raise DlmError("没有匹配的条目")
        inventory.mark(store, items, args.status, args.evidence, args.reason, args.decision)
        _save_store(store, args)
        emit(f"[OK] {len(items)} 个条目 → {args.status}：{', '.join(i['id'] for i in items[:20])}{' …' if len(items) > 20 else ''}")
    elif action == "report":
        lines, closed = inventory.report_lines(store, limit=args.limit)
        emit(lines)
        if args.gate and not closed:
            return 1
    elif action == "export-lease":
        summary = inventory.export_lease(
            host,
            store,
            work_items_path=args.work_items,
            runtime_path=args.runtime,
            no_runtime=args.no_runtime,
            claims_dir=args.claims_dir,
            task_id=args.task,
            sop_ref=args.sop_ref,
            priority_order=split_list(args.priority_order),
            priority_rules=args.priority_rule,
            default_priority=args.default_priority,
            remarks=args.remark,
            remarks_json=args.remarks_json,
            include_na=args.include_na,
            reset_state=args.reset_state,
            dry_run=args.dry_run,
        )
        _save_store(store, args)
        msg = f"[OK] {store.label} → {summary['work_items_path']}（导出 {summary['exported_count']} 项"
        if summary["runtime_path"]:
            msg += f"，runtime: {summary['runtime_path']}"
        msg += "）"
        emit(msg)
    elif action == "sync-lease":
        summary = inventory.sync_lease(
            host,
            store,
            work_items_path=args.work_items,
            runtime_path=args.runtime,
            evidence=args.evidence,
            blocked_status=args.blocked_status,
            require_zero_active=(args.require_zero_active or args.gate),
            task_id=args.task,
            obligations=split_list(args.obligations),
        )
        _save_store(store, args)
        lines, closed = inventory.report_lines(store, limit=args.limit)
        emit(
            f"[OK] {store.label} ← {summary['work_items_path']}（pass={summary['pass_count']} / "
            f"{args.blocked_status}={summary['blocked_count']} / pending={summary['pending_count']} / "
            f"active={summary['active_count']}"
            + (f" / 证据 {summary['evidence_id']}" if summary["evidence_id"] else "")
            + "）"
        )
        emit(lines)
        if args.gate and not closed:
            return 1
    return 0


def _save_store(store: inventory.Store, args: argparse.Namespace) -> None:
    if args.dry_run:
        emit("[DRY-RUN] 未写入")
        return
    if store.ledger is not None:
        before = getattr(args, "_before", set())
        introduced = [f for f in check_ledger(store.ledger).blocking if str(f) not in before]
        if introduced:
            print_report_list(introduced)
            raise DlmError(f"盘点修改会引入 {len(introduced)} 项规则违例，未写入")
        store.ledger.save()
        render.write_readme(store.ledger)
    else:
        store.save()


# ---------------------------------------------------------------- kb / legacy / adopt
def cmd_kb(host: Host, args: argparse.Namespace) -> int:
    import dlm_kb
    return dlm_kb.run(host, args)


def cmd_legacy(host: Host, args: argparse.Namespace) -> int:
    import dlm_kb
    return dlm_kb.legacy_check(host, args.path, strict=args.strict)


def cmd_adopt(host: Host, args: argparse.Namespace) -> int:
    import dlm_adopt
    return dlm_adopt.adopt(host, args)


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dlm", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="校验并预览，不写入")
    sub = parser.add_subparsers(dest="command", required=True)

    def boundary_opts(p: argparse.ArgumentParser, full: bool = True) -> None:
        p.add_argument("--write", action="append", help="可写路径或 glob（目录用 dir/**）")
        p.add_argument("--generated", action="append", help="生成物路径")
        if full:
            p.add_argument("--read-only", dest="read_only", action="append")
            p.add_argument("--forbidden", action="append")

    epic = sub.add_parser("epic", help="Epic 创建、关系、边界、状态与关闭").add_subparsers(dest="epic_action", required=True)
    p = epic.add_parser("create", help="创建 Epic（需已确认的原则与原话）")
    p.add_argument("title")
    p.add_argument("--outcome", required=True, help="一个可观察、可独立验收的成果")
    p.add_argument("--acceptance", action="append", help="Epic 级验收（owner=epic 的义务），可重复")
    p.add_argument("--principle", action="append", help="已获用户确认的原则，可重复")
    p.add_argument("--confirmation", help="用户确认原话")
    p.add_argument("--session", help="确认所在会话/日期")
    p.add_argument("--relation", action="append", help="verifies:Exxx 或 artifact:Exxx/Ay")
    p.add_argument("--domain", action="append")
    p.add_argument("--slug")
    p.add_argument("--background")
    p.add_argument("--design")
    p.add_argument("--scope")
    boundary_opts(p)
    p.set_defaults(func=cmd_epic_create)
    p = epic.add_parser("relate", help="登记 verifies 或具名产物关系")
    p.add_argument("epic")
    p.add_argument("--verifies")
    p.add_argument("--artifact")
    p.add_argument("--note")
    p.set_defaults(func=cmd_epic_relate)
    p = epic.add_parser("boundary", help="修改 Epic 写集（扩大须用户决议）")
    p.add_argument("epic")
    for name in ("add-write", "add-generated", "add-read-only", "add-forbidden", "remove"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), action="append")
    p.add_argument("--decision")
    p.set_defaults(func=cmd_epic_boundary)
    p = epic.add_parser("gate", help="新增 Epic 级门禁（须用户决议）")
    p.add_argument("epic")
    p.add_argument("--name", required=True)
    p.add_argument("--command")
    p.add_argument("--decision", required=True)
    p.set_defaults(func=cmd_epic_gate)
    p = epic.add_parser("status", help="暂停/恢复 Epic")
    p.add_argument("epic")
    p.add_argument("--set", choices=("active", "paused"), required=True)
    p.add_argument("--note")
    p.set_defaults(func=cmd_epic_status)
    p = epic.add_parser("close", help="关闭 Epic（全部义务终结）")
    p.add_argument("epic")
    p.set_defaults(func=cmd_epic_close)

    task = sub.add_parser("task", help="子任务创建、编辑、开工与阻塞").add_subparsers(dest="task_action", required=True)
    p = task.add_parser("create", help="创建子任务")
    p.add_argument("epic")
    p.add_argument("title")
    p.add_argument("--depth", choices=DEPTHS, required=True)
    p.add_argument("--depth-reason", dest="depth_reason", required=True)
    p.add_argument("--kind", choices=("deliver", "investigation"), default="deliver")
    p.add_argument("--question")
    p.add_argument("--stop")
    p.add_argument("--priority", default="P1")
    p.add_argument("--owns", action="append", help="已有义务编号")
    p.add_argument("--oblige", action="append", help="新建并承担的义务（可观察结果），可重复")
    p.add_argument("--coverage", choices=COVERAGE, default="single")
    p.add_argument("--population", help="coverage=full 时的全集 N#")
    p.add_argument("--origin", help="义务来源决议 D#")
    p.add_argument("--derives", help="派生自的既有义务 O#（义务需要 origin 或 derives）")
    p.add_argument("--sample-decision", dest="sample_decision")
    p.add_argument("--needs", action="append", help="前置产物 A#[:state] 或 Exxx/A#[:state]")
    p.add_argument("--source", action="append", help="必读来源 path[#heading]::intent")
    p.add_argument("--design", help="方案承接与实施设计正文")
    p.add_argument("--slug")
    boundary_opts(p, full=False)
    p.set_defaults(func=cmd_task_create)
    p = task.add_parser("edit", help="编辑子任务字段、前置、写集与来源")
    p.add_argument("epic")
    p.add_argument("task")
    for name in ("title", "priority", "depth-reason", "question", "stop"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"))
    p.add_argument("--depth", choices=DEPTHS)
    for name in ("add-need", "remove-need", "add-write", "add-generated", "remove-scope", "add-source", "remove-source"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"), action="append")
    p.set_defaults(func=cmd_task_edit)
    p = task.add_parser("start", help="核对前置产物并开工，输出简报")
    p.add_argument("epic")
    p.add_argument("task")
    p.set_defaults(func=cmd_task_start)
    p = task.add_parser("drop", help="按用户决议停止任务（其义务须先被替代、豁免或转交）")
    p.add_argument("epic")
    p.add_argument("task")
    p.add_argument("--decision", required=True)
    p.add_argument("--reason")
    p.set_defaults(func=cmd_task_drop)
    p = task.add_parser("block", help="标记阻塞并写明所缺产物")
    p.add_argument("epic")
    p.add_argument("task")
    p.add_argument("--on", required=True)
    p.set_defaults(func=cmd_task_block)

    p = sub.add_parser("decide", help="记录决议（用户决议须含原话）")
    p.add_argument("epic")
    p.add_argument("--source", choices=("user", "executor"), required=True)
    p.add_argument("--kind", choices=DECISION_KINDS, required=True)
    p.add_argument("--quote")
    p.add_argument("--summary")
    p.add_argument("--session")
    p.add_argument("--supersedes", action="append")
    p.add_argument("--affects", action="append")
    p.add_argument("--basis", action="append")
    p.set_defaults(func=cmd_decide)

    p = sub.add_parser("principle", help="增改删 Epic 原则（须用户决议）")
    p.add_argument("epic")
    p.add_argument("--text")
    p.add_argument("--edit")
    p.add_argument("--remove")
    p.add_argument("--decision", required=True)
    p.set_defaults(func=cmd_principle)

    p = sub.add_parser("oblige", help="登记义务（用户可观察结果）")
    p.add_argument("epic")
    p.add_argument("text")
    p.add_argument("--owner", required=True, help="S### 或 epic")
    p.add_argument("--coverage", choices=COVERAGE, default="single")
    p.add_argument("--population")
    p.add_argument("--origin")
    p.add_argument("--derives")
    p.add_argument("--sample-decision", dest="sample_decision")
    p.add_argument("--preservers", action="append")
    p.set_defaults(func=cmd_oblige)

    p = sub.add_parser("obligation", help="编辑义务（改写或降低覆盖须用户决议）")
    p.add_argument("epic")
    p.add_argument("obligation")
    for name in ("text", "owner", "population", "origin", "derives", "sample-decision", "decision"):
        p.add_argument(f"--{name}", dest=name.replace("-", "_"))
    p.add_argument("--coverage", choices=COVERAGE)
    p.add_argument("--add-preserver", dest="add_preserver", action="append")
    p.add_argument("--remove-preserver", dest="remove_preserver", action="append")
    p.set_defaults(func=cmd_obligation_edit)

    p = sub.add_parser("artifact", help="登记或更新产物")
    p.add_argument("epic")
    p.add_argument("--set", help="更新已有 A#")
    p.add_argument("--text")
    p.add_argument("--producer")
    p.add_argument("--state", choices=ARTIFACT_STATES)
    p.add_argument("--locator")
    p.add_argument("--version")
    p.set_defaults(func=cmd_artifact)

    p = sub.add_parser("hypo", help="登记或判定假设")
    p.add_argument("epic")
    p.add_argument("--claim")
    p.add_argument("--set")
    p.add_argument("--status", choices=HYPOTHESIS_STATUS)
    p.add_argument("--evidence")
    p.set_defaults(func=cmd_hypo)

    p = sub.add_parser("limit", help="登记限制（产品边界 / 实现预算 / 资源事实）")
    p.add_argument("epic")
    p.add_argument("what")
    p.add_argument("--kind", choices=LIMIT_KINDS, required=True)
    p.add_argument("--basis")
    p.add_argument("--on-exceed", dest="on_exceed", choices=ON_EXCEED, required=True)
    p.set_defaults(func=cmd_limit)

    p = sub.add_parser("evidence", help="登记证据（必须列出覆盖的义务/条目/门禁）")
    p.add_argument("epic")
    p.add_argument("--obligations", action="append")
    p.add_argument("--items", action="append")
    p.add_argument("--item-status", dest="item_status", choices=ITEM_STATUS)
    p.add_argument("--scope", choices=EVIDENCE_SCOPES, required=True)
    p.add_argument("--locator", required=True)
    p.add_argument("--method")
    p.add_argument("--answers", help="本次验证回答的问题")
    p.add_argument("--duration", help="验证耗时（如 12m），用于暴露重复全量验证的成本")
    p.add_argument("--gate")
    p.add_argument("--task")
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("transfer", help="把未完成义务转交给具名未完成任务")
    p.add_argument("epic")
    p.add_argument("obligation")
    p.add_argument("--to", required=True)
    p.add_argument("--entry", required=True, help="接手的进入条件")
    p.set_defaults(func=cmd_transfer)
    p = sub.add_parser("accept", help="接手被转交的义务")
    p.add_argument("epic")
    p.add_argument("obligation")
    p.set_defaults(func=cmd_accept_transfer)
    for name, status in (("waive", "waived"), ("supersede", "superseded")):
        p = sub.add_parser(name, help=f"{status}（须用户决议）")
        p.add_argument("epic")
        p.add_argument("obligation")
        p.add_argument("--decision", required=True)
        p.add_argument("--text")
        p.add_argument("--owner")
        p.set_defaults(func=lambda h, a, s=status: cmd_resolve(h, a, s))

    p = sub.add_parser("reopen", help="按五类原因重开，后果确定")
    p.add_argument("epic")
    p.add_argument("obligation")
    p.add_argument("--cause", choices=REOPEN_CAUSES, required=True)
    p.add_argument("--by")
    p.add_argument("--evidence", action="append")
    p.add_argument("--items", action="append")
    p.add_argument("--decision")
    p.add_argument("--text")
    p.add_argument("--owner")
    p.add_argument("--note")
    p.set_defaults(func=cmd_reopen)

    p = sub.add_parser("justify", help="触及公共层的职责说明（字段由宿主 policy 定义）")
    p.add_argument("epic")
    p.add_argument("task")
    p.add_argument("--layer", required=True)
    p.add_argument("--field", action="append", help="name=value")
    p.add_argument("--decision", required=True)
    p.set_defaults(func=cmd_justify)

    p = sub.add_parser("checkpoint", help="每轮收尾核对")
    p.add_argument("epic")
    p.add_argument("task")
    p.add_argument("--verdict", choices=("aligned", "deviated", "pending"), required=True)
    p.add_argument("--note")
    p.add_argument("--changed", nargs="*")
    p.set_defaults(func=cmd_checkpoint)

    p = sub.add_parser("close", help="关闭子任务（机械条件 + 逐条复盘）")
    p.add_argument("epic")
    p.add_argument("task")
    p.add_argument("--review", help="JSON：{P#/O#: 判断}")
    p.add_argument("--line", action="append", help="ID=判断")
    p.add_argument("--changed", nargs="*")
    p.add_argument("--print-checklist", action="store_true")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("brief", help="输出任务简报")
    p.add_argument("epic")
    p.add_argument("task")
    p.set_defaults(func=cmd_brief)
    p = sub.add_parser("render", help="重新生成 README 与 backlog")
    p.add_argument("epic", nargs="*")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_render)
    p = sub.add_parser("check", help="只读检查 v2 Epic")
    p.add_argument("epic", nargs="*")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("scope", help="变更归属、边界冲突与门禁计算")
    p.add_argument("epic")
    p.add_argument("task")
    p.add_argument("--changed", nargs="*")
    p.set_defaults(func=cmd_scope)
    p = sub.add_parser("plan-check", help="规划检查与粒度信号（--draft 不写盘）")
    p.add_argument("epic", nargs="?")
    p.add_argument("--draft")
    p.set_defaults(func=cmd_plan_check)

    inv = sub.add_parser("inv", help="全集盘点").add_subparsers(dest="inv_action", required=True)
    p = inv.add_parser("new")
    p.add_argument("name")
    p.add_argument("--reference", required=True, help="参照/全集定义")
    p.add_argument("--target", help="对齐模式的目标方")
    p.add_argument("--epic")
    p.add_argument("--items", action="append", help="小集合直接列出")
    p.add_argument("--sample-quote", dest="sample_quote", help="用户同意抽检的原话（独立盘点）")
    p.add_argument("--sample-decision", dest="sample_decision", help="用户抽检决议 D#（Epic 盘点）")
    p = inv.add_parser("source")
    p.add_argument("name")
    p.add_argument("--epic")
    p.add_argument("--side", choices=("ref", "target"), default="ref")
    p.add_argument("--kind", choices=("glob", "grep", "cmd", "list"), required=True)
    p.add_argument("--spec", required=True)
    p.add_argument("--in", dest="within")
    p.add_argument("--key", help="正则，group(1) 作为规范化 key")
    p = inv.add_parser("add")
    p.add_argument("name")
    p.add_argument("key_value", nargs="+")
    p.add_argument("--epic")
    p.add_argument("--side", choices=("ref", "target"), default="ref")
    p.add_argument("--reason", required=True, help="来源为何没有覆盖")
    p = inv.add_parser("diff")
    p.add_argument("name")
    p.add_argument("--epic")
    p = inv.add_parser("mark")
    p.add_argument("name")
    p.add_argument("--epic")
    p.add_argument("--ids", action="append")
    p.add_argument("--filter")
    p.add_argument("--status", choices=ITEM_STATUS, required=True)
    p.add_argument("--evidence")
    p.add_argument("--reason")
    p.add_argument("--decision", help="用户决议 D#（全量义务的参照条目判为 na 时必需）")
    p = inv.add_parser("report")
    p.add_argument("name")
    p.add_argument("--epic")
    p.add_argument("--gate", action="store_true", help="未闭合时返回 1")
    p.add_argument("--limit", type=int, default=40)
    p = inv.add_parser("export-lease", help="将盘点全集一键导出为 dlm-multi-llm-lease 的 work_items.md 与 work_item_runtime.json")
    p.add_argument("name")
    p.add_argument("--epic")
    p.add_argument("--work-items", dest="work_items", help="目标 work_items.md 路径（默认 <epic_dir>/references/work_items.md）")
    p.add_argument("--runtime", help="目标 work_item_runtime.json 路径")
    p.add_argument("--no-runtime", dest="no_runtime", action="store_true", help="仅生成 work_items.md，不生成/更新 work_item_runtime.json")
    p.add_argument("--claims-dir", dest="claims_dir", help="claims 目录（默认 <cache_dir>/work_item_claims/...）")
    p.add_argument("--task", help="关联的并行 worker 子任务编号 S###")
    p.add_argument("--sop-ref", dest="sop_ref", help="统一具体的 SOP / 任务指引路径")
    p.add_argument("--priority-order", dest="priority_order", action="append", help="优先级系列顺序（如 A,B,C）")
    p.add_argument("--priority-rule", dest="priority_rule", action="append", help="按正则分配优先级 TIER=REGEX（可重复）")
    p.add_argument("--default-priority", dest="default_priority", default="—", help="未命中规则时的默认优先级")
    p.add_argument("--remark", action="append", help="为指定模块或编号设置静态备注 KEY=REMARK（可重复，无备注默认留空）")
    p.add_argument("--remarks-json", dest="remarks_json", help="模块备注映射 JSON 文件 {模块: 备注}")
    p.add_argument("--include-na", dest="include_na", action="store_true", help="包含已判为 na 的条目")
    p.add_argument("--reset-state", dest="reset_state", action="store_true", help="忽略已有 work_items.md 中的运行态，按盘点状态重置")
    p = inv.add_parser("sync-lease", help="从 dlm-multi-llm-lease 的 work_items.md 一键回填盘点状态与证据")
    p.add_argument("name")
    p.add_argument("--epic")
    p.add_argument("--work-items", dest="work_items", help="源 work_items.md 路径")
    p.add_argument("--runtime", help="源 work_item_runtime.json 路径")
    p.add_argument("--evidence", help="pass 条目绑定的证据路径（默认指向 work_items.md）")
    p.add_argument("--blocked-status", dest="blocked_status", choices=("blocked", "fail"), default="blocked")
    p.add_argument("--require-zero-active", dest="require_zero_active", action="store_true", help="若仍有活跃 claim 则拒绝同步")
    p.add_argument("--gate", action="store_true", help="要求 0 活跃 claim 且盘点全集完全闭合，否则返回非 0")
    p.add_argument("--task", help="登记证据时关联的收口子任务编号 S###")
    p.add_argument("--obligations", action="append", help="额外关联的义务编号 O#")
    p.add_argument("--limit", type=int, default=40)
    for action_parser in inv.choices.values():
        action_parser.set_defaults(func=cmd_inv)

    kb = sub.add_parser("kb", help="知识库：check / route / scaffold / map / stale / sediment")
    kb.add_argument("kb_action", choices=("check", "route", "scaffold", "map", "stale", "sediment"))
    kb.add_argument("rest", nargs=argparse.REMAINDER)
    kb.set_defaults(func=cmd_kb)

    p = sub.add_parser("legacy", help="历史（v1）任务记录的只读报告")
    p.add_argument("legacy_action", choices=("check",))
    p.add_argument("path")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_legacy)

    p = sub.add_parser("adopt", help="把续接的 v1 Epic 机械导入为 v2 账本")
    p.add_argument("epic")
    p.add_argument("--outcome")
    p.add_argument("--acceptance", action="append")
    p.add_argument("--confirmation", help="原则缺少确认记录时，提供用户确认原话")
    p.set_defaults(func=cmd_adopt)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        host = load_host()
        return int(args.func(host, args) or 0)
    except DlmUsageError as exc:
        print(f"[USAGE] {exc}", file=sys.stderr)
        return 2
    except (DlmError, ValueError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
