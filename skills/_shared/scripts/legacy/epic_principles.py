"""Mechanical inheritance of the fixed Epic principles section."""

from __future__ import annotations

import re

from context_manifest_tool import markdown_heading_spans


PRINCIPLES_TITLE = "Epic 核心原则"
PRINCIPLES_HEADING = "## " + PRINCIPLES_TITLE
PRINCIPLES_PLACEHOLDER = "{Epic 核心原则正文}"
CONFIRMATION_PLACEHOLDER = "{原则确认依据}"


def has_nonplaceholder_text(value: str, placeholder: str) -> bool:
    text = value.replace(placeholder, "")
    text = re.sub(r"(?m)^[ \t]*(?:>[ \t]*)?(?:[-*+][ \t]+(?:\[[ xX]\][ \t]*)?|\d+[.)][ \t]+)", "", text)
    # Underscores are Markdown emphasis, not content left by a filled field.
    return re.search(r"[^\W_]", text) is not None


def require_confirmation(value: str) -> str:
    """Require a recorded human confirmation; authenticating the conversation is the caller's duty."""
    visible = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL).strip()
    if not has_nonplaceholder_text(visible, CONFIRMATION_PLACEHOLDER) or re.fullmatch(r"\{[^{}]+\}", visible):
        raise ValueError("创建 Epic 前必须取得用户对本 Epic 具体原则的明确确认，并用 --principles-confirmation 记录会话来源与确认原话；确认前仅可 --dry-run")
    return value.strip()


def principles_span(content: str, *, title: str = PRINCIPLES_TITLE) -> tuple[int, int, int] | None:
    """Return heading start, body start and section end; ignore fenced examples."""
    headings = markdown_heading_spans(content)
    matches = [(i, row) for i, row in enumerate(headings) if row[1:] == (2, title)]
    if len(matches) > 1:
        raise ValueError(f"重复固定字段 ## {title}")
    if not matches:
        return None
    index, (line, level, _title) = matches[0]
    lines = content.splitlines(keepends=True)
    end_line = next((n - 1 for n, depth, _ in headings[index + 1:] if depth <= level), len(lines))
    return sum(map(len, lines[:line - 1])), sum(map(len, lines[:line])), sum(map(len, lines[:end_line]))


def require_principles(content: str, *, title: str = PRINCIPLES_TITLE) -> str:
    span = principles_span(content, title=title)
    if span is None:
        raise ValueError(f"缺少固定字段 ## {title}；先填写已确认的原则")
    # Blank lines separating Markdown sections are not part of the field value.
    body = content[span[1]:span[2]].strip("\r\n")
    visible = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()
    if not has_nonplaceholder_text(visible, "{" + title + "正文}"):
        raise ValueError(f"固定字段 ## {title} 为空或仍为占位内容")
    return body


def put_principles(content: str, body: str, *, title: str = PRINCIPLES_TITLE) -> str:
    """Copy the body verbatim, replacing only this field or inserting after Meta."""
    body = "\n" + body.strip("\r\n") + "\n\n"
    span = principles_span(content, title=title)
    if span is not None:
        return content[:span[1]] + body + content[span[2]:]
    headings = markdown_heading_spans(content)
    lines = content.splitlines(keepends=True)
    meta = next((i for i, row in enumerate(headings) if row[1:] == (2, "Meta")), None)
    if meta is not None:
        insertion_line = next((n - 1 for n, level, _ in headings[meta + 1:] if level <= 2), len(lines))
    else:
        insertion_line = next((n - 1 for n, level, _ in headings if level == 2), len(lines))
    offset = sum(map(len, lines[:insertion_line]))
    prefix = content[:offset]
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + "## " + title + "\n" + body + content[offset:]
