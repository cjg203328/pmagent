"""Extracted workspace knowledge responsibility boundary."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable


class KnowledgeMigrationService:
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

            if current_version < 6:
                connection.executescript(
                    """
                    CREATE TABLE knowledge_index_outbox (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tenant_id TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        resource_id TEXT,
                        operation TEXT NOT NULL
                            CHECK(operation IN ('upsert', 'delete', 'rebuild')),
                        attempts INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE INDEX idx_knowledge_index_outbox_pending
                    ON knowledge_index_outbox(id, tenant_id, workspace_id);
                    """
                )
                now = self._utc_now()
                connection.execute(
                    """
                    INSERT INTO knowledge_index_outbox(
                        tenant_id, workspace_id, resource_id, operation,
                        created_at, updated_at
                    )
                    SELECT ?, ?, NULL, 'rebuild', ?, ?
                    WHERE EXISTS (SELECT 1 FROM knowledge_resources)
                    """,
                    (self.DEFAULT_TENANT_ID, self.DEFAULT_WORKSPACE_ID, now, now),
                )
                connection.execute(
                    "INSERT INTO knowledge_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (6, now),
                )
                current_version = 6

            if current_version < 7:
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(knowledge_index_outbox)"
                    ).fetchall()
                }
                additions = {
                    "status": "TEXT NOT NULL DEFAULT 'pending'",
                    "available_at": "TEXT",
                    "lease_owner": "TEXT",
                    "lease_expires_at": "TEXT",
                    "last_attempt_at": "TEXT",
                    "completed_at": "TEXT",
                    "dead_lettered_at": "TEXT",
                }
                for name, definition in additions.items():
                    if name not in columns:
                        connection.execute(
                            f"ALTER TABLE knowledge_index_outbox "
                            f"ADD COLUMN {name} {definition}"
                        )
                connection.execute(
                    "UPDATE knowledge_index_outbox "
                    "SET available_at = COALESCE(available_at, created_at), "
                    "status = COALESCE(NULLIF(status, ''), 'pending')"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_knowledge_outbox_claim "
                    "ON knowledge_index_outbox(status, available_at, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_knowledge_outbox_lease "
                    "ON knowledge_index_outbox(status, lease_expires_at)"
                )
                connection.execute(
                    "INSERT INTO knowledge_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (7, self._utc_now()),
                )

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
            (
                "ALTER TABLE knowledge_resources "
                "ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0"
            ),
            (
                "ALTER TABLE knowledge_resources ADD COLUMN consolidation_status "
                "TEXT NOT NULL DEFAULT 'active' "
                "CHECK(consolidation_status IN ('active', 'superseded', 'conflict'))"
            ),
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

        for index in connection.execute(f"PRAGMA index_list({table})").fetchall():
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

        resources_need_rebuild = cls._has_unique_index(
            connection,
            "knowledge_resources",
            ("workspace_id", "source_type", "source_id"),
        ) or not cls._has_unique_index(
            connection,
            "knowledge_resources",
            ("tenant_id", "workspace_id", "source_type", "source_id"),
        )
        proposals_need_rebuild = cls._has_unique_index(
            connection,
            "knowledge_ingestion_proposals",
            ("workspace_id", "idempotency_key"),
        ) or not cls._has_unique_index(
            connection,
            "knowledge_ingestion_proposals",
            ("tenant_id", "workspace_id", "idempotency_key"),
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


__all__ = ["KnowledgeMigrationService"]
