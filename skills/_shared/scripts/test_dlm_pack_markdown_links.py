#!/usr/bin/env python3
from __future__ import annotations

import re
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath


SKILLS_ROOT = Path(__file__).resolve().parents[2]
PACK_DIRS = [
    SKILLS_ROOT / "dlm-task",
    SKILLS_ROOT / "dlm-review",
    SKILLS_ROOT / "dlm-load",
    SKILLS_ROOT / "dlm-knowledge",
    SKILLS_ROOT / "dlm-multi-llm-lease",
    SKILLS_ROOT / "_shared",
]
EXCLUDED_DIR_NAMES = {"archive", "__pycache__"}
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCED_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
IGNORED_TARGET_PREFIXES = ("http://", "https://", "mailto:", "app://", "plugin://")


def iter_markdown_files() -> list[Path]:
    files: list[Path] = []
    for pack_dir in PACK_DIRS:
        for path in pack_dir.rglob("*.md"):
            if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
                continue
            if "templates" in path.parts:
                continue
            files.append(path)
    return files


def strip_code_fences(content: str) -> str:
    return FENCED_BLOCK_RE.sub("", content)


def should_skip_target(target: str) -> bool:
    normalized = target.strip()
    if not normalized or normalized.startswith("#"):
        return True
    if normalized.startswith(IGNORED_TARGET_PREFIXES):
        return True
    if "{" in normalized or "}" in normalized:
        return True
    return False


def normalize_target(target: str) -> str:
    normalized = target.strip()
    if normalized.startswith("<") and normalized.endswith(">"):
        normalized = normalized[1:-1]
    normalized = normalized.split("#", 1)[0].strip()
    return normalized


class DlmPackMarkdownLinkTests(unittest.TestCase):
    def test_local_markdown_links_resolve(self) -> None:
        violations: list[str] = []

        for path in iter_markdown_files():
            content = strip_code_fences(path.read_text(encoding="utf-8"))
            for match in LINK_RE.finditer(content):
                raw_target = match.group(1)
                if should_skip_target(raw_target):
                    continue

                target = normalize_target(raw_target)
                if not target:
                    continue

                relative = path.relative_to(SKILLS_ROOT)
                if PurePosixPath(target).is_absolute() or PureWindowsPath(target).is_absolute():
                    violations.append(f"{relative}: absolute target {target}")
                    continue

                resolved = (path.parent / target).resolve()
                if not resolved.exists():
                    violations.append(f"{relative}: {target}")
                elif not any(root == resolved or root in resolved.parents for root in PACK_DIRS):
                    violations.append(f"{relative}: target outside DLM pack {target}")

        self.assertEqual([], violations, "Non-portable local markdown links:\n" + "\n".join(violations))
