#!/usr/bin/env python3
"""Host discovery and configuration for the DLM pack.

The pack never embeds host paths. Everything host-specific is read from optional
files at the host root; missing files fall back to generic defaults.

- ``dlm.config.json`` (host root, optional)::

      {"knowledge_dir": "docs-llm", "tasks_dir": "docs-llm/Tasks",
       "cache_dir": "out", "locale": "zh-CN",
       "policy": "docs-llm/dlm.policy.json"}

- policy file (optional, see ``DEFAULT_POLICY``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILE = "dlm.config.json"
DEFAULT_KNOWLEDGE_DIR = "docs-llm"
KNOWN_KNOWLEDGE_DIRS = ("docs-llm", "doc_llm", "docs_llm")

DEFAULT_TRIGGERS = [
    "全面", "深度", "完整", "全量", "全部", "所有", "逐项", "一比一", "1:1", "对齐", "匹配",
    "无遗漏", "不要遗漏", "彻底", "无劣化", "完整复审",
    "comprehensive", "exhaustive", "all items", "every", "parity", "one-to-one", "full coverage",
]

DEFAULT_POLICY: dict[str, Any] = {
    # Paths whose modification by a task requires a responsibility justification.
    "public_layers": [],
    # Field names of that justification; each must be filled before close.
    "public_layer_justification": ["phenomenon", "why_here", "files", "how", "risk", "boundary"],
    # [{"name": "...", "paths": ["glob/**"], "command": "how to run (informational)"}]
    "gates": [],
    "inventory_triggers_extra": [],
    "epic": {"max_subtasks": 10, "max_chain_depth": 4, "max_external_epics": 2,
             "max_task_owner_roots": 3, "max_task_obligations": 5,
             "max_task_body_lines": 200, "max_dated_lines": 12},
}


class DlmError(Exception):
    """Deterministic, user-facing failure (exit code 1)."""


class DlmUsageError(Exception):
    """Bad arguments or target (exit code 2)."""


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise DlmUsageError(f"{path}: JSON 无法解析：{exc}") from exc
    if not isinstance(data, dict):
        raise DlmUsageError(f"{path}: 顶层必须是对象")
    return data


def find_root(start: Path | None = None) -> Path:
    env = os.environ.get("DLM_PROJECT_ROOT")
    if env:
        return Path(env).resolve()
    current = (start or Path.cwd()).resolve()
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / CONFIG_FILE).is_file():
            return candidate
    for candidate in candidates:
        if any((candidate / name).is_dir() for name in KNOWN_KNOWLEDGE_DIRS):
            return candidate
    raise DlmUsageError(
        f"未找到宿主根目录：向上没有 {CONFIG_FILE} 或 {'/、'.join(KNOWN_KNOWLEDGE_DIRS)}/；"
        f"可设置 DLM_PROJECT_ROOT 或在宿主根创建空的 {DEFAULT_KNOWLEDGE_DIR}/。"
    )


def resolve_knowledge_dir_name(root: Path, config: dict[str, Any] | None = None) -> str:
    """Resolve the active knowledge directory name for ``root``.

    Priority:
      1. Explicit ``knowledge_dir`` in ``dlm.config.json`` at ``root``.
      2. Explicit ``DLM_DOC_LLM_DIR`` env var when it exists under ``root`` or no known
         directory exists yet under ``root``.
      3. Existing directory on disk among ``KNOWN_KNOWLEDGE_DIRS`` (``docs-llm``, ``doc_llm``, ``docs_llm``).
      4. ``DEFAULT_KNOWLEDGE_DIR`` (``docs-llm``).
    """
    if config is None and (root / CONFIG_FILE).is_file():
        try:
            config = _read_json(root / CONFIG_FILE)
        except Exception:
            config = {}
    if config and config.get("knowledge_dir"):
        return str(config["knowledge_dir"]).replace("\\", "/").strip("/")
    env_dir = os.environ.get("DLM_DOC_LLM_DIR", "").replace("\\", "/").strip("/")
    if env_dir and ((root / env_dir).is_dir() or not any((root / d).is_dir() for d in KNOWN_KNOWLEDGE_DIRS)):
        return env_dir
    for name in KNOWN_KNOWLEDGE_DIRS:
        if (root / name).is_dir():
            return name
    return env_dir or DEFAULT_KNOWLEDGE_DIR


@dataclass
class Host:
    root: Path
    knowledge_dir: Path
    tasks_dir: Path
    cache_dir: Path
    locale: str
    policy: dict[str, Any] = field(default_factory=dict)
    policy_path: Path | None = None

    def rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    @property
    def triggers(self) -> list[str]:
        return DEFAULT_TRIGGERS + list(self.policy.get("inventory_triggers_extra") or [])


def load_host(start: Path | None = None) -> Host:
    root = find_root(start)
    config: dict[str, Any] = {}
    if (root / CONFIG_FILE).is_file():
        config = _read_json(root / CONFIG_FILE)
    kb_name = resolve_knowledge_dir_name(root, config)
    knowledge = root / kb_name
    tasks = root / config.get("tasks_dir", f"{kb_name}/Tasks")
    cache = root / config.get("cache_dir", "out")
    policy_path = root / config["policy"] if config.get("policy") else knowledge / "dlm.policy.json"
    policy = dict(DEFAULT_POLICY)
    if policy_path.is_file():
        policy = _merge(DEFAULT_POLICY, _read_json(policy_path))
    else:
        policy_path = None
    return Host(
        root=root,
        knowledge_dir=knowledge,
        tasks_dir=tasks,
        cache_dir=cache,
        locale=str(config.get("locale", "zh-CN")),
        policy=policy,
        policy_path=policy_path,
    )
