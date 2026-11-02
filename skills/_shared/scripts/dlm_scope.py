#!/usr/bin/env python3
"""Changed-path collection and boundary/gate attribution.

Git (including nested repositories) is the only automatic source; when it is
unavailable the result is *unknown*, never an empty pass. ``--changed`` lets
the executor provide paths explicitly.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dlm_paths as paths
from dlm_config import Host


@dataclass
class ScopeResult:
    known: bool
    reason: str = ""
    changed: list[str] = field(default_factory=list)
    attributed: list[str] = field(default_factory=list)
    conflicts: list[tuple[str, str, str]] = field(default_factory=list)  # path, kind, rule
    unattributed: list[str] = field(default_factory=list)
    gates: dict[str, list[str]] = field(default_factory=dict)
    public: dict[str, list[str]] = field(default_factory=dict)


def _git(root: Path, args: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
    except OSError as exc:
        return 127, str(exc)
    return result.returncode, result.stdout


def git_changes(root: Path, prefix: str = "", depth: int = 0) -> tuple[bool, list[str], str]:
    code, out = _git(root, ["-c", "core.quotepath=off", "status", "--porcelain=v1", "-uall",
                            "--ignore-submodules=none"])
    if code != 0:
        return False, [], f"git status 失败（{root}）"
    changed: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:]
        if " -> " in entry:
            entry = entry.split(" -> ", 1)[1]
        entry = entry.strip().strip('"')
        nested = root / entry
        if depth < 4 and nested.is_dir() and (nested / ".git").exists():
            ok, inner, reason = git_changes(nested, f"{prefix}{entry.rstrip('/')}/", depth + 1)
            if not ok:
                return False, [], reason
            changed.extend(inner)
        else:
            changed.append(paths.norm(prefix + entry))
    return True, changed, ""


def file_digest(root: Path, path: str) -> str:
    target = root / path
    if not target.is_file():
        return "deleted"
    import hashlib
    return hashlib.sha1(target.read_bytes()).hexdigest()


def baseline_for(host: Host, task_boundary: dict[str, list[str]]) -> tuple[bool, dict[str, str]]:
    """Digest of paths already changed inside the task scope when work starts.

    Paths that stay byte-identical to this baseline are not attributed to the
    task later, so concurrent or earlier edits by others do not trigger its gates.
    """
    ok, changed, _ = git_changes(host.root)
    if not ok:
        return False, {}
    rules = {"write": task_boundary.get("write", []) or [], "generated": task_boundary.get("generated", []) or []}
    digests = {}
    for path in changed:
        kind, _ = paths.classify(path, rules)
        if kind in {"write", "generated"}:
            digests[path] = file_digest(host.root, path)
    return True, digests


def attribute(host: Host, epic_boundary: dict[str, list[str]], task_boundary: dict[str, list[str]],
              changed: list[str] | None, baseline: dict[str, str] | None = None) -> ScopeResult:
    explicit_list = [paths.norm(p) for p in (changed or []) if p and p.strip()]
    explicit_set = set(explicit_list)
    ok, git_list, reason = git_changes(host.root)
    if ok:
        # Explicit paths add to, never replace, what git observes.
        baseline = baseline or {}
        filtered_git = [
            p for p in git_list
            if p in explicit_set or not (p in baseline and baseline[p] == file_digest(host.root, p))
        ]
        merged = filtered_git + explicit_list
    elif changed is not None:
        merged = explicit_list
    else:
        return ScopeResult(known=False, reason=reason + "；可用 --changed 显式提供变更路径")
    result = ScopeResult(known=True, changed=sorted(set(merged)))
    task_rules = {"write": task_boundary.get("write", []) or [],
                  "generated": task_boundary.get("generated", []) or []}
    epic_has_write = bool((epic_boundary.get("write") or []) or (epic_boundary.get("generated") or []))
    for path in result.changed:
        kind, _rule = paths.classify(path, task_rules)
        if kind in {"write", "generated"}:
            epic_kind, epic_rule = paths.classify(path, epic_boundary)
            if epic_kind in {"read_only", "forbidden"}:
                result.conflicts.append((path, epic_kind, epic_rule or ""))
            elif epic_has_write and epic_kind not in {"write", "generated"}:
                result.conflicts.append((path, "out_of_epic_scope", "不在 Epic write/generated 写集内"))
            result.attributed.append(path)
            continue
        if path in explicit_set:
            epic_kind, epic_rule = paths.classify(path, epic_boundary)
            if epic_kind in {"read_only", "forbidden"}:
                result.conflicts.append((path, epic_kind, epic_rule or ""))
            else:
                result.conflicts.append((path, "out_of_task_scope", "不在本任务 write/generated 写集内"))
            result.attributed.append(path)
            continue
        result.unattributed.append(path)
    for gate in host.policy.get("gates", []) or []:
        hits = [p for p in result.attributed if any(paths.matches(p, g) for g in gate.get("paths", []))]
        if hits:
            result.gates[gate.get("name", "?")] = hits
    for layer in host.policy.get("public_layers", []) or []:
        hits = [p for p in result.attributed if paths.matches(p, layer)]
        if hits:
            result.public[layer] = hits
    return result


def describe(result: ScopeResult, limit: int = 30) -> list[str]:
    if not result.known:
        return [f"变更范围：未知 — {result.reason}"]
    lines = [f"变更：共 {len(result.changed)}，归属本任务写集 {len(result.attributed)}，"
             f"未归属 {len(result.unattributed)}，边界冲突 {len(result.conflicts)}"]
    for path in result.attributed[:limit]:
        lines.append(f"  + {path}")
    for path, kind, rule in result.conflicts[:limit]:
        label = f"Epic {kind}: {rule}" if kind in {"read_only", "forbidden"} else f"{kind}: {rule}"
        lines.append(f"  ! {path}（{label}）")
    for name, hits in result.gates.items():
        lines.append(f"  门禁 {name}：由 {len(hits)} 个变更触发")
    for layer, hits in result.public.items():
        lines.append(f"  公共层 {layer}：{len(hits)} 个变更，需要职责说明与用户决议")
    return lines


def gate_command(host: Host, name: str) -> str:
    for gate in host.policy.get("gates", []) or []:
        if gate.get("name") == name:
            return str(gate.get("command", ""))
    return ""


def boundary_dict(raw: dict[str, Any] | None) -> dict[str, list[str]]:
    raw = raw or {}
    return {k: list(raw.get(k, []) or []) for k in paths.BOUNDARY_KINDS}
