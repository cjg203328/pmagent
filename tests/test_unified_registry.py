"""Contract tests for the unified capability registry (skills + MCP + workflows)."""

from threading import Event

from artpm_agent.runtime import (
    AgentTool,
    ToolRegistry,
    build_capability_registry,
    registry_from_mcp_client,
    registry_from_workflow_engine,
)


class _FakeMCPClient:
    enabled = True

    def list_tools(self):
        return [
            {"name": "read_file", "description": "Read a workspace file"},
            {"name": "execute_command", "description": "Run a system command"},
        ]

    def call_tool(self, name, params):
        return {"success": True, "content": f"{name}:{params}"}


class _FakeEngine:
    def start(self, definition, conversation_id, *, input_data=None):
        return _FakeWFResult(definition, conversation_id, input_data or {})


class _FakeWFResult:
    def __init__(self, definition, conversation_id, input_data):
        self.definition = definition
        self.conversation_id = conversation_id
        self.input_data = input_data

    def to_dict(self):
        return {
            "definition_id": getattr(self.definition, "id", None),
            "conversation_id": self.conversation_id,
            "input_data": self.input_data,
        }


class _FakeDefinition:
    def __init__(self, definition_id):
        self.id = definition_id
        self.name = f"Workflow {definition_id}"
        self.description = f"Run {definition_id}"
        self.input_schema = {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
        }


def _fake_definition_provider(definition_id):
    if definition_id == "onboard":
        return _FakeDefinition("onboard")
    return None


def _fake_skill_router():
    class Skill:
        input_schema = {"type": "object"}

    class Router:
        skills = {"read_project": Skill(), "send_notice": Skill()}

        def list_skills(self):
            return [
                {
                    "skill_name": "read_project",
                    "description": "Read",
                    "risk": "low",
                    "read_only": True,
                    "requires_approval": False,
                },
                {
                    "skill_name": "send_notice",
                    "description": "Send",
                    "risk": "high",
                    "read_only": False,
                    "requires_approval": False,
                },
            ]

        def execute_skill(self, name, arguments):
            return {"success": True, "skill": name}

    return Router()


def test_mcp_client_tools_are_untrusted_and_require_approval():
    registry = registry_from_mcp_client(_FakeMCPClient())

    assert len(registry) == 2
    read_file = registry.get("read_file")
    assert read_file is not None
    assert read_file.requires_approval is True
    assert read_file.read_only is False
    assert read_file.execution_mode == "sequential"

    result = read_file.invoke("call-1", {"path": "x"}, Event())
    assert result.content.startswith("read_file:")


def test_mcp_client_disabled_yields_empty_registry():
    client = _FakeMCPClient()
    client.enabled = False
    assert len(registry_from_mcp_client(client)) == 0


def test_mcp_client_failure_normalizes_to_error_result():
    class FailingClient:
        enabled = True

        def list_tools(self):
            return [{"name": "read_file", "description": "Read"}]

        def call_tool(self, name, params):
            return {"success": False, "error": "boom"}

    result = (
        registry_from_mcp_client(FailingClient())
        .get("read_file")
        .invoke("call-1", {}, Event())
    )
    assert result.is_error is True
    assert "boom" in result.content


def test_workflow_tools_are_write_tools_requiring_approval():
    registry = registry_from_workflow_engine(
        _FakeEngine(),
        definition_provider=_fake_definition_provider,
        conversation_id_factory=lambda: "conv:1",
        definition_ids=["onboard"],
    )

    assert len(registry) == 1
    tool = registry.get("workflow__onboard")
    assert isinstance(tool, AgentTool)
    assert tool.requires_approval is True
    assert tool.read_only is False

    result = tool.invoke("call-1", {"project_id": "p1"}, Event())
    assert result.details["definition_id"] == "onboard"
    assert result.details["input_data"] == {"project_id": "p1"}


def test_build_capability_registry_merges_all_three_sources():
    registry = build_capability_registry(
        skill_router=_fake_skill_router(),
        mcp_client=_FakeMCPClient(),
        workflow_engine=_FakeEngine(),
        workflow_definition_provider=_fake_definition_provider,
        workflow_conversation_id_factory=lambda: "conv:1",
        workflow_definition_ids=["onboard"],
    )

    names = {tool.name for tool in registry.snapshot()}
    assert "read_project" in names  # skill
    assert "send_notice" in names  # skill (write -> approval)
    assert "read_file" in names  # mcp
    assert "execute_command" in names  # mcp (untrusted)
    assert "workflow__onboard" in names  # workflow

    # Every write-style tool must fail closed under the host-approval policy.
    for tool in registry.snapshot():
        if not tool.read_only:
            assert tool.requires_approval is True


def test_build_capability_registry_with_only_skills():
    registry = build_capability_registry(skill_router=_fake_skill_router())
    assert len(registry) == 2
    assert isinstance(registry, ToolRegistry)
