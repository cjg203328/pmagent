from __future__ import annotations

from copy import deepcopy

import streamlit

import artpm_agent.ui_helpers as ui_helpers
from artpm_agent.tenancy import TenantContext


class SessionState(dict):
    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __setattr__(self, name: str, value) -> None:
        self[name] = value


class WorkspaceConversationStore:
    """Store double that permits one conversation id in two workspaces."""

    def __init__(self) -> None:
        self.conversations = {
            "workspace-a": {
                "shared": {
                    "id": "shared",
                    "workspace_id": "workspace-a",
                    "title": "Workspace A",
                },
                "only-a": {
                    "id": "only-a",
                    "workspace_id": "workspace-a",
                    "title": "Only A",
                },
            },
            "workspace-b": {
                "shared": {
                    "id": "shared",
                    "workspace_id": "workspace-b",
                    "title": "Workspace B",
                },
                "next-b": {
                    "id": "next-b",
                    "workspace_id": "workspace-b",
                    "title": "Next B",
                },
            },
        }
        self.messages = {
            ("workspace-a", "shared"): [
                {"role": "user", "content": "message-a", "metadata": {}}
            ],
            ("workspace-b", "shared"): [
                {"role": "user", "content": "message-b", "metadata": {}}
            ],
            ("workspace-b", "next-b"): [
                {"role": "user", "content": "next-message-b", "metadata": {}}
            ],
        }
        self.calls: list[tuple[str, str, str | None]] = []

    def get_conversation(self, conversation_id, *, workspace_id=None):
        self.calls.append(("get", str(workspace_id), str(conversation_id)))
        value = self.conversations.get(str(workspace_id), {}).get(str(conversation_id))
        return deepcopy(value) if value is not None else None

    def list_messages(self, conversation_id, *, workspace_id=None, **_kwargs):
        self.calls.append(("messages", str(workspace_id), str(conversation_id)))
        return deepcopy(self.messages.get((str(workspace_id), str(conversation_id)), []))

    def delete_conversation(self, conversation_id, *, workspace_id=None):
        self.calls.append(("delete", str(workspace_id), str(conversation_id)))
        workspace = self.conversations.get(str(workspace_id), {})
        removed = workspace.pop(str(conversation_id), None)
        self.messages.pop((str(workspace_id), str(conversation_id)), None)
        return removed is not None

    def list_conversations(self, *, workspace_id=None, limit=100, **_kwargs):
        self.calls.append(("list", str(workspace_id), None))
        values = list(self.conversations.get(str(workspace_id), {}).values())
        return deepcopy(values[:limit])

    def create_conversation(self, *, workspace_id=None, **_kwargs):
        self.calls.append(("create", str(workspace_id), None))
        value = {
            "id": f"created-{workspace_id}",
            "workspace_id": str(workspace_id),
            "title": "Created",
        }
        self.conversations.setdefault(str(workspace_id), {})[value["id"]] = value
        return deepcopy(value)


def _context(workspace_id: str) -> TenantContext:
    return TenantContext(
        tenant_id="tenant-a",
        workspace_id=workspace_id,
        principal_id="alice",
        roles=frozenset({"user"}),
    )


def _patch_session(monkeypatch, store, workspace_id: str) -> SessionState:
    state = SessionState(
        tenant_context=_context(workspace_id),
        conversation_store=store,
        active_conversation_id="shared",
        messages=[{"role": "user", "content": "stale-message"}],
        messages_loaded_for=("workspace-a", "shared"),
        view="对话",
    )
    monkeypatch.setattr(streamlit, "session_state", state)
    monkeypatch.setattr(ui_helpers, "st", streamlit)
    monkeypatch.setattr(
        ui_helpers,
        "render_error_callback",
        lambda *_args, **_kwargs: None,
    )
    return state


def test_load_and_activate_are_bound_to_current_workspace(monkeypatch):
    store = WorkspaceConversationStore()
    state = _patch_session(monkeypatch, store, "workspace-b")

    ui_helpers.activate_conversation("shared")

    assert [message["content"] for message in state["messages"]] == ["message-b"]
    assert state["messages_loaded_for"] == ("workspace-b", "shared")
    assert ("get", "workspace-b", "shared") in store.calls
    assert ("messages", "workspace-b", "shared") in store.calls

    ui_helpers.activate_conversation("only-a")

    assert state["active_conversation_id"] == "shared"
    assert [message["content"] for message in state["messages"]] == ["message-b"]
    assert ("get", "workspace-b", "only-a") in store.calls


def test_delete_removes_only_current_workspace_and_activates_scoped_next(monkeypatch):
    store = WorkspaceConversationStore()
    state = _patch_session(monkeypatch, store, "workspace-b")

    ui_helpers.delete_conversation_and_activate_next(store, "shared")

    assert "shared" in store.conversations["workspace-a"]
    assert "shared" not in store.conversations["workspace-b"]
    assert state["active_conversation_id"] == "next-b"
    assert [message["content"] for message in state["messages"]] == [
        "next-message-b"
    ]
    assert ("delete", "workspace-b", "shared") in store.calls
    assert ("list", "workspace-b", None) in store.calls
    assert all(workspace == "workspace-b" for _, workspace, _ in store.calls)
