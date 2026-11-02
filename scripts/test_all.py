#!/usr/bin/env python3
"""Run every bundled suite with an isolated host, including installation E2E."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUITES = ("skills/_shared/scripts", "skills/dlm-multi-llm-lease/scripts", "tests")


def main() -> int:
    cache = ROOT / "out" / "tests"
    cache.mkdir(parents=True, exist_ok=True)
    failed = []
    with tempfile.TemporaryDirectory(prefix="bootstrap-", dir=cache) as temporary:
        host = Path(temporary)
        (host / "docs-llm").mkdir()
        env = dict(os.environ)
        for name in ("DLM_PROJECT_ROOT", "DLM_DOC_LLM_DIR", "DLM_SKILL_ENTRYPOINT", "PYTHONPATH"):
            env.pop(name, None)
        # A global DLM_PROJECT_ROOT would override the individual hosts created by
        # the original tests. Use cwd only for their import-time host discovery.
        # Keep tempfile's OS default: some tests deliberately exercise a directory
        # with no Git ancestor, which cannot be modeled below this checkout.
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        for suite in SUITES:
            print(f"\n=== {suite} ===", flush=True)
            result = subprocess.run([sys.executable, "-X", "utf8", "-m", "unittest", "discover",
                                     "-s", str(ROOT / suite), "-p", "test_*.py"], cwd=host, env=env)
            if result.returncode:
                failed.append(suite)
    print("\n[FAIL] " + ", ".join(failed) if failed else "\n[OK] All suites passed")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
