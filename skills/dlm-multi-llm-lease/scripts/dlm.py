#!/usr/bin/env python3
"""Skill entrypoint: delegates to the shared DLM CLI (``_shared/scripts/dlm.py``)."""

from pathlib import Path
import runpy
import sys


def main() -> None:
    shared = Path(__file__).resolve().parents[2] / "_shared" / "scripts" / "dlm.py"
    if not shared.exists():
        print(f"[ERROR] Shared DLM CLI not found: {shared}", file=sys.stderr)
        sys.exit(2)
    sys.argv[0] = str(shared)
    runpy.run_path(str(shared), run_name="__main__")


if __name__ == "__main__":
    main()
