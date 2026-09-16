"""Explicit retention, export, compaction, and tenant-offboarding contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class MemoryRetentionPolicy:
    """Conservative defaults; authoritative current knowledge never auto-expires."""

    conversation_days: int = 365
    session_event_days: int = 180
    feedback_days: int = 365
    episode_days: int = 365
    completed_outbox_days: int = 30
    dead_letter_days: int = 90
    knowledge_versions_per_resource: int = 10
    authoritative_knowledge_days: None = None
    accepted_rule_days: None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class MemoryLifecycleService:
    """Coordinate lifecycle operations across registry-owned memory stores."""

    def __init__(
        self,
        storage_registry: Any,
        *,
        policy: MemoryRetentionPolicy | None = None,
        auxiliary_stores: dict[str, Any] | None = None,
    ) -> None:
        self.storage = storage_registry
        self.policy = policy or MemoryRetentionPolicy()
        self.auxiliary_stores = auxiliary_stores or {}

    def bind_auxiliary_stores(self, **stores: Any) -> None:
        """Attach process-owned learning stores without constructing new copies."""
        self.auxiliary_stores.update(
            (name, store) for name, store in stores.items() if store is not None
        )

    @staticmethod
    def _required(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _workspace(self, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        tenant_id = self._required(tenant_id, "tenant_id")
        workspace_id = self._required(workspace_id, "workspace_id")
        with self.storage.conversation._connection() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ? AND tenant_id = ?",
                (workspace_id, tenant_id),
            ).fetchone()
        if row is None:
            raise KeyError("workspace does not belong to tenant")
        return dict(row)

    def export_workspace(self, *, tenant_id: str, workspace_id: str) -> dict[str, Any]:
        """Return a complete JSON-serializable export without changing state."""
        workspace = self._workspace(tenant_id, workspace_id)
        conversation = self.storage.conversation
        knowledge = self.storage.knowledge
        with conversation._connection() as connection:
            conversations = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM conversations WHERE workspace_id = ? ORDER BY created_at, id",
                    (workspace_id,),
                ).fetchall()
            ]
            conversation_ids = [item["id"] for item in conversations]
            messages: list[dict[str, Any]] = []
            sessions: list[dict[str, Any]] = []
            if conversation_ids:
                placeholders = ", ".join("?" for _ in conversation_ids)
                messages = [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM messages WHERE conversation_id IN ({placeholders}) "
                        "ORDER BY conversation_id, id",
                        conversation_ids,
                    ).fetchall()
                ]
                table_exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'session_entries'"
                ).fetchone()
                if table_exists:
                    sessions = [
                        dict(row)
                        for row in connection.execute(
                            "SELECT * FROM session_entries WHERE workspace_id = ? "
                            "ORDER BY conversation_id, sequence",
                            (workspace_id,),
                        ).fetchall()
                    ]

        with knowledge._connection() as connection:
            table_queries = {
                "resources": (
                    "SELECT * FROM knowledge_resources "
                    "WHERE tenant_id = ? AND workspace_id = ? ORDER BY id"
                ),
                "versions": (
                    "SELECT v.* FROM knowledge_versions v "
                    "JOIN knowledge_resources r ON r.id = v.resource_id "
                    "WHERE r.tenant_id = ? AND r.workspace_id = ? "
                    "ORDER BY v.resource_id, v.version"
                ),
                "rules": (
                    "SELECT * FROM knowledge_rules "
                    "WHERE tenant_id = ? AND workspace_id = ? ORDER BY id"
                ),
                "ingestion_proposals": (
                    "SELECT * FROM knowledge_ingestion_proposals "
                    "WHERE tenant_id = ? AND workspace_id = ? ORDER BY id"
                ),
                "ingestion_events": (
                    "SELECT * FROM knowledge_ingestion_events "
                    "WHERE tenant_id = ? AND workspace_id = ? ORDER BY id"
                ),
            }
            knowledge_export = {
                name: [
                    dict(row)
                    for row in connection.execute(
                        query, (tenant_id, workspace_id)
                    ).fetchall()
                ]
                for name, query in table_queries.items()
            }
        return {
            "contract_version": 1,
            "exported_at": self._now().isoformat(timespec="microseconds"),
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "retention_policy": self.policy.as_dict(),
            "workspace": workspace,
            "conversations": conversations,
            "messages": messages,
            "session_entries": sessions,
            "knowledge": knowledge_export,
        }

    def compact_workspace(self, *, tenant_id: str, workspace_id: str) -> dict[str, int]:
        """Prune superseded history and expired operational records only."""
        self._workspace(tenant_id, workspace_id)
        now = self._now()
        knowledge = self.storage.knowledge
        removed_versions = 0
        with knowledge._connection(write=True) as connection:
            resources = connection.execute(
                "SELECT id, current_version FROM knowledge_resources "
                "WHERE tenant_id = ? AND workspace_id = ?",
                (tenant_id, workspace_id),
            ).fetchall()
            for resource in resources:
                versions = connection.execute(
                    "SELECT version FROM knowledge_versions WHERE resource_id = ? "
                    "ORDER BY version DESC",
                    (resource["id"],),
                ).fetchall()
                keep = {
                    int(item["version"])
                    for item in versions[: self.policy.knowledge_versions_per_resource]
                }
                keep.add(int(resource["current_version"]))
                stale = [
                    int(item["version"])
                    for item in versions
                    if int(item["version"]) not in keep
                ]
                if stale:
                    placeholders = ", ".join("?" for _ in stale)
                    removed_versions += connection.execute(
                        f"DELETE FROM knowledge_versions "
                        f"WHERE resource_id = ? AND version IN ({placeholders})",
                        (resource["id"], *stale),
                    ).rowcount
            done_before = (
                now - timedelta(days=self.policy.completed_outbox_days)
            ).isoformat(timespec="microseconds")
            dead_before = (
                now - timedelta(days=self.policy.dead_letter_days)
            ).isoformat(timespec="microseconds")
            removed_outbox = connection.execute(
                "DELETE FROM knowledge_index_outbox "
                "WHERE tenant_id = ? AND workspace_id = ? AND "
                "((status = 'done' AND completed_at < ?) OR "
                "(status = 'dead' AND dead_lettered_at < ?))",
                (tenant_id, workspace_id, done_before, dead_before),
            ).rowcount

        conversation = self.storage.conversation
        session_before = (
            now - timedelta(days=self.policy.session_event_days)
        ).isoformat(timespec="microseconds")
        with conversation._connection(write=True) as connection:
            table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'session_entries'"
            ).fetchone()
            removed_sessions = (
                connection.execute(
                    "DELETE FROM session_entries "
                    "WHERE workspace_id = ? AND created_at < ?",
                    (workspace_id, session_before),
                ).rowcount
                if table_exists
                else 0
            )
        return {
            "knowledge_versions": removed_versions,
            "outbox_records": removed_outbox,
            "session_entries": removed_sessions,
        }

    def delete_workspace(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Delete one workspace and enqueue a full derived-index rebuild."""
        self._workspace(tenant_id, workspace_id)
        expected = f"{tenant_id}:{workspace_id}"
        if confirmation != expected:
            raise ValueError("confirmation must exactly match tenant_id:workspace_id")

        knowledge = self.storage.knowledge
        now = self._now().isoformat(timespec="microseconds")
        with knowledge._connection(write=True) as connection:
            counts = {
                "resources": connection.execute(
                    "SELECT COUNT(*) FROM knowledge_resources "
                    "WHERE tenant_id = ? AND workspace_id = ?",
                    (tenant_id, workspace_id),
                ).fetchone()[0],
                "rules": connection.execute(
                    "SELECT COUNT(*) FROM knowledge_rules "
                    "WHERE tenant_id = ? AND workspace_id = ?",
                    (tenant_id, workspace_id),
                ).fetchone()[0],
            }
            connection.execute(
                "DELETE FROM knowledge_ingestion_events "
                "WHERE tenant_id = ? AND workspace_id = ?",
                (tenant_id, workspace_id),
            )
            connection.execute(
                "DELETE FROM knowledge_ingestion_proposals "
                "WHERE tenant_id = ? AND workspace_id = ?",
                (tenant_id, workspace_id),
            )
            connection.execute(
                "DELETE FROM knowledge_rules WHERE tenant_id = ? AND workspace_id = ?",
                (tenant_id, workspace_id),
            )
            connection.execute(
                "DELETE FROM knowledge_resources "
                "WHERE tenant_id = ? AND workspace_id = ?",
                (tenant_id, workspace_id),
            )
            connection.execute(
                "DELETE FROM knowledge_index_outbox "
                "WHERE tenant_id = ? AND workspace_id = ?",
                (tenant_id, workspace_id),
            )
            connection.execute(
                """
                INSERT INTO knowledge_index_outbox(
                    tenant_id, workspace_id, resource_id, operation, status,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, NULL, 'rebuild', 'pending', ?, ?, ?)
                """,
                (tenant_id, workspace_id, now, now, now),
            )

        conversation = self.storage.conversation
        with conversation._connection(write=True) as connection:
            workflow_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'workflow_runs'"
            ).fetchone()
            if workflow_exists:
                connection.execute(
                    "DELETE FROM workflow_runs "
                    "WHERE tenant_id = ? AND workspace_id = ?",
                    (tenant_id, workspace_id),
                )
                connection.execute(
                    "DELETE FROM workflow_definitions "
                    "WHERE tenant_id = ? AND workspace_id = ?",
                    (tenant_id, workspace_id),
                )
            permission_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'permission_requests'"
            ).fetchone()
            if permission_exists:
                connection.execute(
                    "DELETE FROM permission_requests "
                    "WHERE tenant_id = ? AND workspace_id = ?",
                    (tenant_id, workspace_id),
                )
            conversations = connection.execute(
                "DELETE FROM conversations WHERE workspace_id = ?",
                (workspace_id,),
            ).rowcount
            connection.execute(
                "DELETE FROM workspaces WHERE id = ? AND tenant_id = ?",
                (workspace_id, tenant_id),
            )

        auxiliary_counts: dict[str, int] = {}
        for name, store in self.auxiliary_stores.items():
            purge = getattr(store, "purge_scope", None)
            if callable(purge):
                auxiliary_counts[name] = int(
                    purge(tenant_id=tenant_id, workspace_id=workspace_id)
                )

        report = knowledge._vector_projector.run_until_idle()
        cleanup_pending = bool(report.get("pending") or report.get("dead"))
        if not cleanup_pending:
            with knowledge._connection(write=True) as connection:
                connection.execute(
                    "DELETE FROM knowledge_index_outbox "
                    "WHERE tenant_id = ? AND workspace_id = ? AND status = 'done'",
                    (tenant_id, workspace_id),
                )
        return {
            **counts,
            "conversations": conversations,
            "auxiliary": auxiliary_counts,
            "status": "index_cleanup_pending" if cleanup_pending else "complete",
            "vector_cleanup": report,
        }

    def offboard_tenant(
        self,
        *,
        tenant_id: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Delete all tenant workspaces; exact tenant confirmation is mandatory."""
        tenant_id = self._required(tenant_id, "tenant_id")
        if confirmation != tenant_id:
            raise ValueError("confirmation must exactly match tenant_id")
        workspaces = self.storage.conversation.list_workspaces(
            tenant_id=tenant_id,
            profile_id=None,
            limit=10_000,
        )
        results = [
            self.delete_workspace(
                tenant_id=tenant_id,
                workspace_id=item["id"],
                confirmation=f"{tenant_id}:{item['id']}",
            )
            for item in workspaces
        ]
        return {
            "tenant_id": tenant_id,
            "workspace_count": len(results),
            "status": (
                "complete"
                if all(item["status"] == "complete" for item in results)
                else "index_cleanup_pending"
            ),
            "workspaces": results,
        }


__all__ = ["MemoryLifecycleService", "MemoryRetentionPolicy"]
