"""Extracted workspace knowledge responsibility boundary."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator, Mapping
from math import isfinite
from typing import Any, cast

from .knowledge import (
    normalize_filter as _normalized_filter_contract,
)
from .knowledge import (
    validate_limit as _validate_limit_contract,
)


class KnowledgeSearchService:
    def iter_active_resources(
        self,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        consolidation_status: str | None = None,
        limit: int = 5000,
    ) -> Iterator[dict[str, Any]]:
        """Yield current versions of resources for consolidation scanning."""
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        # Consolidation scans the whole base; allow a larger ceiling than the
        # public search limit but keep it bounded.
        limit = min(limit, self.MAX_SEARCH_LIMIT * 50)
        clauses = [
            "r.tenant_id = ?",
            "r.workspace_id = ?",
            "r.status = 'active'",
        ]
        params: list[Any] = [tenant_id, workspace_id]
        if consolidation_status is not None:
            if consolidation_status not in self.CONSOLIDATION_STATUSES:
                raise ValueError("unsupported consolidation_status")
            clauses.append("r.consolidation_status = ?")
            params.append(consolidation_status)
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT r.id, r.tenant_id, r.title, r.resource_type, r.source_type,
                    r.current_version, r.confidence, r.consolidation_status,
                    r.supersedes, r.last_hit, r.metadata_json, r.updated_at,
                    v.content_hash, v.searchable_text, v.source_episode
                FROM knowledge_resources r
                JOIN knowledge_versions v
                  ON v.resource_id = r.id AND v.version = r.current_version
                WHERE {" AND ".join(clauses)}
                ORDER BY r.updated_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        for row in rows:
            yield {
                "id": row["id"],
                "tenant_id": row["tenant_id"],
                "title": row["title"],
                "resource_type": row["resource_type"],
                "source_type": row["source_type"],
                "current_version": int(row["current_version"]),
                "confidence": float(row["confidence"]),
                "consolidation_status": row["consolidation_status"],
                "supersedes": row["supersedes"],
                "last_hit": row["last_hit"],
                "updated_at": row["updated_at"],
                "metadata": self._decode_json(row["metadata_json"], {}),
                "content_hash": row["content_hash"],
                "searchable_text": row["searchable_text"],
                "source_episode": row["source_episode"],
            }

    def vector_status(self) -> dict[str, Any]:
        """Return a user-safe summary of the knowledge vector backend."""
        outbox = self._vector_projector.metrics()
        if self.vector_store is None:
            return {
                "available": False,
                "count": 0,
                "last_search_mode": self.last_search_mode,
                "sync_pending": bool(outbox["unresolved"]),
                "outbox_pending": outbox["pending"],
                "outbox_dead_letters": outbox["dead"],
                "outbox_lag_seconds": outbox["oldest_lag_seconds"],
                "outbox_next_retry_at": outbox["next_retry_at"],
                "last_error": "vector search disabled",
            }
        status = self.vector_store.status()
        status["last_search_mode"] = self.last_search_mode
        status["sync_pending"] = bool(self.sync_pending or outbox["unresolved"])
        status["outbox_pending"] = outbox["pending"]
        status["outbox_dead_letters"] = outbox["dead"]
        status["outbox_lag_seconds"] = outbox["oldest_lag_seconds"]
        status["outbox_next_retry_at"] = outbox["next_retry_at"]
        return status

    def project_index_outbox(self, *, limit: int = 100) -> dict[str, Any]:
        """Project committed outbox rows into the rebuildable vector index."""
        return self._vector_projector.run_once(limit=limit)

    def _project_index_best_effort(self) -> None:
        self.sync_pending = True
        self.project_index_outbox()

    def sync_vector_index(self, *, force: bool = False) -> dict[str, Any]:
        """Synchronize the derived index from committed knowledge state."""
        self._sync_vector_index(force=force)
        now = self._utc_now()
        with self._connection(write=True) as connection:
            connection.execute(
                """
                UPDATE knowledge_index_outbox
                SET status = 'done', completed_at = ?, last_error = NULL,
                    lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE status != 'done'
                """,
                (now, now),
            )
        self.sync_pending = False
        return self.vector_status()

    def rebuild_vector_index(self) -> dict[str, Any]:
        """Rebuild all active current-version resource chunks."""
        return self.sync_vector_index(force=True)

    def _sync_vector_index(self, *, force: bool = False) -> None:
        vector_store = self.vector_store
        if vector_store is None or not vector_store.available:
            return
        with self._vector_lock:
            current = {
                item["id"]: item["metadata"] for item in vector_store.list_entries()
            }
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT r.id, r.tenant_id, r.workspace_id, r.title, r.resource_type,
                        r.source_type, r.current_version, v.content_hash,
                        v.searchable_text, v.structured_data_json
                    FROM knowledge_resources r
                    JOIN knowledge_versions v
                      ON v.resource_id = r.id AND v.version = r.current_version
                    WHERE r.status = 'active'
                      AND r.consolidation_status = 'active'
                    ORDER BY r.tenant_id, r.workspace_id, r.id
                    """
                ).fetchall()
            specs = self._vector_specs(rows)
            desired = {item["id"]: item["metadata"] for item in specs}
            if force or vector_store.needs_rebuild:
                vector_store.replace(
                    [self._embedded_vector_entry(item) for item in specs]
                )
                self.sync_pending = False
                return

            changed = [
                item for item in specs if current.get(item["id"]) != item["metadata"]
            ]
            removed_ids = set(current) - set(desired)
            if changed or removed_ids:
                vector_store.sync(
                    [self._embedded_vector_entry(item) for item in changed],
                    delete_ids=removed_ids,
                )
            self.sync_pending = False

    def _sync_vector_resources(
        self,
        resource_ids: Iterable[str],
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        """Update only resources changed by the just-committed transaction."""
        vector_store = self.vector_store
        if vector_store is None or not vector_store.available:
            return
        normalized_ids = {
            self._required_text(resource_id, "resource_id")
            for resource_id in resource_ids
        }
        if not normalized_ids:
            self.sync_pending = False
            return
        with self._vector_lock:
            if vector_store.needs_rebuild:
                raise RuntimeError(
                    "vector index requires a full rebuild before incremental sync"
                )
            current_entries = vector_store.list_entries()
            current = {
                item["id"]: item["metadata"]
                for item in current_entries
                if (item.get("metadata") or {}).get("resource_id") in normalized_ids
                and (
                    tenant_id is None
                    or (item.get("metadata") or {}).get("tenant_id") == tenant_id
                )
                and (
                    workspace_id is None
                    or (item.get("metadata") or {}).get("workspace_id") == workspace_id
                )
            }
            placeholders = ", ".join("?" for _ in normalized_ids)
            scope_clauses = [f"r.id IN ({placeholders})"]
            scope_parameters: list[Any] = list(normalized_ids)
            if tenant_id is not None:
                scope_clauses.append("r.tenant_id = ?")
                scope_parameters.append(tenant_id)
            if workspace_id is not None:
                scope_clauses.append("r.workspace_id = ?")
                scope_parameters.append(workspace_id)
            with self._connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT r.id, r.tenant_id, r.workspace_id, r.title,
                        r.resource_type, r.source_type, r.current_version,
                        v.content_hash, v.searchable_text, v.structured_data_json
                    FROM knowledge_resources r
                    JOIN knowledge_versions v
                      ON v.resource_id = r.id AND v.version = r.current_version
                    WHERE {" AND ".join(scope_clauses)}
                      AND r.status = 'active'
                      AND r.consolidation_status = 'active'
                    """,
                    tuple(scope_parameters),
                ).fetchall()
            specs = self._vector_specs(rows)
            desired = {item["id"]: item["metadata"] for item in specs}
            changed = [
                item for item in specs if current.get(item["id"]) != item["metadata"]
            ]
            removed_ids = set(current) - set(desired)
            if changed or removed_ids:
                vector_store.sync(
                    [self._embedded_vector_entry(item) for item in changed],
                    delete_ids=removed_ids,
                )
            self.sync_pending = False

    def _sync_vector_index_best_effort(
        self,
        *,
        resource_ids: Iterable[str] | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        """Keep committed knowledge authoritative when the derived index fails."""
        self.sync_pending = self.vector_store is not None
        try:
            if resource_ids is None:
                self._sync_vector_index()
            else:
                self._sync_vector_resources(
                    resource_ids,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
        except Exception as error:  # noqa: BLE001 - vector index is derived
            if self.vector_store is not None:
                self.vector_store.needs_rebuild = True
                self.vector_store.last_error = f"knowledge vector sync pending: {error}"

    def _vector_specs(self, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for row in rows:
            structured_text = (
                row["structured_data_json"]
                if row["structured_data_json"] != "null"
                else ""
            )
            body = "\n".join(
                part
                for part in (
                    str(row["title"]),
                    str(row["searchable_text"]),
                    structured_text,
                )
                if part.strip()
            )
            chunks = self._chunk_vector_text(body)
            for chunk_index, chunk_text in enumerate(chunks):
                logical_id = (
                    f"knowledge:{row['tenant_id']}:{row['workspace_id']}:{row['id']}:"
                    f"v{int(row['current_version'])}:c{chunk_index}"
                )
                metadata = {
                    "resource_id": row["id"],
                    "tenant_id": row["tenant_id"],
                    "workspace_id": row["workspace_id"],
                    "current_version": int(row["current_version"]),
                    "content_hash": row["content_hash"],
                    "title": row["title"],
                    "resource_type": row["resource_type"],
                    "source_type": row["source_type"],
                    "chunk_index": chunk_index,
                    "chunk_text": chunk_text,
                }
                entries.append(
                    {
                        "id": logical_id,
                        "text": chunk_text,
                        "metadata": metadata,
                    }
                )
        return entries

    def _embedded_vector_entry(self, item: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": item["id"],
            "vector": self.embedding_provider.embed(str(item["text"])),
            "metadata": item["metadata"],
        }

    @classmethod
    def _chunk_vector_text(cls, text: str) -> list[str]:
        normalized = "\n".join(
            line.strip() for line in str(text).splitlines() if line.strip()
        )
        if not normalized:
            return []
        chunks: list[str] = []
        start = 0
        while (
            start < len(normalized) and len(chunks) < cls.MAX_VECTOR_CHUNKS_PER_RESOURCE
        ):
            end = min(len(normalized), start + cls.VECTOR_CHUNK_CHARS)
            if end < len(normalized):
                boundary = normalized.rfind("\n", start, end)
                if boundary <= start + cls.VECTOR_CHUNK_CHARS // 2:
                    boundary = normalized.rfind("。", start, end)
                if boundary > start + cls.VECTOR_CHUNK_CHARS // 2:
                    end = boundary + 1
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            start = max(end - cls.VECTOR_CHUNK_OVERLAP, start + 1)
        return chunks

    def _search_vectors(
        self,
        query: str,
        *,
        tenant_id: str,
        workspace_id: str,
        limit: int,
        resource_types: frozenset[str],
        source_types: frozenset[str],
    ) -> list[dict[str, Any]]:
        vector_store = self.vector_store
        if vector_store is None or not vector_store.available:
            return []
        filters: dict[str, Any] = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
        }
        if resource_types:
            filters["resource_type"] = resource_types
        if source_types:
            filters["source_type"] = source_types
        query_vector = self.embedding_provider.embed(query)
        candidate_k = min(vector_store.count, max(32, limit * 8))
        while candidate_k > 0:
            matches = vector_store.search(
                query_vector,
                top_k=candidate_k,
                filters=filters,
            )
            resource_ids = {
                (item.get("metadata") or {}).get("resource_id")
                for item in matches
                if (item.get("metadata") or {}).get("resource_id")
            }
            if len(resource_ids) >= limit or candidate_k >= vector_store.count:
                return cast(list[dict[str, Any]], matches)
            candidate_k = min(vector_store.count, candidate_k * 2)
        return []

    def search(
        self,
        query: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 10,
        resource_types: Iterable[str] | None = None,
        source_types: Iterable[str] | None = None,
        include_rules: bool = True,
        max_text_chars: int = 4000,
        use_confidence: bool = False,
        confidence_floor: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Search current active resource versions and accepted rules."""
        query = self._required_text(query, "query")
        if len(query) > self.MAX_QUERY_CHARS:
            raise ValueError(f"query cannot exceed {self.MAX_QUERY_CHARS} characters")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        limit = self._validate_limit(limit)
        if (
            isinstance(max_text_chars, bool)
            or not isinstance(max_text_chars, int)
            or not 100 <= max_text_chars <= 20_000
        ):
            raise ValueError("max_text_chars must be between 100 and 20000")
        if isinstance(confidence_floor, bool) or not isinstance(
            confidence_floor, (int, float)
        ):
            raise ValueError(  # noqa: TRY004
                "confidence_floor must be between 0 and 1"
            )
        try:
            confidence_floor = float(confidence_floor)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("confidence_floor must be between 0 and 1") from error
        if not isfinite(confidence_floor) or not 0.0 <= confidence_floor <= 1.0:
            raise ValueError("confidence_floor must be between 0 and 1")
        resource_type_filter = self._normalized_filter(resource_types, "resource_types")
        source_type_filter = self._normalized_filter(source_types, "source_types")

        resource_sql = """
            SELECT r.*, v.version AS v_version, v.content_hash,
                v.searchable_text, v.mime_type,
                v.source_uri AS version_source_uri,
                v.source_id AS version_source_id,
                v.structured_data_json,
                v.metadata_json AS version_metadata_json,
                v.created_by, v.change_note, v.created_at AS version_created_at
            FROM knowledge_resources r
            JOIN knowledge_versions v
                ON v.resource_id = r.id AND v.version = r.current_version
            WHERE r.tenant_id = ? AND r.workspace_id = ?
                AND r.status = 'active'
                AND r.consolidation_status = 'active'
        """
        resource_params: list[Any] = [tenant_id, workspace_id]
        if resource_type_filter:
            placeholders = ", ".join("?" for _ in resource_type_filter)
            resource_sql += f" AND r.resource_type IN ({placeholders})"
            resource_params.extend(resource_type_filter)
        if source_type_filter:
            placeholders = ", ".join("?" for _ in source_type_filter)
            resource_sql += f" AND r.source_type IN ({placeholders})"
            resource_params.extend(source_type_filter)
        resource_sql += " ORDER BY r.updated_at DESC LIMIT 2000"

        with self._connection() as connection:
            resource_rows = connection.execute(resource_sql, resource_params).fetchall()
            rule_sql = (
                "SELECT * FROM knowledge_rules "
                "WHERE tenant_id = ? AND workspace_id = ? AND status = 'accepted'"
            )
            rule_params: list[Any] = [tenant_id, workspace_id]
            # Pre-filter accepted rules by a literal query match so the LIMIT
            # applies to already-relevant rows instead of silently dropping
            # older-but-matching rules (the Python scorer only keeps
            # score > 0, so any statement lacking a query term is dead weight).
            like_terms = [
                term
                for term in dict.fromkeys([query.strip(), *query.strip().split()])
                if term
            ]
            if like_terms:
                escape_char = "\\"
                like_clauses: list[str] = []
                for term in like_terms:
                    safe = (
                        term.replace(escape_char, escape_char + escape_char)
                        .replace("%", escape_char + "%")
                        .replace("_", escape_char + "_")
                    )
                    like_clauses.append("statement LIKE ? ESCAPE ?")
                    rule_params.append(f"%{safe}%")
                    rule_params.append(escape_char)
                rule_sql += " AND (" + " OR ".join(like_clauses) + ")"
            rule_sql += " ORDER BY updated_at DESC LIMIT 1000"
            rule_rows = (
                connection.execute(rule_sql, rule_params).fetchall()
                if include_rules
                else []
            )

        eligible_rows = {
            row["id"]: row
            for row in resource_rows
            if (
                not resource_type_filter or row["resource_type"] in resource_type_filter
            )
            and (not source_type_filter or row["source_type"] in source_type_filter)
        }
        vector_hits: list[dict[str, Any]] = []
        vector_available = bool(
            self.vector_store is not None
            and self.vector_store.available
            and not self.sync_pending
            and self._pending_index_count() == 0
        )
        if vector_available and eligible_rows:
            try:
                vector_hits = self._search_vectors(
                    query,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    limit=limit,
                    resource_types=resource_type_filter,
                    source_types=source_type_filter,
                )
                self.last_search_mode = "vector"
            except Exception as error:  # noqa: BLE001 - literal fallback is required
                self.last_search_mode = "literal-fallback"
                if self.vector_store is not None:
                    self.vector_store.last_error = f"knowledge search failed: {error}"
        else:
            self.last_search_mode = "vector" if vector_available else "literal-fallback"

        # Keep the inexpensive ranking fields separate from the full public
        # record.  Decoding three JSON columns for every eligible resource is
        # wasteful when the caller only asks for a small ``limit``.
        resource_candidates: dict[str, dict[str, Any]] = {}
        literal_scores: dict[str, float] = {}
        for hit in vector_hits:
            metadata = hit.get("metadata") or {}
            resource_id = metadata.get("resource_id")
            if not isinstance(resource_id, str) or not resource_id:
                continue
            row = eligible_rows.get(resource_id)
            if row is None:
                continue
            try:
                vector_score = float(hit.get("score", 0.0))
            except (TypeError, ValueError, OverflowError):
                continue
            if not isfinite(vector_score):
                continue
            haystack = "\n".join(
                [
                    row["title"],
                    row["searchable_text"],
                    row["source_uri"] or "",
                    row["structured_data_json"],
                ]
            )
            literal_score = self._literal_score(query, haystack)
            literal_scores[resource_id] = literal_score
            if vector_score < self.VECTOR_MIN_SCORE and literal_score <= 0:
                continue
            current = resource_candidates.get(resource_id)
            if current is not None and current["vector_score"] >= vector_score:
                continue
            chunk_text = str(metadata.get("chunk_text") or row["searchable_text"])
            resource_candidates[resource_id] = {
                "row": row,
                "retrieval_mode": "vector",
                "vector_score": vector_score,
                "literal_score": literal_score,
                "score": vector_score + min(literal_score, 10.0) * 0.1,
                "chunk_text": chunk_text,
            }

        # Exact matching supplements FAISS and is the explicit compatibility
        # fallback when the native extension cannot be loaded.
        for resource_id, row in eligible_rows.items():
            literal_score = literal_scores.get(resource_id)
            if literal_score is None:
                haystack = "\n".join(
                    [
                        row["title"],
                        row["searchable_text"],
                        row["source_uri"] or "",
                        row["structured_data_json"],
                    ]
                )
                literal_score = self._literal_score(query, haystack)
                literal_scores[resource_id] = literal_score
            if literal_score <= 0:
                continue
            existing = resource_candidates.get(resource_id)
            if existing is not None:
                existing["literal_score"] = literal_score
                existing["score"] = (
                    float(existing["vector_score"]) + min(literal_score, 10.0) * 0.1
                )
                continue
            resource_candidates[resource_id] = {
                "row": row,
                "retrieval_mode": (
                    "literal-supplement" if vector_available else "literal-fallback"
                ),
                "vector_score": None,
                "literal_score": literal_score,
                "score": min(literal_score, 10.0) * 0.1,
                "chunk_text": row["searchable_text"],
            }

        # A resource outside the top ``limit`` resources cannot enter the
        # top ``limit`` of the combined resource/rule result set. Rank by the
        # same effective score used below, then decode only the records needed
        # for the response.
        ranked_resources: list[tuple[float, dict[str, Any]]] = []
        for candidate in resource_candidates.values():
            try:
                confidence = float(candidate["row"]["confidence"])
            except (TypeError, ValueError, OverflowError):
                confidence = 0.0
            if not isfinite(confidence):
                confidence = 0.0
            if use_confidence and confidence < confidence_floor:
                continue
            effective_score = float(candidate["score"])
            if use_confidence:
                effective_score *= confidence
            ranked_resources.append((effective_score, candidate))
        ranked_resources.sort(
            key=lambda item: (
                item[0],
                item[1]["row"]["updated_at"],
                item[1]["row"]["id"],
            ),
            reverse=True,
        )
        results: list[dict[str, Any]] = []
        for _effective_score, candidate in ranked_resources[:limit]:
            row = candidate["row"]
            record = self._joined_resource_record(row)
            record.update(
                {
                    "record_type": "resource",
                    "retrieval_mode": candidate["retrieval_mode"],
                    "vector_score": candidate["vector_score"],
                    "literal_score": candidate["literal_score"],
                    "score": candidate["score"],
                    "text": self._excerpt(
                        candidate["chunk_text"], query, max_text_chars
                    ),
                }
            )
            results.append(record)

        if include_rules and (
            not resource_type_filter or "rule" in resource_type_filter
        ):
            for row in rule_rows:
                score = self._literal_score(query, row["statement"])
                if score <= 0:
                    continue
                record = self._rule_record(row)
                record.update(
                    {
                        "record_type": "rule",
                        "retrieval_mode": "accepted-rule",
                        "literal_score": score,
                        "score": min(score, 10.0) * 0.1,
                        "text": row["statement"],
                        "title": "已采纳规则",
                    }
                )
                results.append(record)

        if use_confidence:
            weighted: list[dict[str, Any]] = []
            for item in results:
                try:
                    conf = float(item.get("confidence", 1.0))
                except (TypeError, ValueError, OverflowError):
                    conf = 0.0
                if not isfinite(conf):
                    conf = 0.0
                if conf < confidence_floor:
                    continue
                item = dict(item)
                item["score"] = float(item.get("score", 0.0)) * conf
                item["confidence_weighted"] = True
                weighted.append(item)
            results = weighted

        results.sort(
            key=lambda item: (item["score"], item["updated_at"], item["id"]),
            reverse=True,
        )
        return results[:limit]

    @classmethod
    def _validate_limit(cls, value: int) -> int:
        return _validate_limit_contract(value, maximum=cls.MAX_SEARCH_LIMIT)

    @classmethod
    def _normalized_filter(
        cls,
        values: Iterable[str] | None,
        field: str,
    ) -> frozenset[str]:
        return _normalized_filter_contract(values, field)

    @staticmethod
    def _literal_score(query: str, haystack: str) -> float:
        query_text = query.casefold().strip()
        searchable = haystack.casefold()
        terms = list(dict.fromkeys([query_text, *query_text.split()]))
        score = 0.0
        for index, term in enumerate(terms):
            if not term:
                continue
            occurrences = searchable.count(term)
            score += occurrences * (4.0 if index == 0 else 1.0)
        return score

    @staticmethod
    def _excerpt(text: str, query: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        position = text.casefold().find(query.casefold())
        if position < 0:
            return text[:limit]
        start = max(0, position - limit // 3)
        end = min(len(text), start + limit)
        start = max(0, end - limit)
        prefix = "..." if start else ""
        suffix = "..." if end < len(text) else ""
        return f"{prefix}{text[start:end]}{suffix}"

    @classmethod
    def _joined_resource_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "workspace_id": row["workspace_id"],
            "title": row["title"],
            "resource_type": row["resource_type"],
            "source": {
                "type": row["source_type"],
                "uri": row["source_uri"],
                "id": row["source_id"],
            },
            "status": row["status"],
            "current_version": int(row["current_version"]),
            "metadata": cls._decode_json(row["metadata_json"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "confidence": float(row["confidence"]),
            "consolidation_status": row["consolidation_status"],
            "supersedes": row["supersedes"],
            "last_hit": row["last_hit"],
            "version": {
                "version": int(row["v_version"]),
                "content_hash": row["content_hash"],
                "searchable_text": row["searchable_text"],
                "mime_type": row["mime_type"],
                "source": {
                    "uri": row["version_source_uri"],
                    "id": row["version_source_id"],
                },
                "structured_data": cls._decode_json(row["structured_data_json"], None),
                "metadata": cls._decode_json(row["version_metadata_json"], {}),
                "created_by": row["created_by"],
                "change_note": row["change_note"],
                "created_at": row["version_created_at"],
            },
        }


__all__ = ["KnowledgeSearchService"]
