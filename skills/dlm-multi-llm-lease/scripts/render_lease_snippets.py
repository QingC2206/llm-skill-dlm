#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import re
import sys


ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the strict user-orchestrated top-level-task claim/lease contract."
    )
    parser.add_argument("--profile", choices=("generic", "codex"), default="codex")
    parser.add_argument("--task-root", default="<task_root>", help="Relative task root.")
    parser.add_argument(
        "--runtime-path",
        default=None,
        help="Relative runtime config path; defaults to <task_root>/references/work_item_runtime.json.",
    )
    parser.add_argument(
        "--work-items-path",
        default=None,
        help="Relative work-item table path; defaults to <task_root>/references/work_items.md.",
    )
    parser.add_argument("--claims-dir", default="<claims_dir>", help="Relative claims directory under out/.")
    parser.add_argument(
        "--worker-env-var",
        default=None,
        help="Host-provided stable task identity environment variable; Codex defaults to CODEX_THREAD_ID.",
    )
    parser.add_argument(
        "--session-env-var",
        default=None,
        help="Host-provided top-level session identity environment variable; Codex defaults to CODEX_SESSION_ID.",
    )
    parser.add_argument("--label", default="Task Group Label", help="Human label for runtime config.")
    parser.add_argument("--epic-id", default="EXXX/SXXX", help="Task or subtask id for runtime config.")
    parser.add_argument(
        "--lease-scripts-dir",
        default=".agents/skills/dlm-multi-llm-lease/scripts",
        help="Repository-relative authoritative lease script directory.",
    )
    parser.add_argument(
        "--sop-ref",
        default=None,
        help="Relative path or reference to the unified concrete SOP / execution guide.",
    )
    parser.add_argument(
        "--priority-tier",
        action="append",
        default=None,
        help="Priority-tier or series order for automatic claiming (repeat in order, e.g. --priority-tier A --priority-tier B).",
    )
    parser.add_argument("--object-label", default="模块", help="Module/object label used in the claim report.")
    parser.add_argument("--id-column", default="编号")
    parser.add_argument("--module-column", default="模块")
    parser.add_argument("--priority-column", default="优先级")
    parser.add_argument("--remark-column", default="备注")
    parser.add_argument("--state-column", default="状态")
    parser.add_argument("--worker-column", default="执行者")
    parser.add_argument("--started-at-column", default="开始时间")
    parser.add_argument("--completed-at-column", default="完成时间")
    parser.add_argument("--note-column", default="运行备注")
    parser.add_argument(
        "--note-column-alias",
        action="append",
        default=None,
        help="Additional note-column aliases accepted by runtime config.",
    )
    parser.add_argument("--lease-expired-note", default="租约过期回收，待重新领取。")
    parser.add_argument("--claim-missing-note", default="锁文件缺失，已回收。")
    parser.add_argument("--released-note", default="已释放，待重新领取。")
    parser.add_argument(
        "--authorized-item-id",
        action="append",
        default=None,
        help=(
            "Exact work item explicitly authorized by the user for targeted claim; repeat for each "
            "authorized item. Omit to keep automatic-only assignment."
        ),
    )
    parser.add_argument(
        "--user-authorization-ref",
        default=None,
        help="Reference to the explicit user instruction authorizing the exact targeted item(s).",
    )
    parser.add_argument(
        "--max-active-claims",
        type=int,
        default=1,
        help="Maximum active claims per worker (default 1; >1 requires explicit user multi-claim authorization).",
    )
    parser.add_argument(
        "--multi-claim-authorization-ref",
        default=None,
        help="Reference to the explicit user instruction authorizing holding multiple active claims.",
    )
    return parser.parse_args()


def strict_identity_env(args: argparse.Namespace) -> tuple[str, str]:
    worker_env_var = args.worker_env_var
    session_env_var = args.session_env_var
    if args.profile == "codex":
        worker_env_var = worker_env_var or "CODEX_THREAD_ID"
        session_env_var = session_env_var or "CODEX_SESSION_ID"
    if not worker_env_var or not session_env_var:
        raise ValueError(
            "generic profile requires --worker-env-var and --session-env-var; manual worker IDs are unsupported."
        )
    return worker_env_var, session_env_var


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    args = parse_args()
    try:
        worker_env_var, session_env_var = strict_identity_env(args)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc

    authorized_item_ids: list[str] = []
    for raw_item_id in args.authorized_item_id or []:
        item_id = raw_item_id.strip()
        if ITEM_ID_PATTERN.fullmatch(item_id) is None:
            raise SystemExit(
                "error: --authorized-item-id must match "
                "[A-Za-z0-9][A-Za-z0-9._-]{0,127}."
            )
        if item_id in authorized_item_ids:
            raise SystemExit(f"error: duplicate --authorized-item-id '{item_id}'.")
        authorized_item_ids.append(item_id)
    authorization_ref = (args.user_authorization_ref or "").strip()
    if authorized_item_ids and not authorization_ref:
        raise SystemExit(
            "error: --user-authorization-ref is required when --authorized-item-id is present."
        )
    if authorization_ref and not authorized_item_ids:
        raise SystemExit(
            "error: --user-authorization-ref requires at least one --authorized-item-id."
        )
    max_active_claims = int(args.max_active_claims)
    multi_claim_ref = (args.multi_claim_authorization_ref or "").strip()
    if max_active_claims < 1:
        raise SystemExit("error: --max-active-claims must be at least 1.")
    if max_active_claims > 1:
        if not authorized_item_ids:
            raise SystemExit("error: --max-active-claims > 1 requires --authorized-item-id.")
        if not multi_claim_ref:
            raise SystemExit("error: --multi-claim-authorization-ref is required when --max-active-claims > 1.")
        if len(authorized_item_ids) < max_active_claims:
            raise SystemExit("error: --max-active-claims cannot exceed the number of --authorized-item-id items.")
    elif multi_claim_ref:
        raise SystemExit("error: --multi-claim-authorization-ref requires --max-active-claims > 1.")
    runtime_path = args.runtime_path or f"{args.task_root}/references/work_item_runtime.json"
    work_items_path = args.work_items_path or f"{args.task_root}/references/work_items.md"

    note_candidates = [args.note_column]
    for alias in args.note_column_alias or []:
        alias_text = alias.strip()
        if alias_text and alias_text not in note_candidates:
            note_candidates.append(alias_text)

    priority_order: list[str] = []
    for raw_tier in args.priority_tier or []:
        tier_text = raw_tier.strip()
        if not tier_text:
            raise SystemExit("error: --priority-tier cannot be empty.")
        if tier_text not in priority_order:
            priority_order.append(tier_text)

    sop_ref = (args.sop_ref or "").strip() or None

    orchestration_policy = {
        "schema_version": 1,
        "profile": "user_started_top_level_tasks",
        "parallelism_authority": "user",
        "peer_awareness": "self_only",
        "identity_source": "host_task_environment",
        "worker_env_var": worker_env_var,
        "session_env_var": session_env_var,
        "require_equal_identity": True,
        "assignment_mode": (
            "next_available_or_user_authorized" if authorized_item_ids else "next_available_only"
        ),
        "max_active_claims_per_worker": max_active_claims,
        "worker_loop": "serial_claim_complete_repeat",
        "closure_mode": "followup_subtask_only",
    }
    if authorized_item_ids:
        orchestration_policy["user_authorized_item_ids"] = authorized_item_ids
        orchestration_policy["user_authorization_ref"] = authorization_ref
    if max_active_claims > 1:
        orchestration_policy["multi_claim_authorization_ref"] = multi_claim_ref

    runtime_config: dict[str, object] = {
        "epic_id": args.epic_id,
        "label": args.label,
        "work_items_path": work_items_path,
        "claims_dir": args.claims_dir,
    }
    if sop_ref is not None:
        runtime_config["sop_ref"] = sop_ref
    if priority_order:
        runtime_config["priority_order"] = priority_order
    runtime_config.update(
        {
            "lease_seconds": 1800,
            "lock_timeout_seconds": 120,
            "lock_stale_seconds": 600,
            "worker_env_var": worker_env_var,
            "orchestration_policy": orchestration_policy,
            "column_map": {
                "id": args.id_column,
                "module": args.module_column,
                "priority": args.priority_column,
                "remark": args.remark_column,
                "state": args.state_column,
                "worker": args.worker_column,
                "started_at": args.started_at_column,
                "completed_at": args.completed_at_column,
                "note": note_candidates if len(note_candidates) > 1 else note_candidates[0],
            },
            "note_messages": {
                "lease_expired": args.lease_expired_note,
                "claim_missing": args.claim_missing_note,
                "released": args.released_note,
            },
        }
    )

    print("## pre-creation assessment")
    print("- 前置信息 1（统一具体 SOP/任务指引）：确认存在明确的统一 SOP 或执行指引（`sop_ref`），所有模块遵循同一套执行步骤与产出合同。")
    print("- 前置信息 2（模块耦合度评估，中/弱等级提醒，由用户决策）：评估各模块是否存在耦合或文件重合；如有局部文件重合但影响面不同，LLM 仅向用户作中/弱等级提醒（可在 `备注` 列注明模块边界），不作脚本强制拦截，是否并行推进由用户最终把控。")
    print()

    print("## strict orchestration")
    print(f"- profile: `{args.profile}`")
    print(f"- host worker identity: `{worker_env_var}`")
    print(f"- host session identity: `{session_env_var}`")
    if sop_ref is not None:
        print(f"- unified SOP reference: `{sop_ref}`")
    if priority_order:
        print(f"- priority claim order: `{' -> '.join(priority_order)}`")
    print("- 用户决定是否并行以及启动多少个独立顶层任务；单个任务不读取并行数或 peer 清单。")
    print("- 每个顶层任务只串行执行 claim -> complete -> claim，直到 queue empty/closed 或用户停止。")
    print("- 本 Subtask 没有 coordinator/finalizer/election/统一收口；共享收口必须属于后续独立 Subtask。")
    print()

    print("## work_items note")
    print(f"> 本表是展示层；活跃领取状态的唯一事实源在 `{args.claims_dir}/*.json`。")
    print(
        "> 禁止手工改状态或填写 worker ID。默认由队列按优先级与行序分配下一可用项；只有用户明确授权并写入 canonical policy 的"
        "精确 work item 才能点名领取。"
    )
    print(f"> `{args.remark_column}` 列用于承载用户或上级任务提供的模块特定前置说明，领取与完成过程中保持只读；运行状态写入 `{args.note_column}` 列。")
    print()

    print("## work_items table template")
    print(
        f"| {args.id_column} | {args.module_column} | {args.priority_column} | {args.remark_column} | "
        f"{args.state_column} | {args.worker_column} | {args.started_at_column} | {args.completed_at_column} | {args.note_column} |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    tier_a = priority_order[0] if priority_order else "A"
    tier_b = priority_order[1] if len(priority_order) > 1 else "B"
    print(f"| W001 | ModuleA1 | {tier_a} | 用户/上级任务备注（只读传入） | ⬜ | — | — | — | — |")
    print(f"| W002 | ModuleB1 | {tier_b} |  | ⬜ | — | — | — | — |")
    print()

    print("## runtime config")
    print(json.dumps(runtime_config, ensure_ascii=False, indent=2))
    print()

    print("## claim return contract")
    print(
        "- success requires `resolved_worker`, `claim_file`, `claim_file_exists=true`, "
        "`claim_verified=true`, and `resolved_worker` equal to the current host task identity"
    )
    print(
        "- normalized fields: `work_item_id`, `work_item_module`, `work_item_priority`, `work_item_remark`, "
        "`work_item_state` (`⬜/🔄/✅/❌`), `work_item_status` (`open/active/done/blocked`), `work_item_worker`, "
        "`work_item_started_at`, `work_item_completed_at`, `work_item_note`"
    )
    print()

    print("## README / plan bullets")
    print("- 并行权限：只有用户可以通过启动多个独立顶层任务形成并行；任务自身不得创建并行执行者。")
    print("- 身份：宿主为每个顶层任务自动分配稳定唯一 ID；用户和 LLM 都不定义或复制 worker ID。")
    print("- 领取：默认由脚本原子选择下一可用项；禁止 `--worker` 和 `--worker-env-var`。")
    print("- 点名例外：只有用户明确授权且 exact item/ref 已写入 canonical policy 时，claim 才可使用 `--item-id`。")
    print("- 执行：默认每个任务只持有一个 claim；用户明确授权多项持有时按 SKILL 配置 exact 名单与上限，仍逐项串行；遵守合同确认与停止门。")
    print("- 感知：任务只知道自身 identity/current claim/queue result，不知道 N、peer identity 或是否存在 peer。")
    print("- 收口：本 Subtask 只产出逐 item 结果；聚合、投影、全量验证和最终状态由后续独立 Subtask 串行负责。")
    print()

    print("## SOP worker loop")
    print("```powershell")
    print(f"python {args.lease_scripts_dir}/claim_next_work_item.py `")
    print(f"  --config {runtime_path}")
    if authorized_item_ids:
        print(f"# user-authorized targeted items: {', '.join(authorized_item_ids)}")
        print("# replace the automatic claim above only for the exact item selected by the user for this call:")
        print(f"# python {args.lease_scripts_dir}/claim_next_work_item.py `")
        print(f"#   --config {runtime_path} `")
        print("#   --item-id <exact-user-authorized-item-id>")
    print("# execute only the returned item")
    print(f"python {args.lease_scripts_dir}/heartbeat_work_item.py `")
    print(f"  --config {runtime_path}")
    print(f"python {args.lease_scripts_dir}/complete_work_item.py `")
    print(f"  --config {runtime_path} `")
    print("  --status done `")
    print('  --note "High-signal conclusion."')
    print("# immediately claim again; stop only on queue empty/closed, user stop, or a real blocker")
    print("```")
    print()

    print("## non-blocking claim report")
    print("```text")
    print("已完成领取校验，继续串行执行当前 work item。")
    print()
    print("resolved_worker: {resolved_worker}")
    print("claim_file: {claim_file}")
    print("claim_verified: true")
    print("work item: {work_item_id}")
    print(f"{args.object_label}: {{work_item_module}}")
    print(f"{args.priority_column}: {{work_item_priority}}")
    print(f"{args.remark_column}: {{work_item_remark}}")
    print("```")
    print()

    print("## closure boundary")
    print("- worker 遇到 queue empty/closed 后直接停止，不竞争 finalizer，不改 shared aggregate，不关闭 Subtask。")
    print("- 后续收口 Subtask 必须与本并行队列分离，不出现在本 work_items 表，也不由任何 worker 自动启动。")
    print("- 后续收口 Subtask 才负责 denominator、missing/duplicate、0 active claim、聚合/投影/构建和最终验收。")
    print()

    print("## legacy boundary")
    print("- 底层 CLI 为历史配置保留显式 worker/item 参数；strict 配置始终拒绝 worker override。")
    print("- strict `--item-id` 默认拒绝，仅在 canonical policy 记录用户对 exact item 的明确授权时接受。")
    print("- 新任务必须使用本 skill 目录中的当前脚本，不能复制旧共享脚本或回退到手填 ID 的旧 SOP。")


if __name__ == "__main__":
    main()
