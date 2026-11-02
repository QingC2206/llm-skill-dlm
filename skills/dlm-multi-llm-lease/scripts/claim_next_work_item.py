#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sys

from work_item_lease_lib import claim_next, load_runtime_config, resolve_cli_worker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Claim the next available work item with a file-based lease.")
    parser.add_argument("--config", required=True, help="Path to work_item_runtime.json.")
    parser.add_argument(
        "--worker",
        help="Legacy-only explicit worker id; strict orchestration configs reject this option.",
    )
    parser.add_argument(
        "--worker-env-var",
        help="Legacy-only worker environment override; strict orchestration configs reject this option.",
    )
    parser.add_argument(
        "--item-id",
        help=(
            "Targeted claim for legacy configs, or for a strict config whose canonical policy "
            "or --user-authorization-ref contains exact user authorization for this item."
        ),
    )
    parser.add_argument(
        "--module",
        help="Targeted claim by module name (requires exact user authorization in strict mode).",
    )
    parser.add_argument(
        "--user-authorization-ref",
        help="Verbatim user instruction or DLM decision reference authorizing the targeted claim.",
    )
    parser.add_argument(
        "--reopen",
        action="store_true",
        help="Atomically reopen a completed/blocked item (or abandoned active item with --force-active) and claim it.",
    )
    parser.add_argument(
        "--force-active",
        action="store_true",
        help="When used with --reopen, allow taking over an item currently active under an abandoned session.",
    )
    parser.add_argument("--reason", help="Optional reason when --reopen is used.")
    parser.add_argument("--remark", help="Optional replacement for the static '备注' column on targeted claim/reopen.")
    parser.add_argument("--append-remark", help="Optional guidance to append to the static '备注' column.")
    parser.add_argument("--lease-seconds", type=int, help="Optional lease duration override.")
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
    )
    result = claim_next(
        config,
        worker=worker,
        item_id=args.item_id,
        module=args.module,
        reopen=args.reopen,
        force_active=args.force_active,
        reason=args.reason,
        remark=args.remark,
        append_remark=args.append_remark,
        user_authorization_ref=args.user_authorization_ref,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
