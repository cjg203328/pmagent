import json

from artpm_agent.tools.check_config import (
    check_provider_model_alignment,
    check_vision_pairing,
)


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


# ── vision pairing check ──


def test_vision_pairing_disabled_by_default(monkeypatch):
    monkeypatch.delenv("LLM_VISION_MODEL", raising=False)
    monkeypatch.delenv("LLM_VISION_PROVIDER", raising=False)
    assert check_vision_pairing() == []


def test_vision_pairing_missing_provider_warns(monkeypatch):
    monkeypatch.setenv("LLM_VISION_MODEL", "glm-4v-flash")
    monkeypatch.delenv("LLM_VISION_PROVIDER", raising=False)
    issues = check_vision_pairing()
    assert any("LLM_VISION_PROVIDER" in issue for issue in issues)


def test_vision_pairing_missing_key_warns(monkeypatch):
    monkeypatch.setenv("LLM_VISION_MODEL", "glm-4v-flash")
    monkeypatch.setenv("LLM_VISION_PROVIDER", "zhipu")
    monkeypatch.delenv("LLM_VISION_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    issues = check_vision_pairing()
    assert any("缺少 API key" in issue for issue in issues)


def test_vision_pairing_uses_provider_key(monkeypatch):
    monkeypatch.setenv("LLM_VISION_MODEL", "glm-4v-flash")
    monkeypatch.setenv("LLM_VISION_PROVIDER", "zhipu")
    monkeypatch.setenv("ZHIPU_API_KEY", "real-zhipu-key")
    monkeypatch.delenv("LLM_VISION_API_KEY", raising=False)
    assert check_vision_pairing() == []


def test_vision_pairing_vision_key_overrides_provider_key(monkeypatch):
    monkeypatch.setenv("LLM_VISION_MODEL", "glm-4v-flash")
    monkeypatch.setenv("LLM_VISION_PROVIDER", "zhipu")
    monkeypatch.setenv("ZHIPU_API_KEY", "real-zhipu-key")
    monkeypatch.setenv("LLM_VISION_API_KEY", "sk-your-key-here")  # placeholder wins
    issues = check_vision_pairing()
    assert any("占位符" in issue for issue in issues)
