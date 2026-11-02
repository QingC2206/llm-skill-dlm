#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from work_item_lease_lib import (
    claim_next,
    complete_claim,
    heartbeat_claim,
    load_runtime_config,
    refresh_work_items,
    reopen_work_item,
)


WORKER_ENV = "TEST_HOST_THREAD_ID"
SESSION_ENV = "TEST_HOST_SESSION_ID"


class StrictOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "work_item_runtime.json"
        (self.root / "work_items.md").write_text(
            "# Work items\n\n"
            "| 编号 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |\n"
            "|---|---|---|---|---|---|\n"
            "| W001 | ⬜ | — | — | — | — |\n"
            "| W002 | ⬜ | — | — | — | — |\n",
            encoding="utf-8",
        )
        self.write_config()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def orchestration_policy(**overrides: object) -> dict[str, object]:
        policy: dict[str, object] = {
            "schema_version": 1,
            "profile": "user_started_top_level_tasks",
            "parallelism_authority": "user",
            "peer_awareness": "self_only",
            "identity_source": "host_task_environment",
            "worker_env_var": WORKER_ENV,
            "session_env_var": SESSION_ENV,
            "require_equal_identity": True,
            "assignment_mode": "next_available_only",
            "max_active_claims_per_worker": 1,
            "worker_loop": "serial_claim_complete_repeat",
            "closure_mode": "followup_subtask_only",
        }
        policy.update(overrides)
        return policy

    def write_config(self, *, policy: dict[str, object] | None = None) -> None:
        data = {
            "epic_id": "TEST/STRICT",
            "label": "Strict orchestration test",
            "path_base": "config_dir",
            "work_items_path": "work_items.md",
            "claims_dir": "claims",
            "lease_seconds": 1800,
            "lock_timeout_seconds": 2,
            "lock_stale_seconds": 2,
            "worker_env_var": WORKER_ENV,
            "orchestration_policy": policy or self.orchestration_policy(),
        }
        self.config_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def identity(self, worker: str, session: str | None = None) -> mock._patch_dict:
        return mock.patch.dict(
            os.environ,
            {WORKER_ENV: worker, SESSION_ENV: session or worker},
            clear=False,
        )

    def test_same_host_task_reenters_then_serially_claims_next_item(self) -> None:
        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            first = claim_next(config, worker="root-a")
            reentry = claim_next(config, worker="root-a")
            heartbeat = heartbeat_claim(config, worker="root-a")
            completed = complete_claim(config, worker="root-a", status="done")
            second = claim_next(config, worker="root-a")

        self.assertEqual(("claimed", "W001"), (first["mode"], first["work_item_id"]))
        self.assertEqual(("reentered", "W001"), (reentry["mode"], reentry["work_item_id"]))
        self.assertEqual("heartbeat", heartbeat["mode"])
        self.assertEqual("completed:done", completed["mode"])
        self.assertEqual(("claimed", "W002"), (second["mode"], second["work_item_id"]))
        self.assertEqual(1, len(list((self.root / "claims").glob("*.json"))))

    def test_different_host_tasks_keep_distinct_identity_and_claims(self) -> None:
        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            first = claim_next(config, worker="root-a")
        with self.identity("root-b"):
            second = claim_next(config, worker="root-b")

        self.assertEqual(("root-a", "W001"), (first["resolved_worker"], first["work_item_id"]))
        self.assertEqual(("root-b", "W002"), (second["resolved_worker"], second["work_item_id"]))
        self.assertEqual(2, len(list((self.root / "claims").glob("*.json"))))

    def test_manual_worker_item_and_child_identity_are_rejected(self) -> None:
        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            with self.assertRaisesRegex(PermissionError, "current host task identity"):
                claim_next(config, worker="invented-worker")
            with self.assertRaisesRegex(PermissionError, "requires exact user authorization"):
                claim_next(config, worker="root-a", item_id="W002")
        with self.identity("root-a", "child-session"):
            with self.assertRaisesRegex(PermissionError, "independent top-level task"):
                claim_next(config, worker="root-a")
        self.assertFalse(list((self.root / "claims").glob("*.json")))

    def test_strict_cli_rejects_manual_identity_and_item_selection(self) -> None:
        cli = Path(__file__).with_name("claim_next_work_item.py")
        env = os.environ.copy()
        env[WORKER_ENV] = "root-a"
        env[SESSION_ENV] = "root-a"

        manual_worker = subprocess.run(
            [sys.executable, str(cli), "--config", str(self.config_path), "--worker", "root-a"],
            cwd=str(self.root),
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        manual_env = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--config",
                str(self.config_path),
                "--worker-env-var",
                WORKER_ENV,
            ],
            cwd=str(self.root),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        manual_item = subprocess.run(
            [sys.executable, str(cli), "--config", str(self.config_path), "--item-id", "W002"],
            cwd=str(self.root),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(0, manual_worker.returncode)
        self.assertIn("--worker is forbidden", manual_worker.stderr)
        self.assertNotEqual(0, manual_env.returncode)
        self.assertIn("--worker-env-var is forbidden", manual_env.stderr)
        self.assertNotEqual(0, manual_item.returncode)
        self.assertIn("requires exact user authorization", manual_item.stderr)
        self.assertFalse(list((self.root / "claims").glob("*.json")))

    def test_exact_user_authorization_allows_targeted_claim_and_keeps_auto_default(self) -> None:
        self.write_config(
            policy=self.orchestration_policy(
                assignment_mode="next_available_or_user_authorized",
                user_authorized_item_ids=["W002"],
                user_authorization_ref="User instruction: claim W002 before W001.",
            )
        )
        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            targeted = claim_next(config, worker="root-a", item_id="W002")
            complete_claim(config, worker="root-a", status="done")
            automatic = claim_next(config, worker="root-a")

        self.assertEqual(("claimed", "W002"), (targeted["mode"], targeted["work_item_id"]))
        self.assertEqual(("claimed", "W001"), (automatic["mode"], automatic["work_item_id"]))

    def test_user_authorization_is_exact_and_cannot_expand_to_another_item(self) -> None:
        self.write_config(
            policy=self.orchestration_policy(
                assignment_mode="next_available_or_user_authorized",
                user_authorized_item_ids=["W002"],
                user_authorization_ref="User instruction: target W002.",
            )
        )
        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            with self.assertRaisesRegex(PermissionError, "is not listed"):
                claim_next(config, worker="root-a", item_id="W001")

        self.assertFalse(list((self.root / "claims").glob("*.json")))

    def test_targeted_claim_cannot_bypass_one_active_claim(self) -> None:
        self.write_config(
            policy=self.orchestration_policy(
                assignment_mode="next_available_or_user_authorized",
                user_authorized_item_ids=["W001", "W002"],
                user_authorization_ref="User instruction: W001 then W002.",
            )
        )
        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            first = claim_next(config, worker="root-a", item_id="W001")
            with self.assertRaisesRegex(PermissionError, "complete or release it"):
                claim_next(config, worker="root-a", item_id="W002")

        self.assertEqual("W001", first["work_item_id"])
        self.assertEqual(1, len(list((self.root / "claims").glob("*.json"))))

    def test_strict_cli_accepts_exact_user_authorized_item(self) -> None:
        self.write_config(
            policy=self.orchestration_policy(
                assignment_mode="next_available_or_user_authorized",
                user_authorized_item_ids=["W002"],
                user_authorization_ref="User instruction: target W002.",
            )
        )
        cli = Path(__file__).with_name("claim_next_work_item.py")
        env = os.environ.copy()
        env[WORKER_ENV] = "root-a"
        env[SESSION_ENV] = "root-a"
        result = subprocess.run(
            [sys.executable, str(cli), "--config", str(self.config_path), "--item-id", "W002"],
            cwd=str(self.root),
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("W002", json.loads(result.stdout)["work_item_id"])

    def test_authorized_assignment_requires_exact_ids_and_user_reference(self) -> None:
        self.write_config(
            policy=self.orchestration_policy(
                assignment_mode="next_available_or_user_authorized",
            )
        )
        with self.assertRaisesRegex(ValueError, "user_authorized_item_ids"):
            load_runtime_config(str(self.config_path))

        self.write_config(
            policy=self.orchestration_policy(
                user_authorized_item_ids=["W002"],
                user_authorization_ref="Not valid in automatic-only mode.",
            )
        )
        with self.assertRaisesRegex(ValueError, "must not declare user authorization fields"):
            load_runtime_config(str(self.config_path))

    def test_worker_refresh_closeout_is_rejected(self) -> None:
        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            with self.assertRaisesRegex(PermissionError, "later follow-up subtask"):
                refresh_work_items(config, worker="root-a")

    def test_runtime_rejects_non_deferred_closeout_policy(self) -> None:
        self.write_config(policy=self.orchestration_policy(closure_mode="worker_finalizer_election"))
        with self.assertRaisesRegex(ValueError, "followup_subtask_only"):
            load_runtime_config(str(self.config_path))

    def test_project_root_resolves_docs_llm_and_doc_llm_without_git(self) -> None:
        for kb_name in ("docs-llm", "doc_llm"):
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                task_ref = repo / kb_name / "Tasks" / "E001_demo" / "references"
                task_ref.mkdir(parents=True)
                (task_ref / "work_items.md").write_text(
                    "# Work items\n\n"
                    "| 编号 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |\n"
                    "|---|---|---|---|---|---|\n"
                    "| W001 | ⬜ | — | — | — | — |\n",
                    encoding="utf-8",
                )
                cfg = task_ref / "work_item_runtime.json"
                cfg.write_text(
                    json.dumps(
                        {
                            "epic_id": "E001/S001",
                            "label": "DLM root test",
                            "path_base": "project_root",
                            "work_items_path": f"{kb_name}/Tasks/E001_demo/references/work_items.md",
                            "claims_dir": "out/claims/E001_S001",
                            "worker_env_var": WORKER_ENV,
                            "orchestration_policy": self.orchestration_policy(),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                loaded = load_runtime_config(str(cfg))
                self.assertEqual(loaded.base_dir, repo.resolve())
                self.assertEqual(loaded.work_items_path, (task_ref / "work_items.md").resolve())

    def test_render_lease_snippets_supports_single_and_multi_claim(self) -> None:
        renderer = Path(__file__).with_name("render_lease_snippets.py")
        single = subprocess.run(
            [sys.executable, "-X", "utf8", str(renderer), "--task-root", "docs-llm/Tasks/E001_demo"],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, single.returncode, single.stderr)
        self.assertIn("docs-llm/Tasks/E001_demo/references/work_item_runtime.json", single.stdout)
        self.assertIn(".agents/skills/dlm-multi-llm-lease/scripts/claim_next_work_item.py", single.stdout)
        self.assertIn("已完成领取校验，继续串行执行当前 work item。", single.stdout)

        multi = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(renderer),
                "--task-root",
                "doc_llm/Tasks/E001_demo",
                "--authorized-item-id",
                "W001",
                "--authorized-item-id",
                "W002",
                "--user-authorization-ref",
                "D2: User authorized W001 and W002",
                "--max-active-claims",
                "2",
                "--multi-claim-authorization-ref",
                "D2: User authorized holding 2 items serially",
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, multi.returncode, multi.stderr)
        self.assertIn('"max_active_claims_per_worker": 2', multi.stdout)
        self.assertIn('"multi_claim_authorization_ref": "D2: User authorized holding 2 items serially"', multi.stdout)

    def test_priority_tier_ordering_claims_series_in_order_across_workers(self) -> None:
        (self.root / "work_items.md").write_text(
            "# Work items\n\n"
            "| 编号 | 模块 | 优先级 | 备注 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| W001 | ModC1 | C | C系列模块1 | open | — | — | — | — |\n"
            "| W002 | ModB1 | B系列 | B系列模块1 | open | — | — | — | — |\n"
            "| W003 | ModA1 | A | A系列模块1 | open | — | — | — | — |\n"
            "| W004 | ModB2 | B | B系列模块2 | open | — | — | — | — |\n"
            "| W005 | ModA2 | A-核心 | A系列模块2 | open | — | — | — | — |\n",
            encoding="utf-8",
        )
        data = json.loads(self.config_path.read_text(encoding="utf-8"))
        data["sop_ref"] = "docs-llm/SOPs/module-sop/README.md"
        data["priority_order"] = ["A", "B", "C"]
        self.config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            claim_a1 = claim_next(config, worker="root-a")
        with self.identity("root-b"):
            claim_a2 = claim_next(config, worker="root-b")
        with self.identity("root-c"):
            claim_b1 = claim_next(config, worker="root-c")

        self.assertEqual(("W003", "ModA1", "A", "A系列模块1"), (
            claim_a1["work_item_id"],
            claim_a1["work_item_module"],
            claim_a1["work_item_priority"],
            claim_a1["work_item_remark"],
        ))
        self.assertEqual("docs-llm/SOPs/module-sop/README.md", claim_a1["sop_ref"])
        self.assertEqual(["A", "B", "C"], claim_a1["priority_order"])
        self.assertEqual(("W005", "ModA2", "A-核心", "A系列模块2"), (
            claim_a2["work_item_id"],
            claim_a2["work_item_module"],
            claim_a2["work_item_priority"],
            claim_a2["work_item_remark"],
        ))
        self.assertEqual(("W002", "ModB1", "B系列", "B系列模块1"), (
            claim_b1["work_item_id"],
            claim_b1["work_item_module"],
            claim_b1["work_item_priority"],
            claim_b1["work_item_remark"],
        ))

        with self.identity("root-a"):
            complete_claim(config, worker="root-a", status="done", note="ModA1 done")
            claim_b2 = claim_next(config, worker="root-a")
        with self.identity("root-b"):
            complete_claim(config, worker="root-b", status="done", note="ModA2 done")
            claim_c1 = claim_next(config, worker="root-b")

        self.assertEqual(("W004", "ModB2"), (claim_b2["work_item_id"], claim_b2["work_item_module"]))
        self.assertEqual(("W001", "ModC1"), (claim_c1["work_item_id"], claim_c1["work_item_module"]))

    def test_remark_column_is_preserved_and_returned_across_lifecycle(self) -> None:
        (self.root / "work_items.md").write_text(
            "# Work items\n\n"
            "| 编号 | 模块 | 优先级 | 备注 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| W001 | AuthModule | A | 注意兼容旧版 Token，仅改 src/auth/ | open | — | — | — | 租约过期回收，待重新领取。 |\n"
            "| W002 | EmptyRemarkModule | B |  | ⬜ | — | — | — | — |\n",
            encoding="utf-8",
        )
        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            claimed = claim_next(config, worker="root-a")
            self.assertEqual("AuthModule", claimed["work_item_module"])
            self.assertEqual("注意兼容旧版 Token，仅改 src/auth/", claimed["work_item_remark"])
            self.assertEqual("—", claimed["work_item_note"])

            hb = heartbeat_claim(config, worker="root-a")
            self.assertEqual("heartbeat", hb["mode"])

            reentered = claim_next(config, worker="root-a")
            self.assertEqual("注意兼容旧版 Token，仅改 src/auth/", reentered["work_item_remark"])

            done = complete_claim(config, worker="root-a", status="done", note="已完成 SOP 校验")
            self.assertEqual("注意兼容旧版 Token，仅改 src/auth/", done["work_item_remark"])
            self.assertEqual("已完成 SOP 校验", done["work_item_note"])

            claimed_empty = claim_next(config, worker="root-a")
            self.assertEqual("EmptyRemarkModule", claimed_empty["work_item_module"])
            self.assertEqual("", claimed_empty["work_item_remark"])
            complete_claim(config, worker="root-a", status="done", note="Done W002")

        table_text = (self.root / "work_items.md").read_text(encoding="utf-8")
        self.assertIn("注意兼容旧版 Token，仅改 src/auth/", table_text)
        self.assertIn("已完成 SOP 校验", table_text)

        # Also verify legacy 6-column table without '备注' column: compatible and returns work_item_remark=""
        (self.root / "work_items.md").write_text(
            "# Work items\n\n"
            "| 编号 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |\n"
            "|---|---|---|---|---|---|\n"
            "| W001 | ⬜ | — | — | — | — |\n",
            encoding="utf-8",
        )
        legacy_no_remark_cfg = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            legacy_no_remark_claim = claim_next(legacy_no_remark_cfg, worker="root-a")
            self.assertEqual("", legacy_no_remark_claim["work_item_remark"])
            complete_claim(legacy_no_remark_cfg, worker="root-a", status="done")

        # And legacy 6-column table where '备注' is the only note column:
        # user remark must not be wiped on claim_next.
        (self.root / "work_items.md").write_text(
            "# Work items\n\n"
            "| 编号 | 状态 | 执行者 | 开始时间 | 完成时间 | 备注 |\n"
            "|---|---|---|---|---|---|\n"
            "| W001 | open | — | — | — | 用户预设模块备注 |\n",
            encoding="utf-8",
        )
        legacy_cfg = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            legacy_claim = claim_next(legacy_cfg, worker="root-a")
            self.assertEqual("用户预设模块备注", legacy_claim["work_item_remark"])
            self.assertEqual("用户预设模块备注", legacy_claim["work_item_note"])

    def test_render_lease_snippets_includes_sop_priority_and_remark_columns(self) -> None:
        renderer = Path(__file__).with_name("render_lease_snippets.py")
        rendered = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(renderer),
                "--task-root",
                "docs-llm/Tasks/E001_demo",
                "--sop-ref",
                "docs-llm/SOPs/plugin-manual/README.md",
                "--priority-tier",
                "A",
                "--priority-tier",
                "B",
                "--priority-tier",
                "C",
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, rendered.returncode, rendered.stderr)
        self.assertIn('"sop_ref": "docs-llm/SOPs/plugin-manual/README.md"', rendered.stdout)
        self.assertIn('"priority_order": [', rendered.stdout)
        self.assertIn('"module": "模块"', rendered.stdout)
        self.assertIn('"priority": "优先级"', rendered.stdout)
        self.assertIn('"remark": "备注"', rendered.stdout)
        self.assertIn("| 编号 | 模块 | 优先级 | 备注 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |", rendered.stdout)

    def test_targeted_module_claim_and_auto_authorization_persistence(self) -> None:
        (self.root / "work_items.md").write_text(
            "# Work items\n\n"
            "| 编号 | 模块 | 优先级 | 备注 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| W001 | ModA1 | A | A模块说明 | ⬜ | — | — | — | — |\n"
            "| W002 | ModB1 | B | B模块说明 | ⬜ | — | — | — | — |\n",
            encoding="utf-8",
        )
        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            # Without authorization, targeting a module is rejected
            with self.assertRaisesRegex(PermissionError, "requires exact user authorization"):
                claim_next(config, worker="root-a", module="ModB1")

            # Passing user_authorization_ref atomically persists authorization and claims ModB1 (W002)
            claimed = claim_next(
                config,
                worker="root-a",
                module="ModB1",
                user_authorization_ref="用户指定先领用 ModB1",
            )
            self.assertEqual(("claimed", "W002", "ModB1", "active"), (
                claimed["mode"],
                claimed["work_item_id"],
                claimed["work_item_module"],
                claimed["work_item_status"],
            ))
            complete_claim(config, worker="root-a", status="done", note="ModB1 完成")

            # Targeting an already done module without --reopen gives a clear diagnostic error
            with self.assertRaisesRegex(ValueError, "already in terminal state '✅'.*--reopen"):
                claim_next(config, worker="root-a", module="ModB1")

    def test_reopen_completed_blocked_and_abandoned_active_items(self) -> None:
        (self.root / "work_items.md").write_text(
            "# Work items\n\n"
            "| 编号 | 模块 | 优先级 | 备注 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| W001 | ModA1 | A | 初始备注 | ✅ | root-old | 2026-10-30 10:00:00 +0800 | 2026-10-30 10:10:00 +0800 | 初次完成 |\n"
            "| W002 | ModB1 | B | B初始备注 | ⬜ | — | — | — | — |\n",
            encoding="utf-8",
        )
        config = load_runtime_config(str(self.config_path))

        # 1. Reopen W001 (ModA1) back to open (⬜) with appended remark via CLI
        reopen_cli = Path(__file__).with_name("reopen_work_item.py")
        env = os.environ.copy()
        env[WORKER_ENV] = "root-a"
        env[SESSION_ENV] = "root-a"
        reopened_proc = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                str(reopen_cli),
                "--config",
                str(self.config_path),
                "--module",
                "ModA1",
                "--reason",
                "用户复检发现需补充空指针校验",
                "--append-remark",
                "返工：补充空指针校验",
            ],
            cwd=str(self.root),
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, reopened_proc.returncode, reopened_proc.stderr)
        reopened_payload = json.loads(reopened_proc.stdout)
        self.assertEqual("reopened", reopened_payload["mode"])
        self.assertEqual("⬜", reopened_payload["work_item_state"])
        self.assertEqual("open", reopened_payload["work_item_status"])
        self.assertEqual("初始备注；返工：补充空指针校验", reopened_payload["work_item_remark"])
        self.assertIn("已重开：用户复检发现需补充空指针校验", reopened_payload["work_item_note"])

        # 2. Complete W001 as blocked, then atomically reopen-and-claim via claim_next(..., reopen=True)
        config = load_runtime_config(str(self.config_path))
        with self.identity("root-a"):
            claim_next(config, worker="root-a")
            complete_claim(config, worker="root-a", status="blocked", note="缺少依赖")
            reclaimed = claim_next(
                config,
                worker="root-a",
                module="ModA1",
                reopen=True,
                reason="依赖已就绪，重开并立即领用",
                append_remark="二次重开：依赖已修复",
            )
            self.assertEqual("reopened_and_claimed", reclaimed["mode"])
            self.assertEqual("🔄", reclaimed["work_item_state"])
            self.assertEqual("初始备注；返工：补充空指针校验；二次重开：依赖已修复", reclaimed["work_item_remark"])

        # 3. Abandoned active session takeover: root-b cannot take over root-a's active W001 without --force-active
        with self.identity("root-b"):
            with self.assertRaisesRegex(PermissionError, "currently active under worker 'root-a'.*--force-active"):
                reopen_work_item(
                    config,
                    worker="root-b",
                    module="ModA1",
                    reason="尝试接管",
                    claim=True,
                )
            takeover = reopen_work_item(
                config,
                worker="root-b",
                module="ModA1",
                reason="原窗口已关闭，用户授权接管",
                force_active=True,
                claim=True,
            )
            self.assertEqual(("reopened_and_claimed", "root-b", "W001"), (
                takeover["mode"],
                takeover["resolved_worker"],
                takeover["work_item_id"],
            ))

    def test_same_worker_expired_lease_auto_renews_on_reentry_heartbeat_and_complete(self) -> None:
        (self.root / "work_items.md").write_text(
            "# Work items\n\n"
            "| 编号 | 模块 | 优先级 | 备注 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| W001 | ModA1 | A | 耗时模块备注 | ⬜ | — | — | — | — |\n"
            "| W002 | ModA2 | A | 模块2备注 | ⬜ | — | — | — | — |\n",
            encoding="utf-8",
        )
        config = load_runtime_config(str(self.config_path))

        def expire_claim_on_disk(item_id: str) -> None:
            cpath = self.root / "claims" / f"{item_id}.json"
            cdata = json.loads(cpath.read_text(encoding="utf-8"))
            cdata["expires_at"] = "2020-01-01T00:00:00+08:00"
            cpath.write_text(json.dumps(cdata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        with self.identity("root-a"):
            claimed = claim_next(config, worker="root-a")
            self.assertEqual("W001", claimed["work_item_id"])

            # Simulate lease expiring while root-a is still working and no other worker claimed W001
            expire_claim_on_disk("W001")
            hb = heartbeat_claim(config, worker="root-a")
            self.assertEqual(("heartbeat", "W001", "耗时模块备注"), (
                hb["mode"],
                hb["work_item_id"],
                hb["work_item_remark"],
            ))

            # Expire again and re-enter claim_next: must renew W001 (reentered), NOT drop it!
            expire_claim_on_disk("W001")
            reentered = claim_next(config, worker="root-a")
            self.assertEqual(("reentered", "W001"), (reentered["mode"], reentered["work_item_id"]))

            # Expire again and call complete_claim: must succeed!
            expire_claim_on_disk("W001")
            done = complete_claim(config, worker="root-a", status="done", note="长耗时任务完成")
            self.assertEqual(("completed:done", "W001"), (done["mode"], done["work_item_id"]))


if __name__ == "__main__":
    unittest.main()
