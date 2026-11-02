# Knowledge Base (`docs-llm/` / `doc_llm/`) Writing Rules

Applies to all documentation and task prose written into `<knowledge_dir>/` (`docs-llm/` by default, or `doc_llm/`).

- **Language**: Write all `<knowledge_dir>/` documents and task prose in **Chinese (`zh-CN`)** unless the host explicitly overrides `locale` in `dlm.config.json`. Keep code identifiers, file paths, CLI flags, and standard technical acronyms in their exact original form.
- **Goal**: Maintain a **professional, direct, and rigorous** style that maximizes information density and clarity.
- **No emotional or figurative rhetoric**: Do not use metaphors, personification, dramatic framing, or exaggerated analogies.
- **No artificial jargon**: Avoid coined buzzwords or unnecessarily pompous phrasing that obscures concrete engineering facts.
- **Use standard engineering terminology**: Prefer exact domain terms (e.g., `浅拷贝`, `生命周期`, `线程竞态`, `内存对齐`, `P/Invoke 越界`).
- **Target form**: Every document should read like a clean, deterministic technical memo rather than an essay.
