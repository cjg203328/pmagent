"""JSON Schema contracts for model-visible built-in skills."""

from __future__ import annotations

from typing import Any


def _object(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] = (),
    all_of: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    constraints = []
    if required:
        constraints.append({"required": list(required)})
    constraints.extend(all_of)
    if constraints:
        schema["allOf"] = constraints
    return schema


def _when_action(action: str, then: dict[str, Any]) -> dict[str, Any]:
    return {
        "if": {
            "properties": {"action": {"const": action}},
            "required": ["action"],
        },
        "then": then,
    }


def _identifier() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "integer", "minimum": 1},
            {"type": "string", "minLength": 1},
        ]
    }


def _json_data_source() -> dict[str, Any]:
    return {
        "oneOf": [
            {"type": "string", "minLength": 1},
            {"type": "array"},
            {"type": "object"},
        ]
    }


DOCUMENT_CLASSIFIER_PARSER_INPUT_SCHEMA = _object(
    {
        "file_path": {"type": "string", "minLength": 1},
        "source": {"type": "string", "minLength": 1},
        "user_hint": {"type": "string"},
        "ocr_prompt": {"type": "string", "minLength": 1},
        "ocr_image_mode": {"type": "string", "enum": ["base", "gundam"]},
    },
    required=("file_path",),
)


QUOTE_CALCULATOR_INPUT_SCHEMA = _object(
    {
        "quote_data": {
            "type": "object",
            "properties": {
                "total_amount": {"type": "number", "exclusiveMinimum": 0},
                "cost": {"type": "number", "minimum": 0},
            },
            "additionalProperties": True,
        },
        "cost_config": {
            "type": "object",
            "properties": {
                "overhead_rate": {"type": "number", "minimum": 0, "maximum": 1},
                "tax_rate": {"type": "number", "minimum": 0, "maximum": 1},
                "currency": {"type": "string", "minLength": 1},
                "risk_thresholds": {"type": "object"},
            },
            "additionalProperties": True,
        },
        "quote_amount": {"type": "number", "exclusiveMinimum": 0},
        "cost": {"type": "number", "minimum": 0},
        "overhead_rate": {"type": "number", "minimum": 0, "maximum": 1},
        "tax_rate": {"type": "number", "minimum": 0, "maximum": 1},
        "high_risk_below": {"type": "number", "minimum": -1, "maximum": 1},
        "medium_risk_below": {"type": "number", "minimum": -1, "maximum": 1},
    }
)
QUOTE_CALCULATOR_INPUT_SCHEMA["anyOf"] = [
    {"required": ["quote_amount"]},
    {
        "properties": {
            "quote_data": {"required": ["total_amount"]},
        },
        "required": ["quote_data"],
    },
]


TASK_ALLOCATOR_INPUT_SCHEMA = _object(
    {
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "oneOf": [
                    {"type": "string", "minLength": 1},
                    {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "minLength": 1},
                            "type": {"type": "string"},
                            "description": {"type": "string"},
                            "estimated_hours": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                            },
                            "keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "priority": {"type": "string"},
                        },
                        "additionalProperties": True,
                    },
                ]
            },
        },
        "constraints": {
            "type": "object",
            "properties": {
                "max_load_per_person": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                },
                "consider_skills": {"type": "boolean"},
                "consider_history": {"type": "boolean"},
                "project_id": _identifier(),
            },
            "additionalProperties": True,
        },
        "project_id": _identifier(),
    },
    required=("tasks",),
)


PROGRESS_TRACKER_INPUT_SCHEMA = _object(
    {
        "check_type": {"type": "string", "minLength": 1},
        "project_id": _identifier(),
        "warning_days_ahead": {"type": "integer", "minimum": 0},
        "include_completed": {"type": "boolean"},
    }
)


REMINDER_BOT_INPUT_SCHEMA = _object(
    {
        "type": {"type": "string", "minLength": 1},
        "recipients": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string", "minLength": 1},
                },
            ]
        },
        "task_id": {
            "oneOf": [
                {"type": "integer"},
                {"type": "string"},
            ]
        },
        "tone": {"type": "string", "enum": ["friendly", "formal", "urgent"]},
    }
)


REMINDER_DISPATCH_INPUT_SCHEMA = _object(
    {
        **REMINDER_BOT_INPUT_SCHEMA["properties"],
        "idempotency_key": {"type": "string", "minLength": 1},
    },
    required=("idempotency_key",),
)


QUALITY_CONTROL_INPUT_SCHEMA = _object(
    {
        "action": {"type": "string", "enum": ["submit", "review", "report"]},
        "task_id": _identifier(),
        "task_name": {"type": "string", "minLength": 1},
        "submitter": {"type": "string"},
        "decision": {"type": "string", "enum": ["accept", "reject", "revise"]},
        "quality_score": {"type": "number", "minimum": 0, "maximum": 5},
        "reviewer": {"type": "string"},
        "notes": {"type": "string"},
        "project_id": _identifier(),
    },
    all_of=(
        _when_action(
            "submit",
            {"anyOf": [{"required": ["task_id"]}, {"required": ["task_name"]}]},
        ),
        _when_action(
            "review",
            {
                "required": ["decision"],
                "anyOf": [
                    {"required": ["task_id"]},
                    {"required": ["task_name"]},
                ],
            },
        ),
    ),
)


REQUIREMENTS_ASSESSMENT_INPUT_SCHEMA = _object(
    {
        "action": {"type": "string", "enum": ["assess", "scope", "ingest"]},
        "asset_type": {"type": "string"},
        "requirements_text": {"type": "string"},
        "asset_name": {"type": "string"},
        "asset_types": {"type": "array", "items": {"type": "string"}},
        "project_name": {"type": "string", "minLength": 1},
        "client": {"type": "string", "minLength": 1},
        "parsed_data": {"type": "object"},
        "document_file_name": {"type": "string"},
        "document_type": {"type": "string", "minLength": 1},
    },
    all_of=(
        _when_action(
            "ingest",
            {"required": ["project_name", "client", "parsed_data"]},
        ),
    ),
)


COST_CONTROL_INPUT_SCHEMA = _object(
    {
        "action": {"type": "string", "enum": ["estimate", "budget", "overrun"]},
        "hours": {"type": "number", "minimum": 0},
        "staff_level": {"type": "string", "minLength": 1},
        "quantity": {"type": "integer", "minimum": 1},
        "complexity": {"type": "string"},
        "project_id": _identifier(),
        "threshold": {"type": "number", "minimum": 0, "maximum": 1},
    },
    all_of=(
        _when_action("budget", {"required": ["project_id"]}),
        _when_action("overrun", {"required": ["project_id"]}),
    ),
)


_SCHEDULE_ASSET = {
    "type": "object",
    "properties": {
        "asset_name": {"type": "string"},
        "asset_type": {"type": "string"},
        "complexity": {"type": "string", "enum": ["simple", "medium", "complex"]},
        "quantity": {"type": "integer", "minimum": 1},
        "history_factor": {"type": "number", "exclusiveMinimum": 0},
    },
    "additionalProperties": True,
}


QUOTE_SCHEDULING_INPUT_SCHEMA = _object(
    {
        "action": {
            "type": "string",
            "enum": ["estimate", "schedule", "milestone"],
        },
        "complexity": {"type": "string", "enum": ["simple", "medium", "complex"]},
        "asset_type": {"type": "string"},
        "quantity": {"type": "integer", "minimum": 1},
        "history_factor": {"type": "number", "exclusiveMinimum": 0},
        "assets": {"type": "array", "minItems": 1, "items": _SCHEDULE_ASSET},
        "start_date": {"type": "string", "minLength": 1},
        "team_size": {"type": "integer", "minimum": 1},
        "parallel": {"type": "integer", "minimum": 1},
    },
    all_of=(
        _when_action("schedule", {"required": ["assets", "start_date"]}),
        _when_action("milestone", {"required": ["assets", "start_date"]}),
    ),
)


PROGRESS_MANAGEMENT_INPUT_SCHEMA = _object(
    {
        "action": {"type": "string", "enum": ["view", "blockers", "standup"]},
        "project_id": _identifier(),
        "today": {"type": "string", "minLength": 1},
    }
)


DELIVERY_INPUT_SCHEMA = _object(
    {
        "action": {
            "type": "string",
            "enum": ["manifest", "acceptance", "record", "version"],
        },
        "project_id": _identifier(),
        "delivery_no": {"type": "string", "minLength": 1},
        "items": {"type": "array", "items": {"type": "object"}},
        "delivered_by": {"type": "string"},
        "title": {"type": "string"},
        "asset_id": _identifier(),
        "version": {"type": "string", "minLength": 1},
        "status": {"type": "string", "minLength": 1},
        "note": {"type": "string"},
        "file_ref": {"type": "string"},
    },
    all_of=(
        _when_action("manifest", {"required": ["project_id"]}),
        _when_action("acceptance", {"required": ["project_id"]}),
        _when_action("record", {"required": ["project_id", "delivery_no"]}),
        _when_action("version", {"required": ["asset_id", "version"]}),
    ),
)


RETROSPECTIVE_INPUT_SCHEMA = _object(
    {
        "action": {"type": "string", "enum": ["report", "lessons"]},
        "project_id": _identifier(),
        "lessons": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "title": {"type": "string"},
    },
    required=("project_id",),
    all_of=(_when_action("lessons", {"required": ["lessons"]}),),
)


FILE_READER_INPUT_SCHEMA = _object(
    {
        "file_path": {"type": "string", "minLength": 1},
        "encoding": {"type": "string", "minLength": 1},
        "lines_limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        "summary": {"type": "boolean"},
    },
    required=("file_path",),
)


FILE_SEARCH_INPUT_SCHEMA = _object(
    {
        "pattern": {"type": "string", "minLength": 1},
        "directory": {"type": "string"},
        "recursive": {"type": "boolean"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "content_search": {"type": "string", "minLength": 1},
    },
    required=("pattern",),
)


DATA_ANALYZER_INPUT_SCHEMA = _object(
    {
        "data_source": _json_data_source(),
        "analysis_type": {
            "type": "string",
            "enum": ["descriptive", "summary", "statistics"],
        },
        "metrics": {"type": "array", "items": {"type": "string"}},
        "visualize": {"type": "boolean"},
    },
    required=("data_source",),
)


TREND_ANALYZER_INPUT_SCHEMA = _object(
    {
        "data_series": _json_data_source(),
        "time_column": {"type": "string", "minLength": 1},
        "value_column": {"type": "string", "minLength": 1},
        "period": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
        "forecast": {"type": "boolean"},
    },
    required=("data_series",),
)


PROJECT_EVALUATOR_INPUT_SCHEMA = _object(
    {
        "project_data": {
            "oneOf": [
                {"type": "string", "minLength": 1},
                {
                    "type": "object",
                    "required": ["quote_amount", "cost"],
                    "properties": {
                        "quote_amount": {"type": "number", "exclusiveMinimum": 0},
                        "cost": {"type": "number", "minimum": 0},
                        "deadline": {"type": "string", "minLength": 1},
                    },
                    "additionalProperties": True,
                },
            ]
        },
        "historical_data": _json_data_source(),
        "constraints": {"type": "object"},
    },
    required=("project_data",),
)


BUILTIN_SKILL_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "document_classifier_parser": DOCUMENT_CLASSIFIER_PARSER_INPUT_SCHEMA,
    "quote_calculator": QUOTE_CALCULATOR_INPUT_SCHEMA,
    "task_allocator": TASK_ALLOCATOR_INPUT_SCHEMA,
    "progress_tracker": PROGRESS_TRACKER_INPUT_SCHEMA,
    "reminder_bot": REMINDER_BOT_INPUT_SCHEMA,
    "reminder_dispatch": REMINDER_DISPATCH_INPUT_SCHEMA,
    "quality_control": QUALITY_CONTROL_INPUT_SCHEMA,
    "requirements_assessment": REQUIREMENTS_ASSESSMENT_INPUT_SCHEMA,
    "cost_control": COST_CONTROL_INPUT_SCHEMA,
    "quote_scheduling": QUOTE_SCHEDULING_INPUT_SCHEMA,
    "progress_management": PROGRESS_MANAGEMENT_INPUT_SCHEMA,
    "delivery": DELIVERY_INPUT_SCHEMA,
    "retrospective": RETROSPECTIVE_INPUT_SCHEMA,
    "file_reader": FILE_READER_INPUT_SCHEMA,
    "file_search": FILE_SEARCH_INPUT_SCHEMA,
    "data_analyzer": DATA_ANALYZER_INPUT_SCHEMA,
    "trend_analyzer": TREND_ANALYZER_INPUT_SCHEMA,
    "project_evaluator": PROJECT_EVALUATOR_INPUT_SCHEMA,
}
