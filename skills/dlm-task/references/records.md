# Authoring Ledger Objects

Every ledger object has a stable ID and machine-checked state; `dlm` enforces structural and referential invariants while the executor is responsible for semantic quality. Write prose fields (`text`, `summary`, `answers`, `note`, `what`) in **Chinese (`zh-CN`)** for `<knowledge_dir>/` (`docs-llm/` by default, or `doc_llm/`) records unless the host configures another locale.

## Obligations (`O`)

- State **user- or system-observable outcomes**, not implementation steps. *"All 167 commands can be sent end-to-end"* is an obligation; *"Generate configuration view code"* is not.
- `coverage`:
  - `single`: provable by a single `scope=full` evidence record;
  - `full`: bound to a population `N#`, with status derived mechanically from item states;
  - `sample`: must reference a user sampling decision (`--sample-decision D#`).
- `owner`: the subtask responsible for delivering the obligation (`S###`), or `epic` for Epic-level end-to-end acceptance. `preservers`: subsequent subtasks that must preserve this outcome without regression.
- **Stored vs. derived status**: In `ledger.json`, an obligation's stored `status` is `open | transferred | waived | superseded`. Runtime status (`verified | implemented | stale`) is derived automatically from `evidence` (`V`) and `populations` (`N`):
  - Only `verified` (derived from `scope=full` evidence or a closed `coverage=full` population), `waived`, and `superseded` are **terminal**.
  - `implemented` (derived when only `partial`, `sample`, or `proxy` evidence exists on a non-sample obligation) is **not terminal** and still blocks `dlm close` unless verified at full scope or transferred (`dlm transfer`).
- **Mandatory provenance**: Every obligation must specify either `--origin D#` (a user decision) or `--derives O#` (decomposed from an existing obligation). Using an executor decision as `--origin` emits a warning—if it expands acceptance scope, obtain user confirmation.
- Rewriting obligation text, downgrading `coverage=full`, or swapping the bound `--population` requires a user decision (`--decision D#`).
- Staged delivery never alters the obligation itself: a compile-only subtask may finish its own scoped obligation and close, while any unfulfilled end-to-end obligation must be transferred (`dlm transfer`) to a named unfinished subtask, preventing premature Epic closure.

## Evidence (`V`)

- `--scope full | partial | sample | proxy` must match what the verification actually exercised: short samples, synthetic delays, diagnostic harnesses, and production workloads prove different claims.
- `--items` lists covered inventory items (`N1.1`, `N1.2`, ...); `--obligations` lists proved obligations (`O#`); `--gate` names the satisfied host policy gate; `--answers` states the concrete question answered by this validation run.
- `--locator` points to an existing file under the host root; for non-file evidence (e.g., manual user verification or external URLs), prefix with `note:...` or `http(s)://...`. Missing evidence files on disk cause `dlm close` to fail with `UNKNOWN (I14)`.
- Unparsed, unloaded, or unexecuted items never count as `pass`.
- Use `--duration` (e.g., `12m`) to record validation cost; registering ≥ 3 full-scope validations for the same obligation triggers a signal—incremental edits should run targeted checks and direct regressions unless a new full-run question is stated.

## Decisions (`D`)

- User decisions (`--source user`) must record the verbatim user quote (`--quote`) and session/date context (`--session`); executor decisions (`--source executor`) record self-contained technical choices within existing authorization.
- When a new decision replaces an earlier one, pass `--supersedes D#`; list affected obligations or tasks in `--affects` so `dlm brief` surfaces the active decision automatically.
- Record material decisions made during conversation immediately via `dlm decide`—never leave them buried in chat history or prose logs.

## Hypotheses (`H`) & Artifacts (`A`)

- Record unverified root-cause claims or "module X lacks capability Y" assumptions as hypotheses (`H`); update them via `dlm hypo --set H# --status confirmed|refuted --evidence <locator>` once tested.
- Task prerequisites (`--needs`) may only reference artifacts (`A#` or `Exxx/Ay`), never hypotheses. Advance artifact states as work progresses (`planned → contract → implemented → verified`).

## Limits (`L`)

| `kind` | Meaning | Enforcement Rule |
|---|---|---|
| `product` | User-accepted product boundary or scope limit | Must reference a user decision (`--basis D#`) |
| `budget` | Executor-chosen memory, size, timeout, or sample budget | `on_exceed` may only be `chunk`, `degrade-with-record`, or `unmet`—never `reject` valid business inputs |
| `resource-fact` | Verified hardware, OS, or representation constraint | Must state the factual basis (`--basis`) |

When an implementation budget prevents achieving the target outcome, keep the obligation unfulfilled (`open` / `unmet`) rather than shrinking the goal.

## Reopen Causes (`dlm reopen --cause`)

| Cause | When to Use | Deterministic Tool Effect |
|---|---|---|
| `regression` | A subsequent change broke a previously verified outcome | Marks prior evidence `invalidated`, resets obligation to `open`, and reassigns `owner` to the task that introduced the regression (`--by S#`) |
| `omission` | Original requirement was missed or completion claim exceeded evidence | Marks prior evidence `overclaimed`, resets obligation to `open`, and returns the owning `done` task to `active` |
| `new-requirement` | User added or changed a requirement | Creates a new obligation (requires `--decision D#`, `--text`, and `--owner`); prior obligations and evidence retain their original validity |
| `superseded` | Old contract was explicitly replaced by the user | Marks old obligation `superseded` (requires `--decision D#`) and optionally creates its replacement obligation |
| `evidence-lost` | Evidence file was lost, expired, or cannot be re-verified in the current environment | Marks evidence `unverifiable` and obligation `stale`; **does not reopen the completed task or presume implementation failure** |

Always identify the specific affected obligation and factual basis when reopening; shared file paths, reference links, or unfinished downstream tasks never justify cascading reopens on their own.
