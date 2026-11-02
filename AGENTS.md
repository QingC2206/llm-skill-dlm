# DLM package maintenance

This repository owns a reusable DLM skill pack. Keep the sibling layout under `skills/`: the five `dlm-*` skills share `_shared/` and resolve templates, locales and lease tools relative to it.

- Read the relevant skill and [pack governance](skills/_shared/references/dlm_skill_pack_governance.md) when changing behavior. Host-specific paths, policies, knowledge and task records belong in consuming repositories.
- `install.py` copies the complete package to a host. Preserve host-owned files and report conflicts before writing; keep installation repeatable and independent of the source checkout.
- Validate changes with `python -X utf8 scripts/test_all.py`. Keep retained artifacts under `out/`; bundled no-Git tests use automatically cleaned system temporary directories so an ancestor checkout cannot affect their assertions.
- Record verified integration changes in `CHANGELOG.md`; shared-runtime changes also follow the pack's own changelog convention.
