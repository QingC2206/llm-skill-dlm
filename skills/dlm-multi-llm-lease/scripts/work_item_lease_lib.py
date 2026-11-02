#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


EMPTY = "—"
STATE_OPEN = "⬜"
STATE_ACTIVE = "🔄"
STATE_DONE = "✅"
STATE_BLOCKED = "❌"
TERMINAL_STATES = {STATE_DONE, STATE_BLOCKED}

_OPEN_STATE_ALIASES = frozenset({STATE_OPEN, "open", "pending", "todo", "待领取", "", EMPTY})
_ACTIVE_STATE_ALIASES = frozenset({STATE_ACTIVE, "active", "in_progress", "running", "进行中", "已领取"})
_DONE_STATE_ALIASES = frozenset({STATE_DONE, "done", "completed", "pass", "已完成"})
_BLOCKED_STATE_ALIASES = frozenset({STATE_BLOCKED, "⛔", "blocked", "failed", "已阻塞"})


def normalize_state(raw_state: str | None) -> str:
    text = (raw_state or "").strip()
    lowered = text.lower()
    if text in _DONE_STATE_ALIASES or lowered in _DONE_STATE_ALIASES:
        return STATE_DONE
    if text in _BLOCKED_STATE_ALIASES or lowered in _BLOCKED_STATE_ALIASES:
        return STATE_BLOCKED
    if text in _ACTIVE_STATE_ALIASES or lowered in _ACTIVE_STATE_ALIASES:
        return STATE_ACTIVE
    if text in _OPEN_STATE_ALIASES or lowered in _OPEN_STATE_ALIASES:
        return STATE_OPEN
    return text


def state_to_status_code(raw_state: str | None) -> str:
    norm = normalize_state(raw_state)
    if norm == STATE_DONE:
        return "done"
    if norm == STATE_BLOCKED:
        return "blocked"
    if norm == STATE_ACTIVE:
        return "active"
    return "open"

LEGACY_DEFAULT_WORKER_ENV = "CODEX_THREAD_ID"
ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
STRICT_ORCHESTRATION_PROFILE = "user_started_top_level_tasks"
STRICT_PARALLELISM_AUTHORITY = "user"
STRICT_PEER_AWARENESS = "self_only"
STRICT_IDENTITY_SOURCE = "host_task_environment"
STRICT_ASSIGNMENT_MODE = "next_available_only"
STRICT_USER_AUTHORIZED_ASSIGNMENT_MODE = "next_available_or_user_authorized"
STRICT_WORKER_LOOP = "serial_claim_complete_repeat"
STRICT_CLOSURE_MODE = "followup_subtask_only"


@dataclass(frozen=True)
class ColumnMap:
    item_id: str
    state: str
    worker: str
    started_at: str
    completed_at: str
    note_candidates: tuple[str, ...]
    remark_candidates: tuple[str, ...] = ("备注", "任务备注", "说明")
    module_candidates: tuple[str, ...] = ("模块", "对象", "目标模块")
    priority_candidates: tuple[str, ...] = ("优先级", "批次", "系列")

    @property
    def required_headers(self) -> tuple[str, ...]:
        return (
            self.item_id,
            self.state,
            self.worker,
            self.started_at,
            self.completed_at,
        )


@dataclass(frozen=True)
class NoteMessages:
    lease_expired: str
    claim_missing: str
    released: str
    terminal_unverified: str


@dataclass(frozen=True)
class AdmissionPolicy:
    directory: Path
    file_template: str
    worker_field: str
    role_field: str
    digest_field: str
    digest_fields: tuple[str, ...]
    identity_fields: tuple[str, ...]
    environment_bindings: dict[str, str]
    require_equal_fields: tuple[tuple[str, ...], ...]
    expected_fields: dict[str, Any]
    allowed_identities: tuple[dict[str, Any], ...]
    allowed_roles: frozenset[str]


@dataclass(frozen=True)
class ReceiptArtifactDigestPolicy:
    receipt_field: str
    path_template: str


@dataclass(frozen=True)
class ReceiptJsonDigestPolicy:
    receipt_field: str
    path_template: str
    json_pointer: str


@dataclass(frozen=True)
class ReceiptFieldConstraint:
    allowed_values: tuple[Any, ...] | None
    pattern: str | None


@dataclass(frozen=True)
class CompletionReceiptPolicy:
    required_statuses: frozenset[str]
    path_template: str
    worker_field: str
    item_field: str
    status_field: str
    digest_field: str
    digest_fields: tuple[str, ...]
    expected_fields: dict[str, Any]
    claim_admission_digest_field: str | None
    claim_admission_binding_field: str | None
    artifact_digest_bindings: tuple[ReceiptArtifactDigestPolicy, ...]
    json_digest_bindings: tuple[ReceiptJsonDigestPolicy, ...]
    field_constraints: dict[str, ReceiptFieldConstraint]


@dataclass(frozen=True)
class AccessPolicy:
    schema_version: int
    queue_open: bool
    admission: AdmissionPolicy
    action_roles: dict[str, frozenset[str]]
    max_active_claims_per_worker: int
    completion_receipt: CompletionReceiptPolicy | None


@dataclass(frozen=True)
class OrchestrationPolicy:
    schema_version: int
    profile: str
    parallelism_authority: str
    peer_awareness: str
    identity_source: str
    worker_env_var: str
    session_env_var: str
    require_equal_identity: bool
    assignment_mode: str
    user_authorized_item_ids: tuple[str, ...]
    user_authorization_ref: str | None
    max_active_claims_per_worker: int
    worker_loop: str
    closure_mode: str


@dataclass
class RuntimeConfig:
    config_path: Path
    base_dir: Path
    epic_id: str
    label: str
    work_items_path: Path
    claims_dir: Path
    lease_seconds: int
    lock_timeout_seconds: int
    lock_stale_seconds: int
    worker_env_var: str | None
    columns: ColumnMap
    note_messages: NoteMessages
    access_policy: AccessPolicy | None = None
    orchestration_policy: OrchestrationPolicy | None = None
    sop_ref: str | None = None
    priority_order: tuple[str, ...] = ()

    @property
    def write_lock_path(self) -> Path:
        return self.claims_dir / ".work_items.write.lock"


@dataclass
class MarkdownTable:
    intro_lines: list[str]
    headers: list[str]
    separator_line: str
    rows: list[list[str]]
    tail_lines: list[str]
    header_index: dict[str, int]
    id_column: str
    state_column: str
    worker_column: str
    started_at_column: str
    completed_at_column: str
    note_column: str
    remark_column: str | None = None
    module_column: str | None = None
    priority_column: str | None = None

    def row_as_dict(self, row: list[str]) -> dict[str, str]:
        return {header: row[index] for header, index in self.header_index.items()}

    def get_row(self, item_id: str) -> list[str] | None:
        id_index = self.header_index[self.id_column]
        for row in self.rows:
            if row[id_index] == item_id:
                return row
        return None

    def resolve_item_id(self, item_id: str | None = None, module: str | None = None) -> str | None:
        raw_id = normalize_optional_text(item_id)
        raw_mod = normalize_optional_text(module)
        if raw_id is None and raw_mod is None:
            return None
        id_index = self.header_index[self.id_column]
        if raw_mod is not None:
            if self.module_column is None:
                raise ValueError(f"Table does not have a module column; cannot resolve module '{raw_mod}'.")
            mod_index = self.header_index[self.module_column]
            matches = [row for row in self.rows if row[mod_index].strip() == raw_mod]
            if not matches:
                raise ValueError(f"Module '{raw_mod}' was not found in work_items.md.")
            if raw_id is not None:
                matches = [row for row in matches if row[id_index].strip() == raw_id]
                if not matches:
                    raise ValueError(f"Work item '{raw_id}' does not match module '{raw_mod}' in work_items.md.")
            if len(matches) > 1:
                matched_ids = [row[id_index] for row in matches]
                raise ValueError(f"Module '{raw_mod}' matches multiple work items ({matched_ids}); specify --item-id.")
            return matches[0][id_index]

        assert raw_id is not None
        if self.get_row(raw_id) is not None:
            return raw_id
        if self.module_column is not None:
            mod_index = self.header_index[self.module_column]
            matches = [row for row in self.rows if row[mod_index].strip() == raw_id]
            if len(matches) == 1:
                return matches[0][id_index]
            if len(matches) > 1:
                matched_ids = [row[id_index] for row in matches]
                raise ValueError(f"Target '{raw_id}' matches multiple modules ({matched_ids}); specify exact --item-id.")
        return raw_id


def now_local() -> datetime:
    return datetime.now().astimezone()


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def ensure_text(value: Any, *, field_name: str) -> str:
    text = normalize_optional_text(value)
    if text is None:
        raise ValueError(f"Missing required text field '{field_name}'.")
    return text


def validate_item_id(value: Any) -> str:
    item_id = ensure_text(value, field_name="work_item_id")
    if ITEM_ID_PATTERN.fullmatch(item_id) is None:
        raise ValueError(
            "Work item IDs must be a single safe path segment matching "
            "[A-Za-z0-9][A-Za-z0-9._-]{0,127}."
        )
    return item_id


def _load_candidate_tuple(
    raw: dict[str, Any],
    key: str,
    defaults: tuple[str, ...],
) -> tuple[str, ...]:
    value = raw.get(key)
    if value is None:
        return defaults
    if isinstance(value, str):
        return (ensure_text(value, field_name=f"column_map.{key}"),)
    if isinstance(value, list):
        candidates = tuple(
            ensure_text(item, field_name=f"column_map.{key}[]")
            for item in value
            if normalize_optional_text(item) is not None
        )
        if not candidates:
            raise ValueError(f"'column_map.{key}' list must contain at least one non-empty value.")
        return candidates
    raise ValueError(f"'column_map.{key}' must be a string or a list of strings.")


def load_column_map(data: dict[str, Any]) -> ColumnMap:
    raw = data.get("column_map") or {}
    if not isinstance(raw, dict):
        raise ValueError("'column_map' must be an object when provided.")

    note_value = raw.get("note", raw.get("note_candidates"))
    if note_value is None:
        note_candidates = ("运行备注", "备注")
    elif isinstance(note_value, str):
        note_candidates = (ensure_text(note_value, field_name="column_map.note"),)
    elif isinstance(note_value, list):
        note_candidates = tuple(
            ensure_text(item, field_name="column_map.note[]")
            for item in note_value
            if normalize_optional_text(item) is not None
        )
        if not note_candidates:
            raise ValueError("'column_map.note' list must contain at least one non-empty value.")
    else:
        raise ValueError("'column_map.note' must be a string or a list of strings.")

    return ColumnMap(
        item_id=ensure_text(raw.get("id", "编号"), field_name="column_map.id"),
        state=ensure_text(raw.get("state", "状态"), field_name="column_map.state"),
        worker=ensure_text(raw.get("worker", "执行者"), field_name="column_map.worker"),
        started_at=ensure_text(raw.get("started_at", "开始时间"), field_name="column_map.started_at"),
        completed_at=ensure_text(raw.get("completed_at", "完成时间"), field_name="column_map.completed_at"),
        note_candidates=note_candidates,
        remark_candidates=_load_candidate_tuple(raw, "remark", ("备注", "任务备注", "说明")),
        module_candidates=_load_candidate_tuple(raw, "module", ("模块", "对象", "目标模块")),
        priority_candidates=_load_candidate_tuple(raw, "priority", ("优先级", "批次", "系列")),
    )


def load_note_messages(data: dict[str, Any]) -> NoteMessages:
    raw = data.get("note_messages") or data.get("messages") or {}
    if not isinstance(raw, dict):
        raise ValueError("'note_messages' must be an object when provided.")

    return NoteMessages(
        lease_expired=ensure_text(
            raw.get("lease_expired", "租约过期回收，待重新领取。"),
            field_name="note_messages.lease_expired",
        ),
        claim_missing=ensure_text(
            raw.get("claim_missing", "锁文件缺失，已回收。"),
            field_name="note_messages.claim_missing",
        ),
        released=ensure_text(
            raw.get("released", "已释放，待重新领取。"),
            field_name="note_messages.released",
        ),
        terminal_unverified=ensure_text(
            raw.get("terminal_unverified", "终态缺少有效完成回执，已退回待领取。"),
            field_name="note_messages.terminal_unverified",
        ),
    )


def load_text_tuple(value: Any, *, field_name: str, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"'{field_name}' must be a list of strings.")
    result = tuple(ensure_text(item, field_name=f"{field_name}[]") for item in value)
    if not allow_empty and not result:
        raise ValueError(f"'{field_name}' must not be empty.")
    if len(set(result)) != len(result):
        raise ValueError(f"'{field_name}' must not contain duplicate values.")
    return result


def require_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"'{field_name}' must be an object.")
    return value


def load_access_policy(data: dict[str, Any], base_dir: Path) -> AccessPolicy | None:
    raw_policy = data.get("access_policy")
    if raw_policy is None:
        return None
    raw_policy = require_object(raw_policy, field_name="access_policy")
    schema_version = int(raw_policy.get("schema_version", 1))
    if schema_version != 1:
        raise ValueError("Unsupported 'access_policy.schema_version'; expected 1.")

    raw_admission = require_object(raw_policy.get("admission"), field_name="access_policy.admission")
    digest_fields = load_text_tuple(
        raw_admission.get("digest_fields"),
        field_name="access_policy.admission.digest_fields",
    )
    identity_fields = load_text_tuple(
        raw_admission.get("identity_fields"),
        field_name="access_policy.admission.identity_fields",
    )
    raw_environment_bindings = require_object(
        raw_admission.get("environment_bindings"),
        field_name="access_policy.admission.environment_bindings",
    )
    environment_bindings = {
        ensure_text(field, field_name="access_policy.admission.environment_bindings key"): ensure_text(
            env_var,
            field_name=f"access_policy.admission.environment_bindings.{field}",
        )
        for field, env_var in raw_environment_bindings.items()
    }
    if not environment_bindings:
        raise ValueError("'access_policy.admission.environment_bindings' must not be empty.")
    unknown_environment_fields = sorted(set(environment_bindings).difference(identity_fields))
    if unknown_environment_fields:
        raise ValueError(
            "'access_policy.admission.environment_bindings' references fields outside identity_fields: "
            f"{unknown_environment_fields}"
        )
    raw_equal_groups = raw_admission.get("require_equal_fields", [])
    if not isinstance(raw_equal_groups, list):
        raise ValueError("'access_policy.admission.require_equal_fields' must be a list of field lists.")
    require_equal_fields: list[tuple[str, ...]] = []
    for index, raw_group in enumerate(raw_equal_groups):
        group = load_text_tuple(
            raw_group,
            field_name=f"access_policy.admission.require_equal_fields[{index}]",
        )
        if len(group) < 2:
            raise ValueError(
                f"'access_policy.admission.require_equal_fields[{index}]' must contain at least two fields."
            )
        unknown_equal_fields = sorted(set(group).difference(identity_fields))
        if unknown_equal_fields:
            raise ValueError(
                f"Equal-field group {index} references fields outside identity_fields: {unknown_equal_fields}"
            )
        require_equal_fields.append(group)
    expected_fields = require_object(
        raw_admission.get("expected_fields", {}),
        field_name="access_policy.admission.expected_fields",
    )
    worker_field = ensure_text(
        raw_admission.get("worker_field"),
        field_name="access_policy.admission.worker_field",
    )
    if worker_field not in environment_bindings:
        raise ValueError(
            "'access_policy.admission.environment_bindings' must bind worker_field to the current process."
        )
    role_field = ensure_text(
        raw_admission.get("role_field", "role"),
        field_name="access_policy.admission.role_field",
    )
    digest_field = ensure_text(
        raw_admission.get("digest_field", "admission_digest"),
        field_name="access_policy.admission.digest_field",
    )
    protected_fields = {worker_field, role_field, *identity_fields, *expected_fields}
    missing_digest_fields = sorted(protected_fields.difference(digest_fields))
    if missing_digest_fields:
        raise ValueError(
            "'access_policy.admission.digest_fields' must cover worker, role, identity, and expected fields: "
            f"{missing_digest_fields}"
        )

    allowed_roles = frozenset(
        load_text_tuple(
            raw_admission.get("allowed_roles"),
            field_name="access_policy.admission.allowed_roles",
        )
    )
    raw_allowed_identities = raw_admission.get("allowed_identities")
    if not isinstance(raw_allowed_identities, list):
        raise ValueError("'access_policy.admission.allowed_identities' must be a list.")
    allowed_identities: list[dict[str, Any]] = []
    for index, raw_identity in enumerate(raw_allowed_identities):
        identity = require_object(
            raw_identity,
            field_name=f"access_policy.admission.allowed_identities[{index}]",
        )
        missing_identity_fields = sorted(set(identity_fields).difference(identity))
        if missing_identity_fields:
            raise ValueError(
                f"Allowed identity {index} is missing identity fields: {missing_identity_fields}"
            )
        unknown_identity_fields = sorted(set(identity).difference({*identity_fields, "roles"}))
        if unknown_identity_fields:
            raise ValueError(
                f"Allowed identity {index} contains unsupported fields: {unknown_identity_fields}"
            )
        identity_roles = frozenset(
            load_text_tuple(
                identity.get("roles"),
                field_name=f"access_policy.admission.allowed_identities[{index}].roles",
            )
        )
        unknown_roles = sorted(identity_roles.difference(allowed_roles))
        if unknown_roles:
            raise ValueError(f"Allowed identity {index} references unknown roles: {unknown_roles}")
        allowed_identities.append({**identity, "roles": identity_roles})

    file_template = ensure_text(
        raw_admission.get("file_template", "{worker}.json"),
        field_name="access_policy.admission.file_template",
    )
    if "{worker}" not in file_template:
        raise ValueError("'access_policy.admission.file_template' must contain '{worker}'.")
    admission_dir_raw = ensure_text(
        raw_admission.get("dir"),
        field_name="access_policy.admission.dir",
    )
    admission = AdmissionPolicy(
        directory=resolve_runtime_path(base_dir, admission_dir_raw),
        file_template=file_template,
        worker_field=worker_field,
        role_field=role_field,
        digest_field=digest_field,
        digest_fields=digest_fields,
        identity_fields=identity_fields,
        environment_bindings=environment_bindings,
        require_equal_fields=tuple(require_equal_fields),
        expected_fields=dict(expected_fields),
        allowed_identities=tuple(allowed_identities),
        allowed_roles=allowed_roles,
    )

    raw_action_roles = require_object(
        raw_policy.get("action_roles"),
        field_name="access_policy.action_roles",
    )
    action_roles: dict[str, frozenset[str]] = {}
    for action in ("claim", "heartbeat", "complete", "refresh"):
        roles = frozenset(
            load_text_tuple(
                raw_action_roles.get(action),
                field_name=f"access_policy.action_roles.{action}",
            )
        )
        unknown_roles = sorted(roles.difference(allowed_roles))
        if unknown_roles:
            raise ValueError(f"Action '{action}' references unknown admission roles: {unknown_roles}")
        action_roles[action] = roles

    max_active_claims = int(raw_policy.get("max_active_claims_per_worker", 1))
    if max_active_claims < 1:
        raise ValueError("'access_policy.max_active_claims_per_worker' must be at least 1.")

    receipt_policy = None
    raw_receipt = raw_policy.get("completion_receipt")
    if raw_receipt is not None:
        raw_receipt = require_object(raw_receipt, field_name="access_policy.completion_receipt")
        required_statuses = frozenset(
            load_text_tuple(
                raw_receipt.get("required_statuses", ["done", "blocked"]),
                field_name="access_policy.completion_receipt.required_statuses",
                allow_empty=True,
            )
        )
        unsupported_statuses = sorted(required_statuses.difference({"done", "blocked"}))
        if unsupported_statuses:
            raise ValueError(
                "'access_policy.completion_receipt.required_statuses' only supports terminal statuses "
                f"'done' and 'blocked': {unsupported_statuses}"
            )
        receipt_digest_fields = load_text_tuple(
            raw_receipt.get("digest_fields"),
            field_name="access_policy.completion_receipt.digest_fields",
        )
        receipt_worker_field = ensure_text(
            raw_receipt.get("worker_field", "worker"),
            field_name="access_policy.completion_receipt.worker_field",
        )
        receipt_item_field = ensure_text(
            raw_receipt.get("item_field", "work_item_id"),
            field_name="access_policy.completion_receipt.item_field",
        )
        receipt_status_field = ensure_text(
            raw_receipt.get("status_field", "status"),
            field_name="access_policy.completion_receipt.status_field",
        )
        receipt_expected_fields = require_object(
            raw_receipt.get("expected_fields", {}),
            field_name="access_policy.completion_receipt.expected_fields",
        )
        claim_admission_digest_field = normalize_optional_text(
            raw_receipt.get("claim_admission_digest_field")
        )
        claim_admission_binding_field = normalize_optional_text(
            raw_receipt.get("claim_admission_binding_field")
        )
        raw_artifact_bindings = raw_receipt.get("artifact_digest_bindings", [])
        if not isinstance(raw_artifact_bindings, list):
            raise ValueError(
                "'access_policy.completion_receipt.artifact_digest_bindings' must be a list."
            )
        artifact_digest_bindings: list[ReceiptArtifactDigestPolicy] = []
        artifact_receipt_fields: set[str] = set()
        for index, raw_binding in enumerate(raw_artifact_bindings):
            binding = require_object(
                raw_binding,
                field_name=f"access_policy.completion_receipt.artifact_digest_bindings[{index}]",
            )
            receipt_field = ensure_text(
                binding.get("receipt_field"),
                field_name=(
                    f"access_policy.completion_receipt.artifact_digest_bindings[{index}].receipt_field"
                ),
            )
            if receipt_field in artifact_receipt_fields:
                raise ValueError(
                    "'access_policy.completion_receipt.artifact_digest_bindings' must not repeat "
                    f"receipt field '{receipt_field}'."
                )
            artifact_receipt_fields.add(receipt_field)
            path_template = ensure_text(
                binding.get("path_template"),
                field_name=(
                    f"access_policy.completion_receipt.artifact_digest_bindings[{index}].path_template"
                ),
            )
            if "{work_item_id}" not in path_template:
                raise ValueError(
                    "Completion artifact path templates must contain '{work_item_id}'."
                )
            artifact_digest_bindings.append(
                ReceiptArtifactDigestPolicy(
                    receipt_field=receipt_field,
                    path_template=path_template,
                )
            )

        raw_json_bindings = raw_receipt.get("json_digest_bindings", [])
        if not isinstance(raw_json_bindings, list):
            raise ValueError("'access_policy.completion_receipt.json_digest_bindings' must be a list.")
        json_digest_bindings: list[ReceiptJsonDigestPolicy] = []
        json_receipt_fields: set[str] = set()
        for index, raw_binding in enumerate(raw_json_bindings):
            binding = require_object(
                raw_binding,
                field_name=f"access_policy.completion_receipt.json_digest_bindings[{index}]",
            )
            receipt_field = ensure_text(
                binding.get("receipt_field"),
                field_name=f"access_policy.completion_receipt.json_digest_bindings[{index}].receipt_field",
            )
            if receipt_field in json_receipt_fields:
                raise ValueError(
                    "'access_policy.completion_receipt.json_digest_bindings' must not repeat "
                    f"receipt field '{receipt_field}'."
                )
            json_receipt_fields.add(receipt_field)
            path_template = ensure_text(
                binding.get("path_template"),
                field_name=f"access_policy.completion_receipt.json_digest_bindings[{index}].path_template",
            )
            if "{work_item_id}" not in path_template:
                raise ValueError("Completion JSON digest path templates must contain '{work_item_id}'.")
            json_pointer = ensure_text(
                binding.get("json_pointer"),
                field_name=f"access_policy.completion_receipt.json_digest_bindings[{index}].json_pointer",
            )
            if json_pointer != "" and not json_pointer.startswith("/"):
                raise ValueError("Completion JSON digest pointers must be empty or start with '/'.")
            json_digest_bindings.append(
                ReceiptJsonDigestPolicy(
                    receipt_field=receipt_field,
                    path_template=path_template,
                    json_pointer=json_pointer,
                )
            )

        raw_constraints = require_object(
            raw_receipt.get("field_constraints", {}),
            field_name="access_policy.completion_receipt.field_constraints",
        )
        field_constraints: dict[str, ReceiptFieldConstraint] = {}
        for field, raw_constraint in raw_constraints.items():
            field_name = ensure_text(field, field_name="completion receipt constraint field")
            constraint = require_object(
                raw_constraint,
                field_name=f"access_policy.completion_receipt.field_constraints.{field_name}",
            )
            raw_allowed_values = constraint.get("allowed_values")
            allowed_values = None
            if raw_allowed_values is not None:
                if not isinstance(raw_allowed_values, list) or not raw_allowed_values:
                    raise ValueError(
                        f"Completion receipt constraint '{field_name}.allowed_values' must be a non-empty list."
                    )
                if any(isinstance(value, (dict, list)) for value in raw_allowed_values):
                    raise ValueError(
                        f"Completion receipt constraint '{field_name}.allowed_values' only supports JSON scalars."
                    )
                allowed_values = tuple(raw_allowed_values)
            pattern = normalize_optional_text(constraint.get("pattern"))
            if pattern is not None:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(
                        f"Completion receipt constraint '{field_name}.pattern' is invalid."
                    ) from exc
            if allowed_values is None and pattern is None:
                raise ValueError(
                    f"Completion receipt constraint '{field_name}' needs allowed_values or pattern."
                )
            field_constraints[field_name] = ReceiptFieldConstraint(
                allowed_values=allowed_values,
                pattern=pattern,
            )
        receipt_protected_fields = {
            receipt_worker_field,
            receipt_item_field,
            receipt_status_field,
            *receipt_expected_fields,
            *artifact_receipt_fields,
            *json_receipt_fields,
            *field_constraints,
        }
        if claim_admission_digest_field is not None:
            receipt_protected_fields.add(claim_admission_digest_field)
        if claim_admission_binding_field is not None:
            receipt_protected_fields.add(claim_admission_binding_field)
        missing_receipt_digest_fields = sorted(receipt_protected_fields.difference(receipt_digest_fields))
        if missing_receipt_digest_fields:
            raise ValueError(
                "'access_policy.completion_receipt.digest_fields' must cover worker, item, status, expected, "
                "claim-binding, and artifact fields: "
                f"{missing_receipt_digest_fields}"
            )
        receipt_policy = CompletionReceiptPolicy(
            required_statuses=required_statuses,
            path_template=ensure_text(
                raw_receipt.get("path_template"),
                field_name="access_policy.completion_receipt.path_template",
            ),
            worker_field=receipt_worker_field,
            item_field=receipt_item_field,
            status_field=receipt_status_field,
            digest_field=ensure_text(
                raw_receipt.get("digest_field", "receipt_digest"),
                field_name="access_policy.completion_receipt.digest_field",
            ),
            digest_fields=receipt_digest_fields,
            expected_fields=dict(receipt_expected_fields),
            claim_admission_digest_field=claim_admission_digest_field,
            claim_admission_binding_field=claim_admission_binding_field,
            artifact_digest_bindings=tuple(artifact_digest_bindings),
            json_digest_bindings=tuple(json_digest_bindings),
            field_constraints=field_constraints,
        )

    return AccessPolicy(
        schema_version=schema_version,
        queue_open=raw_policy.get("queue_open") is True,
        admission=admission,
        action_roles=action_roles,
        max_active_claims_per_worker=max_active_claims,
        completion_receipt=receipt_policy,
    )


def load_orchestration_policy(
    data: dict[str, Any],
    *,
    worker_env_var: str | None,
    access_policy: AccessPolicy | None,
) -> OrchestrationPolicy | None:
    raw = data.get("orchestration_policy")
    if raw is None:
        return None
    raw = require_object(raw, field_name="orchestration_policy")
    required_fields = {
        "schema_version",
        "profile",
        "parallelism_authority",
        "peer_awareness",
        "identity_source",
        "worker_env_var",
        "session_env_var",
        "require_equal_identity",
        "assignment_mode",
        "max_active_claims_per_worker",
        "worker_loop",
        "closure_mode",
    }
    optional_fields = {
        "user_authorized_item_ids",
        "user_authorization_ref",
        "multi_claim_authorization_ref",
    }
    missing = sorted(required_fields.difference(raw))
    unknown = sorted(set(raw).difference(required_fields | optional_fields))
    if missing or unknown:
        raise ValueError(
            "'orchestration_policy' must use the exact strict schema; "
            f"missing={missing}, unknown={unknown}."
        )

    schema_version = int(raw["schema_version"])
    if schema_version != 1:
        raise ValueError("Unsupported 'orchestration_policy.schema_version'; expected 1.")

    exact_values = {
        "profile": STRICT_ORCHESTRATION_PROFILE,
        "parallelism_authority": STRICT_PARALLELISM_AUTHORITY,
        "peer_awareness": STRICT_PEER_AWARENESS,
        "identity_source": STRICT_IDENTITY_SOURCE,
        "worker_loop": STRICT_WORKER_LOOP,
        "closure_mode": STRICT_CLOSURE_MODE,
    }
    for field, expected in exact_values.items():
        actual = ensure_text(raw[field], field_name=f"orchestration_policy.{field}")
        if actual != expected:
            raise ValueError(
                f"'orchestration_policy.{field}' must be '{expected}', observed '{actual}'."
            )

    assignment_mode = ensure_text(
        raw["assignment_mode"],
        field_name="orchestration_policy.assignment_mode",
    )
    if assignment_mode == STRICT_ASSIGNMENT_MODE:
        forbidden_authorization_fields = sorted(optional_fields.intersection(raw))
        if forbidden_authorization_fields:
            raise ValueError(
                "Automatic-only assignment must not declare user authorization fields; "
                f"observed={forbidden_authorization_fields}."
            )
        user_authorized_item_ids: tuple[str, ...] = ()
        user_authorization_ref: str | None = None
    elif assignment_mode == STRICT_USER_AUTHORIZED_ASSIGNMENT_MODE:
        raw_authorized_ids = load_text_tuple(
            raw.get("user_authorized_item_ids"),
            field_name="orchestration_policy.user_authorized_item_ids",
        )
        for token in raw_authorized_ids:
            if ".." in token or "\n" in token or "\r" in token:
                raise ValueError(f"Invalid target in user_authorized_item_ids: '{token}'.")
        user_authorized_item_ids = tuple(raw_authorized_ids)
        user_authorization_ref = ensure_text(
            raw.get("user_authorization_ref"),
            field_name="orchestration_policy.user_authorization_ref",
        )
    else:
        raise ValueError(
            "'orchestration_policy.assignment_mode' must be either "
            f"'{STRICT_ASSIGNMENT_MODE}' or '{STRICT_USER_AUTHORIZED_ASSIGNMENT_MODE}', "
            f"observed '{assignment_mode}'."
        )

    strict_worker_env = ensure_text(
        raw["worker_env_var"],
        field_name="orchestration_policy.worker_env_var",
    )
    strict_session_env = ensure_text(
        raw["session_env_var"],
        field_name="orchestration_policy.session_env_var",
    )
    if worker_env_var != strict_worker_env:
        raise ValueError(
            "'worker_env_var' must exactly match 'orchestration_policy.worker_env_var'."
        )
    if raw["require_equal_identity"] is not True:
        raise ValueError("'orchestration_policy.require_equal_identity' must be true.")
    max_claims = int(raw["max_active_claims_per_worker"])
    if max_claims < 1:
        raise ValueError("'orchestration_policy.max_active_claims_per_worker' must be at least 1.")
    if max_claims > 1:
        if assignment_mode != STRICT_USER_AUTHORIZED_ASSIGNMENT_MODE:
            raise ValueError("Multiple claims require exact user-authorized assignment.")
        ensure_text(raw.get("multi_claim_authorization_ref"), field_name="orchestration_policy.multi_claim_authorization_ref")
        if len(set(user_authorized_item_ids)) < max_claims:
            raise ValueError("Multiple claim limit exceeds the exact authorized item set.")
    elif "multi_claim_authorization_ref" in raw:
        raise ValueError("multi_claim_authorization_ref requires a multiple-claim limit.")

    if access_policy is not None:
        if access_policy.max_active_claims_per_worker != max_claims:
            raise ValueError(
                "Strict orchestration and access_policy claim limits must match."
            )
        admission = access_policy.admission
        worker_field = admission.worker_field
        if admission.environment_bindings.get(worker_field) != strict_worker_env:
            raise ValueError(
                "Strict orchestration requires the admission worker field to bind the host worker environment."
            )
        session_fields = {
            field
            for field, env_name in admission.environment_bindings.items()
            if env_name == strict_session_env
        }
        if not session_fields:
            raise ValueError(
                "Strict orchestration access policy must bind the configured host session environment."
            )
        if not any(
            worker_field in group and any(field in group for field in session_fields)
            for group in admission.require_equal_fields
        ):
            raise ValueError(
                "Strict orchestration access policy must require session and worker identity equality."
            )

    return OrchestrationPolicy(
        schema_version=schema_version,
        profile=STRICT_ORCHESTRATION_PROFILE,
        parallelism_authority=STRICT_PARALLELISM_AUTHORITY,
        peer_awareness=STRICT_PEER_AWARENESS,
        identity_source=STRICT_IDENTITY_SOURCE,
        worker_env_var=strict_worker_env,
        session_env_var=strict_session_env,
        require_equal_identity=True,
        assignment_mode=assignment_mode,
        user_authorized_item_ids=user_authorized_item_ids,
        user_authorization_ref=user_authorization_ref,
        max_active_claims_per_worker=max_claims,
        worker_loop=STRICT_WORKER_LOOP,
        closure_mode=STRICT_CLOSURE_MODE,
    )


def resolve_worker_id(
    worker: str | None,
    *,
    env_var: str | None = LEGACY_DEFAULT_WORKER_ENV,
    allow_auto_literal: bool = True,
    default: str | None = None,
) -> str:
    raw = (worker or "").strip()
    if raw and (not allow_auto_literal or raw.lower() != "auto"):
        return raw

    env_var_name = normalize_optional_text(env_var)
    if env_var_name:
        env_worker = os.environ.get(env_var_name, "").strip()
        if env_worker:
            return env_worker

    if default is not None:
        return default

    if raw:
        if env_var_name:
            raise ValueError(
                f"Automatic worker resolution requested, but environment variable '{env_var_name}' is not set."
            )
        raise ValueError("Automatic worker resolution requested, but no worker environment variable is configured.")

    if env_var_name:
        raise ValueError(f"No --worker provided and environment variable '{env_var_name}' is not set.")
    raise ValueError("No --worker provided and no worker environment variable is configured.")


def resolve_cli_worker(
    config: RuntimeConfig,
    *,
    worker: str | None,
    worker_env_var_override: str | None,
    default: str | None = None,
) -> str:
    if config.orchestration_policy is not None:
        if normalize_optional_text(worker) is not None:
            raise PermissionError(
                "Strict orchestration derives worker identity from the host task; --worker is forbidden."
            )
        if normalize_optional_text(worker_env_var_override) is not None:
            raise PermissionError(
                "Strict orchestration fixes the host identity environment; --worker-env-var is forbidden."
            )
        return resolve_worker_id(
            None,
            env_var=config.orchestration_policy.worker_env_var,
            allow_auto_literal=False,
        )
    return resolve_worker_id(
        worker,
        env_var=worker_env_var_override or config.worker_env_var,
        default=default,
    )


def _persist_user_authorization_locked(
    config: RuntimeConfig,
    target_id: str,
    user_authorization_ref: str,
) -> None:
    ref_text = ensure_text(user_authorization_ref, field_name="user_authorization_ref")
    raw_data = json.loads(config.config_path.read_text(encoding="utf-8"))
    raw_policy = raw_data.get("orchestration_policy")
    if isinstance(raw_policy, dict):
        existing_ids = list(raw_policy.get("user_authorized_item_ids") or [])
        if target_id not in existing_ids:
            existing_ids.append(target_id)
        raw_policy["assignment_mode"] = STRICT_USER_AUTHORIZED_ASSIGNMENT_MODE
        raw_policy["user_authorized_item_ids"] = existing_ids
        raw_policy["user_authorization_ref"] = ref_text
        temp_cfg = config.config_path.with_suffix(config.config_path.suffix + ".tmp")
        temp_cfg.write_text(json.dumps(raw_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_cfg.replace(config.config_path)
        config.orchestration_policy = load_orchestration_policy(
            raw_data,
            worker_env_var=config.worker_env_var,
            access_policy=config.access_policy,
        )


def validate_orchestration_action(
    config: RuntimeConfig,
    worker: str,
    action: str,
    *,
    item_id: str | None = None,
    module: str | None = None,
    user_authorization_ref: str | None = None,
    candidate_tokens: tuple[str, ...] = (),
) -> None:
    policy = config.orchestration_policy
    if policy is None:
        return
    if action == "refresh":
        raise PermissionError(
            "Strict parallel subtasks do not expose a worker/coordinator refresh or closeout action; "
            "shared closeout belongs to a later follow-up subtask."
        )
    target = normalize_optional_text(item_id) or normalize_optional_text(module)
    auth_ref = normalize_optional_text(user_authorization_ref)
    if target is not None:
        if action not in {"claim", "reopen"} and policy.max_active_claims_per_worker == 1:
            raise PermissionError(
                "Strict orchestration resolves heartbeat/complete/release from the current claim; "
                "explicit --item-id / --module is only supported for a user-authorized claim."
            )
        if action in {"claim", "reopen"} and auth_ref is None:
            if policy.assignment_mode == STRICT_ASSIGNMENT_MODE:
                raise PermissionError(
                    "Strict orchestration assigns the next available item atomically by default; "
                    "explicit --item-id / --module requires exact user authorization in the canonical policy "
                    "(or pass --user-authorization-ref)."
                )
            tokens_to_check = {
                tok
                for tok in (normalize_optional_text(item_id), normalize_optional_text(module), *candidate_tokens)
                if tok is not None
            }
            if not tokens_to_check.intersection(policy.user_authorized_item_ids):
                raise PermissionError(
                    f"Work item '{target}' is not listed in "
                    "orchestration_policy.user_authorized_item_ids."
                )

    host_worker = os.environ.get(policy.worker_env_var, "").strip()
    host_session = os.environ.get(policy.session_env_var, "").strip()
    if not host_worker or not host_session:
        raise PermissionError(
            "Strict orchestration requires non-empty host task worker and session identities."
        )
    if policy.require_equal_identity and host_worker != host_session:
        raise PermissionError(
            "Strict orchestration only admits an independent top-level task; host session/thread identity differs."
        )
    if worker != host_worker:
        raise PermissionError(
            "Strict orchestration worker must equal the current host task identity."
        )


def format_local(dt: datetime) -> str:
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def parse_iso(dt_text: str) -> datetime:
    return datetime.fromisoformat(dt_text)


KNOWN_DLM_DIRS = ("docs-llm", "doc_llm", "docs_llm")


def find_git_root(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    candidates = [current, *current.parents]
    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate
    env_root = os.environ.get("DLM_PROJECT_ROOT", "").strip()
    if env_root:
        resolved_env = Path(env_root).resolve()
        if resolved_env in candidates:
            return resolved_env
    for candidate in candidates:
        if (candidate / "dlm.config.json").is_file() or any((candidate / d).is_dir() for d in KNOWN_DLM_DIRS):
            return candidate
    return None


def resolve_base_dir(config_path: Path, data: dict[str, Any]) -> Path:
    root_dir = data.get("root_dir")
    if root_dir:
        return (config_path.parent / root_dir).resolve()

    path_base = data.get("path_base", "project_root")
    if path_base == "config_dir":
        return config_path.parent
    if path_base != "project_root":
        raise ValueError("Unsupported path_base. Use 'project_root' or 'config_dir'.")

    git_root = find_git_root(config_path)
    if git_root is not None:
        return git_root

    return config_path.parent


def resolve_runtime_path(base_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return (base_dir / candidate).resolve()


def resolve_registry_path(repository_root: Path, raw_path: Any, *, field_name: str) -> Path:
    text = ensure_text(raw_path, field_name=field_name)
    candidate = (repository_root / text).resolve()
    try:
        candidate.relative_to(repository_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Lease namespace registry field '{field_name}' escapes the repository root.") from exc
    return candidate


def validate_registered_namespace(
    config_path: Path,
    epic_id: str,
    work_items_path: Path,
    claims_dir: Path,
) -> None:
    repository_roots = {
        root
        for candidate in (config_path, work_items_path, claims_dir)
        if (root := find_git_root(candidate)) is not None
    }
    for repository_root in repository_roots:
        registry_path = repository_root / ".agents" / "work_item_lease_namespaces.json"
        if not registry_path.is_file():
            continue
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PermissionError(f"Lease namespace registry is not valid JSON: {registry_path}") from exc
        if registry.get("schema_version") != 1 or not isinstance(registry.get("namespaces"), list):
            raise PermissionError(f"Lease namespace registry schema is invalid: {registry_path}")

        matched: list[dict[str, Any]] = []
        for index, raw_entry in enumerate(registry["namespaces"]):
            entry = require_object(raw_entry, field_name=f"lease_namespace.namespaces[{index}]")
            protected_config = resolve_registry_path(
                repository_root,
                entry.get("canonical_config"),
                field_name=f"namespaces[{index}].canonical_config",
            )
            protected_work_items = resolve_registry_path(
                repository_root,
                entry.get("work_items"),
                field_name=f"namespaces[{index}].work_items",
            )
            protected_claims = resolve_registry_path(
                repository_root,
                entry.get("claims_dir"),
                field_name=f"namespaces[{index}].claims_dir",
            )
            if (
                config_path == protected_config
                or work_items_path == protected_work_items
                or claims_dir == protected_claims
            ):
                matched.append(
                    {
                        "task": ensure_text(entry.get("task"), field_name=f"namespaces[{index}].task"),
                        "config": protected_config,
                        "work_items": protected_work_items,
                        "claims": protected_claims,
                    }
                )

        if not matched:
            continue
        if len(matched) != 1:
            raise PermissionError("Lease runtime paths match multiple protected namespace registrations.")
        namespace = matched[0]
        mismatches = []
        if epic_id != namespace["task"]:
            mismatches.append("task")
        if config_path != namespace["config"]:
            mismatches.append("canonical_config")
        if work_items_path != namespace["work_items"]:
            mismatches.append("work_items")
        if claims_dir != namespace["claims"]:
            mismatches.append("claims_dir")
        if mismatches:
            raise PermissionError(
                "Protected lease namespace can only be opened by its registered canonical config; "
                f"mismatched fields: {mismatches}."
            )


def load_runtime_config(config_path: str, lease_seconds_override: int | None = None) -> RuntimeConfig:
    resolved_config = Path(config_path).resolve()
    data = json.loads(resolved_config.read_text(encoding="utf-8"))
    base_dir = resolve_base_dir(resolved_config, data)

    lease_seconds = int(lease_seconds_override or data.get("lease_seconds", 1800))
    lock_timeout_seconds = int(data.get("lock_timeout_seconds", 120))
    lock_stale_seconds = int(data.get("lock_stale_seconds", 600))

    work_items_path = resolve_runtime_path(base_dir, data["work_items_path"])
    claims_dir = resolve_runtime_path(base_dir, data["claims_dir"])
    validate_registered_namespace(
        resolved_config,
        ensure_text(data.get("epic_id"), field_name="epic_id"),
        work_items_path,
        claims_dir,
    )
    claims_dir.mkdir(parents=True, exist_ok=True)

    if "worker_env_var" in data:
        worker_env_var = normalize_optional_text(data.get("worker_env_var"))
    else:
        worker_env_var = LEGACY_DEFAULT_WORKER_ENV

    access_policy = load_access_policy(data, base_dir)
    orchestration_policy = load_orchestration_policy(
        data,
        worker_env_var=worker_env_var,
        access_policy=access_policy,
    )
    sop_ref = normalize_optional_text(data.get("sop_ref"))
    raw_priority_order = data.get("priority_order")
    priority_order: tuple[str, ...] = ()
    if raw_priority_order is not None:
        priority_order = load_text_tuple(raw_priority_order, field_name="priority_order")

    return RuntimeConfig(
        config_path=resolved_config,
        base_dir=base_dir,
        epic_id=data["epic_id"],
        label=data["label"],
        work_items_path=work_items_path,
        claims_dir=claims_dir,
        lease_seconds=lease_seconds,
        lock_timeout_seconds=lock_timeout_seconds,
        lock_stale_seconds=lock_stale_seconds,
        worker_env_var=worker_env_var,
        columns=load_column_map(data),
        note_messages=load_note_messages(data),
        access_policy=access_policy,
        orchestration_policy=orchestration_policy,
        sop_ref=sop_ref,
        priority_order=priority_order,
    )


def split_markdown_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise ValueError(f"Invalid markdown table row: {line}")
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def load_markdown_table(path: Path, columns: ColumnMap) -> MarkdownTable:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    for index in range(len(lines) - 1):
        if lines[index].lstrip().startswith("|") and lines[index + 1].lstrip().startswith("|") and "---" in lines[index + 1]:
            start = index
            break
    if start is None:
        raise ValueError(f"No markdown table found in {path}")

    end = start + 2
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1

    headers = split_markdown_row(lines[start])
    rows = [split_markdown_row(line) for line in lines[start + 2 : end]]
    header_index = {header: index for index, header in enumerate(headers)}

    missing = sorted(header for header in columns.required_headers if header not in header_index)
    if missing:
        raise ValueError(f"{path} is missing required columns: {missing}")

    note_column = next((candidate for candidate in columns.note_candidates if candidate in header_index), None)
    if note_column is None:
        raise ValueError(f"{path} must provide one of the configured note columns: {list(columns.note_candidates)}.")
    remark_column = next(
        (candidate for candidate in columns.remark_candidates if candidate in header_index and candidate != note_column),
        next((candidate for candidate in columns.remark_candidates if candidate in header_index), None),
    )
    module_column = next((candidate for candidate in columns.module_candidates if candidate in header_index), None)
    priority_column = next((candidate for candidate in columns.priority_candidates if candidate in header_index), None)

    id_index = header_index[columns.item_id]
    seen_item_ids: set[str] = set()
    for row_index, row in enumerate(rows, start=1):
        if len(row) != len(headers):
            raise ValueError(
                f"{path} work-item row {row_index} has {len(row)} cells; expected {len(headers)}."
            )
        item_id = validate_item_id(row[id_index])
        if item_id in seen_item_ids:
            raise ValueError(f"{path} contains duplicate work item ID '{item_id}'.")
        seen_item_ids.add(item_id)

    return MarkdownTable(
        intro_lines=lines[:start],
        headers=headers,
        separator_line=lines[start + 1],
        rows=rows,
        tail_lines=lines[end:],
        header_index=header_index,
        id_column=columns.item_id,
        state_column=columns.state,
        worker_column=columns.worker,
        started_at_column=columns.started_at,
        completed_at_column=columns.completed_at,
        note_column=note_column,
        remark_column=remark_column,
        module_column=module_column,
        priority_column=priority_column,
    )


def write_markdown_table(path: Path, table: MarkdownTable) -> None:
    body = []
    body.extend(table.intro_lines)
    body.append(format_markdown_row(table.headers))
    body.append(table.separator_line)
    for row in table.rows:
        body.append(format_markdown_row(row))
    body.extend(table.tail_lines)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text("\n".join(body) + "\n", encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def format_markdown_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def set_row_value(table: MarkdownTable, row: list[str], column: str, value: str) -> None:
    row[table.header_index[column]] = value


def get_row_value(table: MarkdownTable, row: list[str], column: str) -> str:
    return row[table.header_index[column]]


def clear_runtime_fields(table: MarkdownTable, row: list[str], note: str = EMPTY) -> None:
    set_row_value(table, row, table.state_column, STATE_OPEN)
    set_row_value(table, row, table.worker_column, EMPTY)
    set_row_value(table, row, table.started_at_column, EMPTY)
    set_row_value(table, row, table.completed_at_column, EMPTY)
    set_row_value(table, row, table.note_column, note)


def list_claim_files(config: RuntimeConfig) -> list[Path]:
    return sorted(path for path in config.claims_dir.glob("*.json") if path.name != ".work_items.write.lock")


def load_claim(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_claim(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def create_claim(path: Path, payload: dict[str, Any]) -> None:
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def canonical_record_digest(record: dict[str, Any], fields: tuple[str, ...]) -> str:
    try:
        payload = {field: record[field] for field in fields}
    except KeyError as exc:
        raise PermissionError(f"Digest source field is missing: {exc.args[0]}") from exc
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def render_policy_template(template: str, values: dict[str, Any], *, field_name: str) -> str:
    try:
        return template.format(**values)
    except KeyError as exc:
        raise ValueError(f"'{field_name}' references unsupported placeholder: {exc.args[0]}") from exc


def admission_file_path(config: RuntimeConfig, worker: str) -> Path:
    policy = config.access_policy
    if policy is None:
        raise ValueError("No access policy is configured.")
    directory = policy.admission.directory.resolve()
    rendered = render_policy_template(
        policy.admission.file_template,
        {"worker": worker, "epic_id": config.epic_id, "label": config.label},
        field_name="access_policy.admission.file_template",
    )
    candidate = (directory / rendered).resolve()
    try:
        candidate.relative_to(directory)
    except ValueError as exc:
        raise PermissionError("Admission file template resolved outside the configured admission directory.") from exc
    return candidate


def identity_entry_matches(
    entry: dict[str, Any],
    record: dict[str, Any],
    worker: str,
    identity_fields: tuple[str, ...],
) -> bool:
    values = {"worker": worker}
    for field in identity_fields:
        expected = entry[field]
        if isinstance(expected, str):
            expected = render_policy_template(
                expected,
                values,
                field_name=f"access_policy.admission.allowed_identities[].{field}",
            )
        if record.get(field) != expected:
            return False
    return True


def validate_mutation_access(
    config: RuntimeConfig,
    worker: str,
    action: str | None,
    *,
    require_environment: bool = True,
) -> dict[str, Any] | None:
    policy = config.access_policy
    if policy is None:
        return None
    if action is not None and action not in policy.action_roles:
        raise PermissionError(f"Access policy does not define action '{action}'.")

    admission_policy = policy.admission
    path = admission_file_path(config, worker)
    if not path.is_file():
        raise PermissionError(f"Admission file is required for worker '{worker}': {path}")
    try:
        record = load_claim(path)
    except Exception as exc:
        raise PermissionError(f"Admission file is not valid JSON: {path}") from exc
    if not isinstance(record, dict):
        raise PermissionError(f"Admission file must contain a JSON object: {path}")

    if record.get(admission_policy.worker_field) != worker:
        raise PermissionError(
            f"Admission worker mismatch for '{worker}' in field '{admission_policy.worker_field}'."
        )
    for field in admission_policy.identity_fields:
        if normalize_optional_text(record.get(field)) is None:
            raise PermissionError(f"Admission identity field '{field}' is missing or empty.")
    if require_environment:
        for field, env_var in admission_policy.environment_bindings.items():
            current_identity = os.environ.get(env_var, "").strip()
            if not current_identity:
                raise PermissionError(
                    f"Admission environment binding requires non-empty environment variable '{env_var}'."
                )
            if record.get(field) != current_identity:
                raise PermissionError(
                    f"Admission environment binding mismatch: field '{field}' does not match '{env_var}'."
                )
    for fields in admission_policy.require_equal_fields:
        values = [record.get(field) for field in fields]
        if any(value != values[0] for value in values[1:]):
            raise PermissionError(f"Admission identity fields must be equal: {list(fields)}")
    for field, expected in admission_policy.expected_fields.items():
        if record.get(field) != expected:
            raise PermissionError(f"Admission expected field mismatch: '{field}'.")

    role = normalize_optional_text(record.get(admission_policy.role_field))
    if role is None or role not in admission_policy.allowed_roles:
        raise PermissionError(f"Admission role is not allowed: {role!r}.")
    if action is not None and role not in policy.action_roles[action]:
        raise PermissionError(f"Admission role '{role}' cannot perform action '{action}'.")

    matched_identity = next(
        (
            entry
            for entry in admission_policy.allowed_identities
            if identity_entry_matches(entry, record, worker, admission_policy.identity_fields)
            and role in entry["roles"]
        ),
        None,
    )
    if matched_identity is None:
        raise PermissionError(
            f"Admission identity is not allowlisted for worker '{worker}' with role '{role}'."
        )

    expected_digest = canonical_record_digest(record, admission_policy.digest_fields)
    actual_digest = record.get(admission_policy.digest_field)
    if actual_digest != expected_digest:
        raise PermissionError(f"Admission digest mismatch: {path}")

    return {
        "schema_version": 1,
        "admission_file": str(path),
        "admission_digest": expected_digest,
        "worker": worker,
        "role": role,
        "identity": {field: record[field] for field in admission_policy.identity_fields},
    }


def verify_claim_access_binding(
    config: RuntimeConfig,
    claim: dict[str, Any],
    access_binding: dict[str, Any] | None,
) -> None:
    if config.access_policy is None:
        return
    bound = claim.get("access_admission")
    if not isinstance(bound, dict):
        raise PermissionError("Claim is missing the access admission binding required by the runtime policy.")
    if access_binding is None or bound != access_binding:
        raise PermissionError("Claim admission binding no longer matches the current admission record.")


def persisted_admission_binding(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PermissionError("Claim admission binding is missing or invalid.")
    identity = value.get("identity")
    if not isinstance(identity, dict) or not identity:
        raise PermissionError("Claim admission binding is missing its identity fields.")
    binding = {
        "schema_version": value.get("schema_version"),
        "admission_digest": value.get("admission_digest"),
        "worker": value.get("worker"),
        "role": value.get("role"),
        "identity": identity,
    }
    if binding["schema_version"] != 1:
        raise PermissionError("Claim admission binding schema is invalid.")
    for field in ("admission_digest", "worker", "role"):
        if normalize_optional_text(binding[field]) is None:
            raise PermissionError(f"Claim admission binding field '{field}' is missing.")
    return binding


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    current = document
    if pointer == "":
        return current
    for raw_token in pointer.split("/")[1:]:
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise PermissionError(f"Completion artifact JSON pointer is missing token '{token}'.")
            current = current[token]
        elif isinstance(current, list):
            try:
                index = int(token)
                current = current[index]
            except (ValueError, IndexError) as exc:
                raise PermissionError(
                    f"Completion artifact JSON pointer has invalid array token '{token}'."
                ) from exc
        else:
            raise PermissionError(
                f"Completion artifact JSON pointer cannot traverse token '{token}'."
            )
    return current


def resolve_completion_path(
    config: RuntimeConfig,
    template: str,
    values: dict[str, Any],
    *,
    field_name: str,
) -> Path:
    rendered = render_policy_template(template, values, field_name=field_name)
    raw_path = Path(rendered)
    path = resolve_runtime_path(config.base_dir, rendered)
    if not raw_path.is_absolute():
        try:
            path.relative_to(config.base_dir.resolve())
        except ValueError as exc:
            raise PermissionError("Completion path template resolved outside the runtime base directory.") from exc
    return path


def verify_completion_receipt(
    config: RuntimeConfig,
    worker: str,
    item_id: str,
    status: str,
    claim: dict[str, Any] | None,
) -> dict[str, Any] | None:
    policy = config.access_policy
    if policy is None or policy.completion_receipt is None:
        return None
    receipt_policy = policy.completion_receipt
    if status not in receipt_policy.required_statuses:
        return None
    item_id = validate_item_id(item_id)

    values = {
        "worker": worker,
        "work_item_id": item_id,
        "status": status,
        "epic_id": config.epic_id,
        "label": config.label,
    }
    path = resolve_completion_path(
        config,
        receipt_policy.path_template,
        values,
        field_name="access_policy.completion_receipt.path_template",
    )
    if not path.is_file():
        raise PermissionError(f"Completion receipt is required before status '{status}': {path}")
    try:
        receipt = load_claim(path)
    except Exception as exc:
        raise PermissionError(f"Completion receipt is not valid JSON: {path}") from exc
    if not isinstance(receipt, dict):
        raise PermissionError(f"Completion receipt must contain a JSON object: {path}")

    required_values = {
        receipt_policy.worker_field: worker,
        receipt_policy.item_field: item_id,
        receipt_policy.status_field: status,
        **receipt_policy.expected_fields,
    }
    for field, expected in required_values.items():
        if receipt.get(field) != expected:
            raise PermissionError(f"Completion receipt field mismatch: '{field}'.")

    for field, constraint in receipt_policy.field_constraints.items():
        value = receipt.get(field)
        if constraint.allowed_values is not None and value not in constraint.allowed_values:
            raise PermissionError(f"Completion receipt field '{field}' is outside the allowed values.")
        if constraint.pattern is not None and (
            not isinstance(value, str) or re.fullmatch(constraint.pattern, value) is None
        ):
            raise PermissionError(f"Completion receipt field '{field}' does not match its required pattern.")

    raw_claim_access_admission = claim.get("access_admission") if isinstance(claim, dict) else None
    claim_access_admission = (
        persisted_admission_binding(raw_claim_access_admission)
        if raw_claim_access_admission is not None
        else None
    )
    receipt_access_admission = None
    if receipt_policy.claim_admission_binding_field is not None:
        binding_field = receipt_policy.claim_admission_binding_field
        receipt_access_admission = receipt.get(binding_field)
        if not isinstance(receipt_access_admission, dict):
            raise PermissionError(
                f"Completion receipt field '{binding_field}' must contain the persisted claim admission binding."
            )
        normalized_receipt_binding = persisted_admission_binding(receipt_access_admission)
        if receipt_access_admission != normalized_receipt_binding:
            raise PermissionError(
                f"Completion receipt field '{binding_field}' must contain only the stable admission credential."
            )
        receipt_access_admission = normalized_receipt_binding
        if receipt_access_admission.get("worker") != worker:
            raise PermissionError("Persisted completion admission binding does not match the receipt worker.")
        if claim is not None and receipt_access_admission != claim_access_admission:
            raise PermissionError("Completion receipt admission binding does not match the current claim.")

    access_admission = claim_access_admission or receipt_access_admission
    if receipt_policy.claim_admission_digest_field is not None:
        if not isinstance(access_admission, dict):
            raise PermissionError("Completion verification is missing the bound claim admission.")
        claim_admission_digest = access_admission.get("admission_digest")
        field = receipt_policy.claim_admission_digest_field
        if receipt.get(field) != claim_admission_digest:
            raise PermissionError(
                f"Completion receipt does not match the claim admission digest in field '{field}'."
            )

    verified_artifacts: list[dict[str, str]] = []
    for binding in receipt_policy.artifact_digest_bindings:
        artifact_path = resolve_completion_path(
            config,
            binding.path_template,
            values,
            field_name="access_policy.completion_receipt.artifact_digest_bindings[].path_template",
        )
        if not artifact_path.is_file():
            raise PermissionError(f"Completion artifact is required before status '{status}': {artifact_path}")
        artifact_digest = "sha256:" + hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if receipt.get(binding.receipt_field) != artifact_digest:
            raise PermissionError(
                f"Completion receipt artifact digest mismatch for field '{binding.receipt_field}': "
                f"{artifact_path}"
            )
        verified_artifacts.append(
            {
                "path": str(artifact_path),
                "digest_field": binding.receipt_field,
                "digest": artifact_digest,
            }
        )

    for binding in receipt_policy.json_digest_bindings:
        artifact_path = resolve_completion_path(
            config,
            binding.path_template,
            values,
            field_name="access_policy.completion_receipt.json_digest_bindings[].path_template",
        )
        if not artifact_path.is_file():
            raise PermissionError(f"Completion JSON artifact is required before status '{status}': {artifact_path}")
        try:
            artifact_document = json.loads(artifact_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PermissionError(f"Completion JSON artifact is invalid: {artifact_path}") from exc
        bound_value = resolve_json_pointer(artifact_document, binding.json_pointer)
        canonical_value = json.dumps(
            bound_value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        json_digest = "sha256:" + hashlib.sha256(canonical_value).hexdigest()
        if receipt.get(binding.receipt_field) != json_digest:
            raise PermissionError(
                f"Completion receipt JSON digest mismatch for field '{binding.receipt_field}': "
                f"{artifact_path}{binding.json_pointer}"
            )
        verified_artifacts.append(
            {
                "path": str(artifact_path),
                "digest_field": binding.receipt_field,
                "digest": json_digest,
            }
        )

    expected_digest = canonical_record_digest(receipt, receipt_policy.digest_fields)
    if receipt.get(receipt_policy.digest_field) != expected_digest:
        raise PermissionError(f"Completion receipt digest mismatch: {path}")
    return {
        "completion_receipt": str(path),
        "completion_receipt_digest": expected_digest,
        "completion_receipt_verified": True,
        "completion_artifacts_verified": verified_artifacts,
    }


def claim_file_path(config: RuntimeConfig, item_id: str) -> Path:
    safe_item_id = validate_item_id(item_id)
    claims_dir = config.claims_dir.resolve()
    path = (claims_dir / f"{safe_item_id}.json").resolve()
    try:
        path.relative_to(claims_dir)
    except ValueError as exc:
        raise PermissionError("Claim path resolved outside the configured claims directory.") from exc
    return path


def verify_claim_file(config: RuntimeConfig, item_id: str, worker: str, *, must_exist: bool = True) -> dict[str, Any]:
    path = claim_file_path(config, item_id)
    info: dict[str, Any] = {
        "claim_file": str(path),
        "claim_file_exists": path.exists(),
    }

    if not path.exists():
        if must_exist:
            raise ValueError(f"Claim file is missing for item '{item_id}': {path}")
        info["claim_verified"] = False
        return info

    claim = load_claim(path)
    info.update(
        {
            "claim_file_work_item_id": claim.get("work_item_id"),
            "claim_file_worker": claim.get("worker"),
            "claim_claimed_at": claim.get("claimed_at"),
            "claim_heartbeat_at": claim.get("heartbeat_at"),
            "claim_expires_at": claim.get("expires_at"),
        }
    )
    if "access_admission" in claim:
        info["claim_access_admission"] = claim["access_admission"]

    if claim.get("work_item_id") != item_id:
        raise ValueError(
            f"Claim file item mismatch. expected='{item_id}' actual='{claim.get('work_item_id')}' path='{path}'"
        )
    if claim.get("worker") != worker:
        raise ValueError(
            f"Claim file worker mismatch. expected='{worker}' actual='{claim.get('worker')}' path='{path}'"
        )
    if not claim_matches_config(claim, config):
        raise ValueError(f"Claim file does not belong to the current runtime config: {path}")

    info["claim_verified"] = True
    return info


def build_claim_payload(
    config: RuntimeConfig,
    item_id: str,
    worker: str,
    claimed_at: datetime,
    access_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item_id = validate_item_id(item_id)
    expires_at = claimed_at + timedelta(seconds=config.lease_seconds)
    payload = {
        "schema_version": 2 if access_binding is not None else 1,
        "epic_id": config.epic_id,
        "label": config.label,
        "work_items_path": str(config.work_items_path),
        "work_item_id": item_id,
        "worker": worker,
        "claimed_at": claimed_at.isoformat(),
        "heartbeat_at": claimed_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    if access_binding is not None:
        payload["access_admission"] = access_binding
    return payload


def claim_is_expired(claim: dict[str, Any], now: datetime) -> bool:
    try:
        return parse_iso(claim["expires_at"]) <= now
    except Exception:
        return True


def claim_matches_config(claim: dict[str, Any], config: RuntimeConfig) -> bool:
    return claim.get("work_items_path") == str(config.work_items_path)


def try_unlink_runtime_file(path: Path, *, missing_ok: bool = False) -> bool:
    try:
        path.unlink(missing_ok=missing_ok)
        return True
    except PermissionError as exc:
        print(f"[lease-warning] Could not remove runtime file {path}: {exc}", file=sys.stderr)
        return False


def active_claims(config: RuntimeConfig, now: datetime) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    active: dict[str, dict[str, Any]] = {}
    expired: list[dict[str, Any]] = []

    for path in list_claim_files(config):
        try:
            claim = load_claim(path)
        except Exception:
            expired.append({"path": path, "claim": None, "reason": "invalid"})
            continue

        if not claim_matches_config(claim, config):
            expired.append({"path": path, "claim": claim, "reason": "invalid"})
            continue

        try:
            claim_item_id = validate_item_id(claim.get("work_item_id"))
            if path.resolve() != claim_file_path(config, claim_item_id):
                raise ValueError("Claim filename does not match its work_item_id.")
        except Exception:
            expired.append({"path": path, "claim": claim, "reason": "invalid"})
            continue

        if config.access_policy is not None:
            try:
                claim_worker = ensure_text(claim.get("worker"), field_name="claim.worker")
                binding = validate_mutation_access(
                    config,
                    claim_worker,
                    None,
                    require_environment=False,
                )
                verify_claim_access_binding(config, claim, binding)
            except Exception:
                expired.append({"path": path, "claim": claim, "reason": "invalid"})
                continue

        if claim_is_expired(claim, now):
            claim["__path"] = path
            expired.append({"path": path, "claim": claim, "reason": "time_expired"})
            continue

        claim["__path"] = path
        active[claim_item_id] = claim

    return active, expired


def refresh_locked(
    config: RuntimeConfig,
    table: MarkdownTable,
    now: datetime,
    *,
    preserve_worker: str | None = None,
) -> dict[str, Any]:
    changed = False
    active, expired = active_claims(config, now)
    remaining_expired: list[dict[str, Any]] = []

    for item in expired:
        path = item["path"]
        claim = item["claim"] or {}
        item_id = claim.get("work_item_id")
        if (
            preserve_worker is not None
            and item.get("reason") == "time_expired"
            and item_id
            and item_id not in active
            and claim.get("worker") == preserve_worker
        ):
            row = table.get_row(item_id)
            if (
                row is not None
                and normalize_state(get_row_value(table, row, table.state_column)) == STATE_ACTIVE
                and get_row_value(table, row, table.worker_column) in {preserve_worker, "", EMPTY}
            ):
                claim["heartbeat_at"] = now.isoformat()
                claim["expires_at"] = (now + timedelta(seconds=config.lease_seconds)).isoformat()
                write_claim(path, {k: v for k, v in claim.items() if not k.startswith("__")})
                claim["__path"] = path
                active[item_id] = claim
                continue

        remaining_expired.append(item)
        if item_id:
            row = table.get_row(item_id)
            if row and normalize_state(get_row_value(table, row, table.state_column)) == STATE_ACTIVE:
                clear_runtime_fields(table, row, config.note_messages.lease_expired)
                changed = True
        if path.exists():
            try_unlink_runtime_file(path)

    active_ids = set(active)
    for row in table.rows:
        item_id = get_row_value(table, row, table.id_column)
        raw_state = get_row_value(table, row, table.state_column)
        state = normalize_state(raw_state)
        if raw_state != state and state in {STATE_OPEN, STATE_ACTIVE, STATE_DONE, STATE_BLOCKED}:
            set_row_value(table, row, table.state_column, state)
            changed = True
        if state in TERMINAL_STATES:
            if item_id in active_ids:
                if config.access_policy is None:
                    try_unlink_runtime_file(active[item_id]["__path"], missing_ok=True)
                    continue
                claim = active[item_id]
                set_row_value(table, row, table.state_column, STATE_ACTIVE)
                set_row_value(table, row, table.worker_column, claim["worker"])
                set_row_value(table, row, table.completed_at_column, EMPTY)
                changed = True
                state = STATE_ACTIVE
            elif config.access_policy is not None and config.access_policy.completion_receipt is not None:
                terminal_status = "done" if state == STATE_DONE else "blocked"
                terminal_worker = normalize_optional_text(get_row_value(table, row, table.worker_column))
                try:
                    if terminal_worker is None or terminal_worker == EMPTY:
                        raise PermissionError("Terminal row is missing its worker identity.")
                    receipt_policy = config.access_policy.completion_receipt
                    terminal_claim = None
                    if receipt_policy.claim_admission_binding_field is None:
                        terminal_binding = validate_mutation_access(
                            config,
                            terminal_worker,
                            None,
                            require_environment=False,
                        )
                        terminal_claim = {"access_admission": terminal_binding}
                    verify_completion_receipt(
                        config,
                        terminal_worker,
                        item_id,
                        terminal_status,
                        terminal_claim,
                    )
                except Exception:
                    clear_runtime_fields(table, row, config.note_messages.terminal_unverified)
                    changed = True
                continue
            else:
                continue

        if item_id in active:
            claim = active[item_id]
            if state != STATE_ACTIVE:
                set_row_value(table, row, table.state_column, STATE_ACTIVE)
                changed = True
            if get_row_value(table, row, table.worker_column) != claim["worker"]:
                set_row_value(table, row, table.worker_column, claim["worker"])
                changed = True
            if get_row_value(table, row, table.started_at_column) in {"", EMPTY}:
                set_row_value(table, row, table.started_at_column, format_local(parse_iso(claim["claimed_at"])))
                changed = True
            if get_row_value(table, row, table.completed_at_column) != EMPTY:
                set_row_value(table, row, table.completed_at_column, EMPTY)
                changed = True
        elif state == STATE_ACTIVE:
            clear_runtime_fields(table, row, config.note_messages.claim_missing)
            changed = True

    return {
        "changed": changed,
        "active_count": len(active),
        "expired_count": len(remaining_expired),
        "active": active,
    }


@contextmanager
def write_mutex(config: RuntimeConfig, worker: str):
    lock_path = config.write_lock_path
    deadline = time.monotonic() + config.lock_timeout_seconds

    while True:
        payload = {
            "worker": worker,
            "pid": os.getpid(),
            "acquired_at": now_local().isoformat(),
        }
        try:
            create_claim(lock_path, payload)
            break
        except FileExistsError:
            stale = False
            try:
                current = load_claim(lock_path)
                acquired = parse_iso(current["acquired_at"])
                stale = acquired + timedelta(seconds=config.lock_stale_seconds) <= now_local()
            except Exception:
                stale = True

            if stale:
                if not try_unlink_runtime_file(lock_path, missing_ok=True):
                    raise TimeoutError(f"Stale markdown write lock could not be removed: {lock_path}")
                continue

            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for markdown write lock: {lock_path}")
            time.sleep(0.25)

    try:
        yield
    finally:
        try_unlink_runtime_file(lock_path, missing_ok=True)


def resolve_worker_claim(active: dict[str, dict[str, Any]], worker: str, item_id: str | None = None) -> dict[str, Any] | None:
    matches = [
        claim
        for claim in active.values()
        if claim.get("worker") == worker and (item_id is None or claim.get("work_item_id") == item_id)
    ]
    if not matches:
        return None
    if item_id is None and len(matches) > 1:
        raise ValueError(f"Worker '{worker}' owns multiple active claims; specify --item-id.")
    return matches[0]


def resolve_expired_worker_claim(config: RuntimeConfig, worker: str, item_id: str | None) -> dict[str, Any] | None:
    candidate_paths = (
        [claim_file_path(config, item_id)]
        if item_id is not None
        else list_claim_files(config)
    )
    matches: list[dict[str, Any]] = []
    for path in candidate_paths:
        if not path.exists():
            continue
        try:
            claim = load_claim(path)
        except Exception:
            continue
        if not claim_matches_config(claim, config):
            continue
        if claim.get("worker") != worker:
            continue
        if item_id is not None and claim.get("work_item_id") != item_id:
            continue
        claim["__path"] = path
        matches.append(claim)

    if not matches:
        return None
    if item_id is None and len(matches) > 1:
        raise ValueError(f"Worker '{worker}' owns multiple expired claims; specify --item-id.")
    return matches[0]


def build_normalized_row_view(table: MarkdownTable, row: list[str]) -> dict[str, str]:
    raw_state = get_row_value(table, row, table.state_column)
    raw_remark = (
        get_row_value(table, row, table.remark_column).strip()
        if table.remark_column is not None
        else ""
    )
    view = {
        "work_item_id": get_row_value(table, row, table.id_column),
        "work_item_state": normalize_state(raw_state),
        "work_item_status": state_to_status_code(raw_state),
        "work_item_worker": get_row_value(table, row, table.worker_column),
        "work_item_started_at": get_row_value(table, row, table.started_at_column),
        "work_item_completed_at": get_row_value(table, row, table.completed_at_column),
        "work_item_note": get_row_value(table, row, table.note_column),
        "work_item_remark": "" if raw_remark == EMPTY else raw_remark,
    }
    if table.module_column is not None:
        view["work_item_module"] = get_row_value(table, row, table.module_column)
    if table.priority_column is not None:
        view["work_item_priority"] = get_row_value(table, row, table.priority_column)
    return view


def finalize_result(
    config: RuntimeConfig,
    table: MarkdownTable,
    row: list[str],
    mode: str,
    *,
    worker: str | None = None,
    claim_info: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = table.row_as_dict(row)
    result.update(build_normalized_row_view(table, row))
    configured_cols: dict[str, str | None] = {
        "id": table.id_column,
        "state": table.state_column,
        "worker": table.worker_column,
        "started_at": table.started_at_column,
        "completed_at": table.completed_at_column,
        "note": table.note_column,
    }
    if table.remark_column is not None:
        configured_cols["remark"] = table.remark_column
    if table.module_column is not None:
        configured_cols["module"] = table.module_column
    if table.priority_column is not None:
        configured_cols["priority"] = table.priority_column
    result.update(
        {
            "epic_id": config.epic_id,
            "label": config.label,
            "mode": mode,
            "work_items_path": str(config.work_items_path),
            "claims_dir": str(config.claims_dir),
            "configured_worker_env_var": config.worker_env_var,
            "configured_columns": configured_cols,
        }
    )
    if config.sop_ref is not None:
        result["sop_ref"] = config.sop_ref
    if config.priority_order:
        result["priority_order"] = list(config.priority_order)
    if worker is not None:
        result["resolved_worker"] = worker
    if claim_info:
        result.update(claim_info)
    if extra:
        result.update(extra)
    return result


_PRIORITY_SUFFIX_DELIMITERS = ("系列", "类", "批次", "组", "级", "-", "_", ":", "：", " ", "(", "（")


def _priority_rank(tier_value: str, priority_order: tuple[str, ...]) -> int:
    normalized = tier_value.strip()
    if not normalized or normalized == EMPTY:
        return len(priority_order)
    norm_lower = normalized.lower()
    for idx, tier in enumerate(priority_order):
        tier_clean = tier.strip()
        tier_lower = tier_clean.lower()
        if norm_lower == tier_lower:
            return idx
        if norm_lower.startswith(tier_lower):
            rest = normalized[len(tier_clean):]
            if any(rest.startswith(delim) for delim in _PRIORITY_SUFFIX_DELIMITERS) or not rest[0].isalpha():
                return idx
    return len(priority_order)


def select_claimable_rows(config: RuntimeConfig, table: MarkdownTable, item_id: str | None) -> list[list[str]]:
    if item_id is not None or not config.priority_order or table.priority_column is None:
        return table.rows
    return sorted(
        table.rows,
        key=lambda row: _priority_rank(get_row_value(table, row, table.priority_column), config.priority_order),
    )


def _apply_remark_update(
    table: MarkdownTable,
    row: list[str],
    *,
    remark: str | None = None,
    append_remark: str | None = None,
) -> bool:
    if table.remark_column is None:
        return False
    changed = False
    if remark is not None:
        new_val = remark.strip()
        if get_row_value(table, row, table.remark_column) != new_val:
            set_row_value(table, row, table.remark_column, new_val)
            changed = True
    if append_remark is not None:
        extra = append_remark.strip()
        if extra:
            current = get_row_value(table, row, table.remark_column).strip()
            if not current or current == EMPTY:
                combined = extra
            elif extra not in current:
                combined = f"{current}；{extra}"
            else:
                combined = current
            if get_row_value(table, row, table.remark_column) != combined:
                set_row_value(table, row, table.remark_column, combined)
                changed = True
    return changed


def _candidate_tokens_for_row(table: MarkdownTable, row: list[str] | None, resolved_id: str | None) -> tuple[str, ...]:
    tokens: list[str] = []
    if resolved_id is not None:
        tokens.append(resolved_id)
    if row is not None and table.module_column is not None:
        mod_val = normalize_optional_text(get_row_value(table, row, table.module_column))
        if mod_val is not None and mod_val not in tokens:
            tokens.append(mod_val)
    return tuple(tokens)


def reopen_work_item(
    config: RuntimeConfig,
    worker: str,
    *,
    item_id: str | None = None,
    module: str | None = None,
    reason: str | None = None,
    remark: str | None = None,
    append_remark: str | None = None,
    force_active: bool = False,
    claim: bool = False,
    user_authorization_ref: str | None = None,
) -> dict[str, Any]:
    raw_id = normalize_optional_text(item_id)
    raw_mod = normalize_optional_text(module)
    if raw_id is None and raw_mod is None:
        raise ValueError("Reopening a work item requires --item-id or --module.")
    auth_ref = normalize_optional_text(user_authorization_ref) or normalize_optional_text(reason)

    table_preview = load_markdown_table(config.work_items_path, config.columns)
    resolved_id = table_preview.resolve_item_id(item_id=raw_id, module=raw_mod)
    assert resolved_id is not None
    preview_row = table_preview.get_row(resolved_id)
    tokens = _candidate_tokens_for_row(table_preview, preview_row, resolved_id)
    validate_orchestration_action(
        config,
        worker,
        "reopen",
        item_id=resolved_id,
        module=raw_mod,
        user_authorization_ref=auth_ref,
        candidate_tokens=tokens,
    )
    access_binding = validate_mutation_access(config, worker, "claim" if claim else None)

    with write_mutex(config, worker):
        access_binding = validate_mutation_access(config, worker, "claim" if claim else None)
        table = load_markdown_table(config.work_items_path, config.columns)
        target_id = table.resolve_item_id(item_id=raw_id, module=raw_mod)
        assert target_id is not None
        row = table.get_row(target_id)
        if row is None:
            raise ValueError(f"Work item '{target_id}' was not found in {config.work_items_path.name}.")

        if config.orchestration_policy is not None and auth_ref is not None:
            row_tokens = _candidate_tokens_for_row(table, row, target_id)
            if not set(row_tokens).intersection(config.orchestration_policy.user_authorized_item_ids):
                _persist_user_authorization_locked(config, target_id, auth_ref)

        sync_result = refresh_locked(config, table, now_local(), preserve_worker=worker)
        active = sync_result["active"]

        if target_id in active:
            current_owner = active[target_id].get("worker")
            if current_owner != worker and not force_active:
                raise PermissionError(
                    f"Work item '{target_id}' is currently active under worker '{current_owner}'; "
                    "pass --force-active if that session was abandoned and the user authorized takeover."
                )
            try_unlink_runtime_file(active[target_id]["__path"], missing_ok=True)
            active.pop(target_id, None)

        if config.access_policy is not None and config.access_policy.completion_receipt is not None:
            prev_worker = normalize_optional_text(get_row_value(table, row, table.worker_column)) or worker
            for prev_status in ("done", "blocked"):
                try:
                    stale_receipt = resolve_completion_path(
                        config,
                        config.access_policy.completion_receipt.path_template,
                        {
                            "worker": prev_worker,
                            "work_item_id": target_id,
                            "status": prev_status,
                            "epic_id": config.epic_id,
                            "label": config.label,
                        },
                        field_name="access_policy.completion_receipt.path_template",
                    )
                    if stale_receipt.is_file():
                        try_unlink_runtime_file(stale_receipt, missing_ok=True)
                except Exception:
                    pass

        _apply_remark_update(table, row, remark=remark, append_remark=append_remark)
        reason_text = normalize_optional_text(reason) or normalize_optional_text(user_authorization_ref)
        reopen_note = f"已重开：{reason_text}" if reason_text else "已重开，待重新领取。"

        if not claim:
            clear_runtime_fields(table, row, reopen_note)
            write_markdown_table(config.work_items_path, table)
            return finalize_result(config, table, row, "reopened", worker=worker)

        if config.orchestration_policy is not None:
            worker_claims = [c for c in active.values() if c.get("worker") == worker]
            limit = config.orchestration_policy.max_active_claims_per_worker
            if len(worker_claims) >= limit:
                raise PermissionError(
                    f"Worker '{worker}' already owns active item "
                    f"'{worker_claims[0].get('work_item_id')}'; complete or release it before claiming '{target_id}'."
                )
        elif config.access_policy is not None:
            worker_claim_count = sum(1 for c in active.values() if c.get("worker") == worker)
            if worker_claim_count >= config.access_policy.max_active_claims_per_worker:
                raise PermissionError(
                    f"Worker '{worker}' already owns the configured maximum of "
                    f"{config.access_policy.max_active_claims_per_worker} active claim(s)."
                )
            if not config.access_policy.queue_open:
                raise PermissionError("The work item queue is closed; creating a new claim is forbidden.")

        claimed_at = now_local()
        claim_payload = build_claim_payload(
            config,
            target_id,
            worker,
            claimed_at,
            access_binding=access_binding,
        )
        target_claim_path = claim_file_path(config, target_id)
        try_unlink_runtime_file(target_claim_path, missing_ok=True)
        create_claim(target_claim_path, claim_payload)
        set_row_value(table, row, table.state_column, STATE_ACTIVE)
        set_row_value(table, row, table.worker_column, worker)
        set_row_value(table, row, table.started_at_column, format_local(claimed_at))
        set_row_value(table, row, table.completed_at_column, EMPTY)
        if table.remark_column != table.note_column:
            claim_reopen_note = f"已重开并领取：{reason_text}" if reason_text else EMPTY
            set_row_value(table, row, table.note_column, claim_reopen_note)
        write_markdown_table(config.work_items_path, table)
        claim_info = verify_claim_file(config, target_id, worker)
        return finalize_result(config, table, row, "reopened_and_claimed", worker=worker, claim_info=claim_info)


def claim_next(
    config: RuntimeConfig,
    worker: str,
    item_id: str | None = None,
    *,
    module: str | None = None,
    reopen: bool = False,
    force_active: bool = False,
    reason: str | None = None,
    remark: str | None = None,
    append_remark: str | None = None,
    user_authorization_ref: str | None = None,
) -> dict[str, Any]:
    if reopen:
        return reopen_work_item(
            config,
            worker,
            item_id=item_id,
            module=module,
            reason=reason,
            remark=remark,
            append_remark=append_remark,
            force_active=force_active,
            claim=True,
            user_authorization_ref=user_authorization_ref,
        )

    raw_id = normalize_optional_text(item_id)
    raw_mod = normalize_optional_text(module)
    resolved_target = raw_id
    candidate_tokens: tuple[str, ...] = ()
    if raw_id is not None or raw_mod is not None:
        table_preview = load_markdown_table(config.work_items_path, config.columns)
        resolved_target = table_preview.resolve_item_id(item_id=raw_id, module=raw_mod)
        preview_row = table_preview.get_row(resolved_target) if resolved_target is not None else None
        candidate_tokens = _candidate_tokens_for_row(table_preview, preview_row, resolved_target)

    validate_orchestration_action(
        config,
        worker,
        "claim",
        item_id=resolved_target,
        module=raw_mod,
        user_authorization_ref=user_authorization_ref,
        candidate_tokens=candidate_tokens,
    )
    access_binding = validate_mutation_access(config, worker, "claim")
    if config.access_policy is not None:
        active_before_lock, _ = active_claims(config, now_local())
        worker_claims = [claim for claim in active_before_lock.values() if claim.get("worker") == worker]
        if (
            config.orchestration_policy is not None
            and config.orchestration_policy.max_active_claims_per_worker == 1
            and resolved_target is not None
            and worker_claims
            and worker_claims[0].get("work_item_id") != resolved_target
        ):
            raise PermissionError(
                f"Worker '{worker}' already owns active item "
                f"'{worker_claims[0].get('work_item_id')}'; complete or release it before claiming '{resolved_target}'."
            )
        matching_claim = next(
            (
                claim
                for claim in worker_claims
                if resolved_target is None or claim.get("work_item_id") == resolved_target
            ),
            None,
        )
        if matching_claim is None:
            if len(worker_claims) >= config.access_policy.max_active_claims_per_worker:
                raise PermissionError(
                    f"Worker '{worker}' already owns the configured maximum of "
                    f"{config.access_policy.max_active_claims_per_worker} active claim(s)."
                )
            if not config.access_policy.queue_open:
                raise PermissionError("The work item queue is closed; creating a new claim is forbidden.")

    with write_mutex(config, worker):
        access_binding = validate_mutation_access(config, worker, "claim")
        table = load_markdown_table(config.work_items_path, config.columns)
        if raw_id is not None or raw_mod is not None:
            resolved_target = table.resolve_item_id(item_id=raw_id, module=raw_mod)
        if (
            config.orchestration_policy is not None
            and resolved_target is not None
            and normalize_optional_text(user_authorization_ref) is not None
        ):
            target_row = table.get_row(resolved_target)
            row_tokens = _candidate_tokens_for_row(table, target_row, resolved_target)
            if not set(row_tokens).intersection(config.orchestration_policy.user_authorized_item_ids):
                _persist_user_authorization_locked(config, resolved_target, user_authorization_ref)

        sync_result = refresh_locked(config, table, now_local(), preserve_worker=worker)
        active = sync_result["active"]
        if config.access_policy is not None and not config.access_policy.queue_open:
            has_reentry = any(
                claim.get("worker") == worker
                and (resolved_target is None or claim.get("work_item_id") == resolved_target)
                for claim in active.values()
            )
            if not has_reentry:
                raise PermissionError("The work item queue is closed; creating a new claim is forbidden.")

        if config.orchestration_policy is not None:
            worker_claims = [claim for claim in active.values() if claim.get("worker") == worker]
            limit = config.orchestration_policy.max_active_claims_per_worker
            if len(worker_claims) > limit:
                raise ValueError(
                    f"Strict worker '{worker}' owns multiple active claims; repair the queue before continuing."
                )
            if resolved_target is None and len(worker_claims) > 1:
                raise ValueError("Multiple active claims: specify --item-id to reenter one owned item.")
            existing = next((c for c in worker_claims if resolved_target is None or c.get("work_item_id") == resolved_target), None)
            if limit == 1 and worker_claims and resolved_target is not None and existing is None:
                raise PermissionError(
                    f"Worker '{worker}' already owns active item '{worker_claims[0].get('work_item_id')}'; "
                    f"complete or release it before claiming '{resolved_target}'."
                )
            if existing is None and len(worker_claims) >= limit:
                raise PermissionError(f"Worker '{worker}' already owns the configured maximum of {limit} active claim(s).")
        elif config.access_policy is None:
            existing = resolve_worker_claim(active, worker, resolved_target)
        else:
            worker_claims = [claim for claim in active.values() if claim.get("worker") == worker]
            existing = next(
                (
                    claim
                    for claim in worker_claims
                    if resolved_target is None or claim.get("work_item_id") == resolved_target
                ),
                None,
            )
        if existing:
            verify_claim_access_binding(config, existing, access_binding)
            _heartbeat_claim_locked(config, worker, item_id=existing["work_item_id"])
            row = table.get_row(existing["work_item_id"])
            if row is None:
                raise ValueError(f"Claimed work item {existing['work_item_id']} is missing from markdown.")
            if _apply_remark_update(table, row, remark=remark, append_remark=append_remark):
                sync_result["changed"] = True
            claim_info = verify_claim_file(config, existing["work_item_id"], worker)
            if sync_result["changed"]:
                write_markdown_table(config.work_items_path, table)
            return finalize_result(config, table, row, "reentered", worker=worker, claim_info=claim_info)

        if config.access_policy is not None:
            worker_claim_count = sum(1 for claim in active.values() if claim.get("worker") == worker)
            if worker_claim_count >= config.access_policy.max_active_claims_per_worker:
                raise PermissionError(
                    f"Worker '{worker}' already owns the configured maximum of "
                    f"{config.access_policy.max_active_claims_per_worker} active claim(s)."
                )
            if not config.access_policy.queue_open:
                if sync_result["changed"]:
                    write_markdown_table(config.work_items_path, table)
                raise PermissionError("The work item queue is closed; creating a new claim is forbidden.")

        if resolved_target is not None:
            target_row = table.get_row(resolved_target)
            if target_row is None:
                if sync_result["changed"]:
                    write_markdown_table(config.work_items_path, table)
                raise ValueError(f"Work item '{resolved_target}' was not found in {config.work_items_path.name}.")
            target_state = normalize_state(get_row_value(table, target_row, table.state_column))
            if target_state in TERMINAL_STATES:
                if sync_result["changed"]:
                    write_markdown_table(config.work_items_path, table)
                raise ValueError(
                    f"Work item '{resolved_target}' is already in terminal state '{target_state}'; "
                    "pass --reopen (or run reopen_work_item.py) if the user instructed to rework it."
                )
            if resolved_target in active:
                other_worker = active[resolved_target].get("worker")
                if sync_result["changed"]:
                    write_markdown_table(config.work_items_path, table)
                raise ValueError(
                    f"Work item '{resolved_target}' is currently active under another worker ('{other_worker}'); "
                    "pass --reopen --force-active if that session was abandoned and the user authorized takeover."
                )

        system_notes = {
            config.note_messages.lease_expired,
            config.note_messages.claim_missing,
            config.note_messages.released,
            config.note_messages.terminal_unverified,
        }
        for row in select_claimable_rows(config, table, resolved_target):
            candidate_id = get_row_value(table, row, table.id_column)
            state = normalize_state(get_row_value(table, row, table.state_column))
            if candidate_id in active or state in TERMINAL_STATES:
                continue
            if resolved_target and candidate_id != resolved_target:
                continue
            claimed_at = now_local()
            access_binding = validate_mutation_access(config, worker, "claim")
            claim = build_claim_payload(
                config,
                candidate_id,
                worker,
                claimed_at,
                access_binding=access_binding,
            )
            create_claim(claim_file_path(config, candidate_id), claim)
            set_row_value(table, row, table.state_column, STATE_ACTIVE)
            set_row_value(table, row, table.worker_column, worker)
            set_row_value(table, row, table.started_at_column, format_local(claimed_at))
            set_row_value(table, row, table.completed_at_column, EMPTY)
            _apply_remark_update(table, row, remark=remark, append_remark=append_remark)
            current_note = get_row_value(table, row, table.note_column)
            if table.remark_column != table.note_column or current_note in system_notes:
                set_row_value(table, row, table.note_column, EMPTY)
            write_markdown_table(config.work_items_path, table)
            claim_info = verify_claim_file(config, candidate_id, worker)
            return finalize_result(config, table, row, "claimed", worker=worker, claim_info=claim_info)

        if sync_result["changed"]:
            write_markdown_table(config.work_items_path, table)
        raise ValueError("No available work items.")


def _heartbeat_claim_locked(
    config: RuntimeConfig,
    worker: str,
    item_id: str | None = None,
    lease_seconds_override: int | None = None,
) -> dict[str, Any]:
    access_binding = validate_mutation_access(config, worker, "heartbeat")
    now = now_local()
    table = load_markdown_table(config.work_items_path, config.columns)
    active, expired = active_claims(config, now)
    claim = resolve_worker_claim(active, worker, item_id)
    if claim is None:
        for exp_item in expired:
            exp_claim = exp_item.get("claim") or {}
            exp_id = exp_claim.get("work_item_id")
            if (
                exp_item.get("reason") == "time_expired"
                and exp_id
                and exp_id not in active
                and exp_claim.get("worker") == worker
                and (item_id is None or exp_id == item_id)
            ):
                row = table.get_row(exp_id)
                if (
                    row is not None
                    and normalize_state(get_row_value(table, row, table.state_column)) == STATE_ACTIVE
                    and get_row_value(table, row, table.worker_column) in {worker, "", EMPTY}
                ):
                    claim = exp_claim
                    break
    if claim is None:
        raise ValueError(f"No active claim found for worker '{worker}'.")
    verify_claim_access_binding(config, claim, access_binding)

    lease_seconds = int(lease_seconds_override or config.lease_seconds)
    claim["heartbeat_at"] = now.isoformat()
    claim["expires_at"] = (now + timedelta(seconds=lease_seconds)).isoformat()
    access_binding = validate_mutation_access(config, worker, "heartbeat")
    verify_claim_access_binding(config, claim, access_binding)
    write_claim(claim["__path"], {key: value for key, value in claim.items() if not key.startswith("__")})
    claim_info = verify_claim_file(config, claim["work_item_id"], worker)

    row = table.get_row(claim["work_item_id"])
    if row is not None:
        return finalize_result(
            config,
            table,
            row,
            "heartbeat",
            worker=worker,
            claim_info=claim_info,
        )
    return {
        "epic_id": config.epic_id,
        "mode": "heartbeat",
        "work_item_id": claim["work_item_id"],
        "resolved_worker": worker,
        "configured_worker_env_var": config.worker_env_var,
        **claim_info,
    }


def heartbeat_claim(
    config: RuntimeConfig,
    worker: str,
    item_id: str | None = None,
    lease_seconds_override: int | None = None,
    *,
    module: str | None = None,
) -> dict[str, Any]:
    resolved_id = normalize_optional_text(item_id)
    if normalize_optional_text(module) is not None or resolved_id is not None:
        table_preview = load_markdown_table(config.work_items_path, config.columns)
        resolved_id = table_preview.resolve_item_id(item_id=resolved_id, module=module)
    validate_orchestration_action(config, worker, "heartbeat", item_id=resolved_id, module=module)
    validate_mutation_access(config, worker, "heartbeat")
    with write_mutex(config, worker):
        return _heartbeat_claim_locked(
            config,
            worker,
            item_id=resolved_id,
            lease_seconds_override=lease_seconds_override,
        )


def complete_claim(
    config: RuntimeConfig,
    worker: str,
    status: str,
    item_id: str | None = None,
    note: str | None = None,
    *,
    module: str | None = None,
) -> dict[str, Any]:
    resolved_id = normalize_optional_text(item_id)
    if normalize_optional_text(module) is not None or resolved_id is not None:
        table_preview = load_markdown_table(config.work_items_path, config.columns)
        resolved_id = table_preview.resolve_item_id(item_id=resolved_id, module=module)
    validate_orchestration_action(config, worker, "complete", item_id=resolved_id, module=module)
    state_map = {
        "done": STATE_DONE,
        "blocked": STATE_BLOCKED,
        "open": STATE_OPEN,
    }
    if status not in state_map:
        raise ValueError(f"Unsupported status '{status}'. Use one of: {sorted(state_map)}")

    access_binding = validate_mutation_access(config, worker, "complete")
    claim_before_lock = None
    receipt_info = None
    if config.access_policy is not None:
        now = now_local()
        active_before_lock, _ = active_claims(config, now)
        claim_before_lock = resolve_worker_claim(active_before_lock, worker, resolved_id)
        if claim_before_lock is None:
            claim_before_lock = resolve_expired_worker_claim(config, worker, resolved_id)
        if claim_before_lock is None:
            raise ValueError(f"No active claim found for worker '{worker}'.")
        verify_claim_access_binding(config, claim_before_lock, access_binding)
        receipt_info = verify_completion_receipt(
            config,
            worker,
            claim_before_lock["work_item_id"],
            status,
            claim_before_lock,
        )

    with write_mutex(config, worker):
        access_binding = validate_mutation_access(config, worker, "complete")
        if config.access_policy is not None:
            assert claim_before_lock is not None
            current_claim_path = claim_file_path(config, claim_before_lock["work_item_id"])
            if not current_claim_path.exists():
                raise ValueError(f"Claim file is missing before completion: {current_claim_path}")
            current_claim = load_claim(current_claim_path)
            verify_claim_access_binding(config, current_claim, access_binding)
            receipt_info = verify_completion_receipt(
                config,
                worker,
                claim_before_lock["work_item_id"],
                status,
                current_claim,
            )
        table = load_markdown_table(config.work_items_path, config.columns)
        sync_result = refresh_locked(config, table, now_local(), preserve_worker=worker)
        active = sync_result["active"]
        claim = resolve_worker_claim(active, worker, resolved_id)
        if claim is None:
            claim = resolve_expired_worker_claim(config, worker, resolved_id)
        if claim is None:
            raise ValueError(f"No active claim found for worker '{worker}'.")

        row = table.get_row(claim["work_item_id"])
        if row is None:
            raise ValueError(f"Claimed work item {claim['work_item_id']} is missing from markdown.")
        claim_info = verify_claim_file(config, claim["work_item_id"], worker)

        completed_at = now_local()
        target_state = state_map[status]
        if target_state == STATE_OPEN:
            clear_runtime_fields(table, row, note or config.note_messages.released)
        else:
            set_row_value(table, row, table.state_column, target_state)
            set_row_value(table, row, table.worker_column, worker)
            if get_row_value(table, row, table.started_at_column) in {"", EMPTY}:
                set_row_value(table, row, table.started_at_column, format_local(parse_iso(claim["claimed_at"])))
            set_row_value(table, row, table.completed_at_column, format_local(completed_at))
            if note:
                set_row_value(table, row, table.note_column, note)

        write_markdown_table(config.work_items_path, table)
        if not try_unlink_runtime_file(claim["__path"], missing_ok=True):
            raise OSError(
                "Work-item table was persisted but the claim could not be removed; retry completion "
                f"for item '{claim['work_item_id']}'."
            )
        return finalize_result(
            config,
            table,
            row,
            f"completed:{status}",
            worker=worker,
            claim_info={
                "claim_file": claim_info["claim_file"],
                "claim_verified_before_action": claim_info["claim_verified"],
                "claim_file_exists_after_action": claim_file_path(config, claim["work_item_id"]).exists(),
            },
            extra={"completed_status": status, **(receipt_info or {})},
        )


def refresh_work_items(config: RuntimeConfig, worker: str = "system") -> dict[str, Any]:
    validate_orchestration_action(config, worker, "refresh")
    validate_mutation_access(config, worker, "refresh")
    with write_mutex(config, worker):
        validate_mutation_access(config, worker, "refresh")
        table = load_markdown_table(config.work_items_path, config.columns)
        sync_result = refresh_locked(config, table, now_local())
        if sync_result["changed"]:
            write_markdown_table(config.work_items_path, table)
        return {
            "epic_id": config.epic_id,
            "mode": "refresh",
            "changed": sync_result["changed"],
            "active_count": sync_result["active_count"],
            "expired_count": sync_result["expired_count"],
            "work_items_path": str(config.work_items_path),
            "resolved_worker": worker,
            "configured_worker_env_var": config.worker_env_var,
        }
