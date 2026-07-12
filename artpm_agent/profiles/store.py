"""Versioned workspace Agent Profiles and confirmed change proposals."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from .models import (
    DEFAULT_WORKSPACE_ID,
    AgentIdentity,
    AgentProfile,
    AgentProfilePatch,
    ProfileChangeProposal,
    QuotePolicy,
    apply_profile_patch,
)


class ProfileConflictError(RuntimeError):
    """Raised when a proposal loses its profile revision compare-and-swap."""


class AgentProfileStore:
    """Persist profile snapshots in the workspace/conversation SQLite database."""

    SCHEMA_VERSION = 1
    BUSY_TIMEOUT_MS = 10_000
    MAX_JSON_BYTES = 64 * 1024

    def __init__(
        self,
        db_path: str | Path,
        *,
        default_identity: AgentIdentity | None = None,
        default_quote_policy: QuotePolicy | None = None,
    ) -> None:
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        self.default_identity = default_identity or AgentIdentity()
        self.default_quote_policy = default_quote_policy or QuotePolicy()
        self._enable_wal()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {self.BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()

    def _enable_wal(self) -> None:
        with self._connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @classmethod
    def _json(cls, value: Any) -> str:
        raw = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(raw.encode("utf-8")) > cls.MAX_JSON_BYTES:
            raise ValueError("profile JSON payload is too large")
        return raw

    def _migrate(self) -> None:
        with self._connection(write=True) as conn:
            workspace_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspaces'"
            ).fetchone()
            if workspace_table is None:
                raise RuntimeError(
                    "workspaces table is missing; initialize ConversationStore first"
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_profile_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version "
                "FROM agent_profile_schema_migrations"
            ).fetchone()
            current = int(row["version"])
            if current > self.SCHEMA_VERSION:
                raise RuntimeError(
                    "Agent Profile schema is newer than this application supports"
                )
            if current >= 1:
                return
            conn.executescript(
                """
                CREATE TABLE agent_profiles (
                    workspace_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, profile_id, revision),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
                        ON DELETE CASCADE
                );

                CREATE TABLE agent_profile_proposals (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    conversation_id TEXT,
                    turn_id TEXT,
                    base_revision INTEGER NOT NULL CHECK(base_revision >= 1),
                    status TEXT NOT NULL CHECK(status IN (
                        'pending', 'confirmed', 'rejected', 'conflict'
                    )),
                    patch_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    actor TEXT,
                    applied_revision INTEGER,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    UNIQUE(workspace_id, profile_id, idempotency_key),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                        ON DELETE SET NULL
                );

                CREATE TABLE agent_profile_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    proposal_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(proposal_id) REFERENCES agent_profile_proposals(id)
                        ON DELETE SET NULL
                );

                CREATE INDEX idx_agent_profiles_current
                    ON agent_profiles(workspace_id, profile_id, revision DESC);
                CREATE INDEX idx_agent_profile_proposals_pending
                    ON agent_profile_proposals(
                        workspace_id, profile_id, status, created_at DESC
                    );
                CREATE INDEX idx_agent_profile_events_workspace
                    ON agent_profile_events(workspace_id, profile_id, id);
                """
            )
            conn.execute(
                """
                INSERT INTO agent_profile_schema_migrations(version, applied_at)
                VALUES (1, ?)
                """,
                (self._now(),),
            )

    @staticmethod
    def _workspace(
        conn: sqlite3.Connection,
        workspace_id: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT id, profile_id FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown workspace: {workspace_id}")
        return row

    def _ensure_profile(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
    ) -> AgentProfile:
        workspace = self._workspace(conn, workspace_id)
        profile_id = workspace["profile_id"]
        row = conn.execute(
            """
            SELECT profile_json FROM agent_profiles
            WHERE workspace_id = ? AND profile_id = ?
            ORDER BY revision DESC LIMIT 1
            """,
            (workspace_id, profile_id),
        ).fetchone()
        if row is not None:
            return AgentProfile.model_validate_json(row["profile_json"])
        now = self._now()
        profile = AgentProfile(
            workspace_id=workspace_id,
            profile_id=profile_id,
            revision=1,
            identity=self.default_identity,
            quote_policy=self.default_quote_policy,
            updated_at=now,
        )
        conn.execute(
            """
            INSERT INTO agent_profiles(
                workspace_id, profile_id, revision, profile_json, created_at
            ) VALUES (?, ?, 1, ?, ?)
            """,
            (
                workspace_id,
                profile_id,
                self._json(profile.model_dump(mode="json")),
                now,
            ),
        )
        self._event(
            conn,
            workspace_id,
            profile_id,
            None,
            "profile.created",
            {"revision": 1},
        )
        return profile

    def _event(
        self,
        conn: sqlite3.Connection,
        workspace_id: str,
        profile_id: str,
        proposal_id: str | None,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO agent_profile_events(
                workspace_id, profile_id, proposal_id,
                event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                profile_id,
                proposal_id,
                event_type,
                self._json(payload or {}),
                self._now(),
            ),
        )

    def get_effective_profile(
        self,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> AgentProfile:
        with self._connection(write=True) as conn:
            return self._ensure_profile(conn, workspace_id)

    @staticmethod
    def _proposal_from_row(row: sqlite3.Row) -> ProfileChangeProposal:
        return ProfileChangeProposal(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            workspace_id=row["workspace_id"],
            profile_id=row["profile_id"],
            conversation_id=row["conversation_id"],
            turn_id=row["turn_id"],
            base_revision=int(row["base_revision"]),
            status=row["status"],
            patch=AgentProfilePatch.model_validate_json(row["patch_json"]),
            summary=row["summary"],
            actor=row["actor"],
            applied_revision=row["applied_revision"],
            created_at=row["created_at"],
            decided_at=row["decided_at"],
        )

    def propose_change(
        self,
        conversation_id: str,
        patch: AgentProfilePatch,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        turn_id: str | None = None,
        summary: str = "更新 Workspace Agent Profile",
        idempotency_key: str | None = None,
        expected_revision: int | None = None,
    ) -> ProfileChangeProposal:
        """Persist an inert proposal; this method never changes the profile."""
        if not summary.strip() or len(summary.strip()) > 500:
            raise ValueError("summary must contain 1 to 500 characters")
        proposal_id = uuid4().hex
        idempotency_key = idempotency_key or uuid4().hex
        patch_json = self._json(patch.model_dump(mode="json"))
        now = self._now()
        with self._connection(write=True) as conn:
            profile = self._ensure_profile(conn, workspace_id)
            conversation = conn.execute(
                """
                SELECT 1 FROM conversations
                WHERE id = ? AND workspace_id = ?
                """,
                (conversation_id, workspace_id),
            ).fetchone()
            if conversation is None:
                raise KeyError("conversation does not belong to the workspace")
            if expected_revision is not None and expected_revision != profile.revision:
                raise ProfileConflictError("profile revision changed before proposal")
            existing = conn.execute(
                """
                SELECT * FROM agent_profile_proposals
                WHERE workspace_id = ? AND profile_id = ? AND idempotency_key = ?
                """,
                (workspace_id, profile.profile_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["conversation_id"] != conversation_id
                    or existing["patch_json"] != patch_json
                ):
                    raise ValueError("idempotency key was used for another proposal")
                return self._proposal_from_row(existing)
            conn.execute(
                """
                INSERT INTO agent_profile_proposals(
                    id, idempotency_key, workspace_id, profile_id,
                    conversation_id, turn_id, base_revision, status,
                    patch_json, summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    proposal_id,
                    idempotency_key,
                    workspace_id,
                    profile.profile_id,
                    conversation_id,
                    turn_id,
                    profile.revision,
                    patch_json,
                    summary.strip(),
                    now,
                ),
            )
            self._event(
                conn,
                workspace_id,
                profile.profile_id,
                proposal_id,
                "proposal.created",
                {"base_revision": profile.revision},
            )
            row = conn.execute(
                "SELECT * FROM agent_profile_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return self._proposal_from_row(row)

    def get_proposal(self, proposal_id: str) -> ProfileChangeProposal | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM agent_profile_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return self._proposal_from_row(row) if row is not None else None

    def list_pending(
        self,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        conversation_id: str | None = None,
    ) -> list[ProfileChangeProposal]:
        conversation_sql = "AND conversation_id = ?" if conversation_id else ""
        params: list[Any] = [workspace_id]
        if conversation_id:
            params.append(conversation_id)
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM agent_profile_proposals
                WHERE workspace_id = ? AND status = 'pending' {conversation_sql}
                ORDER BY created_at DESC, id DESC
                """,
                params,
            ).fetchall()
        return [self._proposal_from_row(row) for row in rows]

    def confirm_change(
        self,
        proposal_id: str,
        *,
        actor: str,
    ) -> AgentProfile:
        """CAS-confirm a proposal and append one immutable profile revision."""
        if not actor.strip():
            raise ValueError("actor is required")
        conflict = False
        result: AgentProfile | None = None
        with self._connection(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM agent_profile_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise KeyError("unknown profile proposal")
            proposal = self._proposal_from_row(row)
            current = self._ensure_profile(conn, proposal.workspace_id)
            if proposal.status == "confirmed":
                revision_row = conn.execute(
                    """
                    SELECT profile_json FROM agent_profiles
                    WHERE workspace_id = ? AND profile_id = ? AND revision = ?
                    """,
                    (
                        proposal.workspace_id,
                        proposal.profile_id,
                        proposal.applied_revision,
                    ),
                ).fetchone()
                return AgentProfile.model_validate_json(revision_row["profile_json"])
            if proposal.status != "pending":
                raise ProfileConflictError(
                    f"proposal cannot be confirmed from {proposal.status}"
                )
            now = self._now()
            if current.revision != proposal.base_revision:
                conn.execute(
                    """
                    UPDATE agent_profile_proposals
                    SET status = 'conflict', actor = ?, decided_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (actor.strip(), now, proposal_id),
                )
                self._event(
                    conn,
                    proposal.workspace_id,
                    proposal.profile_id,
                    proposal_id,
                    "proposal.conflict",
                    {
                        "base_revision": proposal.base_revision,
                        "current_revision": current.revision,
                    },
                )
                conflict = True
            else:
                result = apply_profile_patch(
                    current,
                    proposal.patch,
                    revision=current.revision + 1,
                    updated_at=now,
                )
                conn.execute(
                    """
                    INSERT INTO agent_profiles(
                        workspace_id, profile_id, revision,
                        profile_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        result.workspace_id,
                        result.profile_id,
                        result.revision,
                        self._json(result.model_dump(mode="json")),
                        now,
                    ),
                )
                cursor = conn.execute(
                    """
                    UPDATE agent_profile_proposals
                    SET status = 'confirmed', actor = ?,
                        applied_revision = ?, decided_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (actor.strip(), result.revision, now, proposal_id),
                )
                if cursor.rowcount != 1:
                    raise ProfileConflictError("proposal confirmation lost a race")
                self._event(
                    conn,
                    proposal.workspace_id,
                    proposal.profile_id,
                    proposal_id,
                    "proposal.confirmed",
                    {"applied_revision": result.revision},
                )
        if conflict:
            raise ProfileConflictError("profile changed after the proposal was created")
        return result

    def reject_change(
        self,
        proposal_id: str,
        *,
        actor: str,
    ) -> ProfileChangeProposal:
        if not actor.strip():
            raise ValueError("actor is required")
        now = self._now()
        with self._connection(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM agent_profile_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise KeyError("unknown profile proposal")
            proposal = self._proposal_from_row(row)
            if proposal.status == "rejected":
                return proposal
            if proposal.status != "pending":
                raise ProfileConflictError(
                    f"proposal cannot be rejected from {proposal.status}"
                )
            cursor = conn.execute(
                """
                UPDATE agent_profile_proposals
                SET status = 'rejected', actor = ?, decided_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (actor.strip(), now, proposal_id),
            )
            if cursor.rowcount != 1:
                raise ProfileConflictError("proposal rejection lost a race")
            self._event(
                conn,
                proposal.workspace_id,
                proposal.profile_id,
                proposal_id,
                "proposal.rejected",
            )
            row = conn.execute(
                "SELECT * FROM agent_profile_proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return self._proposal_from_row(row)

    def list_events(
        self,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_profile_events
                WHERE workspace_id = ? ORDER BY id
                """,
                (workspace_id,),
            ).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]
