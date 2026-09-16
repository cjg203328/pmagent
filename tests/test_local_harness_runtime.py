"""Contract tests for the canonical local Harness host."""

from __future__ import annotations

from collections.abc import Mapping
from threading import Event, Thread
from pathlib import Path

from artpm_agent.harness import (
    BaseHarnessRuntime,
    LegacyAgentRuntimeAdapter,
    LocalHarnessRuntime,
    RuntimeCapabilities,
    TurnContext,
    run_turn,
)
from artpm_agent.runtime.counters import counter_snapshot, reset_counters
from artpm_agent.tenancy import TenantContext


class _Router:
    skills = {}

    def __init__(self, bound: TenantContext | None = None) -> None:
        self.bound = bound

    def for_tenant(self, context: TenantContext) -> "_Router":
        return _Router(context)


class _Agent:
    def __init__(self) -> None:
        self.router = _Router()
        self.llm_client = None

    def chat(self, prompt: str, *, context: Mapping[str, object] | None = None) -> str:
        del context
        return f"echo:{prompt}"


def test_local_runtime_is_legacy_compatible_and_tenant_scoped() -> None:
    agent = _Agent()
    runtime = LocalHarnessRuntime(agent)
    tenant = TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )

    scoped = runtime.for_tenant(tenant)

    assert isinstance(runtime, LegacyAgentRuntimeAdapter)
    assert isinstance(scoped, LocalHarnessRuntime)
    assert scoped.agent is agent
    assert scoped._router.bound == tenant


def test_local_runtime_builds_context_with_explicit_runtime() -> None:
    runtime = LocalHarnessRuntime(_Agent())
    tenant = TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )

    context = runtime.build_turn_context(
        "hello",
        turn_id="turn-1",
        conversation_id="conversation-1",
        conversation_history=[{"role": "user", "content": "previous"}],
        extra={"source": "test"},
        tenant_context=tenant,
    )

    assert context.runtime is runtime
    assert context.agent is None
    assert context.scope.workspace_id == "workspace-a"
    assert context.conversation_history == [{"role": "user", "content": "previous"}]


def test_local_runtime_run_turn_uses_canonical_thin_path() -> None:
    runtime = LocalHarnessRuntime(_Agent())

    result = runtime.run_turn(
        runtime.build_turn_context(
            "hello",
            turn_id="turn-2",
            conversation_id="conversation-2",
        )
    )

    assert result.success is True
    assert result.response == "echo:hello"
    assert result.handled_by == "thin_agent_chat"
    assert result.metadata["runtime_kind"] == "local"


def test_run_turn_replays_the_same_context_without_running_the_agent_twice() -> None:
    reset_counters()
    agent = _Agent()
    runtime = LocalHarnessRuntime(agent)
    context = runtime.build_turn_context(
        "hello",
        turn_id="turn-idempotent",
        conversation_id="conversation-idempotent",
    )

    first = run_turn(context)
    second = run_turn(context)

    assert first.response == second.response == "echo:hello"
    assert second.metadata["idempotent_replay"] is True
    assert counter_snapshot()["harness.turns.duplicate_replays"] == 1
    reset_counters()


def test_run_turn_rejects_concurrent_reuse_of_a_context() -> None:
    reset_counters()
    runtime = LocalHarnessRuntime(_Agent())
    context = runtime.build_turn_context(
        "hello",
        turn_id="turn-concurrent",
        conversation_id="conversation-concurrent",
    )
    entered = Event()
    release = Event()
    first_result: list[object] = []

    def response_handler(_context: object) -> str:
        entered.set()
        release.wait(timeout=2)
        return "done"

    def first_call() -> None:
        first_result.append(run_turn(context, response_handler=response_handler))

    worker = Thread(target=first_call)
    worker.start()
    assert entered.wait(timeout=2)
    duplicate = run_turn(context, response_handler=response_handler)
    release.set()
    worker.join(timeout=2)

    assert duplicate.success is False
    assert duplicate.error == "turn_in_progress"
    assert len(first_result) == 1
    assert first_result[0].success is True
    assert counter_snapshot()["harness.turns.concurrent_duplicates"] == 1
    reset_counters()


def test_chat_page_does_not_reenter_legacy_response_paths() -> None:
    source = Path("artpm_agent/views/chat.py").read_text(encoding="utf-8")

    assert "turn_runtime.chat(" not in source
    assert "stream_agent_response(" not in source
    assert "response_handler=respond_from_public_agent_api" not in source


def test_canonical_model_stream_is_consumed_once_by_harness() -> None:
    rendered: list[str] = []

    class StreamingRuntime(BaseHarnessRuntime):
        capabilities = RuntimeCapabilities(
            turn_processing=True,
            model_chat=True,
        )
        model_available = True

        def build_system_prompt(self, _profile: object, _knowledge: str) -> str:
            return "system"

        def stream_with_failover(self, *_args: object, **_kwargs: object):
            yield "one"
            yield "two"

        def chat_with_failover(self, *_args: object, **_kwargs: object) -> str:
            raise AssertionError("streaming turn must not execute a second chat")

    context = TurnContext(
        turn_id="stream-turn",
        conversation_id="stream-conversation",
        user_input="answer this",
        runtime=StreamingRuntime(),
        extra={"response_stream_callback": rendered.append},
    )

    result = run_turn(context)

    assert result.success is True
    assert result.response == "onetwo"
    assert result.response_rendered is True
    assert result.metadata["streamed"] is True
    assert rendered == ["one", "two"]
