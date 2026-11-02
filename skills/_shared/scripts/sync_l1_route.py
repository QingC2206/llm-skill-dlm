#!/usr/bin/env python3
# -*- coding: utf-8-sig -*-
"""
sync_l1_route.py — shared implementation for L1 route table syncing

Entrypoint:
  python <skill>/scripts/dlm.py kb route <domain_dir> [--preview]

功能:
  1. 扫描指定 Domain 的 L2_Modules/ 目录
  2. 扫描指定 Domain 的 references/*/README.md topic 入口
  3. 解析各入口文件的 YAML front-matter
  4. 分别生成 L1 的 L2 路由表与参考资料路由
  5. 替换 L1 路由文件中对应段落的内容

输出格式:
  L2 路由表:
    | 当你需要了解... | 去读 | match_globs | 状态 |
  参考资料路由:
    | 当你需要查阅... | 去读 | 类型 | 权威性 | match_globs | 状态 |
"""

import os
import sys
import re
from pathlib import Path

L2_HEADING_PATTERN = r"^##[ \t]+(?:[一二三四五六七八九十]+、)?(?:L2[ \t]+)?路由表[ \t]*$"
REFERENCE_HEADING_PATTERN = r"^##[ \t]+(?:[一二三四五六七八九十]+、)?参考资料路由[ \t]*$"
L2_SECTION_PATTERN = rf"(?m)({L2_HEADING_PATTERN}(?:\n|\Z))(.*?)(?=^##[ \t]+|\Z)"
REFERENCE_SECTION_PATTERN = rf"(?m)({REFERENCE_HEADING_PATTERN}(?:\n|\Z))(.*?)(?=^##[ \t]+|\Z)"
CHINESE_SECTION_NUMBERS = [
    "一",
    "二",
    "三",
    "四",
    "五",
    "六",
    "七",
    "八",
    "九",
    "十",
]

# Windows 控制台 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


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
    sys.exit(1)


def ensure_path_within(path: Path, root: Path, label: str) -> Path:
    resolved = resolve_path(path)
    if not is_path_within_root(resolved, root):
        fail_path_validation(label, str(path))
    return resolved


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from dlm_config import DEFAULT_KNOWLEDGE_DIR, KNOWN_KNOWLEDGE_DIRS, resolve_knowledge_dir_name  # noqa: E402

ACTIVE_DOC_LLM_DIR = os.environ.get("DLM_DOC_LLM_DIR", DEFAULT_KNOWLEDGE_DIR).replace("\\", "/").strip("/") or DEFAULT_KNOWLEDGE_DIR


def _kb_dir_for(project_root: Path) -> str:
    return resolve_knowledge_dir_name(project_root)


def resolve_domain_dir(project_root: Path, raw_domain: str) -> Path:
    kb_dir = _kb_dir_for(project_root)
    domains_root = project_root / kb_dir / "Domains"
    candidate = Path(raw_domain)
    if candidate.is_absolute():
        return ensure_path_within(candidate, domains_root, "domain directory")

    normalized = raw_domain.replace("\\", "/").strip()
    for prefix_dir in tuple(dict.fromkeys([kb_dir, ACTIVE_DOC_LLM_DIR, *KNOWN_KNOWLEDGE_DIRS])):
        prefix = f"{prefix_dir}/Domains/"
        if normalized.startswith(prefix):
            candidate = project_root / kb_dir / normalized[len(f"{prefix_dir}/"):]
            return ensure_path_within(candidate, domains_root, "domain directory")
    if normalized.startswith("Domains/"):
        candidate = project_root / kb_dir / normalized
    elif normalized and "/" not in normalized:
        candidate = domains_root / normalized
    else:
        candidate = project_root / normalized
    return ensure_path_within(candidate, domains_root, "domain directory")


def validate_domain_dir(domain_dir: Path) -> Path:
    project_root = find_project_root(resolve_path(domain_dir))
    return ensure_path_within(domain_dir, project_root / _kb_dir_for(project_root) / "Domains", "domain directory")


def find_project_root(start: Path) -> Path:
    """从脚本位置向上查找项目根目录 (包含配置的知识库目录或默认知识库目录)"""
    env_root = os.environ.get("DLM_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    candidates = tuple(dict.fromkeys([ACTIVE_DOC_LLM_DIR, *KNOWN_KNOWLEDGE_DIRS]))
    current = start
    for _ in range(10):  # 最多向上 10 层
        if (current / "dlm.config.json").is_file() or any((current / d).is_dir() for d in candidates):
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    # 回退: 使用 cwd
    cwd = Path.cwd()
    for current in [cwd, *cwd.parents]:
        if (current / "dlm.config.json").is_file() or any((current / d).is_dir() for d in candidates):
            return current
    print(f"[ERROR] Cannot find project root (no {'/ or '.join(candidates)}/ directory found)")
    sys.exit(1)


def parse_yaml_frontmatter(filepath: Path) -> dict | None:
    """解析 Markdown 文件的 YAML front-matter (简单解析, 不依赖 pyyaml)"""
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            content = f.read()
    except Exception as e:
        print(f"  [WARN] Cannot read {filepath}: {e}")
        return None

    # 匹配 --- ... ---
    match = re.match(r"^---\s*\n(.*?\n)---\s*\n", content, re.DOTALL)
    if not match:
        print(f"  [SKIP] {filepath.name}: no YAML front-matter")
        return None

    yaml_block = match.group(1)
    result = {}

    for line in yaml_block.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # 解析列表项: - "value"
        if line.startswith("- "):
            val = line[2:].strip().strip('"').strip("'")
            if "match_globs" in result and isinstance(result["match_globs"], list):
                result["match_globs"].append(val)
            continue

        # 解析键值对: key: value 或 key: "value"
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")

            # 去除行尾注释
            if "#" in val:
                val = val[:val.index("#")].strip().strip('"').strip("'")

            if key == "match_globs":
                result[key] = []  # 下面的 - 项会追加
                if val:  # 单行格式: match_globs: ["a", "b"]
                    if val.startswith("["):
                        items = val.strip("[]").split(",")
                        result[key] = [i.strip().strip('"').strip("'") for i in items if i.strip()]
            else:
                result[key] = val

    return result


def extract_first_heading(filepath: Path) -> str:
    """提取 Markdown 文件的第一个 # 标题作为模块描述"""
    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line.startswith("# ") and not line.startswith("# {"):
                    return line[2:].strip()
    except Exception:
        pass
    return filepath.stem


def generate_l2_route_table(domain_dir: Path) -> str:
    """为指定 Domain 生成 L2 路由表 Markdown"""
    domain_dir = validate_domain_dir(domain_dir)
    l2_dir = domain_dir / "L2_Modules"
    if not l2_dir.exists():
        return f"> [WARN] `{l2_dir}` directory not found\n"

    l2_files = sorted(l2_dir.glob("*.md"))
    if not l2_files:
        return "> [WARN] No L2 files found\n"

    rows = []
    for f in l2_files:
        meta = parse_yaml_frontmatter(f)
        heading = extract_first_heading(f)

        if meta:
            globs = meta.get("match_globs", [])
            status = meta.get("status", "未标注")
            globs_str = ", ".join(f"`{g}`" for g in globs) if globs else "—"
        else:
            globs_str = "—"
            status = "未标注"

        rows.append(
            f"| {heading} | `L2_Modules/{f.name}` | {globs_str} | {status} |"
        )

    header = (
        "> 本表由 `dlm kb route` 从 L2 YAML front-matter 自动生成。\n"
        "> 手动修改将在下次运行脚本时被覆盖。\n\n"
        "| 当你需要了解... | 去读 | match_globs | 状态 |\n"
        "|---|---|---|:---:|\n"
    )

    return header + "\n".join(rows) + "\n"


def generate_reference_route_table(domain_dir: Path) -> str | None:
    """为指定 Domain 生成参考资料路由 Markdown"""
    domain_dir = validate_domain_dir(domain_dir)
    ref_dir = domain_dir / "references"
    if not ref_dir.exists():
        return None

    topic_readmes = sorted(ref_dir.glob("*/README.md"))
    if not topic_readmes:
        return None

    rows = []
    for readme in topic_readmes:
        meta = parse_yaml_frontmatter(readme)
        heading = extract_first_heading(readme)
        rel_path = readme.relative_to(domain_dir).as_posix()

        if meta:
            globs = meta.get("match_globs", [])
            reference_type = meta.get("reference_type", "未标注")
            authority = meta.get("authority", "未标注")
            status = meta.get("status", "未标注")
            globs_str = ", ".join(f"`{g}`" for g in globs) if globs else "—"
        else:
            globs_str = "—"
            reference_type = "未标注"
            authority = "未标注"
            status = "未标注"

        rows.append(
            f"| {heading} | `{rel_path}` | {reference_type} | {authority} | {globs_str} | {status} |"
        )

    header = (
        "> 本表由 `dlm kb route` 从 `references/*/README.md` 的 YAML front-matter 自动生成。\n"
        "> 只把 topic README 作为入口; topic 内原文在执行阶段按目录卡继续下钻。\n\n"
        "| 当你需要查阅... | 去读 | 类型 | 权威性 | match_globs | 状态 |\n"
        "|---|---|---|---|---|:---:|\n"
    )

    return header + "\n".join(rows) + "\n"


def replace_section(content: str, pattern: str, section_body: str) -> tuple[str, bool]:
    """替换 Markdown 二级标题段落内容"""
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return content, False

    normalized = section_body.rstrip("\r\n")
    next_section = content[match.end(0):].lstrip("\r\n")
    new_content = (
        content[:match.end(1)]
        + "\n"
        + normalized
        + "\n\n"
        + next_section
    )
    return new_content, True


def remove_section(content: str, pattern: str) -> tuple[str, bool]:
    """删除 Markdown 二级标题段落内容"""
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return content, False

    before = content[:match.start(0)].rstrip("\r\n")
    after = content[match.end(0):].lstrip("\r\n")
    if before and after:
        return before + "\n\n" + after, True
    return before + after, True


def format_chinese_section_number(number: int) -> str:
    """将 1-based 编号格式化为中文章节序号"""
    if 1 <= number <= len(CHINESE_SECTION_NUMBERS):
        return CHINESE_SECTION_NUMBERS[number - 1]
    raise ValueError(f"Unsupported section number: {number}")


def normalize_l1_numbering(content: str) -> str:
    """按实际保留的章节重排已有编号，不给无编号的标题添加编号。"""
    number = 0

    def renumber(match: re.Match) -> str:
        nonlocal number
        number += 1
        return f"## {format_chinese_section_number(number)}、{match.group(1)}"

    return re.sub(r"^##[ \t]+[一二三四五六七八九十]+、(.*)$", renumber, content, flags=re.MULTILINE)


def update_l1_file(domain_dir: Path, l2_table: str, reference_table: str | None) -> bool:
    """在 L1 路由文件中替换 L2 / 参考资料路由段落"""
    domain_dir = validate_domain_dir(domain_dir)
    # 查找 L1 文件
    l1_candidates = list(domain_dir.glob("L1_*_router.md"))
    if not l1_candidates:
        print(f"  [ERROR] No L1_*_router.md found in {domain_dir}")
        return False

    l1_path = l1_candidates[0]
    print(f"  L1: {l1_path.name}")

    try:
        with open(l1_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
    except Exception as e:
        print(f"  [ERROR] Cannot read {l1_path}: {e}")
        return False

    new_content, replaced_l2 = replace_section(content, L2_SECTION_PATTERN, l2_table)
    if not replaced_l2:
        print(f"  [WARN] No '## L2 路由表' section found, skipping")
        return False

    if reference_table:
        updated_content, replaced_ref = replace_section(
            new_content,
            REFERENCE_SECTION_PATTERN,
            reference_table,
        )
        if replaced_ref:
            new_content = updated_content
        else:
            l2_match = re.search(L2_SECTION_PATTERN, new_content, re.DOTALL)
            if not l2_match:
                print("  [WARN] L2 route section disappeared during update, skipping reference insertion")
                return False
            numbered = re.match(r"##[ \t]+[一二三四五六七八九十]+、", l2_match.group(1))
            ref_heading = "## 一、参考资料路由" if numbered else "## 参考资料路由"
            before = new_content[:l2_match.end(0)].rstrip("\r\n")
            after = new_content[l2_match.end(0):].lstrip("\r\n")
            insertion = ref_heading + "\n\n" + reference_table.rstrip("\r\n")
            new_content = before + "\n\n" + insertion + "\n\n" + after
    else:
        new_content, _ = remove_section(new_content, REFERENCE_SECTION_PATTERN)

    new_content = normalize_l1_numbering(new_content)

    try:
        # Preserve the file's existing BOM and newline representation.
        raw = l1_path.read_bytes()
        encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
        newline = "\r\n" if b"\r\n" in raw else "\n"
        with open(l1_path, "w", encoding=encoding, newline=newline) as f:
            f.write(new_content)
        print(f"  [OK] Route tables updated")
        return True
    except Exception as e:
        print(f"  [ERROR] Write failed: {e}")
        return False


def process_domain(domain_dir: Path, preview: bool = False) -> bool:
    """处理单个 Domain"""
    domain_dir = validate_domain_dir(domain_dir)
    print(f"\n{'='*60}")
    print(f"Domain: {domain_dir.name}")
    print(f"{'='*60}")

    l2_table = generate_l2_route_table(domain_dir)
    ref_table = generate_reference_route_table(domain_dir)

    print(f"\n[L2 路由表]\n{l2_table}")
    if ref_table:
        print(f"\n[参考资料路由]\n{ref_table}")
    else:
        print("\n[参考资料路由]\n> [INFO] No reference topics found\n")

    if preview:
        print("  [PREVIEW] Preview mode, no files written")
        return True

    return update_l1_file(domain_dir, l2_table, ref_table)


def print_usage() -> None:
    """Print usage with the actual invoked entrypoint path."""
    entry = os.environ.get("DLM_SKILL_ENTRYPOINT") or Path(sys.argv[0]).as_posix()
    print("Usage:")
    print(f"  python {entry} <domain_dir> [--preview]")
    print(f"  python {entry} --all [--preview]")
    print(f"  python {entry} --help")
    print("")
    print("Examples:")
    print(f"  python {entry} {_kb_dir_for(find_project_root())}/Domains/1_core_runtime")
    print(f"  python {entry} --all --preview")


def main():
    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    preview = "--preview" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--preview"]
    if any(arg in {"-h", "--help"} for arg in args):
        print_usage()
        sys.exit(0)
    if not args:
        print_usage()
        sys.exit(1)

    # 定位项目根目录 (向上查找知识库目录)
    script_dir = Path(__file__).resolve().parent
    project_root = find_project_root(script_dir)
    doc_llm_dir = project_root / _kb_dir_for(project_root)

    if args[0] == "--all":
        domains_dir = doc_llm_dir / "Domains"
        if not domains_dir.exists():
            print(f"[ERROR] {domains_dir} not found")
            sys.exit(1)

        domain_dirs = sorted([d for d in domains_dir.iterdir() if d.is_dir()])
        if not domain_dirs:
            print("[ERROR] No domain directories found")
            sys.exit(1)

        results = {}
        for d in domain_dirs:
            results[d.name] = process_domain(d, preview)

        print(f"\n{'='*60}")
        print("Summary:")
        for name, ok in results.items():
            print(f"  {'[OK]' if ok else '[FAIL]'} {name}")
    else:
        domain_path = resolve_domain_dir(project_root, args[0])

        if not domain_path.exists():
            print(f"[ERROR] {domain_path} not found")
            sys.exit(1)

        process_domain(domain_path, preview)


if __name__ == "__main__":
    main()
