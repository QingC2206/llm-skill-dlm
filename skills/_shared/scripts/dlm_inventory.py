#!/usr/bin/env python3
"""Inventory (full object set) support: enumerate from sources of truth, track
each item to a terminal status, compute parity differences and counts.

Standalone inventories live in ``<cache_dir>/dlm/inventory/<name>.json``;
Epic inventories are ledger populations (``N``) referenced by obligations.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from dlm_config import DlmError, DlmUsageError, Host
from dlm_model import ITEM_STATUS, Ledger, now, today
from dlm_status import population_closed, population_counts


class Store:
    """A population plus how to persist it."""

    def __init__(self, population: dict[str, Any], prefix: str, save: Callable[[], None], label: str,
                 ledger: Ledger | None = None):
        self.population = population
        self.prefix = prefix
        self._save = save
        self.label = label
        self.ledger = ledger

    def save(self) -> None:
        self._save()

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.population.setdefault("items", [])

    def next_item_id(self) -> str:
        numbers = [int(i["id"].rsplit(".", 1)[-1]) for i in self.items]
        return f"{self.prefix}.{(max(numbers) + 1) if numbers else 1}"

    def resolve_ids(self, raw: list[str]) -> list[dict[str, Any]]:
        by_id = {i["id"]: i for i in self.items}
        result = []
        for value in raw:
            key = value if "." in value else f"{self.prefix}.{value}"
            if key not in by_id:
                raise DlmError(f"{self.label}: 条目 {value} 不存在")
            result.append(by_id[key])
        return result


def standalone_path(host: Host, name: str) -> Path:
    if not re.match(r"^[\w.-]+$", name):
        raise DlmUsageError(f"盘点名只能包含字母、数字、下划线、点和连字符：{name}")
    return host.cache_dir / "dlm" / "inventory" / f"{name}.json"


def open_store(host: Host, name: str, epic: str | None) -> Store:
    if epic:
        ledger = Ledger.load(host, epic)
        population = ledger.find(name, "N") or next(
            (n for n in ledger.collection("N") if n.get("name") == name), None)
        if population is None:
            raise DlmError(f"{ledger.epic_id}: 全集 {name} 不存在")
        return Store(population, population["id"], ledger.save, f"{ledger.epic_id}/{population['id']}", ledger)
    path = standalone_path(host, name)
    if not path.is_file():
        raise DlmError(f"盘点 {name} 不存在：{host.rel(path)}")
    population = json.loads(path.read_text(encoding="utf-8-sig"))

    def save() -> None:
        path.write_text(json.dumps(population, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    return Store(population, "I", save, name)


def create(host: Host, name: str, reference: str, target: str | None, epic: str | None,
           items: list[str], sample_quote: str | None, sample_decision: str | None) -> Store:
    population: dict[str, Any] = {"name": name, "reference": reference, "target": target or "",
                                  "created": today(), "sources": [], "items": []}
    if epic:
        ledger = Ledger.load(host, epic)
        if any(n.get("name") == name for n in ledger.collection("N")):
            raise DlmError(f"{ledger.epic_id}: 全集名 {name} 已存在")
        if sample_decision:
            population["sample"] = {"decision": sample_decision}
        population = ledger.add("N", population)
        store = Store(population, population["id"], ledger.save, f"{ledger.epic_id}/{population['id']}", ledger)
        ledger.log("inv new", f"{population['id']} {name}：{reference}")
    else:
        path = standalone_path(host, name)
        if path.exists():
            raise DlmError(f"盘点 {name} 已存在：{host.rel(path)}")
        if sample_quote:
            population["sample"] = {"quote": sample_quote}
        path.parent.mkdir(parents=True, exist_ok=True)

        def save() -> None:
            path.write_text(json.dumps(population, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                            newline="\n")

        store = Store(population, "I", save, name)
    if items:
        add_keys(store, "ref", items, manual=False, source_index=None)
        store.population["sources"].append({"side": "ref", "kind": "list", "spec": "--items", "count": len(items),
                                            "date": today()})
    return store


def add_keys(store: Store, side: str, keys: list[str], *, manual: bool, source_index: int | None,
             reason: str = "") -> tuple[int, int]:
    by_key = {i["key"]: i for i in store.items}
    added = merged = 0
    for key in keys:
        key = key.strip()
        if not key:
            continue
        existing = by_key.get(key)
        if existing:
            if existing.get("side") not in {side, "both"} and side in {"ref", "target"}:
                existing["side"] = "both"
                merged += 1
            continue
        item = {"id": store.next_item_id(), "key": key, "side": side, "status": "pending", "evidence": []}
        if manual:
            item["manual"] = True
            item["reason"] = reason
        if source_index is not None:
            item["source"] = source_index
        store.items.append(item)
        by_key[key] = item
        added += 1
    return added, merged


def _apply_key(values: list[str], key_regex: str | None) -> list[str]:
    if not key_regex:
        return values
    pattern = re.compile(key_regex)
    keys = []
    for value in values:
        match = pattern.search(value)
        if match:
            keys.append(match.group(1) if match.groups() else match.group(0))
    return keys


def enumerate_source(host: Host, kind: str, spec: str, within: str | None, key_regex: str | None) -> list[str]:
    root = host.root
    if kind == "glob":
        values = sorted(p.relative_to(root).as_posix() for p in root.glob(spec) if p.is_file())
    elif kind == "grep":
        if not within:
            raise DlmUsageError("grep 来源需要 --in <glob> 指定检索文件")
        pattern = re.compile(spec)
        values = []
        for path in sorted(root.glob(within)):
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
            except OSError:
                continue
            rel = path.relative_to(root).as_posix()
            for number, line in enumerate(lines, 1):
                match = pattern.search(line)
                if match:
                    values.append(match.group(1) if match.groups() else f"{rel}:{number}")
        key_regex = key_regex if key_regex else None
    elif kind == "cmd":
        result = subprocess.run(spec, shell=True, cwd=root, capture_output=True, text=True,
                                encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise DlmError(f"来源命令失败（{result.returncode}）：{result.stderr.strip()[:300]}")
        values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    elif kind == "list":
        if spec.startswith("@"):
            values = [line.strip() for line in (root / spec[1:]).read_text(encoding="utf-8-sig").splitlines()]
        else:
            values = [part.strip() for part in re.split(r"[,\n]", spec)]
        values = [v for v in values if v]
    else:
        raise DlmUsageError(f"未知来源类型 {kind}")
    keys = _apply_key(values, key_regex)
    # stable de-duplication
    return list(dict.fromkeys(keys))


def add_source(host: Host, store: Store, side: str, kind: str, spec: str, within: str | None,
               key_regex: str | None) -> tuple[int, int, int]:
    keys = enumerate_source(host, kind, spec, within, key_regex)
    index = len(store.population.setdefault("sources", []))
    store.population["sources"].append({"side": side, "kind": kind, "spec": spec, "in": within or "",
                                        "key": key_regex or "", "count": len(keys), "date": today()})
    added, merged = add_keys(store, side, keys, manual=False, source_index=index)
    if store.population.get("target") and side == "target":
        store.population["parity"] = True
    return len(keys), added, merged


def diff(store: Store) -> Counter:
    counts: Counter = Counter()
    if not any(s.get("side") == "target" for s in store.population.get("sources", [])):
        raise DlmError(f"{store.label}: 对齐差集需要分别枚举 ref 与 target 两侧来源")
    store.population["parity"] = True
    for item in store.items:
        side = item.get("side")
        counts[side] += 1
        if item.get("status") != "pending":
            continue
        if side == "ref":
            item["status"] = "gap"
            item["reason"] = "目标侧缺失"
        elif side == "target":
            item["status"] = "na"
            item["reason"] = "仅目标侧存在（多出项，记录）"
    return counts


def mark(store: Store, items: list[dict[str, Any]], status: str, evidence: str | None, reason: str | None,
         decision: str | None = None) -> None:
    if status not in ITEM_STATUS:
        raise DlmUsageError(f"状态必须是 {'/'.join(ITEM_STATUS)}")
    if status == "pass" and not evidence:
        raise DlmError("pass 必须提供 --evidence（覆盖这些条目的证据定位）")
    if status == "na" and not reason:
        raise DlmError("na 必须提供 --reason")
    stamp = now()
    for item in items:
        item["status"] = status
        if evidence:
            item.setdefault("evidence", []).append(evidence)
        if reason:
            item["reason"] = reason
        if decision:
            item["decision"] = decision
        item["updated"] = stamp


def report_lines(store: Store, limit: int = 40) -> tuple[list[str], bool]:
    population = store.population
    counts = population_counts(population)
    closed = population_closed(population)
    head = (f"{store.label}：参照「{population.get('reference', '')}」"
            + (f" → 目标「{population['target']}」" if population.get("target") else ""))
    lines = [head,
             "计数：总数 {total} / pass {pass_} / fail {fail} / gap {gap} / na {na} / blocked {blocked} / pending {pending}".format(
                 total=counts["total"], pass_=counts["pass"], fail=counts["fail"], gap=counts["gap"],
                 na=counts["na"], blocked=counts["blocked"], pending=counts["pending"])]
    manual = [i for i in store.items if i.get("manual")]
    if manual:
        lines.append(f"手工补充条目 {len(manual)} 个（来源未覆盖，复审重点）")
    for source in population.get("sources", []):
        lines.append(f"来源[{source.get('side')}] {source.get('kind')} {source.get('spec')} {source.get('in', '')} → {source.get('count')}")
    if population.get("sample"):
        lines.append("抽检（用户决议）：结论只适用于已检条目，不代表全集")
    open_items = [i for i in store.items if i.get("status") in {"pending", "fail", "gap", "blocked"}]
    for item in open_items[:limit]:
        lines.append(f"  {item['id']} [{item['status']}] {item['key']}" + (f" — {item['reason']}" if item.get("reason") else ""))
    if len(open_items) > limit:
        lines.append(f"  … 另有 {len(open_items) - limit} 个未闭合条目")
    # na closes an item, so every na must stay visible with its reason for review.
    na_items = [i for i in store.items if i.get("status") == "na" and i.get("side") != "target"]
    for item in na_items[:limit]:
        lines.append(f"  {item['id']} [na] {item['key']} — {item.get('reason', '')}"
                     + (f"（{item['decision']}）" if item.get("decision") else ""))
    if len(na_items) > limit:
        lines.append(f"  … 另有 {len(na_items) - limit} 个 na 条目")
    if counts["total"] == 0:
        lines.append("结论：全集为空——未知，不能宣称完成")
    elif closed:
        lines.append("结论：已逐项闭合" if not population.get("sample") else "结论：抽检条目无失败")
    else:
        lines.append("结论：未闭合——不得宣称完成或已对齐")
    return lines, closed


# ------------------------------------------- dlm-multi-llm-lease bridge
LEASE_COLUMNS = ("编号", "模块", "优先级", "备注", "状态", "执行者", "开始时间", "完成时间", "运行备注")
LEASE_HEADER_NOTE = [
    "> 本表只做展示；活跃领取状态的唯一事实源是 `<claims_dir>/*.json`。",
    "> 用户可以启动一个或多个独立顶层任务；每个任务由宿主自动提供唯一身份，只串行领取下一可用项。",
    "> 禁止手工修改运行状态、填写 worker、创建内部并行执行者或在本队列中加入统一收口项。",
    "> item 默认按优先级（`优先级` 列 / `priority_order`）与行序自动分配；只有用户明确授权并写入 canonical policy 的 exact item 才能点名领取。",
    "> `备注` 列用于承载用户或上级任务给出的模块特定前置说明（只读传入 worker）；运行时状态与完成说明写入 `运行备注` 列。",
    "> queue empty/closed 后 worker 直接停止；共享聚合与最终验收由后续独立 Subtask 负责。",
]


def _split_md_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    parts = re.split(r"(?<!\\)\|", stripped)
    return [p.replace(r"\|", "|").strip() for p in parts]


def _escape_md_cell(value: str) -> str:
    return (value or "").replace("\r\n", " ").replace("\n", " ").replace("|", r"\|").strip()


def _parse_work_items_md(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    headers: list[str] | None = None
    rows: list[dict[str, str]] = []
    for i, line in enumerate(lines):
        if "|" not in line:
            continue
        if headers is None:
            if i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", lines[i + 1]):
                headers = _split_md_row(line)
            continue
        if re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", line):
            continue
        cells = _split_md_row(line)
        if not cells or all(not c for c in cells):
            continue
        row = {headers[idx]: (cells[idx] if idx < len(cells) else "") for idx in range(len(headers))}
        rows.append(row)
    return rows


def _normalize_lease_state(raw: str) -> str:
    token = (raw or "").strip().lower()
    if token in {"✅", "done", "completed", "pass"}:
        return "done"
    if token in {"❌", "blocked", "failed", "fail"}:
        return "blocked"
    if token in {"🔄", "active", "in_progress", "claiming", "claimed"}:
        return "active"
    return "open"


def _priority_rank(raw_priority: str, tier_order: list[str]) -> int:
    if not tier_order:
        return 0
    val = (raw_priority or "").strip()
    if not val or val == "—":
        return len(tier_order)
    low = val.lower()
    for idx, tier in enumerate(tier_order):
        t = tier.strip().lower()
        if not t:
            continue
        if low == t or low.startswith(f"{t}-") or low.startswith(f"{t}_") or low.startswith(f"{t}:"):
            return idx
        if len(t) == 1 and low.startswith(t) and (len(low) == 1 or not low[1].isalnum() or low[1:].startswith("系列")):
            return idx
    return len(tier_order)


def _resolve_lease_paths(
    host: Host,
    store: Store,
    work_items_path: str | None,
    runtime_path: str | None,
    *,
    no_runtime: bool = False,
) -> tuple[Path, Path | None]:
    rt_path: Path | None = None
    if not no_runtime and runtime_path:
        rt_path = Path(runtime_path) if Path(runtime_path).is_absolute() else (host.root / runtime_path)
    if work_items_path:
        wi_path = Path(work_items_path) if Path(work_items_path).is_absolute() else (host.root / work_items_path)
    elif rt_path and rt_path.is_file():
        rt_data = json.loads(rt_path.read_text(encoding="utf-8-sig"))
        rel_wi = rt_data.get("work_items_path")
        wi_path = (host.root / rel_wi) if rel_wi else rt_path.with_name("work_items.md")
    elif store.ledger is not None:
        wi_path = store.ledger.dir / "references" / "work_items.md"
    else:
        wi_path = host.cache_dir / "dlm" / "inventory" / f"{store.label}_work_items.md"
    if not no_runtime and rt_path is None:
        rt_path = wi_path.with_name("work_item_runtime.json")
    return wi_path.resolve(), (rt_path.resolve() if rt_path is not None else None)


def export_lease(
    host: Host,
    store: Store,
    *,
    work_items_path: str | None = None,
    runtime_path: str | None = None,
    no_runtime: bool = False,
    claims_dir: str | None = None,
    task_id: str | None = None,
    sop_ref: str | None = None,
    priority_order: list[str] | None = None,
    priority_rules: list[str] | None = None,
    default_priority: str = "—",
    remarks: list[str] | None = None,
    remarks_json: str | None = None,
    include_na: bool = False,
    reset_state: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    wi_path, rt_path = _resolve_lease_paths(host, store, work_items_path, runtime_path, no_runtime=no_runtime)
    parsed_rules: list[tuple[str, re.Pattern[str]]] = []
    for raw in priority_rules or []:
        tier, sep, pat = raw.partition("=")
        if not sep or not tier.strip() or not pat.strip():
            raise DlmUsageError(f"--priority-rule 需要 TIER=REGEX：{raw}")
        parsed_rules.append((tier.strip(), re.compile(pat.strip())))

    tier_order = [p.strip() for p in (priority_order or []) if p.strip()]
    if not tier_order and parsed_rules:
        tier_order = list(dict.fromkeys(t for t, _ in parsed_rules))

    remark_map: dict[str, str] = {}
    if remarks_json:
        rj_path = Path(remarks_json) if Path(remarks_json).is_absolute() else (host.root / remarks_json)
        data = json.loads(rj_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise DlmUsageError("--remarks-json 文件必须是 {模块或编号: 备注} 对象")
        remark_map.update({str(k).strip(): str(v).strip() for k, v in data.items()})
    for raw in remarks or []:
        key, sep, val = raw.partition("=")
        if not sep or not key.strip():
            raise DlmUsageError(f"--remark 需要 KEY=REMARK：{raw}")
        remark_map[key.strip()] = val.strip()

    existing_rows = _parse_work_items_md(wi_path)
    by_module = {r.get("模块", "").strip(): r for r in existing_rows if r.get("模块", "").strip()}
    by_wid = {r.get("编号", "").strip(): r for r in existing_rows if r.get("编号", "").strip()}
    used_nums: set[int] = set()
    for wid in list(by_wid) + [str(i.get("work_item_id", "")) for i in store.items]:
        m = re.match(r"^W(\d+)$", wid.strip(), re.IGNORECASE)
        if m:
            used_nums.add(int(m.group(1)))

    def next_wid() -> str:
        n = (max(used_nums) + 1) if used_nums else 1
        used_nums.add(n)
        return f"W{n:03d}"

    built_rows: list[tuple[int, int, dict[str, str]]] = []
    for idx, item in enumerate(store.items):
        if item.get("status") == "na" and not include_na:
            continue
        key = item["key"]
        prev = by_module.get(key) or by_wid.get(str(item.get("work_item_id", "")))
        wid = (prev.get("编号", "").strip() if prev else "") or str(item.get("work_item_id", "")).strip() or next_wid()
        item["work_item_id"] = wid

        matched_tier = next((t for t, rx in parsed_rules if rx.search(key)), None)
        prev_prio = (prev.get("优先级", "").strip() if prev else "")
        prio = (
            matched_tier
            or str(item.get("priority", "")).strip()
            or (prev_prio if prev_prio and prev_prio != "—" else "")
            or default_priority
        )
        if prio and prio != "—":
            item["priority"] = prio

        prev_remark = (prev.get("备注", "").strip() if prev else "")
        if prev_remark == "—":
            prev_remark = ""
        if key in remark_map:
            rem = remark_map[key]
        elif wid in remark_map:
            rem = remark_map[wid]
        elif "remark" in item and str(item.get("remark", "")).strip():
            rem = str(item.get("remark", "")).strip()
        else:
            rem = prev_remark
        if rem:
            item["remark"] = rem
        else:
            item.pop("remark", None)

        if prev and not reset_state:
            state = prev.get("状态", "⬜").strip() or "⬜"
            worker = prev.get("执行者", "—").strip() or "—"
            started = prev.get("开始时间", "—").strip() or "—"
            completed = prev.get("完成时间", "—").strip() or "—"
            note = prev.get("运行备注", prev.get("备注" if "运行备注" not in prev else "", "—")).strip() or "—"
        else:
            st = item.get("status", "pending")
            if st == "pass":
                state = "✅"
            elif st in {"blocked", "fail"}:
                state = "❌"
            else:
                state = "⬜"
            worker = "—"
            started = "—"
            completed = "—"
            note = (str(item.get("reason", "")).strip() or "—") if state in {"✅", "❌"} else "—"

        row_dict = {
            "编号": wid,
            "模块": key,
            "优先级": prio or "—",
            "备注": rem,
            "状态": state,
            "执行者": worker,
            "开始时间": started,
            "完成时间": completed,
            "运行备注": note,
        }
        built_rows.append((_priority_rank(prio, tier_order), idx, row_dict))

    if tier_order:
        built_rows.sort(key=lambda t: (t[0], t[1]))

    md_lines = [
        f"# {store.label} Work Items",
        "",
        *LEASE_HEADER_NOTE,
        "",
        "| " + " | ".join(LEASE_COLUMNS) + " |",
        "| " + " | ".join("---" for _ in LEASE_COLUMNS) + " |",
    ]
    for _, _, row in built_rows:
        cells = [_escape_md_cell(row[col]) for col in LEASE_COLUMNS]
        md_lines.append("| " + " | ".join(cells) + " |")
    md_text = "\n".join(md_lines) + "\n"

    rt_rel = ""
    if rt_path is not None:
        epic_scope = (
            f"{store.ledger.epic_id}/{task_id}"
            if (store.ledger is not None and task_id)
            else (f"{store.ledger.epic_id}/{store.population['id']}" if store.ledger is not None else store.label)
        )
        default_claims = claims_dir or f"{host.rel(host.cache_dir)}/work_item_claims/{epic_scope.replace('/', '_')}"
        rt_data: dict[str, Any] = {}
        if rt_path.is_file():
            try:
                rt_data = json.loads(rt_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                rt_data = {}
        rt_data.setdefault("epic_id", epic_scope)
        if task_id and store.ledger is not None:
            rt_data["epic_id"] = f"{store.ledger.epic_id}/{task_id}"
        rt_data.setdefault("label", store.population.get("reference") or store.label)
        rt_data["path_base"] = "project_root"
        rt_data["work_items_path"] = host.rel(wi_path)
        if claims_dir or "claims_dir" not in rt_data:
            rt_data["claims_dir"] = default_claims
        if sop_ref:
            rt_data["sop_ref"] = sop_ref
        if tier_order:
            rt_data["priority_order"] = tier_order
        rt_data.setdefault("lease_seconds", 1800)
        rt_data.setdefault("lock_timeout_seconds", 120)
        rt_data.setdefault("lock_stale_seconds", 600)
        rt_data.setdefault("worker_env_var", "CODEX_THREAD_ID")
        rt_data.setdefault(
            "orchestration_policy",
            {
                "schema_version": 1,
                "profile": "user_started_top_level_tasks",
                "parallelism_authority": "user",
                "peer_awareness": "self_only",
                "identity_source": "host_task_environment",
                "worker_env_var": "CODEX_THREAD_ID",
                "session_env_var": "CODEX_SESSION_ID",
                "require_equal_identity": True,
                "assignment_mode": "next_available_only",
                "max_active_claims_per_worker": 1,
                "worker_loop": "serial_claim_complete_repeat",
                "closure_mode": "followup_subtask_only",
            },
        )
        rt_data["column_map"] = {
            "id": "编号",
            "module": "模块",
            "priority": "优先级",
            "remark": "备注",
            "state": "状态",
            "worker": "执行者",
            "started_at": "开始时间",
            "completed_at": "完成时间",
            "note": "运行备注",
        }
        rt_data.setdefault(
            "note_messages",
            {
                "lease_expired": "租约过期回收，待重新领取。",
                "claim_missing": "锁文件缺失，已回收。",
                "released": "已释放，待重新领取。",
            },
        )
        rt_rel = host.rel(rt_path)
        if not dry_run:
            rt_path.parent.mkdir(parents=True, exist_ok=True)
            rt_path.write_text(json.dumps(rt_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    if not dry_run:
        wi_path.parent.mkdir(parents=True, exist_ok=True)
        wi_path.write_text(md_text, encoding="utf-8", newline="\n")
    if store.ledger is not None:
        store.ledger.log("inv export-lease", f"{store.population['id']} → {host.rel(wi_path)}（{len(built_rows)} 项）")

    return {
        "work_items_path": host.rel(wi_path),
        "runtime_path": rt_rel,
        "exported_count": len(built_rows),
        "priority_order": tier_order,
    }


def sync_lease(
    host: Host,
    store: Store,
    *,
    work_items_path: str | None = None,
    runtime_path: str | None = None,
    evidence: str | None = None,
    blocked_status: str = "blocked",
    require_zero_active: bool = False,
    task_id: str | None = None,
    obligations: list[str] | None = None,
) -> dict[str, Any]:
    if blocked_status not in {"blocked", "fail"}:
        raise DlmUsageError("--blocked-status 必须是 blocked 或 fail")
    wi_path, rt_path = _resolve_lease_paths(host, store, work_items_path, runtime_path, no_runtime=False)
    if not wi_path.is_file():
        raise DlmError(f"{store.label}: 找不到 work_items.md：{host.rel(wi_path)}")

    rows = _parse_work_items_md(wi_path)
    if not rows:
        raise DlmError(f"{store.label}: work_items.md 为空或未解析到有效表格：{host.rel(wi_path)}")

    active_ids: list[str] = []
    for r in rows:
        if _normalize_lease_state(r.get("状态", "")) == "active":
            active_ids.append(r.get("编号") or r.get("模块") or "?")

    if rt_path is not None and rt_path.is_file():
        try:
            rt_data = json.loads(rt_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            rt_data = {}
        claims_rel = rt_data.get("claims_dir")
        if claims_rel:
            cdir = Path(claims_rel) if Path(claims_rel).is_absolute() else (host.root / claims_rel)
            if cdir.is_dir():
                for cfile in sorted(cdir.glob("*.json")):
                    if cfile.stem not in active_ids:
                        active_ids.append(cfile.stem)

    if require_zero_active and active_ids:
        raise DlmError(
            f"{store.label}: 仍有 {len(active_ids)} 个活跃 claim 未完成/释放（{', '.join(active_ids[:10])}），拒绝收口同步"
        )

    evidence_locator = evidence or host.rel(wi_path)
    by_wid = {str(i.get("work_item_id")): i for i in store.items if i.get("work_item_id")}
    by_key = {i["key"]: i for i in store.items}
    by_id = {i["id"]: i for i in store.items}

    pass_items: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []
    pending_items: list[dict[str, Any]] = []
    unmatched: list[str] = []
    stamp = now()

    for r in rows:
        wid = r.get("编号", "").strip()
        mod = r.get("模块", "").strip()
        item = by_wid.get(wid) or (by_key.get(mod) if mod else None) or by_id.get(wid) or by_key.get(wid)
        if item is None:
            unmatched.append(wid or mod or "?")
            continue
        if wid:
            item["work_item_id"] = wid
        prio = r.get("优先级", "").strip()
        if prio and prio != "—":
            item["priority"] = prio
        has_runtime_note_col = "运行备注" in r
        rem = r.get("备注", "").strip() if has_runtime_note_col else ""
        if rem == "—":
            rem = ""
        if rem:
            item["remark"] = rem
        note = r.get("运行备注", r.get("备注", "") if not has_runtime_note_col else "").strip()
        if note == "—":
            note = ""

        norm = _normalize_lease_state(r.get("状态", ""))
        if norm == "done":
            item["status"] = "pass"
            ev_list = item.setdefault("evidence", [])
            if evidence_locator not in ev_list:
                ev_list.append(evidence_locator)
            if note:
                item["reason"] = note
            item["updated"] = stamp
            pass_items.append(item)
        elif norm == "blocked":
            item["status"] = blocked_status
            if note:
                item["reason"] = note
            item["updated"] = stamp
            blocked_items.append(item)
        else:
            if item.get("status") != "na":
                item["status"] = "pending"
                item["updated"] = stamp
            pending_items.append(item)

    evidence_id = ""
    if store.ledger is not None and pass_items:
        pop_id = store.population["id"]
        bound_obs = [o["id"] for o in store.ledger.collection("O") if o.get("population") == pop_id]
        target_obs = list(dict.fromkeys((obligations or []) + bound_obs))
        pass_ids = [i["id"] for i in pass_items]
        scope_val = "full" if population_closed(store.population) else "partial"
        existing_v = next(
            (
                v
                for v in store.ledger.collection("V")
                if v.get("locator") == evidence_locator and v.get("status", "valid") == "valid"
            ),
            None,
        )
        if existing_v is not None:
            existing_v["scope"] = scope_val
            existing_v["obligations"] = list(dict.fromkeys((existing_v.get("obligations") or []) + target_obs))
            existing_v["items"] = list(dict.fromkeys((existing_v.get("items") or []) + pass_ids))
            if task_id:
                existing_v["task"] = task_id
            evidence_id = existing_v["id"]
        else:
            new_v = store.ledger.add(
                "V",
                {
                    "obligations": target_obs,
                    "items": pass_ids,
                    "scope": scope_val,
                    "locator": evidence_locator,
                    "method": "dlm-multi-llm-lease sync",
                    "answers": f"从 {host.rel(wi_path)} 同步 {len(pass_ids)} 项已完成模块状态",
                    "duration": "",
                    "gate": "",
                    "task": task_id or "",
                    "status": "valid",
                    "date": today(),
                },
            )
            evidence_id = new_v["id"]
        for item in pass_items:
            ev_list = item.setdefault("evidence", [])
            if evidence_id not in ev_list:
                ev_list.append(evidence_id)

    if store.ledger is not None:
        store.ledger.log(
            "inv sync-lease",
            f"{store.population['id']} ← {host.rel(wi_path)}（pass={len(pass_items)}, "
            f"{blocked_status}={len(blocked_items)}, pending={len(pending_items)}）",
        )

    return {
        "work_items_path": host.rel(wi_path),
        "pass_count": len(pass_items),
        "blocked_count": len(blocked_items),
        "pending_count": len(pending_items),
        "active_count": len(active_ids),
        "unmatched_rows": unmatched,
        "evidence_id": evidence_id,
    }
