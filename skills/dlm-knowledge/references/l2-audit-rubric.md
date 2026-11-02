# L2 Placement & Audit Value Rubric

Use this reference when judging layer placement, merging, splitting, or retention during `dlm-knowledge` (`build`, `sediment`, or `audit`). Consult only the points relevant to the question at hand; do not produce a checklist report unless requested.

## Who Each Layer Serves

| Content Type | Proper Layer | Key Judgment Criterion |
|---|---|---|
| Stable module mechanisms, state machines, lifecycles, boundaries, non-obvious pitfalls, and design trade-offs | `L2` | Does a future consumer working on this module need this conclusion directly? |
| Verbatim external specs, historical baselines, large parameter tables, or cross-module reference facts | `Domain Reference` | Does it require source fidelity, versioning, provenance, and topic-based drill-down? |
| Reusable step-by-step operational procedures for recurring scenarios | `SOP` | Does it answer "how to execute this workflow when scenario X occurs"? |
| Current task goals, working hypotheses, completion facts, and resumption checkpoints | `Task` (`ledger.json` / `tasks/`) | Does it serve only the current task and its audit trail? |
| Raw execution traces, temporary fixtures, or repeated polling outputs | Host cache directory (`out/`) | Is it transient execution evidence rather than reusable domain knowledge? |

`L2` retains stable project-internal conclusions and links to raw sources in `Reference`. Shared vocabulary alone does not justify merging separate modules, nor should `Reference` be used as a dumping ground for bloated `L2` prose.

## Common Audit Misjudgments

- **High lexical similarity**: The same upstream constraint may be consumed differently by distinct callers. Compare actual responsibilities, entrypoints, and `match_globs` to check for contract divergence before extracting a shared source.
- **Short file length**: A simple module may already express everything a consumer needs. Expand a short `L2` only when placeholders remain or missing facts would mislead an executor.
- **Long file length**: Check whether consumers are forced to load large blocks of unrelated material. Never delete complete state machines or compatibility redlines merely to hit a line-count target.
- **Accumulated evolution logs**: Fold settled conclusions into the main prose and keep only historical notes that still explain current trade-offs or compatibility boundaries. Never resolve a contradiction between old and new behavior by appending a "correction" note at the bottom of the file.
- **Discrepancy between source code and docs**: Source code proves current implementation behavior, whereas an accepted contract may prove the implementation has a bug. Pinpoint the discrepancy before overwriting either side as the sole truth.
- **Repeated stylistic polishing**: When there are no new facts, factual errors, or navigation obstacles, keep the existing prose and stop polishing.

## Choosing the Right Fix

Fix localized factual errors or outdated statements in place. Consider splitting when a single `L2` mixes multiple independent consumers; consider consolidating when multiple files duplicate the same contract and risk drifting apart. After any split or merge, update affected `L1` route tables (`dlm kb route`), markdown links, and `match_globs`.

Cross-domain restructuring belongs to `build` mode; if it requires multi-session coordination, track the scope and obligations in a task. Stay within your existing authorized write scope, and request explicit user approval before expanding write boundaries.

Preserve valuable decisions and essential evidence locators before cleaning up changelog bloat. Never create dummy tasks, duplicate logs, or build archive bundles just to delete low-value prose.

## Good-Enough Stopping Point

Focus audit conclusions on confirmed defects, concrete navigation/comprehension gains, and information-loss risks. When no material defect exists, state directly—with brief evidence—that no adjustment is needed, without enumerating every `kb map` or `kb stale` candidate.

Once authorized updates are complete, verify that current prose is accurate (in Chinese per [`doc_llm_writing_rules.md`](../../_shared/references/doc_llm_writing_rules.md)), essential knowledge is preserved, and affected `L1` routes resolve cleanly (`dlm kb check`), then stop.
