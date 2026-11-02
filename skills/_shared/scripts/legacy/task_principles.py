"""Check recorded principle reviews; judgments and evidence remain the executor's responsibility."""

from __future__ import annotations

import re

from context_manifest_tool import markdown_heading_spans
from epic_principles import principles_span, require_principles

SUBTASK_PRINCIPLES = "Subtask 原则"
TURN_REVIEW = "本轮原则核对"
COMPLETION_REVIEW = "完成前原则复盘"
PRINCIPLE_REVIEW_MARKER = "<!-- dlm:subtask-principles-v1 -->"


def mark_principle_reviews(content: str) -> str:
    """Remember adoption without requiring user-maintained metadata."""
    if PRINCIPLE_REVIEW_MARKER in content:
        return content
    lines = content.splitlines(keepends=True)
    insertion = next((index + 1 for index, line in enumerate(lines) if line.startswith("# ")), 0)
    lines.insert(insertion, "\n" + PRINCIPLE_REVIEW_MARKER + "\n")
    return "".join(lines)


def uses_principle_reviews(content: str) -> bool:
    return PRINCIPLE_REVIEW_MARKER in content or any(
        principles_span(content, title=title) is not None
        for title in (SUBTASK_PRINCIPLES, TURN_REVIEW, COMPLETION_REVIEW)
    )


def is_verified_baseline(value: str) -> bool:
    """Markdown emphasis, code ticks and status icons do not alter the state."""
    value = re.sub(r"^[\W_]+", "", value.strip())
    return re.match(r"Verified\b", value, re.IGNORECASE) is not None


def section(content: str, title: str, level: int = 2) -> str:
    headings = markdown_heading_spans(content)
    matches = [(i, row) for i, row in enumerate(headings) if row[1:] == (level, title)]
    if len(matches) != 1:
        raise ValueError(f"必须有且只有一个 {'#' * level} {title}")
    index, (start, _level, _title) = matches[0]
    lines = content.splitlines(keepends=True)
    end = next((n - 1 for n, depth, _ in headings[index + 1:] if depth <= level), len(lines))
    return "".join(lines[start:end])


def visible_prose(content: str) -> str:
    """Ignore commented guidance and fenced examples when checking review fields."""
    lines = []
    fence = ""
    hide = lambda text: re.sub(r"[^\r\n]", " ", text)
    content = re.sub(r"<!--.*?-->", lambda match: hide(match[0]), content, flags=re.DOTALL)
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ""
            lines.append(hide(line))
        elif marker:
            fence = marker[1]
            lines.append(hide(line))
        else:
            lines.append(line)
    return "".join(lines)


def prose(content: str) -> str:
    return visible_prose(content).strip()


def require_text(content: str, label: str) -> str:
    value = prose(content)
    if not re.search(r"\w", value) or re.fullmatch(r"\{[^{}]+\}", value):
        raise ValueError(f"原则复盘缺少有效内容：{label}")
    return value


def field(content: str, label: str) -> str:
    # Metadata belongs before nested review sections, never inside an example.
    headings = markdown_heading_spans(content)
    if headings:
        content = "\n".join(content.splitlines()[:headings[0][0] - 1])
    values = re.findall(rf"^-[ \t]*{re.escape(label)}[：:][ \t]*(.*)$", prose(content), re.MULTILINE)
    if len(values) != 1:
        raise ValueError(f"原则复盘必须声明唯一的 {label}")
    return values[0].strip()


def check_reviews(content: str, epic_content: str, *, phase: str) -> str:
    # Historical Epics keep their existing goal/contract until explicitly migrated.
    # Once the parent adopts the fixed field, exact inheritance remains mandatory.
    if principles_span(epic_content) is not None:
        if require_principles(content) != require_principles(epic_content):
            raise ValueError("Epic 核心原则副本已过期；先同步，再根据当前原则重新核对")
    elif principles_span(content) is not None:
        raise ValueError("子任务包含 Epic 核心原则，但父 Epic 缺少源字段")
    require_principles(content, title=SUBTASK_PRINCIPLES)
    turn = section(content, TURN_REVIEW)
    verdict = field(turn, "结论")
    if verdict not in {"一致", "偏离", "待验证"}:
        raise ValueError("本轮原则核对结论必须是 一致 / 偏离 / 待验证")
    for title in ("逐条核对", "纠偏与下一步"):
        require_text(section(turn, title, 3), title)
    if phase == "turn":
        return verdict
    if phase != "completion":
        raise ValueError(f"未知原则核对阶段：{phase}")
    if verdict != "一致":
        raise ValueError("本轮核对尚有偏离或待验证事项，不能标记完成")
    review = section(content, COMPLETION_REVIEW)
    if field(review, "结论") != "通过" or field(review, "未闭合项") != "无":
        raise ValueError("完成前原则复盘必须通过且未闭合项为无")
    for title in ("Epic 原则逐条复盘", "Subtask 原则逐条复盘", "验收与证据"):
        require_text(section(review, title, 3), title)
    return "通过"


def invalidate_reviews(content: str) -> str:
    """Principle changes invalidate recorded verdicts, retaining the evidence and task status."""
    for title in (TURN_REVIEW, COMPLETION_REVIEW):
        span = principles_span(content, title=title)
        if span is None:
            continue
        body = content[span[1]:span[2]]
        match = re.search(r"(?m)^-[ \t]*结论[：:][ \t]*(.*)$", visible_prose(body))
        if match:
            body = body[:match.start(1)] + "待验证" + body[match.end(1):]
        content = content[:span[1]] + body + content[span[2]:]
    return content
