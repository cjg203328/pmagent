from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

from artpm_agent.runtime.counters import counter_snapshot, reset_counters


def test_storage_registry_constructs_each_authoritative_store_once(tmp_path):
    from artpm_agent.runtime.storage_registry import StorageRegistry

    reset_counters()
    registry = StorageRegistry(
        db_path=tmp_path / "runtime.sqlite",
        vector_store_path=tmp_path / "vectors",
        enable_vector_search=False,
    )

    assert registry.conversation is registry.conversation
    assert registry.session is registry.session
    assert registry.permission is registry.permission
    assert registry.workflow is registry.workflow
    assert registry.knowledge is registry.knowledge
    assert registry.wiki is registry.wiki

    counts = counter_snapshot()
    assert counts["runtime.storage_registry.constructed"] == 1
    for name in ("conversation", "session", "permission", "workflow", "knowledge", "wiki"):
        assert counts[f"runtime.store.{name}.constructed"] == 1


def test_runtime_factory_constructs_one_agent_across_threads(tmp_path):
    from artpm_agent.runtime.factory import RuntimeFactory
    from artpm_agent.runtime.storage_registry import StorageRegistry

    reset_counters()
    created = []

    def build_agent():
        agent = SimpleNamespace(marker=object())
        created.append(agent)
        return agent

    factory = RuntimeFactory(
        storage=StorageRegistry(
            db_path=tmp_path / "runtime.sqlite",
            vector_store_path=tmp_path / "vectors",
            enable_vector_search=False,
        ),
        agent_factory=build_agent,
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        agents = list(executor.map(lambda _index: factory.agent(), range(32)))

    assert len(created) == 1
    assert all(agent is created[0] for agent in agents)
    assert counter_snapshot()["runtime.agent.constructed"] == 1


def test_runtime_factory_uses_workspace_scoped_locks(tmp_path):
    from artpm_agent.runtime.factory import RuntimeFactory
    from artpm_agent.runtime.storage_registry import StorageRegistry

    factory = RuntimeFactory(
        storage=StorageRegistry(
            db_path=tmp_path / "runtime.sqlite",
            vector_store_path=tmp_path / "vectors",
            enable_vector_search=False,
        ),
        agent_factory=lambda: object(),
    )

    first = factory.workspace_lock("tenant-a", "workspace-a")
    same = factory.workspace_lock("tenant-a", "workspace-a")
    other_workspace = factory.workspace_lock("tenant-a", "workspace-b")
    other_tenant = factory.workspace_lock("tenant-b", "workspace-a")

    assert first is same
    assert first is not other_workspace
    assert first is not other_tenant


def test_reset_runtime_factory_rebuilds_storage_from_current_config(monkeypatch, tmp_path):
    from artpm_agent import config as config_module
    from artpm_agent.runtime.factory import get_runtime_factory, reset_runtime_factory

    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    monkeypatch.setenv("DATA_ROOT", str(first_root))
    monkeypatch.setenv("CONVERSATION_DB_PATH", str(first_root / "conversations.db"))
    config_module.reset_config()
    reset_runtime_factory()
    first = get_runtime_factory()

    monkeypatch.setenv("DATA_ROOT", str(second_root))
    monkeypatch.setenv("CONVERSATION_DB_PATH", str(second_root / "conversations.db"))
    config_module.reset_config()
    reset_runtime_factory()
    second = get_runtime_factory()

    assert first is not second
    assert first.storage.db_path == (first_root / "conversations.db").resolve()
    assert second.storage.db_path == (second_root / "conversations.db").resolve()


def test_runtime_factory_reuses_workspace_coordinator_and_engine(tmp_path):
    from artpm_agent.runtime.factory import RuntimeFactory
    from artpm_agent.runtime.storage_registry import StorageRegistry
    from artpm_agent.tenancy import TenantContext

    class Router:
        def for_tenant(self, _context):
            return self

        def list_skills(self):
            return []

        def execute_skill(self, _name, _inputs):
            return {"success": True}

    agent = SimpleNamespace(router=Router(), memory=None, tencentdb_memory=None)
    factory = RuntimeFactory(
        storage=StorageRegistry(
            db_path=tmp_path / "runtime.sqlite",
            vector_store_path=tmp_path / "vectors",
            enable_vector_search=False,
        ),
        agent_factory=lambda: agent,
    )
    context = TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="user-a",
    )

    first = factory.workflow_coordinator(context)
    second = factory.workflow_coordinator(context)

    assert first is second
    assert first.engine is second.engine


def test_gateway_chats_in_different_workspaces_are_not_globally_serialized(tmp_path):
    from artpm_agent.api.services import ChatCommand, DefaultGatewayRuntime, RequestPrincipal
    from artpm_agent.runtime.factory import RuntimeFactory
    from artpm_agent.runtime.storage_registry import StorageRegistry

    factory = RuntimeFactory(
        storage=StorageRegistry(
            db_path=tmp_path / "runtime.sqlite",
            vector_store_path=tmp_path / "vectors",
            enable_vector_search=False,
        ),
        agent_factory=lambda: object(),
    )
    runtime = object.__new__(DefaultGatewayRuntime)
    runtime.runtime_factory = factory
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    def scoped(command):
        if command.principal.workspace_id == "workspace-a":
            first_entered.set()
            assert release_first.wait(2)
        else:
            second_entered.set()
        return command.principal.workspace_id

    runtime._chat_scoped = scoped

    def command(workspace_id):
        principal = RequestPrincipal(
            tenant_id="tenant-a",
            workspace_id=workspace_id,
            actor_id="user-a",
        )
        return ChatCommand(
            principal=principal,
            conversation_id=f"conversation-{workspace_id}",
            turn_id=f"turn-{workspace_id}",
            message="hello",
            tenant_context=principal.tenant_context(),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(runtime.chat, command("workspace-a"))
        assert first_entered.wait(2)
        second = executor.submit(runtime.chat, command("workspace-b"))
        assert second_entered.wait(2)
        assert second.result(timeout=2) == "workspace-b"
        release_first.set()
        assert first.result(timeout=2) == "workspace-a"


def test_default_gateway_uses_registry_owned_stores(tmp_path):
    from artpm_agent.api.services import DefaultGatewayRuntime

    runtime = DefaultGatewayRuntime(tmp_path / "gateway.sqlite")
    storage = runtime.runtime_factory.storage

    assert runtime.conversations is storage.conversation
    assert runtime.session_store is storage.session
    assert runtime.permissions is storage.permission
    assert runtime.workflows is storage.workflow
