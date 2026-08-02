from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from artpm_agent.runtime.tools import AgentTool, ToolCall, ToolRegistry, ToolResult
from artpm_agent.security import permission_preflight


APP_FILE = str(Path(__file__).resolve().parent.parent / "artpm_agent" / "app.py")


class PermissionRouter:
    def __init__(self) -> None:
        self.skills = {"delivery": object()}
        self.calls: list[tuple[str, dict]] = []

    def execute_skill(self, skill_name: str, inputs: dict):
        self.calls.append((skill_name, dict(inputs)))
        return {"success": True, "delivery_no": inputs.get("delivery_no")}


class PermissionAgent:
    def __init__(self) -> None:
        self.router = PermissionRouter()
        self.last_response_model = None

    @staticmethod
    def _format_skill_result(skill_name: str, result: dict) -> str:
        return f"已完成 {skill_name}: {result.get('delivery_no')}"


class PermissionToolAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.router = type("Router", (), {"skills": {}})()
        self.last_response_model = None
        self.tool_registry = ToolRegistry(
            [
                AgentTool(
                    name="record_progress",
                    label="Publish report",
                    description="Publish one project report",
                    parameters={
                        "type": "object",
                        "properties": {"project_id": {"type": "integer"}},
                        "required": ["project_id"],
                        "additionalProperties": False,
                    },
                    execute=self._execute,
                    requires_approval=True,
                    read_only=False,
                    risk="medium",
                )
            ]
        )

    def _execute(self, call_id, arguments, _abort_event, _on_update):
        self.calls.append((call_id, dict(arguments)))
        return ToolResult(
            "Report published",
            details={"project_id": arguments["project_id"]},
        )


def _seed_request(
    app: AppTest,
    *,
    risk: str = "medium",
    required_role: str = "user",
):
    app.session_state["agent"] = PermissionAgent()
    store = app.session_state["permission_store"]
    conversation_id = app.session_state["active_conversation_id"]
    request = store.create_request(
        workspace_id="local-default",
        conversation_id=conversation_id,
        turn_id="permission-turn",
        agent_id="artpm-agent",
        source="skill",
        action="skill.delivery",
        resource={"skill": "delivery", "project_id": 7},
        risk=risk,
        required_role=required_role,
        payload={
            "skill_name": "delivery",
            "inputs": {
                "action": "record",
                "project_id": 7,
                "delivery_no": "D-7",
            },
        },
        idempotency_key="streamlit-permission-turn",
    )
    return request, conversation_id


def test_permission_card_allows_one_execution_and_persists_response():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    request, conversation_id = _seed_request(app)

    app.run(timeout=30)

    assert not app.exception
    rendered = "\n".join(item.value for item in app.markdown)
    assert "需要你的确认" in rendered
    assert "写入交付与验收记录" in rendered
    assert "影响" in rendered
    assert "将写入交付、验收或资产版本记录" in rendered
    assert "工作区 local-default · 仅此操作" in rendered
    assert "项目=7" in rendered
    assert "交付单号=D-7" in rendered
    chat_input = app.chat_input(key="chat_input")
    assert chat_input.placeholder == "请先处理上方权限请求（允许一次或拒绝）"
    assert chat_input.proto.disabled is True
    assert app.button(key=f"allow_once_{request.id}") is not None
    assert app.button(key=f"reject_permission_{request.id}") is not None
    assert not any(
        button.key and "always" in button.key.casefold()
        for button in app.button
    )
    app.button(key=f"allow_once_{request.id}").click().run(timeout=30)

    assert not app.exception
    assert app.session_state["permission_store"].get(request.id).status == "completed"
    assert app.session_state["agent"].router.calls[0][0] == "delivery"
    assert app.chat_input(key="chat_input").placeholder == "输入消息或添加附件"
    assert app.chat_input(key="chat_input").proto.disabled is False
    messages = app.session_state["conversation_store"].list_messages(conversation_id)
    assert any("已完成 delivery" in item["content"] for item in messages)


def test_permission_card_rejects_without_calling_skill():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    request, _conversation_id = _seed_request(app)

    app.run(timeout=30)
    app.button(key=f"reject_permission_{request.id}").click().run(timeout=30)

    assert not app.exception
    assert app.session_state["permission_store"].get(request.id).status == "rejected"
    assert app.session_state["agent"].router.calls == []
    assert app.chat_input(key="chat_input").placeholder == "输入消息或添加附件"
    assert app.chat_input(key="chat_input").proto.disabled is False


def test_model_tool_request_uses_same_allow_once_card_and_exact_arguments():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    agent = PermissionToolAgent()
    app.session_state["agent"] = agent
    store = app.session_state["permission_store"]
    conversation_id = app.session_state["active_conversation_id"]
    tool = agent.tool_registry.get("record_progress")
    decision = permission_preflight(
        ToolCall(
            name="record_progress",
            id="tool-call-1",
            arguments={"project_id": 7},
        ),
        tool,
        tool.prepare({"project_id": 7}),
        {
            "permission_store": store,
            "workspace_id": "local-default",
            "conversation_id": conversation_id,
            "turn_id": "tool-permission-turn",
            "agent_id": "artpm-agent",
        },
    )
    request = store.list_pending(conversation_id=conversation_id)[-1]

    assert decision is not None and decision.block is True
    app.run(timeout=30)
    app.button(key=f"allow_once_{request.id}").click().run(timeout=30)

    assert not app.exception
    assert store.get(request.id).status == "completed"
    assert len(app.session_state["agent"].calls) == 1
    assert app.session_state["agent"].calls[0][1] == {"project_id": 7}


def test_high_risk_permission_requires_review_and_explicit_acknowledgement():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    request, _conversation_id = _seed_request(app, risk="high")

    app.run(timeout=30)

    assert app.button(key=f"review_permission_{request.id}") is not None
    assert not any(
        button.key == f"allow_once_{request.id}" for button in app.button
    )
    app.button(key=f"review_permission_{request.id}").click().run(timeout=30)

    acknowledge = app.checkbox(key=f"acknowledge_permission_{request.id}")
    confirm = app.button(key=f"confirm_permission_{request.id}")
    assert acknowledge is not None
    assert confirm.proto.disabled is True

    acknowledge.check().run(timeout=30)
    confirm = app.button(key=f"confirm_permission_{request.id}")
    assert confirm.proto.disabled is False
    confirm.click().run(timeout=30)

    assert not app.exception
    assert app.session_state["permission_store"].get(request.id).status == "completed"
    assert len(app.session_state["agent"].router.calls) == 1
    rendered = "\n".join(item.value for item in app.markdown)
    assert "已按本次授权执行" in rendered


def test_critical_permission_is_admin_only_even_when_caller_requests_user_role():
    app = AppTest.from_file(APP_FILE).run(timeout=30)
    request, _conversation_id = _seed_request(
        app,
        risk="critical",
        required_role="user",
    )

    app.run(timeout=30)

    stored = app.session_state["permission_store"].get(request.id)
    assert stored.required_role == "admin"
    assert app.button(key=f"review_permission_{request.id}").proto.disabled is True
    assert app.button(key=f"reject_permission_{request.id}").proto.disabled is False
    rendered = "\n".join(item.value for item in app.markdown)
    assert "管理员二次确认" in rendered
