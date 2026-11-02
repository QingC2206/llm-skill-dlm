---
name: dlm-review
description: Independently review results against the user's goal when requested, required by an accepted contract, or needed for a material risk; for complete, deep or parity reviews, inventory the full object set first and account for every item.
---

# DLM Review

Evaluate whether work results genuinely satisfy the user's goal, identifying material defects, omissions, and completion claims that exceed their evidence. Review alone does not grant write authorization; when reviewing within an active implementation task, fix confirmed issues within your existing authorized write scope.

**Output language (`<knowledge_dir>/`)**: When recording review findings, decisions, or reopen notes into `<knowledge_dir>/` (`docs-llm/` by default, or `doc_llm/`), write them in **Chinese (`zh-CN`)** unless the host configures another locale.

## Gathering Evidence

- **When a v2 Epic exists**: Run `python scripts/dlm.py check <E>` for structural facts and `python scripts/dlm.py brief <E> <S>` for obligations, decisions, prerequisites, and evidence locators. Passing `dlm check` only proves structural validity, not semantic correctness.
- **When no task record exists**: Inspect code, contracts, and runtime evidence directly. A user request to review is sufficient to start—never demand pre-named suspects or create a task just to run a review.
- Reuse valid direct evidence; run additional checks only for new changes, failures, or unresolved risks. Scale review depth to failure impact, blast radius, and uncertainty.

## Complete, Deep, or Parity Reviews

When the user asks for a comprehensive, complete, deep, 1:1 parity, or non-degradation review, first define and mechanically enumerate the full object or mechanism set (see [Inventory Routing in dlm-task](../dlm-task/references/inventory.md)), then account for every item with evidence. Unparsed, unloaded, or unexecuted items never count as `pass`; sample-based conclusions apply only to the sampled subset.

## What to Evaluate

| Dimension | Questions to Ask |
|---|---|
| Goal vs. Proxy Substitution | Do obligations state user-observable outcomes, and does the evidence prove those exact outcomes rather than compile/mock proxies? |
| Evidence Scope Fidelity | Is the `--scope` label (`full` / `partial` / `sample` / `proxy`) honest? Did the test enter the declared inputs, payloads, state transitions, and production code path? |
| Partial vs. Whole Delivery | Are staged deliverables and end-to-end behavior distinguished? Does every unfulfilled mandatory obligation have a named receiving task? |
| Attribution & Ownership | Were failures traced along the active call chain, adapter layer, and data owner? Was plugin- or scenario-specific policy pushed into a shared public layer? |
| Limits vs. Budgets | Does a hard cap come from a user-confirmed product decision (`product`), a verified `resource-fact`, or an executor's implementation `budget`? |
| Change Classification | Are `regression`, `omission`, `new-requirement`, `evidence-lost`, and `superseded` properly separated (see [records.md](../dlm-task/references/records.md))? |
| Inventory Quality | Do mechanical sources cover the full set? Are manually added items (`manual=true`) and blind spots explicitly explained? |

Separate confirmed defects, unverified risks, and optional improvements; personal style preferences and out-of-scope optimizations are never blocking findings.

## Independent Context Review

Whether a separate reviewer context is needed is decided by the coordinator based on task risk and host capabilities. When delegating an independent review, prefer a clean context that does not inherit the implementation conversation history (e.g., equivalent to `fork_turns: "none"` when supported). Provide the reviewer with the user's original goal, acceptance criteria, confirmed trade-offs, actual changed paths, evidence locators, and this skill; the reviewer must verify facts directly rather than trusting "completed" summaries. A delegated reviewer completes the review directly without sub-delegating further.

## Delivery & Stopping

State the conclusion, the factual basis and impact of material findings, and any remaining uncertainty or required action that affects the completion verdict. Report item counts (`总数 (total) / pass / fail / gap / na / blocked / pending`) whenever an inventory is used. When findings require changing a task's completion state, use `dlm reopen --cause ...` (executed by the agent holding write authorization). Stop once the review questions are answered.
