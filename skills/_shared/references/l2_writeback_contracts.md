# L2 Write-Back Contracts

Used by `dlm-knowledge` (`build` and `sediment` modes) when creating or updating `L2` module documents in `<knowledge_dir>/` (`docs-llm/` by default, or `doc_llm/`). Whether a finding is worth persisting depends on the current task, verified facts, and future consumers.

## Consumable Structure & Front-Matter

Every `L2` document must keep the YAML front-matter required by `dlm kb route` and `dlm kb check`. Write the document body and front-matter `status` in **Chinese (`zh-CN`)** per [`templates/L2_template.md`](../../dlm-knowledge/templates/L2_template.md); organize body sections around the module's actual subject matter rather than forcing rigid boilerplate headings.

```yaml
---
domain: "{domain_number}_{domain_key}"
module: "{module_key}"
status: "已实现"       # 已实现 / 部分实现 / 待实现 / 已废弃
last_updated: "YYYY-MM-DD"
match_globs:
  - "{glob}"
---
```

`match_globs` declares the source paths governed by this `L2` document (it does not grant write authorization). Because `match_globs` drives both `dlm kb sediment` candidate matching and `dlm kb stale` staleness detection, keep the globs as specific as possible. Whenever domain boundaries, `L2` front-matter entries, or `Reference` topic `README.md` entries (`kind: "reference_topic"`, `status: "已归档" | "部分归档" | "待补充" | "已废弃"`) change, run `dlm kb route <domain_dir>` to recompile the `L1` router under its required Chinese headings (`## 二、L2 路由表` and `## 三、参考资料路由`).

## Active Truth vs. Historical Notes

The `L2` body must state the **currently active** mechanisms, boundaries, state machines, trade-offs, and non-obvious pitfalls. When existing statements become obsolete, update them in place—along with any affected exceptions or caller notes—and bump `last_updated`.

Implementation, verification, or investigation can all produce reusable facts. Distinguish actual implementation behavior from contract requirements; never write an unverified root-cause hypothesis as a settled mechanism.

Retain historical evolution notes only when they explain current design trade-offs, compatibility redlines, or required provenance (e.g., the version cutoff, rationale, and caller impact of a breaking change). Never append chronological logs at the bottom of an `L2` file as a substitute for updating the active body.

## Layer Placement & Scope Boundaries

- Raw API tables, vendor specs, and historical baseline docs belong in `Reference`; `L2` keeps project-internal conclusions and links to the `Reference` topic.
- Reusable scenario procedures belong in `SOP`; active task progress and open proposals stay in the task ledger (`ledger.json`).
- Handle transient logs and fixtures according to the host's cache policy (`out/`)—never create dummy tasks or archive copies just to clean up low-value notes.
- Use `build` mode when merging, splitting, or restructuring knowledge across modules; obtain explicit user approval before writing outside your authorized scope.
