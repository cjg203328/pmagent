from __future__ import annotations

from threading import Event

import pytest

from artpm_agent.runtime import (
    AgentEventType,
    AgentTool,
    ToolCall,
    ToolDefinitionError,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    registry_from_skill_router,
)


def _execute_once(tool: AgentTool, arguments: dict):
    events = list(
        ToolExecutor(ToolRegistry([tool])).execute_batch(
            [ToolCall(tool.name, arguments, id="call-1")],
            run_id="run-1",
            turn_id="turn-1",
            context={},
            abort_event=Event(),
        )
    )
    result = next(
        event.tool_result
        for event in events
        if event.type == AgentEventType.TOOL_EXECUTION_END
    )
    return ToolResult(
        content=result["content"],
        details=result["details"],
        is_error=result["is_error"],
        terminate=result["terminate"],
    )


def _project_schema() -> dict:
    return {
        "type": "object",
        "required": ["project", "budget", "tags", "enabled"],
        "properties": {
            "project": {
                "type": "object",
                "required": ["name", "kind", "version", "members"],
                "properties": {
                    "name": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 20,
                        "pattern": "^[A-Z]",
                    },
                    "kind": {"enum": ["internal", "external"]},
                    "version": {"const": 1},
                    "members": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "items": {
                            "type": "object",
                            "required": ["id"],
                            "properties": {
                                "id": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 10,
                                }
                            },
                            "additionalProperties": False,
                        },
                    },
                },
                "additionalProperties": False,
            },
            "budget": {
                "type": "number",
                "exclusiveMinimum": 0,
                "multipleOf": 0.5,
            },
            "tags": {
                "type": "array",
                "maxItems": 3,
                "uniqueItems": True,
                "items": {"type": "string"},
            },
            "enabled": {"type": "boolean"},
        },
        "additionalProperties": False,
    }


def _valid_arguments() -> dict:
    return {
        "project": {
            "name": "Atlas",
            "kind": "internal",
            "version": 1,
            "members": [{"id": 2}],
        },
        "budget": 10.5,
        "tags": ["art", "ui"],
        "enabled": True,
    }


def test_empty_and_implicit_object_schemas_are_normalized_and_copied():
    empty = AgentTool(name="empty", description="Empty", execute=lambda *_: {})
    assert empty.specification()["parameters"] == {
        "type": "object",
        "properties": {},
    }

    source = {"properties": {"name": {"type": "string"}}}
    tool = AgentTool(
        name="implicit",
        description="Implicit object",
        parameters=source,
        execute=lambda *_: {},
    )
    source["properties"]["name"]["type"] = "integer"

    assert tool.specification()["parameters"] == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
    }


def test_invalid_schema_is_rejected_when_tool_is_defined():
    with pytest.raises(ToolDefinitionError, match="invalid JSON Schema"):
        AgentTool(
            name="invalid",
            description="Invalid",
            parameters={"type": "object", "required": "name"},
            execute=lambda *_: {},
        )

    with pytest.raises(ToolDefinitionError, match="must describe a JSON object"):
        AgentTool(
            name="scalar",
            description="Scalar",
            parameters={"type": "string"},
            execute=lambda *_: {},
        )


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda value: value.pop("project"),
            "at $.project: is required",
        ),
        (
            lambda value: value["project"]["members"][0].pop("id"),
            "at $.project.members[0].id: is required",
        ),
        (
            lambda value: value["project"].update({"secret": "do-not-echo"}),
            "at $.project.secret: is not allowed",
        ),
        (
            lambda value: value["project"].update({"kind": "private-value"}),
            'at $.project.kind: must be one of ["internal", "external"]',
        ),
        (
            lambda value: value["project"].update({"version": 2}),
            "at $.project.version: must equal 1",
        ),
        (
            lambda value: value["project"]["members"][0].update({"id": "secret"}),
            "at $.project.members[0].id: expected type integer",
        ),
        (
            lambda value: value["project"]["members"][0].update({"id": 0}),
            "at $.project.members[0].id: must be greater than or equal to 1",
        ),
        (
            lambda value: value["project"].update({"name": "x"}),
            "at $.project.name: must contain at least 3 characters",
        ),
        (
            lambda value: value["project"].update({"name": "atlas"}),
            'at $.project.name: must match pattern "^[A-Z]"',
        ),
        (
            lambda value: value.update({"budget": 0}),
            "at $.budget: must be greater than 0",
        ),
        (
            lambda value: value.update({"budget": 10.2}),
            "at $.budget: must be a multiple of 0.5",
        ),
        (
            lambda value: value.update({"tags": ["art", "art"]}),
            "at $.tags: must contain unique items",
        ),
        (
            lambda value: value["project"].update(
                {"members": [{"id": 1}, {"id": 2}, {"id": 3}]}
            ),
            "at $.project.members: must contain at most 2 items",
        ),
    ],
)
def test_json_schema_constraints_block_execution_without_echoing_values(
    mutate,
    expected,
):
    callback_arguments = []

    def execute(_call_id, arguments, _abort, _on_update):
        callback_arguments.append(dict(arguments))
        return ToolResult("executed")

    arguments = _valid_arguments()
    mutate(arguments)
    tool = AgentTool(
        name="create_project",
        description="Create a project",
        parameters=_project_schema(),
        execute=execute,
    )

    result = _execute_once(tool, arguments)

    assert result.is_error is True
    assert result.content == f"Invalid arguments for tool 'create_project' {expected}"
    assert "do-not-echo" not in result.content
    assert "private-value" not in result.content
    assert callback_arguments == []


def test_valid_arguments_reach_callback_after_schema_validation():
    callback_arguments = []

    def execute(_call_id, arguments, _abort, _on_update):
        callback_arguments.append(dict(arguments))
        return ToolResult("executed")

    arguments = _valid_arguments()
    result = _execute_once(
        AgentTool(
            name="create_project",
            description="Create a project",
            parameters=_project_schema(),
            execute=execute,
        ),
        arguments,
    )

    assert result == ToolResult("executed")
    assert callback_arguments == [arguments]


def test_skill_schema_runs_before_legacy_validator_and_callback():
    order = []

    class Skill:
        input_schema = {
            "type": "object",
            "required": ["project_id"],
            "properties": {"project_id": {"type": "string"}},
            "additionalProperties": False,
        }

        def validate(self, arguments):
            order.append(("legacy", dict(arguments)))
            return False, "legacy policy rejected arguments"

    class Router:
        skills = {"read_project": Skill()}

        def list_skills(self):
            return [
                {
                    "skill_name": "read_project",
                    "description": "Read a project",
                    "read_only": True,
                    "requires_approval": False,
                }
            ]

        def execute_skill(self, name, arguments):
            order.append(("execute", name, dict(arguments)))
            return {"success": True}

    tool = registry_from_skill_router(Router()).get("read_project")
    schema_result = _execute_once(tool, {"project_id": 123})

    assert schema_result.content.endswith("at $.project_id: expected type string")
    assert order == []

    legacy_result = _execute_once(tool, {"project_id": "p1"})
    assert legacy_result.content == "legacy policy rejected arguments"
    assert order == [("legacy", {"project_id": "p1"})]
