"""Workspace selector primitives for the Streamlit host.

The UI keeps workspace metadata behind ``ConversationStore`` and carries the
selected scope in the trusted ``TenantContext``.  This module intentionally
contains only the small adapter needed by Streamlit; retrieval and chat remain
owned by the API/Harness contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import streamlit as st

from artpm_agent.tenancy import TenantContext


WORKSPACE_WIDGET_KEY = "workspace_selector"


def _workspace_label(item: Mapping[str, Any]) -> str:
    workspace_id = str(item.get("id") or "").strip()
    name = str(item.get("name") or workspace_id).strip()
    return f"{name} · {workspace_id}" if name and name != workspace_id else workspace_id


def _visible_workspaces(store: Any, tenant_id: str) -> list[dict[str, Any]]:
    list_workspaces = getattr(store, "list_workspaces", None)
    if not callable(list_workspaces):
        return []
    try:
        try:
            items = list_workspaces(profile_id=None, tenant_id=tenant_id, limit=100)
        except TypeError:
            # Older injected stores may not know the optional tenant filter;
            # retain the post-query ownership check for that compatibility
            # path while the built-in store filters before pagination.
            items = list_workspaces(profile_id=None, limit=100)
    except Exception:
        return []
    return [
        dict(item)
        for item in items
        if isinstance(item, Mapping)
        and str(item.get("tenant_id") or "").strip() == tenant_id
        and str(item.get("id") or "").strip()
    ]


def switch_workspace(
    store: Any,
    context: TenantContext,
    workspace: Mapping[str, Any],
) -> TenantContext:
    """Switch the UI to one workspace and reset only workspace-local state."""

    workspace_id = str(workspace.get("id") or "").strip()
    if not workspace_id:
        raise ValueError("workspace id is required")
    tenant_id = str(workspace.get("tenant_id") or "").strip()
    if tenant_id and tenant_id != context.tenant_id:
        raise ValueError("workspace is outside the current tenant")
    ensure_workspace_tenant = getattr(store, "ensure_workspace_tenant", None)
    if callable(ensure_workspace_tenant) and not ensure_workspace_tenant(
        workspace_id, context.tenant_id
    ):
        raise ValueError("workspace is outside the current tenant")

    selected = TenantContext(
        tenant_id=context.tenant_id,
        workspace_id=workspace_id,
        principal_id=context.principal_id,
        roles=context.roles,
        request_id=context.request_id,
        permissions=context.permissions,
    )
    st.session_state["tenant_context"] = selected
    st.session_state["profile_id"] = str(
        workspace.get("profile_id") or st.session_state.get("profile_id") or "local-default"
    )

    # Conversation and workflow caches are scoped to the workspace. Do not
    # carry a pending turn or permission grant into a different scope.
    st.session_state.pop("active_conversation_id", None)
    st.session_state.pop("messages_loaded_for", None)
    st.session_state.pop("pending_prompt", None)
    st.session_state.pop("pending_conversation_delete", None)
    st.session_state.pop("_conv_list_cache", None)
    st.session_state.pop("workflow_coordinator", None)
    grants = st.session_state.get("conversation_permission_grants")
    if isinstance(grants, dict):
        grants.clear()

    create_conversation = getattr(store, "create_conversation", None)
    list_conversations = getattr(store, "list_conversations", None)
    if callable(list_conversations) and callable(create_conversation):
        conversations = list_conversations(workspace_id=workspace_id, limit=1)
        conversation = conversations[0] if conversations else create_conversation(workspace_id=workspace_id)
        st.session_state["active_conversation_id"] = conversation["id"]
        st.session_state["messages"] = []
        st.session_state["messages_loaded_for"] = None
    return selected


def render_workspace_selector(store: Any, context: TenantContext) -> None:
    """Render a compact selector and keep its value aligned with trusted scope."""

    items = _visible_workspaces(store, context.tenant_id)
    current_id = context.workspace_id
    if not any(item.get("id") == current_id for item in items):
        current = store.get_workspace(current_id) if callable(getattr(store, "get_workspace", None)) else None
        if isinstance(current, Mapping) and (
            not current.get("tenant_id") or current.get("tenant_id") == context.tenant_id
        ):
            items.insert(0, dict(current))
    if not items:
        return

    ids = [str(item["id"]) for item in items]
    labels = {str(item["id"]): _workspace_label(item) for item in items}
    if st.session_state.get(WORKSPACE_WIDGET_KEY) not in ids:
        st.session_state[WORKSPACE_WIDGET_KEY] = current_id if current_id in ids else ids[0]

    st.markdown('<div class="sidebar-section-label">工作区</div>', unsafe_allow_html=True)
    selected_id = st.selectbox(
        "当前工作区",
        ids,
        format_func=lambda value: labels.get(value, value),
        key=WORKSPACE_WIDGET_KEY,
        label_visibility="collapsed",
    )
    if selected_id != context.workspace_id:
        selected = next(item for item in items if item.get("id") == selected_id)
        try:
            switch_workspace(store, context, selected)
        except Exception as error:  # noqa: BLE001 - keep the sidebar available
            st.session_state[WORKSPACE_WIDGET_KEY] = context.workspace_id
            st.warning(f"工作区切换失败：{error}")
        else:
            st.rerun()


__all__ = ["WORKSPACE_WIDGET_KEY", "render_workspace_selector", "switch_workspace"]
