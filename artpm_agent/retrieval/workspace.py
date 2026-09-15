"""Workspace retriever backed by the existing knowledge store."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from math import isfinite
from typing import Any

from .contracts import RetrievalHit, RetrievalPlan


def _finite_float(value: Any, *, default: float | None = None) -> float | None:
    """Coerce provider metadata without allowing NaN/Infinity into JSON."""

    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if isfinite(number) else default


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
        }
        # Confidence options were added after the first store interface. Only
        # send them when the injected implementation advertises the keyword
        # (or accepts arbitrary keywords); the adapter applies the requested
        # floor below for stores that predate these options.
        try:
            parameters = inspect.signature(search).parameters
        except (TypeError, ValueError):
            parameters = {}
        accepts_var_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        if accepts_var_kwargs or "confidence_floor" in parameters:
            kwargs["confidence_floor"] = plan.confidence_floor
        if plan.confidence_floor > 0.0 and (
            accepts_var_kwargs or "use_confidence" in parameters
        ):
            kwargs["use_confidence"] = True
        try:
            rows = search(plan.query, **kwargs)
        except TypeError as error:
            # Some dynamic callables cannot be inspected reliably. Retry once
            # without optional confidence keywords when Python reports an
            # unexpected keyword; the adapter still enforces the floor below.
            message = str(error)
            if not (
                "unexpected keyword argument" in message
                and ({"confidence_floor", "use_confidence"} & kwargs.keys())
            ):
                raise
            kwargs.pop("use_confidence", None)
            kwargs.pop("confidence_floor", None)
            rows = search(plan.query, **kwargs)
        hits: list[RetrievalHit] = []
        seen_ids: set[str] = set()
        for row in rows or []:
            if not isinstance(row, Mapping):
                continue
            if plan.confidence_floor > 0.0:
                confidence = _finite_float(row.get("confidence", 1.0), default=0.0)
                if confidence is None or confidence < plan.confidence_floor:
                    continue
            row_workspace = str(row.get("workspace_id") or "").strip()
            row_tenant = str(row.get("tenant_id") or "").strip()
            # Defense in depth: a broken/custom store must not cross the
            # request scope while its results are being normalized.
            if row_workspace != plan.target.workspace_id or row_tenant != plan.target.tenant_id:
                continue
            hit_id = str(row.get("id") or "").strip()
            if not hit_id or hit_id in seen_ids:
                continue
            source = row.get("source")
            if not isinstance(source, Mapping):
                source = {}
            version = row.get("version")
            if not isinstance(version, Mapping):
                version = {}
            version_source = version.get("source")
            if not isinstance(version_source, Mapping):
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
            text = text[: plan.max_text_chars]
            raw_score = row.get("score")
            score = (
                0.0
                if raw_score is None or (isinstance(raw_score, str) and not raw_score.strip())
                else _finite_float(raw_score)
            )
            if score is None:
                # A malformed provider row must not poison the whole search
                # response or emit non-standard JSON (NaN/Infinity).
                continue
            hit = RetrievalHit(
                id=hit_id,
                workspace_id=row_workspace,
                tenant_id=row_tenant,
                title=str(row.get("title") or ""),
                text=text,
                score=score,
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
            seen_ids.add(hit_id)
            if len(hits) >= plan.limit:
                break
        return hits
