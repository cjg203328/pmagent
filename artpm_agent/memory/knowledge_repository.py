"""Extracted workspace knowledge responsibility boundary."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from hashlib import sha256
from typing import Any
from uuid import uuid4

from artpm_agent.tenancy import TenantContextManager, WorkspaceAccessDenied

from .knowledge import (
    content_hash as _content_hash_contract,
)
from .knowledge import (
    deserialize_json as _decode_json_contract,
)
from .knowledge import (
    normalize_searchable_text as _normalize_searchable_text_contract,
)
from .knowledge import (
    optional_text as _optional_text_contract,
)
from .knowledge import (
    required_text as _required_text_contract,
)
from .knowledge import (
    serialize_json as _json_contract,
)


class KnowledgeProposalConflictError(RuntimeError):
    """Raised when an ingestion proposal loses a compare-and-swap transition."""


class KnowledgeRepository:
    def _enqueue_index_work(
        self,
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        workspace_id: str,
        resource_id: str | None,
        operation: str,
    ) -> None:
        now = self._utc_now()
        connection.execute(
            """
            INSERT INTO knowledge_index_outbox(
                tenant_id, workspace_id, resource_id, operation,
                status, available_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (tenant_id, workspace_id, resource_id, operation, now, now, now),
        )

    def _pending_index_count(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM knowledge_index_outbox "
                "WHERE status != 'done'"
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    @staticmethod
    def _required_text(value: Any, field: str) -> str:
        return _required_text_contract(value, field)

    def _resolve_scope(
        self,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> tuple[str, str]:
        """Resolve an explicit scope without allowing tenant context overrides."""

        current = TenantContextManager.get_current()
        if current is not None:
            if tenant_id not in {None, "", current.tenant_id}:
                raise WorkspaceAccessDenied(
                    "tenant does not match the authenticated context"
                )
            resolved_workspace = current.require_workspace(workspace_id)
            resolved_tenant = current.tenant_id
            self._claim_legacy_scope(resolved_tenant, resolved_workspace)
            return resolved_tenant, resolved_workspace
        resolved_tenant = self._required_text(
            tenant_id or self.DEFAULT_TENANT_ID, "tenant_id"
        )
        resolved_workspace = self._required_text(
            workspace_id or self.DEFAULT_WORKSPACE_ID, "workspace_id"
        )
        return resolved_tenant, resolved_workspace

    def _resolve_transition_scope(
        self,
        workspace_id: str | None,
        tenant_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Keep legacy id-only rule transitions working outside a request."""

        if workspace_id is None and tenant_id is None:
            current = TenantContextManager.get_current()
            if current is None:
                return None, None
        resolved_tenant, resolved_workspace = self._resolve_scope(
            workspace_id, tenant_id
        )
        return resolved_tenant, resolved_workspace

    def _claim_legacy_scope(self, tenant_id: str, workspace_id: str) -> bool:
        """Bind unowned pre-tenant rows only on a trusted first access.

        Databases created before tenant support contain rows assigned to the
        local compatibility tenant. A workspace with any explicit non-local
        ownership is never claimed, preventing an ambiguous legacy row from
        becoming visible in another tenant.
        """
        if tenant_id == self.DEFAULT_TENANT_ID:
            return False
        tables = (
            "knowledge_resources",
            "knowledge_rules",
            "knowledge_ingestion_proposals",
            "knowledge_ingestion_events",
        )
        claimed = False
        with self._connection(write=True) as connection:
            for table in tables:
                row = connection.execute(
                    f"""
                    SELECT 1 FROM {table}
                    WHERE workspace_id = ?
                      AND tenant_id NOT IN (?, ?)
                    LIMIT 1
                    """,
                    (workspace_id, self.DEFAULT_TENANT_ID, tenant_id),
                ).fetchone()
                if row is not None:
                    return False

            version_cursor = connection.execute(
                """
                UPDATE knowledge_versions
                SET tenant_id = ?
                WHERE tenant_id = ?
                  AND resource_id IN (
                      SELECT id FROM knowledge_resources
                      WHERE tenant_id = ? AND workspace_id = ?
                  )
                """,
                (
                    tenant_id,
                    self.DEFAULT_TENANT_ID,
                    self.DEFAULT_TENANT_ID,
                    workspace_id,
                ),
            )
            for table in (
                "knowledge_resources",
                "knowledge_rules",
                "knowledge_ingestion_proposals",
                "knowledge_ingestion_events",
            ):
                cursor = connection.execute(
                    f"""
                    UPDATE {table}
                    SET tenant_id = ?
                    WHERE tenant_id = ? AND workspace_id = ?
                    """,
                    (tenant_id, self.DEFAULT_TENANT_ID, workspace_id),
                )
                claimed = claimed or cursor.rowcount > 0
            claimed = claimed or version_cursor.rowcount > 0

            if claimed:
                self._enqueue_index_work(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    resource_id=None,
                    operation="rebuild",
                )

        if claimed:
            self._project_index_best_effort()
        return claimed

    @staticmethod
    def _optional_text(value: Any, field: str) -> str | None:
        return _optional_text_contract(value, field)

    @staticmethod
    def _json(value: Any, field: str, *, mapping: bool = False) -> str:
        return _json_contract(value, field, mapping=mapping)

    @staticmethod
    def _decode_json(value: str, fallback: Any) -> Any:
        return _decode_json_contract(value, fallback)

    @classmethod
    def _normalize_searchable_text(
        cls,
        searchable_text: Any,
        structured_data_json: str,
    ) -> str:
        return _normalize_searchable_text_contract(
            searchable_text,
            structured_data_json,
            max_chars=cls.MAX_SEARCHABLE_TEXT_CHARS,
        )

    @staticmethod
    def _content_hash(
        searchable_text: str,
        structured_data_json: str,
        mime_type: str | None,
    ) -> str:
        return _content_hash_contract(searchable_text, structured_data_json, mime_type)

    def ingest_resource(
        self,
        *,
        title: str,
        searchable_text: str = "",
        resource_type: str = "document",
        source_type: str = "manual",
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        source_uri: str | None = None,
        source_id: str | None = None,
        mime_type: str | None = None,
        structured_data: Any = None,
        metadata: Mapping[str, Any] | None = None,
        version_metadata: Mapping[str, Any] | None = None,
        created_by: str | None = None,
        change_note: str | None = None,
        resource_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or version a resource, resolving identity by ID or source ID."""
        title = self._required_text(title, "title")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        resource_type = self._required_text(resource_type, "resource_type")
        source_type = self._required_text(source_type, "source_type")
        source_uri = self._optional_text(source_uri, "source_uri")
        source_id = self._optional_text(source_id, "source_id")
        mime_type = self._optional_text(mime_type, "mime_type")
        created_by = self._optional_text(created_by, "created_by")
        change_note = self._optional_text(change_note, "change_note")
        if resource_id is not None:
            resource_id = self._required_text(resource_id, "resource_id")

        structured_json = self._json(structured_data, "structured_data")
        searchable_text = self._normalize_searchable_text(
            searchable_text, structured_json
        )
        metadata_json = self._json(metadata, "metadata", mapping=True)
        version_metadata_json = self._json(
            version_metadata, "version_metadata", mapping=True
        )
        content_hash = self._content_hash(searchable_text, structured_json, mime_type)
        with self._connection(write=True) as connection:
            result = self._ingest_prepared_resource(
                connection,
                title=title,
                searchable_text=searchable_text,
                resource_type=resource_type,
                source_type=source_type,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                source_uri=source_uri,
                source_id=source_id,
                mime_type=mime_type,
                structured_json=structured_json,
                metadata_json=metadata_json,
                version_metadata_json=version_metadata_json,
                created_by=created_by,
                change_note=change_note,
                resource_id=resource_id,
                content_hash=content_hash,
                now=self._utc_now(),
            )
        self._project_index_best_effort()
        return result

    def _ingest_prepared_resource(
        self,
        connection: sqlite3.Connection,
        *,
        title: str,
        searchable_text: str,
        resource_type: str,
        source_type: str,
        tenant_id: str,
        workspace_id: str,
        source_uri: str | None,
        source_id: str | None,
        mime_type: str | None,
        structured_json: str,
        metadata_json: str,
        version_metadata_json: str,
        created_by: str | None,
        change_note: str | None,
        resource_id: str | None,
        content_hash: str,
        now: str,
    ) -> dict[str, Any]:
        resource_row = None
        if resource_id is not None:
            resource_row = connection.execute(
                """SELECT * FROM knowledge_resources
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?""",
                (resource_id, tenant_id, workspace_id),
            ).fetchone()
        elif source_id is not None:
            resource_row = connection.execute(
                """
                SELECT * FROM knowledge_resources
                WHERE tenant_id = ? AND workspace_id = ?
                  AND source_type = ? AND source_id = ?
                """,
                (tenant_id, workspace_id, source_type, source_id),
            ).fetchone()

        if resource_row is None:
            resource_id = resource_id or uuid4().hex
            connection.execute(
                """
                INSERT INTO knowledge_resources(
                    id, tenant_id, workspace_id, title, resource_type, source_type,
                    source_uri, source_id, status, current_version,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)
                """,
                (
                    resource_id,
                    tenant_id,
                    workspace_id,
                    title,
                    resource_type,
                    source_type,
                    source_uri,
                    source_id,
                    metadata_json,
                    now,
                    now,
                ),
            )
            current_version = 0
        else:
            resource_id = resource_row["id"]
            current_version = int(resource_row["current_version"])
            if resource_row["resource_type"] != resource_type:
                raise ValueError("resource_type cannot change across versions")
            if resource_row["source_type"] != source_type:
                raise ValueError("source_type cannot change across versions")
            current_metadata = self._decode_json(resource_row["metadata_json"], {})
            current_metadata.update(self._decode_json(metadata_json, {}))
            metadata_json = self._json(current_metadata, "metadata", mapping=True)

        current_version_row = None
        if current_version:
            current_version_row = connection.execute(
                """
                SELECT * FROM knowledge_versions
                WHERE resource_id = ? AND version = ?
                """,
                (resource_id, current_version),
            ).fetchone()

        version_created = not (
            current_version_row is not None
            and current_version_row["content_hash"] == content_hash
        )
        if version_created:
            current_version += 1
            connection.execute(
                """
                INSERT INTO knowledge_versions(
                    resource_id, tenant_id, version, content_hash, searchable_text,
                    mime_type, source_uri, source_id, structured_data_json,
                    metadata_json, created_by, change_note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resource_id,
                    tenant_id,
                    current_version,
                    content_hash,
                    searchable_text,
                    mime_type,
                    source_uri,
                    source_id,
                    structured_json,
                    version_metadata_json,
                    created_by,
                    change_note,
                    now,
                ),
            )

        connection.execute(
            """
            UPDATE knowledge_resources
            SET title = ?, source_uri = ?, status = 'active',
                current_version = ?, metadata_json = ?, updated_at = ?
            WHERE id = ? AND tenant_id = ? AND workspace_id = ?
            """,
            (
                title,
                source_uri,
                current_version,
                metadata_json,
                now,
                resource_id,
                tenant_id,
                workspace_id,
            ),
        )
        resource_row = connection.execute(
            """SELECT * FROM knowledge_resources
            WHERE id = ? AND tenant_id = ? AND workspace_id = ?""",
            (resource_id, tenant_id, workspace_id),
        ).fetchone()
        version_row = connection.execute(
            """
            SELECT * FROM knowledge_versions
            WHERE resource_id = ? AND version = ?
            """,
            (resource_id, current_version),
        ).fetchone()

        result = self._resource_record(resource_row, version_row)
        result["version_created"] = version_created
        self._enqueue_index_work(
            connection,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            resource_id=resource_id,
            operation="upsert",
        )
        return result

    def get_resource(
        self,
        resource_id: str,
        *,
        version: int | None = None,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        resource_id = self._required_text(resource_id, "resource_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        if version is not None and (
            isinstance(version, bool) or not isinstance(version, int) or version <= 0
        ):
            raise ValueError("version must be a positive integer")
        with self._connection() as connection:
            resource_row = connection.execute(
                """SELECT * FROM knowledge_resources
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?""",
                (resource_id, tenant_id, workspace_id),
            ).fetchone()
            if resource_row is None:
                return None
            selected_version = version or int(resource_row["current_version"])
            version_row = connection.execute(
                """
                SELECT * FROM knowledge_versions
                WHERE resource_id = ? AND version = ?
                """,
                (resource_id, selected_version),
            ).fetchone()
        if version_row is None:
            return None
        return self._resource_record(resource_row, version_row)

    def get_resource_by_source(
        self,
        *,
        source_type: str,
        source_id: str,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Find a resource by its stable external source identity."""
        source_type = self._required_text(source_type, "source_type")
        source_id = self._required_text(source_id, "source_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT id FROM knowledge_resources
                WHERE tenant_id = ? AND workspace_id = ?
                  AND source_type = ? AND source_id = ?
                """,
                (tenant_id, workspace_id, source_type, source_id),
            ).fetchone()
        if row is None:
            return None
        return self.get_resource(
            row["id"], workspace_id=workspace_id, tenant_id=tenant_id
        )

    def list_versions(
        self,
        resource_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        resource_id = self._required_text(resource_id, "resource_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        parameters = (resource_id, tenant_id, workspace_id)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT v.* FROM knowledge_versions v "
                "JOIN knowledge_resources r ON r.id = v.resource_id "
                "WHERE v.resource_id = ? AND r.tenant_id = ? AND r.workspace_id = ?"
                + " ORDER BY v.version DESC",
                parameters,
            ).fetchall()
        return [self._version_record(row) for row in rows]

    def list_resources(
        self,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        limit = self._validate_limit(limit)
        status_clause = "" if include_archived else "AND r.status = 'active'"
        with self._connection() as connection:
            rows = connection.execute(
                f"""
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
                WHERE r.tenant_id = ? AND r.workspace_id = ? {status_clause}
                ORDER BY r.updated_at DESC, r.id DESC
                LIMIT ?
                """,
                (tenant_id, workspace_id, limit),
            ).fetchall()
        return [self._joined_resource_record(row) for row in rows]

    def archive_resource(
        self,
        resource_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        resource_id = self._required_text(resource_id, "resource_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        parameters: tuple[Any, ...] = (
            self._utc_now(),
            resource_id,
            tenant_id,
            workspace_id,
        )
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                "UPDATE knowledge_resources "
                "SET status = 'archived', updated_at = ? "
                "WHERE id = ? AND tenant_id = ? AND workspace_id = ? "
                "AND status != 'archived'",
                parameters,
            )
            if cursor.rowcount > 0:
                self._enqueue_index_work(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    resource_id=resource_id,
                    operation="delete",
                )
        archived = cursor.rowcount > 0
        if archived:
            self._project_index_best_effort()
        return archived

    def propose_ingestion(
        self,
        conversation_id: str,
        turn_id: str,
        resources: Iterable[Mapping[str, Any]],
        idempotency_key: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        proposed_by: str = "agent",
    ) -> dict[str, Any]:
        """Persist an inert ingestion proposal without creating knowledge."""
        conversation_id = self._required_text(conversation_id, "conversation_id")
        turn_id = self._required_text(turn_id, "turn_id")
        idempotency_key = self._required_text(idempotency_key, "idempotency_key")
        if len(idempotency_key) > 200:
            raise ValueError("idempotency_key cannot exceed 200 characters")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        proposed_by = self._required_text(proposed_by, "proposed_by")
        normalized_resources = self._normalize_ingestion_resources(resources)
        request_json = self._json(normalized_resources, "resources")
        payload_bytes = len(request_json.encode("utf-8"))
        if payload_bytes > self.MAX_INGESTION_PAYLOAD_BYTES:
            raise ValueError(
                "ingestion proposal payload exceeds "
                f"{self.MAX_INGESTION_PAYLOAD_BYTES} bytes"
            )
        payload_hash = sha256(request_json.encode("utf-8")).hexdigest()
        proposal_id = uuid4().hex
        now = self._utc_now()

        with self._connection(write=True) as connection:
            self._validate_conversation_scope(
                connection, conversation_id, workspace_id, tenant_id
            )
            existing = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_proposals
                WHERE tenant_id = ? AND workspace_id = ? AND idempotency_key = ?
                """,
                (tenant_id, workspace_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["conversation_id"] != conversation_id
                    or existing["turn_id"] != turn_id
                    or existing["payload_hash"] != payload_hash
                ):
                    raise ValueError(
                        "idempotency_key was used for another ingestion proposal"
                    )
                return self._ingestion_proposal_record(existing)

            versioned_resources = []
            for resource in normalized_resources:
                versioned = dict(resource)
                existing_resource = None
                if resource["source_id"] is not None:
                    existing_resource = connection.execute(
                        """
                        SELECT * FROM knowledge_resources
                        WHERE tenant_id = ? AND workspace_id = ? AND source_type = ?
                          AND source_id = ?
                        """,
                        (
                            tenant_id,
                            workspace_id,
                            resource["source_type"],
                            resource["source_id"],
                        ),
                    ).fetchone()
                if (
                    existing_resource is not None
                    and existing_resource["resource_type"] != resource["resource_type"]
                ):
                    raise ValueError("resource_type does not match the existing source")
                versioned["expected_resource_id"] = (
                    existing_resource["id"] if existing_resource else None
                )
                versioned["base_version"] = (
                    int(existing_resource["current_version"])
                    if existing_resource
                    else 0
                )
                versioned_resources.append(versioned)

            resources_json = self._json(versioned_resources, "versioned_resources")
            connection.execute(
                """
                INSERT INTO knowledge_ingestion_proposals(
                    id, tenant_id, workspace_id, conversation_id, turn_id,
                    idempotency_key, status, state_version, payload_hash,
                    resources_json, resource_count, payload_bytes,
                    proposed_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    tenant_id,
                    workspace_id,
                    conversation_id,
                    turn_id,
                    idempotency_key,
                    payload_hash,
                    resources_json,
                    len(versioned_resources),
                    payload_bytes,
                    proposed_by,
                    now,
                    now,
                ),
            )
            self._ingestion_event(
                connection,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                proposal_id=proposal_id,
                event_type="ingestion.proposed",
                actor=proposed_by,
                payload={
                    "conversation_id": conversation_id,
                    "turn_id": turn_id,
                    "payload_hash": payload_hash,
                    "resource_count": len(versioned_resources),
                    "payload_bytes": payload_bytes,
                },
            )
            row = connection.execute(
                """SELECT * FROM knowledge_ingestion_proposals
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?""",
                (proposal_id, tenant_id, workspace_id),
            ).fetchone()
        return self._ingestion_proposal_record(row)

    def confirm_ingestion(
        self,
        proposal_id: str,
        *,
        actor: str,
        confirmation_token: str,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        """CAS-confirm a proposal and atomically ingest all candidate resources."""
        proposal_id = self._required_text(proposal_id, "proposal_id")
        actor = self._required_text(actor, "actor")
        confirmation_token = self._required_text(
            confirmation_token, "confirmation_token"
        )
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        expected_state_version = self._optional_state_version(expected_state_version)
        confirmation_hash = sha256(confirmation_token.encode("utf-8")).hexdigest()
        conflict_reason = None

        with self._connection(write=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_proposals
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                """,
                (proposal_id, tenant_id, workspace_id),
            ).fetchone()
            if row is None:
                raise KeyError("unknown ingestion proposal for workspace")
            if row["status"] == "confirmed":
                result = self._ingestion_proposal_record(row)
                result["decision_created"] = False
                return result
            if row["status"] != "pending":
                raise KnowledgeProposalConflictError(
                    f"proposal cannot be confirmed from {row['status']}"
                )
            state_version = int(row["state_version"])
            if (
                expected_state_version is not None
                and expected_state_version != state_version
            ):
                raise KnowledgeProposalConflictError(
                    "ingestion proposal state changed before confirmation"
                )

            resources = self._decode_json(row["resources_json"], [])
            for resource in resources:
                current_resource = None
                expected_resource_id = resource.get("expected_resource_id")
                if expected_resource_id:
                    current_resource = connection.execute(
                        """
                        SELECT * FROM knowledge_resources
                        WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                        """,
                        (expected_resource_id, tenant_id, workspace_id),
                    ).fetchone()
                elif resource.get("source_id"):
                    current_resource = connection.execute(
                        """
                        SELECT * FROM knowledge_resources
                        WHERE tenant_id = ? AND workspace_id = ? AND source_type = ?
                          AND source_id = ?
                        """,
                        (
                            tenant_id,
                            workspace_id,
                            resource["source_type"],
                            resource["source_id"],
                        ),
                    ).fetchone()
                current_version = (
                    int(current_resource["current_version"])
                    if current_resource is not None
                    else 0
                )
                if current_version != int(resource["base_version"]) or (
                    expected_resource_id
                    and (
                        current_resource is None
                        or current_resource["id"] != expected_resource_id
                    )
                ):
                    conflict_reason = (
                        f"source changed after proposal: {resource['title']}"
                    )
                    break

            now = self._utc_now()
            if conflict_reason is not None:
                cursor = connection.execute(
                    """
                    UPDATE knowledge_ingestion_proposals
                    SET status = 'conflict', state_version = state_version + 1,
                        actor = ?, updated_at = ?, decided_at = ?
                    WHERE id = ? AND status = 'pending' AND state_version = ?
                    """,
                    (actor, now, now, proposal_id, state_version),
                )
                if cursor.rowcount != 1:
                    raise KnowledgeProposalConflictError(
                        "ingestion proposal confirmation lost a race"
                    )
                self._ingestion_event(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    proposal_id=proposal_id,
                    event_type="ingestion.conflict",
                    actor=actor,
                    payload={"reason": conflict_reason},
                )
            else:
                ingested = []
                for resource in resources:
                    structured_json = self._json(
                        resource.get("structured_data"), "structured_data"
                    )
                    searchable_text = self._normalize_searchable_text(
                        resource.get("searchable_text", ""), structured_json
                    )
                    mime_type = resource.get("mime_type")
                    ingested_resource = self._ingest_prepared_resource(
                        connection,
                        title=resource["title"],
                        searchable_text=searchable_text,
                        resource_type=resource["resource_type"],
                        source_type=resource["source_type"],
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        source_uri=resource.get("source_uri"),
                        source_id=resource.get("source_id"),
                        mime_type=mime_type,
                        structured_json=structured_json,
                        metadata_json=self._json(
                            resource.get("metadata"), "metadata", mapping=True
                        ),
                        version_metadata_json=self._json(
                            {
                                "ingestion_proposal_id": proposal_id,
                                "conversation_id": row["conversation_id"],
                                "turn_id": row["turn_id"],
                            },
                            "version_metadata",
                            mapping=True,
                        ),
                        created_by=actor,
                        change_note="用户确认的资料入库提案",
                        resource_id=resource.get("expected_resource_id"),
                        content_hash=self._content_hash(
                            searchable_text, structured_json, mime_type
                        ),
                        now=now,
                    )
                    ingested.append(
                        {
                            "id": ingested_resource["id"],
                            "title": ingested_resource["title"],
                            "current_version": ingested_resource["current_version"],
                            "version_created": ingested_resource["version_created"],
                        }
                    )

                result_json = self._json(ingested, "ingestion_result")
                cursor = connection.execute(
                    """
                    UPDATE knowledge_ingestion_proposals
                    SET status = 'confirmed', state_version = state_version + 1,
                        actor = ?, confirmation_hash = ?, result_json = ?,
                        updated_at = ?, decided_at = ?
                    WHERE id = ? AND status = 'pending' AND state_version = ?
                    """,
                    (
                        actor,
                        confirmation_hash,
                        result_json,
                        now,
                        now,
                        proposal_id,
                        state_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise KnowledgeProposalConflictError(
                        "ingestion proposal confirmation lost a race"
                    )
                self._ingestion_event(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    proposal_id=proposal_id,
                    event_type="ingestion.confirmed",
                    actor=actor,
                    payload={"resources": ingested},
                )

        if conflict_reason is not None:
            raise KnowledgeProposalConflictError(conflict_reason)
        self._project_index_best_effort()
        confirmed_result = self.get_ingestion_proposal(
            proposal_id, workspace_id=workspace_id, tenant_id=tenant_id
        )
        if confirmed_result is None:
            raise KnowledgeProposalConflictError(
                "ingestion proposal disappeared after confirmation"
            )
        confirmed_result["decision_created"] = True
        return confirmed_result

    def reject_ingestion(
        self,
        proposal_id: str,
        *,
        actor: str,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        proposal_id = self._required_text(proposal_id, "proposal_id")
        actor = self._required_text(actor, "actor")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        expected_state_version = self._optional_state_version(expected_state_version)
        now = self._utc_now()
        with self._connection(write=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_proposals
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                """,
                (proposal_id, tenant_id, workspace_id),
            ).fetchone()
            if row is None:
                raise KeyError("unknown ingestion proposal for workspace")
            if row["status"] == "rejected":
                return self._ingestion_proposal_record(row)
            if row["status"] != "pending":
                raise KnowledgeProposalConflictError(
                    f"proposal cannot be rejected from {row['status']}"
                )
            state_version = int(row["state_version"])
            if (
                expected_state_version is not None
                and expected_state_version != state_version
            ):
                raise KnowledgeProposalConflictError(
                    "ingestion proposal state changed before rejection"
                )
            cursor = connection.execute(
                """
                UPDATE knowledge_ingestion_proposals
                SET status = 'rejected', state_version = state_version + 1,
                    actor = ?, updated_at = ?, decided_at = ?
                WHERE id = ? AND status = 'pending' AND state_version = ?
                """,
                (actor, now, now, proposal_id, state_version),
            )
            if cursor.rowcount != 1:
                raise KnowledgeProposalConflictError(
                    "ingestion proposal rejection lost a race"
                )
            self._ingestion_event(
                connection,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                proposal_id=proposal_id,
                event_type="ingestion.rejected",
                actor=actor,
                payload={},
            )
            row = connection.execute(
                """SELECT * FROM knowledge_ingestion_proposals
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?""",
                (proposal_id, tenant_id, workspace_id),
            ).fetchone()
        return self._ingestion_proposal_record(row)

    def get_ingestion_proposal(
        self,
        proposal_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        proposal_id = self._required_text(proposal_id, "proposal_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_proposals
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                """,
                (proposal_id, tenant_id, workspace_id),
            ).fetchone()
        return self._ingestion_proposal_record(row) if row is not None else None

    def list_ingestion_proposals(
        self,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        status: str | None = None,
        conversation_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        allowed_statuses = {"pending", "confirmed", "rejected", "conflict"}
        if status is not None and status not in allowed_statuses:
            raise ValueError(f"Unsupported ingestion proposal status: {status}")
        conversation_id = self._optional_text(conversation_id, "conversation_id")
        limit = self._validate_limit(limit)
        clauses = ["tenant_id = ?", "workspace_id = ?"]
        parameters: list[Any] = [tenant_id, workspace_id]
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            parameters.append(conversation_id)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM knowledge_ingestion_proposals
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._ingestion_proposal_record(row) for row in rows]

    def list_ingestion_events(
        self,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        proposal_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        proposal_id = self._optional_text(proposal_id, "proposal_id")
        limit = self._validate_limit(limit)
        proposal_clause = "AND proposal_id = ?" if proposal_id else ""
        parameters: list[Any] = [tenant_id, workspace_id]
        if proposal_id:
            parameters.append(proposal_id)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM knowledge_ingestion_events
                WHERE tenant_id = ? AND workspace_id = ? {proposal_clause}
                ORDER BY id ASC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._ingestion_event_record(row) for row in rows]

    def _normalize_ingestion_resources(
        self,
        resources: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        if isinstance(resources, (str, bytes, Mapping)):
            raise ValueError("resources must be a list of mappings")  # noqa: TRY004
        try:
            raw_resources = list(resources)
        except TypeError as error:
            raise ValueError("resources must be iterable") from error
        if not 1 <= len(raw_resources) <= self.MAX_INGESTION_RESOURCES:
            raise ValueError(
                "resources must contain between 1 and "
                f"{self.MAX_INGESTION_RESOURCES} items"
            )
        allowed_fields = {
            "title",
            "searchable_text",
            "resource_type",
            "source_type",
            "source_uri",
            "source_id",
            "mime",
            "mime_type",
            "structured_data",
            "metadata",
        }
        normalized = []
        for index, resource in enumerate(raw_resources):
            if not isinstance(resource, Mapping):
                raise ValueError(  # noqa: TRY004
                    f"resources[{index}] must be a mapping"
                )
            unknown_fields = set(resource) - allowed_fields
            if unknown_fields:
                raise ValueError(
                    f"resources[{index}] has unsupported fields: "
                    f"{', '.join(sorted(unknown_fields))}"
                )
            mime = resource.get("mime")
            mime_type = resource.get("mime_type")
            if mime is not None and mime_type is not None and mime != mime_type:
                raise ValueError(f"resources[{index}] mime and mime_type disagree")
            mime_type = self._optional_text(
                mime_type if mime_type is not None else mime,
                f"resources[{index}].mime",
            )
            structured_json = self._json(
                resource.get("structured_data"),
                f"resources[{index}].structured_data",
            )
            searchable_text = self._normalize_searchable_text(
                resource.get("searchable_text", ""), structured_json
            )
            metadata_json = self._json(
                resource.get("metadata"),
                f"resources[{index}].metadata",
                mapping=True,
            )
            normalized.append(
                {
                    "title": self._required_text(
                        resource.get("title"), f"resources[{index}].title"
                    ),
                    "searchable_text": searchable_text,
                    "resource_type": self._required_text(
                        resource.get("resource_type", "document"),
                        f"resources[{index}].resource_type",
                    ),
                    "source_type": self._required_text(
                        resource.get("source_type", "conversation"),
                        f"resources[{index}].source_type",
                    ),
                    "source_uri": self._optional_text(
                        resource.get("source_uri"),
                        f"resources[{index}].source_uri",
                    ),
                    "source_id": self._optional_text(
                        resource.get("source_id"),
                        f"resources[{index}].source_id",
                    ),
                    "mime_type": mime_type,
                    "structured_data": self._decode_json(structured_json, None),
                    "metadata": self._decode_json(metadata_json, {}),
                }
            )
        return normalized

    @staticmethod
    def _optional_state_version(value: int | None) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("expected_state_version must be a positive integer")
        return value

    @staticmethod
    def _validate_conversation_scope(
        connection: sqlite3.Connection,
        conversation_id: str,
        workspace_id: str,
        tenant_id: str | None = None,
    ) -> None:
        table = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'conversations'
            """
        ).fetchone()
        if table is None:
            return
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
        }
        if "workspace_id" not in columns:
            return
        tenant_clause = ""
        parameters: list[Any] = [conversation_id, workspace_id]
        if tenant_id and "tenant_id" in columns:
            tenant_clause = " AND tenant_id = ?"
            parameters.append(tenant_id)
        conversation = connection.execute(
            "SELECT 1 FROM conversations "
            "WHERE id = ? AND workspace_id = ?" + tenant_clause,
            parameters,
        ).fetchone()
        if conversation is None:
            raise KeyError("conversation does not belong to the workspace")

    def _ingestion_event(
        self,
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        workspace_id: str,
        proposal_id: str,
        event_type: str,
        actor: str | None,
        payload: Mapping[str, Any],
    ) -> None:
        payload_json = self._json(payload, "audit payload", mapping=True)
        if len(payload_json.encode("utf-8")) > self.MAX_AUDIT_PAYLOAD_BYTES:
            raise ValueError("audit payload is too large")
        connection.execute(
            """
            INSERT INTO knowledge_ingestion_events(
                tenant_id, workspace_id, proposal_id, event_type, actor,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                workspace_id,
                proposal_id,
                event_type,
                actor,
                payload_json,
                self._utc_now(),
            ),
        )

    @classmethod
    def _ingestion_proposal_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "workspace_id": row["workspace_id"],
            "conversation_id": row["conversation_id"],
            "turn_id": row["turn_id"],
            "idempotency_key": row["idempotency_key"],
            "status": row["status"],
            "state_version": int(row["state_version"]),
            "resources": cls._decode_json(row["resources_json"], []),
            "resource_count": int(row["resource_count"]),
            "payload_bytes": int(row["payload_bytes"]),
            "proposed_by": row["proposed_by"],
            "actor": row["actor"],
            "confirmation_recorded": bool(row["confirmation_hash"]),
            "ingested_resources": cls._decode_json(row["result_json"], []),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "decided_at": row["decided_at"],
        }

    @classmethod
    def _ingestion_event_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "workspace_id": row["workspace_id"],
            "proposal_id": row["proposal_id"],
            "event_type": row["event_type"],
            "actor": row["actor"],
            "payload": cls._decode_json(row["payload_json"], {}),
            "created_at": row["created_at"],
        }

    @classmethod
    def _resource_record(
        cls,
        resource_row: sqlite3.Row,
        version_row: sqlite3.Row,
    ) -> dict[str, Any]:
        return {
            "id": resource_row["id"],
            "tenant_id": resource_row["tenant_id"],
            "workspace_id": resource_row["workspace_id"],
            "title": resource_row["title"],
            "resource_type": resource_row["resource_type"],
            "source": {
                "type": resource_row["source_type"],
                "uri": resource_row["source_uri"],
                "id": resource_row["source_id"],
            },
            "status": resource_row["status"],
            "current_version": int(resource_row["current_version"]),
            "metadata": cls._decode_json(resource_row["metadata_json"], {}),
            "created_at": resource_row["created_at"],
            "updated_at": resource_row["updated_at"],
            "confidence": float(resource_row["confidence"]),
            "consolidation_status": resource_row["consolidation_status"],
            "supersedes": resource_row["supersedes"],
            "last_hit": resource_row["last_hit"],
            "version": cls._version_record(version_row),
        }

    @classmethod
    def _version_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "version": int(row["version"]),
            "content_hash": row["content_hash"],
            "searchable_text": row["searchable_text"],
            "mime_type": row["mime_type"],
            "source": {
                "uri": row["source_uri"],
                "id": row["source_id"],
            },
            "structured_data": cls._decode_json(row["structured_data_json"], None),
            "metadata": cls._decode_json(row["metadata_json"], {}),
            "created_by": row["created_by"],
            "change_note": row["change_note"],
            "created_at": row["created_at"],
            "source_episode": row["source_episode"],
            "confidence": float(row["confidence"]),
        }


__all__ = ["KnowledgeProposalConflictError", "KnowledgeRepository"]
