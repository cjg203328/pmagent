"""Persistent, workspace-scoped knowledge and approved-rule storage."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
from pathlib import Path
import sqlite3
from threading import Lock, RLock
from typing import Any, Iterable, Iterator, Mapping, Optional, cast
from uuid import uuid4

from .embeddings import DeterministicEmbeddingProvider, EmbeddingProvider
from .vector_store import VectorStore
from artpm_agent.tenancy import (
    TenantContextManager,
    WorkspaceAccessDenied,
)
from .knowledge import (
    SCHEMA_VERSION as _SCHEMA_VERSION,
    content_hash as _content_hash_contract,
    deserialize_json as _decode_json_contract,
    normalize_filter as _normalized_filter_contract,
    normalize_searchable_text as _normalize_searchable_text_contract,
    optional_text as _optional_text_contract,
    required_text as _required_text_contract,
    serialize_json as _json_contract,
    validate_limit as _validate_limit_contract,
)


_VECTOR_LOCKS_GUARD = Lock()
_VECTOR_SYNC_LOCKS: dict[str, RLock] = {}


def _vector_sync_lock(path: Path) -> RLock:
    key = str(path.resolve())
    with _VECTOR_LOCKS_GUARD:
        return _VECTOR_SYNC_LOCKS.setdefault(key, RLock())


class KnowledgeProposalConflictError(RuntimeError):
    """Raised when an ingestion proposal loses a compare-and-swap transition."""


class WorkspaceKnowledgeStore:
    """Store versioned knowledge without coupling ingestion to a parser or LLM."""

    SCHEMA_VERSION = _SCHEMA_VERSION
    DEFAULT_TENANT_ID = "local"
    DEFAULT_WORKSPACE_ID = "local-default"
    MAX_SEARCHABLE_TEXT_CHARS = 2_000_000
    MAX_QUERY_CHARS = 4_000
    MAX_SEARCH_LIMIT = 100
    MAX_INGESTION_RESOURCES = 20
    MAX_INGESTION_PAYLOAD_BYTES = 2 * 1024 * 1024
    MAX_AUDIT_PAYLOAD_BYTES = 64 * 1024
    VECTOR_CHUNK_CHARS = 1200
    VECTOR_CHUNK_OVERLAP = 200
    MAX_VECTOR_CHUNKS_PER_RESOURCE = 64
    VECTOR_MIN_SCORE = 0.08
    RESOURCE_STATUSES = frozenset({"active", "archived"})
    RULE_STATUSES = frozenset({"proposed", "accepted", "rejected", "revoked"})
    CONFIRMER_TYPES = frozenset({"user", "admin"})

    def __init__(
        self,
        db_path: str | Path,
        *,
        vector_store_path: str | Path | None = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        enable_vector_search: bool = True,
    ):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        self._enable_wal()
        self.embedding_provider = (
            embedding_provider or DeterministicEmbeddingProvider()
        )
        default_vector_path = (
            self.db_path.parent
            / "vector_store"
            / f"{self.db_path.stem}_workspace_knowledge"
        )
        self.vector_store_path = Path(
            vector_store_path or default_vector_path
        ).expanduser().resolve()
        self.vector_store = (
            VectorStore(
                self.vector_store_path,
                dimension=self.embedding_provider.dimension,
                embedding_fingerprint=self.embedding_provider.fingerprint,
            )
            if enable_vector_search
            else None
        )
        self._vector_lock = _vector_sync_lock(self.vector_store_path)
        self.last_search_mode = "not-searched"
        self.sync_pending = bool(
            self.vector_store is not None and self.vector_store.needs_rebuild
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            connection = self._connect()
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except Exception:
            if connection is not None and connection.in_transaction:
                connection.rollback()
            raise
        finally:
            if connection is not None:
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
                        tenant_id TEXT NOT NULL DEFAULT 'local',
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
                        UNIQUE(tenant_id, workspace_id, source_type, source_id)
                    );

                    CREATE TABLE knowledge_versions (
                        resource_id TEXT NOT NULL,
                        tenant_id TEXT NOT NULL DEFAULT 'local',
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
                        tenant_id TEXT NOT NULL DEFAULT 'local',
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
                        tenant_id TEXT NOT NULL DEFAULT 'local',
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
                        UNIQUE(tenant_id, workspace_id, idempotency_key)
                    );

                    CREATE TABLE knowledge_ingestion_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tenant_id TEXT NOT NULL DEFAULT 'local',
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

            if current_version < 3:
                # Phase 2 — 知识炼化字段：置信度/炼化状态/版本覆盖/来源episode/命中时间
                self._ensure_knowledge_consolidation_columns(connection)
                connection.execute(
                    "INSERT INTO knowledge_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (3, self._utc_now()),
                )

            if current_version < 4:
                self._ensure_tenant_scope_columns(connection)
                connection.execute(
                    "INSERT INTO knowledge_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (4, self._utc_now()),
                )

            if current_version < 5:
                self._migrate_tenant_scope_constraints(connection)
                connection.execute(
                    "INSERT INTO knowledge_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (5, self._utc_now()),
                )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _ensure_knowledge_consolidation_columns(
        connection: sqlite3.Connection,
    ) -> None:
        """Add Phase 2 consolidation columns if missing (idempotent)."""
        resource_cols = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(knowledge_resources)"
            ).fetchall()
        }
        resource_alters = [
            "ALTER TABLE knowledge_resources "
            "ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0",
            "ALTER TABLE knowledge_resources ADD COLUMN consolidation_status "
            "TEXT NOT NULL DEFAULT 'active' "
            "CHECK(consolidation_status IN ('active', 'superseded', 'conflict'))",
            "ALTER TABLE knowledge_resources ADD COLUMN supersedes TEXT",
            "ALTER TABLE knowledge_resources ADD COLUMN last_hit TEXT",
        ]
        for stmt in resource_alters:
            col = stmt.split("ADD COLUMN ", 1)[1].split(" ", 1)[0]
            if col not in resource_cols:
                connection.execute(stmt)

        version_cols = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(knowledge_versions)"
            ).fetchall()
        }
        version_alters = [
            "ALTER TABLE knowledge_versions ADD COLUMN source_episode TEXT",
            "ALTER TABLE knowledge_versions ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0",
        ]
        for stmt in version_alters:
            col = stmt.split("ADD COLUMN ", 1)[1].split(" ", 1)[0]
            if col not in version_cols:
                connection.execute(stmt)

        connection.execute(
            "CREATE INDEX IF NOT EXISTS "
            "idx_knowledge_resources_consolidation "
            "ON knowledge_resources(workspace_id, consolidation_status)"
        )

    @staticmethod
    def _ensure_tenant_scope_columns(connection: sqlite3.Connection) -> None:
        """Add tenant scope columns to databases created before scope v4."""

        tables = (
            "knowledge_resources",
            "knowledge_versions",
            "knowledge_rules",
            "knowledge_ingestion_proposals",
            "knowledge_ingestion_events",
        )
        for table in tables:
            columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if "tenant_id" not in columns:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'local'"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_resources_tenant_scope "
            "ON knowledge_resources(tenant_id, workspace_id, status, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_rules_tenant_scope "
            "ON knowledge_rules(tenant_id, workspace_id, status, updated_at DESC)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_knowledge_ingestion_tenant_scope "
            "ON knowledge_ingestion_proposals(tenant_id, workspace_id, status, created_at DESC)"
        )

    @staticmethod
    def _has_unique_index(
        connection: sqlite3.Connection,
        table: str,
        columns: tuple[str, ...],
    ) -> bool:
        """Return whether a table has a unique index with this exact shape."""

        for index in connection.execute(
            f"PRAGMA index_list({table})"
        ).fetchall():
            if not int(index["unique"]):
                continue
            index_name = str(index["name"]).replace('"', '""')
            index_columns = tuple(
                str(row["name"])
                for row in connection.execute(
                    f'PRAGMA index_info("{index_name}")'
                ).fetchall()
            )
            if index_columns == columns:
                return True
        return False

    @staticmethod
    def _drop_indexes(
        connection: sqlite3.Connection,
        names: Iterable[str],
    ) -> None:
        for name in names:
            connection.execute(f'DROP INDEX IF EXISTS "{name}"')

    @classmethod
    def _migrate_tenant_scope_constraints(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        """Replace pre-v4 workspace-only unique constraints.

        v4 added tenant columns to existing databases, but SQLite keeps inline
        UNIQUE constraints in the original table definition. Rebuilding only
        the affected tables removes those stale constraints while preserving
        all rows, foreign keys, and event IDs.
        """

        resources_need_rebuild = (
            cls._has_unique_index(
                connection,
                "knowledge_resources",
                ("workspace_id", "source_type", "source_id"),
            )
            or not cls._has_unique_index(
                connection,
                "knowledge_resources",
                ("tenant_id", "workspace_id", "source_type", "source_id"),
            )
        )
        proposals_need_rebuild = (
            cls._has_unique_index(
                connection,
                "knowledge_ingestion_proposals",
                ("workspace_id", "idempotency_key"),
            )
            or not cls._has_unique_index(
                connection,
                "knowledge_ingestion_proposals",
                ("tenant_id", "workspace_id", "idempotency_key"),
            )
        )

        if resources_need_rebuild:
            cls._rebuild_resource_tables(connection)
        if proposals_need_rebuild:
            cls._rebuild_ingestion_tables(connection)

    @classmethod
    def _rebuild_resource_tables(cls, connection: sqlite3.Connection) -> None:
        cls._drop_indexes(
            connection,
            (
                "idx_knowledge_resources_workspace_updated",
                "idx_knowledge_resources_consolidation",
                "idx_knowledge_resources_tenant_scope",
                "idx_knowledge_versions_hash",
            ),
        )
        connection.execute(
            "ALTER TABLE knowledge_versions RENAME TO knowledge_versions_scope_legacy"
        )
        connection.execute(
            "ALTER TABLE knowledge_resources RENAME TO knowledge_resources_scope_legacy"
        )
        connection.executescript(
            """
            CREATE TABLE knowledge_resources_scope_new (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'local',
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
                confidence REAL NOT NULL DEFAULT 1.0,
                consolidation_status TEXT NOT NULL DEFAULT 'active'
                    CHECK(consolidation_status IN ('active', 'superseded', 'conflict')),
                supersedes TEXT,
                last_hit TEXT,
                UNIQUE(tenant_id, workspace_id, source_type, source_id)
            );

            CREATE TABLE knowledge_versions_scope_new (
                resource_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'local',
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
                source_episode TEXT,
                confidence REAL NOT NULL DEFAULT 1.0,
                PRIMARY KEY(resource_id, version),
                FOREIGN KEY(resource_id) REFERENCES knowledge_resources_scope_new(id)
                    ON DELETE CASCADE
            );
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_resources_scope_new(
                id, tenant_id, workspace_id, title, resource_type, source_type,
                source_uri, source_id, status, current_version, metadata_json,
                created_at, updated_at, confidence, consolidation_status,
                supersedes, last_hit
            )
            SELECT id, tenant_id, workspace_id, title, resource_type, source_type,
                source_uri, source_id, status, current_version, metadata_json,
                created_at, updated_at, confidence, consolidation_status,
                supersedes, last_hit
            FROM knowledge_resources_scope_legacy
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_versions_scope_new(
                resource_id, tenant_id, version, content_hash, searchable_text,
                mime_type, source_uri, source_id, structured_data_json,
                metadata_json, created_by, change_note, created_at,
                source_episode, confidence
            )
            SELECT resource_id, tenant_id, version, content_hash, searchable_text,
                mime_type, source_uri, source_id, structured_data_json,
                metadata_json, created_by, change_note, created_at,
                source_episode, confidence
            FROM knowledge_versions_scope_legacy
            """
        )
        connection.execute("DROP TABLE knowledge_versions_scope_legacy")
        connection.execute("DROP TABLE knowledge_resources_scope_legacy")
        connection.execute(
            "ALTER TABLE knowledge_resources_scope_new RENAME TO knowledge_resources"
        )
        connection.execute(
            "ALTER TABLE knowledge_versions_scope_new RENAME TO knowledge_versions"
        )
        connection.executescript(
            """
            CREATE INDEX idx_knowledge_resources_workspace_updated
            ON knowledge_resources(workspace_id, status, updated_at DESC);
            CREATE INDEX idx_knowledge_resources_tenant_scope
            ON knowledge_resources(tenant_id, workspace_id, status, updated_at DESC);
            CREATE INDEX idx_knowledge_resources_consolidation
            ON knowledge_resources(workspace_id, consolidation_status);
            CREATE INDEX idx_knowledge_versions_hash
            ON knowledge_versions(resource_id, content_hash);
            """
        )

    @classmethod
    def _rebuild_ingestion_tables(cls, connection: sqlite3.Connection) -> None:
        cls._drop_indexes(
            connection,
            (
                "idx_knowledge_ingestion_pending",
                "idx_knowledge_ingestion_events",
                "idx_knowledge_ingestion_tenant_scope",
            ),
        )
        connection.execute(
            "ALTER TABLE knowledge_ingestion_events "
            "RENAME TO knowledge_ingestion_events_scope_legacy"
        )
        connection.execute(
            "ALTER TABLE knowledge_ingestion_proposals "
            "RENAME TO knowledge_ingestion_proposals_scope_legacy"
        )
        connection.executescript(
            """
            CREATE TABLE knowledge_ingestion_proposals_scope_new (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'local',
                workspace_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'confirmed', 'rejected', 'conflict')),
                state_version INTEGER NOT NULL DEFAULT 1 CHECK(state_version > 0),
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
                UNIQUE(tenant_id, workspace_id, idempotency_key)
            );

            CREATE TABLE knowledge_ingestion_events_scope_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'local',
                workspace_id TEXT NOT NULL,
                proposal_id TEXT,
                event_type TEXT NOT NULL,
                actor TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(proposal_id)
                    REFERENCES knowledge_ingestion_proposals_scope_new(id)
                    ON DELETE SET NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_ingestion_proposals_scope_new(
                id, tenant_id, workspace_id, conversation_id, turn_id,
                idempotency_key, status, state_version, payload_hash,
                resources_json, resource_count, payload_bytes, proposed_by, actor,
                confirmation_hash, result_json, created_at, updated_at, decided_at
            )
            SELECT id, tenant_id, workspace_id, conversation_id, turn_id,
                idempotency_key, status, state_version, payload_hash,
                resources_json, resource_count, payload_bytes, proposed_by, actor,
                confirmation_hash, result_json, created_at, updated_at, decided_at
            FROM knowledge_ingestion_proposals_scope_legacy
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_ingestion_events_scope_new(
                id, tenant_id, workspace_id, proposal_id, event_type, actor,
                payload_json, created_at
            )
            SELECT id, tenant_id, workspace_id, proposal_id, event_type, actor,
                payload_json, created_at
            FROM knowledge_ingestion_events_scope_legacy
            """
        )
        connection.execute("DROP TABLE knowledge_ingestion_events_scope_legacy")
        connection.execute("DROP TABLE knowledge_ingestion_proposals_scope_legacy")
        connection.execute(
            "ALTER TABLE knowledge_ingestion_proposals_scope_new "
            "RENAME TO knowledge_ingestion_proposals"
        )
        connection.execute(
            "ALTER TABLE knowledge_ingestion_events_scope_new "
            "RENAME TO knowledge_ingestion_events"
        )
        connection.executescript(
            """
            CREATE INDEX idx_knowledge_ingestion_pending
            ON knowledge_ingestion_proposals(workspace_id, status, created_at DESC);
            CREATE INDEX idx_knowledge_ingestion_tenant_scope
            ON knowledge_ingestion_proposals(tenant_id, workspace_id, status, created_at DESC);
            CREATE INDEX idx_knowledge_ingestion_events
            ON knowledge_ingestion_events(workspace_id, proposal_id, id);
            """
        )

    @staticmethod
    def _required_text(value: Any, field: str) -> str:
        return _required_text_contract(value, field)

    def _resolve_scope(
        self,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
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
        workspace_id: Optional[str],
        tenant_id: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
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

        if claimed and self.vector_store is not None:
            self.sync_pending = True
            try:
                self._sync_vector_index()
            except Exception as error:
                self.vector_store.needs_rebuild = True
                self.vector_store.last_error = (
                    f"knowledge vector sync pending: {error}"
                )
        return claimed

    @staticmethod
    def _optional_text(value: Any, field: str) -> Optional[str]:
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
        mime_type: Optional[str],
    ) -> str:
        return _content_hash_contract(searchable_text, structured_data_json, mime_type)

    def ingest_resource(
        self,
        *,
        title: str,
        searchable_text: str = "",
        resource_type: str = "document",
        source_type: str = "manual",
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
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
        content_hash = self._content_hash(
            searchable_text, structured_json, mime_type
        )
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
        self._sync_vector_index_best_effort(
            resource_ids=(result["id"],),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
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
        return result

    def get_resource(
        self,
        resource_id: str,
        *,
        version: Optional[int] = None,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
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
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
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
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
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
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
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
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> bool:
        resource_id = self._required_text(resource_id, "resource_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        parameters: tuple[Any, ...] = (
            self._utc_now(), resource_id, tenant_id, workspace_id
        )
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                "UPDATE knowledge_resources "
                "SET status = 'archived', updated_at = ? "
                "WHERE id = ? AND tenant_id = ? AND workspace_id = ? "
                "AND status != 'archived'",
                parameters,
            )
        archived = cursor.rowcount > 0
        if archived:
            self._sync_vector_index_best_effort(
                resource_ids=(resource_id,),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        return archived

    def propose_ingestion(
        self,
        conversation_id: str,
        turn_id: str,
        resources: Iterable[Mapping[str, Any]],
        idempotency_key: str,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
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
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
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
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        expected_state_version: Optional[int] = None,
    ) -> dict[str, Any]:
        """CAS-confirm a proposal and atomically ingest all candidate resources."""
        proposal_id = self._required_text(proposal_id, "proposal_id")
        actor = self._required_text(actor, "actor")
        confirmation_token = self._required_text(
            confirmation_token, "confirmation_token"
        )
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
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
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    proposal_id=proposal_id,
                    event_type="ingestion.confirmed",
                    actor=actor,
                    payload={"resources": ingested},
                )

        if conflict_reason is not None:
            raise KnowledgeProposalConflictError(conflict_reason)
        self._sync_vector_index_best_effort(
            resource_ids=(item["id"] for item in ingested),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
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
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        expected_state_version: Optional[int] = None,
    ) -> dict[str, Any]:
        proposal_id = self._required_text(proposal_id, "proposal_id")
        actor = self._required_text(actor, "actor")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        expected_state_version = self._optional_state_version(
            expected_state_version
        )
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
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
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
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        conversation_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        allowed_statuses = {"pending", "confirmed", "rejected", "conflict"}
        if status is not None and status not in allowed_statuses:
            raise ValueError(f"Unsupported ingestion proposal status: {status}")
        conversation_id = self._optional_text(
            conversation_id, "conversation_id"
        )
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
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._ingestion_proposal_record(row) for row in rows]

    def list_ingestion_events(
        self,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        proposal_id: Optional[str] = None,
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
        tenant_id: Optional[str] = None,
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
        actor: Optional[str],
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

    def propose_rule(
        self,
        statement: str,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        scope: str = "workspace",
        proposed_by: str = "agent",
        source_conversation_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        rule_id: Optional[str] = None,
    ) -> dict[str, Any]:
        statement = self._required_text(statement, "statement")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
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
                """SELECT * FROM knowledge_rules
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?""",
                (rule_id, tenant_id, workspace_id),
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
                    id, tenant_id, workspace_id, statement, scope, status, proposed_by,
                    source_conversation_id, source_message_id, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    tenant_id,
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
                """SELECT * FROM knowledge_rules
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?""",
                (rule_id, tenant_id, workspace_id),
            ).fetchone()
        return self._rule_record(row)

    def confirm_rule(
        self,
        rule_id: str,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        confirmed_by: str,
        confirmation_token: str,
        confirmer_type: str = "user",
    ) -> dict[str, Any]:
        rule_id = self._required_text(rule_id, "rule_id")
        tenant_id, workspace_id = self._resolve_transition_scope(
            workspace_id, tenant_id
        )
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
            scope_clause = "id = ?"
            scope_parameters: list[Any] = [rule_id]
            if tenant_id is not None and workspace_id is not None:
                scope_clause += " AND tenant_id = ? AND workspace_id = ?"
                scope_parameters.extend((tenant_id, workspace_id))
            row = connection.execute(
                f"SELECT * FROM knowledge_rules WHERE {scope_clause}",
                scope_parameters,
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
                f"""
                UPDATE knowledge_rules
                SET status = 'accepted', decision_by = ?, decision_at = ?,
                    confirmation_hash = ?, updated_at = ?
                WHERE {scope_clause}
                """,
                [confirmed_by, now, confirmation_hash, now, *scope_parameters],
            )
            row = connection.execute(
                f"SELECT * FROM knowledge_rules WHERE {scope_clause}",
                scope_parameters,
            ).fetchone()
        result = self._rule_record(row)
        result["decision_created"] = True
        return result

    def reject_rule(
        self,
        rule_id: str,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        rejected_by: str,
        confirmer_type: str = "user",
    ) -> dict[str, Any]:
        return self._decide_rule(
            rule_id,
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            status="rejected",
            decided_by=rejected_by,
            confirmer_type=confirmer_type,
        )

    def revoke_rule(
        self,
        rule_id: str,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        revoked_by: str,
        confirmation_token: str,
        confirmer_type: str = "user",
    ) -> dict[str, Any]:
        rule_id = self._required_text(rule_id, "rule_id")
        tenant_id, workspace_id = self._resolve_transition_scope(
            workspace_id, tenant_id
        )
        revoked_by = self._required_text(revoked_by, "revoked_by")
        self._required_text(confirmation_token, "confirmation_token")
        if confirmer_type not in self.CONFIRMER_TYPES:
            raise ValueError("confirmer_type must be user or admin")
        now = self._utc_now()
        with self._connection(write=True) as connection:
            scope_clause = "id = ?"
            scope_parameters: list[Any] = [rule_id]
            if tenant_id is not None and workspace_id is not None:
                scope_clause += " AND tenant_id = ? AND workspace_id = ?"
                scope_parameters.extend((tenant_id, workspace_id))
            row = connection.execute(
                f"SELECT * FROM knowledge_rules WHERE {scope_clause}",
                scope_parameters,
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown rule: {rule_id}")
            if row["status"] != "accepted":
                raise ValueError(f"Rule cannot be revoked from {row['status']}")
            connection.execute(
                f"""
                UPDATE knowledge_rules
                SET status = 'revoked', revoked_by = ?, revoked_at = ?, updated_at = ?
                WHERE {scope_clause}
                """,
                [revoked_by, now, now, rule_id, *scope_parameters[1:]],
            )
            row = connection.execute(
                f"SELECT * FROM knowledge_rules WHERE {scope_clause}",
                scope_parameters,
            ).fetchone()
        return self._rule_record(row)

    def _decide_rule(
        self,
        rule_id: str,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        status: str,
        decided_by: str,
        confirmer_type: str,
    ) -> dict[str, Any]:
        rule_id = self._required_text(rule_id, "rule_id")
        tenant_id, workspace_id = self._resolve_transition_scope(
            workspace_id, tenant_id
        )
        decided_by = self._required_text(decided_by, "decided_by")
        if confirmer_type not in self.CONFIRMER_TYPES:
            raise ValueError("confirmer_type must be user or admin")
        now = self._utc_now()
        with self._connection(write=True) as connection:
            scope_clause = "id = ?"
            scope_parameters: list[Any] = [rule_id]
            if tenant_id is not None and workspace_id is not None:
                scope_clause += " AND tenant_id = ? AND workspace_id = ?"
                scope_parameters.extend((tenant_id, workspace_id))
            row = connection.execute(
                f"SELECT * FROM knowledge_rules WHERE {scope_clause}",
                scope_parameters,
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown rule: {rule_id}")
            if row["status"] != "proposed":
                raise ValueError(f"Rule cannot be {status} from {row['status']}")
            connection.execute(
                f"""
                UPDATE knowledge_rules
                SET status = ?, decision_by = ?, decision_at = ?, updated_at = ?
                WHERE {scope_clause}
                """,
                [status, decided_by, now, now, *scope_parameters],
            )
            row = connection.execute(
                f"SELECT * FROM knowledge_rules WHERE {scope_clause}",
                scope_parameters,
            ).fetchone()
        return self._rule_record(row)

    def list_rules(
        self,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        scope: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        if status is not None and status not in self.RULE_STATUSES:
            raise ValueError(f"Unsupported rule status: {status}")
        scope = self._optional_text(scope, "scope")
        limit = self._validate_limit(limit)
        clauses = ["tenant_id = ?", "workspace_id = ?"]
        parameters: list[Any] = [tenant_id, workspace_id]
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
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        scope: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.list_rules(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            status="accepted",
            scope=scope,
            limit=limit,
        )

    # ===== Phase 2: 知识炼化（consolidation）写入接口 =====

    CONSOLIDATION_STATUSES = frozenset(
        {"active", "superseded", "conflict"}
    )

    def record_hit(
        self,
        resource_id: str,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> bool:
        """Stamp ``last_hit`` for decay/reinforcement bookkeeping."""
        resource_id = self._required_text(resource_id, "resource_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_resources
                SET last_hit = ?, updated_at = updated_at
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                """,
                (self._utc_now(), resource_id, tenant_id, workspace_id),
            )
        return cursor.rowcount > 0

    def set_confidence(
        self,
        resource_id: str,
        confidence: float,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> bool:
        """Set a resource's quality confidence in [0, 1]."""
        resource_id = self._required_text(resource_id, "resource_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence must be a number")
        try:
            normalized_confidence = float(confidence)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("confidence must be a number") from error
        if not isfinite(normalized_confidence):
            raise ValueError("confidence must be a number")
        confidence = max(0.0, min(1.0, normalized_confidence))
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_resources
                SET confidence = ?, updated_at = updated_at
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                """,
                (confidence, resource_id, tenant_id, workspace_id),
            )
        return cursor.rowcount > 0

    def mark_consolidation_status(
        self,
        resource_id: str,
        status: str,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> bool:
        """Mark a resource as active / superseded / conflict."""
        resource_id = self._required_text(resource_id, "resource_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        status = self._required_text(status, "status")
        if status not in self.CONSOLIDATION_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(self.CONSOLIDATION_STATUSES)}"
            )
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_resources
                SET consolidation_status = ?, updated_at = updated_at
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                """,
                (status, resource_id, tenant_id, workspace_id),
            )
        return cursor.rowcount > 0

    def set_supersedes(
        self,
        resource_id: str,
        superseded_id: Optional[str],
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> bool:
        """Link a newer resource to the one it supersedes."""
        resource_id = self._required_text(resource_id, "resource_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        superseded_id = self._optional_text(superseded_id, "superseded_id")
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_resources
                SET supersedes = ?, updated_at = updated_at
                WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                """,
                (superseded_id, resource_id, tenant_id, workspace_id),
            )
        return cursor.rowcount > 0

    def set_source_episode(
        self,
        resource_id: str,
        episode_id: str,
        version: Optional[int] = None,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> bool:
        """Tag a version with the episode that produced/confirmed it."""
        resource_id = self._required_text(resource_id, "resource_id")
        episode_id = self._required_text(episode_id, "episode_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        with self._connection() as connection:
            if version is None:
                row = connection.execute(
                    """SELECT current_version FROM knowledge_resources
                    WHERE id = ? AND tenant_id = ? AND workspace_id = ?""",
                    (resource_id, tenant_id, workspace_id),
                ).fetchone()
                if row is None:
                    return False
                version = int(row["current_version"])
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                UPDATE knowledge_versions
                SET source_episode = ?
                WHERE resource_id = ? AND tenant_id = ? AND version = ?
                """,
                (episode_id, resource_id, tenant_id, version),
            )
        return cursor.rowcount > 0

    def iter_active_resources(
        self,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        consolidation_status: Optional[str] = None,
        limit: int = 5000,
    ) -> Iterator[dict[str, Any]]:
        """Yield current versions of resources for consolidation scanning."""
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
        ):
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
                WHERE {' AND '.join(clauses)}
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
        if self.vector_store is None:
            return {
                "available": False,
                "count": 0,
                "last_search_mode": self.last_search_mode,
                "sync_pending": False,
                "last_error": "vector search disabled",
            }
        status = self.vector_store.status()
        status["last_search_mode"] = self.last_search_mode
        status["sync_pending"] = self.sync_pending
        return status

    def sync_vector_index(self, *, force: bool = False) -> dict[str, Any]:
        """Synchronize the derived index from committed knowledge state."""
        self._sync_vector_index(force=force)
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
                item["id"]: item["metadata"]
                for item in vector_store.list_entries()
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
                item for item in specs
                if current.get(item["id"]) != item["metadata"]
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
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
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
                if (item.get("metadata") or {}).get("resource_id")
                in normalized_ids
                and (
                    tenant_id is None
                    or (item.get("metadata") or {}).get("tenant_id") == tenant_id
                )
                and (
                    workspace_id is None
                    or (item.get("metadata") or {}).get("workspace_id")
                    == workspace_id
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
                    WHERE {' AND '.join(scope_clauses)}
                      AND r.status = 'active'
                      AND r.consolidation_status = 'active'
                    """,
                    tuple(scope_parameters),
                ).fetchall()
            specs = self._vector_specs(rows)
            desired = {item["id"]: item["metadata"] for item in specs}
            changed = [
                item for item in specs
                if current.get(item["id"]) != item["metadata"]
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
        resource_ids: Optional[Iterable[str]] = None,
        tenant_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
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
        except Exception as error:
            if self.vector_store is not None:
                self.vector_store.needs_rebuild = True
                self.vector_store.last_error = f"knowledge vector sync pending: {error}"

    def _vector_specs(
        self, rows: Iterable[sqlite3.Row]
    ) -> list[dict[str, Any]]:
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

    def _embedded_vector_entry(
        self, item: Mapping[str, Any]
    ) -> dict[str, Any]:
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
        while start < len(normalized) and len(chunks) < cls.MAX_VECTOR_CHUNKS_PER_RESOURCE:
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
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 10,
        resource_types: Optional[Iterable[str]] = None,
        source_types: Optional[Iterable[str]] = None,
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
            raise ValueError("confidence_floor must be between 0 and 1")
        try:
            confidence_floor = float(confidence_floor)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("confidence_floor must be between 0 and 1") from error
        if not isfinite(confidence_floor) or not 0.0 <= confidence_floor <= 1.0:
            raise ValueError("confidence_floor must be between 0 and 1")
        resource_type_filter = self._normalized_filter(
            resource_types, "resource_types"
        )
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
                not resource_type_filter
                or row["resource_type"] in resource_type_filter
            )
            and (
                not source_type_filter
                or row["source_type"] in source_type_filter
            )
        }
        vector_hits: list[dict[str, Any]] = []
        vector_available = bool(
            self.vector_store is not None and self.vector_store.available
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
            except Exception as error:
                self.last_search_mode = "literal-fallback"
                if self.vector_store is not None:
                    self.vector_store.last_error = f"knowledge search failed: {error}"
        else:
            self.last_search_mode = (
                "vector" if vector_available else "literal-fallback"
            )

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
                existing["score"] = float(existing["vector_score"]) + min(
                    literal_score, 10.0
                ) * 0.1
                continue
            resource_candidates[resource_id] = {
                "row": row,
                "retrieval_mode": (
                    "literal-supplement"
                    if vector_available
                    else "literal-fallback"
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

        if include_rules and (not resource_type_filter or "rule" in resource_type_filter):
            for row in rule_rows:
                score = self._literal_score(query, row["statement"])
                if score <= 0:
                    continue
                record = self._rule_record(row)
                record.update({
                    "record_type": "rule",
                    "retrieval_mode": "accepted-rule",
                    "literal_score": score,
                    "score": min(score, 10.0) * 0.1,
                    "text": row["statement"],
                    "title": "已采纳规则",
                })
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
        values: Optional[Iterable[str]],
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
            "source_episode": row["source_episode"],
            "confidence": float(row["confidence"]),
        }

    @classmethod
    def _rule_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
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
