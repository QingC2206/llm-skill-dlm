# Strict Multi-LLM Lease Integration

Use this reference when creating or updating a task that uses `$dlm-multi-llm-lease`. Keep all generated `<knowledge_dir>/` (`docs-llm/` by default, or `doc_llm/`) task prose, `work_items.md` headers/notes, `column_map`, and `note_messages` in **Chinese (`zh-CN`)** using the exact snippets below.

## 1. Pre-Creation Assessment & Two-Subtask Split

When creating a `$dlm-multi-llm-lease` queue, evaluate two upfront items while keeping final decision authority with the user:

1. **Unified Concrete SOP (`sop_ref`)**: A single, concrete SOP or execution guide exists so that every worker executes the exact same steps and deliverable contract across modules.
2. **Inter-Module Coupling Assessment (Weak/Medium Reminder Only — User Decides)**: Assess whether target modules are decoupled during execution or have partial file overlap. **Never enforce this via script checks or hard-block task creation.** If some files overlap across modules, emit only a weak-to-medium advisory reminder (and optionally note per-module boundary rules in the `"备注"` column); if the user knows the impact surfaces do not conflict, proceed with the parallel queue as decided by the user.

Always split the work into at least two distinct Subtasks (`dlm task create` in DLM v2):

```text
Parallel Worker Subtask (S_worker)
  User starts 1..N independent top-level tasks
  Each task serially runs claim (module + remark) -> execute unified SOP -> complete
  Writes per-module deliverables within its module boundary
        |
        | queue has no available items; all workers stop
        v
Follow-Up Closeout Subtask (S_closeout)
  Single serial owner reconciles denominator, aggregates, projects,
  runs full validation, records DLM evidence, and runs dlm close
```

Here `1..N` only describes that the user may launch any number of top-level tasks—never write `N` into worker prompts, runtime configs, or queue state. The parallel worker Subtask does not know `N` and contains no finalizer.

Shared aggregate outputs, indexes, catalogs, or final reports should be deferred to the follow-up closeout Subtask; if partial file overlap exists across modules during the worker phase, remind the user at creation time and follow the user's decision on boundaries.

## 2. Subtask Contract Template (Chinese Prose for `<knowledge_dir>/Tasks/`)

Include the following Chinese contract rules in the parallel worker Subtask body (`tasks/Sxxx.md` under `## 方案承接与实施设计` in DLM v2, or `subtasks/Sxxx.md` in v1):

```md
- 前置信息说明：本任务所有目标模块均遵循统一具体的 SOP/任务指引（`<sop_ref>`）；模块间耦合度/文件重合情况由创建时评估提示（如有局部重合但影响面不同，按用户确认的边界及 `备注` 列说明推进）。
- 用户可以自行启动一个或多个独立顶层任务消费同一队列；是否并行和并行数量只由用户决定。
- 每个任务只读取自身宿主 identity、current claim（含目标模块 `work_item_module` 与静态备注 `work_item_remark`）和 queue result，不读取或推测 worker 数量与 peer identity。
- 只有当前顶层任务主代理可以 claim、执行、heartbeat、complete 或 release；不得创建子代理/新任务消费队列。用户明确要求且 Frozen 合同设门时，可在主代理暂停写入后串行启动一个只读 reviewer；reviewer 不能写工作区、操作 queue、代签或嵌套委派。
- worker identity 由宿主自动注入；禁止用户或 LLM 手填、复制、命名或覆盖 worker ID。
- work item 默认由脚本按优先级（`priority_order`）与表行序原子选择下一可用项；只有用户明确授权 exact item/顺序且授权已写入 canonical policy 时，claim 才能点名对应 item。
- 每个任务内部持续串行 claim -> complete -> claim，不逐项等待确认，直到 queue empty/closed、用户停止或真实 blocker。
- 本 Subtask 只产出逐 item 结果，不含 coordinator/finalizer/election/统一收口/共享 aggregate/最终发布。
- denominator 对账、missing/duplicate、0 active claim、聚合、投影、全量验证和最终验收属于后续独立 Subtask。
```

Never include any of the following forbidden patterns:

- `parallel_workers: N`, `expected_worker_count`, peer allowlists, or "wait for other workers";
- "the last worker automatically closes out", "compete for finalizer", or "launch a coordinator alongside workers";
- "wait for user confirmation after each claim before executing";
- asking the user to supply a worker ID or session name per turn, or asking the user to assign work items manually when no targeted-item preference was requested.

## 3. `work_items.md` Header Note & Default Table Template (Chinese)

Place this Chinese header note and default 9-column table at the top of `work_items.md`:

```md
> 本表只做展示；活跃领取状态的唯一事实源是 `<claims_dir>/*.json`。
> 用户可以启动一个或多个独立顶层任务；每个任务由宿主自动提供唯一身份，只串行领取下一可用项。
> 禁止手工修改运行状态、填写 worker、创建内部并行执行者或在本队列中加入统一收口项。
> item 默认按优先级（`优先级` 列 / `priority_order`）与行序自动分配；只有用户明确授权并写入 canonical policy 的 exact item 才能点名领取。
> `备注` 列用于承载用户或上级任务给出的模块特定前置说明（只读传入 worker）；运行时状态与完成说明写入 `运行备注` 列。
> queue empty/closed 后 worker 直接停止；共享聚合与最终验收由后续独立 Subtask 负责。

| 编号 | 模块 | 优先级 | 备注 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| W001 | ModuleA1 | A | 用户或上级任务提供的模块特定说明 | ⬜ | — | — | — | — |
| W002 | ModuleB1 | B |  | ⬜ | — | — | — | — |
```

Domain-specific columns may be added to the Markdown table. If any files overlap across modules but have distinct impact surfaces approved by the user, clarify the per-module boundary in the `"备注"` column; shared aggregate/index outputs still belong in the follow-up closeout Subtask.

## 4. `work_item_runtime.json`

Standard configuration for a new Codex task (with `sop_ref`, optional `priority_order`, and default Chinese `column_map` / `note_messages`):

```json
{
  "epic_id": "EXXX/SXXX",
  "label": "Task Group Label",
  "path_base": "project_root",
  "work_items_path": "<task_root>/references/work_items.md",
  "claims_dir": "out/work_item_claims/EXXX/SXXX",
  "sop_ref": "<knowledge_dir>/SOPs/<key>/README.md",
  "priority_order": ["A", "B", "C"],
  "lease_seconds": 1800,
  "lock_timeout_seconds": 120,
  "lock_stale_seconds": 600,
  "worker_env_var": "CODEX_THREAD_ID",
  "orchestration_policy": {
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
  },
  "column_map": {
    "id": "编号",
    "module": "模块",
    "priority": "优先级",
    "remark": "备注",
    "state": "状态",
    "worker": "执行者",
    "started_at": "开始时间",
    "completed_at": "完成时间",
    "note": "运行备注"
  },
  "note_messages": {
    "lease_expired": "租约过期回收，待重新领取。",
    "claim_missing": "锁文件缺失，已回收。",
    "released": "已释放，待重新领取。"
  }
}
```

All `orchestration_policy` fields are mandatory invariants:

- `parallelism_authority=user`: tasks cannot expand or shrink concurrency;
- `peer_awareness=self_only`: tasks do not inspect `N` or peers;
- `identity_source=host_task_environment`: identity comes from the task host;
- `assignment_mode=next_available_only`: queue assigns the next item atomically by default, forbidding `--item-id`;
- `max_active_claims_per_worker=1`: single active claim per task by default;
- `worker_loop=serial_claim_complete_repeat`: immediately claim again after completing an item;
- `closure_mode=followup_subtask_only`: no unified closeout inside this Subtask.

When running on a non-Codex host, replace `worker_env_var` and `session_env_var` with the host's stable top-level task/session environment variables. Never fall back to manual worker IDs.

When the user explicitly authorizes targeted item IDs, replace only the assignment fields:

```json
{
  "assignment_mode": "next_available_or_user_authorized",
  "user_authorized_item_ids": ["W003", "W001"],
  "user_authorization_ref": "用户消息或 DLM 决议 D# 中对 exact item / 顺序的明确授权"
}
```

All three fields must be present simultaneously, and `user_authorized_item_ids` must be exact and non-empty (no `*`, ranges, regexes, or LLM-inferred neighbors). If global ordering across items is a hard dependency, do not run peer-unaware parallel tasks—use a single serial task or reorder the canonical queue.

## 5. Optional `access_policy`

When admission files, role bindings, or completion receipts are needed, add `access_policy` without altering the orchestration model:

- `max_active_claims_per_worker` must equal `orchestration_policy.max_active_claims_per_worker` (`1` by default);
- `admission.worker_field` must bind to `orchestration_policy.worker_env_var`;
- the session field in `admission.environment_bindings` must bind to `orchestration_policy.session_env_var`;
- `require_equal_fields` must require session and worker identity equality;
- worker roles are used only for `claim` / `heartbeat` / `complete` / `release`—never introduce a coordinator/finalizer role into the parallel worker Subtask;
- `completion_receipt` binds strictly to the current task, worker, item, claim admission, and per-item exclusive artifacts (`artifact_digest_bindings` / `json_digest_bindings`).

Environment variables and SHA-256 digests are not cryptographic isolation against malicious processes running under the same OS user; if the threat model includes untrusted local processes, use a privileged broker or OS ACLs.

## 6. Worker SOP

Production commands must never include `--worker` or `--worker-env-var`. Default claims never include `--item-id`; use Section 6.1.1 only when exact user authorization is recorded in the canonical policy.

### 6.1 Automatic Claim

```powershell
python .agents/skills/dlm-multi-llm-lease/scripts/claim_next_work_item.py `
  --config <task_root>/references/work_item_runtime.json
```

Verify on every claim response:

```text
resolved_worker == current host task ID
claim_file: non-empty
claim_file_exists: true
claim_verified: true
work_item_state: 🔄
work_item_status: active
work_item_worker == resolved_worker
```

Emit the following non-blocking Chinese report block (including the claimed module, priority tier, and static remark) and proceed immediately with item execution:

```text
已完成领取校验，继续串行执行当前 work item。

resolved_worker: <resolved_worker>
claim_file: <claim_file>
claim_verified: true
work item: <work_item_id>
模块: <work_item_module>
优先级: <work_item_priority>
备注: <work_item_remark>
```

### 6.1.1 User-Authorized Targeted Module / Item Claim

Only when the user has explicitly designated the target module or item for this invocation:

```powershell
# By module name (automatically resolves to item ID and records user_authorization_ref if needed)
python .agents/skills/dlm-multi-llm-lease/scripts/claim_next_work_item.py `
  --config <task_root>/references/work_item_runtime.json `
  --module <exact-user-authorized-module> `
  --user-authorization-ref "<用户原话或 DLM 决议 D#>"

# Or by item ID when already recorded in orchestration_policy.user_authorized_item_ids
python .agents/skills/dlm-multi-llm-lease/scripts/claim_next_work_item.py `
  --config <task_root>/references/work_item_runtime.json `
  --item-id <exact-user-authorized-item-id>
```

When multi-claim mode (`max_active_claims_per_worker > 1`) is explicitly authorized by the user, pass `--item-id` or `--module` to `heartbeat_work_item.py` and `complete_work_item.py` as well to select which owned claim to operate on.

### 6.1.2 Claim Reopen & Rework (`reopen_work_item.py` / `--reopen`)

When the user instructs to reopen a completed (`✅`) or blocked (`❌`) module/item—or take over an abandoned session's active (`🔄`) item—and optionally update its `"备注"` column:

```powershell
# Option A: Atomically reopen AND immediately claim in the current window (prevents other workers from snatching it)
python .agents/skills/dlm-multi-llm-lease/scripts/claim_next_work_item.py `
  --config <task_root>/references/work_item_runtime.json `
  --module <target-module> `
  --reopen `
  --user-authorization-ref "<用户要求重开并领用的指令>" `
  --append-remark "<返工补充备注>"

# Option B: Reopen back to the queue (⬜) without claiming immediately
python .agents/skills/dlm-multi-llm-lease/scripts/reopen_work_item.py `
  --config <task_root>/references/work_item_runtime.json `
  --module <target-module> `
  --reason "<用户重开原因>" `
  --append-remark "<返工补充备注>"
```

Pass `--force-active` only when taking over an item whose previous session was closed/abandoned before its lease expired.

### 6.2 Heartbeat

```powershell
python .agents/skills/dlm-multi-llm-lease/scripts/heartbeat_work_item.py `
  --config <task_root>/references/work_item_runtime.json
```

### 6.3 Complete / Release

```powershell
python .agents/skills/dlm-multi-llm-lease/scripts/complete_work_item.py `
  --config <task_root>/references/work_item_runtime.json `
  --status done `
  --note "High-signal conclusion."
```

`blocked` records a deterministic blocker; `open` releases the current claim. After any completion/release action, immediately run `claim_next_work_item.py` again without waiting for per-item user confirmation. If the frozen contract requires a per-item read-only review, wait for the reviewer to return `PASS` on the frozen candidate digest and complete primary fixes, record writing, and receipt sealing before calling `complete_work_item.py`.

### 6.4 Stopping Conditions

A worker stops only when:

- `claim_next_work_item.py` reports `No available work items.` (queue empty) or queue closed;
- the user explicitly stops the task;
- an identity, lease, or claim verification error occurs;
- the current item hits a real blocker that cannot be resolved within authorized scope;
- the baseline contract is invalidated.

Queue empty/closed is **not** a unified closeout signal. Workers never run `refresh_work_items.py`, never compete to finalize, never write shared aggregates, and never mark the Subtask `done` (`dlm close`).

## 7. Follow-Up Closeout Subtask Template

Create a separate follow-up Subtask (`dlm task create`) that runs serially after all parallel workers have stopped. It is responsible for:

- re-reading the canonical denominator;
- verifying that all items reached terminal state, all completion receipts/artifacts match the exact set, and missing/duplicate counts are zero;
- verifying `0` active claims (and reconciling any expired leases if needed);
- syncing `work_items.md` terminal states and evidence back to the DLM inventory population in one command:
  ```powershell
  python .agents/skills/dlm-multi-llm-lease/scripts/dlm.py inv sync-lease <population_name_or_N#> `
    --epic <E###> `
    --task <S_closeout> `
    --require-zero-active `
    --gate
  ```
- generating shared aggregates, indexes, projections, or packages;
- running full static, build, or runtime validation gates;
- closing the worker and closeout Subtasks via `dlm close`.

## 8. Legacy Compatibility

Configurations without `orchestration_policy` retain legacy `--worker` / `--item-id` support for historical tasks only. Under strict `orchestration_policy`:

- explicit `--worker` and `--worker-env-var` are rejected;
- default or out-of-scope `--item-id` is rejected (only exact IDs in `user_authorized_item_ids` are accepted);
- `CODEX_SESSION_ID != CODEX_THREAD_ID` is rejected;
- `refresh_work_items.py` is rejected;
- re-entering `claim` from the same worker returns its existing active claim.

## 9. Integration Acceptance Checklist

Verify behavior directly rather than checking prose wording alone:

1. Two distinct host task identities on the same queue resolve to distinct workers and atomically claim different items.
2. Re-entering `claim` with the same identity returns the same active item.
3. Explicit `--worker`, `--worker-env-var`, unauthorized `--item-id`, and session/thread mismatches all fail without creating a claim file; exact user-authorized `--item-id` succeeds.
4. A worker completing its item immediately claims the next available item, holding at most one active claim by default (or enforcing the exact allowlist, limit, and `--item-id` selection in authorized multi-claim mode).
5. The worker phase performs no shared-output writes, finalizer election, or Subtask closeout.
6. Unified closeout responsibilities exist exclusively in the separate follow-up Subtask.
