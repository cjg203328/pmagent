from __future__ import annotations

from types import SimpleNamespace

import pytest

from artpm_agent.providers import (
    AnthropicStructuredAdapter,
    OpenAIStructuredAdapter,
    ProviderResponseError,
    StructuredProviderGateway,
    create_structured_provider_adapter,
)
from artpm_agent.runtime import AgentMessage


TOOL_SPEC = (
    {
        "name": "lookup",
        "description": "Look up a project",
        "parameters": {
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
    },
)


class _Wrapper:
    def __init__(self, provider, sdk, *, model="test-model"):
        self.config = {"provider": provider}
        self.client = sdk
        self.model = model
        self.temperature = 0.2
        self.max_tokens = 321
        self.retry_calls = 0

    def _retry_request(self, request, *args, **kwargs):
        self.retry_calls += 1
        return request(*args, **kwargs)


class _Recorder:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _history():
    return (
        AgentMessage(role="system", content="System from history"),
        AgentMessage(role="user", content="Find Atlas"),
        AgentMessage(
            role="assistant",
            content="",
            metadata={
                "tool_calls": [
                    {
                        "id": "prior-call",
                        "name": "lookup",
                        "arguments": {"query": "Atlas"},
                    }
                ]
            },
        ),
        AgentMessage(
            role="toolResult",
            content="prior result",
            metadata={
                "tool_call_id": "prior-call",
                "tool_name": "lookup",
                "is_error": False,
            },
        ),
    )


def test_openai_adapter_serializes_messages_tools_and_parses_tool_calls():
    response = SimpleNamespace(
        id="response-1",
        model="actual-model",
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=4,
            total_tokens=14,
        ),
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content="checking",
                    tool_calls=[
                        SimpleNamespace(
                            id="call-2",
                            function=SimpleNamespace(
                                name="lookup",
                                arguments='{"query":"Beta"}',
                            ),
                        )
                    ],
                ),
            )
        ],
    )
    recorder = _Recorder(response)
    wrapper = _Wrapper(
        "openai",
        SimpleNamespace(chat=SimpleNamespace(completions=recorder)),
    )

    turn = OpenAIStructuredAdapter(wrapper)(
        _history(),
        TOOL_SPEC,
        {"system_prompt": "Configured system"},
    )

    assert turn.content == "checking"
    assert turn.tool_calls[0].to_dict() == {
        "id": "call-2",
        "name": "lookup",
        "arguments": {"query": "Beta"},
    }
    assert turn.metadata["provider"] == "openai"
    assert turn.metadata["model"] == "actual-model"
    assert turn.metadata["stop_reason"] == "tool_calls"
    assert turn.metadata["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }
    assert wrapper.retry_calls == 1

    request = recorder.kwargs
    assert request["model"] == "test-model"
    assert request["max_tokens"] == 321
    assert request["tool_choice"] == "auto"
    assert request["tools"][0]["function"]["parameters"] == TOOL_SPEC[0]["parameters"]
    assert request["messages"][0] == {
        "role": "system",
        "content": "Configured system\n\nSystem from history",
    }
    assert request["messages"][-2]["tool_calls"][0]["id"] == "prior-call"
    assert request["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "prior-call",
        "content": "prior result",
    }


def test_openai_adapter_never_guesses_tool_calls_from_text():
    response = {
        "id": "response-text",
        "model": "test-model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": '<tool_call>{"name":"lookup"}</tool_call>',
                    "tool_calls": None,
                },
            }
        ],
    }
    recorder = _Recorder(response)
    wrapper = _Wrapper(
        "custom",
        SimpleNamespace(chat=SimpleNamespace(completions=recorder)),
    )

    turn = OpenAIStructuredAdapter(wrapper)(
        (AgentMessage(role="user", content="hello"),),
        TOOL_SPEC,
        {},
    )

    assert turn.content.startswith("<tool_call>")
    assert turn.tool_calls == ()
    assert turn.metadata["provider"] == "custom"


def test_openai_adapter_rejects_invalid_structured_arguments():
    response = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-bad",
                            "function": {
                                "name": "lookup",
                                "arguments": "not-json",
                            },
                        }
                    ],
                },
            }
        ]
    }
    wrapper = _Wrapper(
        "openai",
        SimpleNamespace(chat=SimpleNamespace(completions=_Recorder(response))),
    )

    with pytest.raises(ProviderResponseError, match="invalid JSON arguments"):
        OpenAIStructuredAdapter(wrapper)(
            (AgentMessage(role="user", content="hello"),),
            TOOL_SPEC,
            {},
        )


def test_anthropic_adapter_serializes_tool_results_and_parses_content_blocks():
    response = SimpleNamespace(
        id="msg-1",
        model="claude-test",
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=20, output_tokens=8),
        content=[
            SimpleNamespace(type="text", text="checking"),
            SimpleNamespace(
                type="tool_use",
                id="call-3",
                name="lookup",
                input={"query": "Gamma"},
            ),
        ],
    )
    recorder = _Recorder(response)
    wrapper = _Wrapper(
        "anthropic",
        SimpleNamespace(messages=recorder),
        model="claude-configured",
    )

    turn = AnthropicStructuredAdapter(wrapper)(
        _history(),
        TOOL_SPEC,
        {"system_prompt": "Configured system"},
    )

    assert turn.content == "checking"
    assert turn.tool_calls[0].to_dict() == {
        "id": "call-3",
        "name": "lookup",
        "arguments": {"query": "Gamma"},
    }
    assert turn.metadata["provider"] == "anthropic"
    assert turn.metadata["usage"] == {"input_tokens": 20, "output_tokens": 8}

    request = recorder.kwargs
    assert request["system"] == "Configured system\n\nSystem from history"
    assert request["tools"][0]["input_schema"] == TOOL_SPEC[0]["parameters"]
    assert request["messages"][-1]["role"] == "user"
    assert request["messages"][-1]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "prior-call",
        "content": "prior result",
        "is_error": False,
    }


@pytest.mark.parametrize(
    ("provider", "adapter_type"),
    [
        ("anthropic", AnthropicStructuredAdapter),
        ("openai", OpenAIStructuredAdapter),
        ("custom", OpenAIStructuredAdapter),
        ("zhipu", OpenAIStructuredAdapter),
    ],
)
def test_structured_adapter_factory_uses_configured_provider(
    provider,
    adapter_type,
):
    wrapper = _Wrapper(provider, object())
    assert isinstance(create_structured_provider_adapter(wrapper), adapter_type)


def test_structured_adapter_factory_rejects_unknown_provider():
    wrapper = _Wrapper("unknown", object())
    with pytest.raises(ValueError, match="unsupported structured provider"):
        create_structured_provider_adapter(wrapper)


def test_structured_gateway_fails_over_before_returning_a_turn():
    records = []

    def primary(*_args):
        raise TimeoutError("timeout")

    def fallback(*_args):
        return SimpleNamespace(
            content="fallback answer",
            tool_calls=(),
            metadata={"model": "fallback-model", "provider": "openai"},
        )

    class Gateway:
        _llm_config = {"provider": "openai"}

        def primary_model_id(self):
            return "primary-model"

        def model_attempts(self):
            return [
                ("primary-model", primary, False),
                ("fallback-model", fallback, True),
            ]

        def client_for_model(self, model_id):
            raise AssertionError(model_id)

        def is_retryable_model_error(self, error):
            return isinstance(error, TimeoutError)

        def mark_model_unavailable(self, model_id):
            records.append(("unavailable", model_id))

        def record_success(self, model_id, fallback_from=None):
            records.append(("success", model_id, fallback_from))

    provider = StructuredProviderGateway(
        Gateway(),
        adapter_factory=lambda client, **_kwargs: client,
    )
    turn = provider(
        (AgentMessage(role="user", content="hello"),),
        TOOL_SPEC,
        {},
    )

    assert turn.content == "fallback answer"
    assert turn.metadata["actual_model_id"] == "fallback-model"
    assert turn.metadata["fallback_from"] == "primary-model"
    assert records == [
        ("unavailable", "primary-model"),
        ("success", "fallback-model", "primary-model"),
    ]
