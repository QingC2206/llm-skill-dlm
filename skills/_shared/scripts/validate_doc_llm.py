#!/usr/bin/env python3
# -*- coding: utf-8-sig -*-
"""
validate_doc_llm.py — shared implementation for doc_llm structural validation

Stable entrypoints:
  python scripts/validate_doc_llm.py --all
  python scripts/validate_doc_llm.py --tasks
  python scripts/validate_doc_llm.py --domains
  python scripts/validate_doc_llm.py <path>

Design constraints:
  - Read-only validator; never edits repo-tracked files
  - Validates knowledge structure (dlm kb check) and reports v1 task records read-only (dlm legacy check)
  - Uses lightweight regex / structure scanning; no third-party dependencies
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

SCRIPT_DIR = Path(__file__).resolve().parent
for _path in (SCRIPT_DIR, SCRIPT_DIR / "legacy"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from dlm_config import DEFAULT_KNOWLEDGE_DIR, KNOWN_KNOWLEDGE_DIRS, resolve_knowledge_dir_name
from task_depth import TASK_DEPTH_VALUES
from sync_l1_route import L2_HEADING_PATTERN, REFERENCE_HEADING_PATTERN
from context_manifest_tool import extract_markdown_section, markdown_heading_spans, section_reference_issue
from epic_principles import PRINCIPLES_TITLE, principles_span, require_confirmation, require_principles
from task_principles import SUBTASK_PRINCIPLES, TURN_REVIEW, COMPLETION_REVIEW, check_reviews, uses_principle_reviews, is_verified_baseline
from task_dependencies import parse_dependency_references


EPIC_REQUIRED_HEADERS = [
    "## 范围",
    "## 上下文构建协议",
    "## 子任务索引",
]

EXECUTION_PLAN_REQUIRED_HEADERS: list[str] = []

SUBTASK_REQUIRED_HEADERS = [
    "## Meta",
    "## 上下文构建协议",
    "## 执行日志",
]

SOP_REQUIRED_FRONTMATTER_KEYS = [
    "kind",
    "id",
    "title",
    "owner_domain",
    "status",
    "last_updated",
    "activation_aliases",
    "match_globs",
]
SOP_ALLOWED_STATUS_VALUES = {"active", "draft", "deprecated"}

SUBTASK_REQUIRED_META_FIELDS = [
    "状态",
    "优先级",
    "依赖",
]
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
MODIFICATION_SCOPE_KINDS = {"write", "generated", "read_only", "forbidden"}

ALLOWED_STATUS_VALUES = {"📋 待办", "🔄 进行中", "🟡 待验收", "✅ 完成"}
DOC_LLM_MANIFEST_PREFIXES = ("00_Global/", "Domains/", "Tasks/", "SOPs/", "v1/", "v2/", "v3/", "v4/")
DOC_LLM_DIR_NAMES = KNOWN_KNOWLEDGE_DIRS
ACTIVE_DOC_LLM_DIR = os.environ.get("DLM_DOC_LLM_DIR", DEFAULT_KNOWLEDGE_DIR)
L0_RECOMMENDED_MAX_CHARS = 500
L2_RECOMMENDED_MIN_BYTES = 3 * 1024
L2_RECOMMENDED_MAX_BYTES = 8 * 1024
ALLOWED_DOC_FENCE_LANGS = {"", "text", "plaintext", "txt", "md", "markdown", "mermaid"}
L1_SIGNATURE_TABLE_MARKERS = ("签名", "参数", "返回值")


try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


TEXT_OUTPUT = True


def emit_line(message: str = "") -> None:
    if TEXT_OUTPUT:
        print(message)


@dataclass
class ValidationMessage:
    level: str
    scope: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "scope": self.scope,
            "message": self.message,
        }


@dataclass
class ValidationReport:
    errors: int = 0
    warnings: int = 0
    oks: int = 0
    messages: list[ValidationMessage] = field(default_factory=list)

    def error(self, scope: str, message: str) -> None:
        self.errors += 1
        self.messages.append(ValidationMessage(level="error", scope=scope, message=message))
        emit_line(f"  [ERROR] {scope}: {message}")

    def warn(self, scope: str, message: str) -> None:
        self.warnings += 1
        self.messages.append(ValidationMessage(level="warn", scope=scope, message=message))
        emit_line(f"  [WARN] {scope}: {message}")

    def ok(self, scope: str, message: str) -> None:
        self.oks += 1
        self.messages.append(ValidationMessage(level="ok", scope=scope, message=message))
        emit_line(f"  [OK] {scope}: {message}")

    def merge(self, other: "ValidationReport") -> None:
        self.errors += other.errors
        self.warnings += other.warnings
        self.oks += other.oks
        self.messages.extend(other.messages)

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": {
                "ok": self.oks,
                "warn": self.warnings,
                "error": self.errors,
            },
            "messages": [item.to_dict() for item in self.messages],
        }


def find_project_root(start: Path) -> Path:
    def find_doc_dir(root: Path) -> str | None:
        if (root / ACTIVE_DOC_LLM_DIR).is_dir():
            return ACTIVE_DOC_LLM_DIR
        for doc_dir in DOC_LLM_DIR_NAMES:
            if (root / doc_dir).is_dir():
                return doc_dir
        return None

    env_root = os.environ.get("DLM_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()

    cwd = Path.cwd()
    for current in [cwd, *cwd.parents]:
        if find_doc_dir(current) or (current / "dlm.config.json").is_file():
            return current

    current = start
    for _ in range(10):
        if find_doc_dir(current) or (current / "dlm.config.json").is_file():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    emit_line(f"[ERROR] Cannot find project root (no {'/ or '.join(DOC_LLM_DIR_NAMES)}/ directory found)")
    sys.exit(2)


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        emit_line(f"  [ERROR] {display_path(path)}: cannot read file: {exc}")
        return None


def display_path(path: Path) -> str:
    try:
        return resolve_path(path).relative_to(resolve_path(PROJECT_ROOT)).as_posix()
    except Exception:
        return path.as_posix().replace("\\", "/")


def resolve_path(path: Path) -> Path:
    return path.resolve(strict=False)


def is_path_within_root(path: Path, root: Path) -> bool:
    try:
        resolve_path(path).relative_to(resolve_path(root))
        return True
    except Exception:
        return False


def same_path(left: Path, right: Path) -> bool:
    return resolve_path(left) == resolve_path(right)


def extract_section(content: str, heading: str) -> str | None:
    return extract_markdown_section(content, heading)


def has_exact_heading(content: str, heading: str) -> bool:
    return extract_section(content, heading) is not None


def is_unresolved_placeholder(value: str | None) -> bool:
    if value is None:
        return False
    stripped = value.strip()
    return bool(stripped) and stripped.startswith("{") and stripped.endswith("}")


def parse_meta_table(content: str) -> dict[str, str]:
    section = extract_section(content, "## Meta")
    if section is None:
        return {}

    result: dict[str, str] = {}
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.split("|")]
        cells = [cell for cell in cells if cell]
        if len(cells) < 2:
            continue
        key, value = cells[0], cells[1]
        if key in {"字段", "------", "---"}:
            continue
        if set(key.replace("-", "").replace(":", "").strip()) == set():
            continue
        result[key] = value
    return result


def parse_yaml_frontmatter(path: Path) -> dict[str, object] | None:
    content = read_text(path)
    if content is None:
        return None

    match = re.match(r"^---\s*\n(.*?\n)---\s*\n", content, re.DOTALL)
    if not match:
        return None

    yaml_block = match.group(1)
    result: dict[str, object] = {}
    current_list_key: str | None = None

    for raw_line in yaml_block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("- "):
            value = line[2:].strip().strip('"').strip("'")
            if current_list_key and isinstance(result.get(current_list_key), list):
                result[current_list_key].append(value)
            continue

        if ":" not in line:
            current_list_key = None
            continue

        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if "#" in value:
            value = value[: value.index("#")].strip().strip('"').strip("'")

        current_list_key = None
        if value == "":
            result[key] = []
            current_list_key = key
        elif value.startswith("[") and value.endswith("]"):
            items = value.strip("[]").split(",")
            result[key] = [item.strip().strip('"').strip("'") for item in items if item.strip()]
        elif key == "match_globs":
            result[key] = []
        else:
            result[key] = value

    return result


def iter_fenced_code_blocks(content: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    pattern = re.compile(r"```([^\n`]*)\n(.*?)\n```", re.DOTALL)
    for match in pattern.finditer(content):
        language = match.group(1).strip().lower()
        body = match.group(2)
        blocks.append((language, body))
    return blocks


def count_code_fence_blocks(content: str, *, disallow_all: bool) -> tuple[int, list[str]]:
    blocks = iter_fenced_code_blocks(content)
    offenders: list[str] = []
    for language, body in blocks:
        if disallow_all:
            offenders.append(language or "(plain)")
            continue
        if language not in ALLOWED_DOC_FENCE_LANGS:
            preview = language or "(plain)"
            offenders.append(preview)
            continue
        if language in {"", "text", "plaintext", "txt", "md", "markdown"}:
            if re.search(r"[{};]|::|public\s+\w+|\w+\([^)]*\)\s*[{;]?$", body, re.MULTILINE):
                offenders.append(language or "(plain)")
    return len(blocks), offenders


def find_code_like_lines(content: str) -> list[str]:
    patterns = [
        re.compile(r"^\s*(public|private|protected|internal|static|virtual|override|sealed|async)\b.*\([^)]*\)\s*[{;]?\s*$"),
        re.compile(r"^\s*[A-Za-z_][\w<>\[\],.?*&:\s]+\s+[A-Za-z_~]\w*\([^)]*\)\s*(const)?\s*(override)?\s*[{;]?\s*$"),
        re.compile(r"^\s*[A-Za-z_]\w*\s*::\s*[A-Za-z_~]\w*\([^)]*\)\s*(const)?\s*(override)?\s*[{;]?\s*$"),
    ]
    hits: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("|") or line.startswith("#") or line.startswith(">"):
            continue
        if line.count("(") == 0 or line.count(")") == 0:
            continue
        if len(line) > 220:
            continue
        if any(pattern.match(line) for pattern in patterns):
            hits.append(line)
        if len(hits) >= 5:
            break
    return hits


def validate_utf8_readability(path: Path) -> ValidationReport:
    report = ValidationReport()
    try:
        path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        report.error(display_path(path), f"文件无法按 UTF-8 读取: {exc}")
    return report


def context_manifest_region(content: str) -> str:
    stripped = content.lstrip()
    if stripped.startswith("<context_manifest"):
        return stripped

    section = extract_section(content, CONTEXT_MANIFEST_HEADING)
    return content if section is None else section


def parse_xml_attrs(raw_attrs: str) -> dict[str, str]:
    try:
        return dict(ET.fromstring(f"<entry {raw_attrs} />").attrib)
    except ET.ParseError:
        return {}


def parse_modification_boundary(content: str) -> tuple[dict[str, str], list[dict[str, str]]] | None:
    if len(re.findall(r"</?modification_boundary\b", content)) != 2:
        return None
    match = re.search(
        r"<modification_boundary\b[^>]*>.*?</modification_boundary\s*>",
        content,
        re.DOTALL,
    )
    if match is None:
        return None
    try:
        root = ET.fromstring(match.group(0))
    except ET.ParseError:
        return None
    if (root.text or "").strip():
        return None
    for child in root:
        if child.tag != "scope" or len(child) or (child.text or "").strip() or (child.tail or "").strip():
            return None
    return dict(root.attrib), [dict(child.attrib) for child in root]


def normalize_boundary_scope(raw_path: str) -> str | None:
    value = raw_path.strip()
    if not value or value in {".", "./", "/", "**", "*/**"}:
        return None
    if "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return None
    if "{" in value or "}" in value or "//" in value:
        return None
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    wildcard_parts = [part for part in parts if "*" in part]
    if wildcard_parts and (wildcard_parts != ["**"] or parts[-1] != "**"):
        return None
    return value.rstrip("/")


def boundary_scope_covers(parent: str, child: str) -> bool:
    parent_normalized = normalize_boundary_scope(parent)
    child_normalized = normalize_boundary_scope(child)
    if parent_normalized is None or child_normalized is None:
        return False
    if parent_normalized.endswith("/**"):
        prefix = parent_normalized[:-3].rstrip("/")
        child_base = child_normalized[:-3].rstrip("/") if child_normalized.endswith("/**") else child_normalized
        return child_base == prefix or child_base.startswith(prefix + "/")
    return parent_normalized == child_normalized


def boundary_scopes_overlap(left: str, right: str) -> bool:
    return boundary_scope_covers(left, right) or boundary_scope_covers(right, left)


def validate_modification_boundary(
    content: str,
    scope: str,
    *,
    contract_kind: str,
    epic_content: str | None = None,
    warn_legacy: bool = True,
) -> ValidationReport:
    report = ValidationReport()
    parsed_boundary = parse_modification_boundary(content)
    if parsed_boundary is None:
        if re.search(r"</?modification_boundary\b", content):
            report.error(scope, "modification_boundary 必须是唯一、完整且可解析的 XML 块")
        elif warn_legacy:
            report.warn(scope, "缺少 modification_boundary；涉及写入时应明确授权写集")
        return report
    root_attrs, scopes = parsed_boundary
    if root_attrs.get("default_outside_write") != "forbidden":
        report.error(scope, "modification_boundary 必须声明 default_outside_write=forbidden")
    if contract_kind == "subtask":
        if root_attrs.get("inherits") != "epic" or root_attrs.get("mode") != "subset":
            report.error(scope, "Subtask modification_boundary 必须声明 inherits=epic mode=subset")

    normalized_scopes: list[dict[str, str]] = []
    for index, item in enumerate(scopes, start=1):
        item_scope = f"{scope} modification_boundary scope[{index}]"
        if set(item) != {"kind", "path"}:
            report.error(item_scope, "scope 仅允许 kind/path 两个属性")
            continue
        kind = item.get("kind", "")
        raw_path = item.get("path", "")
        if kind not in MODIFICATION_SCOPE_KINDS:
            report.error(item_scope, f"非法 kind: {kind}")
            continue
        normalized = normalize_boundary_scope(raw_path)
        if normalized is None:
            report.error(item_scope, f"非法或过宽的仓库相对路径: {raw_path}")
            continue
        normalized_scopes.append({"kind": kind, "path": normalized})

    write_scopes = [item["path"] for item in normalized_scopes if item["kind"] == "write"]
    generated_scopes = [item["path"] for item in normalized_scopes if item["kind"] == "generated"]
    restricted_scopes = [item for item in normalized_scopes if item["kind"] in {"read_only", "forbidden"}]
    if not write_scopes:
        report.error(scope, "modification_boundary 至少需要一个 kind=write 的明确路径")
    for mutable_path in write_scopes + generated_scopes:
        for restricted in restricted_scopes:
            if boundary_scopes_overlap(mutable_path, restricted["path"]):
                report.error(scope, f"可写路径与 {restricted['kind']} 路径重叠: {mutable_path} <-> {restricted['path']}")

    if contract_kind == "subtask":
        if epic_content is None:
            report.error(scope, "无法加载父 Epic，不能验证 Subtask 修改边界是否为子集")
        else:
            epic_boundary = parse_modification_boundary(epic_content)
            if epic_boundary is None:
                report.error(scope, "父 Epic modification_boundary 不可解析")
            else:
                _, epic_scopes = epic_boundary
                epic_by_kind = {
                    kind: [
                        item.get("path", "")
                        for item in epic_scopes
                        if item.get("kind") == kind and normalize_boundary_scope(item.get("path", "")) is not None
                    ]
                    for kind in ("write", "generated", "read_only", "forbidden")
                }
                for kind, child_paths in (("write", write_scopes), ("generated", generated_scopes)):
                    for child_path in child_paths:
                        if not any(boundary_scope_covers(parent, child_path) for parent in epic_by_kind[kind]):
                            report.error(scope, f"Subtask {kind} 路径越过父 Epic 修改边界: {child_path}")
                        for restricted_kind in ("read_only", "forbidden"):
                            for parent_path in epic_by_kind[restricted_kind]:
                                if boundary_scopes_overlap(parent_path, child_path):
                                    report.error(scope, f"Subtask {kind} 路径与父 Epic {restricted_kind} 路径重叠: {child_path} <-> {parent_path}")

    return report


def parse_context_manifest_entries(content: str) -> list[dict[str, str]] | None:
    manifests = re.findall(r"<context_manifest\b.*?</context_manifest>", context_manifest_region(content), re.DOTALL)
    if not manifests:
        return None

    entries: list[dict[str, str]] = []
    for manifest in manifests:
        root_match = re.match(r"<context_manifest\b([^>]*)>", manifest, re.DOTALL)
        root_attrs = parse_xml_attrs(root_match.group(1)) if root_match else {}
        root_priority = root_attrs.get("priority", "").strip().upper()
        for match in re.finditer(r"<file\s+([^>]*?)\/>", manifest, re.DOTALL):
            attrs = parse_xml_attrs(match.group(1))
            attrs["_manifest_priority"] = root_priority
            entries.append(attrs)
    return entries


def validate_priority_manifest_descriptions(content: str, scope: str) -> ValidationReport:
    report = ValidationReport()
    lines = context_manifest_region(content).splitlines()
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
            report.warn(scope, f"{label} context_manifest 缺少说明行; 请在标签与 XML 代码块之间补一行简短说明")
    return report


def iter_labeled_priority_manifests(content: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    lines = context_manifest_region(content).splitlines()
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
            results.append((label.upper(), manifest_text))
        index = end + 1
    return results


def validate_priority_label_contracts(content: str, scope: str) -> ValidationReport:
    report = ValidationReport()
    for label, manifest_text in iter_labeled_priority_manifests(content):
        root_match = re.match(r"<context_manifest\b([^>]*)>", manifest_text, re.DOTALL)
        root_attrs = parse_xml_attrs(root_match.group(1)) if root_match else {}
        root_priority = root_attrs.get("priority", "").strip().upper()
        if not root_priority:
            report.warn(scope, f'{label.lower()} context_manifest 缺少 priority="{label}"')
        elif root_priority in PRIORITY_VALUES and root_priority != label:
            report.error(scope, f'{label.lower()} context_manifest priority="{root_priority}" 与标签 {label.lower()} 不一致')
    return report


def priority_entry_consistency_error(priority: str, required: str | None, defer: str | None) -> str | None:
    defer_is_true = defer == "true"
    if priority == "P0" and (required != "true" or defer_is_true):
        return 'priority="P0" 要求 required="true" 且不能设置 defer="true"'
    if priority == "P1" and (required != "false" or defer_is_true):
        return 'priority="P1" 要求 required="false" 且不能设置 defer="true"'
    if priority == "P2" and (required != "false" or not defer_is_true):
        return 'priority="P2" 要求 required="false" 且必须设置 defer="true"'
    return None


def is_source_context_path(raw_path: str) -> bool:
    normalized = normalize_manifest_path(raw_path)
    lower = normalized.lower()
    doc_prefixes = tuple(f"{doc_dir.lower()}/" for doc_dir in doc_dir_candidates())
    if lower.startswith(doc_prefixes) or lower.startswith(tuple(prefix.lower() for prefix in DOC_LLM_MANIFEST_PREFIXES)):
        return False
    return Path(lower).suffix in SOURCE_CONTEXT_EXTENSIONS


def count_file_lines(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
            return sum(1 for _ in handle)
    except Exception:
        return None


def validate_large_hot_source_entries(entries: list[dict[str, str]], content: str, scope: str) -> ValidationReport:
    report = ValidationReport()
    for entry in entries:
        if entry.get("required") != "true" or entry.get("defer") == "true":
            continue

        raw_path = entry.get("path", "")
        if not is_source_context_path(raw_path):
            continue

        resolved_path = resolve_manifest_path(raw_path)
        if not is_path_within_root(resolved_path, PROJECT_ROOT) or not resolved_path.exists() or not resolved_path.is_file():
            continue

        line_count = count_file_lines(resolved_path)
        if line_count is None or line_count <= LARGE_SOURCE_LINE_THRESHOLD:
            continue

        report.warn(
            scope,
            f"P0 source {raw_path} 有 {line_count} 行; 不要全文读取, 应按 intent 先抽符号骨架再局部读取",
        )
    return report


def normalize_manifest_path(raw_path: str) -> str:
    normalized = raw_path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def manifest_path_to_doc_llm_relative(raw_path: str) -> str | None:
    normalized = normalize_manifest_path(raw_path)
    for doc_dir in doc_dir_candidates():
        prefix = f"{doc_dir}/"
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    if normalized.startswith(DOC_LLM_MANIFEST_PREFIXES):
        return normalized
    return None


def resolve_manifest_path(raw_path: str) -> Path:
    raw_candidate = Path(raw_path)
    if raw_candidate.is_absolute():
        return resolve_path(raw_candidate)

    normalized = normalize_manifest_path(raw_path)
    active_dir = active_doc_llm_dir()
    for doc_dir in doc_dir_candidates():
        prefix = f"{doc_dir}/"
        if normalized.startswith(prefix):
            if doc_dir == active_dir:
                return resolve_path(PROJECT_ROOT / normalized)
            return resolve_path(doc_llm_root() / normalized[len(prefix) :])

    doc_llm_relative = manifest_path_to_doc_llm_relative(raw_path)
    if doc_llm_relative is not None:
        return resolve_path(doc_llm_root() / doc_llm_relative)

    return resolve_path(PROJECT_ROOT / normalized)


def is_directory_like_path(raw_path: str) -> bool:
    if raw_path.endswith("/") or raw_path.endswith("\\"):
        return True
    candidate = resolve_manifest_path(raw_path)
    return candidate.exists() and candidate.is_dir()


def validate_manifest_route_binding(
    entry_scope: str,
    raw_path: str,
    resolved_path: Path,
) -> ValidationReport:
    report = ValidationReport()
    if not resolved_path.exists():
        return report

    doc_llm_relative = manifest_path_to_doc_llm_relative(raw_path)
    if not doc_llm_relative or not doc_llm_relative.startswith("Domains/"):
        return report

    l2_match = re.match(r"Domains/([^/]+)/L2_Modules/([^/]+\.md)$", doc_llm_relative)
    ref_match = re.match(r"Domains/([^/]+)/references/([^/]+)/README\.md$", doc_llm_relative)
    if not l2_match and not ref_match:
        return report

    domain_name = (l2_match or ref_match).group(1)
    domain_dir = doc_llm_root() / "Domains" / domain_name
    l1_files = sorted(domain_dir.glob("L1_*_router.md"))
    if not l1_files:
        report.error(entry_scope, f"对应 Domain 缺少 L1 路由文件: {domain_name}")
        return report

    markers: list[str]
    if l2_match:
        markers = [f"`L2_Modules/{l2_match.group(2)}`"]
    else:
        topic = ref_match.group(2)
        markers = [f"`references/{topic}/README.md`"]

    matched = False
    for l1_file in l1_files:
        content = read_text(l1_file)
        if content is None:
            continue
        if any(marker in content for marker in markers):
            matched = True
            break

    if not matched:
        report.error(entry_scope, f"入口未出现在对应 Domain 的 L1 路由表中: {doc_llm_relative}")

    return report


def extract_subtask_id(stem: str) -> str:
    match = re.match(r"(S\d+[a-z]?)", stem)
    return match.group(1) if match else stem


def parse_dependency_ids(raw_value: str | None) -> list[str]:
    return parse_dependency_references(raw_value)[0]


def validate_subtask_dependency_graph(subtask_files: list[Path]) -> ValidationReport:
    """Check only the supplied tasks and their reachable dependency references.

    Epic-qualified references retain their identity. An Epic-only dependency
    checks that Epic's existence, not every historical Subtask or its status.
    """
    report = ValidationReport()
    tasks_dir = doc_llm_root() / "Tasks"
    epic_cache: dict[str, list[Path]] = {}
    task_cache: dict[Path, dict[str, list[Path]]] = {}
    visit_state: dict[Path, int] = {}
    stack: list[Path] = []

    def task_map(epic_dir: Path) -> dict[str, list[Path]]:
        if epic_dir not in task_cache:
            found: dict[str, list[Path]] = {}
            for path in sorted((epic_dir / "subtasks").glob("S*.md")):
                found.setdefault(extract_subtask_id(path.stem), []).append(path)
            task_cache[epic_dir] = found
        return task_cache[epic_dir]

    def label(path: Path) -> str:
        return path.parent.parent.name.split("_", 1)[0] + "/" + extract_subtask_id(path.stem)

    def resolve_dependency(dependency: str, source: Path) -> Path | None:
        parts = dependency.split("/")
        if parts[0].startswith("E"):
            epic_id = parts[0]
            if epic_id not in epic_cache:
                epic_cache[epic_id] = sorted(
                    path for base in (tasks_dir, tasks_dir / "done")
                    for path in base.glob(epic_id + "_*") if path.is_dir()
                )
            candidates = epic_cache[epic_id]
            if len(candidates) != 1:
                reason = "不存在" if not candidates else "不唯一"
                report.error(display_path(source), f"依赖 Epic {reason}: {epic_id}")
                return None
            epic_dir = candidates[0]
            if not is_path_within_root(epic_dir, tasks_dir):
                report.error(display_path(source), f"依赖路径越出 Tasks: {dependency}")
                return None
            if len(parts) == 1:
                if not (epic_dir / "README.md").is_file():
                    report.error(display_path(source), f"依赖 Epic 缺少 README.md: {dependency}")
                return None
            task_id = parts[1]
        else:
            epic_dir, task_id = source.parent.parent, parts[0]
        candidates = task_map(epic_dir).get(task_id, [])
        if len(candidates) != 1:
            reason = "不存在" if not candidates else "不唯一"
            report.error(display_path(source), f"依赖指向{reason}的 Subtask: {dependency}")
            return None
        target = candidates[0]
        if not is_path_within_root(target, tasks_dir):
            report.error(display_path(source), f"依赖路径越出 Tasks: {dependency}")
            return None
        return target.resolve()

    def dfs(node: Path) -> None:
        state = visit_state.get(node, 0)
        if state == 1:
            cycle_start = stack.index(node)
            cycle = stack[cycle_start:] + [node]
            report.error(display_path(node), f"Subtask 依赖存在环: {' -> '.join(map(label, cycle))}")
            return
        if state == 2:
            return
        visit_state[node] = 1
        stack.append(node)
        content = read_text(node)
        if content is None:
            report.error(display_path(node), "依赖任务读取失败")
        else:
            try:
                dependencies, prose = parse_dependency_references(parse_meta_table(content).get("依赖"))
            except ValueError as exc:
                report.error(display_path(node), str(exc))
                dependencies, prose = [], ""
            if prose:
                report.warn(display_path(node), f"依赖含未作机械判断的条件：{prose}；仅核对已识别 ID，产物就绪需执行者核实")
            for dependency in dependencies:
                target = resolve_dependency(dependency, node)
                if target is not None:
                    dfs(target)
        stack.pop()
        visit_state[node] = 2
    for path in subtask_files:
        # Resolve seeds too: duplicate local IDs cannot silently overwrite nodes.
        target = resolve_dependency(extract_subtask_id(path.stem), path)
        if target is not None:
            dfs(target)
    return report


def validate_backlog_sync(tasks_dir: Path, epic_dirs: list[Path]) -> ValidationReport:
    report = ValidationReport()
    backlog_path = tasks_dir / "backlog.md"
    content = read_text(backlog_path)
    if content is None:
        report.error(display_path(backlog_path), "读取失败")
        return report

    for epic_dir in epic_dirs:
        expected_link = f"({epic_dir.name}/README.md)"
        if expected_link not in content:
            report.error(display_path(backlog_path), f"backlog 缺少 Epic 索引: {epic_dir.name}")

    if report.errors == 0:
        report.ok(display_path(backlog_path), "backlog Epic 索引通过")
    return report


def validate_l0_size(path: Path) -> ValidationReport:
    report = ValidationReport()
    content = read_text(path)
    if content is None:
        return report

    char_count = len(content)
    if char_count > L0_RECOMMENDED_MAX_CHARS:
        report.warn(display_path(path), f"L0 建议控制在 {L0_RECOMMENDED_MAX_CHARS} 字以内，当前 {char_count} 字")
    return report


def validate_l0_purity(path: Path) -> ValidationReport:
    report = ValidationReport()
    content = read_text(path)
    if content is None:
        return report

    scope = display_path(path)
    _, fence_offenders = count_code_fence_blocks(content, disallow_all=True)
    if fence_offenders:
        report.warn(scope, "L0 不应包含代码块；检测到 fenced code block")

    code_like_lines = find_code_like_lines(content)
    if code_like_lines:
        preview = " | ".join(code_like_lines[:2])
        report.warn(scope, f"L0 只应写约束；检测到疑似实现/签名行: {preview}")
    return report


def validate_l2_size(path: Path) -> ValidationReport:
    report = ValidationReport()
    size_bytes = path.stat().st_size
    if size_bytes < L2_RECOMMENDED_MIN_BYTES:
        report.warn(
            display_path(path),
            f"L2 建议控制在 3-8KB；当前 {size_bytes / 1024:.2f}KB，建议评估是否合并",
        )
    elif size_bytes > L2_RECOMMENDED_MAX_BYTES:
        report.warn(
            display_path(path),
            f"L2 建议控制在 3-8KB；当前 {size_bytes / 1024:.2f}KB，建议评估是否拆分",
        )
    return report


def validate_l1_purity(path: Path) -> ValidationReport:
    report = ValidationReport()
    content = read_text(path)
    if content is None:
        return report

    scope = display_path(path)
    _, fence_offenders = count_code_fence_blocks(content, disallow_all=False)
    if fence_offenders:
        offenders = ", ".join(sorted(set(fence_offenders[:3])))
        report.warn(scope, f"L1 不应包含实现代码块；检测到可疑 fenced block: {offenders}")

    signature_table_hits = [
        line.strip()
        for line in content.splitlines()
        if line.strip().startswith("|") and any(marker in line for marker in L1_SIGNATURE_TABLE_MARKERS)
    ]
    if signature_table_hits:
        report.warn(scope, f"L1 不应维护签名/参数表；检测到可疑表头: {signature_table_hits[0]}")

    code_like_lines = find_code_like_lines(content)
    if code_like_lines:
        preview = " | ".join(code_like_lines[:2])
        report.warn(scope, f"L1 只应做路由和拓扑；检测到疑似实现/签名行: {preview}")
    return report


def validate_context_manifest(content: str, scope: str) -> ValidationReport:
    report = ValidationReport()
    entries = parse_context_manifest_entries(content)
    if entries is None:
        report.error(scope, "缺少 <context_manifest> XML 清单")
        return report

    if not entries:
        report.error(scope, "<context_manifest> 中没有任何 <file /> 条目")
        return report

    report.merge(validate_priority_manifest_descriptions(content, scope))
    report.merge(validate_priority_label_contracts(content, scope))
    report.merge(validate_large_hot_source_entries(entries, content, scope))

    invalid_priorities = sorted(
        {
            entry.get("_manifest_priority", "")
            for entry in entries
            if entry.get("_manifest_priority", "") and entry.get("_manifest_priority", "") not in PRIORITY_VALUES
        }
    )
    for priority in invalid_priorities:
        report.error(scope, f"context_manifest priority 必须是 P0 / P1 / P2, 当前为: {priority}")

    for index, entry in enumerate(entries, start=1):
        entry_scope = f"{scope} <file #{index}>"
        required = entry.get("required")
        if required not in {"true", "false"}:
            report.error(entry_scope, "required 必须显式写为 true 或 false")

        priority = entry.get("_manifest_priority", "")
        if priority in PRIORITY_VALUES:
            priority_error = priority_entry_consistency_error(priority, required, entry.get("defer"))
            if priority_error:
                report.error(entry_scope, priority_error)

        raw_path = entry.get("path")
        if not raw_path:
            report.error(entry_scope, "缺少 path 属性")
        elif is_directory_like_path(raw_path):
            report.error(entry_scope, f"path 必须指向具体文件, 当前像目录路径: {raw_path}")
        else:
            resolved_path = resolve_manifest_path(raw_path)
            if not is_path_within_root(resolved_path, PROJECT_ROOT):
                report.error(entry_scope, f"path 越出仓库根目录: {raw_path}")
                continue
            if required == "true" and not resolved_path.exists():
                report.error(entry_scope, f"required 文件不存在: {display_path(resolved_path)}")
            section_issue = section_reference_issue(resolved_path, entry.get("section"))
            if section_issue:
                message = f"{raw_path}: {section_issue}"
                if required == "true":
                    report.error(entry_scope, message)
                else:
                    report.warn(entry_scope, message)
            report.merge(validate_manifest_route_binding(entry_scope, raw_path, resolved_path))

        if entry.get("defer") == "true" and required != "false":
            report.error(entry_scope, 'defer="true" 只能与 required="false" 搭配')

    return report


def validate_epic_readme(path: Path) -> ValidationReport:
    report = ValidationReport()
    content = read_text(path)
    if content is None:
        report.error(display_path(path), "读取失败")
        return report

    scope = display_path(path)
    local_errors = report.errors

    for heading in EPIC_REQUIRED_HEADERS:
        if not has_exact_heading(content, heading):
            report.error(scope, f"缺少必需段落: {heading}")
    if not has_exact_heading(content, "## 核心目标") and not has_exact_heading(content, "## 目标 (What)"):
        report.error(scope, "缺少目标段落: ## 核心目标")
    if not has_exact_heading(content, "## 当前状态") and not has_exact_heading(content, "## 背景 (Why)"):
        report.error(scope, "缺少当前状态段落: ## 当前状态")

    report.merge(validate_context_manifest(content, scope))
    report.merge(validate_modification_boundary(content, scope, contract_kind="epic"))
    report.merge(validate_task_content(content, scope))
    report.merge(validate_acceptance_responsibility_refs(content, path))
    report.merge(validate_principles(content, scope))
    meta = parse_meta_table(content)
    if re.search(r"^\|\s*原则确认\s*\|", extract_section(content, "## Meta") or "", re.MULTILINE):
        try:
            require_confirmation(meta.get("原则确认", ""))
        except ValueError as exc:
            report.error(scope, str(exc))

    if report.errors == local_errors:
        report.ok(scope, "Epic README 结构与已声明内容检查通过")
    return report


def validate_execution_plan(path: Path) -> ValidationReport:
    report = ValidationReport()
    content = read_text(path)
    if content is None:
        report.error(display_path(path), "读取失败")
        return report

    scope = display_path(path)
    local_errors = report.errors

    for heading in EXECUTION_PLAN_REQUIRED_HEADERS:
        if not has_exact_heading(content, heading):
            report.error(scope, f"缺少标准执行方案段落: {heading}")

    report.merge(validate_task_content(content, scope))
    if report.errors == local_errors:
        report.ok(scope, "Execution Plan 结构与已声明内容检查通过")
    return report


def validate_task_depth(content: str, meta: dict[str, str], scope: str, *, warn_legacy: bool = True) -> ValidationReport:
    """Validate an explicit assessment without guessing depth for historical records."""
    report = ValidationReport()
    meta_section = extract_section(content, "## Meta") or ""
    declared = bool(re.search(r"^\|\s*任务深度\s*\|", meta_section, re.MULTILINE))
    assessment = extract_section(content, "## 任务深度评估")
    if not declared and assessment is None:
        if warn_legacy:
            report.warn(scope, "历史任务未评估任务深度；新建或重规划时显式评估，不自动赋值")
        return report
    depth = meta.get("任务深度")
    if depth not in TASK_DEPTH_VALUES:
        report.error(scope, f"任务深度必须为 {' / '.join(TASK_DEPTH_VALUES)}，当前为: {depth or '缺失/空值'}")
    if assessment is None:
        report.error(scope, "声明任务深度时必须有 ## 任务深度评估")
    else:
        for label in ("预估改动量", "评估依据"):
            match = re.search(rf"^-[ \t]*{label}[：:][ \t]*(.*?)[ \t]*$", assessment, re.MULTILINE)
            value = match.group(1).strip() if match else ""
            if not value or is_unresolved_placeholder(value):
                report.error(scope, f"任务深度评估缺少有效{label}")
    return report


LEGACY_TASK_HINTS = (
    "本 Subtask 的所有动作都必须推动这个目标、消除会改变路线的未知，或降低已点名的实质风险。",
    "状态记录已有授权和完成事实。会话已明确授权时，执行者可据此记录 `Frozen`；旧的 `Draft` 标记不要求重复征求同一授权。状态更新不产生或扩大权限，也不把原因假设变成已验证事实。直接证据与停止线成立后记录 `Verified`。",
    "后续任务、知识加载和推进动作始终服务这个目标；新发现没有因果关系时不进入当前任务流。",
)


def task_text(content: str) -> str:
    """Ignore exact template guidance, retaining ordinary quoted task content."""
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    hints = {re.sub(r"\s+", "", hint) for hint in LEGACY_TASK_HINTS}
    lines: list[str] = []
    for line in content.splitlines():
        quoted = re.match(r"^[ \t]*>[ \t]?(.*)$", line)
        if quoted and re.sub(r"\s+", "", quoted.group(1)) in hints:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


@lru_cache(maxsize=1)
def task_template_tokens() -> frozenset[str]:
    # Current tokens come from their actual owner; explicit historical tokens
    # remain recognized after template evolution. Arbitrary {x} is valid prose.
    historical = {
        "{核心目标}", "{预期效果}", "{方案承接与实施设计}", "{共享设计与取舍}",
        "{描述本 Subtask 如何推动 Epic 已确认的真实效果}",
        "{本 Subtask 必须保持的理念与取舍}",
        "{出现什么表现说明虽有产物但方向错误}",
    }
    template_dir = SCRIPT_DIR / "legacy" / "templates"
    for name in ("subtask_template.md", "epic_template.md"):
        template = template_dir / name
        if template.is_file():
            historical.update(re.findall(r"\{[^{}\n]+\}", template.read_text(encoding="utf-8-sig")))
    return frozenset(historical)


def task_lines(content: str, *, include_code: bool = False, code_marker: bool = False) -> list[str]:
    """Keep line positions; fenced examples are content but not unfinished prose."""
    lines: list[str] = []
    fence = ""
    for line in task_text(content).splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ""
                lines.append("")
            else:
                lines.append(line if include_code else ("`代码`" if code_marker and line.strip() else ""))
        elif marker:
            fence = marker[1]
            lines.append("")
        else:
            lines.append(line)
    return lines


def task_placeholders(content: str) -> list[str]:
    # Code examples may intentionally contain braces or template syntax. Only
    # unfinished prose fields are checked; this is not a semantic quality score.
    prose = "\n".join(task_lines(content))
    prose = re.sub(r"(`+).*?\1", "", prose)
    placeholders = [token for token in re.findall(r"\{[^{}\n]+\}", prose) if token in task_template_tokens()]
    for line in prose.splitlines():
        for cell in line.split("|") if line.strip().startswith("|") else [line]:
            value = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?", "", cell).strip()
            value = re.split(r"[：:]", value, maxsplit=1)[-1].strip().strip("*")
            if value.casefold() in {"todo", "tbd", "待填写", "待补充"}:
                placeholders.append(value)
    return list(dict.fromkeys(placeholders))


def current_task_text(content: str) -> str:
    """Exclude explicitly historical sections without relying on task status."""
    content = task_text(content)
    lines = content.splitlines()
    headings = markdown_heading_spans(content)
    for index, (start, level, title) in enumerate(headings):
        if not re.match(r"^(执行日志|历史记录|历史方案|历史证据|历史决策|变更历史|变更记录|历史)(?:$|\s|[（(:：])", title):
            continue
        end = next((line - 1 for line, depth, _ in headings[index + 1:] if depth <= level), len(lines))
        lines[start - 1:end] = [""] * (end - start + 1)
    return "\n".join(lines)


def substantive_task_body(body: str) -> str:
    """Ignore headings and table scaffolding, preserving prose, values and code."""
    body = task_text(body)
    headings = {line - 1 for line, _level, _title in markdown_heading_spans(body)}
    lines = task_lines(body, include_code=True)
    separator = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
    kept: list[str] = []
    key_value_table = False
    for index, line in enumerate(lines):
        if index in headings or separator.match(line):
            continue
        if "|" in line and index + 1 < len(lines) and separator.match(lines[index + 1]):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            key_value_table = len(cells) == 2 and cells[0] in {"字段", "项目", "事项", "条目"} and cells[1] in {"值", "内容"}
            continue
        if not line.strip().startswith("|"):
            key_value_table = False
        elif key_value_table:
            cells = line.strip().strip("|").split("|")
            line = " | ".join(cells[1:])
        kept.append(re.sub(r"^\s*[-*+]\s+\[[ xX]\]\s*", "", line))
    return "\n".join(kept)


def validate_task_body(body: str, scope: str, label: str) -> ValidationReport:
    report = ValidationReport()
    if not re.search(r"[\w\u4e00-\u9fff]", substantive_task_body(body)):
        report.error(scope, f"任务内容为空: {label}")
    lines = task_lines(body, code_marker=True)
    for index, line in enumerate(lines):
        field = re.match(r"^(\s*)[-*+]\s+([^：:]+)[：:]\s*$", line)
        if field is None:
            continue
        following = next((item for item in lines[index + 1:] if item.strip()), "")
        indentation = len(following) - len(following.lstrip())
        # A normal paragraph is also a valid Markdown list continuation.
        peer = re.match(r"^\s*(?:[-*+]\s|\d+[.)]\s|#{1,6}\s)", following)
        if not following or (peer and indentation <= len(field.group(1))):
            report.error(scope, f"任务字段为空 ({label}): {field.group(2).strip()}")
    return report


def task_acceptance_fields(body: str) -> dict[str, list[str]]:
    """Recognize explicit fields; absence of a label never proves missing meaning."""
    body = task_text(body)
    lines = task_lines(body, code_marker=True)
    values: dict[str, list[str]] = {"直接证据": [], "验收入口": [], "停止线": []}
    headings = markdown_heading_spans(body)
    for index, (start, level, title) in enumerate(headings):
        label = title.strip("*")
        if label not in values:
            continue
        end = next((line - 1 for line, depth, _ in headings[index + 1:] if depth <= level), len(lines))
        values[label].append("\n".join(lines[start:end]).strip())
        lines[start - 1:end] = [""] * (end - start + 1)
    pattern = re.compile(r"^([ \t]*)(?:[-*+][ \t]+)?(?:\*\*)?(直接证据|验收入口|停止线)(?:\*\*)?[：:][ \t]*(.*?)[ \t]*$")
    index = 0
    while index < len(lines):
        if lines[index].strip().startswith("|"):
            cells = [cell.strip().strip("*") for cell in lines[index].strip().strip("|").split("|")]
            if cells[0] in values:
                values[cells[0]].append(" | ".join(cells[1:]).strip(" |"))
                index += 1
                continue
        match = pattern.match(lines[index])
        if match is None:
            index += 1
            continue
        value_lines = [match.group(3)]
        indentation = len(match.group(1).expandtabs())
        index += 1
        while index < len(lines):
            line = lines[index]
            if line.strip():
                prefix = re.match(r"^[ \t]*", line).group(0)
                if len(prefix.expandtabs()) <= indentation:
                    peer = re.match(r"^\s*(?:[-*+]\s|\d+[.)]\s|#{1,6}\s|\|)", line)
                    if match.group(3).strip() or peer:
                        break
            value_lines.append(line)
            index += 1
        values[match.group(2)].append(task_text("\n".join(value_lines)))
    return values


def validate_task_content(content: str, scope: str) -> ValidationReport:
    """Check definite unfinished content, without prescribing a new schema."""
    report = ValidationReport()
    current = current_task_text(content)
    for token in task_placeholders(current):
        report.error(scope, f"任务当前正文保留模板占位符: {token}")
    # Only inspect sections that exist. Legacy or equivalent prose does not
    # need new headings; planning owns semantic coverage and evidence quality.
    for label in (
        PRINCIPLES_TITLE, SUBTASK_PRINCIPLES, "核心目标", "目标", "目标 (What)", "当前状态", "背景 (Why)",
        "输入与交付", "输入与输出", "方案承接与实施设计", "实施设计", "实施方案",
        "职责与拟实施范围", "方案与边界", "共享设计与取舍", "预期效果", "行动模型", "当前推进",
        "完成判断", "检查点", "当前动作与停止线", "行动与停止线", "停止与续接", "授权与停止线",
    ):
        body = extract_section(current, "## " + label)
        if body is None:
            continue
        report.merge(validate_task_body(body, scope, label))
        if label in {"完成判断", "检查点"}:
            fields = task_acceptance_fields(body)
            for field, entries in fields.items():
                for value in entries:
                    if not re.search(r"[\w\u4e00-\u9fff]", substantive_task_body(value)):
                        report.error(scope, f"任务字段为空 ({label}): {field}")
    return report


def validate_acceptance_responsibility_refs(content: str, path: Path) -> ValidationReport:
    """Check explicit local IDs, equally in prose, lists and tables."""
    report = ValidationReport()
    section = extract_section(current_task_text(content), "## 关键要求与验收责任")
    if section is None:
        return report
    known_ids = {extract_subtask_id(item.stem) for item in (path.parent / "subtasks").glob("S*.md")}
    epic_match = re.match(r"E\d+", path.parent.name)
    epic_id = epic_match[0] if epic_match else ""
    text = "\n".join(task_lines(section))
    text = re.sub(r"(E\d+)[/／](S\d+[a-z]?)", lambda match: match[2] if match[1] == epic_id else "", text)
    for task_id in sorted(set(re.findall(r"(?<![/\w])S\d+[a-z]?(?!\w)", text))):
        if task_id not in known_ids:
            report.error(display_path(path), f"关键要求与验收责任引用不存在的 Subtask: {task_id}")
    return report


def validate_principles(content: str, scope: str, *, epic_content: str | None = None) -> ValidationReport:
    report = ValidationReport()
    try:
        if epic_content is None:
            # Historical Epics migrate explicitly; creation always requires the field.
            if principles_span(content) is not None:
                require_principles(content)
        elif principles_span(epic_content) is not None:
            expected = require_principles(epic_content)
            actual = require_principles(content)
            if actual != expected:
                report.error(scope, "Epic 核心原则与 Epic 源文不一致；v1 记录只读；续接时用 dlm adopt 导入 v2")
        elif principles_span(content) is not None:
            report.error(scope, "子任务包含 Epic 核心原则，但父 Epic 缺少源字段")
    except ValueError as exc:
        report.error(scope, str(exc))
    return report


def validate_subtask(path: Path, *, warn_legacy: bool = True) -> ValidationReport:
    report = ValidationReport()
    content = read_text(path)
    if content is None:
        report.error(display_path(path), "读取失败")
        return report

    scope = display_path(path)
    local_errors = report.errors

    for heading in SUBTASK_REQUIRED_HEADERS:
        if not has_exact_heading(content, heading):
            report.error(scope, f"缺少必需段落: {heading}")
    if not has_exact_heading(content, "## 核心目标") and not has_exact_heading(content, "## 目标"):
        report.error(scope, "缺少目标段落: ## 核心目标")
    if not has_exact_heading(content, "## 完成判断") and not has_exact_heading(content, "## 检查点"):
        report.error(scope, "缺少完成判断段落: ## 完成判断 或旧版 ## 检查点")

    meta = parse_meta_table(content)
    if not meta:
        report.error(scope, "缺少可解析的 Meta 表格")
    else:
        for field in SUBTASK_REQUIRED_META_FIELDS:
            if field not in meta:
                report.error(scope, f"Meta 缺少字段: {field}")
        status = meta.get("状态")
        if status and status not in ALLOWED_STATUS_VALUES:
            report.error(scope, f"状态值非法: {status}")

    report.merge(validate_task_depth(content, meta, scope, warn_legacy=warn_legacy))
    report.merge(validate_context_manifest(content, scope))
    report.merge(validate_task_content(content, scope))

    epic_readme = path.parent.parent / "README.md"
    epic_content = read_text(epic_readme) if epic_readme.exists() else None
    report.merge(validate_principles(content, scope, epic_content=epic_content))
    try:
        # Historical tasks adopt this contract when resumed; do not invent past reviews.
        opted_in = uses_principle_reviews(content)
        claims_completion = meta.get("状态") == "✅ 完成" or is_verified_baseline(meta.get("基线状态", ""))
        if opted_in:
            require_principles(content, title=SUBTASK_PRINCIPLES)
            if claims_completion:
                if epic_content is None:
                    raise ValueError("缺少父 Epic，不能核对完成前原则复盘")
                check_reviews(content, epic_content, phase="completion")
        elif claims_completion:
            report.warn(scope, "历史兼容：未发现原则闭环标记或局部复盘字段，未检查原则复盘；结构通过不代表该完成声明已验证")
    except ValueError as exc:
        report.error(scope, str(exc))
    report.merge(
        validate_modification_boundary(
            content,
            scope,
            contract_kind="subtask",
            epic_content=epic_content,
            warn_legacy=warn_legacy,
        )
    )

    if report.errors == local_errors:
        report.ok(scope, "Subtask 结构与已声明内容检查通过")
    return report


def validate_reference_topic_readme(path: Path) -> ValidationReport:
    report = ValidationReport()
    scope = display_path(path)
    meta = parse_yaml_frontmatter(path)
    if meta is None:
        report.error(scope, "缺少 YAML front-matter")
        return report

    local_errors = report.errors
    required_keys = [
        "domain",
        "topic",
        "kind",
        "reference_type",
        "authority",
        "match_globs",
        "status",
        "last_updated",
    ]
    for key in required_keys:
        value = meta.get(key)
        if value in (None, "", []):
            report.error(scope, f"front-matter 缺少字段: {key}")

    if meta.get("kind") != "reference_topic":
        report.error(scope, 'kind 必须为 "reference_topic"')

    if report.errors == local_errors:
        report.ok(scope, "Reference topic front-matter 通过")
    return report


def validate_sop_readme(path: Path) -> ValidationReport:
    report = ValidationReport()
    scope = display_path(path)
    content = read_text(path)
    if content is None:
        report.error(scope, "读取失败")
        return report

    local_errors = report.errors
    meta = parse_yaml_frontmatter(path)
    if meta is None:
        report.error(scope, "缺少 YAML front-matter")
    else:
        for key in SOP_REQUIRED_FRONTMATTER_KEYS:
            value = meta.get(key)
            if value in (None, "", []):
                report.error(scope, f"front-matter 缺少字段: {key}")

        if meta.get("kind") != "sop":
            report.error(scope, 'kind 必须为 "sop"')

        status = meta.get("status")
        if status and status not in SOP_ALLOWED_STATUS_VALUES:
            report.error(scope, f"SOP status 非法: {status}")

        owner_domain = meta.get("owner_domain")
        if isinstance(owner_domain, str) and owner_domain not in {"global", "_GLOBAL"} and owner_domain.startswith("Domains/"):
            owner_path = doc_llm_root() / owner_domain
            if not owner_path.is_dir():
                report.warn(scope, f"owner_domain 指向的 Domain 不存在: {owner_domain}")

    if report.errors == local_errors:
        report.ok(scope, "SOP 路由契约通过")
    return report


def validate_sops_root(sops_dir: Path) -> ValidationReport:
    report = ValidationReport()
    scope = display_path(sops_dir)

    index_path = sops_dir / "README.md"
    if not index_path.exists():
        report.warn(scope, "缺少 SOPs/README.md 路由表")
    else:
        report.merge(validate_utf8_readability(index_path))

    sop_readmes = sorted(
        path for path in sops_dir.glob("*/README.md")
        if path.parent.name != "checks"
    )
    if not sop_readmes:
        report.warn(scope, "SOPs/ 下没有场景 SOP")
    for sop_readme in sop_readmes:
        report.merge(validate_utf8_readability(sop_readme))
        report.merge(validate_sop_readme(sop_readme))

    if report.errors == 0:
        report.ok(scope, "SOPs 契约通过")
    return report


def validate_l1_router(path: Path, has_reference_topics: bool) -> ValidationReport:
    report = ValidationReport()
    content = read_text(path)
    if content is None:
        report.error(display_path(path), "读取失败")
        return report

    scope = display_path(path)
    local_errors = report.errors

    if not re.search(L2_HEADING_PATTERN, content, re.MULTILINE):
        report.error(scope, "缺少 `## L2 路由表`（可带章节编号）")

    has_reference_section = bool(re.search(REFERENCE_HEADING_PATTERN, content, re.MULTILINE))
    if has_reference_topics and not has_reference_section:
        report.error(scope, "存在 Domain reference topic, 但缺少 `## 参考资料路由`（可带章节编号）")
    if not has_reference_topics and has_reference_section:
        report.error(scope, "当前 Domain 没有 reference topic, 但 L1 仍保留了 `## 参考资料路由`")

    if report.errors == local_errors:
        report.ok(scope, "L1 路由结构通过")
    return report


def validate_domain(domain_dir: Path) -> ValidationReport:
    report = ValidationReport()
    scope = display_path(domain_dir)

    required_paths = [
        domain_dir / "BRIEF.md",
        domain_dir / "L2_Modules",
    ]
    for required_path in required_paths:
        if not required_path.exists():
            report.error(scope, f"缺少必需路径: {display_path(required_path)}")

    l1_files = sorted(domain_dir.glob("L1_*_router.md"))
    if not l1_files:
        report.error(scope, "缺少 L1_*_router.md")
    elif len(l1_files) > 1:
        report.warn(scope, f"发现多个 L1 路由文件, 将逐个校验: {len(l1_files)}")

    topic_readmes = sorted(domain_dir.glob("references/*/README.md"))
    has_reference_topics = bool(topic_readmes)
    l0_files = sorted(domain_dir.glob("L0_*_rules.md"))
    l2_files = sorted((domain_dir / "L2_Modules").glob("*.md")) if (domain_dir / "L2_Modules").exists() else []

    for l1_file in l1_files:
        report.merge(validate_utf8_readability(l1_file))
        report.merge(validate_l1_router(l1_file, has_reference_topics))
        report.merge(validate_l1_purity(l1_file))

    for topic_readme in topic_readmes:
        report.merge(validate_utf8_readability(topic_readme))
        report.merge(validate_reference_topic_readme(topic_readme))

    for l0_file in l0_files:
        report.merge(validate_utf8_readability(l0_file))
        report.merge(validate_l0_size(l0_file))
        report.merge(validate_l0_purity(l0_file))

    for l2_file in l2_files:
        report.merge(validate_utf8_readability(l2_file))
        report.merge(validate_l2_size(l2_file))

    if report.errors == 0:
        report.ok(scope, "Domain 契约通过")
    return report


def validate_global_root(global_dir: Path) -> ValidationReport:
    report = ValidationReport()
    scope = display_path(global_dir)

    required_files = [
        global_dir / "L0_global_redlines.md",
        global_dir / "L1_target_architecture.md",
    ]
    local_errors = report.errors

    for required_file in required_files:
        if not required_file.exists():
            report.error(scope, f"缺少全局必需文件: {display_path(required_file)}")

    l0_path = global_dir / "L0_global_redlines.md"
    if l0_path.exists():
        report.merge(validate_utf8_readability(l0_path))
        report.merge(validate_l0_size(l0_path))
        report.merge(validate_l0_purity(l0_path))

    l1_path = global_dir / "L1_target_architecture.md"
    if l1_path.exists():
        report.merge(validate_utf8_readability(l1_path))
        report.merge(validate_l1_purity(l1_path))

    if report.errors == local_errors:
        report.ok(scope, "Global 契约通过")
    return report


def validate_epic_dir(epic_dir: Path) -> ValidationReport:
    report = ValidationReport()
    if not (epic_dir / "README.md").exists():
        report.error(display_path(epic_dir), "缺少 Epic README.md")
        return report

    report.merge(validate_epic_readme(epic_dir / "README.md"))

    plan_dir = epic_dir / "plan"
    if plan_dir.exists():
        plan_files = sorted(plan_dir.glob("*.md"))
        if not plan_files:
            report.warn(display_path(plan_dir), "plan/ 目录存在但没有 .md 文件")
        for plan_file in plan_files:
            report.merge(validate_execution_plan(plan_file))

    subtasks_dir = epic_dir / "subtasks"
    if not subtasks_dir.exists():
        report.error(display_path(epic_dir), "缺少 subtasks/ 目录")
    else:
        subtask_files = sorted(subtasks_dir.glob("S*.md"))
        if not subtask_files:
            report.error(display_path(subtasks_dir), "subtasks/ 中没有 S*.md")
        for subtask_file in subtask_files:
            report.merge(validate_subtask(subtask_file, warn_legacy=False))
        if subtask_files:
            report.merge(validate_subtask_dependency_graph(subtask_files))

    return report


def validate_tasks_root(tasks_dir: Path) -> ValidationReport:
    report = ValidationReport()
    epic_dirs = sorted(
        path for path in tasks_dir.iterdir()
        if path.is_dir() and path.name.startswith("E")
    )
    report.merge(validate_backlog_sync(tasks_dir, epic_dirs))
    for epic_dir in epic_dirs:
        if (epic_dir / "ledger.json").exists():
            # v2 ledgers are checked by `dlm check`; this validator only reports v1 records.
            continue
        emit_line(f"\n{'=' * 60}\nEpic: {epic_dir.name}\n{'=' * 60}")
        report.merge(validate_epic_dir(epic_dir))
    return report


def validate_domains_root(domains_dir: Path) -> ValidationReport:
    report = ValidationReport()
    domain_dirs = sorted(path for path in domains_dir.iterdir() if path.is_dir())
    for domain_dir in domain_dirs:
        emit_line(f"\n{'=' * 60}\nDomain: {domain_dir.name}\n{'=' * 60}")
        report.merge(validate_domain(domain_dir))
    return report


def validate_single_path(path: Path) -> ValidationReport:
    report = ValidationReport()

    if path.is_dir():
        if same_path(path, doc_llm_root() / "Tasks"):
            return validate_tasks_root(path)
        if same_path(path, doc_llm_root() / "Domains"):
            return validate_domains_root(path)
        if same_path(path, doc_llm_root() / "00_Global"):
            emit_line(f"\n{'=' * 60}\nGlobal: {path.name}\n{'=' * 60}")
            return validate_global_root(path)
        if same_path(path, doc_llm_root() / "SOPs"):
            emit_line(f"\n{'=' * 60}\nSOPs: {path.name}\n{'=' * 60}")
            return validate_sops_root(path)
        if (path / "README.md").exists() and (path / "subtasks").exists():
            emit_line(f"\n{'=' * 60}\nEpic: {path.name}\n{'=' * 60}")
            return validate_epic_dir(path)
        if (path / "BRIEF.md").exists() and (path / "L2_Modules").exists():
            emit_line(f"\n{'=' * 60}\nDomain: {path.name}\n{'=' * 60}")
            return validate_domain(path)
        if path.parent == doc_llm_root() / "SOPs" and (path / "README.md").exists():
            emit_line(f"\n{'=' * 60}\nSOP: {path.name}\n{'=' * 60}")
            return validate_sop_readme(path / "README.md")
        report.error(display_path(path), "无法识别目录类型 (既不是 Epic, Domain, 也不是 SOP)")
        return report

    parent = path.parent
    if path.name == "README.md" and parent.name.startswith("E") and (parent / "subtasks").exists():
        return validate_epic_readme(path)
    if parent.name == "plan":
        return validate_execution_plan(path)
    if parent.name == "subtasks":
        report.merge(validate_subtask(path))
        report.merge(validate_subtask_dependency_graph([path]))
        return report
    if re.match(r"L0_.*\.md$", path.name):
        report.merge(validate_utf8_readability(path))
        report.merge(validate_l0_size(path))
        report.merge(validate_l0_purity(path))
        return report
    if re.match(r"L1_.*_router\.md$", path.name):
        topic_readmes = sorted(path.parent.glob("references/*/README.md"))
        report.merge(validate_utf8_readability(path))
        report.merge(validate_l1_router(path, bool(topic_readmes)))
        report.merge(validate_l1_purity(path))
        return report
    if path.name == "L1_target_architecture.md":
        report.merge(validate_utf8_readability(path))
        report.merge(validate_l1_purity(path))
        return report
    if path.parent.name == "L2_Modules":
        report.merge(validate_utf8_readability(path))
        report.merge(validate_l2_size(path))
        return report
    if path.name == "README.md" and parent.parent.name == "references":
        report.merge(validate_utf8_readability(path))
        report.merge(validate_reference_topic_readme(path))
        return report
    if path.name == "README.md" and parent.parent == doc_llm_root() / "SOPs":
        report.merge(validate_utf8_readability(path))
        report.merge(validate_sop_readme(path))
        return report

    report.error(display_path(path), "无法识别文件类型 (仅支持 Epic README / plan / subtask / L0 / L1 / L2 / reference topic README / SOP README)")
    return report


def validate_target_argument(arg: str) -> ValidationReport:
    root = doc_llm_root()
    tasks_root = root / "Tasks"
    domains_root = root / "Domains"
    global_root = root / "00_Global"
    sops_root = root / "SOPs"

    if arg == "--all":
        report = ValidationReport()
        report.merge(validate_global_root(global_root))
        report.merge(validate_tasks_root(tasks_root))
        report.merge(validate_domains_root(domains_root))
        if sops_root.exists():
            report.merge(validate_sops_root(sops_root))
        return report
    if arg == "--tasks":
        return validate_tasks_root(tasks_root)
    if arg == "--domains":
        return validate_domains_root(domains_root)
    if arg == "--sops":
        return validate_sops_root(sops_root)

    target = Path(arg)
    if not target.is_absolute():
        doc_llm_relative = manifest_path_to_doc_llm_relative(arg)
        target = resolve_manifest_path(arg) if doc_llm_relative is not None else PROJECT_ROOT / target
    target = resolve_path(target)
    if not is_path_within_root(target, root):
        report = ValidationReport()
        report.error("cli", f"Target must stay inside {active_doc_llm_dir()}: {target}")
        return report
    if not target.exists():
        report = ValidationReport()
        report.error("cli", f"Target not found: {target}")
        return report
    return validate_single_path(target)


def print_usage() -> None:
    entry = os.environ.get("DLM_SKILL_ENTRYPOINT") or Path(sys.argv[0]).as_posix()
    emit_line("Usage:")
    emit_line(f"  python {entry} --all")
    emit_line(f"  python {entry} --tasks")
    emit_line(f"  python {entry} --domains")
    emit_line(f"  python {entry} --sops")
    emit_line(f"  python {entry} --json --all")
    emit_line(f"  python {entry} --json <path>")
    emit_line(f"  python {entry} <path>")
    emit_line(f"  python {entry} --help")
    emit_line("")
    kb_dir = active_doc_llm_dir()
    emit_line("Examples:")
    emit_line(f"  python {entry} --all")
    emit_line(f"  python {entry} --json {kb_dir}/00_Global")
    emit_line(f"  python {entry} {kb_dir}/Tasks/E001_example_capability")
    emit_line(f"  python {entry} {kb_dir}/Domains/1_core_runtime")
    emit_line("默认检查任务当前正文的明确空缺及显式引用，不判断方案正确性、授权或产品验收。")


def main() -> int:
    global TEXT_OUTPUT

    original_text_output = TEXT_OUTPUT
    raw_args = sys.argv[1:]
    json_mode = "--json" in raw_args
    args = [arg for arg in raw_args if arg != "--json"]
    TEXT_OUTPUT = not json_mode

    try:
        if len(args) != 1:
            print_usage()
            return 2

        arg = args[0]
        if arg in {"-h", "--help"}:
            print_usage()
            return 0

        report = validate_target_argument(arg)
        if report.errors and any(item.scope == "cli" and item.message.startswith("Target not found:") for item in report.messages):
            if json_mode:
                print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            return 2

        if json_mode:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            emit_line(f"\n{'=' * 60}")
            emit_line("Summary:")
            emit_line(f"  [OK] {report.oks}")
            emit_line(f"  [WARN] {report.warnings}")
            emit_line(f"  [ERROR] {report.errors}")

        return 1 if report.errors else 0
    finally:
        TEXT_OUTPUT = original_text_output


PROJECT_ROOT = find_project_root(Path(__file__).resolve().parent)
DOC_LLM_DIR = resolve_knowledge_dir_name(PROJECT_ROOT)
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


if __name__ == "__main__":
    sys.exit(main())
