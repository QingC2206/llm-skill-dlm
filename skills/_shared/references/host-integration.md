# Host Integration

Place the DLM skills (`dlm-task`, `dlm-review`, `dlm-load`, `dlm-knowledge`, `dlm-multi-llm-lease`) alongside `_shared/` inside the host repository's skills directory. The pack depends only on the Python 3.10+ standard library and `git` (when `git` is unavailable, changed-path scope reports `UNKNOWN` unless `--changed` is provided).

## Host Root Discovery & Directory Layout

`dlm` locates the host root in the following order: environment variable `DLM_PROJECT_ROOT` → upward search for `dlm.config.json` → upward search for any existing known knowledge directory (`docs-llm/`, `doc_llm/`, or `docs_llm/`).

Within the resolved host root, `dlm` resolves the active knowledge directory (`<knowledge_dir>`) in priority order:
1. Explicit `"knowledge_dir"` in `dlm.config.json`
2. `DLM_DOC_LLM_DIR` environment variable
3. Existing directory on disk among `docs-llm/`, `doc_llm/`, and `docs_llm/`
4. Default **`docs-llm`** (`DEFAULT_KNOWLEDGE_DIR = "docs-llm"`)

Host discovery must succeed before `kb scaffold init-project` can run. From the `llm-skill-dlm` source checkout root, `python -X utf8 install.py --host "<host-root>"` installs the complete pack and creates `dlm.config.json` if absent, so the installed CLI can initialize an empty host.

For a manual copy into a brand-new host's `.agents/skills/`, first create `docs-llm/`, then initialize the knowledge base. Run both commands from that host's root:

```bash
mkdir docs-llm
python -X utf8 .agents/skills/dlm-knowledge/scripts/dlm.py kb scaffold init-project "Project title"
```

An existing `dlm.config.json` or an explicit `DLM_PROJECT_ROOT` also supplies the host context. For direct use without installing, run `python -X utf8 dlm.py --root "<host-root>" kb scaffold init-project "Project title"` from the source checkout root; `--root` belongs to that checkout wrapper.

Existing repositories that use non-default `doc_llm/` or `docs_llm/` are auto-detected without requiring config changes, or can pin `"knowledge_dir"` explicitly in `dlm.config.json`.

Optional `dlm.config.json` at the host root (showing default values):

```json
{
  "knowledge_dir": "docs-llm",
  "tasks_dir": "docs-llm/Tasks",
  "cache_dir": "out",
  "locale": "zh-CN",
  "policy": "docs-llm/dlm.policy.json"
}
```

`locale` controls the language of generated views (`README.md`, `backlog.md`, `epic.md`, `tasks/Sxxx.md`, `dlm brief`). It defaults to **`zh-CN`** so `<knowledge_dir>/` outputs (`docs-llm/` by default, or `doc_llm/`) are generated in Chinese; `en` is also built in.

## Host Policy (Optional)

Default location: `<knowledge_dir>/dlm.policy.json`. Missing fields fall back to:

```json
{
  "public_layers": [],
  "public_layer_justification": ["phenomenon", "why_here", "files", "how", "risk", "boundary"],
  "gates": [],
  "inventory_triggers_extra": [],
  "epic": {
    "max_subtasks": 10,
    "max_chain_depth": 4,
    "max_external_epics": 2,
    "max_task_owner_roots": 3,
    "max_task_obligations": 5,
    "max_task_body_lines": 200,
    "max_dated_lines": 12
  }
}
```

- `public_layers`: Glob patterns for shared/public layers. When a task's attributed changes match a public layer, `dlm close` requires a `dlm justify` record backed by a user decision.
- `gates`: `[{"name": "...", "paths": ["glob/**"], "command": "how to run (informational)"}]`. When a task's attributed changes match `paths`, `dlm close` requires valid gate evidence (`dlm evidence --gate <name>`).
- `inventory_triggers_extra`: Additional host-specific coverage/parity trigger keywords.

## Host Rule Snippet (Optional)

The skill descriptions in `SKILL.md` are sufficient to trigger DLM automatically. If a host repository wishes to reference DLM explicitly in its root `AGENTS.md`, include the following points:

- Maintain persistent tasks through the `dlm` CLI; task states transition only via `dlm` commands, end each turn with `dlm checkpoint`, and treat passing `dlm close` as a necessary (not sufficient) condition for completion.
- Creating an Epic, changing principles, expanding write boundaries, waiving/superseding obligations, adding Epic gates, or replacing full coverage with sampling all require an explicit user decision (`dlm decide --source user`).
- Size each Epic as one medium-sized, independently acceptable outcome; Epics have no prerequisite dependencies by default (only `verifies` and named `artifact` relations).
- When the user uses coverage/parity keywords or names a concrete modification subject, enumerate the full object set via `dlm inv` first, execute item by item, and report status counts in the final response.

## Historical (v1) Records

Existing v1 task directories remain read-only: `dlm legacy check` reports findings without blocking, and `dlm adopt` mechanically imports a v1 Epic when persistent resumption is required.
