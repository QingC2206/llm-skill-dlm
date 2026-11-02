#!/usr/bin/env python3
"""Install the complete DLM pack without overwriting host-owned changes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys

PACK_DIRS = ("_shared", "dlm-load", "dlm-task", "dlm-review", "dlm-knowledge", "dlm-multi-llm-lease")
PACK_ROOT = Path(__file__).resolve().parent / "skills"
SKILLS_DIR = Path(".agents/skills")
MANIFEST = Path(".agents/dlm-install.json")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pack_files() -> dict[str, bytes]:
    files = {}
    for name in PACK_DIRS:
        entry = PACK_ROOT / name / ("README.md" if name == "_shared" else "SKILL.md")
        if not entry.is_file():
            raise ValueError(f"Incomplete package: missing {entry}")
        for path in sorted((PACK_ROOT / name).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files[path.relative_to(PACK_ROOT).as_posix()] = path.read_bytes()
    return files


def safe_target(host: Path, relative: Path) -> Path:
    path = host / relative
    if not path.resolve().is_relative_to(host):
        raise ValueError(f"Destination escapes host: {path}")
    for part in (path, *path.parents):
        if part == host:
            break
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), "st_file_attributes", 0)
                                 & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise ValueError(f"Destination uses a link/junction; choose a real directory: {part}")
    return path


def read_object(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def previous_files(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = read_object(path)
    if data.get("format") != 1 or not isinstance(data.get("files"), dict):
        raise ValueError(f"Unsupported install manifest: {path}")
    for key, value in data["files"].items():
        parts = PurePosixPath(key).parts
        if (not parts or parts[0] not in PACK_DIRS or len(parts) < 2 or ".." in parts
                or "\\" in key or ":" in key or PurePosixPath(key).as_posix() != key
                or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)):
            raise ValueError(f"Invalid installed file record: {key}")
    return data["files"]


def install(host: Path, *, dry_run: bool = False, check: bool = False) -> int:
    host = host.resolve()
    if not host.is_dir():
        raise ValueError(f"Host directory does not exist: {host}")
    if host == PACK_ROOT.parent or host.is_relative_to(PACK_ROOT):
        raise ValueError("Choose a consuming repository, not this package's source directory")
    files = pack_files()
    hashes = {key: digest(value) for key, value in files.items()}
    manifest_path = safe_target(host, MANIFEST)
    old = previous_files(manifest_path)
    config_path = safe_target(host, Path("dlm.config.json"))
    config = None
    if config_path.exists():
        read_object(config_path)
    else:
        knowledge = next((name for name in ("docs-llm", "doc_llm", "docs_llm")
                          if (host / name).is_dir()), "docs-llm")
        config = {"knowledge_dir": knowledge, "locale": "zh-CN"}

    writes, removals, conflicts = [], [], []
    for key in sorted(files.keys() | old.keys()):
        target = safe_target(host, SKILLS_DIR / key)
        if target.exists() and not target.is_file():
            conflicts.append(f"not a file: {key}")
            continue
        # Catch a file used as an ancestor before writing any other files.
        if any(p.exists() and not p.is_dir() for p in target.parents if p != host):
            conflicts.append(f"parent is not a directory: {key}")
            continue
        current = digest(target.read_bytes()) if target.is_file() else None
        if key in files:
            if current == hashes[key]:
                continue
            if current is not None and current != old.get(key):
                conflicts.append(f"locally modified or unmanaged file: {key}")
            else:
                writes.append(key)
        elif current is not None:
            if current != old[key]:
                conflicts.append(f"locally modified removed file: {key}")
            else:
                removals.append(key)
    if conflicts:
        raise ValueError("No files written; resolve these conflicts first:\n  " + "\n  ".join(conflicts))

    payload_hash = digest(json.dumps(hashes, sort_keys=True).encode("utf-8"))
    manifest = {"format": 1, "package": "llm-skill-dlm", "payload_sha256": payload_hash, "files": hashes}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest_changed = not manifest_path.is_file() or manifest_path.read_bytes() != manifest_bytes
    if check:
        if writes or removals or config is not None or manifest_changed:
            print(f"[DIFF] write={len(writes)} remove={len(removals)} config={config is not None} "
                  f"manifest={manifest_changed}")
            return 1
        print(f"[OK] {len(files)} installed files match this package")
        return 0
    if dry_run:
        print(f"[DRY-RUN] host={host}; write={len(writes)} remove={len(removals)} "
              f"config={config is not None} manifest={manifest_changed}; no files written")
        return 0

    for key in writes:
        target = safe_target(host, SKILLS_DIR / key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(files[key])
    for key in removals:
        safe_target(host, SKILLS_DIR / key).unlink()
    if config is not None:
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if manifest_changed:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(manifest_bytes)
    print(f"[OK] {len(files)} DLM files installed in {host / SKILLS_DIR}; "
          f"write={len(writes)} remove={len(removals)}")
    print("From the host root: python -X utf8 .agents/skills/dlm-task/scripts/dlm.py --help")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, type=Path, help="existing consuming repository directory")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate and preview without writing")
    mode.add_argument("--check", action="store_true", help="verify installed content without writing")
    args = parser.parse_args()
    try:
        return install(args.host, dry_run=args.dry_run, check=args.check)
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
