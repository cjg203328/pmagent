"""Workspace retriever backed by the existing knowledge store."""

from __future__ import annotations

import inspect
from typing import Any

from .contracts import RetrievalHit, RetrievalPlan


class WorkspaceRetriever:
    """Adapt ``WorkspaceKnowledgeStore.search`` to stable retrieval results.

    The store remains the authority for knowledge facts. This adapter owns
    only query planning, result normalization and citation metadata, so it can
    later compose sparse/dense/rerank providers without changing API clients.
    """

    def __init__(self, store: Any):
        if store is None or not callable(getattr(store, "search", None)):
            raise TypeError("store must provide a search() method")
        self.store = store

    def search(self, plan: RetrievalPlan) -> list[RetrievalHit]:
        search = self.store.search
        kwargs: dict[str, Any] = {
            "tenant_id": plan.target.tenant_id,
            "workspace_id": plan.target.workspace_id,
            "limit": plan.limit,
            "resource_types": plan.target.resource_types or None,
            "source_types": plan.target.source_types or None,
            "include_rules": plan.target.include_rules,
            "max_text_chars": plan.max_text_chars,
            "confidence_floor": plan.confidence_floor,
        }
        # ``WorkspaceKnowledgeStore`` keeps confidence weighting opt-in for
        # backwards compatibility. A non-zero plan floor is an explicit
        # request for that behavior. Keep the new keyword out of older
        # injected stores when the caller did not ask for confidence gating.
        if plan.confidence_floor > 0.0:
            kwargs["use_confidence"] = True
        else:
            try:
                parameters = inspect.signature(search).parameters
            except (TypeError, ValueError):
                parameters = {}
            if parameters and "confidence_floor" not in parameters and not any(
                parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters.values()
            ):
                kwargs.pop("confidence_floor", None)
        try:
            rows = search(plan.query, **kwargs)
        except TypeError as error:
            # A legacy store may advertise a confidence floor but not the
            # opt-in switch. Retry without both new keywords; the adapter
            # applies the floor below so the behavior remains correct.
            if plan.confidence_floor <= 0.0 or "use_confidence" not in str(error):
                raise
            kwargs.pop("use_confidence", None)
            kwargs.pop("confidence_floor", None)
            rows = search(plan.query, **kwargs)
        hits: list[RetrievalHit] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            if plan.confidence_floor > 0.0:
                try:
                    confidence = float(row.get("confidence", 1.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                if confidence < plan.confidence_floor:
                    continue
            row_workspace = str(row.get("workspace_id") or "").strip()
            row_tenant = str(row.get("tenant_id") or "").strip()
            # Defense in depth: a broken/custom store must not cross the
            # request scope while its results are being normalized.
            if row_workspace != plan.target.workspace_id or row_tenant != plan.target.tenant_id:
                continue
            hit_id = str(row.get("id") or "").strip()
            if not hit_id:
                continue
            source = row.get("source")
            if not isinstance(source, dict):
                source = {}
            version = row.get("version")
            if not isinstance(version, dict):
                version = {}
            version_source = version.get("source")
            if not isinstance(version_source, dict):
                version_source = {}
            source_uri = str(
                source.get("uri")
                or version_source.get("uri")
                or row.get("source_uri")
                or ""
            )
            text = str(
                row.get("text")
                or row.get("searchable_text")
                or version.get("searchable_text")
                or row.get("statement")
                or ""
            )
            hit = RetrievalHit(
                id=hit_id,
                workspace_id=row_workspace,
                tenant_id=row_tenant,
                title=str(row.get("title") or ""),
                text=text,
                score=float(row.get("score") or 0.0),
                match_type=str(row.get("retrieval_mode") or "unknown"),
                source_uri=source_uri,
                resource_type=str(row.get("resource_type") or row.get("record_type") or ""),
                metadata={
                    key: value
                    for key, value in row.items()
                    if key
                    not in {
                        "id",
                        "workspace_id",
                        "tenant_id",
                        "title",
                        "text",
                        "searchable_text",
                        "score",
                        "retrieval_mode",
                        "source_uri",
                        "resource_type",
                        "record_type",
                    }
                },
            )
            hits.append(hit)
        return hits
