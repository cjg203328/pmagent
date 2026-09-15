from __future__ import annotations

from pathlib import Path

from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.tenancy import TenantContext
from artpm_agent.ui import workspace_selector


def test_switch_workspace_rebinds_scope_and_creates_workspace_local_conversation(
    tmp_path: Path, monkeypatch
):
    store = ConversationStore(tmp_path / "conversations.sqlite")
    workspace = store.create_workspace("studio", "Studio", tenant_id="local")
    state = {
        "tenant_context": TenantContext.local(),
        "active_conversation_id": "old",
        "messages": [{"role": "user", "content": "old"}],
        "pending_prompt": {"message": "stale"},
    }
    monkeypatch.setattr(workspace_selector.st, "session_state", state)

    selected = workspace_selector.switch_workspace(store, TenantContext.local(), workspace)

    assert selected.workspace_id == "studio"
    assert state["tenant_context"].workspace_id == "studio"
    conversation = store.get_conversation(
        state["active_conversation_id"], workspace_id="studio"
    )
    assert conversation is not None
    assert "pending_prompt" not in state
    assert state["messages"] == []
