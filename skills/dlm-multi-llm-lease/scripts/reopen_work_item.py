#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sys

from work_item_lease_lib import load_runtime_config, reopen_work_item, resolve_cli_worker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reopen a completed, blocked, or abandoned active work item (and optionally claim it atomically)."
    )
    parser.add_argument("--config", required=True, help="Path to work_item_runtime.json.")
    parser.add_argument(
        "--worker",
        help="Legacy-only explicit worker id; strict orchestration configs reject this option.",
    )
    parser.add_argument(
        "--worker-env-var",
        help="Legacy-only worker environment override; strict orchestration configs reject this option.",
    )
    parser.add_argument("--item-id", help="Target work item ID to reopen.")
    parser.add_argument("--module", help="Target module name to reopen.")
    parser.add_argument("--reason", help="Reason or user instruction for reopening the work item.")
    parser.add_argument(
        "--user-authorization-ref",
        help="Verbatim user instruction or DLM decision reference authorizing the targeted reopen/claim.",
    )
    parser.add_argument("--remark", help="Replace the static '备注' (remark) column value for this item.")
    parser.add_argument("--append-remark", help="Append guidance to the static '备注' (remark) column for this item.")
    parser.add_argument(
        "--force-active",
        action="store_true",
        help="Allow reopening an item that is currently active under an abandoned session.",
    )
    parser.add_argument(
        "--claim",
        action="store_true",
        help="Atomically reopen and immediately claim the item for the current host worker under the same write lock.",
    )
    parser.add_argument("--lease-seconds", type=int, help="Optional lease duration override when --claim is used.")
    return parser.parse_args()


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    args = parse_args()
    config = load_runtime_config(args.config, lease_seconds_override=args.lease_seconds)
    worker = resolve_cli_worker(
        config,
        worker=args.worker,
        worker_env_var_override=args.worker_env_var,
        default="system",
    )
    result = reopen_work_item(
        config,
        worker=worker,
        item_id=args.item_id,
        module=args.module,
        reason=args.reason,
        remark=args.remark,
        append_remark=args.append_remark,
        force_active=args.force_active,
        claim=args.claim,
        user_authorization_ref=args.user_authorization_ref,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
