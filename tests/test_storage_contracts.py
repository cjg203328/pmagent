from __future__ import annotations

from pathlib import Path

import pytest

from artpm_agent.api.services import DefaultGatewayRuntime
from artpm_agent.memory.conversation_store import ConversationStore
from artpm_agent.runtime.storage_contracts import (
    AUTHORITATIVE_STORE_CONTRACTS,
    probe_sqlite_store,
)
from artpm_agent.runtime.storage_registry import StorageRegistry


def test_authoritative_store_contracts_are_immutable_and_scoped() -> None:
    assert set(AUTHORITATIVE_STORE_CONTRACTS) == {
        "business_store",
        "conversation_store",
        "session_store",
        "knowledge_store",
    }
    assert AUTHORITATIVE_STORE_CONTRACTS["business_store"].trusted_scope == (
        "tenant_id",
        "workspace_id",
    )
    with pytest.raises(TypeError):
        AUTHORITATIVE_STORE_CONTRACTS["other"] = (  # type: ignore[index]
            AUTHORITATIVE_STORE_CONTRACTS["business_store"]
        )


def test_registry_readiness_probes_every_authority_without_agent(tmp_path: Path) -> None:
    registry = StorageRegistry(
        db_path=tmp_path / "conversations.sqlite",
        business_db_path=tmp_path / "business.sqlite",
        vector_store_path=tmp_path / "vectors",
        enable_vector_search=False,
    )

    checks = registry.readiness(force=True)

    assert set(checks) == set(AUTHORITATIVE_STORE_CONTRACTS)
    assert all(check["status"] == "ok" for check in checks.values())
    assert "knowledge" not in registry.snapshot()
    assert registry.snapshot()["business"] is True
    registry.close()
    assert registry.snapshot() == {}


def test_registry_readiness_probes_the_real_knowledge_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import artpm_agent.runtime.storage_registry as registry_module

    registry = StorageRegistry(
        db_path=tmp_path / "conversations.sqlite",
        business_db_path=tmp_path / "business.sqlite",
        vector_store_path=tmp_path / "vectors",
        enable_vector_search=False,
    )
    seen: dict[str, str] = {}
    probe_store = registry_module.probe_store

    def capture_store(store: object, contract):
        seen[contract.name] = type(store).__name__
        return probe_store(store, contract)

    monkeypatch.setattr(registry_module, "probe_store", capture_store)

    checks = registry.readiness(force=True)

    assert checks["knowledge_store"]["status"] == "ok"
    assert seen["knowledge_store"] == "WorkspaceKnowledgeStore"
    assert "knowledge" not in registry.snapshot()
    registry.close()


def test_knowledge_contract_detects_missing_authoritative_schema(tmp_path: Path) -> None:
    conversation = ConversationStore(tmp_path / "conversation-only.sqlite")

    result = probe_sqlite_store(
        conversation,
        AUTHORITATIVE_STORE_CONTRACTS["knowledge_store"],
    )

    assert result.status == "error"
    assert result.message == "store schema is incomplete"
    assert "knowledge_resources" in result.missing_tables


def test_default_gateway_health_requires_all_authoritative_stores(tmp_path: Path) -> None:
    runtime = DefaultGatewayRuntime(tmp_path / "gateway.sqlite")

    health = runtime.health()

    assert runtime._agent is None
    assert set(AUTHORITATIVE_STORE_CONTRACTS) <= set(health["checks"])
    assert all(
        health["checks"][name]["status"] == "ok"
        for name in AUTHORITATIVE_STORE_CONTRACTS
    )
    assert health["status"] == "ok"
    runtime.close()
