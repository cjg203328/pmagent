from __future__ import annotations

from typing import Any, Mapping

from artpm_agent.harness import BaseHarnessRuntime, RuntimeCapabilities, TurnContext
from artpm_agent.harness.turn_service import _prepare_turn_attachments
from artpm_agent.retrieval import RetrievalPlan, SearchTarget, WorkspaceRetriever


class _EmptyAttachmentRuntime(BaseHarnessRuntime):
    capabilities = RuntimeCapabilities(attachment_parsing=True)

    def __init__(self) -> None:
        self.calls = 0

    def parse_attachments(
        self,
        _user_input: str,
        _context: Mapping[str, Any],
    ) -> tuple[list[Mapping[str, Any]], str]:
        self.calls += 1
        return [], ""


def test_empty_attachment_snapshot_is_parsed_once_per_turn() -> None:
    runtime = _EmptyAttachmentRuntime()
    context = TurnContext(
        turn_id="attachment-once",
        conversation_id="conversation-1",
        user_input="inspect",
        runtime=runtime,
        extra={"file_paths": ["notes.txt"]},
    )

    _prepare_turn_attachments(context)
    _prepare_turn_attachments(context)

    assert runtime.calls == 1
    assert context.attachments_prepared is True
    assert context.extra["parsed_files"] == []
    assert context.extra["attachment_context"] == ""


def test_retrieval_plan_confidence_floor_enables_store_filtering() -> None:
    calls: dict[str, Any] = {}

    class Store:
        def search(self, _query: str, **kwargs: Any) -> list[dict[str, Any]]:
            calls.update(kwargs)
            return []

    plan = RetrievalPlan(
        query="budget",
        target=SearchTarget(tenant_id="tenant-a", workspace_id="workspace-a"),
        confidence_floor=0.4,
    )

    WorkspaceRetriever(Store()).search(plan)

    assert calls["confidence_floor"] == 0.4
    assert calls["use_confidence"] is True


def test_retriever_keeps_legacy_store_compatible_without_confidence_floor() -> None:
    class LegacyStore:
        def search(
            self,
            _query: str,
            *,
            tenant_id: str,
            workspace_id: str,
            limit: int,
            resource_types: Any = None,
            source_types: Any = None,
            include_rules: bool = True,
            max_text_chars: int = 4000,
        ) -> list[dict[str, Any]]:
            del tenant_id, workspace_id, limit, resource_types, source_types
            del include_rules, max_text_chars
            return []

    plan = RetrievalPlan(
        query="budget",
        target=SearchTarget(tenant_id="tenant-a", workspace_id="workspace-a"),
    )
    assert WorkspaceRetriever(LegacyStore()).search(plan) == []
