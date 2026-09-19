from __future__ import annotations

from collections.abc import Mapping
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

import artpm_agent.request_orchestrator as orchestrator_module
from artpm_agent.harness import LocalHarnessRuntime, TurnContext, run_turn
from artpm_agent.harness.model_handler import fallback_to_model
from artpm_agent.request_orchestrator import RequestOrchestrator
from artpm_agent.routing.service import IntentDecision
from artpm_agent.runtime import (
    AgentLoop,
    AssistantTurn,
    ToolCall,
    build_capability_registry,
)
from artpm_agent.runtime.factory import RuntimeFactory
from artpm_agent.runtime.request_services import TurnServiceBundle
from artpm_agent.runtime.storage_registry import StorageRegistry
from artpm_agent.tenancy import TenantContext


class _Config:
    values: ClassVar[dict[str, Any]] = {
        "agent_runtime.model_tool_calls_enabled": True,
        "agent_runtime.max_turns": 4,
        "agent_runtime.max_tool_calls_per_turn": 4,
    }

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


class _Skill:
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    @staticmethod
    def validate(inputs: Mapping[str, Any]) -> tuple[bool, str]:
        return (bool(str(inputs.get("query") or "").strip()), "query is required")


class _Router:
    def __init__(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        *,
        tenant_context: TenantContext | None = None,
    ) -> None:
        self.calls = calls
        self.tenant_context = tenant_context
        self.skills = {"scoped_lookup": _Skill()}

    def for_tenant(self, tenant_context: TenantContext) -> _Router:
        return _Router(self.calls, tenant_context=tenant_context)

    def list_skills(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "scoped_lookup",
                "description": "Read one workspace-scoped record",
                "read_only": True,
                "requires_approval": False,
                "risk": "low",
            }
        ]

    def execute_skill(
        self,
        name: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        assert self.tenant_context is not None
        self.calls.append((name, dict(inputs)))
        return {"success": True, "answer": "scoped result"}


class _UnscopedMCP:
    enabled = True

    @staticmethod
    def list_tools() -> list[dict[str, Any]]:
        return [
            {
                "name": "unscoped_mcp",
                "description": "Must not enter a tenant-scoped registry",
                "input_schema": {"type": "object", "properties": {}},
            }
        ]

    @staticmethod
    def call_tool(_name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("unscoped MCP tool must not execute")


def _agent(provider, calls: list[tuple[str, dict[str, Any]]]) -> RequestOrchestrator:
    agent = object.__new__(RequestOrchestrator)
    agent.config = _Config()
    agent.router = _Router(calls)
    agent.mcp_client = _UnscopedMCP()
    agent.tool_registry = build_capability_registry(
        skill_router=agent.router,
        mcp_client=agent.mcp_client,
    )
    agent.structured_provider = provider
    agent._model_tool_session_store = None
    agent.__dict__["_standalone_llm_client"] = object()
    agent.__dict__["_standalone_last_response_model"] = "structured-model"
    agent.__dict__["_standalone_model_fallback_from"] = None
    agent.memory = None
    agent.tencentdb_memory = None
    agent._detect_intent = lambda _prompt: None
    agent._detect_intent_decision = lambda _prompt: IntentDecision()
    agent._skill_input_with_history = lambda prompt, _context: prompt
    agent._extract_inputs = lambda *_args: {}
    agent._format_skill_result = lambda _name, result: str(result)
    agent._build_system_prompt = lambda *_args: "system prompt"
    agent._needs_visual_semantics = lambda _prompt: False
    agent._vision_attachment_paths = lambda *_args: nullcontext([])
    agent._chat_with_model_failover = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("canonical model tool turn must not use plain model fallback")
    )
    agent.close = lambda: None
    return agent


def test_canonical_harness_runs_scoped_agent_loop_with_factory_session_store(
    tmp_path,
    monkeypatch,
) -> None:
    provider_tools: list[set[str]] = []
    provider_contexts: list[dict[str, Any]] = []
    provider_calls = 0

    def provider(_messages, tools, context):
        nonlocal provider_calls
        provider_calls += 1
        provider_tools.append({str(tool["name"]) for tool in tools})
        provider_contexts.append(dict(context))
        if provider_calls == 1:
            return AssistantTurn(
                tool_calls=(
                    ToolCall(
                        "scoped_lookup",
                        {"query": "current project"},
                        id="call-scoped",
                    ),
                )
            )
        return AssistantTurn(
            "structured answer",
            metadata={"model": "structured-model"},
        )

    created_loops: list[AgentLoop] = []

    class RecordingAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            created_loops.append(self)

    monkeypatch.setattr(orchestrator_module, "AgentLoop", RecordingAgentLoop)

    storage = StorageRegistry(
        db_path=tmp_path / "runtime.sqlite",
        business_db_path=tmp_path / "business.sqlite",
        vector_store_path=tmp_path / "vectors",
        enable_vector_search=False,
    )
    tenant_context = TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="alice",
    )
    storage.conversation.create_workspace(
        "workspace-a",
        "Workspace A",
        tenant_id="tenant-a",
        profile_id="profile-a",
    )
    conversation = storage.conversation.create_conversation(
        "Harness model tools",
        workspace_id="workspace-a",
    )
    calls: list[tuple[str, dict[str, Any]]] = []
    agent = _agent(provider, calls)

    def reject_fallback_session_store():
        raise AssertionError("canonical Harness must inject the factory session store")

    agent._get_model_tool_session_store = reject_fallback_session_store
    factory = RuntimeFactory(storage=storage, agent_factory=lambda: agent)
    services = TurnServiceBundle(session_store=factory.storage.session)
    runtime = LocalHarnessRuntime(
        factory.agent(),
        services=services,
        tenant_context=tenant_context,
    ).for_tenant(tenant_context)
    context = TurnContext(
        turn_id="turn-model-tools",
        conversation_id=conversation["id"],
        user_input="Use the scoped lookup tool for this project",
        agent_profile=SimpleNamespace(profile_id="profile-a"),
        runtime=runtime,
        services=services,
        extra={
            "tenant_context": tenant_context,
            "profile_id": "profile-a",
            "run_id": "run-model-tools",
        },
    )

    result = run_turn(context)

    assert result.success is True
    assert result.handled_by == "model_tool_loop"
    assert result.response == "structured answer"
    assert len(created_loops) == 1
    assert provider_calls == 2
    assert provider_tools == [{"scoped_lookup"}, {"scoped_lookup"}]
    assert all("unscoped_mcp" not in names for names in provider_tools)
    assert provider_contexts[0]["tenant_id"] == "tenant-a"
    assert provider_contexts[0]["workspace_id"] == "workspace-a"
    assert provider_contexts[0]["profile_id"] == "profile-a"
    assert calls == [
        (
            "scoped_lookup",
            {
                "query": "current project",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "profile_id": "profile-a",
            },
        )
    ]
    assert services.session_store is factory.storage.session
    assert services.session_store.conversations is factory.storage.conversation
    assert Path(services.session_store.db_path).resolve() == factory.storage.db_path
    entries = services.session_store.replay(
        conversation["id"],
        workspace_id="workspace-a",
    )
    assert any(
        entry.tool_call
        and entry.tool_call.get("name") == "scoped_lookup"
        for entry in entries
    )
    factory.close()


def test_model_tool_skill_rejects_forged_profile_scope() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    agent = _agent(lambda *_args: AssistantTurn("unused"), calls)
    tenant_context = TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="alice",
    )

    loop = agent.create_agent_loop(
        tenant_context=tenant_context,
        profile_id="profile-a",
    )
    tool = loop.registry.get("scoped_lookup")

    assert tool is not None
    assert loop.registry.get("unscoped_mcp") is None
    with pytest.raises(ValueError, match="profile_id does not match"):
        tool.prepare(
            {
                "query": "current project",
                "profile_id": "profile-other",
            }
        )
    assert calls == []


def test_model_handler_replaces_forged_scope_values_before_structured_tools():
    captured: dict[str, Any] = {}

    class Runtime:
        model_available = True
        supports_response_cache_scope = False

        @staticmethod
        def build_system_prompt(*_args):
            return "system"

        @staticmethod
        def needs_visual_semantics(*_args):
            return False

        @staticmethod
        def vision_attachment_paths(*_args):
            return nullcontext([])

        @staticmethod
        def run_model_tool_turn(_prompt, **kwargs):
            captured.update(kwargs)
            return "answer", {"model": "structured-model"}

    context = SimpleNamespace(
        runtime=Runtime(),
        agent_profile=SimpleNamespace(profile_id="profile-trusted"),
        knowledge_context="trusted knowledge",
        conversation_history=[],
        conversation_id="conversation-trusted",
        turn_id="turn-trusted",
        user_input="Use a tool",
        services=None,
        scope=SimpleNamespace(
            tenant_id="tenant-trusted",
            workspace_id="workspace-trusted",
        ),
        extra={
            "tenant_context": SimpleNamespace(
                tenant_id="tenant-context",
                workspace_id="workspace-context",
            ),
            "tenant_id": "tenant-forged",
            "workspace_id": "workspace-forged",
            "conversation_id": "conversation-forged",
            "profile_id": "profile-forged",
            "agent_profile": SimpleNamespace(profile_id="profile-forged"),
        },
    )

    result = fallback_to_model(context, [], "")

    assert result.success is True
    assert result.handled_by == "model_tool_loop"
    assert captured["conversation_id"] == "conversation-trusted"
    assert captured["workspace_id"] == "workspace-trusted"
    assert captured["context"]["tenant_id"] == "tenant-trusted"
    assert captured["context"]["workspace_id"] == "workspace-trusted"
    assert captured["context"]["conversation_id"] == "conversation-trusted"
    assert captured["context"]["profile_id"] == "profile-trusted"
    assert captured["context"]["agent_profile"].profile_id == "profile-trusted"
