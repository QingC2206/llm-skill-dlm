#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sys

from work_item_lease_lib import complete_claim, load_runtime_config, resolve_cli_worker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Complete, block, or release an active work item.")
    parser.add_argument("--config", required=True, help="Path to work_item_runtime.json.")
    parser.add_argument(
        "--worker",
        help="Legacy-only explicit worker id; strict orchestration configs reject this option.",
    )
    parser.add_argument(
        "--worker-env-var",
        help="Legacy-only worker environment override; strict orchestration configs reject this option.",
    )
    parser.add_argument("--status", required=True, choices=("done", "blocked", "open"))
    parser.add_argument(
        "--item-id",
        help="Select an owned claim in explicit multi-claim mode; default strict mode resolves its sole claim.",
    )
    parser.add_argument(
        "--module",
        help="Select an owned claim by module name in explicit multi-claim mode.",
    )
    parser.add_argument("--note", help="Optional runtime note.")
    return parser.parse_args()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    args = parse_args()
    config = load_runtime_config(args.config)
    worker = resolve_cli_worker(
        config,
        worker=args.worker,
        worker_env_var_override=args.worker_env_var,
    )
    result = complete_claim(
        config,
        worker=worker,
        status=args.status,
        item_id=args.item_id,
        note=args.note,
        module=args.module,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
