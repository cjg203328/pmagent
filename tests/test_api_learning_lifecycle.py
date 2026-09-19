"""Regression tests for the API compatibility lifecycle boundary."""

from __future__ import annotations

from types import SimpleNamespace

from artpm_agent.api.services import DefaultGatewayRuntime


def test_legacy_api_learning_reuses_canonical_turn_lifecycle(monkeypatch):
    runtime = object.__new__(DefaultGatewayRuntime)
    context = SimpleNamespace(scope=SimpleNamespace(tenant_id="tenant-a"))
    result = SimpleNamespace(success=True)
    services = SimpleNamespace()
    calls = []

    def complete_turn_lifecycle(received_context, received_result, **kwargs):
        calls.append((received_context, received_result, kwargs))
        return {"finalized": True}

    monkeypatch.setattr(
        "artpm_agent.harness.turn_service.complete_turn_lifecycle",
        complete_turn_lifecycle,
    )

    runtime._record_turn_learning(context, result, services)

    assert calls == [
        (
            context,
            result,
            {"services": services, "auto_reflect": True},
        )
    ]


def test_legacy_api_learning_absorbs_lifecycle_failures(monkeypatch):
    runtime = object.__new__(DefaultGatewayRuntime)

    def fail(*_args, **_kwargs):
        raise RuntimeError("learning unavailable")

    monkeypatch.setattr(
        "artpm_agent.harness.turn_service.complete_turn_lifecycle",
        fail,
    )

    runtime._record_turn_learning(
        SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
    )
