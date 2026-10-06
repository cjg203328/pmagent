"""The API host must feed the conversation's persisted access mode to the gate.

These tests pin the trust boundary: the approval gate reads its policy from the
conversation's own grant row, and every failure path falls back to
``controlled``. A regression here would either silently ignore the operator's
choice or, worse, relax the gate when the store misbehaves.
"""

from __future__ import annotations

from artpm_agent.api.services import (
    ChatCommand,
    DefaultGatewayRuntime,
    RequestPrincipal,
)
from artpm_agent.security import (
    ACCESS_MODE_CONTROLLED,
    ACCESS_MODE_FULL,
    ACCESS_MODE_READ_ONLY,
)


class _StubPermissions:
    def __init__(self, grant=None, error=None):
        self._grant = grant
        self._error = error

    def get_conversation_access_mode(self, *_args, **_kwargs):
        if self._error is not None:
            raise self._error
        return self._grant


class _HostWithoutAccessMode:
    """A host adapter that predates the access-mode contract."""

    def __init__(self):
        self.create_request = None


def _services(tmp_path, permissions):
    runtime = DefaultGatewayRuntime(db_path=tmp_path / "runtime.db")
    runtime.permissions = permissions
    return runtime


def _command(conversation_id="conv-1"):
    return ChatCommand(
        principal=RequestPrincipal(workspace_id="local-default", actor_id="local-ui"),
        conversation_id=conversation_id,
        turn_id="turn-1",
        message="hi",
    )


def test_persisted_modes_are_passed_through_unchanged(tmp_path):
    for mode in (ACCESS_MODE_READ_ONLY, ACCESS_MODE_CONTROLLED, ACCESS_MODE_FULL):
        services = _services(tmp_path, _StubPermissions(grant={"mode": mode}))
        assert services._conversation_access_mode(_command()) == mode


def test_unknown_or_missing_grant_fails_closed_to_controlled(tmp_path):
    cases = [
        _StubPermissions(grant={"mode": "forged"}),
        _StubPermissions(grant={"mode": None}),
        _StubPermissions(grant={}),
        _StubPermissions(grant=None),
        _StubPermissions(grant="not-a-mapping"),
    ]
    for permissions in cases:
        services = _services(tmp_path, permissions)
        assert services._conversation_access_mode(_command()) == ACCESS_MODE_CONTROLLED


def test_store_failure_never_relaxes_the_gate(tmp_path):
    services = _services(tmp_path, _StubPermissions(error=RuntimeError("store offline")))
    assert services._conversation_access_mode(_command()) == ACCESS_MODE_CONTROLLED


def test_host_without_access_mode_support_fails_closed(tmp_path):
    services = _services(tmp_path, _HostWithoutAccessMode())
    assert services._conversation_access_mode(_command()) == ACCESS_MODE_CONTROLLED


def test_grant_is_looked_up_with_the_request_binding(tmp_path):
    seen = {}

    class _Recording:
        def get_conversation_access_mode(self, conversation_id, **kwargs):
            seen["conversation_id"] = conversation_id
            seen.update(kwargs)
            return {"mode": ACCESS_MODE_FULL}

    services = _services(tmp_path, _Recording())
    assert services._conversation_access_mode(_command("conv-42")) == ACCESS_MODE_FULL
    assert seen == {
        "conversation_id": "conv-42",
        "tenant_id": "local",
        "workspace_id": "local-default",
        "principal_id": "local-ui",
    }
