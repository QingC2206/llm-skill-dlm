#!/usr/bin/env python3
"""Knowledge-base tooling behind ``dlm kb`` and the read-only ``dlm legacy`` report.

Structure validation, route generation, scaffolding and size/overlap signals reuse
the established modules; stale detection and sediment candidates are computed here.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import dlm_paths as paths
from dlm_config import DlmUsageError, Host

SCRIPT_DIR = Path(__file__).resolve().parent
KB_DIRS = ("00_Global", "Domains", "SOPs")


def _env(host: Host) -> dict[str, str]:
    env = dict(os.environ)
    env["DLM_PROJECT_ROOT"] = str(host.root)
    env["DLM_DOC_LLM_DIR"] = host.rel(host.knowledge_dir)
    env["DLM_SKILL_ENTRYPOINT"] = "dlm kb"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run(host: Host, script: str, argv: list[str]) -> int:
    result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT_DIR / script), *argv],
                            cwd=host.root, env=_env(host), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def run(host: Host, args: Any) -> int:
    action, rest = args.kb_action, list(args.rest or [])
    dry_run = bool(getattr(args, "dry_run", False))
    if action == "check":
        flags = [r for r in rest if r.startswith("-")]
        targets = [r for r in rest if not r.startswith("-")]
        if "--tasks" in flags:
            raise DlmUsageError("任务记录不由 kb check 检查：v2 用 dlm check，历史记录用 dlm legacy check")
        if not targets and not flags:
            targets = [host.rel(host.knowledge_dir / d) for d in KB_DIRS if (host.knowledge_dir / d).exists()]
        for target in targets:
            path = (host.root / target) if not Path(target).is_absolute() else Path(target)
            if _inside(path, host.tasks_dir):
                raise DlmUsageError("任务记录不由 kb check 检查：v2 用 dlm check，历史记录用 dlm legacy check")
        if not targets:
            return _run(host, "validate_doc_llm.py", flags)
        return max((_run(host, "validate_doc_llm.py", [*flags, target]) for target in targets), default=0)
    if action == "route":
        flags = [r for r in rest if r.startswith("-")]
        targets = [r for r in rest if not r.startswith("-")]
        if dry_run and "--preview" not in flags:
            flags.append("--preview")
        if any(f in {"-h", "--help"} for f in flags):
            return _run(host, "sync_l1_route.py", ["--help"])
        if "--all" in flags:
            return _run(host, "sync_l1_route.py", flags)
        if not targets:
            raise DlmUsageError("dlm kb route <domain_dir> [...] [--preview] | --all [--preview]")
        return max(_run(host, "sync_l1_route.py", [target, *flags]) for target in targets)
    if action == "scaffold":
        top_flags = [r for r in rest if r in {"--dry-run", "--force", "--json"}]
        sub_args = [r for r in rest if r not in {"--dry-run", "--force", "--json"}]
        if dry_run and "--dry-run" not in top_flags:
            top_flags.insert(0, "--dry-run")
        return _run(host, "doc_llm_scaffold_tool.py", [*top_flags, *sub_args])
    if action == "map":
        return _run(host, "l2_audit_map.py", rest)
    if action == "stale":
        return stale(host, rest)
    if action == "sediment":
        changed = rest
        if not changed:
            import dlm_scope
            ok, changed, reason = dlm_scope.git_changes(host.root)
            if not ok:
                print(f"[UNKNOWN] {reason}")
                return 1
        for line in sediment_candidates(host, changed) or ["没有命中的知识文档"]:
            print(line)
        return 0
    raise DlmUsageError(f"未知 kb 动作 {action}")


def legacy_check(host: Host, target: str, *, strict: bool) -> int:
    code = _run(host, "validate_doc_llm.py", [target])
    if code and not strict:
        print("\n[NOTE] 历史（v1）记录只读报告：不阻断，不批量迁移；续接时用 dlm adopt 导入。")
        return 0
    return code


# ------------------------------------------------------------ front-matter
def front_matter(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    data: dict[str, Any] = {}
    key = None
    for line in text[3:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and key:
            data.setdefault(key, [])
            if isinstance(data[key], list):
                data[key].append(item.group(1).strip().strip("'\""))
            continue
        match = re.match(r"^([\w-]+):\s*(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        elif value:
            data[key] = value.strip("'\"")
        else:
            data[key] = []
    return data


def knowledge_docs(host: Host) -> list[tuple[Path, dict[str, Any]]]:
    docs = []
    patterns = ("Domains/*/L2_Modules/*.md", "Domains/*/references/*/README.md", "SOPs/*/README.md")
    for pattern in patterns:
        for path in sorted(host.knowledge_dir.glob(pattern)):
            meta = front_matter(path)
            if meta and meta.get("match_globs"):
                docs.append((path, meta))
    return docs


def sediment_candidates(host: Host, changed: list[str]) -> list[str]:
    lines = []
    for path, meta in knowledge_docs(host):
        if str(meta.get("status", "")).lower() in {"deprecated", "已废弃"}:
            continue
        globs = meta.get("match_globs") or []
        hits = [c for c in changed if any(paths.matches(c, g) for g in globs)]
        if hits:
            lines.append(f"{host.rel(path)} ← {len(hits)} 个变更（例：{hits[0]}）")
    return lines


# ------------------------------------------------------------ stale
def _repo_for(root: Path, prefix: str) -> Path:
    current = (root / prefix) if prefix else root
    while not current.exists() and current != root:
        current = current.parent
    if current.is_file():
        current = current.parent
    while True:
        if (current / ".git").exists():
            return current
        if current == root or current.parent == current:
            return root
        current = current.parent


def last_change(host: Host, globs: list[str]) -> tuple[str | None, str]:
    dates = []
    for glob in globs:
        prefix = paths.literal_prefix(paths.norm(glob)).rsplit("/", 1)[0] if "/" in glob else ""
        repo = _repo_for(host.root, prefix)
        rel = paths.norm(glob)
        repo_rel = repo.relative_to(host.root).as_posix() if repo != host.root else ""
        if repo_rel and rel.startswith(repo_rel + "/"):
            rel = rel[len(repo_rel) + 1:]
        try:
            result = subprocess.run(["git", "log", "-1", "--format=%cs", "--", f":(glob){rel}"], cwd=repo,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            return None, str(exc)
        if result.returncode != 0:
            return None, (result.stderr.strip() or "git log 失败")[:200]
        if result.stdout.strip():
            dates.append(result.stdout.strip())
    return (max(dates) if dates else ""), ""


def stale(host: Host, rest: list[str]) -> int:
    domain_filter = rest[0] if rest else None
    counts = {"stale": 0, "ok": 0, "unknown": 0}
    for path, meta in knowledge_docs(host):
        if domain_filter and domain_filter not in host.rel(path):
            continue
        updated = str(meta.get("last_updated", "")).strip()
        date, error = last_change(host, list(meta.get("match_globs") or []))
        if date is None or not re.match(r"^\d{4}-\d{2}-\d{2}", updated):
            counts["unknown"] += 1
            print(f"[UNKNOWN] {host.rel(path)}：{error or 'last_updated 缺失或格式不明'}")
        elif date and date > updated[:10]:
            counts["stale"] += 1
            print(f"[STALE] {host.rel(path)}：源码最近变更 {date} 晚于 last_updated {updated[:10]}")
        else:
            counts["ok"] += 1
    print(f"候选：stale {counts['stale']} / ok {counts['ok']} / unknown {counts['unknown']}"
          "（只是候选，是否修正按内容判断）")
    return 0
