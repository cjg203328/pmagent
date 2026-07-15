"""Tests for the LLM task classifier + layered model selection."""
import pytest

from artpm_agent.providers.gateway import ModelGateway
from artpm_agent.providers.response_cache import ResponseCache
from artpm_agent.routing.task_classifier import LLMTaskClassifier


class FakeClient:
    def __init__(self, model, label=None):
        self.model = model
        self.label = label  # if set, .chat returns this (classifier style)
        self.calls = 0

    def chat(self, prompt, system_prompt=None, history=None):
        self.calls += 1
        return self.label if self.label is not None else f"ok:{self.model}"

    def chat_with_images(self, prompt, image_paths, system_prompt=None, history=None):
        self.calls += 1
        return self.label if self.label is not None else f"ok:{self.model}"

    def stream_chat(self, prompt, system_prompt=None, history=None):
        yield f"ok:{self.model}"


class FakeTelemetry:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)


class FakeGateway:
    """Minimal gateway stub for the classifier (no real client building)."""

    def __init__(self, routing_model, client):
        self._routing = routing_model
        self._client = client

    def best_model_for_task(self, task_type, *, require_vision=False):
        return self._routing

    def client_for_model(self, model_id):
        return self._client

    def primary_model_id(self):
        return "primary"


def _gw(primary, available, **kw):
    cfg = {"model": primary, "provider": "openai", "available_models": available}
    return ModelGateway(
        cfg,
        FakeClient(primary),
        client_factory=lambda c: FakeClient(c["model"]),
        **kw,
    )


# ── Classifier unit behaviour ──


def test_classifier_disabled_returns_chat():
    tc = LLMTaskClassifier(FakeGateway("mini", None), enabled=False)
    assert tc.classify("帮我推理方案", image_paths=["x.png"]) == "chat"


def test_classifier_heuristic_vision():
    tc = LLMTaskClassifier(FakeGateway("mini", None), enabled=True)
    assert tc.classify("看图", image_paths=["a.png"]) == "vision"


def test_classifier_heuristic_reasoning():
    tc = LLMTaskClassifier(FakeGateway("mini", None), enabled=True)
    assert tc.classify("帮我分析一下原因") == "reasoning"


def test_classifier_heuristic_chat():
    tc = LLMTaskClassifier(FakeGateway("mini", None), enabled=True)
    assert tc.classify("你好") == "chat"


def test_classifier_llm_returns_label():
    client = FakeClient("mini", label="vision")
    tc = LLMTaskClassifier(FakeGateway("mini", client), enabled=True)
    assert tc.classify("some input") == "vision"


def test_classifier_llm_routing_normalised_to_chat():
    client = FakeClient("mini", label="routing")
    tc = LLMTaskClassifier(FakeGateway("mini", client), enabled=True)
    assert tc.classify("some input") == "chat"


def test_classifier_llm_bad_label_falls_back_to_heuristic():
    client = FakeClient("mini", label="not-a-label")
    tc = LLMTaskClassifier(FakeGateway("mini", client), enabled=True)
    assert tc.classify("帮我分析一下原因") == "reasoning"


def test_classifier_caches_results():
    client = FakeClient("mini", label="chat")
    tc = LLMTaskClassifier(FakeGateway("mini", client), enabled=True)
    tc.classify("hello")
    tc.classify("hello")
    assert client.calls == 1


# ── Gateway wiring: opt-in layered model selection ──


def test_gateway_no_classifier_defaults_to_chat(monkeypatch):
    monkeypatch.delenv("ARTPM_TASK_CLASSIFIER", raising=False)
    gw = _gw("gpt-4o", ["gpt-4o-mini"])
    assert gw.classify_task("anything") == "chat"
    out = gw.chat_with_failover("hi", "sys", [], task_type=None)
    assert "gpt-4o" in out  # primary (chat tier) used, no behaviour change


def test_gateway_classifies_and_selects_reasoning_model(monkeypatch):
    monkeypatch.setenv("ARTPM_TASK_CLASSIFIER", "true")

    class ClassifyingClient(FakeClient):
        def chat(self, prompt, system_prompt=None, history=None):
            # The classifier prompt asks for a task label; the real answer
            # prompt simply returns an "ok:<model>" string.
            return "reasoning"

    cfg = {
        "model": "gpt-4o",
        "provider": "openai",
        "available_models": ["gpt-4o-mini", "o3"],
    }
    tel = FakeTelemetry()
    gw = ModelGateway(
        cfg,
        ClassifyingClient("gpt-4o"),
        client_factory=lambda c: ClassifyingClient(c["model"]),
        telemetry=tel,
    )
    out = gw.chat_with_failover("请帮我推理一下方案", "sys", [], task_type=None)
    # routing classifier → "reasoning" → strongest model (o3) selected
    assert "o3" in out
    assert tel.records[0]["task_type"] == "reasoning"


def test_gateway_classifies_vision_requires_vision_model(monkeypatch):
    monkeypatch.setenv("ARTPM_TASK_CLASSIFIER", "true")

    class VisionClient(FakeClient):
        def chat(self, prompt, system_prompt=None, history=None):
            return "vision"

    cfg = {
        "model": "gpt-4o",
        "provider": "openai",
        "available_models": ["gpt-4o-mini", "gpt-4o-vision"],
    }
    tel = FakeTelemetry()
    gw = ModelGateway(
        cfg,
        VisionClient("gpt-4o"),
        client_factory=lambda c: VisionClient(c["model"]),
        telemetry=tel,
    )
    out = gw.chat_with_failover(
        "看这张图", "sys", [], image_paths=["x.png"], task_type=None
    )
    # vision task → vision-capable model selected
    assert "gpt-4o-vision" in out
    assert tel.records[0]["task_type"] == "vision"


def test_gateway_stream_classifies_task(monkeypatch):
    monkeypatch.setenv("ARTPM_TASK_CLASSIFIER", "true")

    class C(FakeClient):
        def chat(self, prompt, system_prompt=None, history=None):
            return "reasoning"

        def stream_chat(self, prompt, system_prompt=None, history=None):
            yield "ok:" + self.model

    cfg = {
        "model": "gpt-4o",
        "provider": "openai",
        "available_models": ["gpt-4o-mini", "o3"],
    }
    tel = FakeTelemetry()
    gw = ModelGateway(
        cfg, C("gpt-4o"), client_factory=lambda c: C(c["model"]), telemetry=tel
    )
    out = "".join(
        gw.stream_with_failover("推理一下", "sys", [], task_type=None)
    )
    assert "o3" in out
    assert tel.records[0]["task_type"] == "reasoning"
