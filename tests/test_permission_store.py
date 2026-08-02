from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from artpm_agent.security.permission_store import (
    PermissionBindingError,
    PermissionConflictError,
    PermissionNotFoundError,
    PermissionStore,
    PermissionValidationError,
    canonical_payload_sha256,
)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


def create_request(store: PermissionStore, **overrides):
    values = {
        "workspace_id": "workspace-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "agent_id": "planner",
        "source": "skill",
        "action": "files.create",
        "resource": {"path": "reports/plan.xlsx"},
        "risk": "medium",
        "required_role": "user",
        "payload": {"rows": 3},
        "idempotency_key": "run-1:call-1",
    }
    values.update(overrides)
    return store.create_request(**values)


def test_create_request_is_idempotent_and_persists_only_redacted_json(tmp_path: Path):
    db_path = tmp_path / "permissions.db"
    store = PermissionStore(db_path)
    payload = {
        "rows": 3,
        "credentials": {
            "api_key": "api-secret-value",
            "nested": [{"password": "password-secret-value"}],
        },
    }

    first = create_request(store, payload=payload)
    repeated = create_request(
        store,
        payload={"credentials": payload["credentials"], "rows": 3},
    )

    assert repeated.id == first.id
    assert repeated.payload_sha256 == first.payload_sha256
    assert first.payload["credentials"]["api_key"] == "api-secret-value"
    assert first.redacted_arguments["credentials"] == "[REDACTED]"
    assert first.payload_sha256 == canonical_payload_sha256(payload)
    assert "api-secret-value" not in repr(first)
    assert "payload" not in first.to_dict()
    assert first.to_dict()["redacted_arguments"]["credentials"] == "[REDACTED]"

    with closing(sqlite3.connect(db_path)) as connection, connection:
        raw_payload, redacted = connection.execute(
            """
            SELECT payload_json, redacted_arguments_json
            FROM permission_requests WHERE id = ?
            """,
            (first.id,),
        ).fetchone()
    assert json.loads(raw_payload)["credentials"]["api_key"] == "api-secret-value"
    assert "api-secret-value" not in redacted
    assert "password-secret-value" not in redacted
    assert json.loads(redacted)["credentials"] == "[REDACTED]"


def test_idempotency_key_and_hash_are_bound_to_exact_action(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")
    first = create_request(store, payload={"b": 2, "a": 1})
    reordered = create_request(store, payload={"a": 1, "b": 2})

    assert reordered.id == first.id
    assert reordered.action_sha256 == first.action_sha256

    with pytest.raises(PermissionBindingError, match="different action"):
        create_request(store, payload={"a": 1, "b": 3})
    with pytest.raises(PermissionBindingError, match="different action"):
        create_request(store, resource={"path": "reports/other.xlsx"})


def test_approval_is_claimed_once_and_completed_with_redacted_result(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")
    request = create_request(store)
    approved = store.decide(
        request.id,
        decision="approved",
        actor_id="user-1",
        actor_role="user",
        expected_version=request.state_version,
    )
    claimed = store.claim_execution(
        request.id,
        execution_id="execution-1",
        expected_version=approved.state_version,
        expected_payload_sha256=request.payload_sha256,
        expected_action_sha256=request.action_sha256,
    )

    assert claimed.status == "executing"
    with pytest.raises(PermissionConflictError, match="unconsumed approval"):
        store.claim_execution(
            request.id,
            execution_id="execution-2",
            expected_version=claimed.state_version,
        )

    completed = store.complete_execution(
        request.id,
        execution_id="execution-1",
        success=True,
        expected_version=claimed.state_version,
        result={"path": "reports/plan.xlsx", "access_token": "do-not-store"},
    )
    assert completed.status == "completed"
    assert completed.result["access_token"] == "[REDACTED]"
    assert completed.state_version == 3


def test_rejected_and_expired_requests_cannot_execute(tmp_path: Path):
    clock = MutableClock()
    store = PermissionStore(tmp_path / "permissions.db", clock=clock)
    rejected_request = create_request(store, idempotency_key="reject")
    rejected = store.decide(
        rejected_request.id,
        decision="rejected",
        actor_id="user-1",
        actor_role="user",
        expected_version=0,
    )
    assert rejected.status == "rejected"
    with pytest.raises(PermissionConflictError, match="unconsumed approval"):
        store.claim_execution(
            rejected.id,
            execution_id="execution-rejected",
            expected_version=rejected.state_version,
        )

    expiring = create_request(store, idempotency_key="expire", ttl_seconds=5)
    assert [item.id for item in store.list_pending()] == [expiring.id]
    clock.advance(6)

    assert store.list_pending() == []
    assert store.get(expiring.id).status == "expired"
    with pytest.raises(PermissionConflictError, match="no longer pending"):
        store.decide(
            expiring.id,
            decision="approved",
            actor_id="user-1",
            actor_role="user",
            expected_version=expiring.state_version,
        )


def test_admin_requirement_and_execution_hash_mismatch_fail_closed(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")
    request = create_request(
        store,
        risk="critical",
        required_role="admin",
        idempotency_key="admin-action",
    )
    with pytest.raises(PermissionError, match="below the required role"):
        store.decide(
            request.id,
            decision="approved",
            actor_id="user-1",
            actor_role="user",
            expected_version=0,
        )
    with pytest.raises(PermissionValidationError, match="explicit acknowledgement"):
        store.decide(
            request.id,
            decision="approved",
            actor_id="admin-1",
            actor_role="admin",
            expected_version=0,
        )
    approved = store.decide(
        request.id,
        decision="approved",
        actor_id="admin-1",
        actor_role="admin",
        expected_version=0,
        acknowledged_risk="critical",
    )
    with pytest.raises(PermissionBindingError, match="execution payload"):
        store.claim_execution(
            request.id,
            execution_id="execution-admin",
            expected_version=approved.state_version,
            expected_payload_sha256="0" * 64,
        )


def test_severe_risk_cannot_be_downgraded_to_user_role(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")

    request = create_request(
        store,
        risk="untrusted",
        required_role="user",
        idempotency_key="role-floor",
    )

    assert request.required_role == "admin"


def test_double_click_decision_has_one_cas_winner(tmp_path: Path):
    store = PermissionStore(tmp_path / "permissions.db")
    request = create_request(store)

    def decide(decision: str):
        try:
            return store.decide(
                request.id,
                decision=decision,
                actor_id=f"actor-{decision}",
                actor_role="user",
                expected_version=request.state_version,
            )
        except PermissionConflictError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decide, ("approved", "rejected")))

    winners = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, PermissionConflictError)]
    assert len(winners) == 1
    assert len(conflicts) == 1
    assert store.get(request.id).status in {"approved", "rejected"}


def test_wrong_workspace_cannot_expire_or_transition_another_request(tmp_path: Path):
    clock = MutableClock()
    db_path = tmp_path / "permissions.db"
    store = PermissionStore(db_path, clock=clock)
    request = create_request(store, ttl_seconds=5)
    clock.advance(6)

    assert store.get(request.id, workspace_id="workspace-2") is None
    with closing(sqlite3.connect(db_path)) as connection:
        status = connection.execute(
            "SELECT status FROM permission_requests WHERE id = ?", (request.id,)
        ).fetchone()[0]
    assert status == "pending"
    assert store.get(request.id, workspace_id="workspace-1").status == "expired"

    active = create_request(
        store,
        idempotency_key="workspace-cas",
        ttl_seconds=60,
    )
    with pytest.raises(PermissionNotFoundError):
        store.decide(
            active.id,
            decision="approved",
            actor_id="user-2",
            actor_role="user",
            expected_version=active.state_version,
            workspace_id="workspace-2",
        )
    unchanged = store.get(active.id, workspace_id="workspace-1")
    assert unchanged.status == "pending"
    assert unchanged.state_version == active.state_version


def test_static_server_deny_cannot_be_requested_or_claimed(tmp_path: Path):
    db_path = tmp_path / "permissions.db"
    store = PermissionStore(db_path)
    with pytest.raises(PermissionError, match="server risk policy"):
        create_request(
            store,
            action="tool.execute_command",
            idempotency_key="denied-command",
        )
    assert store.list_pending(workspace_id="workspace-1") == []

    request = create_request(store, idempotency_key="legacy-request")
    approved = store.decide(
        request.id,
        decision="approved",
        actor_id="admin-1",
        actor_role="admin",
        expected_version=request.state_version,
        workspace_id=request.workspace_id,
    )
    # Simulate a pending row written before the static policy deny was added.
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute(
            "UPDATE permission_requests SET action = 'tool.execute_command' WHERE id = ?",
            (request.id,),
        )
    with pytest.raises(PermissionError, match="server risk policy"):
        store.claim_execution(
            request.id,
            execution_id="denied-execution",
            expected_version=approved.state_version,
            workspace_id=request.workspace_id,
        )
    assert store.get(request.id, workspace_id=request.workspace_id).status == "approved"


def test_json_boundary_rejects_cycles_non_string_keys_and_non_finite_numbers(
    tmp_path: Path,
):
    store = PermissionStore(tmp_path / "permissions.db")
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic

    for payload in (cyclic, {1: "bad-key"}, {"amount": float("nan")}):
        with pytest.raises(PermissionValidationError):
            create_request(
                store,
                payload=payload,
                idempotency_key=f"invalid-{id(payload)}",
            )
