from __future__ import annotations

from typing import Any, Mapping

from artpm_agent.harness import BaseHarnessRuntime, RuntimeCapabilities, TurnContext
from artpm_agent.harness.turn_service import _prepare_turn_attachments
from artpm_agent.retrieval import RetrievalHit, RetrievalPlan, SearchTarget, WorkspaceRetriever


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


def test_retriever_applies_confidence_floor_to_legacy_store_without_new_keywords() -> None:
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
            return [
                {
                    "id": "low",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "confidence": 0.2,
                    "score": 0.9,
                },
                {
                    "id": "high",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "confidence": 0.8,
                    "score": 0.7,
                },
            ]

    plan = RetrievalPlan(
        query="budget",
        target=SearchTarget(tenant_id="tenant-a", workspace_id="workspace-a"),
        confidence_floor=0.5,
    )
    hits = WorkspaceRetriever(LegacyStore()).search(plan)

    assert [hit.id for hit in hits] == ["high"]


def test_retriever_drops_non_finite_scores_and_bounds_provider_text() -> None:
    class DirtyStore:
        def search(self, _query: str, **_kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": "nan-score",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "score": "NaN",
                    "text": "discarded",
                },
                {
                    "id": "infinite-score",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "score": float("inf"),
                    "text": "discarded",
                },
                {
                    "id": "bounded",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "score": "0.75",
                    "text": "0123456789",
                },
            ]

    plan = RetrievalPlan(
        query="budget",
        target=SearchTarget(tenant_id="tenant-a", workspace_id="workspace-a"),
        max_text_chars=100,
    )
    hits = WorkspaceRetriever(DirtyStore()).search(plan)

    assert [hit.id for hit in hits] == ["bounded"]
    assert hits[0].score == 0.75
    assert hits[0].text == "0123456789"


def test_retriever_caps_text_from_store_that_ignores_max_text_chars() -> None:
    class UnboundedStore:
        def search(self, _query: str, **_kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": "long",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "score": 1,
                    "text": "x" * 500,
                }
            ]

    plan = RetrievalPlan(
        query="budget",
        target=SearchTarget(tenant_id="tenant-a", workspace_id="workspace-a"),
        max_text_chars=100,
    )
    hits = WorkspaceRetriever(UnboundedStore()).search(plan)

    assert len(hits) == 1
    assert len(hits[0].text) == 100


def test_retriever_enforces_limit_and_deduplicates_provider_rows() -> None:
    class DuplicateStore:
        def search(self, _query: str, **_kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "id": "same",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "score": 0.9,
                    "text": "first",
                },
                {
                    "id": "same",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "score": 0.8,
                    "text": "duplicate",
                },
                {
                    "id": "second",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "score": 0.7,
                    "text": "second",
                },
                {
                    "id": "third",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "score": 0.6,
                    "text": "third",
                },
            ]

    plan = RetrievalPlan(
        query="budget",
        target=SearchTarget(tenant_id="tenant-a", workspace_id="workspace-a"),
        limit=2,
    )
    hits = WorkspaceRetriever(DuplicateStore()).search(plan)

    assert [hit.id for hit in hits] == ["same", "second"]
    assert [hit.text for hit in hits] == ["first", "second"]


def test_retrieval_hit_rejects_non_finite_scores() -> None:
    for score in (True, float("nan"), float("inf"), "invalid"):
        try:
            RetrievalHit(
                id="hit",
                tenant_id="tenant-a",
                workspace_id="workspace-a",
                title="title",
                text="text",
                score=score,  # type: ignore[arg-type]
                match_type="literal",
            )
        except (TypeError, ValueError):
            continue
        raise AssertionError(f"invalid score should be rejected: {score!r}")
