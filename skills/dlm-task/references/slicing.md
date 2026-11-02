# Subtask Slicing & Executability

## Anatomy of a Subtask

| Element | Where It Lives |
|---|---|
| Owned obligations (one primary advancement outcome plus any inseparable companion results) | `dlm task create --oblige` / `--owns` |
| Prerequisites: named artifacts and required maturity (`planned < contract < implemented < verified`) | `--needs A2:contract`, cross-Epic `--needs E012/A3:implemented` |
| Write boundary (must be a subset of the Epic write boundary) | `--write` / `--generated` |
| Required reading sources: existing file, optional Markdown heading, and what this task consumes | `--source path#Heading::intent` |
| Task depth and assessment rationale | `--depth`, `--depth-reason` |
| Design carry-over & implementation plan: settled contracts (with rationale/source), tentative choices left to executor judgment, bounded unknowns, and key paths | `tasks/Sxxx.md` body under generated Chinese headings `## 方案承接与实施设计` and `## 关键路径与入口` |

Keep `tasks/Sxxx.md` focused on design facts that guide action under its generated Chinese headings (`## 方案承接与实施设计` and `## 关键路径与入口`). Do not duplicate task status, obligations, or write boundaries in the markdown body (`dlm brief` renders them directly from `ledger.json`). Never copy-paste shared paragraphs (baselines, authorization boilerplate, Epic principles) across multiple task files—record them once as a Decision (`D`). Never append dated execution logs to the task body; use `dlm checkpoint` and `dlm evidence` for progress, while `journal.md` records command history automatically. `dlm check` emits signals for duplicated paragraphs, dated log accumulation (`max_dated_lines=12`), overly long task bodies (`max_task_body_lines=200`), and subtasks spanning too many top-level roots (`max_task_owner_roots=3`) or obligations (`max_task_obligations=5`).

When a user direction change makes a subtask obsolete, first `supersede`, `waive`, or `transfer` all obligations it owns, then run `dlm task drop <E> <S> --decision D#` (requires a user decision).

## Slicing Principles

1. Work backward from confirmed user-observable outcomes, checking existing owners, capabilities, and constraints; for mechanisms that already exist, specify only how this task consumes them.
2. Isolate key unknowns and failure boundaries that could alter the technical route. Create an `investigation` task (with explicit `--question` and `--stop`) when an unknown must be resolved before committing to a contract, rather than assuming new public interfaces upfront.
3. Keep implementation, direct verification, and necessary fixes inside the same subtask. A single cohesive outcome spanning multiple modules belongs in one subtask; slice by independent outcomes and decision boundaries, not by layer names or file count.
4. Never split an atomic correctness invariant across tasks: if identity checking, coordination, execution, and readback only make sense together, keep them in one subtask.
5. Any unfulfilled mandatory effect must be transferred (`dlm transfer`) to a named unfinished task with an explicit entry condition—never relabeled "optional". Downstream diagnostics, packaging, or end-to-end regression tasks do not take over upstream implementation obligations.
6. When the user selects a shared claim/lease queue across decoupled modules following a unified SOP, use [`dlm-multi-llm-lease`](../../dlm-multi-llm-lease/SKILL.md) and split into a parallel worker subtask (`S_worker`) plus a follow-up serial closeout subtask (`S_closeout`).

## Pre-Delivery Executability Walkthrough

For every newly created or materially re-sliced subtask, trace one primary success scenario and one risk-relevant failure scenario from concrete inputs through to the actual verification entrypoint.

| Check | Question | Action When a Gap Is Found |
|---|---|---|
| **Delivery Independence** | Can this task be delivered, failed, and retried on its own, or does it share an indivisible correctness invariant with another task? | Split if independently consumable; merge if indivisible |
| **Responsibility Reuse** | Does existing code or an upstream task already own this state, lifecycle, or algorithm? | State the consumption boundary and remove duplicate ownership |
| **Prerequisite Necessity** | Which concrete artifact (`A#`) does each prerequisite provide, and what specifically cannot proceed without it? | Remove false prerequisites; distinguish `:contract` readiness from `:implemented` readiness |
| **Verification Reachability** | Do this task and its ready prerequisites include the wiring, host entrypoint, and fixtures needed to run direct verification? | Assign minimal wiring to its first consumer |
| **Evidence Alignment** | Does the verification scenario genuinely exercise the declared state, payload, and production path? | Replace proxy/mock scenarios or narrow the claimed scope |
| **Unknown Concentration** | Does the task bundle several unrelated technical unknowns at once? | Separate unknowns that can converge independently |
| **Falsification Test** | If the implementation violated this requirement, would the planned verification still pass? | Add a direct negative/boundary check or bind to a named end-to-end obligation |

Fold conclusions directly into the subtask's obligations, prerequisites, write scope, and design body—do not create a separate self-check report. `dlm plan-check <E>` validates structure and granularity signals, not semantic slicing quality.

## Task Depth (`--depth`)

| Depth | When to Use |
|---|---|
| `Ultra` | Non-local global invariants, multi-solution / concurrency / compatibility reasoning where subtle errors silently corrupt critical downstream consumers |
| `Max` | Public contracts, ABI / ownership boundaries, core algorithmic verdicts, or persistence semantics affecting multiple consumers |
| `High` | Bounded recursion, state machines, numerical precision, identity mapping, or cross-component integration |
| `Medium` | Localized implementation and regression with clear inputs, outputs, and rules |
| `Lite` | Thin wrappers, documentation, localization, or minor layout adjustments with settled rules and materials |

In `--depth-reason`, state: *what kind of error → which downstream consumers are affected → whether errors are immediately detectable → rework cost*. Depth is not priority, does not automatically select a model, and must never be used to bundle unrelated outcomes into one giant task or to lower acceptance standards.
