#!/usr/bin/env python3
# -*- coding: utf-8-sig -*-
"""
context_manifest_tool.py — shared implementation for context manifest tooling

Stable entrypoints:
  python scripts/context_manifest_tool.py ...
    internal module: markdown helpers and the v1 context_manifest format (read-only use)

Subcommands:
  resolve    Infer Domain / L2 / Reference matches from source paths
  generate   Build a context_manifest XML snippet from epic/task inputs
  summarize  Extract and group an embedded context_manifest from a Markdown file
  lint       Validate schema / path / defer rules for an embedded context_manifest
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import fnmatch
import json
import os
from pathlib import Path
import re
import sys
from typing import Iterable
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

SCRIPT_DIR = Path(__file__).resolve().parent
for _path in (SCRIPT_DIR, SCRIPT_DIR / "legacy"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from dlm_config import DEFAULT_KNOWLEDGE_DIR, KNOWN_KNOWLEDGE_DIRS, resolve_knowledge_dir_name  # noqa: E402
from task_dependencies import parse_dependency_references  # noqa: E402


DOC_LLM_PREFIXES = ("00_Global/", "Domains/", "Tasks/", "SOPs/", "v1/", "v2/", "v3/", "v4/")
DOC_LLM_DIR_NAMES = KNOWN_KNOWLEDGE_DIRS
ACTIVE_DOC_LLM_DIR = os.environ.get("DLM_DOC_LLM_DIR", DEFAULT_KNOWLEDGE_DIR)
DEFAULT_PLAN_INTENT = "行动路径与直接验证"
CONTEXT_MANIFEST_HEADING = "## 上下文构建协议"
PRIORITY_VALUES = {"P0", "P1", "P2"}
LARGE_SOURCE_LINE_THRESHOLD = 300
SOURCE_CONTEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".cs",
    ".fs",
    ".vb",
    ".axaml",
    ".xaml",
    ".qml",
    ".ui",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".kt",
    ".kts",
    ".go",
    ".rs",
    ".swift",
    ".m",
    ".mm",
}
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


@dataclass
class ManifestEntry:
    path: str
    intent: str
    required: bool
    section: str | None = None
    defer: bool = False

    def key(self) -> tuple[str, str]:
        return (normalize_path(self.path), self.section or "")

    def to_xml(self) -> str:
        attrs = [
            f'path={quoteattr(self.path)}',
            f'intent={quoteattr(self.intent)}',
            f'required="{"true" if self.required else "false"}"',
        ]
        if self.section:
            attrs.append(f'section={quoteattr(self.section)}')
        if self.defer:
            attrs.append('defer="true"')
        return "  <file " + " ".join(attrs) + " />"

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "intent": self.intent,
            "required": self.required,
            "section": self.section,
            "defer": self.defer,
        }


@dataclass
class CatalogEntry:
    rel_path: str
    label: str
    domain: str
    match_globs: list[str] = field(default_factory=list)
    status: str = "未标注"
    kind: str = ""
    topic: str | None = None


@dataclass
class DomainCatalog:
    name: str
    brief_rel: str | None
    l0_rel: str | None
    l1_rel: str | None
    l2_entries: list[CatalogEntry] = field(default_factory=list)
    reference_entries: list[CatalogEntry] = field(default_factory=list)


def find_project_root(start: Path) -> Path:
    env_root = os.environ.get("DLM_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()

    cwd = Path.cwd()
    for current in [cwd, *cwd.parents]:
        if (current / "dlm.config.json").is_file() or (current / ACTIVE_DOC_LLM_DIR).is_dir():
            return current
        for doc_dir in DOC_LLM_DIR_NAMES:
            if (current / doc_dir).is_dir():
                return current

    current = start
    for _ in range(10):
        if (current / "dlm.config.json").is_file() or (current / ACTIVE_DOC_LLM_DIR).is_dir():
            return current
        for doc_dir in DOC_LLM_DIR_NAMES:
            if (current / doc_dir).is_dir():
                return current
        parent = current.parent
        if parent == current:
            break
        current = parent

    print(f"[ERROR] Cannot find project root (no {'/ or '.join(DOC_LLM_DIR_NAMES)}/ directory found)")
    sys.exit(2)


PROJECT_ROOT = find_project_root(Path(__file__).resolve())
DOC_LLM_DIR = resolve_knowledge_dir_name(PROJECT_ROOT)
_INITIAL_PROJECT_ROOT = PROJECT_ROOT
_INITIAL_DOC_LLM_DIR = DOC_LLM_DIR


def active_doc_llm_dir(root: Path | None = None) -> str:
    target_root = root or PROJECT_ROOT
    if DOC_LLM_DIR != _INITIAL_DOC_LLM_DIR:
        return DOC_LLM_DIR
    return resolve_knowledge_dir_name(target_root)


def doc_dir_candidates(root: Path | None = None) -> tuple[str, ...]:
    return tuple(dict.fromkeys([active_doc_llm_dir(root), *DOC_LLM_DIR_NAMES]))


def doc_llm_root() -> Path:
    return PROJECT_ROOT / active_doc_llm_dir(PROJECT_ROOT)


def tasks_root() -> Path:
    return doc_llm_root() / "Tasks"


def normalize_path(raw_path: str) -> str:
    normalized = raw_path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def resolve_path(path: Path) -> Path:
    return path.resolve(strict=False)


def is_path_within_root(path: Path, root: Path) -> bool:
    try:
        resolve_path(path).relative_to(resolve_path(root))
        return True
    except Exception:
        return False


def fail_path_validation(label: str, raw_value: str) -> "NoReturn":
    print(f"[ERROR] Invalid {label}: {raw_value}")
    sys.exit(2)


def ensure_path_within(path: Path, root: Path, label: str) -> Path:
    resolved = resolve_path(path)
    if not is_path_within_root(resolved, root):
        fail_path_validation(label, str(path))
    return resolved


def validate_key_segment(raw_key: str, label: str) -> str:
    candidate = Path(raw_key)
    normalized = normalize_path(raw_key).strip("/")
    if (
        candidate.is_absolute()
        or not normalized
        or normalized in {".", ".."}
        or "/" in normalized
        or ":" in normalized
    ):
        fail_path_validation(label, raw_key)
    return normalized


def validate_repo_scoped_path(raw_path: str, label: str) -> str:
    normalized = normalize_path(raw_path)
    if not normalized:
        fail_path_validation(label, raw_path)
    if not is_path_within_root(resolve_manifest_path(normalized), PROJECT_ROOT):
        fail_path_validation(label, raw_path)
    return normalized


def to_repo_relative(path: Path) -> str:
    try:
        return resolve_path(path).relative_to(resolve_path(PROJECT_ROOT)).as_posix()
    except Exception:
        return normalize_path(path.as_posix())


def to_manifest_relative(path: Path | str) -> str:
    raw = normalize_path(str(path))
    for doc_dir in doc_dir_candidates():
        prefix = f"{doc_dir}/"
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    if raw.startswith(DOC_LLM_PREFIXES):
        return raw
    return raw


def resolve_manifest_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return resolve_path(candidate)

    normalized = normalize_path(raw_path)
    active_dir = active_doc_llm_dir()
    for doc_dir in doc_dir_candidates():
        prefix = f"{doc_dir}/"
        if normalized.startswith(prefix):
            if doc_dir == active_dir:
                return resolve_path(PROJECT_ROOT / normalized)
            return resolve_path(PROJECT_ROOT / active_dir / normalized[len(prefix) :])
    if normalized.startswith(DOC_LLM_PREFIXES):
        return resolve_path(doc_llm_root() / normalized)
    return resolve_path(PROJECT_ROOT / normalized)


def markdown_heading_spans(content: str) -> list[tuple[int, int, str]]:
    """Return (1-based line, level, title) for real ATX headings.

    Fences, HTML comments and opening frontmatter are excluded. Optional
    closing hashes do not form part of titles; inline Markdown remains literal.
    """
    headings: list[tuple[int, int, str]] = []
    fence_char = ""
    fence_length = 0
    in_comment = False
    lines = content.splitlines()
    frontmatter_end = 0
    if lines and lines[0].lstrip("\ufeff").strip() == "---":
        frontmatter_end = next(
            (index + 1 for index, line in enumerate(lines[1:], start=1) if line.strip() in {"---", "..."}),
            0,
        )
    for line_number, line in enumerate(lines, start=1):
        if line_number <= frontmatter_end:
            continue
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence_char:
            if (
                fence
                and fence.group(1)[0] == fence_char
                and len(fence.group(1)) >= fence_length
                and not fence.group(2).strip()
            ):
                fence_char = ""
                fence_length = 0
            continue
        if not in_comment and fence and (fence.group(1)[0] != "`" or "`" not in fence.group(2)):
            fence_char = fence.group(1)[0]
            fence_length = len(fence.group(1))
            continue

        # A comment inside a fenced example never changes the outer state.
        # Comment-only blocks include their closing line; inline heading
        # comments are removed while retaining the heading's visible text.
        comment_line = in_comment
        visible: list[str] = []
        remaining = line
        while remaining:
            if in_comment:
                closing = remaining.find("-->")
                if closing < 0:
                    break
                in_comment = False
                remaining = remaining[closing + 3 :]
                continue
            opening = remaining.find("<!--")
            if opening < 0:
                visible.append(remaining)
                break
            visible.append(remaining[:opening])
            if not "".join(visible).strip():
                comment_line = True
            in_comment = True
            remaining = remaining[opening + 4 :]
        if comment_line:
            continue
        line = "".join(visible)
        heading = re.match(r"^ {0,3}(#{1,6})(?:[ \t]+(.*)|[ \t]*)$", line)
        if heading:
            title = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", heading.group(2) or "").strip()
            headings.append((line_number, len(heading.group(1)), title))
    return headings


def markdown_section_matches(content: str, section: str) -> list[int]:
    """Return exact heading-title matches as 1-based lines, not URL anchors."""
    expected = section.strip()
    return [line for line, _level, title in markdown_heading_spans(content) if title == expected]


def extract_markdown_section(content: str, heading: str) -> str | None:
    """Extract the first exact level/title section, retaining its subheadings.

    ``heading`` is an ATX heading such as ``## Core goal``. The heading itself
    is omitted; extraction ends at the next heading of equal or higher level.
    """
    selector = markdown_heading_spans(heading.strip())
    if len(selector) != 1 or len(heading.strip().splitlines()) != 1:
        return None
    _selector_line, expected_level, expected_title = selector[0]
    headings = markdown_heading_spans(content)
    lines = content.splitlines(keepends=True)
    for index, (line, level, title) in enumerate(headings):
        if (level, title) != (expected_level, expected_title):
            continue
        end = next(
            (next_line - 1 for next_line, next_level, _title in headings[index + 1 :] if next_level <= level),
            len(lines),
        )
        return "".join(lines[line:end])
    return None


def section_reference_issue(path: Path, section: str | None) -> str | None:
    """Diagnose one explicit section; callers decide required/optional severity.

    Missing files and directories stay with the caller's existing path checks.
    A section is an exact Markdown ATX heading title, without the leading hashes.
    """
    if section is None or not path.is_file():
        return None
    if not section.strip():
        return "section must name a Markdown ATX heading"
    if path.suffix.lower() not in {".md", ".markdown"}:
        return f"section validation supports Markdown (.md/.markdown) only: {path.name}"
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return f"cannot read Markdown section source {path.name}: {exc}"
    matches = markdown_section_matches(content, section)
    if not matches:
        return f"section not found: {section!r} in {path.name}"
    if len(matches) > 1:
        return f"section is ambiguous: {section!r} in {path.name} at lines {', '.join(map(str, matches))}"
    return None


def parse_yaml_frontmatter(path: Path) -> dict[str, object] | None:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except Exception:
        return None

    match = re.match(r"^---\s*\n(.*?\n)---\s*\n", content, re.DOTALL)
    if not match:
        return None

    yaml_block = match.group(1)
    result: dict[str, object] = {}
    for raw_line in yaml_block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            if isinstance(result.get("match_globs"), list):
                result["match_globs"].append(line[2:].strip().strip('"').strip("'"))
            continue
        if ":" not in line:
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if "#" in value:
            value = value[: value.index("#")].strip().strip('"').strip("'")

        if key == "match_globs":
            result[key] = []
            if value.startswith("[") and value.endswith("]"):
                items = value.strip("[]").split(",")
                result[key] = [item.strip().strip('"').strip("'") for item in items if item.strip()]
        else:
            result[key] = value

    return result


def extract_first_heading(path: Path) -> str:
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if stripped.startswith("# ") and not stripped.startswith("# {"):
                return stripped[2:].strip()
    except Exception:
        pass
    return path.stem


def build_domain_catalog() -> dict[str, DomainCatalog]:
    catalog: dict[str, DomainCatalog] = {}
    domains_root = doc_llm_root() / "Domains"
    if not domains_root.is_dir():
        return catalog

    for domain_dir in sorted(domains_root.iterdir()):
        if not domain_dir.is_dir():
            continue

        brief = next(iter(sorted(domain_dir.glob("BRIEF.md"))), None)
        l0 = next(iter(sorted(domain_dir.glob("L0_*.md"))), None)
        l1 = next(iter(sorted(domain_dir.glob("L1*_router.md"))), None)
        domain = DomainCatalog(
            name=domain_dir.name,
            brief_rel=to_manifest_relative(to_repo_relative(brief)) if brief else None,
            l0_rel=to_manifest_relative(to_repo_relative(l0)) if l0 else None,
            l1_rel=to_manifest_relative(to_repo_relative(l1)) if l1 else None,
        )

        l2_root = domain_dir / "L2_Modules"
        for l2_path in sorted(l2_root.glob("*.md")):
            meta = parse_yaml_frontmatter(l2_path) or {}
            domain.l2_entries.append(
                CatalogEntry(
                    rel_path=to_manifest_relative(to_repo_relative(l2_path)),
                    label=extract_first_heading(l2_path),
                    domain=domain_dir.name,
                    match_globs=list(meta.get("match_globs", [])) if isinstance(meta.get("match_globs"), list) else [],
                    status=str(meta.get("status", "未标注")),
                    kind="l2",
                )
            )

        ref_root = domain_dir / "references"
        for topic_readme in sorted(ref_root.glob("*/README.md")):
            meta = parse_yaml_frontmatter(topic_readme) or {}
            domain.reference_entries.append(
                CatalogEntry(
                    rel_path=to_manifest_relative(to_repo_relative(topic_readme)),
                    label=extract_first_heading(topic_readme),
                    domain=domain_dir.name,
                    match_globs=list(meta.get("match_globs", [])) if isinstance(meta.get("match_globs"), list) else [],
                    status=str(meta.get("status", "未标注")),
                    kind="reference",
                    topic=topic_readme.parent.name,
                )
            )

        catalog[domain.name] = domain

    return catalog


CATALOG = build_domain_catalog()


def manifest_exists_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        print(f"[ERROR] Cannot read {to_repo_relative(path)}: {exc}")
        sys.exit(2)


def context_manifest_region(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("<context_manifest"):
        return stripped

    section = extract_markdown_section(text, CONTEXT_MANIFEST_HEADING)
    return text if section is None else section


def extract_manifest_texts(target: Path) -> list[str]:
    text = manifest_exists_text(target)
    region = context_manifest_region(text)
    stripped = region.lstrip()
    if stripped.startswith("<context_manifest"):
        matches = re.findall(r"<context_manifest\b.*?</context_manifest>", stripped, re.DOTALL)
        return matches if matches else [stripped]

    matches = re.findall(r"<context_manifest\b.*?</context_manifest>", region, re.DOTALL)
    if not matches:
        print(f"[ERROR] No <context_manifest> found in {to_repo_relative(target)}")
        sys.exit(2)
    return matches


def extract_manifest_text(target: Path) -> str:
    return extract_manifest_texts(target)[0]


def priority_manifest_description_warnings(target: Path) -> list[str]:
    text = context_manifest_region(manifest_exists_text(target))
    warnings: list[str] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        label = line.strip().lower()
        if label not in {"p0", "p1", "p2"}:
            continue

        next_index = index + 1
        while next_index < len(lines) and not lines[next_index].strip():
            next_index += 1
        if next_index >= len(lines):
            continue

        description_line = lines[next_index].strip()
        if description_line == "```xml":
            fence_index = next_index
            has_description = False
        else:
            fence_index = next_index + 1
            while fence_index < len(lines) and not lines[fence_index].strip():
                fence_index += 1
            has_description = description_line.startswith(">")

        if (
            fence_index < len(lines)
            and lines[fence_index].strip() == "```xml"
            and fence_index + 1 < len(lines)
            and lines[fence_index + 1].lstrip().startswith("<context_manifest")
            and not has_description
        ):
            warnings.append(f"{label}: missing description line before XML fence")
    return warnings


def iter_labeled_priority_manifests(text: str) -> Iterable[tuple[str, str]]:
    lines = context_manifest_region(text).splitlines()
    index = 0
    while index < len(lines):
        label = lines[index].strip().lower()
        if label not in {"p0", "p1", "p2"}:
            index += 1
            continue

        fence_index = index + 1
        while fence_index < len(lines) and not lines[fence_index].strip():
            fence_index += 1
        if fence_index < len(lines) and lines[fence_index].strip() != "```xml":
            fence_index += 1
            while fence_index < len(lines) and not lines[fence_index].strip():
                fence_index += 1
        if fence_index >= len(lines) or lines[fence_index].strip() != "```xml":
            index += 1
            continue

        start = fence_index + 1
        end = start
        while end < len(lines) and lines[end].strip() != "```":
            end += 1
        manifest_text = "\n".join(lines[start:end]).strip()
        if manifest_text.startswith("<context_manifest"):
            yield label.upper(), manifest_text
        index = end + 1


def priority_label_messages(target: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for label, manifest_text in iter_labeled_priority_manifests(manifest_exists_text(target)):
        try:
            root = ET.fromstring(manifest_text)
        except ET.ParseError:
            continue

        root_priority = root.attrib.get("priority", "").strip().upper()
        if not root_priority:
            warnings.append(f"{label.lower()}: context_manifest missing priority=\"{label}\"")
        elif root_priority in PRIORITY_VALUES and root_priority != label:
            errors.append(f"{label.lower()}: context_manifest priority=\"{root_priority}\" does not match label {label.lower()}")
    return errors, warnings


def priority_entry_consistency_error(priority: str, required: bool, defer: bool, scope: str, path: str) -> str | None:
    if priority == "P0" and (not required or defer):
        return f'{scope}: priority="P0" requires required="true" and no defer for {path}'
    if priority == "P1" and (required or defer):
        return f'{scope}: priority="P1" requires required="false" and no defer for {path}'
    if priority == "P2" and (required or not defer):
        return f'{scope}: priority="P2" requires required="false" and defer="true" for {path}'
    return None


def parse_manifest(target: Path) -> tuple[dict[str, str], list[ManifestEntry]]:
    entries: list[ManifestEntry] = []
    attrs: dict[str, str] = {}
    for manifest_index, manifest_text in enumerate(extract_manifest_texts(target), start=1):
        try:
            root = ET.fromstring(manifest_text)
        except ET.ParseError as exc:
            print(f"[ERROR] Invalid XML in {to_repo_relative(target)} <context_manifest #{manifest_index}>: {exc}")
            sys.exit(2)

        if not attrs:
            attrs = {key: value for key, value in root.attrib.items()}
        else:
            for key, value in root.attrib.items():
                attrs.setdefault(key, value)

        for elem in root.findall("file"):
            required_raw = elem.attrib.get("required", "").strip().lower()
            entry = ManifestEntry(
                path=normalize_path(elem.attrib.get("path", "")),
                intent=elem.attrib.get("intent", "").strip(),
                required=required_raw == "true",
                section=elem.attrib.get("section"),
                defer=elem.attrib.get("defer", "").strip().lower() == "true",
            )
            entries.append(entry)

    return attrs, entries


def parse_meta_table(content: str) -> dict[str, str]:
    match = re.search(r"^## Meta[ \t]*\n(.*?)(?=^##\s|\Z)", content, re.MULTILINE | re.DOTALL)
    if not match:
        return {}

    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.split("|") if cell.strip()]
        if len(cells) < 2 or cells[0] in {"字段", "------", "---"}:
            continue
        result[cells[0]] = cells[1]
    return result


def is_unresolved_placeholder(value: str | None) -> bool:
    if value is None:
        return False
    stripped = value.strip()
    return bool(stripped) and stripped.startswith("{") and stripped.endswith("}")


def is_source_context_path(raw_path: str) -> bool:
    normalized = normalize_path(raw_path)
    lower = normalized.lower()
    doc_prefixes = tuple(f"{doc_dir.lower()}/" for doc_dir in doc_dir_candidates())
    if lower.startswith(doc_prefixes) or lower.startswith(tuple(prefix.lower() for prefix in DOC_LLM_PREFIXES)):
        return False
    return Path(lower).suffix in SOURCE_CONTEXT_EXTENSIONS


def count_file_lines(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
            return sum(1 for _ in handle)
    except Exception:
        return None


def large_hot_source_warnings(target: Path, entries: list[ManifestEntry]) -> list[str]:
    warnings: list[str] = []
    for entry in entries:
        if not entry.required or entry.defer or not is_source_context_path(entry.path):
            continue

        resolved = resolve_manifest_path(entry.path)
        if not is_path_within_root(resolved, PROJECT_ROOT) or not resolved.exists() or not resolved.is_file():
            continue

        line_count = count_file_lines(resolved)
        if line_count is None or line_count <= LARGE_SOURCE_LINE_THRESHOLD:
            continue

        warnings.append(
            f"P0 source {entry.path} has {line_count} lines; do not full-read it, "
            "use symbol skeleton/local slices based on intent"
        )
    return warnings


def path_matches_glob(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(normalize_path(path), normalize_path(pattern))


def sort_domain_names(names: Iterable[str]) -> list[str]:
    def key(name: str) -> tuple[int, str]:
        match = re.match(r"(\d+)_", name)
        return (int(match.group(1)) if match else 999, name)

    return sorted(set(names), key=key)


def resolve_sources(source_paths: list[str]) -> dict[str, object]:
    results: dict[str, object] = {"paths": {}, "domains": [], "l2": [], "references": []}
    domain_hits: set[str] = set()
    l2_hits: dict[str, dict[str, object]] = {}
    ref_hits: dict[str, dict[str, object]] = {}

    for raw_path in source_paths:
        normalized = to_repo_relative(resolve_manifest_path(validate_repo_scoped_path(raw_path, "source path")))
        per_path = {"domains": [], "l2": [], "references": []}
        for domain_name, domain in CATALOG.items():
            matched_here = False
            for entry in domain.l2_entries:
                if any(path_matches_glob(normalized, pattern) for pattern in entry.match_globs):
                    matched_here = True
                    per_path["l2"].append(entry.rel_path)
                    hit = l2_hits.setdefault(entry.rel_path, {"entry": entry, "paths": set()})
                    hit["paths"].add(normalized)
            for entry in domain.reference_entries:
                if any(path_matches_glob(normalized, pattern) for pattern in entry.match_globs):
                    matched_here = True
                    per_path["references"].append(entry.rel_path)
                    hit = ref_hits.setdefault(entry.rel_path, {"entry": entry, "paths": set()})
                    hit["paths"].add(normalized)
            if matched_here:
                domain_hits.add(domain_name)
                per_path["domains"].append(domain_name)

        results["paths"][normalized] = {
            "domains": sort_domain_names(per_path["domains"]),
            "l2": sorted(set(per_path["l2"])),
            "references": sorted(set(per_path["references"])),
        }

    results["domains"] = sort_domain_names(domain_hits)
    results["l2"] = sorted(
        [
            {
                "path": rel_path,
                "label": hit["entry"].label,
                "domain": hit["entry"].domain,
                "hits": sorted(hit["paths"]),
            }
            for rel_path, hit in l2_hits.items()
        ],
        key=lambda item: (-len(item["hits"]), item["path"]),
    )
    results["references"] = sorted(
        [
            {
                "path": rel_path,
                "label": hit["entry"].label,
                "domain": hit["entry"].domain,
                "hits": sorted(hit["paths"]),
            }
            for rel_path, hit in ref_hits.items()
        ],
        key=lambda item: (-len(item["hits"]), item["path"]),
    )
    return results


def parse_path_intent(raw_items: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw in raw_items:
        if "=" not in raw:
            print(f"[ERROR] Expected PATH=INTENT, got: {raw}")
            sys.exit(2)
        path, intent = raw.split("=", 1)
        path = validate_repo_scoped_path(path, "PATH=INTENT path")
        intent = intent.strip()
        if not path or not intent:
            print(f"[ERROR] Invalid PATH=INTENT: {raw}")
            sys.exit(2)
        mapping[path] = intent
    return mapping


def infer_epic_rel(epic_dir_raw: str) -> str:
    normalized = normalize_path(epic_dir_raw)
    for doc_dir in doc_dir_candidates():
        prefix = f"{doc_dir}/Tasks/"
        if normalized.startswith(prefix):
            ensure_path_within(resolve_manifest_path(normalized), tasks_root(), "epic directory")
            return normalized[len(f"{doc_dir}/") :]
    if normalized.startswith("Tasks/"):
        ensure_path_within(doc_llm_root() / normalized, tasks_root(), "epic directory")
        return normalized
    if re.match(r"^E\d+_", normalized):
        ensure_path_within(tasks_root() / normalized, tasks_root(), "epic directory")
        return f"Tasks/{normalized}"

    candidate = Path(epic_dir_raw)
    if candidate.is_absolute():
        safe_candidate = ensure_path_within(candidate, tasks_root(), "epic directory")
        return safe_candidate.relative_to(doc_llm_root()).as_posix()

    fail_path_validation("epic directory", epic_dir_raw)


def detect_anchor_file(epic_abs: Path, default_rel: str, folder_name: str, preferred_name: str) -> str:
    preferred = epic_abs / folder_name / preferred_name
    if preferred.exists():
        return default_rel

    folder = epic_abs / folder_name
    if folder.is_dir():
        candidates = sorted(folder.glob("*.md"))
        preferred_candidates = [path for path in candidates if preferred_name.replace(".md", "") in path.stem]
        if preferred_candidates:
            return to_manifest_relative(to_repo_relative(preferred_candidates[0]))
        if len(candidates) == 1:
            return to_manifest_relative(to_repo_relative(candidates[0]))
    return default_rel


def resolve_dependency_path(dep: str, epic_abs: Path, epic_rel: str) -> str:
    normalized = normalize_path(dep)
    if re.fullmatch(r"E\d+(?:/S\d+[a-z]?)?", normalized):
        parts = normalized.split("/")
        matches = sorted(path for base in (tasks_root(), tasks_root() / "done")
                         for path in base.glob(parts[0] + "_*") if path.is_dir())
        if len(matches) != 1:
            raise ValueError(f"依赖 Epic 必须存在且唯一：{parts[0]}（找到 {len(matches)} 项）")
        epic_abs = ensure_path_within(matches[0], tasks_root(), "dependency Epic")
        if len(parts) == 1:
            readme = epic_abs / "README.md"
            if not readme.is_file():
                raise ValueError(f"依赖 Epic 缺少 README.md：{dep}")
            return to_manifest_relative(to_repo_relative(readme))
        normalized = parts[1]
    elif "/" in normalized:
        resolved = ensure_path_within(resolve_manifest_path(normalized), tasks_root(), "dependency path")
        if not resolved.is_file():
            raise ValueError(f"依赖文件不存在：{dep}")
        return to_manifest_relative(to_repo_relative(resolved))

    if re.fullmatch(r"S\d+[a-z]?", normalized) is None:
        raise ValueError(f"依赖必须是任务 ID 或 Tasks 内的文件路径：{dep}")
    matches = sorted(path for path in (epic_abs / "subtasks").glob("S*.md")
                     if re.fullmatch(re.escape(normalized) + r"(?:_.+)?\.md", path.name))
    if len(matches) != 1:
        raise ValueError(f"依赖 Subtask 必须存在且唯一：{dep}（找到 {len(matches)} 项）")
    resolved = ensure_path_within(matches[0], tasks_root(), "dependency path")
    return to_manifest_relative(to_repo_relative(resolved))


def resolve_sop_path(raw_sop: str) -> str:
    normalized = normalize_path(raw_sop)
    if any(normalized.startswith(f"{doc_dir}/SOPs/") for doc_dir in doc_dir_candidates()):
        path = to_manifest_relative(normalized)
    elif normalized.startswith("SOPs/"):
        path = normalized
    else:
        sop_key = validate_key_segment(normalized, "SOP key")
        path = f"SOPs/{sop_key}/README.md"

    if not path.endswith("/README.md"):
        path = f"{path.rstrip('/')}/README.md"
    ensure_path_within(resolve_manifest_path(path), doc_llm_root() / "SOPs", "SOP path")
    return path


def add_unique_entry(entries: list[ManifestEntry], entry: ManifestEntry) -> None:
    existing = {item.key(): item for item in entries}
    key = entry.key()
    if key in existing:
        current = existing[key]
        if not current.intent and entry.intent:
            current.intent = entry.intent
        current.required = current.required or entry.required
        current.defer = current.defer and entry.defer
        if not current.section and entry.section:
            current.section = entry.section
        return
    entries.append(entry)


def generate_manifest(args: argparse.Namespace) -> str:
    epic_rel = infer_epic_rel(args.epic_dir)
    epic_abs = resolve_manifest_path(epic_rel)
    epic_name = Path(epic_rel).name
    epic_id = args.epic_id or epic_name.split("_", 1)[0]
    task_id = args.task_id
    intent_map = parse_path_intent(args.intent or [])
    extra_required = parse_path_intent(args.extra_required or [])
    extra_optional = parse_path_intent(args.extra_optional or [])

    resolved = resolve_sources(args.source or [])
    explicit_domains = set(args.domain or [])
    all_domains = sort_domain_names(list(explicit_domains) + list(resolved["domains"]))

    for domain_name in all_domains:
        if domain_name not in CATALOG:
            print(f"[ERROR] Unknown domain: {domain_name}")
            sys.exit(2)

    l2_ranked = list(resolved["l2"])
    required_l2_count = len(l2_ranked) if len(l2_ranked) <= args.max_total_l2 else args.max_required_l2
    l2_required_paths = {item["path"] for item in l2_ranked[:required_l2_count]}

    reference_paths: list[str] = []
    for item in resolved["references"][: args.max_reference]:
        reference_paths.append(item["path"])
    for raw_topic in args.ref_topic or []:
        normalized = normalize_path(raw_topic)
        if normalized.startswith("Domains/"):
            reference_paths.append(to_manifest_relative(normalized))
            continue
        if "/" not in normalized:
            print(f"[ERROR] ref-topic must be Domains/.../README.md or domain/topic, got: {raw_topic}")
            sys.exit(2)
        domain_name, topic = normalized.split("/", 1)
        reference_paths.append(f"Domains/{domain_name}/references/{topic}/README.md")

    entries: list[ManifestEntry] = []
    add_unique_entry(entries, ManifestEntry(path="00_Global/L0_global_redlines.md", intent="适用全局产品约束时按需读取", required=False))
    add_unique_entry(entries, ManifestEntry(path=f"{epic_rel}/README.md", intent="目标与进度", required=True))

    plan_rel = detect_anchor_file(epic_abs, f"{epic_rel}/plan/execution_plan.md", "plan", "execution_plan.md")
    if resolve_manifest_path(plan_rel).exists():
        add_unique_entry(entries, ManifestEntry(path=plan_rel, intent=DEFAULT_PLAN_INTENT, required=False))

    for domain_name in all_domains:
        domain = CATALOG[domain_name]
        if domain.brief_rel:
            add_unique_entry(entries, ManifestEntry(path=domain.brief_rel, intent="owner 不确定时的领域定位", required=False))
        if domain.l0_rel:
            add_unique_entry(entries, ManifestEntry(path=domain.l0_rel, intent="领域红线", required=True))
        if domain.l1_rel:
            add_unique_entry(entries, ManifestEntry(path=domain.l1_rel, intent="按需导航到目标知识", required=False))

    for item in l2_ranked:
        path = item["path"]
        required = path in l2_required_paths
        label = item["label"]
        intent = intent_map.get(path, label)
        add_unique_entry(entries, ManifestEntry(path=path, intent=intent, required=required, defer=not required))

    for path in sorted(dict.fromkeys(reference_paths)):
        resolved_path = resolve_manifest_path(path)
        label = extract_first_heading(resolved_path) if resolved_path.exists() else Path(path).parent.name
        add_unique_entry(entries, ManifestEntry(path=path, intent=intent_map.get(path, f"参考资料入口: {label}"), required=False))

    raw_sops = getattr(args, "sop", None) or []
    if not isinstance(raw_sops, list):
        raw_sops = []
    raw_optional_sops = getattr(args, "optional_sop", None) or []
    if not isinstance(raw_optional_sops, list):
        raw_optional_sops = []
    for raw_sop in raw_sops:
        path = resolve_sop_path(raw_sop)
        resolved_path = resolve_manifest_path(path)
        label = extract_first_heading(resolved_path) if resolved_path.exists() else Path(path).parent.name
        add_unique_entry(entries, ManifestEntry(path=path, intent=intent_map.get(path, f"场景 SOP: {label}"), required=True))
    for raw_sop in raw_optional_sops:
        path = resolve_sop_path(raw_sop)
        resolved_path = resolve_manifest_path(path)
        label = extract_first_heading(resolved_path) if resolved_path.exists() else Path(path).parent.name
        add_unique_entry(entries, ManifestEntry(path=path, intent=intent_map.get(path, f"场景 SOP: {label}"), required=False))

    for raw_source in args.source or []:
        normalized = to_repo_relative(resolve_manifest_path(validate_repo_scoped_path(raw_source, "source path")))
        add_unique_entry(
            entries,
            ManifestEntry(
                path=normalized,
                intent=intent_map.get(normalized, "目标源码入口"),
                required=True,
            ),
        )

    for dep in args.dep_task or []:
        # File inputs retain their exact path. ID lists/ranges share the graph
        # validator's parser; prose is visible but never turned into a fake file.
        if normalize_path(dep).endswith(".md"):
            dependencies, prose = [dep], ""
        else:
            dependencies, prose = parse_dependency_references(dep)
        if prose:
            print(f"[WARN] 依赖含未作机械判断的条件：{prose}；产物就绪需执行者核实", file=sys.stderr)
        for dependency in dependencies:
            dep_path = resolve_dependency_path(dependency, epic_abs, epic_rel)
            is_epic = Path(dep_path).name == "README.md"
            add_unique_entry(entries, ManifestEntry(path=dep_path, intent="前置 Epic 当前合同" if is_epic else "前置执行日志",
                                                   required=False, section=None if is_epic else "执行日志"))

    for path, intent in extra_required.items():
        add_unique_entry(entries, ManifestEntry(path=path, intent=intent, required=True))
    for path, intent in extra_optional.items():
        add_unique_entry(entries, ManifestEntry(path=path, intent=intent, required=False))

    # Older scaffold callers construct Namespaces without the contract option.
    contracts = getattr(args, "contract", None) or []
    for raw_path, raw_section, raw_intent in contracts:
        path = to_manifest_relative(validate_repo_scoped_path(raw_path, "contract path"))
        section = raw_section.strip()
        intent = raw_intent.strip()
        if not section or not intent:
            print("[ERROR] --contract requires a section title (or '-') and a non-empty intent")
            sys.exit(2)
        contract = ManifestEntry(path=path, intent=intent, required=True, section=None if section == "-" else section)
        for existing in entries:
            if existing.key() == contract.key():
                existing.intent = intent
        add_unique_entry(entries, contract)

    lines = [f'<context_manifest task="{escape(task_id)}" epic="{escape(epic_id)}">']
    lines.extend(entry.to_xml() for entry in entries)
    lines.append("</context_manifest>")
    return "\n".join(lines)


def summarize_manifest(target: Path, as_json: bool = False) -> int:
    attrs, entries = parse_manifest(target)
    grouped = {
        "required": [],
        "optional": [],
        "deferred": [],
    }
    for entry in entries:
        item = entry.to_dict()
        if entry.defer:
            grouped["deferred"].append(item)
        elif entry.required:
            grouped["required"].append(item)
        else:
            grouped["optional"].append(item)

    if as_json:
        print(
            json.dumps(
                {
                    "task": attrs.get("task"),
                    "epic": attrs.get("epic"),
                    "counts": {key: len(value) for key, value in grouped.items()},
                    "entries": grouped,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"Manifest: task={attrs.get('task', '—')} epic={attrs.get('epic', '—')}")
    print(f"P0 Required ({len(grouped['required'])})")
    for item in grouped["required"]:
        print(f"  - {item['path']} [{item['intent']}]")
    print(f"P1 Optional ({len(grouped['optional'])})")
    for item in grouped["optional"]:
        print(f"  - {item['path']} [{item['intent']}]")
    print(f"P2 Deferred ({len(grouped['deferred'])})")
    for item in grouped["deferred"]:
        print(f"  - {item['path']} [{item['intent']}]")
    return 0


def lint_manifest(target: Path, as_json: bool = False) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    entries: list[ManifestEntry] = []
    content = manifest_exists_text(target)
    for manifest_index, manifest_text in enumerate(extract_manifest_texts(target), start=1):
        try:
            root = ET.fromstring(manifest_text)
        except ET.ParseError as exc:
            print(f"[ERROR] Invalid XML in {to_repo_relative(target)} <context_manifest #{manifest_index}>: {exc}")
            return 2
        priority = root.attrib.get("priority", "").strip().upper()
        if priority and priority not in PRIORITY_VALUES:
            errors.append(f"<manifest #{manifest_index}>: priority must be one of P0, P1, P2")

        for index, elem in enumerate(root.findall("file"), start=1):
            attrs = elem.attrib
            scope = f"<manifest #{manifest_index} file #{index}>"
            path = normalize_path(attrs.get("path", ""))
            intent = attrs.get("intent", "").strip()
            required_raw = attrs.get("required")
            defer_raw = attrs.get("defer", "").strip().lower()

            if not path:
                errors.append(f"{scope}: missing path")
                continue
            if not intent:
                errors.append(f"{scope}: missing intent for {path}")
            if required_raw not in {"true", "false"}:
                errors.append(f"{scope}: required must be 'true' or 'false' for {path}")
                required = False
            else:
                required = required_raw == "true"
            entries.append(
                ManifestEntry(
                    path=path,
                    intent=intent,
                    required=required,
                    section=attrs.get("section"),
                    defer=defer_raw == "true",
                )
            )
            if defer_raw and defer_raw != "true":
                errors.append(f"{scope}: defer only supports 'true' for {path}")
            if defer_raw == "true" and required:
                errors.append(f"{scope}: defer='true' must pair with required='false' for {path}")
            if priority in PRIORITY_VALUES:
                priority_error = priority_entry_consistency_error(priority, required, defer_raw == "true", scope, path)
                if priority_error:
                    errors.append(priority_error)

            resolved = resolve_manifest_path(path)
            if not is_path_within_root(resolved, PROJECT_ROOT):
                errors.append(f"{scope}: path escapes project root: {path}")
                continue
            if path.endswith("/") or path.endswith("\\") or (resolved.exists() and resolved.is_dir()):
                errors.append(f"{scope}: path must point to a concrete file, got directory-like path {path}")
            if required and not resolved.exists():
                errors.append(f"{scope}: required path does not exist: {path}")
            if not required and not resolved.exists():
                warnings.append(f"{scope}: optional path missing (allowed): {path}")
            section_issue = section_reference_issue(resolved, attrs.get("section"))
            if section_issue:
                (errors if required else warnings).append(f"{scope}: {section_issue}")

    label_errors, label_warnings = priority_label_messages(target)
    errors.extend(label_errors)
    warnings.extend(label_warnings)
    warnings.extend(large_hot_source_warnings(target, entries))
    warnings.extend(priority_manifest_description_warnings(target))

    exit_code = 1 if errors else 0
    if as_json:
        print(json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
        return exit_code

    if not errors and not warnings:
        print(f"[OK] {to_repo_relative(target)}: context_manifest lint passed")
        return 0

    for message in errors:
        print(f"[ERROR] {message}")
    for message in warnings:
        print(f"[WARN] {message}")
    return exit_code


def cmd_resolve(args: argparse.Namespace) -> int:
    resolved = resolve_sources(args.paths)
    if args.json:
        print(json.dumps(resolved, ensure_ascii=False, indent=2))
        return 0

    for path, info in resolved["paths"].items():
        print(f"Path: {path}")
        print(f"  Domains: {', '.join(info['domains']) if info['domains'] else '—'}")
        print(f"  L2: {', '.join(info['l2']) if info['l2'] else '—'}")
        print(f"  References: {', '.join(info['references']) if info['references'] else '—'}")

    print("Summary:")
    print(f"  Domains: {', '.join(resolved['domains']) if resolved['domains'] else '—'}")
    print(f"  L2 hits: {len(resolved['l2'])}")
    print(f"  Reference hits: {len(resolved['references'])}")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    print(generate_manifest(args))
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    target = ensure_path_within(resolve_manifest_path(args.target), PROJECT_ROOT, "summarize target")
    return summarize_manifest(target, as_json=args.json)


def cmd_lint(args: argparse.Namespace) -> int:
    target = ensure_path_within(resolve_manifest_path(args.target), PROJECT_ROOT, "lint target")
    return lint_manifest(target, as_json=args.json)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shared tooling for DLM context_manifest generation and validation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve", help="Resolve source paths to Domain / L2 / Reference hits.")
    resolve_parser.add_argument("paths", nargs="+", help="Repository-relative source paths to inspect.")
    resolve_parser.add_argument("--json", action="store_true", help="Print structured JSON output.")
    resolve_parser.set_defaults(func=cmd_resolve)

    generate_parser = subparsers.add_parser("generate", help="Generate a context_manifest XML snippet.")
    generate_parser.add_argument(
        "--epic-dir",
        required=True,
        help=f"Epic directory path, such as {active_doc_llm_dir()}/Tasks/E001_example_capability.",
    )
    generate_parser.add_argument("--task-id", required=True, help="Task id, such as S001 or E001.")
    generate_parser.add_argument("--epic-id", help="Epic id override. Defaults to the epic dir prefix.")
    generate_parser.add_argument("--source", action="append", help="Repository-relative source path. Repeat for multiple files.")
    generate_parser.add_argument("--domain", action="append", help="Explicit domain name, such as 1_core_runtime.")
    generate_parser.add_argument("--dep-task", action="append", help="Dependency task id or path. Repeat for multiple entries.")
    generate_parser.add_argument("--ref-topic", action="append", help="Reference topic as domain/topic or Domains/.../README.md.")
    generate_parser.add_argument("--sop", action="append", help="Scenario SOP key or SOPs/.../README.md to include as P0/required.")
    generate_parser.add_argument("--optional-sop", action="append", help="Scenario SOP key or SOPs/.../README.md to include as P1/optional.")
    generate_parser.add_argument("--intent", action="append", help="PATH=INTENT override. Works for source/doc entries.")
    generate_parser.add_argument("--extra-required", action="append", help="Extra required PATH=INTENT entry.")
    generate_parser.add_argument("--extra-optional", action="append", help="Extra optional PATH=INTENT entry.")
    generate_parser.add_argument(
        "--contract", action="append", nargs=3, metavar=("PATH", "SECTION", "INTENT"),
        help="Required contract source and exact Markdown heading; SECTION '-' selects the full file. Repeat for multiple sections.",
    )
    generate_parser.add_argument("--max-required-l2", type=int, default=2, help="Required L2 cap once the L2 hit count exceeds the total cap.")
    generate_parser.add_argument("--max-total-l2", type=int, default=3, help="If L2 hits exceed this count, only the top required cap stays required.")
    generate_parser.add_argument("--max-reference", type=int, default=2, help="Auto-include at most this many inferred reference topics.")
    generate_parser.set_defaults(func=cmd_generate)

    summarize_parser = subparsers.add_parser("summarize", help="Summarize an embedded context_manifest.")
    summarize_parser.add_argument("target", help="Markdown file or raw XML file containing a context_manifest.")
    summarize_parser.add_argument("--json", action="store_true", help="Print structured JSON output.")
    summarize_parser.set_defaults(func=cmd_summarize)

    lint_parser = subparsers.add_parser("lint", help="Lint an embedded context_manifest.")
    lint_parser.add_argument("target", help="Markdown file or raw XML file containing a context_manifest.")
    lint_parser.add_argument("--json", action="store_true", help="Print structured JSON output.")
    lint_parser.set_defaults(func=cmd_lint)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
