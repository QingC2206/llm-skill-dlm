#!/usr/bin/env python3
"""Run the bundled DLM CLI against the current or explicitly selected host."""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--root":
        if len(args) < 2 or not Path(args[1]).is_dir():
            print("[USAGE] --root requires an existing host directory", file=sys.stderr)
            raise SystemExit(2)
        root = Path(args[1]).resolve()
        os.environ["DLM_PROJECT_ROOT"] = str(root)
        os.chdir(root)
        args = args[2:]
    entry = Path(__file__).resolve().parent / "skills" / "_shared" / "scripts" / "dlm.py"
    sys.argv = [str(entry), *args]
    runpy.run_path(str(entry), run_name="__main__")


if __name__ == "__main__":
    main()
