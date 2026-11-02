#!/usr/bin/env python3
"""Path normalisation and write-boundary semantics (most specific rule wins)."""

from __future__ import annotations

import re
from functools import lru_cache

BOUNDARY_KINDS = ("write", "generated", "read_only", "forbidden")
# On equal specificity the more restrictive kind wins.
_RESTRICTIVENESS = {"forbidden": 3, "read_only": 2, "generated": 1, "write": 0}
_WILDCARDS = re.compile(r"[*?\[]")


def norm(path: str) -> str:
    value = path.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    value = re.sub(r"/+", "/", value)
    return value.rstrip("/") if value not in {"", "/"} else value


def validate_pattern(pattern: str) -> str:
    value = norm(pattern)
    if (not value or value in {".", ".."} or value.startswith("/") or value.startswith("../")
            or "/../" in value or value.endswith("/..") or re.match(r"^[A-Za-z]:", value)):
        raise ValueError(f"路径必须是宿主根下的相对路径或 glob：{pattern}")
    return value


def literal_prefix(pattern: str) -> str:
    match = _WILDCARDS.search(pattern)
    return pattern if match is None else pattern[: match.start()]


def specificity(pattern: str) -> int:
    if not _WILDCARDS.search(pattern):
        return 100_000 + len(pattern)
    return len(literal_prefix(pattern))


@lru_cache(maxsize=4096)
def _regex(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def matches(path: str, pattern: str) -> bool:
    path = norm(path)
    pattern = norm(pattern)
    if pattern.endswith("/**"):
        base = pattern[:-3]
        if not _WILDCARDS.search(base):
            return path == base or path.startswith(base + "/")
    return bool(_regex(pattern).match(path))


def classify(path: str, boundary: dict[str, list[str]]) -> tuple[str | None, str | None]:
    best: tuple[int, int, str, str] | None = None
    for kind in BOUNDARY_KINDS:
        for pattern in boundary.get(kind, []) or []:
            if matches(path, pattern):
                key = (specificity(pattern), _RESTRICTIVENESS[kind], kind, pattern)
                if best is None or key[:2] > best[:2]:
                    best = key
    return (best[2], best[3]) if best else (None, None)


def sample_path(pattern: str) -> str:
    """A concrete path representative of the pattern, used for containment checks."""
    if not _WILDCARDS.search(pattern):
        return pattern
    prefix = literal_prefix(pattern)
    return (prefix.rstrip("/") + "/__dlm_sample__") if prefix else "__dlm_sample__"


def within(child: str, parent: str) -> bool:
    child, parent = norm(child), norm(parent)
    if child == parent:
        return True
    if parent.endswith("/**") and not _WILDCARDS.search(parent[:-3]):
        base = parent[:-3]
        prefix = literal_prefix(child)
        return prefix == base or prefix.startswith(base + "/")
    if not _WILDCARDS.search(child):
        return matches(child, parent)
    return False


def top_roots(patterns: list[str], depth: int = 2) -> set[str]:
    roots = set()
    for pattern in patterns:
        parts = [p for p in literal_prefix(norm(pattern)).split("/") if p]
        roots.add("/".join(parts[:depth]) if parts else "<root>")
    return roots
