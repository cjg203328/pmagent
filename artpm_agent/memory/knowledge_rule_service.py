"""Extracted workspace knowledge responsibility boundary."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from hashlib import sha256
from math import isfinite
from typing import Any
from uuid import uuid4


class KnowledgeRuleService:
    def propose_rule(
        self,
        statement: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: str = "workspace",
        proposed_by: str = "agent",
        source_conversation_id: str | None = None,
        source_message_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        rule_id: str | None = None,
    ) -> dict[str, Any]:
        statement = self._required_text(statement, "statement")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        scope = self._required_text(scope, "scope")
        proposed_by = self._required_text(proposed_by, "proposed_by")
        source_conversation_id = self._optional_text(
            source_conversation_id, "source_conversation_id"
        )
        source_message_id = self._optional_text(source_message_id, "source_message_id")
        rule_id = self._required_text(rule_id, "rule_id") if rule_id else uuid4().hex
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
                    and existing["source_conversation_id"] == source_conversation_id
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
        workspace_id: str | None = None,
        tenant_id: str | None = None,
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
        confirmation_hash = sha256(confirmation_token.encode("utf-8")).hexdigest()
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
        workspace_id: str | None = None,
        tenant_id: str | None = None,
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
        workspace_id: str | None = None,
        tenant_id: str | None = None,
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
        workspace_id: str | None = None,
        tenant_id: str | None = None,
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
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        status: str | None = None,
        scope: str | None = None,
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
                WHERE {" AND ".join(clauses)}
                ORDER BY updated_at DESC, id DESC LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [self._rule_record(row) for row in rows]

    def get_active_rules(
        self,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
        scope: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return self.list_rules(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            status="accepted",
            scope=scope,
            limit=limit,
        )

    def record_hit(
        self,
        resource_id: str,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
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
        workspace_id: str | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        """Set a resource's quality confidence in [0, 1]."""
        resource_id = self._required_text(resource_id, "resource_id")
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            raise ValueError("confidence must be a number")  # noqa: TRY004
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
        workspace_id: str | None = None,
        tenant_id: str | None = None,
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
        superseded_id: str | None,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
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
        version: int | None = None,
        *,
        workspace_id: str | None = None,
        tenant_id: str | None = None,
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


__all__ = ["KnowledgeRuleService"]
