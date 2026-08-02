import json

from artpm_agent.tools.check_config import check_provider_model_alignment


def test_custom_provider_allows_mixed_model_catalog(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv(
        "LLM_AVAILABLE_MODELS",
        json.dumps(["deepseek-v4-pro", "gemma-4", "qwen3.5"]),
    )

    assert check_provider_model_alignment() == []


def test_named_provider_still_warns_for_cross_provider_models(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")
    monkeypatch.setenv(
        "LLM_AVAILABLE_MODELS",
        json.dumps(["gpt-4o-mini", "deepseek-v4-pro"]),
    )

    issues = check_provider_model_alignment()

    assert any("deepseek-v4-pro" in issue for issue in issues)
