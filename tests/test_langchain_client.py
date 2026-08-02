from types import SimpleNamespace
from unittest.mock import Mock

from artpm_agent.providers import LangChainStructuredAdapter
from artpm_agent.runtime import AgentMessage
from artpm_agent.utils.langchain_client import LangChainClient
from artpm_agent.utils import llm_client as llm_client_module
from artpm_agent.utils.llm_client import BaseLLMClient, create_llm_client


def _client(model):
    client = object.__new__(LangChainClient)
    BaseLLMClient.__init__(
        client,
        {
            "model": "test-model",
            "temperature": 0,
            "max_tokens": 128,
            "retry_max_attempts": 1,
        },
    )
    client.provider = "custom"
    client.model_client = model
    client.client = model
    client.last_usage = None
    return client


def test_langchain_client_invokes_with_sanitized_history_and_usage():
    response = SimpleNamespace(
        content="answer",
        usage_metadata={"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
    )
    model = Mock()
    model.invoke.return_value = response
    client = _client(model)

    assert client.chat(
        "current",
        system_prompt="trusted",
        history=[
            {"role": "user", "content": "previous"},
            {"role": "system", "content": "untrusted"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "current"},
        ],
    ) == "answer"

    messages = model.invoke.call_args.args[0]
    assert [item["role"] for item in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[-1]["content"] == "current"
    assert client.last_usage == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
    }


def test_langchain_client_streams_text_and_captures_usage():
    model = Mock()
    model.stream.return_value = iter(
        [
            SimpleNamespace(content="first", usage_metadata=None),
            SimpleNamespace(
                content=" second",
                usage_metadata={
                    "input_tokens": 8,
                    "output_tokens": 3,
                    "total_tokens": 11,
                },
            ),
        ]
    )
    client = _client(model)

    assert list(client.stream_chat("hello")) == ["first", " second"]
    assert client.last_usage["total_tokens"] == 11


def test_langchain_client_captures_provider_prompt_cache_tokens():
    response = SimpleNamespace(
        content="answer",
        usage_metadata={
            "input_tokens": 20,
            "output_tokens": 4,
            "total_tokens": 24,
        },
        response_metadata={"usage": {"prompt_cache_hit_tokens": 12}},
    )
    model = Mock()
    model.invoke.return_value = response
    client = _client(model)

    assert client.chat("current") == "answer"
    assert client.last_usage["cached_tokens"] == 12


def test_langchain_multimodal_stream_is_closed_on_consumer_error(monkeypatch):
    class ClosingStream:
        def __init__(self):
            self.closed = False

        def __iter__(self):
            yield SimpleNamespace(content="first", usage_metadata=None)
            raise RuntimeError("stream failed")

        def close(self):
            self.closed = True

    stream = ClosingStream()
    model = Mock()
    model.stream.return_value = stream
    client = _client(model)
    monkeypatch.setattr(
        client,
        "_multimodal_messages",
        lambda *_args, **_kwargs: [{"role": "user", "content": "image"}],
    )

    iterator = client.stream_chat_with_images("inspect", ["unused.png"])
    assert next(iterator) == "first"
    try:
        next(iterator)
    except RuntimeError as error:
        assert str(error) == "stream failed"
    else:
        raise AssertionError("stream failure should propagate")

    assert stream.closed is True


def test_langchain_structured_adapter_converts_tool_calls():
    response = SimpleNamespace(
        content="checking",
        tool_calls=[
            {
                "id": "call-1",
                "name": "lookup",
                "args": {"query": "Atlas"},
            }
        ],
        response_metadata={"model": "actual-model", "id": "resp-1"},
        usage_metadata={"input_tokens": 10, "output_tokens": 5},
    )
    bound_model = Mock()
    bound_model.invoke.return_value = response
    model = Mock()
    model.bind_tools.return_value = bound_model
    client = _client(model)
    adapter = LangChainStructuredAdapter(client, provider_name="custom")

    turn = adapter(
        (AgentMessage(role="user", content="Find Atlas"),),
        (
            {
                "name": "lookup",
                "description": "Look up a project",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        ),
        {},
    )

    assert turn.content == "checking"
    assert turn.tool_calls[0].to_dict() == {
        "id": "call-1",
        "name": "lookup",
        "arguments": {"query": "Atlas"},
    }
    assert turn.metadata["provider"] == "custom"
    assert turn.metadata["model"] == "actual-model"
    assert turn.metadata["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert model.bind_tools.call_args.args[0][0]["name"] == "lookup"


def test_langchain_client_rejects_missing_provider_key():
    client = object.__new__(LangChainClient)
    BaseLLMClient.__init__(client, {"model": "test-model"})
    client.provider = "custom"
    try:
        client._build_model(
            {
                "provider": "custom",
                "model": "test-model",
                "openai_api_key": "",
            }
        )
    except ValueError as error:
        assert "API key" in str(error)
    else:
        raise AssertionError("missing API key should fail closed")


def test_langchain_import_failure_marks_native_fallback_honestly(monkeypatch):
    class NativeClient:
        is_langchain = False

        def __init__(self, config):
            self.config = config
            self.framework = config["framework"]

    monkeypatch.setattr(
        LangChainClient,
        "_build_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ImportError("integration unavailable")
        ),
    )
    monkeypatch.setattr(llm_client_module, "OpenAIClient", NativeClient)

    client = create_llm_client(
        {
            "provider": "openai",
            "framework": "langchain",
            "model": "test-model",
            "openai_api_key": "sk-test-key",
        }
    )

    assert client.framework == "native"
    assert client.config["framework_fallback_from"] == "langchain"


def test_unknown_framework_marks_native_fallback_honestly(monkeypatch):
    class NativeClient:
        is_langchain = False

        def __init__(self, config):
            self.config = config
            self.framework = config["framework"]

    monkeypatch.setattr(llm_client_module, "OpenAIClient", NativeClient)

    client = create_llm_client(
        {
            "provider": "openai",
            "framework": "future-framework",
            "model": "test-model",
            "openai_api_key": "sk-test-key",
        }
    )

    assert client.framework == "native"
    assert client.config["framework_fallback_from"] == "future-framework"
