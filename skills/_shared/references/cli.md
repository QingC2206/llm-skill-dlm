# `dlm` CLI Reference

Single entrypoint: run `python scripts/dlm.py <command>` from any DLM skill directory (all forward to `_shared/scripts/dlm.py`). Pass the global flag `--dry-run` before the subcommand to validate and preview without writing to disk.

Exit codes: `0` pass; `1` deterministic invariant failure or `UNKNOWN` at a gate; `2` invalid arguments or target.

Every mutating command runs the shared invariant suite first (see [ledger.md](ledger.md)); any mutation that introduces a new blocking violation is refused. Successful writes automatically regenerate the Epic `README.md` and `backlog.md` block using the host locale (`zh-CN` by default).

## Epic Commands

| Command | Purpose |
|---|---|
| `epic create <title> --outcome T --acceptance T... --principle T... --confirmation "<verbatim user quote>" [--session S] [--write G...] [--generated G...] [--read-only G...] [--forbidden G...] [--relation verifies:Exxx\|artifact:Exxx/Ay] [--background T] [--design T] [--scope T]` | Create an Epic; concrete principles, verbatim user confirmation quote, observable outcome, and Epic-level acceptance are all mandatory |
| `epic relate <E> --verifies Exxx \| --artifact Exxx/Ay [--note T]` | Register an Epic relation (`verifies` or named `artifact`) |
| `epic boundary <E> [--add-write G] [--add-generated G] [--add-read-only G] [--add-forbidden G] [--remove G] [--decision D#]` | Update Epic write boundary; expanding write/generated scope or removing restrictions requires a user decision |
| `epic gate <E> --name N [--command C] --decision D#` | Add an Epic-level validation gate (requires user decision) |
| `epic status <E> --set active\|paused [--note T]` | Pause or resume an Epic |
| `epic close <E>` | Close an Epic once all subtasks are `done`/`dropped`, all obligations are terminal, and no referenced hypothesis remains `unverified` |

## Subtask Commands

| Command | Purpose |
|---|---|
| `task create <E> <title> --depth Ultra\|Max\|High\|Medium\|Lite --depth-reason T [--oblige T...] [--owns O#...] [--coverage single\|full\|sample] [--population N#] [--origin D# \| --derives O#] [--needs A#[:state]...] [--write G...] [--generated G...] [--source path[#heading]::intent...] [--kind deliver\|investigation --question T --stop T] [--priority P#] [--design T]` | Create a subtask and its Chinese markdown body file |
| `task edit <E> <S> [...]` | Edit title, priority, depth, prerequisites, write boundary, or required sources |
| `task start <E> <S>` | Verify prerequisite artifacts; if ready, set status to `active`, capture the in-scope git baseline, and print the task brief; otherwise set `blocked` and exit `1` |
| `task block <E> <S> --on T` | Mark a subtask `blocked` on a specific missing prerequisite or external blocker |
| `task drop <E> <S> --decision D# [--reason T]` | Drop an obsolete subtask per a user decision; all owned obligations must first be `superseded`, `waived`, or `transferred` |

## Semantic Object Commands

| Command | Purpose |
|---|---|
| `decide <E> --source user\|executor --kind K [--quote T] [--summary T] [--session S] [--supersedes D#] [--affects ID...] [--basis H#...]` | Record a decision; `user` decisions require `--quote` |
| `principle <E> (--text T \| --edit P# --text T \| --remove P#) --decision D#` | Add, edit, or remove an Epic principle (requires user decision) |
| `oblige <E> <text> --owner S#\|epic (--origin D# \| --derives O#) [--coverage ...] [--population N#] [--sample-decision D#] [--preservers S#...]` | Register an obligation (provenance `--origin` or `--derives` is mandatory) |
| `obligation <E> <O#> [--text T] [--owner S#] [--coverage C] [--population N#] [--derives O#] [--add-preserver S#] [--decision D#]` | Edit an obligation; rewriting text, downgrading `full` coverage, or swapping `--population` requires a user decision |
| `artifact <E> --text T [--producer S#] [--state S] [--locator L]` / `artifact <E> --set A# --state S` | Register or advance a named artifact (`planned → contract → implemented → verified`) |
| `hypo <E> --claim T` / `hypo <E> --set H# --status confirmed\|refuted --evidence L` | Register or resolve a hypothesis |
| `limit <E> <what> --kind product\|budget\|resource-fact --on-exceed chunk\|degrade-with-record\|unmet\|reject [--basis D#\|T]` | Register a product limit, implementation budget, or resource fact |
| `evidence <E> --scope full\|partial\|sample\|proxy --locator L [--obligations O#...] [--items N#.#...] [--item-status S] [--gate N] [--task S#] [--answers T] [--method T] [--duration T]` | Register validation evidence |
| `transfer <E> <O#> --to S# --entry T` / `accept <E> <O#>` | Transfer an unfulfilled obligation to a named unfinished task / accept a transferred obligation |
| `waive <E> <O#> --decision D#` / `supersede <E> <O#> --decision D# [--text T --owner S#]` | Waive or supersede an obligation (requires user decision) |
| `reopen <E> <O#> --cause regression\|omission\|new-requirement\|superseded\|evidence-lost [--by S#] [--evidence V#] [--items N#.#] [--decision D#] [--text T --owner S#] [--note T]` | Reopen or reclassify an obligation by deterministic cause |
| `justify <E> <S#> --layer G --field name=value... --decision D#` | Record public-layer responsibility justification (required fields defined by host policy) |

## Checkpoints & Views

| Command | Purpose |
|---|---|
| `checkpoint <E> <S> --verdict aligned\|deviated\|pending [--note T] [--changed P...]` | End-of-turn check (`--note` is mandatory when `deviated` or `pending`); records obligation deltas and attributed scope |
| `close <E> <S> [--line ID=judgment...] [--review file.json] [--changed P...] [--print-checklist]` | Close a subtask once obligations are terminal/transferred, latest checkpoint is `aligned`, scope has no conflicts, triggered gates and public layers are satisfied, and every checklist item (`P#`, owned `O#`, plus `Q` for `investigation` tasks) has a judgment |
| `brief <E> <S>` | Render the self-contained task brief |
| `scope <E> <S> [--changed P...]` | Compute attributed changes, boundary conflicts, and triggered gates |
| `plan-check <E>` / `plan-check --draft plan.json` | Run structural invariants and Epic granularity signals (`--draft` is read-only) |
| `check [E...]` | Read-only check of all or specified v2 Epics (including hand-edit detection on generated views) |
| `render [E...] [--all]` | Regenerate Epic `README.md` and `backlog.md` views |

Changed paths are collected from `git status` across the host root and nested git repositories; in-scope files unchanged since the task's `baseline` are excluded. Unrelated `git` worktree changes outside the task's `write`/`generated` scope are reported as `unattributed` and do not block `close`. `--changed` adds explicit paths (and serves as the sole source when `git` is unavailable; passing `--changed` with no values declares zero changed files).

## Inventory (`dlm inv`)

| Command | Purpose |
|---|---|
| `inv new <name> --reference T [--target T] [--epic E] [--items a,b] [--sample-quote T \| --sample-decision D#]` | Create a standalone or Epic-bound inventory |
| `inv source <name> [--epic E] --side ref\|target --kind glob\|grep\|cmd\|list --spec S [--in G] [--key REGEX]` | Enumerate items mechanically from a source |
| `inv add <name> <key...> [--epic E] --reason T [--side ...]` | Add manual items (flagged `manual=true` for review) |
| `inv diff <name> [--epic E]` | Compute parity diff between `ref` and `target` |
| `inv mark <name> [--epic E] (--ids 1,2 \| --filter REGEX) --status pass\|fail\|gap\|na\|blocked [--evidence L] [--reason T] [--decision D#]` | Update item statuses; marking a reference item `na` in a `coverage=full` population requires a user decision |
| `inv report <name> [--epic E] [--gate] [--limit N]` | Print status counts and unclosed items; `--gate` exits `1` if not closed |
| `inv export-lease <name> [--epic E] [--work-items P] [--runtime P] [--task S###] [--sop-ref P] [--priority-order A,B,C] [--priority-rule TIER=REGEX...] [--remark KEY=TEXT...]` | Export an inventory population (`N#`) into `work_items.md` (with mandatory `备注` column) and `work_item_runtime.json` for `dlm-multi-llm-lease` |
| `inv sync-lease <name> [--epic E] [--work-items P] [--runtime P] [--evidence L] [--require-zero-active] [--gate] [--task S###]` | Sync completed/blocked/reopened states from `work_items.md` back into inventory population `N#` and register `V#` evidence |

## Knowledge Base & Legacy

| Command | Purpose |
|---|---|
| `kb check [paths...] [--json]` | Validate knowledge base structure (does not check task records) |
| `kb route (<domain_dir>... \| --all) [--preview]` | Compile `L1` route tables from `L2` and `Reference` YAML front-matter |
| `kb scaffold init-project <title> \| init-domain <domain_key> [--domain-title T] [--root-path P] \| create-l2 <domain_key> <module_key> [--module-title T] [--match-glob G...] [--status S] \| create-reference <domain_key> <topic_key> [...] \| create-sop <sop_key> [...] [--dry-run] [--force] [--json]` | Generate Chinese `<knowledge_dir>/` (`docs-llm/` by default, or `doc_llm/`) scaffolds from `dlm-knowledge/templates/` |
| `kb map <target> [--json]` | Report `L2` size, changelog-bloat, layer-leak, and overlap signals |
| `kb stale [domain]` | List `L2` docs whose matched source files changed after `last_updated` |
| `kb sediment [paths...]` | List candidate `L2`/`Reference`/`SOP` docs matching changed paths |
| `legacy check <path> [--strict]` | Read-only validation report for v1 task records (non-blocking by default) |
| `adopt <E> [--outcome T] [--acceptance T...] [--confirmation T]` | Mechanically import a resumed v1 Epic into a v2 `ledger.json` (renames v1 `README.md` to `epic.md`, generates v2 `README.md`, and preserves existing `subtasks/Sxxx.md` files) |
