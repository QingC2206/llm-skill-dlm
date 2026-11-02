# Inventory Routing: Enumerate Full Sets Before Item-by-Item Execution

## Triggers

- **Coverage / parity keywords**: `全面`, `深度`, `完整`, `全量`, `全部`, `所有`, `逐项`, `一比一`, `1:1`, `对齐`, `匹配`, `无遗漏`, `不要遗漏`, `彻底`, `无劣化`, `完整复审`, `comprehensive`, `exhaustive`, `all items`, `every`, `parity`, `one-to-one`, `full coverage` (hosts can append extra triggers via `inventory_triggers_extra` in `dlm.policy.json`).
- **Concrete modification subject**: The user names a specific collection of objects to update or align—for example, "align platform B features 1:1 with platform A", "update configuration pages across all plugins", or "migrate all callers of module X".

Focused, localized edits do not trigger inventory routing.

## Execution Protocol

1. **Define the population**: Identify the `reference` set and, for parity work, the `target` set. Ask the user only if the population definition itself is materially ambiguous (e.g., which baseline version defines the reference).
2. **Enumerate mechanically**: Populate items from authoritative machine-readable sources (registries, command tables, menu definitions, CLI listings, plugin manifests, test suites, call-site greps)—never from memory. Record every source via `dlm inv source`. If any item cannot be captured by a mechanical source, add it via `dlm inv add --reason`, which marks `manual=true` for review scrutiny.
3. **Execute immediately**: Start working through the enumerated items right after inventory creation; do not pause to ask for permission to execute the inventory.
4. **Close item by item**: Every item must reach `pass`, `fail`, `gap`, `na`, or `blocked`. Marking `pass` requires an evidence locator covering that item; marking `na` requires `--reason`. In a population bound to a `coverage=full` obligation, marking a reference-side item `na` also requires a user decision (`--decision D#`), and `inv report` lists every `na` item with its reason.
5. **Sampling requires user authorization**: Replacing full coverage with sampling requires a user decision (`--sample-quote` for standalone inventories, `--sample-decision D#` for Epic populations); conclusions apply strictly to the sampled subset.
6. **Report status counts**: Include `总数 (total) / pass / fail / gap / na / blocked / pending` counts in the final response. Never claim completion or parity when `pending`, `fail`, or `gap` is non-zero.

## CLI Commands

```bash
python scripts/dlm.py inv new <name> --reference "<reference>" [--target "<target>"] [--epic E012] [--items a,b,c]
python scripts/dlm.py inv source <name> [--epic E012] --side ref --kind glob --spec "src/plugins/*/plugin.json"
python scripts/dlm.py inv source <name> [--epic E012] --side ref --kind grep --spec "register\(\"(\w+)\"" --in "src/**/*.cs"
python scripts/dlm.py inv source <name> [--epic E012] --side target --kind cmd --spec "<command listing target items>" --key "^(\S+)"
python scripts/dlm.py inv diff <name> [--epic E012]
python scripts/dlm.py inv mark <name> [--epic E012] --ids 3,4,5 --status pass --evidence out/run.log
python scripts/dlm.py inv report <name> [--epic E012] --gate
# One-click bridge with dlm-multi-llm-lease:
python scripts/dlm.py inv export-lease <name> --epic E012 --task S001 --sop-ref <sop_path> --priority-rule "A=^Core" --remark "CoreAuth=优先处理"
python scripts/dlm.py inv sync-lease <name> --epic E012 --task S002 --require-zero-active --gate
```

- Without `--epic`, the inventory is stored in `<cache_dir>/dlm/inventory/<name>.json`.
- With `--epic` (or when `<name>` is `<E>/<N#>` such as `E012/N1`), the inventory is stored as population `N#` inside `ledger.json`, linked from obligations via `--coverage full --population N1`, and the obligation status is derived automatically from its item states.
- `--key` takes a regular expression whose `group(1)` normalizes keys across reference and target sides so `inv diff` can match them.

## Parity (`diff`) Mode

Enumerate `ref` and `target` sides separately, then run `dlm inv diff <name>`:

- **Present only in `ref`**: Automatically marked `gap` with reason `"目标侧缺失"` (missing in target).
- **Present only in `target`**: Marked `na` with reason `"仅目标侧存在（多出项，记录）"` (present only in target; recorded as extra item).
- **Present in both (`both`)**: Remains `pending`—**behavioral evidence is still required to mark `pass`**. Symbol or file existence never proves behavioral parity.

## Counterexample

*"Align platform B features 1:1 with platform A without degradation"* — An agent spot-checked a handful of visible features and declared completion; real testing later revealed dozens of unaligned features. Under this routing protocol, reference and target sets are enumerated on day one, missing features immediately surface as `gap`, and spot-checking leaves `pending > 0`, causing `dlm inv report --gate` and `dlm close` to fail deterministically.
