"""Serialization and SQLite row codecs for workflow persistence."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .models import (
    WorkflowApproval,
    WorkflowDefinition,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStepRun,
)


def encode_json(value: Any, *, max_bytes: int) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(raw.encode("utf-8")) > max_bytes:
        raise ValueError(f"workflow JSON payload exceeds {max_bytes} bytes")
    return raw


def decode_json(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    return json.loads(raw)


def definition_json(definition: WorkflowDefinition, *, max_bytes: int) -> str:
    return encode_json(definition.model_dump(mode="json"), max_bytes=max_bytes)


def definition_checksum(
    definition: WorkflowDefinition,
    *,
    max_bytes: int,
) -> str:
    raw = definition_json(definition, max_bytes=max_bytes)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def resolved_definition_from_row(row: sqlite3.Row) -> WorkflowDefinition:
    definition = WorkflowDefinition.model_validate_json(row["definition_json"])
    definition = definition.model_copy(
        update={
            "tenant_id": row["tenant_id"],
            "workspace_id": row["workspace_id"],
            "profile_id": row["profile_id"],
        }
    )
    updates: dict[str, Any] = {}
    if row["override_enabled"] is not None:
        updates["enabled"] = bool(row["override_enabled"])
    if row["override_priority"] is not None:
        updates["priority"] = int(row["override_priority"])
    if not updates:
        return definition
    return WorkflowDefinition.model_validate(
        {**definition.model_dump(mode="python"), **updates}
    )


def run_from_row(row: sqlite3.Row) -> WorkflowRun:
    return WorkflowRun(
        id=row["id"],
        idempotency_key=row["idempotency_key"],
        workflow_id=row["workflow_id"],
        workflow_version=int(row["workflow_version"]),
        workspace_id=row["workspace_id"],
        profile_id=row["profile_id"],
        tenant_id=row["tenant_id"],
        conversation_id=row["conversation_id"],
        turn_id=row["turn_id"],
        status=row["status"],
        state_version=int(row["state_version"]),
        current_step=int(row["current_step"]),
        definition_snapshot=WorkflowDefinition.model_validate_json(
            row["definition_snapshot_json"]
        ),
        input_data=decode_json(row["input_json"], {}),
        context_data=decode_json(row["context_json"], {}),
        outputs=decode_json(row["outputs_json"], {}),
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def step_from_row(row: sqlite3.Row) -> WorkflowStepRun:
    return WorkflowStepRun(
        run_id=row["run_id"],
        tenant_id=row["tenant_id"],
        step_index=int(row["step_index"]),
        step_id=row["step_id"],
        skill_id=row["skill_id"],
        status=row["status"],
        state_version=int(row["state_version"]),
        attempt=int(row["attempt"]),
        input_data=decode_json(row["input_json"], {}),
        output_data=decode_json(row["output_json"], {}),
        error=row["error"],
        error_class=row["error_class"],
        next_retry_at=row["next_retry_at"],
        compensation_skill_id=row["compensation_skill_id"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def approval_from_row(row: sqlite3.Row) -> WorkflowApproval:
    return WorkflowApproval(
        id=row["id"],
        run_id=row["run_id"],
        tenant_id=row["tenant_id"],
        step_index=int(row["step_index"]),
        requirement=row["requirement"],
        status=row["status"],
        actor=row["actor"],
        actor_level=row["actor_level"],
        note=row["note"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
    )


def event_from_row(row: sqlite3.Row) -> WorkflowEvent:
    return WorkflowEvent(
        id=int(row["id"]),
        run_id=row["run_id"],
        tenant_id=row["tenant_id"],
        event_type=row["event_type"],
        payload=decode_json(row["payload_json"], {}),
        created_at=row["created_at"],
    )


__all__ = [
    "approval_from_row",
    "decode_json",
    "definition_checksum",
    "definition_json",
    "encode_json",
    "event_from_row",
    "resolved_definition_from_row",
    "run_from_row",
    "step_from_row",
]
