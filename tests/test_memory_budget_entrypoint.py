from artpm_agent.internal.chat_harness_integration import (
    DEFAULT_MEMORY_CONTEXT_MAX_TOKENS,
    execute_turn_with_harness,
    resolve_memory_context_token_budget,
)
from artpm_agent.harness.turn_service import TurnResult


class _Config:
    def __init__(self, value):
        self.value = value

    def get(self, key, default=None):
        assert key == "agent_runtime.memory_context_max_tokens"
        return self.value if self.value is not None else default


class _Agent:
    def __init__(self, value=None):
        self.config = _Config(value)


def test_entrypoint_uses_bounded_default_when_not_configured():
    assert (
        resolve_memory_context_token_budget(_Agent())
        == DEFAULT_MEMORY_CONTEXT_MAX_TOKENS
    )


def test_entrypoint_prefers_config_and_preserves_explicit_opt_out():
    assert resolve_memory_context_token_budget(_Agent(640)) == 640
    assert resolve_memory_context_token_budget(_Agent(640), 0) == 0
    assert resolve_memory_context_token_budget(_Agent(640), 256) == 256


def test_entrypoint_clamps_malformed_large_values():
    assert resolve_memory_context_token_budget(_Agent(100000)) == 8192
    assert resolve_memory_context_token_budget(_Agent("invalid")) == 0
    assert resolve_memory_context_token_budget(_Agent(True)) == 0


def test_harness_entrypoint_activates_default_memory_budget(monkeypatch):
    import artpm_agent.harness.outcome_recorder as outcome_recorder
    import artpm_agent.internal.chat_harness_integration as integration

    captured = {}

    def fake_run_turn(ctx, **_kwargs):
        captured["ctx"] = ctx
        return TurnResult(response="ok", handled_by="model")

    monkeypatch.setattr(integration, "run_turn", fake_run_turn)
    monkeypatch.setattr(integration, "_record_evolution_event", lambda *_a, **_k: None)
    monkeypatch.setattr(outcome_recorder, "record_outcome", lambda *_a, **_k: None)

    response, awaiting, _metadata, _result = execute_turn_with_harness(
        _Agent(),
        "hello",
        "turn-1",
        "conversation-1",
        {"conversation_history": []},
        [],
        [],
        None,
        None,
        None,
        auto_reflect=False,
    )

    assert response == "ok"
    assert awaiting is False
    assert (
        captured["ctx"].extra["memory_inject_max_tokens"]
        == DEFAULT_MEMORY_CONTEXT_MAX_TOKENS
    )
