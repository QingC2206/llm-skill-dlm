---
name: dlm-load
description: Load the knowledge or task brief needed to choose the next action — a task's rendered brief, a known owner's L0/L2/source, or project orientation when explicitly requested.
---

# DLM Load

Read only what is needed to choose the next action reliably; do not load the full repository map by default, and do not save standalone context-loading reports.

## Loading by Situation

- **Known v2 task**: Run `python scripts/dlm.py brief <E> <S>`. If the first line shows a `deviated` or `pending` checkpoint from the previous turn, address that first. The brief already renders owned and preserved obligations, effective principles, relevant decisions, prerequisite artifact readiness, open hypotheses, limits, write scope, triggered gates, design prose, and required sources. Read only the specific source sections needed for the current step.
- **Known owner or concrete question**: Read the relevant `L0`, `L2`, `Reference`, or source files directly. `BRIEF.md` and `L1` routers are navigation aids for unknown owners, not mandatory rituals. For large files, search by symbol or heading first and read targeted ranges.
- **Project orientation**: Only when the user asks to recover project context without naming a specific task, use `BRIEF.md` and `backlog.md` to locate active goals, then narrow to the target domain or task.
- **Historical (v1) task**: Read its `README.md` and subtask file directly; if persistent resumption is needed, import it via `dlm adopt <E>` and continue with the v2 workflow.

## Conflicts & Boundaries

- When the user's current instruction differs from recorded task state, resolve only the discrepancy that affects the next action: record new user decisions via `dlm decide` and update affected obligations or tasks.
- A reading list grants read access only; discovering additional owners or dependencies during loading never expands the authorized write scope.
- Source code proves current implementation behavior, while user decisions and contracts define required behavior; keep unverified explanations as hypotheses (`H`). Missing documentation or superseded historical files are not product defects in themselves.

## Stopping Rule

Proceed directly with authorized work as soon as you can choose the next action reliably; ask the user only when missing information would materially change the goal, key trade-offs, or authorization scope.
