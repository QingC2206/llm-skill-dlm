#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dlm_config import DEFAULT_KNOWLEDGE_DIR, KNOWN_KNOWLEDGE_DIRS, resolve_knowledge_dir_name  # noqa: E402

PROJECT_ROOT = Path.cwd()
ACTIVE_DOC_LLM_DIR = os.environ.get("DLM_DOC_LLM_DIR", DEFAULT_KNOWLEDGE_DIR).replace("\\", "/").strip("/") or DEFAULT_KNOWLEDGE_DIR

REPO_MARKERS = (ACTIVE_DOC_LLM_DIR, *KNOWN_KNOWLEDGE_DIRS, "dlm.config.json", ".git")
FRONTMATTER_RE = re.compile(r"\A---\s*\n([\s\S]*?)\n---\s*\n?", re.MULTILINE)
FENCED_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
PLACEHOLDER_RE = re.compile(r"(\{[^{}\n]{1,80}\}|TODO|TBD|待补充|待确认|占位)", re.IGNORECASE)
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
SIMPLE_YAML_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")

LOG_MARKERS = [
    "Session",
    "执行日志",
    "完成",
    "发现",
    "教训",
    "沉淀",
    "下次",
    "本轮",
    "本次",
    "这次",
    "已完成",
    "仍待",
]

STABLE_KNOWLEDGE_MARKERS = [
    "约束",
    "不变量",
    "契约",
    "边界",
    "所有权",
    "生命周期",
    "状态机",
    "状态",
    "数据流",
    "流程",
    "权衡",
    "陷阱",
    "根因",
    "影响",
    "原因",
    "接口",
]

REQUIRED_FRONTMATTER_KEYS = ("domain", "module", "match_globs", "status", "last_updated")


@dataclass(frozen=True)
class L2Doc:
    path: Path
    rel_path: str
    raw: str
    body: str
    frontmatter: dict[str, object]
    fingerprint: set[str]
    metrics: dict[str, object]
    signals: list[dict[str, object]]
    attention_score: int


def find_project_root(start: Path) -> Path:
    env_root = os.environ.get("DLM_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if any((candidate / marker).exists() for marker in REPO_MARKERS):
            return candidate
    return current


def repo_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    match = FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw
    return parse_frontmatter(match.group(1)), raw[match.end() :]


def parse_frontmatter(text: str) -> dict[str, object]:
    result: dict[str, object] = {}
    current_list_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if current_list_key and line.strip().startswith("- "):
            value = line.strip()[2:].strip().strip('"').strip("'")
            current_value = result.setdefault(current_list_key, [])
            if isinstance(current_value, list):
                current_value.append(value)
            continue
        match = SIMPLE_YAML_KEY_RE.match(line)
        if not match:
            current_list_key = None
            continue
        key, value = match.groups()
        value = value.strip()
        if value == "":
            result[key] = []
            current_list_key = key
            continue
        current_list_key = None
        if value.startswith("[") and value.endswith("]"):
            items = [
                item.strip().strip('"').strip("'")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
            result[key] = items
        else:
            result[key] = value.strip('"').strip("'")
    return result


def normalize_text_for_tokens(text: str) -> str:
    text = FENCED_BLOCK_RE.sub(" ", text)
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    return text


def tokenize(text: str) -> set[str]:
    normalized = normalize_text_for_tokens(text)
    tokens = {match.group(0).lower() for match in WORD_RE.finditer(normalized)}
    for match in CJK_RE.finditer(normalized):
        value = match.group(0)
        tokens.update(value[index : index + 2] for index in range(0, len(value) - 1))
    return {token for token in tokens if len(token) >= 2}


def count_hits(text: str, markers: Iterable[str]) -> int:
    return sum(text.count(marker) for marker in markers)


def extract_section_share(body: str, heading_text: str) -> float:
    headings = list(HEADING_RE.finditer(body))
    if not headings:
        return 0.0
    for index, match in enumerate(headings):
        if heading_text not in match.group(2):
            continue
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        return max(0.0, (end - start) / max(1, len(body)))
    return 0.0


def add_signal(
    signals: list[dict[str, object]],
    code: str,
    severity: str,
    reason: str,
    evidence: object,
) -> None:
    signals.append(
        {
            "code": code,
            "severity": severity,
            "reason": reason,
            "evidence": evidence,
        }
    )


def analyze_doc(path: Path) -> L2Doc:
    raw = read_text(path)
    frontmatter, body = strip_frontmatter(raw)
    body_without_code = FENCED_BLOCK_RE.sub("", body)
    headings = [match.group(2).strip() for match in HEADING_RE.finditer(body)]
    line_count = len(raw.splitlines())
    char_count = len(raw)
    body_chars = len(body_without_code.strip())
    code_block_count = len(FENCED_BLOCK_RE.findall(body))
    code_chars = sum(len(match.group(0)) for match in FENCED_BLOCK_RE.finditer(body))
    table_line_count = len(TABLE_LINE_RE.findall(body))
    date_count = len(DATE_RE.findall(body))
    placeholder_count = len(PLACEHOLDER_RE.findall(body_without_code))
    log_hits = count_hits(body, LOG_MARKERS)
    stable_hits = count_hits(body, STABLE_KNOWLEDGE_MARKERS)
    fingerprint = tokenize(body)
    missing_frontmatter = [
        key
        for key in REQUIRED_FRONTMATTER_KEYS
        if key not in frontmatter or frontmatter.get(key) in ("", [], None)
    ]

    signals: list[dict[str, object]] = []
    if missing_frontmatter:
        add_signal(
            signals,
            "frontmatter_gap",
            "medium",
            "L2 front-matter 缺少路由或维护字段",
            {"missing": missing_frontmatter},
        )
    if placeholder_count:
        add_signal(
            signals,
            "placeholder_content",
            "high",
            "正文仍含模板占位或待确认标记",
            {"count": placeholder_count},
        )
    if 0 < body_chars < 1200:
        add_signal(
            signals,
            "thin_l2",
            "medium",
            "正文很短, 可能缺少可复用隐性知识",
            {"body_chars": body_chars},
        )
    if char_count > 9000:
        add_signal(
            signals,
            "oversized_l2",
            "medium",
            "L2 超过建议尺寸, 可能需要拆分或压缩",
            {"chars": char_count},
        )
    if line_count and (log_hits / max(1, line_count) > 0.18 or date_count >= 5):
        add_signal(
            signals,
            "journal_like",
            "high",
            "流水账/执行日志标记占比偏高",
            {"log_hits": log_hits, "date_count": date_count, "lines": line_count},
        )
    evolution_share = extract_section_share(body, "演进记录")
    if evolution_share > 0.45:
        add_signal(
            signals,
            "evolution_record_dominates",
            "high",
            "演进记录占正文比例过高, L2 可能退化为 Git log 复述",
            {"share": round(evolution_share, 3)},
        )
    if body_chars >= 1600 and stable_hits < 5:
        add_signal(
            signals,
            "low_stable_knowledge_markers",
            "medium",
            "稳定契约、边界、陷阱、权衡等知识标记偏少",
            {"stable_marker_hits": stable_hits},
        )
    if code_chars / max(1, len(body)) > 0.5 and stable_hits < 5:
        add_signal(
            signals,
            "raw_artifact_dominates",
            "medium",
            "代码块或原始材料占比偏高, 需要确认是否应迁到 Reference",
            {"code_block_chars": code_chars, "body_chars": len(body)},
        )
    if len(headings) <= 2 and body_chars >= 1800:
        add_signal(
            signals,
            "flat_structure",
            "low",
            "正文较长但结构层级很少, 扫读和路由价值可能不足",
            {"heading_count": len(headings)},
        )

    severity_weight = {"high": 3, "medium": 2, "low": 1}
    attention_score = sum(severity_weight.get(str(signal["severity"]), 1) for signal in signals)
    metrics = {
        "chars": char_count,
        "body_chars": body_chars,
        "lines": line_count,
        "headings": len(headings),
        "tables": table_line_count,
        "code_blocks": code_block_count,
        "dates": date_count,
        "log_marker_hits": log_hits,
        "stable_marker_hits": stable_hits,
        "fingerprint_terms": len(fingerprint),
        "evolution_record_share": round(evolution_share, 3),
    }
    return L2Doc(
        path=path,
        rel_path=repo_path(path),
        raw=raw,
        body=body,
        frontmatter=frontmatter,
        fingerprint=fingerprint,
        metrics=metrics,
        signals=signals,
        attention_score=attention_score,
    )


def resolve_target(raw_target: str | None) -> Path:
    effective = raw_target or resolve_knowledge_dir_name(PROJECT_ROOT)
    target = Path(effective)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    target = target.resolve()
    if not target.exists():
        print(f"[ERROR] target does not exist: {effective}", file=sys.stderr)
        raise SystemExit(2)
    if not is_within(target, PROJECT_ROOT):
        print(f"[ERROR] target is outside project root: {effective}", file=sys.stderr)
        raise SystemExit(2)
    return target


def iter_l2_files(target: Path) -> list[Path]:
    if target.is_file():
        if target.suffix.lower() != ".md":
            print(f"[ERROR] target file is not markdown: {repo_path(target)}", file=sys.stderr)
            raise SystemExit(2)
        return [target]
    files: list[Path] = []
    for path in target.rglob("*.md"):
        parts = set(path.parts)
        if "L2_Modules" in parts:
            files.append(path)
    return sorted(files, key=lambda item: repo_path(item))


def similarity_pairs(
    docs: list[L2Doc],
    min_similarity: float,
    max_pairs: int,
) -> list[dict[str, object]]:
    pairs: list[dict[str, object]] = []
    for left_index, left in enumerate(docs):
        for right in docs[left_index + 1 :]:
            if len(left.fingerprint) < 20 or len(right.fingerprint) < 20:
                continue
            intersection = left.fingerprint & right.fingerprint
            union = left.fingerprint | right.fingerprint
            jaccard = len(intersection) / max(1, len(union))
            overlap = len(intersection) / max(1, min(len(left.fingerprint), len(right.fingerprint)))
            score = max(jaccard, overlap * 0.72)
            if score < min_similarity:
                continue
            pairs.append(
                {
                    "left": left.rel_path,
                    "right": right.rel_path,
                    "similarity": round(score, 3),
                    "jaccard": round(jaccard, 3),
                    "overlap": round(overlap, 3),
                    "shared_terms": len(intersection),
                    "review_hint": "same-topic merge/split candidate" if score >= 0.65 else "overlap needs LLM review",
                }
            )
    pairs.sort(key=lambda item: (-float(item["similarity"]), item["left"], item["right"]))
    return pairs[:max_pairs]


def doc_to_payload(doc: L2Doc, overlaps: list[dict[str, object]]) -> dict[str, object]:
    related_overlaps = [
        item
        for item in overlaps
        if item["left"] == doc.rel_path or item["right"] == doc.rel_path
    ]
    return {
        "path": doc.rel_path,
        "frontmatter": doc.frontmatter,
        "metrics": doc.metrics,
        "signals": doc.signals,
        "attention_score": doc.attention_score,
        "overlap_count": len(related_overlaps),
        "top_overlap": related_overlaps[0] if related_overlaps else None,
    }


def build_payload(args: argparse.Namespace) -> dict[str, object]:
    target = resolve_target(args.target)
    docs = [analyze_doc(path) for path in iter_l2_files(target)]
    overlaps = [] if args.no_overlap else similarity_pairs(docs, args.min_similarity, args.max_pairs)
    candidates = sorted(
        [doc_to_payload(doc, overlaps) for doc in docs if doc.attention_score > 0 or any(
            item["left"] == doc.rel_path or item["right"] == doc.rel_path for item in overlaps
        )],
        key=lambda item: (-int(item["attention_score"]), -int(item["overlap_count"]), str(item["path"])),
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "target": repo_path(target),
        "summary": {
            "l2_files": len(docs),
            "candidates": len(candidates),
            "overlap_pairs": len(overlaps),
            "high_signal_files": sum(
                1
                for doc in docs
                if any(signal["severity"] == "high" for signal in doc.signals)
            ),
        },
        "candidates": candidates[: args.max_candidates],
        "overlap_pairs": overlaps,
        "all_files": [doc_to_payload(doc, overlaps) for doc in docs] if args.include_all else [],
        "guardrails": [
            "This script emits facts and candidates only; LLM must decide whether optimization is worthwhile.",
            "High lexical overlap is not automatically duplication; shared contracts or common terminology may be valid.",
            "Low-density signals must be checked against the L2 purpose and current L1 route before rewriting.",
        ],
    }


def signal_codes(signals: list[dict[str, object]]) -> str:
    if not signals:
        return "-"
    return ", ".join(str(signal["code"]) for signal in signals)


def print_markdown(payload: dict[str, object]) -> None:
    summary = payload["summary"]
    print("# L2 Audit Facts")
    print()
    print(f"- **Target**: `{payload['target']}`")
    print(f"- **L2 files scanned**: {summary['l2_files']}")
    print(f"- **Candidate files**: {summary['candidates']}")
    print(f"- **Overlap pairs**: {summary['overlap_pairs']}")
    print(f"- **High-signal files**: {summary['high_signal_files']}")
    print()
    print("> Script output is evidence, not a verdict. Use the L2 audit rubric before rewriting or merging docs.")
    print()
    print("## Candidate Files")
    print()
    candidates = payload["candidates"]
    if not candidates:
        print("- No candidate files crossed the current deterministic thresholds.")
    else:
        print("| Score | File | Signals | Chars | Overlaps |")
        print("|---:|---|---|---:|---:|")
        for item in candidates:
            print(
                f"| {item['attention_score']} | `{item['path']}` | {signal_codes(item['signals'])} | "
                f"{item['metrics']['chars']} | {item['overlap_count']} |"
            )
    print()
    print("## Overlap Pairs")
    print()
    overlaps = payload["overlap_pairs"]
    if not overlaps:
        print("- No overlap pairs crossed the current similarity threshold.")
    else:
        print("| Similarity | Left | Right | Hint |")
        print("|---:|---|---|---|")
        for item in overlaps:
            print(
                f"| {item['similarity']:.3f} | `{item['left']}` | `{item['right']}` | {item['review_hint']} |"
            )
    print()
    print("## Guardrails")
    print()
    for guardrail in payload["guardrails"]:
        print(f"- {guardrail}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect deterministic facts for auditing doc_llm L2 density and overlap."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Knowledge root (defaults to active knowledge_dir, e.g. docs-llm or doc_llm), Domains directory, one Domain, L2_Modules directory, or a markdown file.",
    )
    parser.add_argument(
        "--min-similarity",
        type=float,
        default=0.42,
        help="Minimum lexical similarity for overlap candidates.",
    )
    parser.add_argument("--max-pairs", type=int, default=30, help="Maximum overlap pairs to report.")
    parser.add_argument("--max-candidates", type=int, default=40, help="Maximum candidate files to report.")
    parser.add_argument("--no-overlap", action="store_true", help="Skip pairwise overlap analysis.")
    parser.add_argument("--include-all", action="store_true", help="Include all file metrics in JSON output.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of markdown.")
    return parser


def main() -> int:
    global PROJECT_ROOT
    PROJECT_ROOT = find_project_root(Path.cwd())
    parser = build_parser()
    args = parser.parse_args()
    if args.min_similarity <= 0 or args.min_similarity > 1:
        print("[ERROR] --min-similarity must be in (0, 1]", file=sys.stderr)
        return 2
    payload = build_payload(args)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_markdown(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
