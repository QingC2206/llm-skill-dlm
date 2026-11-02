# DLM Skill Pack Governance

This reference is for maintainers of the DLM skill pack. Runtime authorization, execution, and stopping always follow user instructions and host repository rules.

## Skill Responsibilities

| Skill | Responsibility |
|---|---|
| `dlm-task` | Task planning, execution, checkpoints, and closure; inventory routing (`dlm inv`); Epic granularity |
| `dlm-review` | Goal-oriented independent review; inventory-first verification for complete/parity reviews |
| `dlm-load` | On-demand loading of task briefs or owner knowledge |
| `dlm-knowledge` | Knowledge base `build`, `sediment` (write-back), and `audit` |
| `dlm-multi-llm-lease` | User-orchestrated shared claim/lease queue across decoupled modules following a unified SOP |

There is no mandatory pipeline chaining the skills together; when switching skills, carry over the active goal, authorization, write boundaries, and valid evidence.

## Language Split: English Prompts, Chinese `<knowledge_dir>/` Output

- **Skill prompts & references (`SKILL.md`, `references/*.md`, `_shared/README.md`)**: Authored in clear, concise **English** so LLMs follow structural rules and judgment rubrics with high precision.
- **Generated views & scaffolds (`_shared/locales/zh-CN.json`, `dlm-knowledge/templates/*.md`)**: Authored in **Chinese (`zh-CN`)** by default so all generated and scaffolded files under `<knowledge_dir>/` (`docs-llm/` by default, or `doc_llm/`: `BRIEF.md`, `L0`, `L1`, `L2`, `Reference`, `SOP`, `README.md`, `backlog.md`, `epic.md`, `tasks/Sxxx.md`, and `dlm brief`) remain in Chinese.

## Hard Constraints vs. Soft Judgment

- **Enforced in Python scripts** (deterministic facts): ID uniqueness and reference resolution, state transitions, population coverage counts, user decision quote provenance, write-boundary subset/conflict checks, gate evidence presence, generated-view tamper detection, and treating `UNKNOWN` as failure at gates. Every structural rule is implemented once in `dlm_invariants.py` (and `cmd_close`) and shared across all commands.
- **Guided by skill prompts** (semantic judgment): Translating user goals into observable obligations, sizing Epics and subtasks, separating confirmed facts from hypotheses, verifying ownership boundaries, judging whether evidence genuinely exercised the production path, and formulating the question each validation run answers.
- Each rule has a single authoritative home. Prompts state *what the tool rejects* and *what you must judge* without repeating mechanical validation code in prose.

## Modifying the Pack

- When adding a rule, ask first: *Is it mechanically decidable?* If yes, implement it in `dlm_invariants.py` / `dlm_scope.py` with both rejection and acceptance unit tests in `test_dlm_v2.py`; if no, add it concisely to the relevant skill's judgment section.
- Do not introduce new mandatory ceremony stages, approval gates, standalone reports, or review loops unless explicitly requested by the user.
- Never embed host-specific paths, project names, or host gate names inside `.agents/skills/{dlm-*,_shared}`; express all host differences through `dlm.config.json` and `dlm.policy.json`. `Portability.test_pack_has_no_host_tokens` enforces this invariant.
- Keep `SKILL.md`, `references/*.md`, `templates/*.md`, and CLI behavior aligned, and record pack changes in `_shared/README.md`.
