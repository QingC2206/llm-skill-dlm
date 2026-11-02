# DLM Multi-LLM Lease Skill

`dlm-multi-llm-lease` provides a shared file-based claim/lease queue for independent top-level tasks launched by the user, working alongside `dlm-task`.

It enforces the following orchestration boundaries:

- **Pre-Creation Assessment & User Decision Authority**: Confirms upfront that (1) a concrete unified SOP / execution guide (`sop_ref`) is shared across target modules, and (2) evaluates inter-module coupling / file overlap as a **weak-to-medium advisory reminder only**—never script-blocking when partial file overlap has distinct impact surfaces and the user decides to proceed in parallel.
- Only the user decides whether to run in parallel and how many top-level tasks to launch; each task is unaware of `N` or peers.
- Each task uses only the stable task/thread identity automatically injected by the host (`CODEX_THREAD_ID == CODEX_SESSION_ID` by default), rejecting manual worker IDs from users or LLMs.
- By default, each task holds at most one active claim and executes `claim -> complete -> claim` serially.
- Work items are assigned atomically by default, respecting optional priority-tier / series ordering (`priority_order`, e.g., `["A", "B", "C"]` via the `优先级` column) and preserving static per-module remarks (`备注` -> `work_item_remark`) separately from runtime notes (`运行备注` -> `work_item_note`). Targeted `--item-id` claims are accepted only when the user explicitly authorizes exact item IDs and records that authorization in the canonical `orchestration_policy`.
- The parallel worker Subtask produces only per-item exclusive deliverables—no coordinator, finalizer, election, or unified closeout inside the worker phase.
- Denominator reconciliation, shared aggregation/projection, full validation, and final `dlm close` acceptance are handled serially by a separate follow-up Subtask.

The display projection is `work_items.md` (written in Chinese under `<knowledge_dir>/`, i.e., `docs-llm/` by default or `doc_llm/`), while active ownership truth lives in `work_item_runtime.json` and `claims/*.json`.

Included components:

- [`SKILL.md`](SKILL.md): Non-overridable rules for task creation and execution;
- [`references/integration-snippets.md`](references/integration-snippets.md): Integration contract and Chinese Subtask / `work_items.md` / SOP snippets;
- [`scripts/render_lease_snippets.py`](scripts/render_lease_snippets.py): Deterministic generator for strict runtime configs and Chinese markdown snippets;
- [`scripts/claim_next_work_item.py`](scripts/claim_next_work_item.py), [`scripts/heartbeat_work_item.py`](scripts/heartbeat_work_item.py), [`scripts/complete_work_item.py`](scripts/complete_work_item.py), [`scripts/reopen_work_item.py`](scripts/reopen_work_item.py), [`scripts/refresh_work_items.py`](scripts/refresh_work_items.py), and [`scripts/work_item_lease_lib.py`](scripts/work_item_lease_lib.py).
