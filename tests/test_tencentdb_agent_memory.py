from __future__ import annotations

from types import SimpleNamespace

import pytest

from artpm_agent.harness import (
    BaseHarnessRuntime,
    RuntimeCapabilities,
    TurnContext,
    run_turn,
)
from artpm_agent.harness.memory_retrieval import inject_memory_context
from artpm_agent.memory.tencentdb_agent_memory import (
    TencentDBAgentMemoryClient,
    TencentDBAgentMemoryConfigurationError,
    TencentDBAgentMemoryScope,
    TencentDBAgentMemorySettings,
    create_tencentdb_agent_memory_client,
)
from artpm_agent.tenancy import TenantContext


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def post(self, url, *, json, headers, timeout):
        self.calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def _settings(**overrides):
    values = {
        "enabled": True,
        "base_url": "http://127.0.0.1:8420",
        "api_key": "gateway-key",
        "scope_secret": "test-scope-secret",
    }
    values.update(overrides)
    return TencentDBAgentMemorySettings(**values)


def _scope(workspace_id: str = "workspace-a"):
    return TencentDBAgentMemoryScope(
        tenant_id="tenant-a",
        workspace_id=workspace_id,
        principal_id="principal-a",
        conversation_id="conversation-a",
    )


def test_client_uses_opaque_scope_keys_for_recall_and_capture():
    session = FakeSession(
        [
            FakeResponse({"context": "stored preference", "code": 0}),
            FakeResponse({"l0_recorded": 1, "scheduler_notified": True}),
        ]
    )
    client = TencentDBAgentMemoryClient(_settings(), session=session)

    assert client.recall("How should I format the report?", _scope()) == "stored preference"
    client.capture("Use Chinese", "I will use Chinese", _scope())

    recall_call, capture_call = session.calls
    assert recall_call["url"] == "http://127.0.0.1:8420/recall"
    assert capture_call["url"] == "http://127.0.0.1:8420/capture"
    assert recall_call["headers"]["Authorization"] == "Bearer gateway-key"
    assert recall_call["json"]["session_key"] == capture_call["json"]["session_key"]
    assert capture_call["json"]["session_id"] != capture_call["json"]["session_key"]
    assert "tenant-a" not in str(capture_call["json"])
    assert "workspace-a" not in str(capture_call["json"])
    assert recall_call["json"]["session_key"] != client._opaque_key("scope", _scope("workspace-b"))


def test_client_requires_secure_remote_gateway_configuration():
    with pytest.raises(TencentDBAgentMemoryConfigurationError, match="HTTPS"):
        _settings(base_url="http://memory.internal:8420")

    assert create_tencentdb_agent_memory_client({"enabled": False}) is None


class RecordingMemory:
    settings = SimpleNamespace(agent_id="artpm-agent")

    def __init__(self, context: str = ""):
        self.context = context
        self.recall_scopes = []
        self.captures = []

    def recall(self, _query, scope):
        self.recall_scopes.append(scope)
        return self.context

    def capture(self, user_content, assistant_content, scope):
        self.captures.append((user_content, assistant_content, scope))


class EmptyStore:
    def active(self):
        return []


def _tenant_context():
    return TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="principal-a",
    )


def test_inject_memory_context_uses_trusted_tenant_scope():
    memory = RecordingMemory("TencentDB recall result")
    context = SimpleNamespace(
        agent=None,
        conversation_id="conversation-a",
        user_input="What is my preferred report format?",
        conversation_history=[],
        knowledge_context="",
        extra={"tenant_context": _tenant_context(), "workspace_id": "workspace-a"},
    )

    inject_memory_context(
        context,
        tencentdb_memory=memory,
        feedback_store=EmptyStore(),
        strategy_store=EmptyStore(),
    )

    assert "TencentDB Agent Memory" in context.knowledge_context
    assert "TencentDB recall result" in context.knowledge_context
    assert memory.recall_scopes[0].tenant_id == "tenant-a"
    assert memory.recall_scopes[0].workspace_id == "workspace-a"


def test_inject_memory_context_rejects_untrusted_nonlocal_scope():
    memory = RecordingMemory("must not be recalled")
    context = SimpleNamespace(
        agent=None,
        conversation_id="conversation-a",
        user_input="query",
        conversation_history=[],
        knowledge_context="",
        extra={
            "tenant_context": {"tenant_id": "tenant-a"},
            "workspace_id": "workspace-a",
        },
    )

    inject_memory_context(
        context,
        tencentdb_memory=memory,
        feedback_store=EmptyStore(),
        strategy_store=EmptyStore(),
    )

    assert memory.recall_scopes == []


class CaptureRuntime(BaseHarnessRuntime):
    capabilities = RuntimeCapabilities(direct_response=True)

    def __init__(self, memory):
        self.tencentdb_memory = memory

    def chat(self, _user_input, *, context=None):
        del context
        return "final answer"


def test_run_turn_captures_only_successful_responses():
    memory = RecordingMemory()
    context = TurnContext(
        turn_id="turn-a",
        conversation_id="conversation-a",
        user_input="remember this",
        runtime=CaptureRuntime(memory),
        extra={"tenant_context": _tenant_context(), "workspace_id": "workspace-a"},
    )

    result = run_turn(context)

    assert result.success is True
    assert len(memory.captures) == 1
    user_content, assistant_content, scope = memory.captures[0]
    assert user_content == "remember this"
    assert assistant_content == "final answer"
    assert scope.conversation_id == "conversation-a"
