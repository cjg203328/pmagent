"""Provider-neutral tool contracts and SkillRouter adaptation.

The model-facing tool description, execution callback, and host security policy
are intentionally separate. A model can request a tool call, but only the host
preflight hook can approve a sensitive operation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import inspect
import json
import logging
import math
import re
from threading import Event, RLock
from types import MappingProxyType
from typing import Any, Literal, Optional
from uuid import uuid4

from jsonschema import FormatChecker
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for


ToolExecutionMode = Literal["parallel", "sequential"]
ToolUpdateCallback = Callable[["ToolResult"], None]
ToolExecute = Callable[
    [str, Mapping[str, Any], Event, Optional[ToolUpdateCallback]],
    "ToolResult | Mapping[str, Any]",
]
ToolArgumentPreparer = Callable[[Mapping[str, Any]], Mapping[str, Any]]

_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_JSON_PATH_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_SCHEMA_REFERENCE_KEYWORDS = frozenset({"$ref", "$dynamicRef", "$recursiveRef"})
_MODEL_APPROVAL_REQUIRED_TOOLS = frozenset(
    {"file_reader", "file_search", "data_analyzer", "trend_analyzer"}
)


def _strict_metadata_flag(value: Any, *, default: bool) -> bool:
    """Accept only actual booleans for security-relevant tool metadata."""

    return value if isinstance(value, bool) else default


def _frozen_mapping(value: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def _copy_tool_argument_value(
    value: Any,
    *,
    tool_name: str,
    path: str = "$",
    _seen: Optional[set[int]] = None,
) -> Any:
    """Copy model arguments while enforcing the JSON value contract."""
    if isinstance(value, (Mapping, list, tuple)):
        seen = _seen if _seen is not None else set()
        identity = id(value)
        if identity in seen:
            raise ValueError(
                f"Invalid arguments for tool '{tool_name}' at {path}: "
                "recursive values are not JSON-compatible"
            )
        seen.add(identity)
        try:
            if isinstance(value, Mapping):
                copied: dict[str, Any] = {}
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise ValueError(
                            f"Invalid arguments for tool '{tool_name}' at {path}: "
                            "object keys must be strings"
                        )
                    copied[key] = _copy_tool_argument_value(
                        item,
                        tool_name=tool_name,
                        path=f"{path}.{key}",
                        _seen=seen,
                    )
                return copied
            return [
                _copy_tool_argument_value(
                    item,
                    tool_name=tool_name,
                    path=f"{path}[{index}]",
                    _seen=seen,
                )
                for index, item in enumerate(value)
            ]
        finally:
            seen.remove(identity)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise ValueError(
            f"Invalid arguments for tool '{tool_name}' at {path}: "
            "numbers must be finite"
        )
    raise ValueError(
        f"Invalid arguments for tool '{tool_name}' at {path}: "
        "value must be JSON-compatible"
    )


def _copy_json_value(
    value: Any,
    *,
    path: str = "$",
    _seen: Optional[set[int]] = None,
) -> Any:
    """Copy a schema into provider-safe JSON data."""
    if isinstance(value, (Mapping, list, tuple)):
        seen = _seen if _seen is not None else set()
        identity = id(value)
        if identity in seen:
            raise ToolDefinitionError(
                f"invalid JSON Schema at {path}: recursive values are not allowed"
            )
        seen.add(identity)
        try:
            if isinstance(value, Mapping):
                copied: dict[str, Any] = {}
                for key, item in value.items():
                    if not isinstance(key, str):
                        raise ToolDefinitionError(
                            f"invalid JSON Schema at {path}: object keys must be strings"
                        )
                    copied[key] = _copy_json_value(
                        item,
                        path=f"{path}.{key}",
                        _seen=seen,
                    )
                return copied
            return [
                _copy_json_value(
                    item,
                    path=f"{path}[{index}]",
                    _seen=seen,
                )
                for index, item in enumerate(value)
            ]
        finally:
            seen.remove(identity)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ToolDefinitionError(
        f"invalid JSON Schema at {path}: values must be JSON-compatible"
    )


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType(
            {key: _freeze_json_value(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _format_json_path(parts: list[Any]) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        elif isinstance(part, str) and _JSON_PATH_IDENTIFIER.fullmatch(part):
            path += f".{part}"
        else:
            path += f"[{json.dumps(str(part), ensure_ascii=True)}]"
    return path


def _safe_schema_literal(value: Any, *, limit: int = 160) -> str:
    rendered = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    rendered = rendered.replace("\r", "\\r").replace("\n", "\\n")
    if len(rendered) > limit:
        return f"{rendered[: limit - 3]}..."
    return rendered


def _reject_external_schema_references(
    value: Any,
    *,
    tool_name: str,
    path: Optional[list[Any]] = None,
) -> None:
    """Keep untrusted tool schemas from performing network or file retrieval."""

    current_path = path if path is not None else []
    if isinstance(value, Mapping):
        for key, item in value.items():
            item_path = [*current_path, key]
            if (
                key in _SCHEMA_REFERENCE_KEYWORDS
                and isinstance(item, str)
                and not item.startswith("#")
            ):
                raise ToolDefinitionError(
                    f"external JSON Schema references are not allowed for tool "
                    f"'{tool_name}' at {_format_json_path(item_path)}"
                )
            _reject_external_schema_references(
                item,
                tool_name=tool_name,
                path=item_path,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_external_schema_references(
                item,
                tool_name=tool_name,
                path=[*current_path, index],
            )


def _normalize_tool_schema(
    schema: Mapping[str, Any],
    *,
    tool_name: str,
) -> tuple[dict[str, Any], Any]:
    normalized = _copy_json_value(schema)
    _reject_external_schema_references(normalized, tool_name=tool_name)
    if not normalized:
        normalized = {"type": "object", "properties": {}}
    elif "type" not in normalized:
        normalized["type"] = "object"

    root_type = normalized.get("type")
    if root_type == ["object"]:
        normalized["type"] = "object"
    elif root_type != "object":
        raise ToolDefinitionError(
            f"tool parameters for '{tool_name}' must describe a JSON object"
        )

    try:
        validator_class = validator_for(normalized)
        validator_class.check_schema(normalized)
    except SchemaError as error:
        schema_path = _format_json_path(list(error.absolute_path))
        detail = str(error.message).splitlines()[0]
        raise ToolDefinitionError(
            f"invalid JSON Schema for tool '{tool_name}' at {schema_path}: {detail}"
        ) from None

    validator = validator_class(normalized, format_checker=FormatChecker())
    return normalized, validator


def _validation_error_sort_key(error: Any) -> tuple[Any, ...]:
    path = tuple(
        (0, part) if isinstance(part, int) else (1, str(part))
        for part in error.absolute_path
    )
    return path, str(error.validator), tuple(map(str, error.absolute_schema_path))


def _required_property(error: Any) -> Optional[str]:
    if not isinstance(error.instance, Mapping):
        return None
    required = error.validator_value
    if not isinstance(required, list):
        return None
    return next(
        (
            item
            for item in required
            if isinstance(item, str) and item not in error.instance
        ),
        None,
    )


def _unexpected_property(error: Any) -> Optional[str]:
    if not isinstance(error.instance, Mapping) or not isinstance(error.schema, Mapping):
        return None
    properties = error.schema.get("properties", {})
    known = set(properties) if isinstance(properties, Mapping) else set()
    patterns = error.schema.get("patternProperties", {})
    pattern_strings = tuple(patterns) if isinstance(patterns, Mapping) else ()
    unexpected = []
    for key in error.instance:
        if not isinstance(key, str) or key in known:
            continue
        if any(re.search(pattern, key) for pattern in pattern_strings):
            continue
        unexpected.append(key)
    return min(unexpected) if unexpected else None


def _dependent_required_property(error: Any) -> Optional[str]:
    if not isinstance(error.instance, Mapping):
        return None
    dependencies = error.validator_value
    if not isinstance(dependencies, Mapping):
        return None
    for trigger, required in dependencies.items():
        if trigger not in error.instance or not isinstance(required, list):
            continue
        for item in required:
            if isinstance(item, str) and item not in error.instance:
                return item
    return None


def _format_argument_validation_error(tool_name: str, error: Any) -> str:
    validator = str(error.validator)
    path_parts = list(error.absolute_path)
    value = error.validator_value

    if validator == "required":
        missing = _required_property(error)
        if missing is not None:
            path_parts.append(missing)
        detail = "is required"
    elif validator == "additionalProperties":
        unexpected = _unexpected_property(error)
        if unexpected is not None:
            path_parts.append(unexpected)
        detail = "is not allowed"
    elif validator == "dependentRequired":
        missing = _dependent_required_property(error)
        if missing is not None:
            path_parts.append(missing)
        detail = "is required by another property"
    elif validator == "type":
        if isinstance(value, list):
            expected = ", ".join(map(str, value))
            detail = f"expected one of these types: {expected}"
        else:
            detail = f"expected type {value}"
    elif validator == "enum":
        detail = f"must be one of {_safe_schema_literal(value)}"
    elif validator == "const":
        detail = f"must equal {_safe_schema_literal(value)}"
    elif validator == "minimum":
        detail = f"must be greater than or equal to {_safe_schema_literal(value)}"
    elif validator == "maximum":
        detail = f"must be less than or equal to {_safe_schema_literal(value)}"
    elif validator == "exclusiveMinimum":
        detail = f"must be greater than {_safe_schema_literal(value)}"
    elif validator == "exclusiveMaximum":
        detail = f"must be less than {_safe_schema_literal(value)}"
    elif validator == "multipleOf":
        detail = f"must be a multiple of {_safe_schema_literal(value)}"
    elif validator == "minLength":
        detail = f"must contain at least {value} characters"
    elif validator == "maxLength":
        detail = f"must contain at most {value} characters"
    elif validator == "pattern":
        detail = f"must match pattern {_safe_schema_literal(value)}"
    elif validator == "format":
        detail = f"must match format {_safe_schema_literal(value)}"
    elif validator == "minItems":
        detail = f"must contain at least {value} items"
    elif validator == "maxItems":
        detail = f"must contain at most {value} items"
    elif validator == "uniqueItems":
        detail = "must contain unique items"
    elif validator == "contains":
        detail = "must contain an item matching the required schema"
    elif validator == "minContains":
        detail = f"must contain at least {value} matching items"
    elif validator == "maxContains":
        detail = f"must contain at most {value} matching items"
    elif validator == "minProperties":
        detail = f"must contain at least {value} properties"
    elif validator == "maxProperties":
        detail = f"must contain at most {value} properties"
    elif validator == "propertyNames":
        detail = "contains an invalid property name"
    elif validator in {"allOf", "anyOf", "oneOf", "not"}:
        detail = f"does not satisfy the '{validator}' constraint"
    else:
        detail = f"does not satisfy the '{validator}' constraint"

    return (
        f"Invalid arguments for tool '{tool_name}' at "
        f"{_format_json_path(path_parts)}: {detail}"
    )


class ToolDefinitionError(ValueError):
    """Raised when a tool definition is invalid or conflicts with a registry."""


class ToolExecutionError(RuntimeError):
    """Raised by adapters when the underlying capability reports failure."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One structured tool request produced by a model adapter."""

    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict, compare=False)
    id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _TOOL_NAME.fullmatch(self.name):
            raise ToolDefinitionError("tool call name is invalid")
        if not isinstance(self.id, str) or not self.id.strip():
            raise ToolDefinitionError("tool call id must be a non-empty string")
        if not isinstance(self.arguments, Mapping):
            raise TypeError("tool call arguments must be a mapping")
        arguments = _copy_tool_argument_value(
            self.arguments,
            tool_name=self.name,
        )
        object.__setattr__(self, "id", self.id.strip())
        object.__setattr__(self, "arguments", _frozen_mapping(arguments))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": dict(self.arguments),
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Normalized final or partial result from a tool."""

    content: str
    details: Mapping[str, Any] = field(default_factory=dict, compare=False)
    is_error: bool = False
    terminate: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("tool result content must be a string")
        if not isinstance(self.details, Mapping):
            raise TypeError("tool result details must be a mapping")
        if not isinstance(self.is_error, bool) or not isinstance(self.terminate, bool):
            raise TypeError("tool result flags must be booleans")
        object.__setattr__(self, "details", _frozen_mapping(self.details))

    @classmethod
    def error(cls, message: str) -> "ToolResult":
        return cls(content=str(message), is_error=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "details": dict(self.details),
            "is_error": self.is_error,
            "terminate": self.terminate,
        }


@dataclass(frozen=True, slots=True)
class BeforeToolCallDecision:
    """Host preflight result for one tool request."""

    block: bool = False
    reason: str = ""
    approved: bool = False

    def __post_init__(self) -> None:
        if not all(isinstance(value, bool) for value in (self.block, self.approved)):
            raise TypeError("tool preflight flags must be booleans")
        if not isinstance(self.reason, str):
            raise TypeError("tool preflight reason must be a string")


@dataclass(frozen=True, slots=True)
class AgentTool:
    """A model-visible tool plus its host-owned execution callback."""

    name: str
    description: str
    execute: ToolExecute = field(repr=False, compare=False)
    label: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict, compare=False)
    execution_mode: ToolExecutionMode = "parallel"
    prepare_arguments: Optional[ToolArgumentPreparer] = field(
        default=None,
        repr=False,
        compare=False,
    )
    requires_approval: bool = False
    risk: str = "low"
    read_only: bool = True
    auto_approval_allowed: bool = False
    _parameter_validator: Any = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _TOOL_NAME.fullmatch(self.name):
            raise ToolDefinitionError("tool name is invalid")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ToolDefinitionError("tool description must be non-empty")
        if not callable(self.execute):
            raise TypeError("tool execute must be callable")
        if self.prepare_arguments is not None and not callable(self.prepare_arguments):
            raise TypeError("prepare_arguments must be callable")
        if self.execution_mode not in {"parallel", "sequential"}:
            raise ToolDefinitionError("tool execution mode is invalid")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("tool parameters must be a mapping")
        if not all(
            isinstance(value, bool)
            for value in (
                self.requires_approval,
                self.read_only,
                self.auto_approval_allowed,
            )
        ):
            raise TypeError("tool policy flags must be booleans")
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "label", self.label.strip() or self.name)
        parameters, validator = _normalize_tool_schema(
            self.parameters,
            tool_name=self.name,
        )
        object.__setattr__(self, "parameters", _freeze_json_value(parameters))
        object.__setattr__(self, "_parameter_validator", validator)

    def prepare(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping):
            raise TypeError("tool arguments must be a mapping")
        model_arguments = _copy_tool_argument_value(
            arguments,
            tool_name=self.name,
        )
        try:
            errors = sorted(
                self._parameter_validator.iter_errors(model_arguments),
                key=_validation_error_sort_key,
            )
        except Exception:
            raise ToolDefinitionError(
                f"JSON Schema for tool '{self.name}' could not be evaluated"
            ) from None
        if errors:
            raise ValueError(_format_argument_validation_error(self.name, errors[0]))

        prepared = (
            self.prepare_arguments(model_arguments)
            if self.prepare_arguments is not None
            else model_arguments
        )
        if not isinstance(prepared, Mapping):
            raise TypeError("prepared tool arguments must be a mapping")
        return _frozen_mapping(prepared)

    def invoke(
        self,
        call_id: str,
        arguments: Mapping[str, Any],
        abort_event: Event,
        on_update: Optional[ToolUpdateCallback] = None,
    ) -> ToolResult:
        raw_result = self.execute(call_id, arguments, abort_event, on_update)
        if inspect.isawaitable(raw_result):
            raise TypeError("synchronous agent tools cannot return an awaitable")
        return normalize_tool_result(raw_result)

    def specification(self) -> dict[str, Any]:
        """Return the model-visible definition without execution internals."""
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "parameters": _thaw_json_value(self.parameters),
        }


def normalize_tool_result(result: ToolResult | Mapping[str, Any]) -> ToolResult:
    if isinstance(result, ToolResult):
        return result
    if not isinstance(result, Mapping):
        raise TypeError("tool must return ToolResult or a mapping")

    details_value = result.get("details", result)
    details = (
        dict(details_value)
        if isinstance(details_value, Mapping)
        else {"value": details_value}
    )
    content_value = result.get("content")
    if content_value is None:
        content = json.dumps(dict(result), ensure_ascii=False, default=str)
    elif isinstance(content_value, str):
        content = content_value
    else:
        content = json.dumps(content_value, ensure_ascii=False, default=str)
    def strict_flag(name: str) -> bool:
        value = result.get(name, False)
        if not isinstance(value, bool):
            raise TypeError(f"tool result {name} must be a boolean")
        return value

    return ToolResult(
        content=content,
        details=details,
        is_error=strict_flag("is_error"),
        terminate=strict_flag("terminate"),
    )


class ToolRegistry:
    """Thread-safe registry with deterministic insertion order."""

    def __init__(self, tools: Optional[list[AgentTool] | tuple[AgentTool, ...]] = None):
        self._tools: dict[str, AgentTool] = {}
        self._lock = RLock()
        for tool in tools or ():
            self.register(tool)

    def register(self, tool: AgentTool, *, replace: bool = False) -> None:
        if not isinstance(tool, AgentTool):
            raise TypeError("registry entries must be AgentTool instances")
        with self._lock:
            if tool.name in self._tools and not replace:
                raise ToolDefinitionError(f"duplicate tool: {tool.name}")
            self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._tools.pop(name, None) is not None

    def get(self, name: str) -> Optional[AgentTool]:
        with self._lock:
            return self._tools.get(name)

    def snapshot(self) -> tuple[AgentTool, ...]:
        with self._lock:
            return tuple(self._tools.values())

    def specifications(self) -> tuple[dict[str, Any], ...]:
        return tuple(tool.specification() for tool in self.snapshot())

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)


def registry_from_skill_router(router: Any) -> ToolRegistry:
    """Expose loaded ArtPM skills through the provider-neutral tool contract."""
    list_skills = getattr(router, "list_skills", None)
    execute_skill = getattr(router, "execute_skill", None)
    if not callable(list_skills) or not callable(execute_skill):
        raise TypeError("router must expose list_skills and execute_skill")

    loaded_skills = getattr(router, "skills", {})
    registry = ToolRegistry()
    for item in list_skills():
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("skill_name") or item.get("name") or "").strip()
        if not _TOOL_NAME.fullmatch(name):
            continue
        skill = loaded_skills.get(name) if isinstance(loaded_skills, Mapping) else None
        parameters = getattr(skill, "input_schema", {})
        if not isinstance(parameters, Mapping):
            parameters = {}
        description = str(item.get("description") or name).strip()
        read_only = _strict_metadata_flag(
            item.get("read_only", False),
            default=False,
        )
        # Model-driven selection has a larger blast radius than deterministic
        # routing. Any capability that may write must fail closed even if
        # legacy metadata forgot to raise the explicit approval flag.
        requires_approval = (
            _strict_metadata_flag(
                item.get("requires_approval", True),
                default=True,
            )
            or not read_only
            or name in _MODEL_APPROVAL_REQUIRED_TOOLS
        )

        def prepare_arguments(
            arguments: Mapping[str, Any],
            *,
            skill_instance: Any = skill,
        ) -> Mapping[str, Any]:
            prepared = dict(arguments)
            validator = getattr(skill_instance, "validate", None)
            if callable(validator):
                validation = validator(prepared)
                if (
                    not isinstance(validation, tuple)
                    or len(validation) != 2
                    or not isinstance(validation[0], bool)
                ):
                    raise TypeError("skill validate must return (bool, message)")
                valid, error = validation
                if not valid:
                    raise ValueError(str(error) or "invalid skill arguments")
            return prepared

        def execute(
            _call_id: str,
            arguments: Mapping[str, Any],
            abort_event: Event,
            _on_update: Optional[ToolUpdateCallback],
            *,
            skill_name: str = name,
        ) -> ToolResult:
            if abort_event.is_set():
                raise ToolExecutionError("tool execution aborted")
            result = execute_skill(skill_name, dict(arguments))
            if not isinstance(result, Mapping):
                raise ToolExecutionError("skill returned a non-mapping result")
            if result.get("success") is False:
                raise ToolExecutionError(
                    str(result.get("error") or f"skill failed: {skill_name}")
                )
            result_dict = dict(result)
            return ToolResult(
                content=json.dumps(result_dict, ensure_ascii=False, default=str),
                details=result_dict,
            )

        registry.register(
            AgentTool(
                name=name,
                label=str(item.get("label") or name),
                description=description,
                parameters=parameters,
                execute=execute,
                prepare_arguments=prepare_arguments,
                execution_mode="parallel" if read_only else "sequential",
                requires_approval=requires_approval,
                risk=str(item.get("risk") or "untrusted"),
                read_only=read_only,
                auto_approval_allowed=not (
                    _strict_metadata_flag(
                        item.get("is_plugin_skill", False),
                        default=True,
                    )
                    or _strict_metadata_flag(
                        item.get("is_mcp_skill", False),
                        default=True,
                    )
                ),
            )
        )
    return registry


# ─────────────────────────────────────────────────────────────
# MCP client adaptation
# ─────────────────────────────────────────────────────────────


def _normalize_mcp_result(raw: Any, tool_name: str) -> ToolResult:
    """Coerce a unified MCP client response dict into a ToolResult."""
    if isinstance(raw, Mapping):
        success = raw.get("success", True)
        if success is False:
            return ToolResult.error(
                str(raw.get("error") or f"MCP tool failed: {tool_name}")
            )
        details = dict(raw)
        content = raw.get("content")
        if isinstance(content, str) and content.strip():
            return ToolResult(content=content, details=details)
        return ToolResult(
            content=json.dumps(details, ensure_ascii=False, default=str),
            details=details,
        )
    if isinstance(raw, str):
        return ToolResult(content=raw)
    return ToolResult(content=json.dumps(raw, ensure_ascii=False, default=str))


def registry_from_mcp_client(client: Any) -> ToolRegistry:
    """Expose the unified MCP client's local tools through the tool contract.

    Local MCP tools (read_file, search_files, search_content, analyze_data,
    execute_command) can read the workspace or run commands. They are treated
    as untrusted: every tool requires an explicit host approval and runs
    sequentially, so the model can never self-approve a filesystem or command
    action. The ``execute_command`` capability is blocked the same way.
    """
    if client is None:
        return ToolRegistry()
    list_tools = getattr(client, "list_tools", None)
    call_tool = getattr(client, "call_tool", None)
    if not callable(list_tools) or not callable(call_tool):
        return ToolRegistry()
    try:
        enabled = bool(getattr(client, "enabled", True))
    except Exception:
        enabled = True
    if not enabled:
        return ToolRegistry()

    registry = ToolRegistry()
    for tool in list_tools():
        if not isinstance(tool, Mapping):
            continue
        name = str(tool.get("name") or "").strip()
        if not _TOOL_NAME.fullmatch(name):
            continue
        parameters = tool.get("input_schema", tool.get("parameters", {}))
        if not isinstance(parameters, Mapping):
            parameters = {}

        def execute(
            _call_id: str,
            arguments: Mapping[str, Any],
            abort_event: Event,
            _on_update: Optional[ToolUpdateCallback],
            *,
            tool_name: str = name,
        ) -> ToolResult:
            if abort_event.is_set():
                raise ToolExecutionError("tool execution aborted")
            raw = call_tool(tool_name, dict(arguments))
            return _normalize_mcp_result(raw, tool_name)

        registry.register(
            AgentTool(
                name=name,
                label=str(tool.get("label") or name),
                description=str(tool.get("description") or name).strip(),
                parameters=parameters,
                execute=execute,
                execution_mode="sequential",
                requires_approval=True,
                risk="untrusted",
                read_only=False,
            )
        )
    return registry


# ─────────────────────────────────────────────────────────────
# Workflow engine adaptation
# ─────────────────────────────────────────────────────────────


def _normalize_workflow_result(result: Any) -> ToolResult:
    details = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    content = json.dumps(details, ensure_ascii=False, default=str)
    return ToolResult(content=content, details=details)


def registry_from_workflow_engine(
    engine: Any,
    *,
    definition_provider: Callable[[str], Any],
    conversation_id_factory: Callable[[], str],
    definition_ids: Optional[list[str]] = None,
) -> ToolRegistry:
    """Expose installed workflows as callable tools.

    The model invokes a workflow by its definition id; the engine owns
    approval gates, idempotency and side-effect policy, so the tool contract
    only forwards the call and normalizes the persisted result. Every workflow
    is a write with side effects and therefore requires explicit host approval.
    """
    if engine is None or not callable(definition_provider):
        return ToolRegistry()
    if not callable(conversation_id_factory):
        raise TypeError("workflow conversation_id_factory must be callable")

    registry = ToolRegistry()
    for definition_id in definition_ids or ():
        definition = definition_provider(definition_id)
        if definition is None:
            continue
        parameters = getattr(definition, "input_schema", {}) or {}
        if not isinstance(parameters, Mapping):
            parameters = {}

        def execute(
            _call_id: str,
            arguments: Mapping[str, Any],
            abort_event: Event,
            _on_update: Optional[ToolUpdateCallback],
            *,
            wf_id: str = definition_id,
        ) -> ToolResult:
            if abort_event.is_set():
                raise ToolExecutionError("tool execution aborted")
            wf_def = definition_provider(wf_id)
            if wf_def is None:
                raise ToolExecutionError(f"workflow not found: {wf_id}")
            result = engine.start(
                wf_def,
                conversation_id_factory(),
                input_data=dict(arguments),
            )
            return _normalize_workflow_result(result)

        registry.register(
            AgentTool(
                name=(
                    "workflow__"
                    + re.sub(r"[^A-Za-z0-9_-]+", "_", definition_id).strip("_")
                )[:64],
                label=str(getattr(definition, "name", None) or definition_id),
                description=str(
                    getattr(definition, "description", None)
                    or f"Run workflow {definition_id}"
                ).strip(),
                parameters=parameters,
                execute=execute,
                execution_mode="sequential",
                requires_approval=True,
                risk="high",
                read_only=False,
            )
        )
    return registry


# ─────────────────────────────────────────────────────────────
# Unified capability registry
# ─────────────────────────────────────────────────────────────


def build_capability_registry(
    *,
    skill_router: Any = None,
    mcp_client: Any = None,
    workflow_engine: Any = None,
    workflow_definition_provider: Optional[Callable[[str], Any]] = None,
    workflow_conversation_id_factory: Optional[Callable[[], str]] = None,
    workflow_definition_ids: Optional[list[str]] = None,
    plugin_registry: Any = None,
) -> ToolRegistry:
    """Union of every provider-neutral capability the agent may call.

    Skills, MCP client tools and workflows are merged into one registry so the
    structured agent loop sees a single, consistent tool surface. Skill tools
    win name collisions (they carry the richer business metadata); MCP and
    workflow tools are appended afterwards.

    ``plugin_registry`` optionally accepts the plugin ``CapabilityRegistry``:
    every plugin tool factory is invoked (no arguments) and, when it returns
    an ``AgentTool`` whose name is not already taken, registered. Plugin tools
    never override built-in tools.
    """
    registry = ToolRegistry()
    if skill_router is not None:
        for tool in registry_from_skill_router(skill_router).snapshot():
            registry.register(tool, replace=True)
    if mcp_client is not None:
        for tool in registry_from_mcp_client(mcp_client).snapshot():
            if registry.get(tool.name) is None:
                registry.register(tool)
    if workflow_engine is not None and workflow_definition_provider is not None:
        for tool in registry_from_workflow_engine(
            workflow_engine,
            definition_provider=workflow_definition_provider,
            conversation_id_factory=(
                workflow_conversation_id_factory or (lambda: "conv:default")
            ),
            definition_ids=workflow_definition_ids,
        ).snapshot():
            if registry.get(tool.name) is None:
                registry.register(tool)
    if plugin_registry is not None:
        _merge_plugin_tools(registry, plugin_registry)
    return registry


def _merge_plugin_tools(registry: ToolRegistry, plugin_registry: Any) -> None:
    """Register plugin-contributed AgentTool factories that are not taken."""
    logger = logging.getLogger(__name__)
    try:
        entries = plugin_registry.tools()
    except Exception:
        logger.warning("plugin capability registry is unavailable", exc_info=True)
        return
    for entry in entries:
        try:
            tool = entry.factory()
        except TypeError:
            # Factories that require a context argument get an empty context.
            try:
                tool = entry.factory({})
            except Exception as error:
                logger.warning(
                    "plugin tool %s failed to build: %s", entry.name, error
                )
                continue
        except Exception as error:
            logger.warning("plugin tool %s failed to build: %s", entry.name, error)
            continue
        if isinstance(tool, AgentTool) and registry.get(tool.name) is None:
            registry.register(tool)
