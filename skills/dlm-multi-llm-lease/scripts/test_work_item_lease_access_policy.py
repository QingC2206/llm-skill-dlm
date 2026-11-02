#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import work_item_lease_lib as lease_lib

from work_item_lease_lib import (
    canonical_record_digest,
    claim_next,
    complete_claim,
    heartbeat_claim,
    load_runtime_config,
    refresh_work_items,
)


PRIMARY_ROLE = "independent_primary"
COORDINATOR_ROLE = "coordinator"


class AccessPolicyLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config_path = self.root / "work_item_runtime.json"
        self.work_items_path = self.root / "work_items.md"
        self.work_items_path.write_text(
            "# Work items\n\n"
            "| 编号 | 状态 | 执行者 | 开始时间 | 完成时间 | 运行备注 |\n"
            "|---|---|---|---|---|---|\n"
            "| W001 | ⬜ | — | — | — | — |\n"
            "| W002 | ⬜ | — | — | — | — |\n",
            encoding="utf-8",
        )
        self.saved_identity_env = {
            name: os.environ.get(name)
            for name in ("TEST_SESSION_ID", "TEST_THREAD_ID")
        }

    def tearDown(self) -> None:
        for name, value in self.saved_identity_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temp.cleanup()

    def policy(self, *, queue_open: bool, identities: list[dict[str, object]] | None = None) -> dict[str, object]:
        return {
            "schema_version": 1,
            "queue_open": queue_open,
            "admission": {
                "dir": "admissions",
                "file_template": "{worker}.json",
                "worker_field": "thread_id",
                "role_field": "role",
                "digest_field": "admission_digest",
                "digest_fields": [
                    "schema_version",
                    "task",
                    "role",
                    "session_id",
                    "thread_id",
                    "admitted_at",
                ],
                "identity_fields": ["session_id", "thread_id"],
                "environment_bindings": {
                    "session_id": "TEST_SESSION_ID",
                    "thread_id": "TEST_THREAD_ID",
                },
                "require_equal_fields": [["session_id", "thread_id"]],
                "expected_fields": {"task": "TEST/S009"},
                "allowed_identities": identities or [],
                "allowed_roles": [PRIMARY_ROLE, COORDINATOR_ROLE],
            },
            "action_roles": {
                "claim": [PRIMARY_ROLE],
                "heartbeat": [PRIMARY_ROLE],
                "complete": [PRIMARY_ROLE],
                "refresh": [COORDINATOR_ROLE],
            },
            "max_active_claims_per_worker": 1,
            "completion_receipt": {
                "required_statuses": ["done", "blocked"],
                "path_template": "receipts/{work_item_id}.json",
                "worker_field": "worker",
                "item_field": "work_item_id",
                "status_field": "status",
                "digest_field": "receipt_digest",
                "claim_admission_digest_field": "admission_digest",
                "claim_admission_binding_field": "access_admission",
                "artifact_digest_bindings": [
                    {
                        "receipt_field": "dossier_digest",
                        "path_template": "dossiers/{work_item_id}.json",
                    }
                ],
                "json_digest_bindings": [
                    {
                        "receipt_field": "lineage_digest",
                        "path_template": "dossiers/{work_item_id}.json",
                        "json_pointer": "/lineage",
                    }
                ],
                "field_constraints": {
                    "verdict": {"allowed_values": ["KEEP"]},
                    "admission_digest": {"pattern": "^sha256:[0-9a-f]{64}$"},
                    "lineage_digest": {"pattern": "^sha256:[0-9a-f]{64}$"},
                    "dossier_digest": {"pattern": "^sha256:[0-9a-f]{64}$"},
                },
                "digest_fields": [
                    "task",
                    "worker",
                    "work_item_id",
                    "status",
                    "admission_digest",
                    "access_admission",
                    "verdict",
                    "lineage_digest",
                    "dossier_digest",
                ],
                "expected_fields": {"task": "TEST/S009"},
            },
        }

    def write_config(self, *, access_policy: dict[str, object] | None) -> None:
        data: dict[str, object] = {
            "epic_id": "TEST/S009",
            "label": "Lease access policy test",
            "path_base": "config_dir",
            "work_items_path": "work_items.md",
            "claims_dir": "claims",
            "lease_seconds": 1800,
            "lock_timeout_seconds": 2,
            "lock_stale_seconds": 2,
        }
        if access_policy is not None:
            data["access_policy"] = access_policy
        self.config_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_admission(self, worker: str, role: str, *, admitted_at: str = "2026-08-19T00:00:00+08:00") -> Path:
        record = {
            "schema_version": 1,
            "task": "TEST/S009",
            "role": role,
            "session_id": worker,
            "thread_id": worker,
            "admitted_at": admitted_at,
        }
        record["admission_digest"] = canonical_record_digest(
            record,
            ("schema_version", "task", "role", "session_id", "thread_id", "admitted_at"),
        )
        path = self.root / "admissions" / f"{worker}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def write_receipt(self, worker: str, item_id: str, status: str) -> Path:
        admission = json.loads((self.root / "admissions" / f"{worker}.json").read_text(encoding="utf-8"))
        claim = json.loads((self.root / "claims" / f"{item_id}.json").read_text(encoding="utf-8"))
        claim_binding = claim["access_admission"]
        persisted_binding = {
            field: claim_binding[field]
            for field in ("schema_version", "admission_digest", "worker", "role", "identity")
        }
        dossier_path = self.root / "dossiers" / f"{item_id}.json"
        dossier_path.parent.mkdir(parents=True, exist_ok=True)
        lineage = {"items": [item_id]}
        dossier_path.write_text(
            json.dumps({"work_item_id": item_id, "lineage": lineage}, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        receipt = {
            "task": "TEST/S009",
            "worker": worker,
            "work_item_id": item_id,
            "status": status,
            "admission_digest": admission["admission_digest"],
            "access_admission": persisted_binding,
            "verdict": "KEEP",
            "lineage_digest": "sha256:"
            + hashlib.sha256(
                json.dumps(lineage, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "dossier_digest": "sha256:" + hashlib.sha256(dossier_path.read_bytes()).hexdigest(),
        }
        receipt["receipt_digest"] = canonical_record_digest(
            receipt,
            (
                "task",
                "worker",
                "work_item_id",
                "status",
                "admission_digest",
                "access_admission",
                "verdict",
                "lineage_digest",
                "dossier_digest",
            ),
        )
        path = self.root / "receipts" / f"{item_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def identity(worker: str, *roles: str) -> dict[str, object]:
        return {"session_id": worker, "thread_id": worker, "roles": list(roles)}

    @staticmethod
    def set_process_identity(session_id: str | None, thread_id: str | None) -> None:
        for name, value in (
            ("TEST_SESSION_ID", session_id),
            ("TEST_THREAD_ID", thread_id),
        ):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_closed_queue_rejects_new_claim_without_mutation(self) -> None:
        worker = "root-a"
        self.write_config(access_policy=self.policy(queue_open=False, identities=[self.identity(worker, PRIMARY_ROLE)]))
        self.write_admission(worker, PRIMARY_ROLE)
        self.set_process_identity(worker, worker)
        config = load_runtime_config(str(self.config_path))
        before_table = self.work_items_path.read_bytes()
        before_claims = sorted(config.claims_dir.iterdir())

        with self.assertRaisesRegex(PermissionError, "queue is closed"):
            claim_next(config, worker)

        self.assertEqual(before_table, self.work_items_path.read_bytes())
        self.assertEqual(before_claims, sorted(config.claims_dir.iterdir()))

    def test_direct_shared_call_without_admission_is_rejected(self) -> None:
        worker = "root-a"
        self.write_config(access_policy=self.policy(queue_open=True, identities=[self.identity(worker, PRIMARY_ROLE)]))
        self.set_process_identity(worker, worker)
        config = load_runtime_config(str(self.config_path))

        with self.assertRaisesRegex(PermissionError, "Admission file is required"):
            claim_next(config, worker)

        self.assertFalse(list(config.claims_dir.glob("*.json")))
        self.assertIn("| W001 | ⬜ |", self.work_items_path.read_text(encoding="utf-8"))

    def test_worker_and_coordinator_roles_are_separated_and_identity_binds_role(self) -> None:
        worker = "root-a"
        coordinator = "coord-a"
        identities = [
            self.identity(worker, PRIMARY_ROLE),
            self.identity(coordinator, COORDINATOR_ROLE),
        ]
        self.write_config(access_policy=self.policy(queue_open=True, identities=identities))
        self.write_admission(worker, PRIMARY_ROLE)
        self.write_admission(coordinator, COORDINATOR_ROLE)
        config = load_runtime_config(str(self.config_path))

        self.set_process_identity(worker, worker)
        with self.assertRaisesRegex(PermissionError, "cannot perform action 'refresh'"):
            refresh_work_items(config, worker)
        self.set_process_identity(coordinator, coordinator)
        with self.assertRaisesRegex(PermissionError, "cannot perform action 'claim'"):
            claim_next(config, coordinator)
        refreshed = refresh_work_items(config, coordinator)
        self.assertEqual("refresh", refreshed["mode"])

        self.write_admission(worker, COORDINATOR_ROLE)
        self.set_process_identity(worker, worker)
        with self.assertRaisesRegex(PermissionError, "not allowlisted"):
            refresh_work_items(config, worker)

    def test_one_worker_cannot_hold_second_active_claim(self) -> None:
        worker = "root-a"
        self.write_config(access_policy=self.policy(queue_open=True, identities=[self.identity(worker, PRIMARY_ROLE)]))
        self.write_admission(worker, PRIMARY_ROLE)
        self.set_process_identity(worker, worker)
        config = load_runtime_config(str(self.config_path))

        first = claim_next(config, worker, item_id="W001")
        self.assertEqual("claimed", first["mode"])
        with self.assertRaisesRegex(PermissionError, "configured maximum"):
            claim_next(config, worker, item_id="W002")
        reentered = claim_next(config, worker, item_id="W001")
        self.assertEqual("reentered", reentered["mode"])
        self.assertFalse((config.claims_dir / "W002.json").exists())

    def test_work_item_id_cannot_escape_claims_directory(self) -> None:
        worker = "root-a"
        self.write_config(access_policy=self.policy(queue_open=True, identities=[self.identity(worker, PRIMARY_ROLE)]))
        self.write_admission(worker, PRIMARY_ROLE)
        self.set_process_identity(worker, worker)
        self.work_items_path.write_text(
            self.work_items_path.read_text(encoding="utf-8").replace("| W001 |", "| ../escape |"),
            encoding="utf-8",
        )
        config = load_runtime_config(str(self.config_path))

        with self.assertRaisesRegex(ValueError, "single safe path segment"):
            claim_next(config, worker)

        self.assertFalse((self.root / "escape.json").exists())
        self.assertFalse(list(config.claims_dir.glob("*.json")))

    def test_coordinator_refresh_preserves_other_admitted_workers_claims(self) -> None:
        worker_a = "root-a"
        worker_b = "root-b"
        coordinator = "coord-a"
        identities = [
            self.identity(worker_a, PRIMARY_ROLE),
            self.identity(worker_b, PRIMARY_ROLE),
            self.identity(coordinator, COORDINATOR_ROLE),
        ]
        self.write_config(access_policy=self.policy(queue_open=True, identities=identities))
        self.write_admission(worker_a, PRIMARY_ROLE)
        self.write_admission(worker_b, PRIMARY_ROLE)
        self.write_admission(coordinator, COORDINATOR_ROLE)
        config = load_runtime_config(str(self.config_path))

        self.set_process_identity(worker_a, worker_a)
        claim_next(config, worker_a, item_id="W001")
        self.set_process_identity(worker_b, worker_b)
        claim_next(config, worker_b, item_id="W002")
        self.set_process_identity(coordinator, coordinator)
        refreshed = refresh_work_items(config, coordinator)

        self.assertEqual(2, refreshed["active_count"])
        self.assertTrue((config.claims_dir / "W001.json").exists())
        self.assertTrue((config.claims_dir / "W002.json").exists())

    def test_refresh_cannot_promote_display_terminal_without_verified_receipt(self) -> None:
        worker = "root-a"
        coordinator = "coord-a"
        identities = [
            self.identity(worker, PRIMARY_ROLE),
            self.identity(coordinator, COORDINATOR_ROLE),
        ]
        self.write_config(access_policy=self.policy(queue_open=True, identities=identities))
        self.write_admission(worker, PRIMARY_ROLE)
        self.write_admission(coordinator, COORDINATOR_ROLE)
        config = load_runtime_config(str(self.config_path))

        self.set_process_identity(worker, worker)
        claim_next(config, worker, item_id="W001")
        drifted = self.work_items_path.read_text(encoding="utf-8").replace(
            "| W001 | 🔄 |",
            "| W001 | ✅ |",
        )
        self.work_items_path.write_text(drifted, encoding="utf-8")

        self.set_process_identity(coordinator, coordinator)
        refreshed = refresh_work_items(config, coordinator)
        self.assertTrue(refreshed["changed"])
        self.assertTrue((config.claims_dir / "W001.json").exists())
        self.assertIn("| W001 | 🔄 |", self.work_items_path.read_text(encoding="utf-8"))

        (config.claims_dir / "W001.json").unlink()
        no_claim_terminal = self.work_items_path.read_text(encoding="utf-8").replace(
            "| W001 | 🔄 |",
            "| W001 | ✅ |",
        )
        self.work_items_path.write_text(no_claim_terminal, encoding="utf-8")
        reopened = refresh_work_items(config, coordinator)
        self.assertTrue(reopened["changed"])
        final_table = self.work_items_path.read_text(encoding="utf-8")
        self.assertIn("| W001 | ⬜ |", final_table)
        self.assertIn("终态缺少有效完成回执", final_table)

    def test_claim_binds_admission_and_heartbeat_revalidates_it(self) -> None:
        worker = "root-a"
        self.write_config(access_policy=self.policy(queue_open=True, identities=[self.identity(worker, PRIMARY_ROLE)]))
        admission_path = self.write_admission(worker, PRIMARY_ROLE)
        self.set_process_identity(worker, worker)
        config = load_runtime_config(str(self.config_path))
        claimed = claim_next(config, worker, item_id="W001")
        claim_path = Path(claimed["claim_file"])
        claim_before = json.loads(claim_path.read_text(encoding="utf-8"))
        self.assertEqual(2, claim_before["schema_version"])
        self.assertEqual(claimed["claim_access_admission"], claim_before["access_admission"])

        self.write_admission(worker, PRIMARY_ROLE, admitted_at="2026-08-19T00:01:00+08:00")
        with self.assertRaisesRegex(ValueError, "No active claim"):
            heartbeat_claim(config, worker, item_id="W001")
        claim_after = json.loads(claim_path.read_text(encoding="utf-8"))
        self.assertEqual(claim_before["heartbeat_at"], claim_after["heartbeat_at"])
        self.assertTrue(admission_path.exists())

    def test_terminal_complete_requires_valid_json_receipt(self) -> None:
        worker = "root-a"
        self.write_config(access_policy=self.policy(queue_open=True, identities=[self.identity(worker, PRIMARY_ROLE)]))
        self.write_admission(worker, PRIMARY_ROLE)
        self.set_process_identity(worker, worker)
        config = load_runtime_config(str(self.config_path))
        claim_next(config, worker, item_id="W001")
        before_table = self.work_items_path.read_bytes()

        with self.assertRaisesRegex(PermissionError, "Completion receipt is required"):
            complete_claim(config, worker, status="done", item_id="W001")
        self.assertEqual(before_table, self.work_items_path.read_bytes())
        self.assertTrue((config.claims_dir / "W001.json").exists())

        receipt_path = self.write_receipt(worker, "W001", "done")
        forged_credential = json.loads(receipt_path.read_text(encoding="utf-8"))
        forged_credential["access_admission"]["role"] = COORDINATOR_ROLE
        forged_credential["receipt_digest"] = canonical_record_digest(
            forged_credential,
            (
                "task",
                "worker",
                "work_item_id",
                "status",
                "admission_digest",
                "access_admission",
                "verdict",
                "lineage_digest",
                "dossier_digest",
            ),
        )
        receipt_path.write_text(
            json.dumps(forged_credential, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(PermissionError, "admission binding does not match"):
            complete_claim(config, worker, status="done", item_id="W001")
        self.assertEqual(before_table, self.work_items_path.read_bytes())
        self.assertTrue((config.claims_dir / "W001.json").exists())

        receipt_path = self.write_receipt(worker, "W001", "done")
        invalid_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        invalid_receipt["receipt_digest"] = "sha256:invalid"
        receipt_path.write_text(
            json.dumps(invalid_receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(PermissionError, "receipt digest mismatch"):
            complete_claim(config, worker, status="done", item_id="W001")
        self.assertEqual(before_table, self.work_items_path.read_bytes())
        self.assertTrue((config.claims_dir / "W001.json").exists())

        receipt_path = self.write_receipt(worker, "W001", "done")
        wrong_binding = json.loads(receipt_path.read_text(encoding="utf-8"))
        wrong_binding["admission_digest"] = "sha256:" + "9" * 64
        wrong_binding["receipt_digest"] = canonical_record_digest(
            wrong_binding,
            (
                "task",
                "worker",
                "work_item_id",
                "status",
                "admission_digest",
                "access_admission",
                "verdict",
                "lineage_digest",
                "dossier_digest",
            ),
        )
        receipt_path.write_text(
            json.dumps(wrong_binding, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(PermissionError, "claim admission digest"):
            complete_claim(config, worker, status="done", item_id="W001")
        self.assertEqual(before_table, self.work_items_path.read_bytes())
        self.assertTrue((config.claims_dir / "W001.json").exists())

        self.write_receipt(worker, "W001", "done")
        (self.root / "dossiers" / "W001.json").unlink()
        with self.assertRaisesRegex(PermissionError, "Completion artifact is required"):
            complete_claim(config, worker, status="done", item_id="W001")
        self.assertEqual(before_table, self.work_items_path.read_bytes())
        self.assertTrue((config.claims_dir / "W001.json").exists())

        receipt_path = self.write_receipt(worker, "W001", "done")
        completed = complete_claim(config, worker, status="done", item_id="W001")
        self.assertTrue(completed["completion_receipt_verified"])
        self.assertEqual(2, len(completed["completion_artifacts_verified"]))
        self.assertEqual(str(receipt_path), completed["completion_receipt"])
        self.assertFalse((config.claims_dir / "W001.json").exists())

    def test_completion_write_failure_preserves_claim_and_active_table(self) -> None:
        worker = "root-a"
        self.write_config(access_policy=self.policy(queue_open=True, identities=[self.identity(worker, PRIMARY_ROLE)]))
        self.write_admission(worker, PRIMARY_ROLE)
        self.set_process_identity(worker, worker)
        config = load_runtime_config(str(self.config_path))
        claim_next(config, worker, item_id="W001")
        self.write_receipt(worker, "W001", "done")

        with mock.patch.object(lease_lib, "write_markdown_table", side_effect=OSError("injected table failure")):
            with self.assertRaisesRegex(OSError, "injected table failure"):
                complete_claim(config, worker, status="done", item_id="W001")

        self.assertTrue((config.claims_dir / "W001.json").exists())
        self.assertIn("| W001 | 🔄 |", self.work_items_path.read_text(encoding="utf-8"))

    def test_late_heartbeat_cannot_resurrect_completed_claim(self) -> None:
        worker = "root-a"
        self.write_config(access_policy=self.policy(queue_open=True, identities=[self.identity(worker, PRIMARY_ROLE)]))
        self.write_admission(worker, PRIMARY_ROLE)
        self.set_process_identity(worker, worker)
        config = load_runtime_config(str(self.config_path))
        claim_next(config, worker, item_id="W001")
        self.write_receipt(worker, "W001", "done")

        complete_at_table_write = threading.Event()
        allow_complete = threading.Event()
        heartbeat_before_claim_write = threading.Event()
        allow_heartbeat_write = threading.Event()
        complete_result: dict[str, object] = {}
        heartbeat_result: dict[str, object] = {}
        original_table_write = lease_lib.write_markdown_table
        original_claim_write = lease_lib.write_claim

        def gated_table_write(path: Path, table: object) -> None:
            if threading.current_thread().name == "complete-worker":
                complete_at_table_write.set()
                if not allow_complete.wait(timeout=5):
                    raise TimeoutError("test timed out while holding the completion write lock")
            original_table_write(path, table)

        def gated_claim_write(path: Path, payload: dict[str, object]) -> None:
            if threading.current_thread().name == "late-heartbeat":
                heartbeat_before_claim_write.set()
                if not allow_heartbeat_write.wait(timeout=5):
                    raise TimeoutError("test timed out before the late heartbeat claim write")
            original_claim_write(path, payload)

        def run_complete() -> None:
            try:
                complete_result["value"] = complete_claim(config, worker, status="done", item_id="W001")
            except Exception as exc:  # pragma: no cover - asserted below
                complete_result["error"] = exc

        def run_heartbeat() -> None:
            try:
                heartbeat_result["value"] = heartbeat_claim(config, worker, item_id="W001")
            except Exception as exc:
                heartbeat_result["error"] = exc

        complete_thread = threading.Thread(target=run_complete, name="complete-worker")
        heartbeat_thread = threading.Thread(target=run_heartbeat, name="late-heartbeat")
        with (
            mock.patch.object(lease_lib, "write_markdown_table", side_effect=gated_table_write),
            mock.patch.object(lease_lib, "write_claim", side_effect=gated_claim_write),
        ):
            complete_thread.start()
            self.assertTrue(complete_at_table_write.wait(timeout=2))
            heartbeat_thread.start()
            wrote_before_completion_released = heartbeat_before_claim_write.wait(timeout=0.5)
            allow_complete.set()
            complete_thread.join(timeout=5)
            allow_heartbeat_write.set()
            heartbeat_thread.join(timeout=5)

        self.assertFalse(complete_thread.is_alive())
        self.assertFalse(heartbeat_thread.is_alive())
        self.assertFalse(wrote_before_completion_released)
        self.assertNotIn("error", complete_result)
        self.assertEqual("completed:done", complete_result["value"]["mode"])
        self.assertIsInstance(heartbeat_result.get("error"), ValueError)
        self.assertIn("No active claim", str(heartbeat_result["error"]))
        self.assertFalse((config.claims_dir / "W001.json").exists())
        self.assertIn("| W001 | ✅ |", self.work_items_path.read_text(encoding="utf-8"))

    def test_verified_terminal_survives_worker_admission_cleanup(self) -> None:
        worker = "root-a"
        coordinator = "coord-a"
        identities = [self.identity(worker, PRIMARY_ROLE), self.identity(coordinator, COORDINATOR_ROLE)]
        self.write_config(access_policy=self.policy(queue_open=True, identities=identities))
        worker_admission = self.write_admission(worker, PRIMARY_ROLE)
        self.write_admission(coordinator, COORDINATOR_ROLE)
        self.set_process_identity(worker, worker)
        config = load_runtime_config(str(self.config_path))
        claim_next(config, worker, item_id="W001")
        self.write_receipt(worker, "W001", "done")
        complete_claim(config, worker, status="done", item_id="W001")
        worker_admission.unlink()

        self.set_process_identity(coordinator, coordinator)
        refreshed = refresh_work_items(config, coordinator)

        self.assertFalse(refreshed["changed"])
        self.assertIn("| W001 | ✅ |", self.work_items_path.read_text(encoding="utf-8"))

    def test_registered_namespace_rejects_shadow_and_legacy_configs(self) -> None:
        worker = "root-a"
        self.write_config(access_policy=self.policy(queue_open=False, identities=[self.identity(worker, PRIMARY_ROLE)]))
        (self.root / ".git").mkdir()
        registry_path = self.root / ".agents" / "work_item_lease_namespaces.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "namespaces": [
                        {
                            "task": "TEST/S009",
                            "canonical_config": "work_item_runtime.json",
                            "work_items": "work_items.md",
                            "claims_dir": "claims",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        canonical = load_runtime_config(str(self.config_path))
        self.assertEqual(self.config_path, canonical.config_path)

        canonical_data = json.loads(self.config_path.read_text(encoding="utf-8"))
        shadow_path = self.root / "shadow.json"
        shadow_path.write_text(json.dumps(canonical_data, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "registered canonical config"):
            load_runtime_config(str(shadow_path))

        legacy_shadow = dict(canonical_data)
        legacy_shadow.pop("access_policy")
        shadow_path.write_text(json.dumps(legacy_shadow, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "registered canonical config"):
            load_runtime_config(str(shadow_path))

        alternate_items = self.root / "shadow-items.md"
        alternate_items.write_bytes(self.work_items_path.read_bytes())
        legacy_shadow["work_items_path"] = "shadow-items.md"
        shadow_path.write_text(json.dumps(legacy_shadow, indent=2) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(PermissionError, "registered canonical config"):
            load_runtime_config(str(shadow_path))

    def test_closed_queue_still_allows_reentry_heartbeat_and_completion(self) -> None:
        worker = "root-a"
        open_policy = self.policy(queue_open=True, identities=[self.identity(worker, PRIMARY_ROLE)])
        self.write_config(access_policy=open_policy)
        self.write_admission(worker, PRIMARY_ROLE)
        self.set_process_identity(worker, worker)
        open_config = load_runtime_config(str(self.config_path))
        claim_next(open_config, worker, item_id="W001")

        closed_policy = self.policy(queue_open=False, identities=[self.identity(worker, PRIMARY_ROLE)])
        self.write_config(access_policy=closed_policy)
        closed_config = load_runtime_config(str(self.config_path))
        self.assertEqual("reentered", claim_next(closed_config, worker, item_id="W001")["mode"])
        self.assertEqual("heartbeat", heartbeat_claim(closed_config, worker, item_id="W001")["mode"])
        self.write_receipt(worker, "W001", "done")
        self.assertEqual(
            "completed:done",
            complete_claim(closed_config, worker, status="done", item_id="W001")["mode"],
        )

    def test_current_environment_blocks_parent_worker_spoof_and_missing_identity(self) -> None:
        parent = "root-parent"
        self.write_config(access_policy=self.policy(queue_open=True, identities=[self.identity(parent, PRIMARY_ROLE)]))
        self.write_admission(parent, PRIMARY_ROLE)
        config = load_runtime_config(str(self.config_path))

        self.set_process_identity(parent, "child-thread")
        with self.assertRaisesRegex(PermissionError, "environment binding mismatch"):
            claim_next(config, parent)
        self.set_process_identity(None, None)
        with self.assertRaisesRegex(PermissionError, "requires non-empty environment variable"):
            claim_next(config, parent)
        self.set_process_identity(parent, parent)
        self.assertEqual("claimed", claim_next(config, parent, item_id="W001")["mode"])

    def test_direct_shared_cli_rejects_child_environment_with_parent_worker(self) -> None:
        parent = "root-parent"
        self.write_config(access_policy=self.policy(queue_open=True, identities=[self.identity(parent, PRIMARY_ROLE)]))
        self.write_admission(parent, PRIMARY_ROLE)
        env = os.environ.copy()
        env["TEST_SESSION_ID"] = parent
        env["TEST_THREAD_ID"] = "child-thread"
        cli = Path(__file__).with_name("claim_next_work_item.py")

        result = subprocess.run(
            [sys.executable, str(cli), "--config", str(self.config_path), "--worker", parent],
            cwd=str(self.root),
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(0, result.returncode)
        self.assertIn("environment binding mismatch", result.stderr)
        self.assertFalse(list((self.root / "claims").glob("*.json")))

    def test_configuration_without_access_policy_keeps_legacy_multi_claim_behavior(self) -> None:
        worker = "legacy-worker"
        self.write_config(access_policy=None)
        config = load_runtime_config(str(self.config_path))
        first = claim_next(config, worker, item_id="W001")
        second = claim_next(config, worker, item_id="W002")
        self.assertEqual("claimed", first["mode"])
        self.assertEqual("claimed", second["mode"])
        self.assertNotIn("access_admission", json.loads(Path(first["claim_file"]).read_text(encoding="utf-8")))
        self.assertEqual("heartbeat", heartbeat_claim(config, worker, item_id="W001")["mode"])
        self.assertEqual(
            "completed:done",
            complete_claim(config, worker, status="done", item_id="W001")["mode"],
        )


if __name__ == "__main__":
    unittest.main()
