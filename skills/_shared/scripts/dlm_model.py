#!/usr/bin/env python3
"""Ledger storage, identity allocation and reference resolution.

The ledger (``ledger.json``) is the only machine source of truth for a v2 Epic.
All mutating commands go through ``Ledger`` and ``dlm_invariants`` before saving.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any, Iterable

from dlm_config import DlmError, DlmUsageError, Host

SCHEMA = "dlm.ledger/1"
LEDGER = "ledger.json"
JOURNAL = "journal.md"
EPIC_DOC = "epic.md"
README = "README.md"

COLLECTIONS = {
    "P": "principles",
    "D": "decisions",
    "O": "obligations",
    "N": "populations",
    "A": "artifacts",
    "H": "hypotheses",
    "L": "limits",
    "V": "evidence",
    "J": "justifications",
}
TASK_PREFIX = "S"

EPIC_STATUS = ("active", "paused", "done")
TASK_STATUS = ("draft", "active", "blocked", "done", "dropped")
TASK_KINDS = ("deliver", "investigation")
DEPTHS = ("Ultra", "Max", "High", "Medium", "Lite")
DECISION_SOURCES = ("user", "executor")
DECISION_KINDS = ("principle", "scope", "waive", "supersede", "gate", "limit", "sample",
                  "requirement", "direction", "other")
OBLIGATION_STATUS = ("open", "transferred", "waived", "superseded")
COVERAGE = ("single", "full", "sample")
ARTIFACT_STATES = ("planned", "contract", "implemented", "verified")
HYPOTHESIS_STATUS = ("unverified", "confirmed", "refuted")
LIMIT_KINDS = ("product", "budget", "resource-fact")
ON_EXCEED = ("chunk", "degrade-with-record", "unmet", "reject")
EVIDENCE_SCOPES = ("full", "partial", "sample", "proxy")
EVIDENCE_STATUS = ("valid", "invalidated", "overclaimed", "unverifiable")
ITEM_STATUS = ("pending", "pass", "fail", "gap", "na", "blocked")
RELATION_KINDS = ("verifies", "artifact")

REF_RE = re.compile(r"^(?:(E\d{2,})/)?([A-Z])(\d+)(?:\.(\d+))?$")
TASK_REF_RE = re.compile(r"^(?:(E\d{2,})/)?(S\d{2,})$")
EPIC_RE = re.compile(r"^E\d{2,}$")


def today() -> str:
    return _dt.date.today().isoformat()


def now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def slugify(title: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "_", title).strip("_").lower()
    return slug[:48] or "epic"


def split_list(values: Iterable[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        for part in re.split(r"[,，\s]+", value or ""):
            if part:
                result.append(part)
    return result


def epic_dirs(host: Host) -> list[Path]:
    if not host.tasks_dir.is_dir():
        return []
    return sorted(p for p in host.tasks_dir.iterdir() if p.is_dir() and re.match(r"^E\d{2,}", p.name))


def find_epic_dir(host: Host, ref: str) -> Path:
    candidate = Path(ref)
    if not candidate.is_absolute():
        candidate = (Path.cwd() / ref)
    if candidate.is_dir() and (candidate / LEDGER).is_file():
        return candidate.resolve()
    if candidate.is_file() and candidate.name == LEDGER:
        return candidate.parent.resolve()
    match = re.match(r"^(E\d{2,})", Path(ref).name)
    if not match:
        raise DlmUsageError(f"无法识别 Epic：{ref}")
    hits = [p for p in epic_dirs(host) if re.match(rf"^{match.group(1)}(?:_|$)", p.name)]
    if len(hits) != 1:
        raise DlmUsageError(f"Epic {match.group(1)} 定位结果 {len(hits)} 个，需唯一")
    if not (hits[0] / LEDGER).is_file():
        raise DlmUsageError(f"{hits[0].name} 是历史（v1）记录：用 dlm legacy check 只读检查，续接需要持久化时 dlm adopt")
    return hits[0]


def next_epic_id(host: Host) -> str:
    matches = [re.match(r"^E(\d+)", p.name).group(1) for p in epic_dirs(host)]
    numbers = [int(m) for m in matches]
    # Follow the host's existing width (E01 or E001); default to three digits.
    width = max((len(m) for m in matches), default=3)
    return f"E{(max(numbers) + 1) if numbers else 1:0{width}d}"


class Ledger:
    def __init__(self, host: Host, epic_dir: Path, data: dict[str, Any]):
        self.host = host
        self.dir = epic_dir
        self.data = data
        self._original = copy.deepcopy(data)
        self._journal: list[str] = []

    # ---------------------------------------------------------------- storage
    @classmethod
    def load(cls, host: Host, ref: str | Path) -> "Ledger":
        epic_dir = find_epic_dir(host, str(ref))
        path = epic_dir / LEDGER
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise DlmError(f"{host.rel(path)}: JSON 无法解析：{exc}") from exc
        if data.get("schema") != SCHEMA:
            raise DlmError(f"{host.rel(path)}: 不支持的 schema {data.get('schema')!r}（需要 {SCHEMA}）")
        return cls(host, epic_dir, data)

    @classmethod
    def new(cls, host: Host, epic_dir: Path, epic: dict[str, Any]) -> "Ledger":
        data: dict[str, Any] = {"schema": SCHEMA, "epic": epic,
                                "boundary": {k: [] for k in ("write", "generated", "read_only", "forbidden")},
                                "tasks": []}
        for name in COLLECTIONS.values():
            data[name] = []
        return cls(host, epic_dir, data)

    @property
    def epic(self) -> dict[str, Any]:
        return self.data["epic"]

    @property
    def epic_id(self) -> str:
        return self.epic["id"]

    def changed(self) -> bool:
        return self.data != self._original

    def save(self, dry_run: bool = False) -> None:
        if dry_run:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        (self.dir / LEDGER).write_text(text, encoding="utf-8", newline="\n")
        if self._journal:
            path = self.dir / JOURNAL
            head = "" if path.exists() else f"# {self.epic_id} journal\n\n"
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(head + "".join(f"- {line}\n" for line in self._journal))
            self._journal.clear()
        self._original = copy.deepcopy(self.data)

    def log(self, command: str, text: str) -> None:
        self._journal.append(f"{now()} `{command}` {text}")

    # --------------------------------------------------------------- identity
    def collection(self, prefix: str) -> list[dict[str, Any]]:
        if prefix == TASK_PREFIX:
            return self.data["tasks"]
        return self.data.setdefault(COLLECTIONS[prefix], [])

    def next_id(self, prefix: str) -> str:
        digits = [re.sub(r"\D", "", item["id"]) for item in self.collection(prefix)]
        numbers = [int(d or 0) for d in digits]
        number = (max(numbers) + 1) if numbers else 1
        if prefix == TASK_PREFIX:
            width = max((len(d) for d in digits), default=3)
            return f"S{number:0{width}d}"
        return f"{prefix}{number}"

    def add(self, prefix: str, entity: dict[str, Any]) -> dict[str, Any]:
        entity = {"id": self.next_id(prefix), **entity}
        self.collection(prefix).append(entity)
        return entity

    def get(self, ref: str, prefix: str | None = None) -> dict[str, Any]:
        found = self.find(ref, prefix)
        if found is None:
            raise DlmError(f"{self.epic_id}: 引用 {ref} 不存在")
        return found

    def find(self, ref: str, prefix: str | None = None) -> dict[str, Any] | None:
        ref = ref.strip()
        if TASK_REF_RE.match(ref):
            epic, task_id = TASK_REF_RE.match(ref).groups()
            if epic and epic != self.epic_id:
                return None
            return next((t for t in self.data["tasks"] if t["id"] == task_id), None)
        match = REF_RE.match(ref)
        if not match or (prefix and match.group(2) != prefix):
            return None
        epic, letter, number, item = match.groups()
        if epic and epic != self.epic_id:
            return None
        if letter not in COLLECTIONS:
            return None
        entity = next((e for e in self.collection(letter) if e["id"] == f"{letter}{number}"), None)
        if entity is not None and item is not None:
            return next((i for i in entity.get("items", []) if i["id"] == ref.split("/")[-1]), None)
        return entity

    def task(self, ref: str) -> dict[str, Any]:
        task = self.find(ref)
        if task is None or not task["id"].startswith(TASK_PREFIX):
            raise DlmError(f"{self.epic_id}: 任务 {ref} 不存在")
        return task


def resolve_external(host: Host, ref: str, current: Ledger) -> tuple[Ledger, dict[str, Any]] | None:
    """Resolve ``E012/A3`` style references, loading other ledgers when needed."""
    match = REF_RE.match(ref) or TASK_REF_RE.match(ref)
    if not match:
        return None
    epic = match.group(1)
    if not epic or epic == current.epic_id:
        found = current.find(ref)
        return (current, found) if found is not None else None
    try:
        other = Ledger.load(host, epic)
    except (DlmError, DlmUsageError):
        return None
    found = other.find(ref)
    return (other, found) if found is not None else None
