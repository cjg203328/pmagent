from artpm_agent.config import Config


def test_model_tool_calls_fail_closed_by_default(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_TOOL_CALLS_ENABLED", raising=False)

    assert Config().get("agent_runtime.model_tool_calls_enabled") is False


def test_model_tool_call_gate_accepts_explicit_boolean_env(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_TOOL_CALLS_ENABLED", "true")
    assert Config().get("agent_runtime.model_tool_calls_enabled") is True

    monkeypatch.setenv("AGENT_MODEL_TOOL_CALLS_ENABLED", "off")
    assert Config().get("agent_runtime.model_tool_calls_enabled") is False
