---
name: dlm-knowledge
description: Build, update, sediment or audit the knowledge base (BRIEF, L0, L1, L2, Reference, SOP) — set up or restructure Domains, write back reusable findings from work, or assess and fix knowledge that is stale, misplaced or impedes a task.
---

# DLM Knowledge

The knowledge base (`<knowledge_dir>/`, defaulting to `docs-llm/` and auto-compatible with `doc_llm/` / `docs_llm/`) stores stable facts needed for future work that cannot be recovered cheaply by grepping source code. Run commands relative to this skill directory: `python scripts/dlm.py kb <action>`.

**Output language (`<knowledge_dir>/`)**: All knowledge base documents created or updated in `<knowledge_dir>/` (`BRIEF.md`, `L0`, `L1`, `L2`, `Reference`, `SOP`) **must be written in Chinese (`zh-CN`)** and use the Chinese templates in [`templates/`](templates/) (including Chinese front-matter status values such as `"已实现"` and `"已归档"`, and Chinese L1 route table headings required by `kb route` and `kb check`). Follow [`doc_llm_writing_rules.md`](../_shared/references/doc_llm_writing_rules.md) and [`l2_writeback_contracts.md`](../_shared/references/l2_writeback_contracts.md).

## Layer Contracts

| Layer | Content | Maintenance Rule |
|---|---|---|
| `BRIEF` | Purpose, consumers, and boundaries of the project or domain | Keep only orientation essentials |
| `L0` | Binding invariants and redlines | State applicability conditions; adding or changing redlines requires user approval |
| `L1` | Navigation router by problem or file path | Keep Chinese route headings (`## 根路由表` in `00_Global/L1_target_architecture.md`; `## 二、L2 路由表` and optional `## 三、参考资料路由` in domain `L1_*_router.md`); compile domain tables via `kb route`—never hand-edit generated route tables |
| `L2` | Stable mechanisms, boundaries, trade-offs, and non-obvious pitfalls | YAML front-matter: `domain`, `module`, `match_globs`, `status` (`"已实现" \| "部分实现" \| "待实现" \| "已废弃"`), `last_updated`; body reflects current active truth |
| `Reference` | Authoritative external specs, vendor docs, or historical baseline facts | Topic `README.md` (`kind: "reference_topic"`, `status: "已归档" \| "部分归档" \| "待补充" \| "已废弃"`) is the routed entrypoint; record source, version, and applicability |
| `SOP` | Reusable operational procedures for specific scenarios | Keep front-matter (`kind: "sop"`, `status: "active" \| "draft" \| "deprecated"`) per [`templates/sop_template.md`](templates/sop_template.md) and index under `## SOP 路由表` in `SOPs/README.md` |

Directory layout: `<knowledge_dir>/BRIEF.md`, `00_Global/`, `Domains/<N_key>/{BRIEF.md, L0_*, L1_*_router.md, L2_Modules/, references/}`, `SOPs/<key>/README.md`. The knowledge root defaults to `docs-llm/` (auto-detecting existing `doc_llm/` / `docs_llm/` or configurable via `dlm.config.json`).

## Three Modes

**`build`** — Initialize the knowledge base, restructure domains, or create new `L2` / `Reference` / `SOP` documents:
- Partition domains by actual responsibility, source ownership, and future consumers; create documents only for topics with stable reusable knowledge.
- Run `kb scaffold init-project|init-domain|create-l2|create-reference|create-sop ...` to generate Chinese markdown scaffolds from [`templates/`](templates/), populate substantive content before delivery, and run `kb route <domain_dir>` to sync `L1` route tables.

**`sediment`** — Write back reusable facts discovered during implementation, debugging, or investigation:
- `dlm close` prints candidate knowledge files whose `match_globs` overlap attributed changes (also queryable via `kb sediment [paths...]`). These are candidates only—**judge by future reuse value**, not as a mandatory gate, and no prior review step is required.
- Placement: task progress stays in the task ledger; reusable mechanisms, boundaries, and pitfalls go to `L2`; scenario procedures go to `SOP`; raw reference material goes to `Reference`; routing changes sync to `L1` via `kb route`. Never dump one-off execution logs, test checklists, or git log summaries into `L2`.
- When existing `L2` statements become obsolete, update them in place rather than appending dated "corrections" at the bottom.

**`audit`** — Assess and fix knowledge when requested by the user or when stale/misplaced docs impede a task:
- `kb stale [domain]` lists `L2` candidates whose matched source files changed after `last_updated`; `kb map <target> --json` reports size, changelog-bloat, layer-leak, and overlap signals. Signals and candidates are prompts for judgment, not automatic proof of defects.
- Apply the criteria in [`l2-audit-rubric.md`](references/l2-audit-rubric.md). If no material issue exists, state directly why no edit is needed.
- When the user asks for a comprehensive/full knowledge audit, follow inventory routing: `dlm inv source <name> --kind glob --spec "<knowledge>/Domains/**/L2_Modules/*.md"` and account for every file.

## What the Tool Checks

`kb check [paths]` validates knowledge structure: YAML front-matter fields, route table and markdown link resolution, UTF-8 readability, and `L0`/`L2` size/purity advisories (size warnings indicate context-loading cost, not hard errors). Task records are checked separately (`dlm check` for v2 ledgers, `dlm legacy check` for v1 tasks).

## What You Must Judge

- Whether the content is accurate, source-backed, and useful to a clear future consumer. When source code and documentation disagree, locate the cause of the discrepancy before assuming either side is right.
- Keep historical evolution notes only when they explain current design trade-offs, compatibility boundaries, or required provenance; write in concise Chinese prose per [`doc_llm_writing_rules.md`](../_shared/references/doc_llm_writing_rules.md) and [`l2_writeback_contracts.md`](../_shared/references/l2_writeback_contracts.md).

## Boundaries & Stopping

Respect host rules and any active task write scope; never expand write scope under the guise of knowledge sync. Stop once the requested knowledge is accurate, navigable, and consumable—do not append unrequested audits or follow-up tasks.
