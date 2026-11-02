"""Parse task references without discarding their Epic identity.

This is a reference parser, not a scheduler: prose and artifact readiness still
need the executor's judgment.
"""

from __future__ import annotations

import re


_DASHES = "-‐‑‒–—−~～至到"
_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"E\d+(?:(?:\s*[/／]\s*|\s+)S\d+[a-z]?|[-‐‑‒–—−]S?\d+[a-z]?)?"
    r"|S\d+[a-z]?)(?![A-Za-z0-9_])"
)


def _canonical(value: str) -> str:
    if value.startswith("S"):
        return value
    match = re.fullmatch(r"(E\d+)(?:[\s/／‐‑‒–—−-]+S?(\d+[a-z]?))?", value)
    if match is None:
        raise ValueError(f"无法解析任务依赖：{value}")
    return match[1] + ("/S" + match[2] if match[2] else "")


def _expand_range(first: str, last: str) -> list[str]:
    start_parts, end_parts = first.split("/"), last.split("/")
    epic = start_parts[0] if len(start_parts) == 2 else ""
    end_epic = end_parts[0] if len(end_parts) == 2 else epic
    if epic != end_epic:
        raise ValueError(f"依赖区间不能跨 Epic：{first} → {last}")
    start = re.fullmatch(r"S(\d+)([a-z]?)", start_parts[-1])
    end = re.fullmatch(r"S(\d+)([a-z]?)", end_parts[-1])
    if start is None or end is None:
        raise ValueError(f"依赖区间必须使用 Subtask ID：{first} → {last}")
    prefix = epic + "/" if epic else ""
    if not start[2] and not end[2] and int(start[1]) <= int(end[1]):
        width = max(len(start[1]), len(end[1]))
        return [f"{prefix}S{number:0{width}d}" for number in range(int(start[1]), int(end[1]) + 1)]
    if start[1] == end[1] and start[2] and end[2] and start[2] <= end[2]:
        return [f"{prefix}S{start[1]}{chr(letter)}" for letter in range(ord(start[2]), ord(end[2]) + 1)]
    raise ValueError(f"依赖区间倒置或端点不兼容：{first} → {last}")


def parse_dependency_references(raw_value: str | None) -> tuple[list[str], str]:
    """Return canonical IDs and prose not mechanically interpreted.

    Explicit malformed task expressions fail. Natural-language conditions are
    retained for a visible diagnostic, never interpreted as satisfied.
    """
    text = (raw_value or "").strip()
    if text in {"", "无", "—", "-", "无依赖"}:
        return [], ""
    # Labels carry task identity; destinations are checked by manifest tooling.
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    # --dep-task has always accepted concrete task paths as well as IDs. Keep
    # their Epic namespace when the scaffold's metadata reaches validation.
    text = re.sub(
        r"(?:(?:docs-llm|doc_llm|docs_llm)/)?(?:Tasks/(?:done/)?)?(E\d+)_[^/\r\n,，;；]+/"
        r"(?:subtasks/(S\d+[a-z]?)(?:_[^/\r\n,，;；]+)?\.md|README\.md)",
        lambda match: match[1] + ("/" + match[2] if match[2] else ""),
        text.replace("\\", "/"),
    )
    matches = list(_REFERENCE.finditer(text))
    ids: list[str] = []
    consumed = list(text)
    index = 0
    while index < len(matches):
        current = matches[index]
        first = _canonical(current[0])
        end = current.end()
        values = [first]
        if index + 1 < len(matches):
            following = matches[index + 1]
            connector = text[end:following.start()].strip()
            if connector and connector in _DASHES:
                values = _expand_range(first, _canonical(following[0]))
                end = following.end()
                index += 1
            elif connector in {"/", "／"} and ("/" in first or "/" in _canonical(following[0])):
                raise ValueError(f"依赖命名空间有歧义：{text[current.start():following.end()]}；逐项写明 Epic/Subtask")
        suffix = text[end:].lstrip()
        if suffix and suffix[0] in _DASHES and not suffix.startswith(("至今", "到此")):
            raise ValueError(f"依赖区间不完整：{text[current.start():]}")
        ids.extend(values)
        consumed[current.start():end] = " " * (end - current.start())
        index += 1
    residue = "".join(consumed)
    if re.search(r"(?<![A-Za-z0-9_])[ES](?:\d|[?{])", residue):
        raise ValueError(f"无法可靠解析任务依赖：{residue.strip()}")
    prose = residue.strip(" \t\r\n,，;；、/／()（）[]【】:：")
    return list(dict.fromkeys(ids)), prose
