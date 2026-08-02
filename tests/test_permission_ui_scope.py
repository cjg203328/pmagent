from __future__ import annotations

import streamlit

import artpm_agent.ui_helpers as ui_helpers
from artpm_agent.tenancy import TenantContext


class RecordingPermissionStore:
    def __init__(self, *, result=None, error: Exception | None = None) -> None:
        self.result = list(result or [])
        self.error = error
        self.calls: list[dict] = []

    def list_pending(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return list(self.result)


def _patch_session(monkeypatch, store, tenant_context) -> None:
    session_state = {
        "tenant_context": tenant_context,
        "permission_store": store,
        # These legacy values must not override the trusted tenant principal.
        "user_id": "forged-user",
        "user_role": "admin",
    }
    # ui_helpers reads session state directly while the lazy store getters live
    # in ui_state and re-import streamlit locally. Patching the module attribute
    # covers both call sites with one trusted session.
    monkeypatch.setattr(streamlit, "session_state", session_state)
    monkeypatch.setattr(ui_helpers, "st", streamlit)


def test_pending_permission_check_uses_trusted_workspace(monkeypatch):
    store = RecordingPermissionStore(result=[object()])
    tenant_context = TenantContext(
        tenant_id="tenant-b",
        workspace_id="workspace-b",
        principal_id="alice",
        roles=frozenset({"user"}),
    )
    _patch_session(monkeypatch, store, tenant_context)

    assert ui_helpers.has_pending_permission_requests("conversation-b") is True
    assert store.calls == [
        {
            "workspace_id": "workspace-b",
            "conversation_id": "conversation-b",
            "limit": 1,
        }
    ]
    assert ui_helpers._permission_actor() == ("alice", "user")


def test_pending_permission_check_fails_closed(monkeypatch):
    store = RecordingPermissionStore(error=OSError("database unavailable"))
    _patch_session(monkeypatch, store, TenantContext.local())

    assert ui_helpers.has_pending_permission_requests("conversation-a") is True


def test_invalid_tenant_context_keeps_composer_locked(monkeypatch):
    store = RecordingPermissionStore(result=[])
    _patch_session(monkeypatch, store, {"workspace_id": "forged"})

    assert ui_helpers.has_pending_permission_requests("conversation-a") is True
    assert store.calls == []
