# Ledger Schema & Deterministic Invariants

## Directory Layout

```text
<tasks>/E012_slug/
  ledger.json         # Single machine source of truth (schema dlm.ledger/1); mutated only via dlm CLI
  epic.md             # Chinese prose (## 背景与预期效果, ## 共享设计与取舍, ## 范围与非目标; or adopted v1 README)
  tasks/S001_slug.md  # Chinese prose (## 方案承接与实施设计, ## 关键路径与入口; adopted v1 tasks stay in subtasks/)
  journal.md          # Append-only command audit log; excluded from dlm brief
  README.md           # Generated Chinese view (with sha256 header; hand edits fail dlm check)
<tasks>/backlog.md    # v2 Epic index block maintained by dlm render
```

## Entities in `ledger.json`

| Prefix | Entity | Key Fields |
|---|---|---|
| — | `epic` | `id` `title` `outcome` `status: active\|paused\|done` `relations[{kind: verifies\|artifact, target}]` `extra_gates[]` |
| — | `boundary` | `write` `generated` `read_only` `forbidden` (globs; most specific rule wins; on ties `forbidden > read_only > generated > write`) |
| `P` | Principle | `text` `decision` |
| `D` | Decision | `source: user\|executor` `kind` `quote` `summary` `session` `supersedes[]` `affects[]` `basis[]` |
| `O` | Obligation | `text` `owner: S#\|epic` `coverage: single\|full\|sample` `population` `origin` `derives` `sample_decision` `preservers[]` `status: open\|transferred\|waived\|superseded` `transfer` `resolution` |
| `N` | Population | `name` `reference` `target` `sources[]` `items[{id: N#.#, key, side, status, evidence[], reason, decision, manual}]` `sample` |
| `A` | Artifact | `text` `producer` `state: planned\|contract\|implemented\|verified` `locator` `version` |
| `H` | Hypothesis | `claim` `status: unverified\|confirmed\|refuted` `evidence[]` |
| `L` | Limit | `what` `kind: product\|budget\|resource-fact` `basis` `on_exceed` |
| `V` | Evidence | `obligations[]` `items[]` `scope` `locator` `method` `answers` `gate` `duration` `task` `status: valid\|invalidated\|overclaimed\|unverifiable` |
| `J` | Justification | `task` `layer` `fields{}` `decision` |
| `S` | Subtask | `title` `file` `status: draft\|active\|blocked\|done\|dropped` `kind` `priority` `depth` `depth_reason` `needs[{artifact, state}]` `boundary` `baseline{}` `sources[{path, heading, intent}]` `checkpoint` `review` |

Obligation states `verified`, `implemented`, and `stale` are derived dynamically from `evidence` (`V`) and `populations` (`N`) rather than stored as static strings. Only `verified`, `waived`, and `superseded` count as terminal obligation states (`implemented` from `partial`/`sample`/`proxy` evidence is non-terminal).

## Deterministic Invariants

| Code | Invariant |
|---|---|
| `E1` | Epic has a non-blank `title`, observable `outcome`, and at least one `owner=epic` acceptance obligation |
| `E2` | Epic relations use only `verifies` (target Epic exists) or `artifact` (resolves to an existing named artifact `Exxx/Ay`) |
| `E3` | Deliverable subtasks own at least one obligation; `investigation` subtasks state non-blank `question` and `stop` |
| `I1` | Every ID reference resolves (including cross-Epic `Exxx/Ay`); required source files and Markdown headings exist |
| `I2` | Every obligation's `owner` is either `epic` or a subtask in the same Epic |
| `I3` | Obligations owned by a `done` subtask must be terminal, `transferred`, or `stale`; transfer targets must exist, be unfinished, and state an entry condition; `dropped` subtasks cannot retain active obligations |
| `I4` | `coverage=full` obligations bind to an existing population `N#`; `pass` items have evidence; obligation status is derived from item states; marking a reference-side item `na` in a full population requires a user decision |
| `I5` | A `done` Epic has all subtasks `done`/`dropped`, all obligations terminal, and no referenced `unverified` hypothesis |
| `I6` | Task prerequisites (`needs`) cannot reference hypotheses (`H#`); `scope` / `gate` / `limit` decisions cannot rely on unverified hypotheses |
| `I8` | Principles, waivers, supersessions, extra Epic gates, product limits, task drops, and sampling all reference a user decision (`source=user`) with a verbatim `quote` |
| `I9` | Subtask write/generated globs are subsets of Epic write/generated globs and are not overridden by more specific Epic `read_only` / `forbidden` rules; public-layer justifications include all policy-required fields and a user decision |
| `I10` | `product` limits cite a user decision; `budget` limits cannot set `on_exceed=reject`; `resource-fact` limits state a factual basis |
| `I12` | Every obligation has provenance (`origin D#` or `derives O#`); evidence gate names exist in host policy or Epic `extra_gates` |
| `I13` | Generated views (`README.md`) are not hand-edited; files marked `dlm: historical` cannot be cited as required sources |
| `I14` | Any `UNKNOWN` at a gate (unresolvable reference, missing evidence file on disk, unavailable git scope without `--changed`) is treated as a blocking failure |

Additional checks on `dlm close`: obligations transferred to the closing subtask have been accepted (`dlm accept`); evidence files proving owned obligations exist on disk; latest `checkpoint` is `aligned`; attributed scope has no boundary conflicts (unrelated git worktree changes outside the task's write/generated scope remain `unattributed`); triggered host/Epic gates have valid evidence; touched public layers have `dlm justify` records; and every principle and owned obligation has a non-blank review judgment.

## Advisory Signals (`SIGNAL` / `WARN`)

Subtask count over limit, disconnected task graph components, excessive dependency chain depth, phase/group markers, single-subtask Epic, excessive external Epic dependencies, cross-Epic dependency web (majority of Epics depending on ≥ 2 other Epics), single subtask spanning too many top-level module roots, single subtask owning too many obligations, duplicated prose paragraphs across files, accumulated dated log lines in task bodies, overly long task body, ≥ 3 full-scope validations on the same obligation, obligations derived from > 2 distinct decisions, executor decision used as obligation origin, and coverage trigger words used on a `single`-coverage obligation without a population.
