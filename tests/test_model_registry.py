"""Tests for the model capability registry / task-aware selection."""
from artpm_agent.providers.model_registry import ModelCapabilityRegistry, TASK_TYPES


def _cfg(primary, available=None, vision=None, provider="openai"):
    c = {"model": primary, "provider": provider}
    if available:
        c["available_models"] = available
    if vision:
        c["vision_model"] = vision
    return c


def test_known_task_types():
    assert set(TASK_TYPES) == {"routing", "chat", "reasoning", "vision"}


def test_select_routing_prefers_cheap_same_family():
    reg = ModelCapabilityRegistry(_cfg("gpt-4o", ["gpt-4o-mini", "gpt-4o"]))
    chosen = reg.select_model("routing", "gpt-4o", ["gpt-4o-mini", "gpt-4o"])
    assert chosen == "gpt-4o-mini"


def test_select_reasoning_prefers_strong_regardless_of_family():
    reg = ModelCapabilityRegistry(
        _cfg("gpt-4o-mini", ["gpt-4o-mini", "gpt-4o", "o3"])
    )
    chosen = reg.select_model("reasoning", "gpt-4o-mini", ["gpt-4o-mini", "gpt-4o", "o3"])
    assert chosen == "o3"


def test_select_vision_requires_vision_capability():
    reg = ModelCapabilityRegistry(
        _cfg("mini-model", ["mini-model", "vision-pro"])
    )
    chosen = reg.select_model("vision", "mini-model", None, require_vision=True)
    assert chosen == "vision-pro"


def test_select_falls_back_to_primary_when_no_candidates():
    reg = ModelCapabilityRegistry(_cfg("llama-70b", []))
    assert reg.select_model("routing", "llama-70b", []) == "llama-70b"


def test_capability_tier_detection():
    reg = ModelCapabilityRegistry(_cfg("llama-70b", ["llama-8b", "o3"]))
    caps = {m.model_id: m for m in reg.all()}
    assert caps["llama-8b"].tier == "cheap"
    assert caps["o3"].tier == "strong"
    assert caps["llama-70b"].tier == "general"
    assert caps["llama-70b"].vision is False
