---
name: dlm-multi-llm-lease
description: Create, maintain or consume a shared claim/lease queue explicitly selected by the user for independent top-level tasks.
---

# dlm-multi-llm-lease

**Output language (`<knowledge_dir>/`)**: When creating or updating task files, `work_items.md` tables, `work_item_runtime.json`, or claim reports under `<knowledge_dir>/` (`docs-llm/` by default, auto-compatible with `doc_llm/` / `docs_llm/`), keep all generated prose, table column headers (`编号`, `模块`, `优先级`, `备注`, `状态`, `执行者`, `开始时间`, `完成时间`, `运行备注`), and status notes (`租约过期回收，待重新领取。`, `锁文件缺失，已回收。`, `已释放，待重新领取。`, `终态缺少有效完成回执，已退回待领取。`) in **Chinese (`zh-CN`)** unless the host overrides `locale` in `dlm.config.json`.

## Target Execution Model

This skill implements a single concurrency model for **multi-module SOP execution**: when a concrete, unified SOP or task execution guide applies across multiple decoupled modules, the user may launch one or more independent top-level LLM sessions that re-enter and consume the same shared work-item queue. Each task is strictly self-only, claims a distinct module along with its per-module remark (`备注`), and executes serially inside its own loop:

```text
claim next (module + remark) -> execute unified SOP on module -> heartbeat as needed -> complete -> claim next
```

Concurrency comes exclusively from the user launching multiple top-level tasks—never from a task spawning subagents, worker threads, or peer tasks to consume the queue. If a frozen task contract explicitly requires per-item independent semantic review by user instruction, the primary agent may serially invoke one read-only reviewer after freezing the candidate snapshot and pausing all writes; that reviewer is not a queue worker and never introduces intra-item write concurrency.

## Pre-Creation Assessment & User Decision Authority

When creating a `$dlm-multi-llm-lease` queue (`work_items.md` + `work_item_runtime.json`), the LLM evaluates two upfront items while keeping final decision authority with the user:

1. **Unified Concrete SOP / Execution Guide (`sop_ref`)**
   - Confirm that a concrete, reusable SOP or task execution guide exists (or author/freeze it first) so that every worker follows the same workflow, verification steps, and deliverable contract across modules.
   - Record the SOP path or reference in `work_item_runtime.json` (`"sop_ref"`) and in the Subtask prose so every worker loads the same SOP upon claiming a module.
2. **Inter-Module Coupling & File Overlap Assessment (Weak/Medium Advisory Reminder — User Decides)**
   - Assess whether target modules are decoupled during worker execution or whether some files/touchpoints overlap.
   - **Never enforce this via script checks or hard-block queue creation.** In practice, modules may touch overlapping files while having distinct impact surfaces or non-conflicting boundaries known to the user.
   - If potential coupling or file overlap is detected, emit only a **weak-to-medium advisory reminder** to the user (and optionally record module-specific boundary hints in the `"备注"` column of `work_items.md`, while routing shared index/catalog/aggregate updates to the follow-up serial closeout Subtask `S_closeout`). **Whether to proceed with the parallel queue is always decided by the user.**

## Non-Overridable Task Creation Constraints

Whenever an Epic, Subtask, SOP, or execution plan selects `$dlm-multi-llm-lease`, the task author must embed the following rules into the task's goal, authorized write scope, execution protocol, and stopping conditions. Task-level prose may never override or relax these constraints:

1. **User Monopoly on Parallelism**
   - Only the user decides whether to run in parallel and how many independent top-level tasks to start.
   - An individual task never receives, infers, or reads the worker count (`N`), and never maintains peer identities, worker rosters, or "whether other tasks exist" state.
   - An individual task must never spawn subagents, new tasks, or parallel executors to claim, author, or complete queue items.
   - **Sole exception**: When explicitly required by the user and set as a completion gate in the frozen task/SOP contract, the primary agent may serially invoke one first-level read-only reviewer after freezing the current item's candidate snapshot and pausing writes. The primary agent must wait for the reviewer to return before continuing; the reviewer must never `claim`, `heartbeat`, `release`, `complete`, write to the workspace, sign a receipt on the primary's behalf, sub-delegate, or switch items.
2. **Only the Current Top-Level Primary Agent Consumes the Queue**
   - The primary agent may only `claim`, `heartbeat`, `complete`, or `release` for itself.
   - A claimed item may never be handed off, and subagents may never write deliverables, sign completion receipts, or report terminal state for an item.
   - By default, each task holds at most **one** active claim (`max_active_claims_per_worker = 1`). Holding multiple active claims simultaneously is allowed only when the user explicitly authorizes an exact item allowlist, `multi_claim_authorization_ref`, and claim limit—and execution inside the task remains strictly serial item by item.
   - A read-only reviewer may only inspect the frozen candidate digest of the currently claimed item and return `PASS / FAIL / BLOCKED` with evidence; final fixes, review record persistence, receipt sealing, and `complete` remain the sole responsibility of the top-level primary agent.
3. **Identity Provided Exclusively by the Task Host**
   - On Codex, the runtime uses the host-injected `CODEX_THREAD_ID` as the stable worker ID and verifies `CODEX_SESSION_ID == CODEX_THREAD_ID` to confirm an independent top-level context.
   - New top-level tasks automatically receive distinct IDs from the host; multi-turn invocations within the same top-level task reuse the same ID.
   - Neither the user nor the LLM may invent, name, fill in, copy, or override a worker ID.
   - Production commands must never pass `--worker` or `--worker-env-var`.
4. **Automatic Item Assignment by Default; Exact User-Authorized Exceptions & Reopen**
   - By default, `claim` atomically selects the next available item under the unified write lock (`assignment_mode = "next_available_only"`), ordered by `priority_order` (when configured) and table row order.
   - **Targeted module/item claiming**: Claiming a specific module (`--module <ModuleName>`) or work item (`--item-id <ID>`) is allowed only when explicitly authorized by the user; tasks must never self-select, infer, expand, or treat a preference as a wildcard authorization.
   - When the user explicitly designates a module or item, either pre-configure `assignment_mode = "next_available_or_user_authorized"`, `user_authorized_item_ids`, and `user_authorization_ref` in `work_item_runtime.json`, **or** pass `--module <ModuleName> --user-authorization-ref "<verbatim user quote or DLM decision D#>"` to `claim_next_work_item.py` (which atomically records the resolved item ID and authorization reference into `work_item_runtime.json` under the write lock and claims it).
   - **Claim reopen & rework (`reopen_work_item.py` / `claim_next_work_item.py --reopen`)**: When the user instructs to reopen a `done` (`✅`) or `blocked` (`❌`) module/item (or take over an abandoned session's `active` (`🔄`) item via `--force-active`), use `reopen_work_item.py --module <ModuleName> --reason "<user instruction>"` to reset it to `⬜`, or pass `--claim` (or `claim_next_work_item.py --module <ModuleName> --reopen --user-authorization-ref "<user instruction>"`) to atomically reopen and claim it in the current window under a single write lock so no other worker can snatch it. Use `--remark` or `--append-remark` to update the static `"备注"` column with the user's rework notes.
   - **Same-worker expired lease auto-renewal**: If a single top-level session takes longer than `lease_seconds` on its owned item while no other worker has claimed it, re-entering `claim_next_work_item.py`, `heartbeat_work_item.py`, or `complete_work_item.py` from that same worker automatically renews its own lease instead of dropping the item or failing.
   - In single-claim mode, `heartbeat` / `complete` / `release` resolve the owned item automatically; in explicitly authorized multi-claim mode, `--item-id` or `--module` is required to select which owned item to operate on (omitting both when holding multiple claims fails closed).
   - Re-entering `claim` returns the existing active claim by default; under multi-claim authorization, an additional claim is acquired only when another allowlisted `--item-id` / `--module` is explicitly passed, bounded by `max_active_claims_per_worker` under the write lock.
5. **Continuous Serial Loop Within Each Task (No Per-Item Prompting)**
   - After verifying a successful claim, the worker may emit a brief non-blocking status block, then immediately execute the item without waiting for per-item user confirmation.
   - Upon completing the current item, the worker immediately claims the next item until the queue is empty/closed, the user stops the task, an identity/lease error occurs, or a real blocker is hit.
   - A worker must never stop merely because it does not know `N`, cannot see peers, or just finished one item.
   - When the contract requires a read-only review, that review is part of the current item's serial loop (`claim -> author -> freeze candidate -> read-only review -> primary fix/seal -> complete -> claim`) and must never overlap with primary writes or the next item.
6. **No Unified Closeout Inside the Parallel Worker Subtask**
   - Never define a coordinator, finalizer, finalizer election, "last worker", shared aggregate writer, or unified closeout item inside the parallel worker Subtask.
   - When a worker encounters queue empty/closed, it stops immediately; it must never compete to aggregate, project, run full-suite validation, build, publish, or mark the Subtask `done` (`dlm close`).
   - Denominator reconciliation, missing/duplicate checks, verifying `0` active claims, shared aggregation/projection, full validation, and final Subtask/Epic acceptance must belong to a **separate follow-up Subtask** executed serially after all workers have stopped.
   - The follow-up closeout Subtask must never appear as a row in `work_items.md` and must never be auto-created or auto-started by a queue worker.

## Pairing with DLM (`dlm-task`)

When using `$dlm-multi-llm-lease` inside a DLM v2 Epic (`ledger.json` under `<tasks_dir>/Exxx_slug/`):

- **Two-subtask split**: Register at least two subtasks via `dlm task create`:
  1. **Parallel worker subtask (`S_worker`)**: Scoped (`--write` / `--generated`) to per-item exclusive output paths, `<task_root>/references/work_items.md`, and `<cache_dir>/work_item_claims/...`; produces a named artifact (`dlm artifact <E> --text "..." --producer S_worker`). Workers execute the serial lease loop and stop on queue empty/closed without running `dlm close`.
  2. **Serial closeout subtask (`S_closeout`)**: Declares `--needs A#:implemented` on the worker subtask's artifact, verifies `0` active claims and complete item receipts, updates the DLM population (`dlm inv sync-lease` or `dlm inv mark`) and validation evidence (`dlm evidence`), writes shared aggregates/indexes, and closes both subtasks via `dlm close`.
- **One-click bidirectional bridge (`dlm inv export-lease` & `dlm inv sync-lease`)**:
  - **Export (`N#` → `work_items.md` + `work_item_runtime.json`)**: After enumerating target modules into population `N#`, run `python scripts/dlm.py inv export-lease <N#_or_name> --epic <E> --task <S_worker> --sop-ref <sop_path> [--priority-rule "A=REGEX"...] [--remark "Module=Note"...]` to generate the 9-column `work_items.md` (with mandatory `"备注"` column, empty when unset) and strict `work_item_runtime.json`, while binding each item's `work_item_id` (`W###`) in `ledger.json`. Re-running `export-lease` is idempotent and preserves active/done worker states unless `--reset-state` is passed.
  - **Sync (`work_items.md` → `N#` + `V#` evidence)**: During `S_closeout`, run `python scripts/dlm.py inv sync-lease <N#_or_name> --epic <E> --task <S_closeout> --require-zero-active --gate` to verify `0` active claims, map `✅`/`❌`/`⬜` rows back to `pass`/`blocked`/`pending` in `N#`, and automatically register/update the ledger `V#` evidence covering all completed items.
- **Decision provenance**: Whenever the user authorizes targeted `--item-id` / `--module` claiming or `max_active_claims_per_worker > 1`, record the user decision via `dlm decide <E> --source user --quote "<verbatim user quote>"` and reference that decision ID (`D#`) in `user_authorization_ref` / `multi_claim_authorization_ref`.

## Applicability

Use `$dlm-multi-llm-lease` when:

- A unified concrete SOP or task execution guide (`sop_ref`) applies across multiple modules;
- Work items can be completed independently (ideally with disjoint write sets or non-conflicting impact surfaces approved by the user; if partial file overlap exists, the LLM provides only a weak/medium reminder and the user decides whether to proceed);
- Multiple top-level tasks share the same local filesystem and the same `claims_dir`;
- Each item has an independently verifiable terminal state and deliverable;
- Shared outputs (aggregates, indexes, catalogs, full builds) can be deferred to a follow-up serial Subtask;
- The task host injects a stable, unique, re-entrant top-level task identity.

Avoid `$dlm-multi-llm-lease` (or warn the user before proceeding) when:

- The current Subtask must produce a unified aggregate during the worker phase;
- The execution environment lacks host-injected task identity and would require manual worker IDs;
- Executors do not share a single local filesystem (use SQLite, Redis, or a server broker for cross-machine queues).

## Task Creation Workflow

1. Perform the **Pre-Creation Assessment** first: confirm that a concrete unified SOP (`sop_ref`) exists, and assess whether modules are decoupled. If potential file overlap or coupling is noticed, issue only a weak-to-medium advisory reminder to the user (and note module boundaries in `"备注"`) without script-blocking; the user holds final decision authority on whether to run in parallel.
2. Read [references/integration-snippets.md](references/integration-snippets.md) when authoring or updating a lease contract; when consuming an existing queue, follow its configured contract directly.
3. Orchestrate the module list, claim priority order, and per-item remarks in `work_items.md` and `work_item_runtime.json`:
   - **Default table columns (`work_items.md`)**: `| 编号 | 模块 | 优先级 | 备注 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |`.
   - **Priority-tier / series claim ordering (`"优先级"` + `priority_order`)**: When the user specifies a series or batch priority order (e.g., claim Series A modules first, then Series B, then Series C), populate `"优先级"` (`A`, `B`, `C`), order the table rows accordingly, and set `"priority_order": ["A", "B", "C"]` in `work_item_runtime.json`. Automatic `claim_next_work_item.py` calls always hand out unclaimed items from the highest-priority tier first while still allowing multiple workers to claim available items in parallel.
   - **Mandatory default `"备注"` (`remark`) vs. runtime `"运行备注"` (`note`)**: Every newly created `work_items.md` **must** generate the `"备注"` column by default (never omit the column); if a specific module has no remark, leave its `"备注"` cell empty (`""`). `"备注"` is a static read-only column where the user or parent task provides module-specific context, parameters, or boundary hints, and the lease runtime never overwrites `"备注"` during `claim`, `heartbeat`, `complete`, or `refresh` (runtime status and completion notes are written exclusively to `"运行备注"`). When a worker claims an item, it always receives `work_item_remark` (empty string `""` when no remark is set) alongside `work_item_module`, `work_item_priority`, and `sop_ref` in the JSON result. Historical `work_items.md` tables created without a `"备注"` column remain backward-compatible and return `"work_item_remark": ""`.
4. Generate or update:
   - `work_items.md`: display table only (in Chinese);
   - `work_item_runtime.json`: must include the strict `orchestration_policy`, `column_map` (including `module`, `priority`, `remark`, and `note`), and optional `sop_ref` / `priority_order`;
   - Epic / Subtask / SOP prose: must state the unified SOP reference, user-controlled parallelism, host-injected identity, single-task serial loop, and deferred closeout.
5. Production worker commands must invoke this skill's scripts with the canonical config, without worker overrides and—by default—without `--item-id` overrides (use `--item-id` only when exact user authorization is recorded in the canonical policy).
6. If `work_items.md` is generated by a materializer script, update the materializer so re-running it preserves the strict header note, `"优先级"`, and `"备注"` columns.
7. Plan a separate follow-up Subtask for unified closeout; unless the user has explicitly authorized a continuous sequential workflow, record only the follow-up boundary without auto-starting it from a worker.

To render deterministic Chinese/JSON contract snippets, run [scripts/render_lease_snippets.py](scripts/render_lease_snippets.py).

## Runtime Invariants

- `work_items.md` is a display projection only; `claims/*.json` is the single source of truth for active ownership.
- Runtime claim directories must live under the host's cache directory (e.g., `out/work_item_claims/...`).
- Every new `work_item_runtime.json` must explicitly declare `worker_env_var`, `orchestration_policy`, `column_map`, and `note_messages`.
- Default `orchestration_policy`:

```json
{
  "schema_version": 1,
  "profile": "user_started_top_level_tasks",
  "parallelism_authority": "user",
  "peer_awareness": "self_only",
  "identity_source": "host_task_environment",
  "worker_env_var": "CODEX_THREAD_ID",
  "session_env_var": "CODEX_SESSION_ID",
  "require_equal_identity": true,
  "assignment_mode": "next_available_only",
  "max_active_claims_per_worker": 1,
  "worker_loop": "serial_claim_complete_repeat",
  "closure_mode": "followup_subtask_only"
}
```

When the user explicitly authorizes targeted work items, replace only the assignment fields:

```json
{
  "assignment_mode": "next_available_or_user_authorized",
  "user_authorized_item_ids": ["W003", "W001"],
  "user_authorization_ref": "Verbatim user quote or DLM decision D# authorizing exact item IDs"
}
```

The allowlist must contain exact, non-empty item IDs; it is neither a worker roster nor a cross-task scheduler. Without all three matching fields, automatic assignment remains in effect and explicit `--item-id` fails closed. A worker may only target the specific item designated by the user for the current invocation.

- With strict `orchestration_policy`, the runtime deterministically rejects manual `--worker`, `--worker-env-var`, unauthorized `--item-id`, child session/thread mismatches (`CODEX_SESSION_ID != CODEX_THREAD_ID`), and worker/coordinator `refresh` closeout calls.
- A successful `claim` returns and verifies `resolved_worker`, `claim_file`, `claim_file_exists=true`, and `claim_verified=true`, with `resolved_worker` equal to the current host task ID.
- `heartbeat`, `complete`, and `claim` share the same write mutex; `complete` persists the terminal table state before unlinking the claim file.
- When `done` / `blocked` requires verifiable business evidence, configure `access_policy.completion_receipt` and artifact digest bindings bound strictly to the current worker, claim, and item.
- Every work item ID must be a single safe path segment (`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`).
- To prevent shadow configs from bypassing strict policy, register the canonical config, table, and claims directory in `.agents/work_item_lease_namespaces.json`.

## User-Authorized Multi-Claim Mode

Multi-claim mode is **not** automatic batch claiming. The user must explicitly authorize the exact item IDs, the maximum number of simultaneous active claims (`max_active_claims_per_worker > 1`), and the serial execution scope, with the verbatim authorization recorded in the canonical policy (`user_authorization_ref` and non-empty `multi_claim_authorization_ref`). The number of `user_authorized_item_ids` must be at least `max_active_claims_per_worker`, and `access_policy.max_active_claims_per_worker` (when present) must match.

Workers still claim items one call at a time via `--item-id` (no all-or-nothing batch transaction). When holding multiple active claims, `claim` re-entry, `heartbeat`, `complete`/`release`, and receipt sealing must each pass `--item-id` to select a specific owned item and maintain leases, evidence, and receipts item by item. Never reuse another item's receipt or bypass prerequisite artifacts. Shared library/SDK implementations still belong to their designated owner; holding multiple work-item claims never expands shared write permissions. If an item is later removed from `user_authorized_item_ids`, existing active claims may still be heartbeated or completed/released, but no new targeted claim for that item is permitted. Default `max_active_claims_per_worker` remains `1`.

## Legacy Boundary

The underlying Python library and CLI retain `--worker` / `--item-id` compatibility strictly for historical pre-strict tasks. New tasks must never derive permissions from legacy flags:

- New tasks must include the strict `orchestration_policy`; the runtime rejects worker overrides and rejects unauthorized item overrides.
- New SOPs must never show explicit `--worker` examples; only user-authorized targeted-claim branches may show `--item-id`.
- Never copy legacy scripts from `<tasks_dir>/_shared/scripts` to bypass strict validation; always invoke the current scripts in this skill directory.
- Historical tasks are migrated only when explicitly requested by the user.

## Verification & Stopping Rule

After creating or updating a lease-backed task, verify at minimum:

- No worker count `N`, peer roster, intra-task parallelism, or subagent queue-consumption instructions exist (if a frozen contract requires a read-only reviewer, verify it cannot write to the workspace, mutate the queue, sub-delegate, or overlap with primary writes);
- Production commands contain no `--worker` or `--worker-env-var`, default claims contain no `--item-id`, and targeted branches use only exact policy-allowlisted IDs;
- The strict runtime policy loads cleanly, admits matching host identities for claim/re-entry, rejects identity overrides / unauthorized `--item-id`, and admits exact user-authorized `--item-id`;
- Single-claim is enforced by default (and any multi-claim exception verifies allowlist, limit, explicit `--item-id` selection, ownership, and serial completion);
- The parallel worker Subtask contains no coordinator/finalizer/election, shared aggregate write, or final closeout step, and routes unified closeout to a separate follow-up Subtask without auto-starting it.

Stop once these checks pass; never launch extra tasks just to prove parallelism exists.

## Resources

- Integration contract & Chinese snippets: [references/integration-snippets.md](references/integration-snippets.md)
- Deterministic snippet generator: [scripts/render_lease_snippets.py](scripts/render_lease_snippets.py)
- Runtime scripts:
  - [scripts/claim_next_work_item.py](scripts/claim_next_work_item.py)
  - [scripts/heartbeat_work_item.py](scripts/heartbeat_work_item.py)
  - [scripts/complete_work_item.py](scripts/complete_work_item.py)
  - [scripts/reopen_work_item.py](scripts/reopen_work_item.py)
  - [scripts/refresh_work_items.py](scripts/refresh_work_items.py) (legacy / follow-up maintenance boundary; forbidden inside strict worker Subtasks)
  - [scripts/work_item_lease_lib.py](scripts/work_item_lease_lib.py)
