from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import pytest

from sqlalchemy import create_engine, inspect

from artpm_agent.components import ComponentFactory, ComponentRegistry
from artpm_agent.database.models import Base
from artpm_agent.database.models import DatabaseManager, Project
from artpm_agent.tenancy import TenantContext
from artpm_agent.memory import vector_store as vector_module
from artpm_agent.skills.base_skill import BaseSkill
from artpm_agent.tenancy import TenantContextManager
from artpm_agent.runtime.request_services import RequestServiceBundle


class SyncSkill(BaseSkill):
    skill_name = "sync-test"

    def __init__(self):
        super().__init__({})
        self.thread_id = None

    def execute(self, inputs):
        self.thread_id = threading.get_ident()
        return {"success": True, "value": inputs["value"]}


def test_sync_skill_execute_async_uses_worker_thread():
    skill = SyncSkill()
    caller_thread = threading.get_ident()
    result = asyncio.run(skill.execute_async({"value": 7}))
    assert result["value"] == 7
    assert skill.thread_id != caller_thread


def test_tenant_context_propagates_into_threaded_async_work():
    context = TenantContext(tenant_id="tenant-a", workspace_id="workspace-a")

    def read_context():
        return TenantContextManager.get_current()

    async def run():
        with TenantContextManager.use(context):
            return await asyncio.to_thread(read_context)

    assert asyncio.run(run()) == context


def test_agent_module_is_a_small_facade():
    import artpm_agent.agent as facade

    path = Path(facade.__file__)
    assert len(path.read_text(encoding="utf-8").splitlines()) < 300
    assert facade.ArtPMAgent.__mro__[1].__name__ == "RequestOrchestrator"


def test_component_registry_is_immutable_mapping():
    registry = ComponentRegistry({"router": object()})
    assert registry.get_required("router") is registry["router"]
    assert registry.as_dict() is not registry._components
    assert len(registry) == 1
    assert list(registry) == ["router"]
    with pytest.raises(LookupError):
        registry.get_required("missing")
    with pytest.raises(TypeError):
        registry._components["router"] = object()


def test_component_factory_builds_registry_from_orchestrator():
    class Orchestrator:
        def __init__(self, config):
            self.config = config
            self.router = "router"

    registry = ComponentFactory.create("config", orchestrator_class=Orchestrator)
    assert registry["config"] == "config"
    assert registry["router"] == "router"


def test_request_service_bundle_exposes_explicit_boundaries():
    intent = type("Intent", (), {"detect": lambda _self, value: value})()
    skills = type(
        "Skills",
        (),
        {"execute_skill": lambda _self, name, inputs: {"name": name, **inputs}},
    )()
    bundle = RequestServiceBundle(
        intent_router=intent,
        skill_router=skills,
        model_gateway=object(),
        parse_attachments=lambda *_args: ([], ""),
        prepare_vision_attachments=lambda *_args: None,
        format_skill_result=lambda name, result: f"{name}:{result['value']}",
    )

    assert bundle.detect_intent("quote") == "quote"
    assert bundle.execute_skill("read", {"value": 1}) == {
        "name": "read",
        "value": 1,
    }
    assert bundle.format_result("read", {"value": 1}) == "read:1"


def test_agent_facade_assembles_and_forwards_sync_and_async(monkeypatch):
    import artpm_agent.agent as facade

    registry = ComponentRegistry({"marker": "assembled"})
    monkeypatch.setattr(
        facade.ComponentFactory,
        "create",
        lambda *_args, **_kwargs: registry,
    )
    monkeypatch.setattr(
        facade.RequestOrchestrator,
        "chat",
        lambda _self, prompt, context=None: f"{prompt}:{bool(context)}",
    )
    agent = facade.ArtPMAgent({"x": 1})
    assert agent.marker == "assembled"
    assert agent.chat("hello", {"x": 1}) == "hello:True"
    assert asyncio.run(agent.execute_async("hello")) == "hello:False"


def test_all_business_models_have_tenant_scope_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scoped.db'}")
    try:
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        for table in Base.metadata.tables:
            columns = {column["name"] for column in inspector.get_columns(table)}
            assert {"tenant_id", "workspace_id"} <= columns
    finally:
        engine.dispose()


def test_database_session_binds_new_rows_to_trusted_tenant(tmp_path):
    manager = DatabaseManager(f"sqlite:///{tmp_path / 'tenant.db'}")
    context = TenantContext(tenant_id="tenant-a", workspace_id="workspace-a")
    session = manager.get_session(context)
    try:
        project = Project(project_name="Scoped", client="Client", quote_amount=1.0)
        session.add(project)
        session.flush()
        assert project.tenant_id == "tenant-a"
        assert project.workspace_id == "workspace-a"
    finally:
        session.rollback()
        session.close()
        manager.close()


def test_database_session_rejects_conflicting_tenant(tmp_path):
    manager = DatabaseManager(f"sqlite:///{tmp_path / 'tenant-conflict.db'}")
    context = TenantContext(tenant_id="tenant-a", workspace_id="workspace-a")
    session = manager.get_session(context)
    try:
        session.add(
            Project(
                project_name="Forged",
                client="Client",
                quote_amount=1.0,
                tenant_id="tenant-b",
            )
        )
        with pytest.raises(ValueError, match="tenant_id conflicts"):
            session.flush()
    finally:
        session.rollback()
        session.close()
        manager.close()


def test_database_queries_are_scoped_to_the_trusted_tenant(tmp_path):
    manager = DatabaseManager(f"sqlite:///{tmp_path / 'tenant-query.db'}")
    tenant_a = TenantContext(tenant_id="tenant-a", workspace_id="workspace-a")
    tenant_b = TenantContext(tenant_id="tenant-b", workspace_id="workspace-b")
    try:
        session_a = manager.get_session(tenant_a)
        try:
            session_a.add(
                Project(
                    project_name="Tenant A project",
                    client="Client A",
                    quote_amount=1.0,
                )
            )
            session_a.commit()
        finally:
            session_a.close()

        session_b = manager.get_session(tenant_b)
        try:
            assert session_b.query(Project).all() == []
        finally:
            session_b.close()

        session_a = manager.get_session(tenant_a)
        try:
            assert [project.project_name for project in session_a.query(Project)] == [
                "Tenant A project"
            ]
        finally:
            session_a.close()
    finally:
        manager.close()


def test_rls_migration_forces_database_isolation():
    migration = (
        Path(__file__).parents[1]
        / "alembic"
        / "versions"
        / "0003_postgresql_rls.py"
    ).read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "current_setting('app.tenant_id', true)" in migration
    assert "WITH CHECK" in migration


def test_qdrant_runtime_failure_switches_to_faiss(monkeypatch, tmp_path):
    class FailingQdrant:
        available = True
        needs_rebuild = False
        last_error = None
        count = 1

        def search(self, *_args, **_kwargs):
            raise ConnectionError("network partition")

    class FakeFaiss:
        available = True
        needs_rebuild = False
        last_error = None
        count = 0

        def __init__(self, **_kwargs):
            pass

        def search(self, *_args, **_kwargs):
            return [{"id": "local", "score": 1.0, "metadata": {}}]

        def status(self):
            return {"available": True, "backend": "faiss", "count": 0}

    monkeypatch.setattr(vector_module, "FaissVectorStore", FakeFaiss)
    store = vector_module.VectorStore.__new__(vector_module.VectorStore)
    store.fallback_reason = None
    store._backend = FailingQdrant()
    store._remote_backend = True
    store._fallback_options = {
        "store_path": tmp_path,
        "dimension": 4,
        "embedding_fingerprint": "test:4",
    }
    assert store.search([1.0, 0.0, 0.0, 0.0])[0]["id"] == "local"
    assert "network partition" in store.fallback_reason


def test_vector_store_selects_qdrant_and_startup_fallback(monkeypatch, tmp_path):
    import artpm_agent.memory.qdrant_vector_store as qdrant_module

    class FakeQdrant:
        available = True
        needs_rebuild = False
        last_error = None
        count = 0

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def status(self):
            return {"available": True, "count": 0}

    monkeypatch.setenv("VECTOR_BACKEND", "qdrant")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setattr(qdrant_module, "QdrantVectorStore", FakeQdrant)
    remote = vector_module.VectorStore(tmp_path, dimension=4)
    assert remote.status()["backend"] == "qdrant"
    assert remote._backend.kwargs["url"] == "http://qdrant:6333"

    class BrokenQdrant:
        def __init__(self, **_kwargs):
            raise ConnectionError("offline")

    monkeypatch.setattr(qdrant_module, "QdrantVectorStore", BrokenQdrant)
    local = vector_module.VectorStore(tmp_path / "fallback", dimension=4)
    assert local.status()["backend"] == "faiss"
    assert "Qdrant unavailable" in local.fallback_reason


def test_observability_is_noop_without_exporters(monkeypatch):
    import artpm_agent.observability as observability

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(observability, "_INITIALIZED", False)
    assert observability.initialize_observability("test") is True
    with observability.traced("test.noop"):
        pass
