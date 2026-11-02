#!/usr/bin/env python3
# -*- coding: utf-8-sig -*-
"""
doc_llm_scaffold_tool.py — knowledge scaffold generator behind dlm kb scaffold

Stable entrypoints:
  python scripts/doc_llm_scaffold_tool.py ...
  python ../dlm-knowledge/scripts/dlm.py kb scaffold ...
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dlm_config import resolve_knowledge_dir_name  # noqa: E402
from context_manifest_tool import (  # noqa: E402
    ensure_path_within,
    PROJECT_ROOT,
    normalize_path,
    validate_key_segment,
)


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "dlm-knowledge" / "templates"
TODAY = date.today().isoformat()


def active_doc_llm_dir() -> str:
    return resolve_knowledge_dir_name(PROJECT_ROOT)


def doc_llm_root() -> Path:
    return PROJECT_ROOT / active_doc_llm_dir()


def domains_root() -> Path:
    return doc_llm_root() / "Domains"


def sops_root() -> Path:
    return doc_llm_root() / "SOPs"


@dataclass
class ChangeRecord:
    path: str
    kind: str
    action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "kind": self.kind,
            "action": self.action,
        }


def template_path(name: str) -> Path:
    path = TEMPLATE_ROOT / name
    if not path.exists():
        print(f"[ERROR] Template not found: {path}")
        sys.exit(2)
    return path


def read_template(name: str) -> str:
    return template_path(name).read_text(encoding="utf-8-sig")


def apply_replacements(content: str, replacements: dict[str, str]) -> str:
    for raw, value in replacements.items():
        content = content.replace(raw, value)
    return content


def ensure_dir(path: Path, dry_run: bool) -> ChangeRecord:
    safe_path = ensure_path_within(path, doc_llm_root(), "doc_llm scaffold path")
    if safe_path.exists():
        action = "exists"
    else:
        action = "create"
        if not dry_run:
            safe_path.mkdir(parents=True, exist_ok=True)
    return ChangeRecord(path=to_repo_path(safe_path), kind="dir", action=action)


def write_file(path: Path, content: str, dry_run: bool, force: bool) -> ChangeRecord:
    safe_path = ensure_path_within(path, doc_llm_root(), "doc_llm scaffold path")
    if safe_path.exists():
        existing = safe_path.read_text(encoding="utf-8-sig")
        if existing == content:
            return ChangeRecord(path=to_repo_path(safe_path), kind="file", action="unchanged")
        if not force:
            return ChangeRecord(path=to_repo_path(safe_path), kind="file", action="skipped_exists")
        action = "update"
    else:
        action = "create"

    if not dry_run:
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_text(content, encoding="utf-8")
    return ChangeRecord(path=to_repo_path(safe_path), kind="file", action=action)


def to_repo_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return path.as_posix()


def domain_slug(domain_key: str) -> str:
    normalized = validate_key_segment(domain_key, "domain key")
    if "_" in normalized:
        return normalized.split("_", 1)[1]
    return normalized


def display_name(raw: str) -> str:
    return raw.replace("_", " ").strip() or raw


def yaml_glob_block(globs: list[str], fallback: str) -> str:
    values = [item.strip() for item in globs if item.strip()]
    if not values:
        values = [fallback]
    return "\n".join(f'  - "{value}"' for value in values)


def render_project_brief(project_title: str) -> str:
    template = read_template("brief_project_template.md")
    return apply_replacements(
        template,
        {
            "{项目名}": project_title,
            "YYYY-MM-DD": TODAY,
        },
    )


def render_domain_brief(domain_title: str) -> str:
    template = read_template("brief_domain_template.md")
    return apply_replacements(
        template,
        {
            "{领域名}": domain_title,
            "YYYY-MM-DD": TODAY,
        },
    )


def render_l0(scope_name: str) -> str:
    template = read_template("L0_template.md")
    return apply_replacements(template, {"{领域名}": scope_name})


def render_global_l1(project_title: str) -> str:
    template = read_template("L1_global_template.md")
    return apply_replacements(
        template,
        {
            "{项目名}": project_title,
            "YYYY-MM-DD": TODAY,
        },
    )


def render_domain_l1(domain_title: str, root_path: str) -> str:
    template = read_template("L1_domain_template.md")
    return apply_replacements(
        template,
        {
            "{领域名}": domain_title,
            "{root_path}": root_path,
            "YYYY-MM-DD": TODAY,
        },
    )


def render_l2(
    domain_key: str,
    module_key: str,
    module_title: str,
    match_globs: list[str],
    status: str,
) -> str:
    template = read_template("L2_template.md")
    rendered = apply_replacements(
        template,
        {
            '"{领域编号}_{领域名}"': f'"{domain_key}"',
            "{模块名}": module_key,
            "{模块名称}": module_title,
            "{核心概念1}": module_title,
            "{核心概念2}": module_key,
            "{设计/实现细节段落1}": "核心机制",
            "{设计/实现细节段落2}": "边界与交互",
            "{组件A}": "Caller",
            "{组件B}": module_title,
            "{调用}": "invoke",
            "{返回}": "result",
            '"已实现"       # 已实现 / 部分实现 / 待实现 / 已废弃': f'"{status}"       # 已实现 / 部分实现 / 待实现 / 已废弃',
            "YYYY-MM-DD": TODAY,
        },
    )
    return rendered.replace(
        '  - "src/path/to/*.hpp"\n  - "src/path/to/*.cxx"',
        yaml_glob_block(match_globs, f"TODO/{module_key}/**/*"),
    )


def render_reference(
    domain_key: str,
    topic_key: str,
    topic_title: str,
    match_globs: list[str],
    reference_type: str,
    authority: str,
    status: str,
) -> str:
    template = read_template("reference_topic_template.md")
    rendered = apply_replacements(
        template,
        {
            '"{领域编号}_{领域名}"': f'"{domain_key}"',
            "{topic_name}": topic_key,
            "{参考主题名称}": topic_title,
            '"official_doc"   # official_doc / api_doc / legacy_doc / spec / vendor_doc / internal_note': (
                f'"{reference_type}"   # official_doc / api_doc / legacy_doc / spec / vendor_doc / internal_note'
            ),
            '"official"            # official / vendor / internal / historical_baseline': (
                f'"{authority}"            # official / vendor / internal / historical_baseline'
            ),
            '"已归档"                  # 已归档 / 部分归档 / 待补充 / 已废弃': (
                f'"{status}"                  # 已归档 / 部分归档 / 待补充 / 已废弃'
            ),
            "YYYY-MM-DD": TODAY,
        },
    )
    return rendered.replace(
        '  - "src/path/to/*.cs"\n  - "docs/vendor/topic/**/*.md"',
        yaml_glob_block(match_globs, f"docs/{topic_key}/**/*"),
    )


def render_sop_index() -> str:
    template = read_template("sop_index_template.md")
    return apply_replacements(
        template,
        {
            "{sop_id}": "example_sop",
            "{SOP 标题}": "示例场景规程",
            "{owner_domain}": "global",
            "{触发场景}": "用户显式调用或任务 manifest 声明",
        },
    )


def render_sop(
    sop_key: str,
    sop_title: str,
    owner_domain: str,
    activation_aliases: list[str],
    match_globs: list[str],
    validator_commands: list[str],
) -> str:
    template = read_template("sop_template.md")
    aliases = [item.strip() for item in activation_aliases if item.strip()] or [sop_title]
    validators = [item.strip() for item in validator_commands if item.strip()]
    rendered = apply_replacements(
        template,
        {
            "{sop_id}": sop_key,
            "{SOP 标题}": sop_title,
            "{owner_domain}": owner_domain,
            "{触发别名}": aliases[0],
            "{glob}": (match_globs[0] if match_globs else f"TODO/{sop_key}/**/*"),
            "YYYY-MM-DD": TODAY,
        },
    )
    alias_block = "\n".join(f'  - "{alias}"' for alias in aliases)
    validator_block = "\n".join(f'  - "{command}"' for command in validators)
    validator_metadata = f"validator_commands:\n{validator_block}" if validators else "validator_commands: []"
    validator_section = (
        "```bash\n" + "\n".join(validators) + "\n```"
        if validators
        else "尚未配置校验命令。脚手架只创建空的 `checks/` 目录，不生成校验脚本。"
        "请先实现或确认可用的校验入口，再填写 `validator_commands` 和本节；"
        "创建时也可通过 `--validator-command` 传入已有命令。"
    )
    return (
        rendered
        .replace(f'  - "{aliases[0]}"', alias_block, 1)
        .replace('validator_commands:\n  - "{校验命令}"', validator_metadata, 1)
        .replace("```bash\n{校验命令}\n```", validator_section, 1)
        .replace(f'  - "{match_globs[0] if match_globs else f"TODO/{sop_key}/**/*"}"', yaml_glob_block(match_globs, f"TODO/{sop_key}/**/*"), 1)
    )


def scaffold_project(args: argparse.Namespace) -> list[ChangeRecord]:
    results = [
        write_file(doc_llm_root() / "BRIEF.md", render_project_brief(args.project_title), args.dry_run, args.force),
        write_file(
            doc_llm_root() / "00_Global" / "L0_global_redlines.md",
            render_l0("全局"),
            args.dry_run,
            args.force,
        ),
        write_file(
            doc_llm_root() / "00_Global" / "L1_target_architecture.md",
            render_global_l1(args.project_title),
            args.dry_run,
            args.force,
        ),
    ]
    results.append(ensure_dir(domains_root(), args.dry_run))
    results.append(write_file(sops_root() / "README.md", render_sop_index(), args.dry_run, args.force))
    return results


def scaffold_domain(args: argparse.Namespace) -> list[ChangeRecord]:
    domain_key = validate_key_segment(args.domain_key, "domain key")
    base = ensure_path_within(domains_root() / domain_key, domains_root(), "domain scaffold path")
    slug = domain_slug(domain_key)
    domain_title = args.domain_title or display_name(slug)
    root_path = args.root_path or f"TODO/{slug}"

    results = [
        ensure_dir(base / "L2_Modules", args.dry_run),
        ensure_dir(base / "references", args.dry_run),
        write_file(base / "BRIEF.md", render_domain_brief(domain_title), args.dry_run, args.force),
        write_file(base / f"L0_{slug}_rules.md", render_l0(domain_title), args.dry_run, args.force),
        write_file(base / f"L1_{slug}_router.md", render_domain_l1(domain_title, root_path), args.dry_run, args.force),
    ]
    return results


def scaffold_l2(args: argparse.Namespace) -> list[ChangeRecord]:
    domain_key = validate_key_segment(args.domain_key, "domain key")
    module_key = validate_key_segment(args.module_key, "module key")
    base = ensure_path_within(domains_root() / domain_key / "L2_Modules", domains_root(), "L2 scaffold path")
    results = [ensure_dir(base, args.dry_run)]
    results.append(
        write_file(
            base / f"{module_key}.md",
            render_l2(
                domain_key=domain_key,
                module_key=module_key,
                module_title=args.module_title or display_name(module_key),
                match_globs=args.match_glob,
                status=args.status,
            ),
            args.dry_run,
            args.force,
        )
    )
    return results


def scaffold_reference(args: argparse.Namespace) -> list[ChangeRecord]:
    domain_key = validate_key_segment(args.domain_key, "domain key")
    topic_key = validate_key_segment(args.topic_key, "topic key")
    base = ensure_path_within(domains_root() / domain_key / "references" / topic_key, domains_root(), "reference scaffold path")
    results = [ensure_dir(base, args.dry_run)]
    results.append(
        write_file(
            base / "README.md",
            render_reference(
                domain_key=domain_key,
                topic_key=topic_key,
                topic_title=args.topic_title or display_name(topic_key),
                match_globs=args.match_glob,
                reference_type=args.reference_type,
                authority=args.authority,
                status=args.status,
            ),
            args.dry_run,
            args.force,
        )
    )
    return results


def scaffold_sop(args: argparse.Namespace) -> list[ChangeRecord]:
    sop_key = validate_key_segment(args.sop_key, "SOP key")
    base = ensure_path_within(sops_root() / sop_key, sops_root(), "SOP scaffold path")
    results = [
        ensure_dir(base / "checks", args.dry_run),
        write_file(
            base / "README.md",
            render_sop(
                sop_key=sop_key,
                sop_title=args.sop_title or display_name(sop_key),
                owner_domain=args.owner_domain,
                activation_aliases=args.alias,
                match_globs=args.match_glob,
                validator_commands=args.validator_command,
            ),
            args.dry_run,
            args.force,
        ),
    ]
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Instantiate doc_llm scaffold files from dlm-knowledge templates.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview scaffold actions without writing files")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files when template output differs")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown summary")

    subparsers = parser.add_subparsers(dest="command", required=True)

    project = subparsers.add_parser("init-project", help="Create project-level BRIEF + 00_Global scaffolds")
    project.add_argument("project_title", help="Project title for BRIEF / global L1")

    domain = subparsers.add_parser("init-domain", help="Create a Domain scaffold (BRIEF/L0/L1 + dirs)")
    domain.add_argument("domain_key", help="Domain directory key, e.g. 1_core_runtime")
    domain.add_argument("--domain-title", help="Human-facing domain title used in headings")
    domain.add_argument("--root-path", help="Default source root shown in the L1 directory tree")

    l2 = subparsers.add_parser("create-l2", help="Create one L2 module scaffold")
    l2.add_argument("domain_key", help="Domain directory key, e.g. 1_core_runtime")
    l2.add_argument("module_key", help="L2 file stem, e.g. workbench_command_service")
    l2.add_argument("--module-title", help="Human-facing module title")
    l2.add_argument("--match-glob", action="append", default=[], help="Repeatable match_globs entries")
    l2.add_argument("--status", default="已实现", help="L2 status label")

    reference = subparsers.add_parser("create-reference", help="Create one reference topic README scaffold")
    reference.add_argument("domain_key", help="Domain directory key, e.g. 1_core_runtime")
    reference.add_argument("topic_key", help="Reference topic directory key")
    reference.add_argument("--topic-title", help="Human-facing topic title")
    reference.add_argument("--match-glob", action="append", default=[], help="Repeatable match_globs entries")
    reference.add_argument("--reference-type", default="official_doc", help="Reference type label")
    reference.add_argument("--authority", default="official", help="Authority label")
    reference.add_argument("--status", default="已归档", help="Reference topic status label")

    sop = subparsers.add_parser("create-sop", help="Create one scenario SOP scaffold")
    sop.add_argument("sop_key", help="SOP directory key, e.g. app_ui_development")
    sop.add_argument("--sop-title", help="Human-facing SOP title")
    sop.add_argument("--owner-domain", default="global", help='Owner domain, e.g. global or Domains/1_core_runtime')
    sop.add_argument("--alias", action="append", default=[], help="Repeatable activation alias")
    sop.add_argument("--match-glob", action="append", default=[], help="Repeatable match_globs entries")
    sop.add_argument("--validator-command", action="append", default=[], help="Repeatable validator command")

    return parser


def print_markdown(command: str, records: list[ChangeRecord]) -> None:
    print(f"Scaffold: {command}")
    for record in records:
        print(f"  - [{record.action}] {record.kind}: {record.path}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "init-project":
        records = scaffold_project(args)
    elif args.command == "init-domain":
        records = scaffold_domain(args)
    elif args.command == "create-l2":
        records = scaffold_l2(args)
    elif args.command == "create-reference":
        records = scaffold_reference(args)
    elif args.command == "create-sop":
        records = scaffold_sop(args)
    else:
        parser.error(f"Unsupported command: {args.command}")
        return 2

    if args.json:
        payload = {
            "command": args.command,
            "dry_run": args.dry_run,
            "records": [record.to_dict() for record in records],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_markdown(args.command, records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
