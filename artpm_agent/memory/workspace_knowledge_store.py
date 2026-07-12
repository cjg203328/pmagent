"""Persistent, workspace-scoped knowledge and approved-rule storage."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping, Optional
from uuid import uuid4


class KnowledgeProposalConflictError(RuntimeError):
    """Raised when an ingestion proposal loses a compare-and-swap transition."""


class WorkspaceKnowledgeStore:
    """Store versioned knowledge without coupling ingestion to a parser or LLM."""

    SCHEMA_VERSION = 2
    DEFAULT_WORKSPACE_ID = "local-default"
    MAX_SEARCHABLE_TEXT_CHARS = 2_000_000
    MAX_SEARCH_LIMIT = 100
    MAX_INGESTION_RESOURCES = 20
    MAX_INGESTION_PAYLOAD_BYTES = 2 * 1024 * 1024
    MAX_AUDIT_PAYLOAD_BYTES = 64 * 1024
    RESOURCE_STATUSES = frozenset({"active", "archived"})
    RULE_STATUSES = frozenset({"proposed", "accepted", "rejected", "revoked"})
    CONFIRMER_TYPES = frozenset({"user", "admin"})

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        self._enable_wal()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _enable_wal(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")

    def _migrate(self) -> None:
        with self._connection(write=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version "
                "FROM knowledge_schema_migrations"
            ).fetchone()
            current_version = int(row["version"])
            if current_version > self.SCHEMA_VERSION:
                raise RuntimeError(
                    "Knowledge database schema is newer than this application supports"
                )

            if current_version < 1:
                connection.executescript(
                    """
                    CREATE TABLE knowledge_resources (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                        resource_type TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_uri TEXT,
                        source_id TEXT,
                        status TEXT NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active', 'archived')),
                        current_version INTEGER NOT NULL DEFAULT 0,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(workspace_id, source_type, source_id)
                    );

                    CREATE TABLE knowledge_versions (
                        resource_id TEXT NOT NULL,
                        version INTEGER NOT NULL CHECK(version > 0),
                        content_hash TEXT NOT NULL,
                        searchable_text TEXT NOT NULL,
                        mime_type TEXT,
                        source_uri TEXT,
                        source_id TEXT,
                        structured_data_json TEXT NOT NULL DEFAULT 'null',
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_by TEXT,
                        change_note TEXT,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(resource_id, version),
                        FOREIGN KEY(resource_id) REFERENCES knowledge_resources(id)
                            ON DELETE CASCADE
                    );

                    CREATE TABLE knowledge_rules (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        statement TEXT NOT NULL CHECK(length(trim(statement)) > 0),
                        scope TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'proposed'
                            CHECK(status IN ('proposed', 'accepted', 'rejected', 'revoked')),
                        proposed_by TEXT NOT NULL,
                        source_conversation_id TEXT,
                        source_message_id TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        decision_by TEXT,
                        decision_at TEXT,
                        confirmation_hash TEXT,
                        revoked_by TEXT,
                        revoked_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE INDEX idx_knowledge_resources_workspace_updated
                    ON knowledge_resources(workspace_id, status, updated_at DESC);

                    CREATE INDEX idx_knowledge_versions_hash
                    ON knowledge_versions(resource_id, content_hash);

                    CREATE INDEX idx_knowledge_rules_workspace_status
                    ON knowledge_rules(workspace_id, status, updated_at DESC);
                    """
                )
                connection.execute(
                    "INSERT INTO knowledge_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (1, self._utc_now()),
                )
                current_version = 1

            if current_version < 2:
                connection.executescript(
                    """
                    CREATE TABLE knowledge_ingestion_proposals (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending'
                            CHECK(status IN (
                                'pending', 'confirmed', 'rejected', 'conflict'
                            )),
                        state_version INTEGER NOT NULL DEFAULT 1
                            CHECK(state_version > 0),
                        payload_hash TEXT NOT NULL,
                        resources_json TEXT NOT NULL,
                        resource_count INTEGER NOT NULL,
                        payload_bytes INTEGER NOT NULL,
                        proposed_by TEXT NOT NULL,
                        actor TEXT,
                        confirmation_hash TEXT,
                        result_json TEXT NOT NULL DEFAULT '[]',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        decided_at TEXT,
                        UNIQUE(workspace_id, idempotency_key)
                    );

                    CREATE TABLE knowledge_ingestion_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        workspace_id TEXT NOT NULL,
                        proposal_id TEXT,
                        event_type TEXT NOT NULL,
                        actor TEXT,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(proposal_id)
                            REFERENCES knowledge_ingestion_proposals(id)
                            ON DELETE SET NULL
                    );

                    CREATE INDEX idx_knowledge_ingestion_pending
                    ON knowledge_ingestion_proposals(
                        workspace_id, status, created_at DESC
                    );

                    CREATE INDEX idx_knowledge_ingestion_events
                    ON knowledge_ingestion_events(workspace_id, proposal_id, id);
                    """
                )
                connection.execute(
                    "INSERT INTO knowledge_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (2, self._utc_now()),
                )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _required_text(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _optional_text(value: Any, field: str) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a string or None")
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _json(value: Any, field: str, *, mapping: bool = False) -> str:
        if value is None:
            value = {} if mapping else None
        if mapping and not isinstance(value, Mapping):
            raise ValueError(f"{field} must be a mapping")
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"{field} must be JSON serializable") from error

    @staticmethod
    def _decode_json(value: str, fallback: Any) -> Any:
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback

    @classmethod
    def _normalize_searchable_text(
        cls,
        searchable_text: Any,
        structured_data_json: str,
    ) -> str:
        if searchable_text is None:
            searchable_text = ""
        if not isinstance(searchable_text, str):
            raise ValueError("searchable_text must be a string")
        searchable_text = searchable_text.strip()
        if not searchable_text and structured_data_json != "null":
            searchable_text = structured_data_json
        if not searchable_text:
            raise ValueError("searchable_text or structured_data is required")
        if len(searchable_text) > cls.MAX_SEARCHABLE_TEXT_CHARS:
            raise ValueError(
                "searchable_text exceeds "
                f"{cls.MAX_SEARCHABLE_TEXT_CHARS} characters"
            )
        return searchable_text

    @staticmethod
    def _content_hash(
        searchable_text: str,
        structured_data_json: str,
        mime_type: Optional[str],
    ) -> str:
        payload = "\0".join(
            [searchable_text, structured_data_json, mime_type or ""]
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def ingest_resource(
        self,
        *,
        title: str,
        searchable_text: str = "",
        resource_type: str = "document",
        source_type: str = "manual",
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        source_uri: Optional[str] = None,
        source_id: Optional[str] = None,
        mime_type: Optional[str] = None,
        structured_data: Any = None,
        metadata: Optional[Mapping[str, Any]] = None,
        version_metadata: Optional[Mapping[str, Any]] = None,
        created_by: Optional[str] = None,
        change_note: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create or version a resource, resolving identity by ID or source ID."""
        title = self._required_text(title, "title")
        workspace_id = self._required_text(workspace_id, "workspace_id")
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
        content_hash = self._content_hash(
            searchable_text, structured_json, mime_type
        )
        with self._connection(write=True) as connection:
            return self._ingest_prepared_resource(
                connection,
                title=title,
                searchable_text=searchable_text,
                resource_type=resource_type,
                source_type=source_type,
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

    def _ingest_prepared_resource(
        self,
        connection: sqlite3.Connection,
        *,
        title: str,
        searchable_text: str,
        resource_type: str,
        source_type: str,
        workspace_id: str,
        source_uri: Optional[str],
        source_id: Optional[str],
        mime_type: Optional[str],
        structured_json: str,
        metadata_json: str,
        version_metadata_json: str,
        created_by: Optional[str],
        change_note: Optional[str],
        resource_id: Optional[str],
        content_hash: str,
        now: str,
    ) -> dict[str, Any]:
        resource_row = None
        if resource_id is not None:
            resource_row = connection.execute(
                "SELECT * FROM knowledge_resources WHERE id = ?",
                (resource_id,),
            ).fetchone()
            if (
                resource_row is not None
                and resource_row["workspace_id"] != workspace_id
            ):
                raise ValueError("resource_id belongs to a different workspace")
        elif source_id is not None:
            resource_row = connection.execute(
                """
                SELECT * FROM knowledge_resources
                WHERE workspace_id = ? AND source_type = ? AND source_id = ?
                """,
                (workspace_id, source_type, source_id),
            ).fetchone()

        if resource_row is None:
            resource_id = resource_id or uuid4().hex
            connection.execute(
                """
                INSERT INTO knowledge_resources(
                    id, workspace_id, title, resource_type, source_type,
                    source_uri, source_id, status, current_version,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)
                """,
                (
                    resource_id,
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
            metadata_json = self._json(
                current_metadata, "metadata", mapping=True
            )

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
                    resource_id, version, content_hash, searchable_text,
                    mime_type, source_uri, source_id, structured_data_json,
                    metadata_json, created_by, change_note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resource_id,
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
            WHERE id = ?
            """,
            (
                title,
                source_uri,
                current_version,
                metadata_json,
                now,
                resource_id,
            ),
        )
        resource_row = connection.execute(
            "SELECT * FROM knowledge_resources WHERE id = ?",
            (resource_id,),
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
        return result

    def get_resource(
        self,
        resource_id: str,
        *,
        version: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        resource_id = self._required_text(resource_id, "resource_id")
        if version is not None and (
            isinstance(version, bool) or not isinstance(version, int) or version <= 0
        ):
            raise ValueError("version must be a positive integer")
        with self._connection() as connection:
            resource_row = connection.execute(
                "SELECT * FROM knowledge_resources WHERE id = ?",
                (resource_id,),
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

    def list_versions(self, resource_id: str) -> list[dict[str, Any]]:
        resource_id = self._required_text(resource_id, "resource_id")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_versions
                WHERE resource_id = ? ORDER BY version DESC
                """,
                (resource_id,),
            ).fetchall()
        return [self._version_record(row) for row in rows]

    def list_resources(
        self,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        include_archived: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        workspace_id = self._required_text(workspace_id, "workspace_id")
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
                WHERE r.workspace_id = ? {status_clause}
                ORDER BY r.updated_at DESC, r.id DESC
                LIMIT ?
                """,
                (workspace_id, limit),
            ).fetchall()
        return [self._joined_resource_record(row) for row in rows]

    def archive_resource(self, resource_id: str) -> bool:
        resource_id = self._required_text(resource_id, "resource_id")
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_resources
                SET status = 'archived', updated_at = ?
                WHERE id = ? AND status != 'archived'
                """,
                (self._utc_now(), resource_id),
            )
        return cursor.rowcount > 0

    def propose_ingestion(
        self,
        conversation_id: str,
        turn_id: str,
        resources: Iterable[Mapping[str, Any]],
        idempotency_key: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        proposed_by: str = "agent",
    ) -> dict[str, Any]:
        """Persist an inert ingestion proposal without creating knowledge."""
        conversation_id = self._required_text(
            conversation_id, "conversation_id"
        )
        turn_id = self._required_text(turn_id, "turn_id")
        idempotency_key = self._required_text(
            idempotency_key, "idempotency_key"
        )
        if len(idempotency_key) > 200:
            raise ValueError("idempotency_key cannot exceed 200 characters")
        workspace_id = self._required_text(workspace_id, "workspace_id")
        proposed_by = self._required_text(proposed_by, "proposed_by")
        normalized_resources = self._normalize_ingestion_resources(resources)
        request_json = self._json(
            normalized_resources, "resources"
        )
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
                connection, conversation_id, workspace_id
            )
            existing = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_proposals
                WHERE workspace_id = ? AND idempotency_key = ?
                """,
                (workspace_id, idempotency_key),
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
                        WHERE workspace_id = ? AND source_type = ?
                          AND source_id = ?
                        """,
                        (
                            workspace_id,
                            resource["source_type"],
                            resource["source_id"],
                        ),
                    ).fetchone()
                if (
                    existing_resource is not None
                    and existing_resource["resource_type"]
                    != resource["resource_type"]
                ):
                    raise ValueError(
                        "resource_type does not match the existing source"
                    )
                versioned["expected_resource_id"] = (
                    existing_resource["id"] if existing_resource else None
                )
                versioned["base_version"] = (
                    int(existing_resource["current_version"])
                    if existing_resource
                    else 0
                )
                versioned_resources.append(versioned)

            resources_json = self._json(
                versioned_resources, "versioned_resources"
            )
            connection.execute(
                """
                INSERT INTO knowledge_ingestion_proposals(
                    id, workspace_id, conversation_id, turn_id,
                    idempotency_key, status, state_version, payload_hash,
                    resources_json, resource_count, payload_bytes,
                    proposed_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal_id,
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
                "SELECT * FROM knowledge_ingestion_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return self._ingestion_proposal_record(row)

    def confirm_ingestion(
        self,
        proposal_id: str,
        *,
        actor: str,
        confirmation_token: str,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        expected_state_version: Optional[int] = None,
    ) -> dict[str, Any]:
        """CAS-confirm a proposal and atomically ingest all candidate resources."""
        proposal_id = self._required_text(proposal_id, "proposal_id")
        actor = self._required_text(actor, "actor")
        confirmation_token = self._required_text(
            confirmation_token, "confirmation_token"
        )
        workspace_id = self._required_text(workspace_id, "workspace_id")
        expected_state_version = self._optional_state_version(
            expected_state_version
        )
        confirmation_hash = sha256(
            confirmation_token.encode("utf-8")
        ).hexdigest()
        conflict_reason = None

        with self._connection(write=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_proposals
                WHERE id = ? AND workspace_id = ?
                """,
                (proposal_id, workspace_id),
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
                        WHERE id = ? AND workspace_id = ?
                        """,
                        (expected_resource_id, workspace_id),
                    ).fetchone()
                elif resource.get("source_id"):
                    current_resource = connection.execute(
                        """
                        SELECT * FROM knowledge_resources
                        WHERE workspace_id = ? AND source_type = ?
                          AND source_id = ?
                        """,
                        (
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
                if (
                    current_version != int(resource["base_version"])
                    or (
                        expected_resource_id
                        and (
                            current_resource is None
                            or current_resource["id"] != expected_resource_id
                        )
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
                    ingested.append({
                        "id": ingested_resource["id"],
                        "title": ingested_resource["title"],
                        "current_version": ingested_resource["current_version"],
                        "version_created": ingested_resource["version_created"],
                    })

                result_json = self._json(
                    ingested, "ingestion_result"
                )
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
                    workspace_id=workspace_id,
                    proposal_id=proposal_id,
                    event_type="ingestion.confirmed",
                    actor=actor,
                    payload={"resources": ingested},
                )

        if conflict_reason is not None:
            raise KnowledgeProposalConflictError(conflict_reason)
        result = self.get_ingestion_proposal(
            proposal_id, workspace_id=workspace_id
        )
        result["decision_created"] = True
        return result

    def reject_ingestion(
        self,
        proposal_id: str,
        *,
        actor: str,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        expected_state_version: Optional[int] = None,
    ) -> dict[str, Any]:
        proposal_id = self._required_text(proposal_id, "proposal_id")
        actor = self._required_text(actor, "actor")
        workspace_id = self._required_text(workspace_id, "workspace_id")
        expected_state_version = self._optional_state_version(
            expected_state_version
        )
        now = self._utc_now()
        with self._connection(write=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_proposals
                WHERE id = ? AND workspace_id = ?
                """,
                (proposal_id, workspace_id),
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
                workspace_id=workspace_id,
                proposal_id=proposal_id,
                event_type="ingestion.rejected",
                actor=actor,
                payload={},
            )
            row = connection.execute(
                "SELECT * FROM knowledge_ingestion_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return self._ingestion_proposal_record(row)

    def get_ingestion_proposal(
        self,
        proposal_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> Optional[dict[str, Any]]:
        proposal_id = self._required_text(proposal_id, "proposal_id")
        workspace_id = self._required_text(workspace_id, "workspace_id")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM knowledge_ingestion_proposals
                WHERE id = ? AND workspace_id = ?
                """,
                (proposal_id, workspace_id),
            ).fetchone()
        return self._ingestion_proposal_record(row) if row is not None else None

    def list_ingestion_proposals(
        self,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        status: Optional[str] = None,
        conversation_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        workspace_id = self._required_text(workspace_id, "workspace_id")
        allowed_statuses = {"pending", "confirmed", "rejected", "conflict"}
        if status is not None and status not in allowed_statuses:
            raise ValueError(f"Unsupported ingestion proposal status: {status}")
        conversation_id = self._optional_text(
            conversation_id, "conversation_id"
        )
        limit = self._validate_limit(limit)
        clauses = ["workspace_id = ?"]
        parameters: list[Any] = [workspace_id]
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
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._ingestion_proposal_record(row) for row in rows]

    def list_ingestion_events(
        self,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        proposal_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        workspace_id = self._required_text(workspace_id, "workspace_id")
        proposal_id = self._optional_text(proposal_id, "proposal_id")
        limit = self._validate_limit(limit)
        proposal_clause = "AND proposal_id = ?" if proposal_id else ""
        parameters: list[Any] = [workspace_id]
        if proposal_id:
            parameters.append(proposal_id)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM knowledge_ingestion_events
                WHERE workspace_id = ? {proposal_clause}
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
            raise ValueError("resources must be a list of mappings")
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
                raise ValueError(f"resources[{index}] must be a mapping")
            unknown_fields = set(resource) - allowed_fields
            if unknown_fields:
                raise ValueError(
                    f"resources[{index}] has unsupported fields: "
                    f"{', '.join(sorted(unknown_fields))}"
                )
            mime = resource.get("mime")
            mime_type = resource.get("mime_type")
            if mime is not None and mime_type is not None and mime != mime_type:
                raise ValueError(
                    f"resources[{index}] mime and mime_type disagree"
                )
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
            normalized.append({
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
            })
        return normalized

    @staticmethod
    def _optional_state_version(value: Optional[int]) -> Optional[int]:
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
            for row in connection.execute(
                "PRAGMA table_info(conversations)"
            ).fetchall()
        }
        if "workspace_id" not in columns:
            return
        conversation = connection.execute(
            """
            SELECT 1 FROM conversations WHERE id = ? AND workspace_id = ?
            """,
            (conversation_id, workspace_id),
        ).fetchone()
        if conversation is None:
            raise KeyError("conversation does not belong to the workspace")

    def _ingestion_event(
        self,
        connection: sqlite3.Connection,
        *,
        workspace_id: str,
        proposal_id: str,
        event_type: str,
        actor: Optional[str],
        payload: Mapping[str, Any],
    ) -> None:
        payload_json = self._json(payload, "audit payload", mapping=True)
        if len(payload_json.encode("utf-8")) > self.MAX_AUDIT_PAYLOAD_BYTES:
            raise ValueError("audit payload is too large")
        connection.execute(
            """
            INSERT INTO knowledge_ingestion_events(
                workspace_id, proposal_id, event_type, actor,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                proposal_id,
                event_type,
                actor,
                payload_json,
                self._utc_now(),
            ),
        )

    def propose_rule(
        self,
        statement: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        scope: str = "workspace",
        proposed_by: str = "agent",
        source_conversation_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        rule_id: Optional[str] = None,
    ) -> dict[str, Any]:
        statement = self._required_text(statement, "statement")
        workspace_id = self._required_text(workspace_id, "workspace_id")
        scope = self._required_text(scope, "scope")
        proposed_by = self._required_text(proposed_by, "proposed_by")
        source_conversation_id = self._optional_text(
            source_conversation_id, "source_conversation_id"
        )
        source_message_id = self._optional_text(
            source_message_id, "source_message_id"
        )
        rule_id = (
            self._required_text(rule_id, "rule_id") if rule_id else uuid4().hex
        )
        metadata_json = self._json(metadata, "metadata", mapping=True)
        now = self._utc_now()
        with self._connection(write=True) as connection:
            existing = connection.execute(
                "SELECT * FROM knowledge_rules WHERE id = ?",
                (rule_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["workspace_id"] == workspace_id
                    and existing["statement"] == statement
                    and existing["source_conversation_id"]
                    == source_conversation_id
                ):
                    return self._rule_record(existing)
                raise ValueError("rule_id is already used by another rule")
            connection.execute(
                """
                INSERT INTO knowledge_rules(
                    id, workspace_id, statement, scope, status, proposed_by,
                    source_conversation_id, source_message_id, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    workspace_id,
                    statement,
                    scope,
                    proposed_by,
                    source_conversation_id,
                    source_message_id,
                    metadata_json,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        return self._rule_record(row)

    def confirm_rule(
        self,
        rule_id: str,
        *,
        confirmed_by: str,
        confirmation_token: str,
        confirmer_type: str = "user",
    ) -> dict[str, Any]:
        rule_id = self._required_text(rule_id, "rule_id")
        confirmed_by = self._required_text(confirmed_by, "confirmed_by")
        confirmation_token = self._required_text(
            confirmation_token, "confirmation_token"
        )
        if confirmer_type not in self.CONFIRMER_TYPES:
            raise ValueError("confirmer_type must be user or admin")
        confirmation_hash = sha256(
            confirmation_token.encode("utf-8")
        ).hexdigest()
        now = self._utc_now()
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_rules WHERE id = ?", (rule_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown rule: {rule_id}")
            if row["status"] == "accepted":
                result = self._rule_record(row)
                result["decision_created"] = False
                return result
            if row["status"] != "proposed":
                raise ValueError(f"Rule cannot be accepted from {row['status']}")
            connection.execute(
                """
                UPDATE knowledge_rules
                SET status = 'accepted', decision_by = ?, decision_at = ?,
                    confirmation_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (confirmed_by, now, confirmation_hash, now, rule_id),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        result = self._rule_record(row)
        result["decision_created"] = True
        return result

    def reject_rule(
        self,
        rule_id: str,
        *,
        rejected_by: str,
        confirmer_type: str = "user",
    ) -> dict[str, Any]:
        return self._decide_rule(
            rule_id,
            status="rejected",
            decided_by=rejected_by,
            confirmer_type=confirmer_type,
        )

    def revoke_rule(
        self,
        rule_id: str,
        *,
        revoked_by: str,
        confirmation_token: str,
        confirmer_type: str = "user",
    ) -> dict[str, Any]:
        rule_id = self._required_text(rule_id, "rule_id")
        revoked_by = self._required_text(revoked_by, "revoked_by")
        self._required_text(confirmation_token, "confirmation_token")
        if confirmer_type not in self.CONFIRMER_TYPES:
            raise ValueError("confirmer_type must be user or admin")
        now = self._utc_now()
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_rules WHERE id = ?", (rule_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown rule: {rule_id}")
            if row["status"] != "accepted":
                raise ValueError(f"Rule cannot be revoked from {row['status']}")
            connection.execute(
                """
                UPDATE knowledge_rules
                SET status = 'revoked', revoked_by = ?, revoked_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (revoked_by, now, now, rule_id),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        return self._rule_record(row)

    def _decide_rule(
        self,
        rule_id: str,
        *,
        status: str,
        decided_by: str,
        confirmer_type: str,
    ) -> dict[str, Any]:
        rule_id = self._required_text(rule_id, "rule_id")
        decided_by = self._required_text(decided_by, "decided_by")
        if confirmer_type not in self.CONFIRMER_TYPES:
            raise ValueError("confirmer_type must be user or admin")
        now = self._utc_now()
        with self._connection(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_rules WHERE id = ?", (rule_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown rule: {rule_id}")
            if row["status"] != "proposed":
                raise ValueError(f"Rule cannot be {status} from {row['status']}")
            connection.execute(
                """
                UPDATE knowledge_rules
                SET status = ?, decision_by = ?, decision_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, decided_by, now, now, rule_id),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        return self._rule_record(row)

    def list_rules(
        self,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        status: Optional[str] = None,
        scope: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        workspace_id = self._required_text(workspace_id, "workspace_id")
        if status is not None and status not in self.RULE_STATUSES:
            raise ValueError(f"Unsupported rule status: {status}")
        scope = self._optional_text(scope, "scope")
        limit = self._validate_limit(limit)
        clauses = ["workspace_id = ?"]
        parameters: list[Any] = [workspace_id]
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        if scope is not None:
            clauses.append("scope = ?")
            parameters.append(scope)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM knowledge_rules
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC, id DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._rule_record(row) for row in rows]

    def get_active_rules(
        self,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        scope: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.list_rules(
            workspace_id=workspace_id,
            status="accepted",
            scope=scope,
            limit=limit,
        )

    def search(
        self,
        query: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        limit: int = 10,
        resource_types: Optional[Iterable[str]] = None,
        source_types: Optional[Iterable[str]] = None,
        include_rules: bool = True,
        max_text_chars: int = 4000,
    ) -> list[dict[str, Any]]:
        """Search current active resource versions and accepted rules."""
        query = self._required_text(query, "query")
        workspace_id = self._required_text(workspace_id, "workspace_id")
        limit = self._validate_limit(limit)
        if (
            isinstance(max_text_chars, bool)
            or not isinstance(max_text_chars, int)
            or not 100 <= max_text_chars <= 20_000
        ):
            raise ValueError("max_text_chars must be between 100 and 20000")
        resource_type_filter = self._normalized_filter(
            resource_types, "resource_types"
        )
        source_type_filter = self._normalized_filter(source_types, "source_types")

        with self._connection() as connection:
            resource_rows = connection.execute(
                """
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
                WHERE r.workspace_id = ? AND r.status = 'active'
                ORDER BY r.updated_at DESC
                LIMIT 2000
                """,
                (workspace_id,),
            ).fetchall()
            rule_rows = (
                connection.execute(
                    """
                    SELECT * FROM knowledge_rules
                    WHERE workspace_id = ? AND status = 'accepted'
                    ORDER BY updated_at DESC LIMIT 1000
                    """,
                    (workspace_id,),
                ).fetchall()
                if include_rules
                else []
            )

        results = []
        for row in resource_rows:
            if (
                resource_type_filter
                and row["resource_type"] not in resource_type_filter
            ):
                continue
            if source_type_filter and row["source_type"] not in source_type_filter:
                continue
            haystack = "\n".join(
                [
                    row["title"],
                    row["searchable_text"],
                    row["source_uri"] or "",
                    row["structured_data_json"],
                ]
            )
            score = self._literal_score(query, haystack)
            if score <= 0:
                continue
            record = self._joined_resource_record(row)
            record.update({
                "record_type": "resource",
                "score": score,
                "text": self._excerpt(
                    row["searchable_text"], query, max_text_chars
                ),
            })
            results.append(record)

        if include_rules and (not resource_type_filter or "rule" in resource_type_filter):
            for row in rule_rows:
                score = self._literal_score(query, row["statement"])
                if score <= 0:
                    continue
                record = self._rule_record(row)
                record.update({
                    "record_type": "rule",
                    "score": score,
                    "text": row["statement"],
                    "title": "已采纳规则",
                })
                results.append(record)

        results.sort(
            key=lambda item: (item["score"], item["updated_at"], item["id"]),
            reverse=True,
        )
        return results[:limit]

    @classmethod
    def _validate_limit(cls, value: int) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= cls.MAX_SEARCH_LIMIT
        ):
            raise ValueError(
                f"limit must be between 1 and {cls.MAX_SEARCH_LIMIT}"
            )
        return value

    @classmethod
    def _normalized_filter(
        cls,
        values: Optional[Iterable[str]],
        field: str,
    ) -> frozenset[str]:
        if values is None:
            return frozenset()
        if isinstance(values, str):
            values = [values]
        return frozenset(cls._required_text(value, field) for value in values)

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
    def _ingestion_proposal_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
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
            "version": cls._version_record(version_row),
        }

    @classmethod
    def _joined_resource_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
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
            "version": {
                "version": int(row["v_version"]),
                "content_hash": row["content_hash"],
                "searchable_text": row["searchable_text"],
                "mime_type": row["mime_type"],
                "source": {
                    "uri": row["version_source_uri"],
                    "id": row["version_source_id"],
                },
                "structured_data": cls._decode_json(
                    row["structured_data_json"], None
                ),
                "metadata": cls._decode_json(
                    row["version_metadata_json"], {}
                ),
                "created_by": row["created_by"],
                "change_note": row["change_note"],
                "created_at": row["version_created_at"],
            },
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
            "structured_data": cls._decode_json(
                row["structured_data_json"], None
            ),
            "metadata": cls._decode_json(row["metadata_json"], {}),
            "created_by": row["created_by"],
            "change_note": row["change_note"],
            "created_at": row["created_at"],
        }

    @classmethod
    def _rule_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "statement": row["statement"],
            "scope": row["scope"],
            "status": row["status"],
            "proposed_by": row["proposed_by"],
            "source_conversation_id": row["source_conversation_id"],
            "source_message_id": row["source_message_id"],
            "metadata": cls._decode_json(row["metadata_json"], {}),
            "decision_by": row["decision_by"],
            "decision_at": row["decision_at"],
            "confirmation_recorded": bool(row["confirmation_hash"]),
            "revoked_by": row["revoked_by"],
            "revoked_at": row["revoked_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
