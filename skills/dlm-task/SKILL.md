---
name: dlm-task
description: Plan, execute, checkpoint and close persistent Epic/Subtask records through the `dlm` CLI; size each Epic as one medium, independently acceptable outcome. Also use whenever the user asks for comprehensive / deep / complete / all-items / 1:1 / parity work or names a concrete modification subject — inventory the full object set first, then execute item by item.
---

# DLM Task

Persistent task records keep goals, obligations, decisions, and evidence resumable across sessions. **Task state transitions only through `scripts/dlm.py`**; `ledger.json` is the single machine source of truth, while `README.md` and `backlog.md` are generated views. Host repository rules govern authorization; neither this skill nor the CLI creates or expands authorization.

Run commands relative to this skill directory: `python scripts/dlm.py <command>`. For full syntax, see the [CLI Reference](../_shared/references/cli.md).

**Output language (`<knowledge_dir>/`)**: Write all `<knowledge_dir>/` (`docs-llm/` by default, or `doc_llm/`) task prose (Epic/Subtask titles, outcomes, principles, obligations, decisions, checkpoint/close notes, `epic.md`, and `tasks/Sxxx.md`) in **Chinese (`zh-CN`)** unless the host explicitly configures another locale in `dlm.config.json`. Generated headings in `README.md`, `backlog.md`, `epic.md` (`## 背景与预期效果`, `## 共享设计与取舍`, `## 范围与非目标`), `tasks/Sxxx.md` (`## 方案承接与实施设计`, `## 关键路径与入口`), and `dlm brief` use [`_shared/locales/zh-CN.json`](../_shared/locales/zh-CN.json) by default—keep those exact Chinese headings when editing `epic.md` or `tasks/Sxxx.md`.

## When to Use

- The user asks to create or maintain persistent tasks, or authorized work genuinely requires cross-session resumption, dependency coordination, or handoff. Do not create an Epic for localized work that fits in a single feedback loop; modifying DLM skills themselves never requires an Epic.
- **Inventory routing (applies with or without an Epic)**: Whenever the user uses coverage/parity keywords (`全面`, `深度`, `完整`, `全部`, `所有`, `逐项`, `一比一`, `对齐`, `无遗漏`, `无劣化`, `comprehensive`, `exhaustive`, `parity`, `1:1`, etc.) or names a concrete set of objects to modify, first enumerate the full set mechanically from authoritative sources via `dlm inv`, then execute item by item immediately. Inventory is an execution discipline, not an approval gate. See [inventory.md](references/inventory.md).

## What the Tool Rejects Mechanically

These invariants are enforced by `dlm`; do not duplicate them in task prose. When a command is rejected, fix the underlying fact or obtain the required user decision—never bypass the CLI.

| Condition | Tool Result |
|---|---|
| Creating an Epic without concrete principles, verbatim user confirmation quote, observable outcome, or Epic-level acceptance | Refused; no files written |
| Declaring a "wait for Epic" dependency between Epics | Refused; only `verifies` and named artifact `Exxx/Ay` relations are allowed |
| Deliverable subtask owns zero obligations (`investigation` tasks require `question` and `stop`) | Refused |
| Obligation has no provenance: neither a user decision (`--origin D#`) nor derived from an existing obligation (`--derives O#`) | Refused; executors cannot invent new closure conditions on their own |
| Task prerequisite (`--needs`) references a hypothesis (`H#`) or a non-existent artifact | Refused; prerequisites must be named artifacts (`A#` or `Exxx/Ay`) |
| Subtask write scope exceeds the Epic write scope, or is overridden by a more specific `read_only` / `forbidden` rule | Refused |
| Editing principles, expanding write scope, waiving/superseding obligations, adding Epic gates, setting product limits, or sampling | Requires `--decision D#` pointing to a user decision with a verbatim quote |
| Implementation budget (`kind=budget`) sets `on_exceed=reject` | Refused (only `chunk`, `degrade-with-record`, or `unmet` allowed) |
| Closing a task when a `coverage=full` obligation has unclosed items, or swapping populations / marking reference items `na` without a user decision | Refused |
| Obligation transferred to this task has not been accepted (`dlm accept`) | Refused on `close` |
| Evidence does not list covered items, or evidence file is missing on disk (non-file evidence must use `note:` or `http(s)://` prefix) | Items stay unverified; `close` fails as `UNKNOWN` |
| Changed-path scope cannot be determined (`git` unavailable and no `--changed` provided) | `UNKNOWN`; `close` fails (`--changed` only adds to `git` when `git` is available, never hides dirty files) |
| Unattributed `git` worktree changes outside the task's `write`/`generated` scope | Kept as `unattributed` (does not block `close`); explicitly claimed `--changed` paths or in-scope paths hitting Epic `read_only`/`forbidden` are rejected |
| Host policy gate triggered without valid gate evidence, or public layer touched without `dlm justify` + user decision | Refused on `close` |
| Latest `checkpoint` is `deviated` or `pending`, or per-ID review checklist is incomplete | Refused on `close` |
| Hand-editing generated views (`README.md`) or citing a `dlm: historical` file as a required source | Flagged as error by `dlm check` |

Passing `dlm close` is a **necessary condition, not a sufficient condition**, for completion.

## What You Must Judge

1. **Goal → Obligations**: Express user-observable outcomes as obligations (`O`), not implementation steps. "Real end-to-end usability" must never be replaced by proxy goals like "code compiles" or "unit test passes". Bind coverage requests to `coverage=full` and a population `N#`. See [records.md](references/records.md).
2. **Epic Granularity**: Each Epic is one medium-sized, independently acceptable outcome. Roadmaps, project phases, and horizontal technical layers are not Epics. See [epic-sizing.md](references/epic-sizing.md).
3. **Slicing & Executability**: Each subtask owns one concrete advancement outcome with implementation, direct verification, and fixes in the same loop. See [slicing.md](references/slicing.md).
4. **Facts, Hypotheses & Ownership**: Trace failures along the active call chain, adapter layer, and data owner first. Record unverified root-cause explanations as hypotheses (`H`); never use an unverified hypothesis as a task prerequisite or a justification to expand write scope.
5. **Evidence Fidelity**: Verify that evidence genuinely exercises the declared inputs, payloads, state transitions, and production code path, and that the `--scope` label (`full` / `partial` / `sample` / `proxy`) matches what was actually proved.
6. **Validation Selection**: Mandatory gates are computed from attributed changes by `dlm scope` against host policy; every additional validation run must answer a specific new question (`--answers`), reusing still-valid evidence instead of re-running full suites blindly. Distinguish regressions introduced by current changes from pre-existing gaps.
7. **Limit Classification**: Separate user-confirmed `product` boundaries, executor-chosen `budget`s, and factual `resource-fact`s. An implementation budget must never reject valid business inputs or silently shrink the user's goal.
8. **Change & Reopen Classification**: Distinguish `new-requirement`, `regression`, `omission`, `evidence-lost`, and `superseded` via `dlm reopen --cause`, re-evaluating only the affected obligations.

## Lifecycle

**Planning**
1. For large goals, draft candidate Epics (outcome, scope, Epic-level acceptance, why each stands on its own, relations) in a draft JSON file, run `dlm plan-check --draft <file>`, and present the candidates together with any granularity signals to the user.
2. Present the concrete principles for the selected Epic to the user and obtain explicit confirmation for those principles and creating the Epic; then run `dlm epic create ... --principle ... --confirmation "<verbatim user quote>"`. Generic "go ahead" instructions do not count as principle confirmation.
3. Register obligations (`dlm oblige`), inventories (`dlm inv new|source`), artifacts (`dlm artifact`), and subtasks (`dlm task create`), then run `dlm plan-check <E>`.

**Execution**
1. Run `dlm task start <E> <S>` to verify prerequisite artifacts, capture the in-scope git baseline, and render the task brief; use `dlm brief <E> <S>` when resuming.
2. Implement and verify; record evidence via `dlm evidence` and `dlm inv mark`; record new decisions via `dlm decide` with `--affects`.
3. **End-of-turn checkpoint**: Run `dlm checkpoint <E> <S> --verdict aligned|deviated|pending [--note]` (`--note` is mandatory when `deviated` or `pending`). Correct any deviation within existing authorization first; if blocked, state the exact cause and next step so it appears on the first line of the next `dlm brief`.

**Closure**
1. Run `dlm close <E> <S> --print-checklist` and write an evidence-backed judgment for every principle (`P#`), owned obligation (`O#`), and—for `kind=investigation` subtasks—the investigation question (`Q`).
2. Run `dlm close <E> <S> --line P1=... --line O2=...` (or `--review file.json`). Any unfulfilled mandatory obligation must be transferred (`dlm transfer`) to a named unfinished task with an explicit entry condition—never downgraded to "optional".
3. Once all obligations and subtasks are terminal, run `dlm epic close <E>`.
4. Evaluate any knowledge write-back candidates printed by `close` on their reuse value via `dlm-knowledge`; write-back is not a completion gate.

**Historical (v1) Records**: Existing v1 Epics remain read-only (`dlm legacy check` is report-only). When resuming a v1 Epic that requires persistent tracking, import it mechanically with `dlm adopt <E>` (which renames the v1 `README.md` to `epic.md`, generates a v2 `README.md`, and keeps existing v1 subtask files at `subtasks/Sxxx.md`) and register obligations (`dlm oblige`) for remaining unfinished subtasks before closing them.

## Final Response

State the current turn's `checkpoint` verdict concisely; when an inventory exists, report `总数 (total) / pass / fail / gap / na / blocked / pending` counts. Never claim completion or parity while `pending`, `fail`, or `gap` is non-zero.

## Stopping Rule

Stop as soon as target obligations are terminal, required validations pass, and agreed deliverables are in place. Do not append extra audit reports, review rounds, gates, or follow-up tasks for process ceremony.
