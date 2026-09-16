"""P2 contracts for the staged large-module split."""

from __future__ import annotations

from datetime import datetime
import importlib


def test_ui_pure_modules_are_importable_without_page_bootstrap():
    formatters = importlib.import_module("artpm_agent.ui.formatters")
    knowledge = importlib.import_module("artpm_agent.ui.knowledge")
    approvals = importlib.import_module("artpm_agent.ui.approvals")

    assert formatters.format_cn_date(datetime(2026, 9, 14)) == "9月14日"
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
    from artpm_agent.views.chat_state import compact_legacy_assistant_copy, feedback_was_saved
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
