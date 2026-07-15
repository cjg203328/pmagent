from __future__ import annotations

import json
from threading import Event

import pandas as pd
import pytest

from artpm_agent.core.mcp_client_enhanced import EnhancedMCPClient
from artpm_agent.runtime import (
    AgentEventType,
    ToolCall,
    ToolExecutor,
    registry_from_mcp_client,
    registry_from_skill_router,
)
from artpm_agent.skills.input_schemas import BUILTIN_SKILL_INPUT_SCHEMAS
from artpm_agent.skills.skill_router import SkillRouter


INVALID_SKILL_ARGUMENTS = {
    "document_classifier_parser": {"file_path": 42},
    "quote_calculator": {"quote_amount": 0},
    "task_allocator": {"tasks": []},
    "progress_tracker": {"warning_days_ahead": -1},
    "reminder_bot": {"recipients": []},
    "reminder_dispatch": {"idempotency_key": ""},
    "quality_control": {"action": "delete"},
    "requirements_assessment": {"asset_types": "character"},
    "cost_control": {"threshold": 2},
    "quote_scheduling": {"complexity": "unknown"},
    "progress_management": {"today": 20260714},
    "delivery": {"project_id": 0},
    "retrospective": {"project_id": 1, "lessons": [1]},
    "file_reader": {"file_path": 7},
    "file_search": {"pattern": "", "limit": 10},
    "data_analyzer": {"data_source": [], "analysis_type": "raw"},
    "trend_analyzer": {"data_series": [], "period": "quarterly"},
    "project_evaluator": {"project_data": []},
}

INVALID_MCP_ARGUMENTS = {
    "read_file": {"file_path": 7},
    "search_files": {"pattern": ""},
    "search_content": {"query": 7},
    "analyze_data": {"data_source": None},
    "execute_command": {"command": "echo ok", "timeout": 121},
}


def _execute_once(registry, name: str, arguments: dict) -> dict:
    events = list(
        ToolExecutor(registry).execute_batch(
            [ToolCall(name, arguments, id=f"call-{name}")],
            run_id="run-schema-audit",
            turn_id="turn-1",
            context={},
            abort_event=Event(),
        )
    )
    return next(
        dict(event.tool_result)
        for event in events
        if event.type == AgentEventType.TOOL_EXECUTION_END
    )


def test_every_registered_builtin_skill_has_model_ready_object_schema():
    router = SkillRouter({})
    registry = registry_from_skill_router(router)

    assert set(router.skills) == set(BUILTIN_SKILL_INPUT_SCHEMAS)
    assert set(INVALID_SKILL_ARGUMENTS) == set(router.skills)

    for specification in registry.specifications():
        schema = specification["parameters"]
        assert schema["type"] == "object", specification["name"]
        assert schema["properties"], specification["name"]
        assert schema["additionalProperties"] is False, specification["name"]
        json.dumps(schema)

    dispatch_schema = registry.get("reminder_dispatch").specification()["parameters"]
    assert "approved" not in dispatch_schema["properties"]
    assert "confirmation_token" not in dispatch_schema["properties"]

    for name in ("file_reader", "file_search", "data_analyzer", "trend_analyzer"):
        tool = registry.get(name)
        assert tool.requires_approval is True
        assert tool.read_only is True


@pytest.mark.parametrize(
    ("skill_name", "arguments"),
    sorted(INVALID_SKILL_ARGUMENTS.items()),
)
def test_each_builtin_skill_rejects_representative_invalid_arguments_before_execute(
    skill_name,
    arguments,
):
    router = SkillRouter({})
    executed = []

    def execute_skill(name, prepared):
        executed.append((name, dict(prepared)))
        return {"success": True}

    router.execute_skill = execute_skill
    registry = registry_from_skill_router(router)

    result = _execute_once(registry, skill_name, arguments)

    assert result["is_error"] is True
    assert result["content"].startswith(
        f"Invalid arguments for tool '{skill_name}' at $"
    )
    assert executed == []


def test_enhanced_mcp_tools_publish_and_enforce_input_schemas(tmp_path):
    client = EnhancedMCPClient(str(tmp_path))
    calls = []

    def call_tool(name, arguments):
        calls.append((name, dict(arguments)))
        return {"success": True}

    client.call_tool = call_tool
    listed = {item["name"]: item for item in client.list_tools()}
    registry = registry_from_mcp_client(client)

    assert set(listed) == set(INVALID_MCP_ARGUMENTS)
    for name, item in listed.items():
        schema = item["input_schema"]
        assert schema["type"] == "object", name
        assert schema["properties"], name
        assert schema["additionalProperties"] is False, name
        assert registry.get(name).specification()["parameters"] == schema
        json.dumps(schema)

    for name, arguments in INVALID_MCP_ARGUMENTS.items():
        result = _execute_once(registry, name, arguments)
        assert result["is_error"] is True
        assert result["content"].startswith(f"Invalid arguments for tool '{name}' at $")

    assert calls == []


def test_model_schema_rejects_dataframe_but_direct_python_input_remains_supported():
    router = SkillRouter({})
    frame = pd.DataFrame([{"date": "2026-07-14", "value": 1}])
    tool = registry_from_skill_router(router).get("trend_analyzer")

    with pytest.raises(ValueError, match="Invalid arguments for tool 'trend_analyzer'"):
        tool.prepare({"data_series": frame})

    loaded = router.skills["trend_analyzer"]._load_frame(frame)
    pd.testing.assert_frame_equal(loaded, frame)


def test_model_required_constraints_do_not_change_legacy_skill_validation():
    router = SkillRouter({})

    result = router.skills["reminder_dispatch"].run(
        {"approved": True, "recipients": "team"}
    )

    assert result["sent_status"] == "approval_required"
    assert "idempotency_key" in result["error"]
