from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from streamlit.testing.v1 import AppTest


APP_FILE = str(Path(__file__).resolve().parent.parent / "artpm_agent" / "app.py")


def _access_label(app: AppTest) -> str:
    popovers = app.get("popover")
    assert len(popovers) == 1
    return popovers[0].proto.popover.label


def _seed_pending_permission(app: AppTest):
    store = app.session_state["permission_store"]
    tenant = app.session_state["tenant_context"]
    conversation_id = app.session_state["active_conversation_id"]
    marker = uuid4().hex
    request = store.create_request(
        workspace_id=tenant.workspace_id,
        conversation_id=conversation_id,
        turn_id=f"permission-mode-{marker}",
        agent_id="artpm-agent",
        source="skill",
        action="skill.delivery",
        resource={"skill": "delivery", "project_id": 7},
        risk="medium",
        required_role="user",
        payload={
            "skill_name": "delivery",
            "inputs": {
                "action": "record",
                "project_id": 7,
                "delivery_no": "D-7",
            },
        },
        idempotency_key=f"permission-mode-{marker}",
    )
    return request


def test_chat_composer_exposes_controlled_access_by_default():
    app = AppTest.from_file(APP_FILE).run(timeout=30)

    assert not app.exception
    assert _access_label(app) == "按需确认"
    assert app.chat_input(key="chat_input") is not None
    enable = app.button(key="request_full_access")
    assert enable.label == "启用完全访问"
    assert enable.disabled is False


def test_full_access_requires_acknowledgement_and_can_be_revoked():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    conversation_id = app.session_state["active_conversation_id"]
    acknowledge_key = f"ack_full_access_{conversation_id}"
    confirm_key = f"confirm_full_access_{conversation_id}"

    app.button(key="request_full_access").click().run(timeout=30)

    assert not app.exception
    assert len(app.get("dialog")) == 1
    assert app.checkbox(key=acknowledge_key).value is False
    assert app.button(key=confirm_key).disabled is True

    # AppTest performs a full-script rerun for dialog widgets. Keep the
    # popover action active during that rerun so the dialog is rendered again.
    app.button(key="request_full_access").click()
    app.checkbox(key=acknowledge_key).check()
    app.run(timeout=30)

    assert app.checkbox(key=acknowledge_key).value is True
    assert app.button(key=confirm_key).disabled is False

    app.button(key="request_full_access").click()
    app.checkbox(key=acknowledge_key).check()
    app.button(key=confirm_key).click()
    app.run(timeout=30)

    assert not app.exception
    tenant = app.session_state["tenant_context"]
    grants = app.session_state["conversation_permission_grants"]
    grant = grants[conversation_id]
    assert grant["mode"] == "full_access"
    assert grant["tenant_id"] == tenant.tenant_id
    assert grant["workspace_id"] == tenant.workspace_id
    assert grant["principal_id"] == tenant.principal_id
    assert grant["conversation_id"] == conversation_id
    assert grant["expires_at"] > grant["issued_at"]
    assert _access_label(app) == "完全访问"

    # The completed dialog remains in AppTest's prior element snapshot after
    # st.rerun. Give its orphaned checkbox an explicit value before rerunning.
    if any(item.key == acknowledge_key for item in app.checkbox):
        app.checkbox(key=acknowledge_key).uncheck()
    app.button(key="use_controlled_access").click().run(timeout=30)

    assert not app.exception
    assert conversation_id not in app.session_state["conversation_permission_grants"]
    assert _access_label(app) == "按需确认"


def test_full_access_dialog_cancel_keeps_controlled_mode():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    conversation_id = app.session_state["active_conversation_id"]

    app.button(key="request_full_access").click().run(timeout=30)
    app.button(key=f"cancel_full_access_{conversation_id}").click().run(timeout=30)

    assert not app.exception
    try:
        grants = app.session_state["conversation_permission_grants"]
    except KeyError:
        grants = {}
    assert conversation_id not in grants
    assert _access_label(app) == "按需确认"


def test_pending_approval_keeps_access_switch_locked_and_request_visible():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    app.button(key="new_conversation").click().run(timeout=30)
    request = _seed_pending_permission(app)
    store = app.session_state["permission_store"]
    tenant = app.session_state["tenant_context"]

    try:
        app.run(timeout=30)

        assert not app.exception
        assert _access_label(app) == "按需确认"
        assert app.button(key="request_full_access").disabled is True
        assert app.chat_input(key="chat_input").disabled is True
        assert app.button(key=f"allow_once_{request.id}") is not None
        assert app.button(key=f"reject_permission_{request.id}") is not None
        assert store.get(
            request.id,
            workspace_id=tenant.workspace_id,
        ).status == "pending"
    finally:
        current = store.get(request.id, workspace_id=tenant.workspace_id)
        if current is not None and current.status == "pending":
            store.decide(
                request.id,
                decision="rejected",
                actor_id=tenant.principal_id,
                actor_role="user",
                expected_version=current.state_version,
                workspace_id=tenant.workspace_id,
            )
