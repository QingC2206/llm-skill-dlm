# DLM — LLM Knowledge Base & Task Workflow Pack

This document is for DLM pack maintainers. For runtime usage, see each skill's `SKILL.md`.

## Pack Structure

| Skill | When to Use | Outcome |
|---|---|---|
| [dlm-task](../dlm-task/SKILL.md) | Persistent task management, or coverage/parity requests and concrete modification subjects (inventory routing) | Epics/Subtasks backed by obligations, evidence, and boundaries; item-by-item closed inventories |
| [dlm-review](../dlm-review/SKILL.md) | User-requested, contract-required, or material-risk reviews | Evidence-backed review verdict and scoped follow-up actions |
| [dlm-load](../dlm-load/SKILL.md) | Next action requires task or domain context | Rendered task brief or targeted owner knowledge |
| [dlm-knowledge](../dlm-knowledge/SKILL.md) | Building, updating, writing back (`sediment`), or auditing `<knowledge_dir>/` (`docs-llm/` by default, or `doc_llm/`) | Accurate, navigable, and consumable Chinese knowledge base docs |
| [dlm-multi-llm-lease](../dlm-multi-llm-lease/SKILL.md) | User-selected shared claim/lease queue across decoupled modules following a unified SOP | Serial per-worker module claiming, priority-tier ordering, static remarks, reopen, and deferred closeout |

`_shared/` holds the shared implementation and reference specifications:

- `scripts/dlm.py`: Single CLI entrypoint; each skill's `scripts/dlm.py` is a thin forwarding wrapper.
- `scripts/dlm_*.py`: Configuration, ledger model, invariants, granularity signals, inventory, git scope attribution, view rendering, knowledge tools, and v1 adoption.
- `scripts/validate_doc_llm.py`, `sync_l1_route.py`, `doc_llm_scaffold_tool.py`, `l2_audit_map.py`, `context_manifest_tool.py`: Knowledge base and legacy v1 validation tools invoked via `dlm kb` and `dlm legacy check`.
- `scripts/legacy/`: Legacy v1 parsing modules and Chinese v1 templates used strictly for read-only reporting.
- `locales/`: Section heading locales for generated views (`zh-CN.json` is the default so `<tasks_dir>/` outputs remain in Chinese; `en.json` is available if overridden in `dlm.config.json`).
- `references/`: [CLI Reference](references/cli.md), [Ledger & Invariants](references/ledger.md), [Host Integration](references/host-integration.md), [Pack Governance](references/dlm_skill_pack_governance.md), [Knowledge Base Writing Rules](references/doc_llm_writing_rules.md), and [L2 Write-Back Contracts](references/l2_writeback_contracts.md).

## Core Design Principles

- **English Prompts, Chinese `<knowledge_dir>/` Output**: Skill prompts and references are written in English to maximize LLM instruction-following precision, while all generated scaffolds (`dlm-knowledge/templates/*.md`), default rendered views (`_shared/locales/zh-CN.json`), and authored `<knowledge_dir>/` (`docs-llm/` by default, auto-compatible with `doc_llm/` / `docs_llm/`) content remain in Chinese (`zh-CN`).
- **Single Machine Source of Truth**: Each Epic's `ledger.json` stores principles, decisions, obligations, populations, artifacts, hypotheses, limits, evidence, and subtask states; `README.md`, `backlog.md`, and `dlm brief` are rendered from it. Principles are never duplicated into subtasks.
- **Deterministic Rules in Python**: Every mutating command runs `dlm_invariants.py` before writing; mutations that introduce new blocking violations are refused. `UNKNOWN` at a gate is treated as failure.
- **Completion Defined by Obligations**: Closing a subtask requires all owned obligations to be terminal or transferred to a named unfinished task; `coverage=full` obligations derive their status from item-level states, and evidence must list covered items.
- **Inventory Routing**: Coverage/parity requests or concrete modification subjects mechanically enumerate the full object set before item-by-item execution; parity mode enumerates both sides and computes the diff.
- **Epic Granularity**: Each Epic is one medium-sized, independently acceptable outcome; only `verifies` and named `artifact` relations are permitted across Epics; `plan-check --draft` surfaces granularity signals before touching disk.
- **Four Fixed Checkpoints**: Planning (`plan-check`), task start (`task start`), end-of-turn (`checkpoint`), and closure (`close`)—no extra process stages.
- **Zero Host Coupling**: Host-specific paths, gates, public layers, and thresholds live exclusively in `dlm.config.json` and `dlm.policy.json`.

## Running Tests

From the root of the `llm-skill-dlm` source checkout, run:

```bash
python -X utf8 scripts/test_all.py
```

The runner provides an isolated host for import-time discovery and runs the shared, lease, and installation suites. It belongs to the source checkout; `install.py` installs the skill pack into consuming repositories, without this maintenance runner.

`test_dlm_v2.py` covers positive and negative cases for every invariant, replays historical failure modes (partial delivery claimed as full completion, spot-checking claimed as 1:1 parity, roadmap/layer Epics, unverified hypotheses as prerequisites, budgets rejecting valid inputs, unattributed worktree changes vs. real boundary violations), and enforces zero host-specific tokens across the pack.

## Changelog

### 2026-11-02 — Portable Paths and SOP Validator Scaffolds

- Clarified source-checkout test commands and the host-discovery prerequisite for initializing a manually installed pack.
- SOP scaffolding now leaves `validator_commands` empty with an explicit setup note when no command is supplied; it no longer invents a script path. Supplied commands remain in both front-matter and the command section.
- Marked the v1 index template as read-only historical material and identified the current `dlm render` entrypoint.

### 2026-10-30 — Default `docs-llm/` Directory, `doc_llm/` Compatibility, `dlm-multi-llm-lease` Rename, English Prompts & Scope Fix

- Standardized the shared lease skill as `dlm-multi-llm-lease` and enhanced it with upfront unified-SOP & advisory coupling checks, priority-tier claiming (`priority_order` / `"优先级"`), mandatory default `"备注"` (`remark`) column with empty-string normalization and legacy table compatibility, targeted module claiming (`--module`), atomic reopen (`reopen_work_item.py` / `claim_next_work_item.py --reopen`), and same-worker expired lease auto-renewal.
- Changed `DEFAULT_KNOWLEDGE_DIR` in `dlm_config.py` to `"docs-llm"` while auto-detecting existing `doc_llm/` / `docs_llm/` directories (`KNOWN_KNOWLEDGE_DIRS = ("docs-llm", "doc_llm", "docs_llm")`) and honoring explicit `dlm.config.json` / `DLM_DOC_LLM_DIR` overrides across all v2 and knowledge-base tools.
- Converted all skill prompts (`SKILL.md`), reference docs (`references/*.md`), and maintainer docs to English while keeping `dlm-knowledge/templates/*.md`, `_shared/locales/zh-CN.json` (default `locale="zh-CN"`), and `<knowledge_dir>/` output contracts in Chinese.
- Fixed `dlm_scope.py` so unattributed git worktree changes outside a task's `write`/`generated` boundary remain `unattributed` even when matching Epic `forbidden`/`read_only` globs, while explicitly claimed `--changed` paths and in-scope paths violating boundaries continue to be rejected.

### 2026-10-29 — Epic Granularity & Prose Governance from Real Counterexamples

- Replayed a repository sliced into 13–14 horizontal stage/layer Epics: added cross-Epic dependency counts (task `needs` + Epic `relations`) and dependency-web signals across `--draft`, mutating commands, and `dlm check`; added the horizontal-layer counterexample to `epic-sizing.md`.
- Added prose-governance signals (duplicated paragraphs across files, accumulated dated log lines, overly long task bodies) and repeated full-validation signals (`--duration` on evidence, validation counts on `checkpoint`).
- Added `task drop` (requires user decision and resolved obligations) and mapped stopped/archived v1 tasks to `dropped` on `adopt`.
- Auto-detected 2-digit vs. 3-digit ID widths (`E01`/`E001`, `S01`/`S001`).

### 2026-10-29 — v2 Review Hardening

- Closed 9 loopholes across the 15 historical failure scenarios: mandatory obligation provenance (`--origin` or `--derives`), user decision required for `na` on reference items in `coverage=full` populations and for swapping `--population`, unaccepted transferred-in obligations block target `close`, missing evidence files on disk fail `close` as `UNKNOWN`, `inv mark` on Epic populations enforces invariants, `--changed` adds to git observations, `task create`/`task start` records an in-scope git baseline, and `brief` strips stale v1 state sections on adopted tasks.

### 2026-10-29 — DLM v2 Architecture

- Replaced Markdown state parsing with `ledger.json` + single `dlm` CLI; consolidated 6 skills into 4 (`dlm-task`, `dlm-review`, `dlm-load`, `dlm-knowledge`); added `dlm inv`, Epic granularity signals, 5 deterministic `reopen` causes, 3 limit kinds, and full host decoupling.
