"""Structured provider adapters for the provider-neutral agent loop."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import json
from types import MappingProxyType
from typing import Any, Optional
from uuid import uuid4

from artpm_agent.runtime.agent_loop import AssistantTurn
from artpm_agent.runtime.events import AgentMessage
from artpm_agent.runtime.tools import ToolCall


class ProviderAdapterError(RuntimeError):
    """Base error raised by structured provider adapters."""


class ProviderResponseError(ProviderAdapterError):
    """Raised when a provider returns an invalid structured response."""


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
        return ""
    parts: list[str] = []
    for part in content:
        text = _value(part, "text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _json_arguments(value: Any, *, tool_name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        raise ProviderResponseError(
            f"provider returned non-JSON arguments for tool '{tool_name}'"
        )
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError as error:
        raise ProviderResponseError(
            f"provider returned invalid JSON arguments for tool '{tool_name}'"
        ) from error
    if not isinstance(parsed, Mapping):
        raise ProviderResponseError(
            f"provider returned non-object arguments for tool '{tool_name}'"
        )
    return dict(parsed)


def _normalized_tool_specs(
    tools: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    normalized = []
    for item in tools:
        if not isinstance(item, Mapping):
            raise TypeError("tool specifications must be mappings")
        name = item.get("name")
        description = item.get("description")
        parameters = item.get("parameters")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("tool specification name must be non-empty")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("tool specification description must be non-empty")
        if not isinstance(parameters, Mapping):
            raise TypeError("tool specification parameters must be a mapping")
        normalized.append(
            {
                "name": name.strip(),
                "description": description.strip(),
                "parameters": dict(parameters),
            }
        )
    return tuple(normalized)


def _usage_metadata(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    fields = (
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    result = {}
    for field in fields:
        value = _value(usage, field)
        if isinstance(value, int) and not isinstance(value, bool):
            result[field] = value
    return result


class StructuredProviderAdapter:
    """Common callable contract for one provider SDK client wrapper."""

    provider_name = "unknown"

    def __init__(
        self,
        llm_client: Any,
        *,
        provider_name: Optional[str] = None,
    ) -> None:
        sdk_client = getattr(llm_client, "client", None)
        if sdk_client is None:
            raise TypeError("llm_client must expose its provider SDK as 'client'")
        model = str(getattr(llm_client, "model", "") or "").strip()
        if not model:
            raise ValueError("structured provider requires a model id")
        self.llm_client = llm_client
        self.client = sdk_client
        self.model = model
        self.temperature = getattr(llm_client, "temperature", 0.7)
        self.max_tokens = getattr(llm_client, "max_tokens", 4000)
        config = getattr(llm_client, "config", {})
        configured_provider = (
            config.get("provider") if isinstance(config, Mapping) else None
        )
        resolved_provider = str(
            provider_name or configured_provider or self.provider_name
        ).strip().lower()
        if not resolved_provider:
            raise ValueError("structured provider requires a provider name")
        self.provider_name = resolved_provider

    def __call__(
        self,
        messages: tuple[AgentMessage, ...],
        tools: tuple[dict[str, Any], ...],
        context: Mapping[str, Any],
    ) -> AssistantTurn:
        return self.complete_turn(messages, tools, context)

    def complete_turn(
        self,
        messages: tuple[AgentMessage, ...],
        tools: tuple[dict[str, Any], ...],
        context: Mapping[str, Any],
    ) -> AssistantTurn:
        raise NotImplementedError

    def _request(self, request: Callable[..., Any], **kwargs: Any) -> Any:
        retry = getattr(self.llm_client, "_retry_request", None)
        if callable(retry):
            return retry(request, **kwargs)
        return request(**kwargs)

    @staticmethod
    def _system_prompt(
        messages: Sequence[AgentMessage],
        context: Mapping[str, Any],
    ) -> str:
        parts = []
        configured = context.get("system_prompt")
        if isinstance(configured, str) and configured.strip():
            parts.append(configured.strip())
        parts.extend(
            message.content.strip()
            for message in messages
            if message.role == "system" and message.content.strip()
        )
        return "\n\n".join(parts)

    @staticmethod
    def _metadata(
        *,
        provider: str,
        model: Any,
        response_id: Any,
        stop_reason: Any,
        usage: Any,
    ) -> Mapping[str, Any]:
        metadata = {
            "provider": provider,
            "model": str(model or ""),
            "response_id": str(response_id or ""),
            "stop_reason": str(stop_reason or ""),
            "usage": _usage_metadata(usage),
        }
        return MappingProxyType(metadata)


class OpenAIStructuredAdapter(StructuredProviderAdapter):
    """OpenAI Chat Completions and compatible structured-tool adapter."""

    provider_name = "openai"

    @staticmethod
    def _tool_calls_from_metadata(message: AgentMessage) -> list[dict[str, Any]]:
        raw_calls = message.metadata.get("tool_calls", ())
        if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes)):
            raise ProviderResponseError("assistant tool_calls metadata must be a list")
        calls = []
        for raw_call in raw_calls:
            if isinstance(raw_call, ToolCall):
                item = raw_call.to_dict()
            elif isinstance(raw_call, Mapping):
                item = dict(raw_call)
            else:
                raise ProviderResponseError("assistant tool call metadata is invalid")
            name = item.get("name")
            call_id = item.get("id")
            arguments = item.get("arguments", {})
            if not isinstance(name, str) or not name.strip():
                raise ProviderResponseError("assistant tool call name is missing")
            if not isinstance(call_id, str) or not call_id.strip():
                raise ProviderResponseError("assistant tool call id is missing")
            if not isinstance(arguments, Mapping):
                raise ProviderResponseError("assistant tool call arguments are invalid")
            calls.append(
                {
                    "id": call_id.strip(),
                    "type": "function",
                    "function": {
                        "name": name.strip(),
                        "arguments": json.dumps(
                            dict(arguments),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                }
            )
        return calls

    def _messages(
        self,
        messages: Sequence[AgentMessage],
        context: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        system_prompt = self._system_prompt(messages, context)
        if system_prompt:
            payload.append({"role": "system", "content": system_prompt})
        for message in messages:
            if message.role == "system":
                continue
            if message.role == "toolResult":
                call_id = message.metadata.get("tool_call_id")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ProviderResponseError("tool result is missing tool_call_id")
                payload.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id.strip(),
                        "content": message.content,
                    }
                )
                continue
            if message.role not in {"user", "assistant"}:
                raise ProviderResponseError(
                    f"unsupported runtime message role: {message.role}"
                )
            item: dict[str, Any] = {
                "role": message.role,
                "content": message.content,
            }
            if message.role == "assistant" and message.metadata.get("tool_calls"):
                item["tool_calls"] = self._tool_calls_from_metadata(message)
                if not message.content:
                    item["content"] = None
            payload.append(item)
        return payload

    def complete_turn(
        self,
        messages: tuple[AgentMessage, ...],
        tools: tuple[dict[str, Any], ...],
        context: Mapping[str, Any],
    ) -> AssistantTurn:
        specifications = _normalized_tool_specs(tools)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(messages, context),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if specifications:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": item["name"],
                        "description": item["description"],
                        "parameters": item["parameters"],
                    },
                }
                for item in specifications
            ]
            kwargs["tool_choice"] = "auto"

        response = self._request(self.client.chat.completions.create, **kwargs)
        choices = _value(response, "choices")
        if not isinstance(choices, Sequence) or not choices:
            raise ProviderResponseError("provider returned no completion choices")
        choice = choices[0]
        message = _value(choice, "message")
        if message is None:
            raise ProviderResponseError("provider returned no assistant message")

        parsed_calls = []
        synthetic_ids = False
        for raw_call in _value(message, "tool_calls", ()) or ():
            function = _value(raw_call, "function")
            name = _value(function, "name")
            if not isinstance(name, str) or not name.strip():
                raise ProviderResponseError("provider tool call name is missing")
            call_id = _value(raw_call, "id")
            if not isinstance(call_id, str) or not call_id.strip():
                call_id = f"call_{uuid4().hex}"
                synthetic_ids = True
            arguments = _json_arguments(
                _value(function, "arguments", "{}"),
                tool_name=name,
            )
            parsed_calls.append(ToolCall(name.strip(), arguments, id=call_id.strip()))

        metadata = dict(
            self._metadata(
                provider=self.provider_name,
                model=_value(response, "model", self.model),
                response_id=_value(response, "id"),
                stop_reason=_value(choice, "finish_reason"),
                usage=_value(response, "usage"),
            )
        )
        if synthetic_ids:
            metadata["synthetic_tool_call_ids"] = True
        try:
            return AssistantTurn(
                content=_text_content(_value(message, "content")),
                tool_calls=tuple(parsed_calls),
                metadata=metadata,
            )
        except (TypeError, ValueError) as error:
            raise ProviderResponseError(str(error)) from error


class AnthropicStructuredAdapter(StructuredProviderAdapter):
    """Anthropic Messages API structured-tool adapter."""

    provider_name = "anthropic"

    @staticmethod
    def _blocks(message: AgentMessage) -> tuple[str, list[dict[str, Any]]]:
        if message.role == "toolResult":
            call_id = message.metadata.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id.strip():
                raise ProviderResponseError("tool result is missing tool_call_id")
            return (
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id.strip(),
                        "content": message.content,
                        "is_error": bool(message.metadata.get("is_error", False)),
                    }
                ],
            )
        if message.role not in {"user", "assistant"}:
            raise ProviderResponseError(
                f"unsupported runtime message role: {message.role}"
            )
        blocks: list[dict[str, Any]] = []
        if message.content:
            blocks.append({"type": "text", "text": message.content})
        if message.role == "assistant":
            raw_calls = message.metadata.get("tool_calls", ())
            if raw_calls:
                if not isinstance(raw_calls, Sequence) or isinstance(
                    raw_calls, (str, bytes)
                ):
                    raise ProviderResponseError(
                        "assistant tool_calls metadata must be a list"
                    )
                for raw_call in raw_calls:
                    item = (
                        raw_call.to_dict()
                        if isinstance(raw_call, ToolCall)
                        else dict(raw_call)
                        if isinstance(raw_call, Mapping)
                        else None
                    )
                    if item is None:
                        raise ProviderResponseError(
                            "assistant tool call metadata is invalid"
                        )
                    call_id = item.get("id")
                    name = item.get("name")
                    arguments = item.get("arguments", {})
                    if not isinstance(call_id, str) or not call_id.strip():
                        raise ProviderResponseError("assistant tool call id is missing")
                    if not isinstance(name, str) or not name.strip():
                        raise ProviderResponseError(
                            "assistant tool call name is missing"
                        )
                    if not isinstance(arguments, Mapping):
                        raise ProviderResponseError(
                            "assistant tool call arguments are invalid"
                        )
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": call_id.strip(),
                            "name": name.strip(),
                            "input": dict(arguments),
                        }
                    )
        return message.role, blocks

    def _messages(self, messages: Sequence[AgentMessage]) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                continue
            role, blocks = self._blocks(message)
            if not blocks:
                continue
            if payload and payload[-1]["role"] == role:
                payload[-1]["content"].extend(blocks)
            else:
                payload.append({"role": role, "content": blocks})
        return payload

    def complete_turn(
        self,
        messages: tuple[AgentMessage, ...],
        tools: tuple[dict[str, Any], ...],
        context: Mapping[str, Any],
    ) -> AssistantTurn:
        specifications = _normalized_tool_specs(tools)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages(messages),
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        system_prompt = self._system_prompt(messages, context)
        if system_prompt:
            kwargs["system"] = system_prompt
        if specifications:
            kwargs["tools"] = [
                {
                    "name": item["name"],
                    "description": item["description"],
                    "input_schema": item["parameters"],
                }
                for item in specifications
            ]

        response = self._request(self.client.messages.create, **kwargs)
        content = _value(response, "content")
        if not isinstance(content, Sequence) or isinstance(content, (str, bytes)):
            raise ProviderResponseError("provider returned invalid content blocks")
        text_parts = []
        calls = []
        for block in content:
            block_type = _value(block, "type")
            if block_type == "text":
                text = _value(block, "text")
                if isinstance(text, str):
                    text_parts.append(text)
            elif block_type == "tool_use":
                name = _value(block, "name")
                call_id = _value(block, "id")
                arguments = _value(block, "input", {})
                if not isinstance(name, str) or not name.strip():
                    raise ProviderResponseError("provider tool call name is missing")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ProviderResponseError("provider tool call id is missing")
                if not isinstance(arguments, Mapping):
                    raise ProviderResponseError(
                        f"provider returned non-object arguments for tool '{name}'"
                    )
                calls.append(
                    ToolCall(name.strip(), dict(arguments), id=call_id.strip())
                )

        try:
            return AssistantTurn(
                content="".join(text_parts),
                tool_calls=tuple(calls),
                metadata=self._metadata(
                    provider=self.provider_name,
                    model=_value(response, "model", self.model),
                    response_id=_value(response, "id"),
                    stop_reason=_value(response, "stop_reason"),
                    usage=_value(response, "usage"),
                ),
            )
        except (TypeError, ValueError) as error:
            raise ProviderResponseError(str(error)) from error


class LangChainStructuredAdapter(StructuredProviderAdapter):
    """Structured-tool bridge for LangChain v1 chat models."""

    provider_name = "langchain"

    @staticmethod
    def _tool_specs(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "name": item["name"],
                "description": item["description"],
                "parameters": item["parameters"],
            }
            for item in _normalized_tool_specs(tools)
        ]

    @staticmethod
    def _messages(
        messages: Sequence[AgentMessage],
        context: Mapping[str, Any],
    ) -> list[Any]:
        try:
            from langchain_core.messages import (
                AIMessage,
                HumanMessage,
                SystemMessage,
                ToolMessage,
            )
        except ImportError as error:
            raise ProviderAdapterError(
                "langchain-core is required for structured LangChain calls"
            ) from error

        payload: list[Any] = []
        system_prompt = StructuredProviderAdapter._system_prompt(messages, context)
        if system_prompt:
            payload.append(SystemMessage(content=system_prompt))
        for message in messages:
            if message.role == "system":
                continue
            if message.role == "user":
                payload.append(HumanMessage(content=message.content))
                continue
            if message.role == "toolResult":
                call_id = message.metadata.get("tool_call_id")
                if not isinstance(call_id, str) or not call_id.strip():
                    raise ProviderResponseError("tool result is missing tool_call_id")
                payload.append(
                    ToolMessage(
                        content=message.content,
                        tool_call_id=call_id.strip(),
                    )
                )
                continue
            if message.role != "assistant":
                raise ProviderResponseError(
                    f"unsupported runtime message role: {message.role}"
                )
            raw_calls = message.metadata.get("tool_calls", ())
            tool_calls = []
            if raw_calls:
                if not isinstance(raw_calls, Sequence) or isinstance(
                    raw_calls, (str, bytes)
                ):
                    raise ProviderResponseError(
                        "assistant tool_calls metadata must be a list"
                    )
                for raw_call in raw_calls:
                    item = (
                        raw_call.to_dict()
                        if isinstance(raw_call, ToolCall)
                        else dict(raw_call)
                        if isinstance(raw_call, Mapping)
                        else None
                    )
                    if item is None:
                        raise ProviderResponseError(
                            "assistant tool call metadata is invalid"
                        )
                    name = item.get("name")
                    call_id = item.get("id")
                    arguments = item.get("arguments", {})
                    if not isinstance(name, str) or not name.strip():
                        raise ProviderResponseError(
                            "assistant tool call name is missing"
                        )
                    if not isinstance(call_id, str) or not call_id.strip():
                        raise ProviderResponseError(
                            "assistant tool call id is missing"
                        )
                    if not isinstance(arguments, Mapping):
                        raise ProviderResponseError(
                            "assistant tool call arguments are invalid"
                        )
                    tool_calls.append(
                        {
                            "name": name.strip(),
                            "args": dict(arguments),
                            "id": call_id.strip(),
                            "type": "tool_call",
                        }
                    )
            payload.append(
                AIMessage(
                    content=message.content or "",
                    tool_calls=tool_calls,
                )
            )
        return payload

    @staticmethod
    def _response_tool_calls(response: Any) -> list[ToolCall]:
        raw_calls = _value(response, "tool_calls", ()) or ()
        if not raw_calls:
            content = _value(response, "content", ())
            if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
                raw_calls = [
                    block
                    for block in content
                    if _value(block, "type") in {"tool_use", "tool_call"}
                ]
        calls = []
        for raw_call in raw_calls:
            name = _value(raw_call, "name")
            call_id = _value(raw_call, "id") or f"call_{uuid4().hex}"
            arguments = _value(raw_call, "args")
            if arguments is None:
                arguments = _value(raw_call, "input", {})
            if not isinstance(name, str) or not name.strip():
                raise ProviderResponseError("provider tool call name is missing")
            calls.append(
                ToolCall(
                    name.strip(),
                    _json_arguments(arguments, tool_name=name),
                    id=str(call_id).strip(),
                )
            )
        return calls

    def complete_turn(
        self,
        messages: tuple[AgentMessage, ...],
        tools: tuple[dict[str, Any], ...],
        context: Mapping[str, Any],
    ) -> AssistantTurn:
        tool_specs = self._tool_specs(tools)
        model = self.client.bind_tools(tool_specs) if tool_specs else self.client
        response = self._request(
            lambda: model.invoke(self._messages(messages, context))
        )
        response_metadata = _value(response, "response_metadata", {})
        usage = _value(response, "usage_metadata") or _value(
            response_metadata, "usage"
        )
        model_id = _value(response_metadata, "model") or self.model
        response_id = _value(response, "id") or _value(response_metadata, "id")
        stop_reason = _value(response_metadata, "stop_reason")
        try:
            return AssistantTurn(
                content=_text_content(_value(response, "content")),
                tool_calls=tuple(self._response_tool_calls(response)),
                metadata=self._metadata(
                    provider=self.provider_name,
                    model=model_id,
                    response_id=response_id,
                    stop_reason=stop_reason,
                    usage=usage,
                ),
            )
        except (TypeError, ValueError) as error:
            raise ProviderResponseError(str(error)) from error


def create_structured_provider_adapter(
    llm_client: Any,
    *,
    provider: Optional[str] = None,
) -> StructuredProviderAdapter:
    """Create an adapter without importing or initializing provider SDKs."""
    configured = provider
    if configured is None:
        config = getattr(llm_client, "config", {})
        configured = config.get("provider") if isinstance(config, Mapping) else None
    normalized = str(configured or "").strip().lower()
    if getattr(llm_client, "is_langchain", False):
        return LangChainStructuredAdapter(llm_client, provider_name=normalized)
    if normalized == "anthropic":
        return AnthropicStructuredAdapter(llm_client, provider_name=normalized)
    if normalized in {"openai", "custom", "zhipu", "deepseek"}:
        return OpenAIStructuredAdapter(llm_client, provider_name=normalized)
    raise ValueError(f"unsupported structured provider: {normalized or 'unknown'}")


class StructuredProviderGateway:
    """Apply ``ModelGateway`` failover to structured model turns."""

    def __init__(
        self,
        model_gateway: Any,
        *,
        adapter_factory: Callable[..., StructuredProviderAdapter] = (
            create_structured_provider_adapter
        ),
    ) -> None:
        if model_gateway is None:
            raise TypeError("model_gateway is required")
        if not callable(adapter_factory):
            raise TypeError("adapter_factory must be callable")
        self.model_gateway = model_gateway
        self.adapter_factory = adapter_factory

    def __call__(
        self,
        messages: tuple[AgentMessage, ...],
        tools: tuple[dict[str, Any], ...],
        context: Mapping[str, Any],
    ) -> AssistantTurn:
        primary_model = self.model_gateway.primary_model_id()
        attempts = self.model_gateway.model_attempts()
        if not attempts:
            raise ProviderAdapterError("no structured model is available")

        provider = str(
            getattr(self.model_gateway, "_llm_config", {}).get("provider", "")
        ).strip()
        last_error: Optional[BaseException] = None
        for model_id, client, is_fallback in attempts:
            try:
                resolved_client = client or self.model_gateway.client_for_model(
                    model_id
                )
                adapter = self.adapter_factory(
                    resolved_client,
                    provider=provider,
                )
                turn = adapter(messages, tools, context)
                actual_model = str(turn.metadata.get("model") or model_id or "")
                self.model_gateway.record_success(
                    actual_model,
                    primary_model if is_fallback else None,
                )
                metadata = dict(turn.metadata)
                metadata["requested_model_id"] = primary_model or ""
                metadata["actual_model_id"] = actual_model
                if is_fallback and primary_model:
                    metadata["fallback_from"] = primary_model
                return AssistantTurn(
                    content=turn.content,
                    tool_calls=turn.tool_calls,
                    metadata=metadata,
                )
            except Exception as error:
                last_error = error
                retryable = getattr(
                    self.model_gateway,
                    "is_retryable_model_error",
                    None,
                )
                if not callable(retryable):
                    retryable = getattr(
                        self.model_gateway,
                        "_is_retryable_model_error",
                        None,
                    )
                if not callable(retryable) or not retryable(error):
                    raise
                if model_id:
                    self.model_gateway.mark_model_unavailable(model_id)

        raise ProviderAdapterError(
            "all structured model attempts failed"
        ) from last_error
