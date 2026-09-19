"""P2 contracts for the staged large-module split."""

from __future__ import annotations

import ast
import importlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTTP_METHODS = frozenset({"delete", "get", "patch", "post", "put"})


def _tree(relative_path: str) -> ast.Module:
    source = (ROOT / relative_path).read_text(encoding="utf-8")
    return ast.parse(source)


def _top_level_definitions(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef))
    }


def _route_paths(relative_path: str, receiver: str) -> set[str]:
    paths: set[str] = set()
    for node in ast.walk(_tree(relative_path)):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            function = decorator.func
            if not (
                isinstance(function, ast.Attribute)
                and function.attr in HTTP_METHODS
                and isinstance(function.value, ast.Name)
                and function.value.id == receiver
                and decorator.args
            ):
                continue
            path = decorator.args[0]
            if isinstance(path, ast.Constant) and isinstance(path.value, str):
                paths.add(path.value)
    return paths


def _protocol_names(relative_path: str) -> set[str]:
    names: set[str] = set()
    for node in _tree(relative_path).body:
        if not isinstance(node, ast.ClassDef):
            continue
        if any(isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases):
            names.add(node.name)
    return names


def _annotated_fields(relative_path: str, class_name: str) -> dict[str, set[str]]:
    for node in _tree(relative_path).body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        fields: dict[str, set[str]] = {}
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields[item.target.id] = {
                    child.id
                    for child in ast.walk(item.annotation)
                    if isinstance(child, ast.Name)
                }
        return fields
    raise AssertionError(f"class not found: {class_name}")


def test_ui_pure_modules_are_importable_without_page_bootstrap():
    formatters = importlib.import_module("artpm_agent.ui.formatters")
    knowledge = importlib.import_module("artpm_agent.ui.knowledge")
    approvals = importlib.import_module("artpm_agent.ui.approvals")

    assert formatters.format_cn_date(datetime(2026, 9, 14, tzinfo=timezone.utc)) == "9月14日"
    assert knowledge.is_knowledge_ingestion_request("把这份文件加入知识库")
    assert knowledge.extract_knowledge_rule("请记住：报价默认需要审批") == "报价默认需要审批"
    assert approvals.permission_parameter_summary({"inputs": {"project_id": "p-1"}}) == "项目=p-1"


def test_knowledge_authority_contract_is_split_from_transaction_facade():
    from artpm_agent.memory.knowledge import (
        SCHEMA_VERSION,
        chunk_text,
        content_hash,
        schema_contract,
        serialize_json,
        validate_limit,
    )

    assert SCHEMA_VERSION == 7
    assert schema_contract()["scope_columns"] == ("tenant_id", "workspace_id")
    assert "knowledge_index_outbox" in schema_contract()["tables"]
    assert schema_contract()["outbox_states"] == (
        "pending",
        "processing",
        "done",
        "dead",
    )
    assert validate_limit(1) == 1
    assert content_hash("a", "null", None) == content_hash("a", "null", None)
    assert serialize_json({"b": 1, "a": 2}, "metadata") == '{"a":2,"b":1}'
    assert chunk_text("abcdef", chunk_chars=4, overlap=1) == ["abcd", "def"]


def test_chat_pure_boundaries_preserve_legacy_projection_contracts():
    from artpm_agent.views.chat_state import (
        compact_legacy_assistant_copy,
        feedback_was_saved,
    )
    from artpm_agent.views.chat_welcome import WELCOME_SUGGESTIONS, welcome_suggestions

    assert compact_legacy_assistant_copy(
        "默认模型 `a` 暂时不可用，本次临时使用 `b` 生成回答；默认设置未修改。\n\n正文"
    ) == "已切换备用模型：`b`。\n\n正文"
    assert feedback_was_saved({"feedback_id": 1})
    assert not feedback_was_saved({"feedback_id": None})
    assert len(WELCOME_SUGGESTIONS) == 7
    assert welcome_suggestions() is not WELCOME_SUGGESTIONS


def test_knowledge_facade_is_physically_split_by_responsibility():
    from artpm_agent.memory.knowledge_migrations import KnowledgeMigrationService
    from artpm_agent.memory.knowledge_repository import KnowledgeRepository
    from artpm_agent.memory.knowledge_rule_service import KnowledgeRuleService
    from artpm_agent.memory.knowledge_search_service import KnowledgeSearchService
    from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore

    assert issubclass(WorkspaceKnowledgeStore, KnowledgeMigrationService)
    assert issubclass(WorkspaceKnowledgeStore, KnowledgeRepository)
    assert issubclass(WorkspaceKnowledgeStore, KnowledgeRuleService)
    assert issubclass(WorkspaceKnowledgeStore, KnowledgeSearchService)


def test_agent_provider_and_legacy_boundaries_are_importable():
    from artpm_agent.providers.port import ProviderPort
    from artpm_agent.runtime.agent_factory import AgentFactory
    from artpm_agent.runtime.legacy_facade import record_legacy_facade_call

    assert AgentFactory(lambda: "agent").create() == "agent"
    assert ProviderPort is not None
    assert callable(record_legacy_facade_call)


def test_workflow_store_schema_and_codec_are_physically_split():
    store_tree = _tree("artpm_agent/workflows/store.py")
    schema_tree = _tree("artpm_agent/workflows/store_schema.py")
    codec_tree = _tree("artpm_agent/workflows/store_codec.py")

    store_imports = {
        node.module
        for node in store_tree.body
        if isinstance(node, ast.ImportFrom) and node.level == 1
    }
    assert {"store_schema", "store_codec"} <= store_imports

    schema_definitions = _top_level_definitions(schema_tree)
    codec_definitions = _top_level_definitions(codec_tree)
    assert {
        "migrate_retry_columns",
        "migrate_tenant_columns",
        "migrate_workflow_schema",
    } <= schema_definitions
    assert {
        "approval_from_row",
        "definition_checksum",
        "definition_json",
        "event_from_row",
        "resolved_definition_from_row",
        "run_from_row",
        "step_from_row",
    } <= codec_definitions

    moved_definitions = schema_definitions | codec_definitions
    assert moved_definitions.isdisjoint(_top_level_definitions(store_tree))
    assert any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "SCHEMA_VERSION"
            for target in node.targets
        )
        for node in schema_tree.body
    )


def test_permission_and_workflow_routes_are_owned_by_router_modules():
    app_routes = _route_paths("artpm_agent/api/app.py", "app")
    permission_routes = _route_paths(
        "artpm_agent/api/routers/permissions.py",
        "router",
    )
    workflow_routes = _route_paths(
        "artpm_agent/api/routers/workflows.py",
        "router",
    )

    assert not any(path.startswith("/v1/permissions") for path in app_routes)
    assert not any(path.startswith("/v1/workflows") for path in app_routes)
    assert not any(path.startswith("/v1/workflow-runs") for path in app_routes)
    assert {
        "/v1/permissions",
        "/v1/permissions/{request_id}",
        "/v1/permissions/{request_id}/approve",
        "/v1/permissions/{request_id}/reject",
    } <= permission_routes
    assert {
        "/v1/workflows",
        "/v1/workflows/{workflow_id}",
        "/v1/workflows/{workflow_id}/runs",
        "/v1/workflow-runs",
        "/v1/workflow-runs/{run_id}",
    } <= workflow_routes

    app_calls = {
        node.func.id
        for node in ast.walk(_tree("artpm_agent/api/app.py"))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"create_permissions_router", "create_workflows_router"} <= app_calls


def test_gateway_and_turn_service_fields_are_protocol_typed():
    gateway_fields = _annotated_fields(
        "artpm_agent/api/services.py",
        "GatewayServices",
    )
    api_protocols = _protocol_names("artpm_agent/api/contracts.py")
    expected_gateway_ports = {
        "conversations": "ConversationStorePort",
        "event_bus": "EventBusPort",
        "permissions": "PermissionStorePort",
        "workflow_engine": "WorkflowEnginePort",
        "workflows": "WorkflowStorePort",
    }
    for field, port in expected_gateway_ports.items():
        assert gateway_fields[field] == {port}
        assert port in api_protocols

    turn_fields = _annotated_fields(
        "artpm_agent/runtime/request_services.py",
        "TurnServiceBundle",
    )
    runtime_protocols = _protocol_names("artpm_agent/runtime/service_ports.py")
    expected_turn_ports = {
        "artifact_coordinator": "ArtifactCoordinatorPort",
        "consolidation_scheduler": "ConsolidationSchedulerPort",
        "episode_store": "EpisodeStorePort",
        "event_bus": "EventBusPort",
        "feedback_store": "FeedbackStorePort",
        "knowledge_store": "KnowledgeStorePort",
        "memory_manager": "MemoryManagerPort",
        "meta_memory_store": "MetaMemoryStorePort",
        "permission_store": "PermissionStorePort",
        "profile_store": "ProfileStorePort",
        "reflection_scheduler": "ReflectionSchedulerPort",
        "session_store": "SessionStorePort",
        "strategy_store": "StrategyStorePort",
        "tencentdb_memory": "TencentMemoryPort",
        "workflow_coordinator": "WorkflowCoordinatorPort",
    }
    for field, port in expected_turn_ports.items():
        assert turn_fields[field] == {port}
        assert port in runtime_protocols
