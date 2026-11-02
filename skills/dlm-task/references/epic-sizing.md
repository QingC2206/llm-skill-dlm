# Epic Granularity & Boundaries

This reference defines how to size Epics and Subtasks cleanly from first principles, without relying on host-specific historical examples.

## Definitions

| Level | What It Is | Target Scale (Host-Configurable) |
|---|---|---|
| **Epic** | One **medium-sized, independently acceptable outcome**: directly observable by users or maintainers upon delivery, with its own scope, principles, and acceptance criteria; **its delivery remains valid and useful even if no subsequent Epic is ever started** | Typically 3–10 subtasks; ≤ 6 concrete principles tailored to this outcome |
| **Subtask** | One **concrete advancement outcome**: clear inputs, outputs, write boundary, positive/negative verification, and stopping line; implementation, direct verification, and fixes close within the same task | One feedback loop |
| **Not an Epic** | Product roadmaps, phases ("Infrastructure", "Group 1", "Phase 2"), horizontal technical layers ("Common Types", "Build System", "SDK Layer"), or the entire implementation of a major release | — |
| **Does Not Need an Epic** | Localized fixes or enhancements that close in a single feedback loop, or incremental work inside an existing Epic | — |

## Relations Between Epics

1. **No prerequisite dependencies by default.** Ordering across Epics is advisory only, never a blocking wait.
2. **Foundational work belongs to its first consumer.** Shared types, build entrypoints, or minimal SDK surfaces are delivered by the first Epic that genuinely needs them, scoped to that Epic's minimum requirement. Subsequent Epics reference the resulting named artifact rather than waiting on a standalone "Foundation Epic".
3. **Only two relation kinds are permitted**:
   - `verifies`: a verification / end-to-end regression Epic pointing to the execution Epic(s) it verifies;
   - `artifact`: a dependency on a specific named artifact `Exxx/Ay` (with explicit contract/version state) produced by another Epic—never a blanket wait on an entire Epic.
4. **Every shared capability has a single owner.** Never duplicate shared implementations or hide real cross-module dependencies just to fake independence.
5. **Dedicated verification Epics can stand alone** (e.g., robustness suites or release regression), but they never absolve an implementation Epic from delivering its own direct functional and boundary verification.

## Planning Workflow

1. Start from user-observable outcomes, inspect existing owners and capabilities, and partition the work into candidate Epics by vertical outcome.
2. For each candidate Epic, state: outcome, scope and non-goals, Epic-level acceptance criteria, why it stands on its own, and relations to other candidates (`none` / `verifies` / `artifact:Exxx/Ay`).
3. Express the candidates as a draft JSON file and run `dlm plan-check --draft plan.json` (read-only). Present any granularity signals together with the candidate list to the user.
4. Create an Epic on disk only after the user explicitly confirms the chosen Epic and its concrete principles; then slice subtasks inside that Epic. (Write the Epic/Subtask titles, outcomes, and prose in Chinese for `<knowledge_dir>/`.)

### Draft JSON Format

```json
{
  "epics": [
    {
      "title": "Deliver standalone slicing to the validation client via SDK",
      "outcome": "Users obtain accurate slice images in the validation client through the public SDK",
      "acceptance": ["C/C# consumers of the installed package obtain accurate slice images and pass invalid-argument and disposal checks"],
      "relations": [],
      "tasks": [
        {"id": "S1", "title": "Geometry grid and sampling task", "obligations": ["Verify corners, start/end positions, and normals without intensity data"], "produces": ["sampling-task"]},
        {"id": "S2", "title": "CPU resampler", "obligations": ["Interpolation, sentinel values, and out-of-bounds behavior match V1 baseline"], "needs": ["sampling-task"], "produces": ["resampler"]},
        {"id": "S3", "title": "First end-to-end SDK slice path", "obligations": ["C ABI and C# disposal contract pass"], "needs": ["resampler"]}
      ]
    },
    {
      "title": "Release package and end-to-end regression",
      "outcome": "Supported routes pass full regression on the packaged build",
      "acceptance": ["Clean-environment package installation passes protocol comparison"],
      "relations": [{"kind": "verifies", "target": "Deliver standalone slicing to the validation client via SDK"}],
      "tasks": [{"id": "R1", "title": "End-to-end regression", "obligations": ["Real datasets and perturbations pass protocol comparison"]}]
    }
  ]
}
```

Fields such as `depends_on`, `after`, or `blocked_by` are rejected (`E2`); fields such as `group`, `phase`, or `stage` and phase-style titles ("Phase N", "Group N", "阶段", "第 N 组") trigger granularity signals.

### Granularity Signals

| Signal | What It Indicates | Typical Fix |
|---|---|---|
| Subtask count exceeds `max_subtasks` (default `10`) | Monolithic roadmap | Split into multiple independent Epics by vertical outcome |
| Task graph decomposes into disconnected components | Unrelated outcomes bundled into one Epic | Split each connected component into its own Epic |
| Dependency chain depth exceeds `max_chain_depth` (default `4`) | Staged waterfall pipeline | Deliver foundational pieces inside their first consumer to shorten the chain |
| Phase / group markers detected (`Phase N`, `Group N`, `Stage N`, `阶段`, `第 N 组`, `第 N 阶段`) | Organized by phase or horizontal layer | Re-organize around user-acceptable outcomes |
| External Epic artifact dependencies exceed `max_external_epics` (default `2`, counting task `needs` + Epic `relations`) | Outcome cannot be accepted independently | Fold minimum required foundation into this outcome, or merge into the true consumer |
| Majority of Epics each depend on ≥ 2 other Epics (dependency web) | Sliced horizontally by algorithm stage, technical layer, or deliverable kind | Re-slice vertically by end-to-end user-observable outcomes (see counterexample 1 below) |
| Only 1 subtask in the Epic | Too small for an Epic | Execute directly as localized work or as a subtask of an existing Epic |

Signals are advisory warnings rather than hard blocks; final Epic boundaries are decided with the user when reviewing candidates.

## Positive & Negative Examples (Host-Agnostic)

| Scenario | Anti-Pattern (Wrong Slicing) | Proper Slicing |
|---|---|---|
| **Horizontal layer / stage split across a dozen "medium" Epics** | Splitting a project into 13–14 Epics by algorithm stage and technical layer: Common Contracts, Coarse Partitioning, Model-A Localization, Model-B Localization, Generic Slice Planning, Inspection Scheme, Resampling, Pipeline Composition, SDK, Visual Workbench, Packaging, and Robustness—each with 4–6 subtasks so each looks "medium-sized" in isolation, while 13 of 14 Epics cross-reference each other's subtasks through a central "early support artifact" matrix and almost none can be accepted by the user on its own; plus a 34-subtask tutorial Epic | Slice each Epic as a vertical, user-acceptable outcome: e.g., "Deliver standalone coarse partitioning without regression" (including the common types, build, and I/O it needs), "Model-A localization reaches baseline equivalence via public entrypoint", "Explicit-grid slicing renders images through the public entrypoint", "Workbench runs partition → localize → slice on a volume", "Close new-product inspection spec and applicability verdict", and "Release package & full regression" (`verifies`). Fold per-capability robustness into each outcome's own acceptance, with `verifies` covering cross-route regression; split tutorials into smaller course modules |
| **New engine version implementation** | A single "V2 Implementation and Delivery" Epic containing ~25 tasks across 4 phase groups (Foundation, Slicing, Recognition, Planning/Release) chained end to end | Multiple independent Epics: "Standalone slicing to validation client via SDK" (owning its minimal build & common types), "Coarse partitioning migration & parity", "Route-A localization migration", "Remaining route migrations", "Inspection planning & full pipeline", "New-product spec closure & applicability", and "Release & full regression" (`verifies`). Ordering is advisory |
| **Cross-platform parity** | Three serial horizontal Epics: "Common Layer → Platform Layer → UI Layer" | Vertical Epics by capability domain, each independently acceptable and each starting with a two-sided reference/target inventory (`dlm inv`) |
| **Execution vs. verification** | Execution Epic stays open waiting for a separate verification Epic to finish | Execution Epic closes with its own direct verification; the verification Epic uses `verifies` to point to it |
| **Underspecified new requirement** | Scheduling a large "Implement new-product algorithm" Epic immediately | First run a spec-closure / applicability Epic or `investigation` task, then create implementation Epics from confirmed gaps |
| **Single localized fix** | Creating a new Epic for a one-off bug fix | Handle as localized work or a subtask under an existing Epic |
