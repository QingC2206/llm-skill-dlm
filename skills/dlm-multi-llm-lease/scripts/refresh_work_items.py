#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sys

from work_item_lease_lib import load_runtime_config, refresh_work_items, resolve_cli_worker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh markdown task-board state from active lease files.")
    parser.add_argument("--config", required=True, help="Path to work_item_runtime.json.")
    parser.add_argument(
        "--worker",
        help="Legacy-only write-lock identity; strict parallel subtasks reject refresh/closeout.",
    )
    parser.add_argument(
        "--worker-env-var",
        help="Legacy-only worker environment override; strict orchestration configs reject this option.",
    )
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
        default="system",
    )
    result = refresh_work_items(config, worker=worker)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
