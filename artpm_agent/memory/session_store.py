"""Append-only, typed session entries backed by the conversation database."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Optional

from .conversation_store import ConversationStore


if TYPE_CHECKING:
    try:
        from artpm_agent.runtime.events import AgentEvent
    except ImportError:
        from artpm_agent.runtime.events import AgentEvent


@dataclass(frozen=True, slots=True)
class SessionEntry:
    """One immutable snapshot from a conversation's ordered session log."""

    id: int
    sequence: int
    workspace_id: str
    conversation_id: str
    entry_type: str
    run_id: str
    turn_id: str
    message: Optional[dict[str, Any]]
    tool_call: Optional[dict[str, Any]]
    tool_result: Optional[dict[str, Any]]
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Return a defensive, JSON-compatible representation."""
        return {
            "id": self.id,
            "sequence": self.sequence,
            "workspace_id": self.workspace_id,
            "conversation_id": self.conversation_id,
            "entry_type": self.entry_type,
            "run_id": self.run_id,
            "turn_id": self.turn_id,
            "message": deepcopy(self.message),
            "tool_call": deepcopy(self.tool_call),
            "tool_result": deepcopy(self.tool_result),
            "payload": deepcopy(self.payload),
            "created_at": self.created_at,
        }


class SessionStore:
    """Persist a linear, append-only log alongside ``ConversationStore`` data.

    The global SQLite ``id`` and the per-conversation ``sequence`` are allocated
    in the same immediate transaction. That makes both orderings monotonic even
    when several threads or store instances append concurrently.
    """

    SCHEMA_VERSION = 1
    BUSY_TIMEOUT_MS = ConversationStore.BUSY_TIMEOUT_MS
    DEFAULT_WORKSPACE_ID = ConversationStore.DEFAULT_WORKSPACE_ID

    def __init__(self, source: ConversationStore | str | Path):
        self.conversations = (
            source
            if isinstance(source, ConversationStore)
            else ConversationStore(source)
        )
        self.db_path = self.conversations.db_path
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

    def _migrate(self) -> None:
        with self._connection(write=True) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = conn.execute(
                """
                SELECT COALESCE(MAX(version), 0) AS version
                FROM session_schema_migrations
                """
            ).fetchone()
            current_version = int(row["version"])
            if current_version > self.SCHEMA_VERSION:
                raise RuntimeError(
                    "Session database schema is newer than this application supports"
                )

            if current_version < 1:
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                        idx_conversations_id_workspace
                    ON conversations(id, workspace_id)
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS session_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        sequence INTEGER NOT NULL CHECK(sequence > 0),
                        workspace_id TEXT NOT NULL,
                        conversation_id TEXT NOT NULL,
                        entry_type TEXT NOT NULL
                            CHECK(length(trim(entry_type)) > 0),
                        run_id TEXT NOT NULL CHECK(length(trim(run_id)) > 0),
                        turn_id TEXT NOT NULL CHECK(length(trim(turn_id)) > 0),
                        message_json TEXT
                            CHECK(message_json IS NULL OR json_valid(message_json)),
                        tool_call_json TEXT
                            CHECK(tool_call_json IS NULL OR json_valid(tool_call_json)),
                        tool_result_json TEXT
                            CHECK(tool_result_json IS NULL OR json_valid(tool_result_json)),
                        payload_json TEXT NOT NULL DEFAULT '{}'
                            CHECK(json_valid(payload_json)),
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(conversation_id, workspace_id)
                            REFERENCES conversations(id, workspace_id)
                            ON DELETE CASCADE,
                        UNIQUE(conversation_id, sequence)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_session_entries_scope_sequence
                    ON session_entries(workspace_id, conversation_id, sequence)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_session_entries_run_sequence
                    ON session_entries(conversation_id, run_id, sequence)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO session_schema_migrations(version, applied_at)
                    VALUES (?, ?)
                    """,
                    (1, self._utc_now()),
                )

            violations = conn.execute(
                "PRAGMA foreign_key_check(session_entries)"
            ).fetchall()
            if violations:
                raise RuntimeError(
                    "Session database contains invalid foreign key references"
                )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _validate_identifier(value: str, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _validate_non_negative_integer(value: int, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
        return value

    @classmethod
    def _normalize_workspace_id(cls, workspace_id: Optional[str]) -> str:
        if workspace_id is None:
            return cls.DEFAULT_WORKSPACE_ID
        return cls._validate_identifier(workspace_id, "workspace_id")

    @classmethod
    def _json_object(
        cls,
        value: Any,
        field: str,
        *,
        optional: bool,
    ) -> Optional[dict[str, Any]]:
        if value is None:
            return None if optional else {}
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            value = to_dict()
        if not isinstance(value, Mapping):
            raise TypeError(f"{field} must be a mapping or expose to_dict()")
        try:
            normalized = cls._copy_json_value(value, path=field)
            json.dumps(
                normalized,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            raise ValueError(f"{field} must contain only JSON values") from None
        return normalized

    @classmethod
    def _copy_json_value(cls, value: Any, *, path: str) -> Any:
        if isinstance(value, Mapping):
            copied: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"{path} keys must be strings")
                copied[key] = cls._copy_json_value(item, path=f"{path}.{key}")
            return copied
        if isinstance(value, (list, tuple)):
            return [
                cls._copy_json_value(item, path=f"{path}[{index}]")
                for index, item in enumerate(value)
            ]
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        raise TypeError(f"{path} is not JSON-compatible")

    @staticmethod
    def _serialize_json(value: Optional[dict[str, Any]]) -> Optional[str]:
        if value is None:
            return None
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _deserialize_json(
        raw_value: Optional[str],
        field: str,
        *,
        optional: bool,
    ) -> Optional[dict[str, Any]]:
        if raw_value is None:
            return None if optional else {}
        try:
            value = json.loads(raw_value)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Stored {field} is not valid JSON") from error
        if not isinstance(value, dict):
            raise RuntimeError(f"Stored {field} must be a JSON object")
        return value

    @classmethod
    def _entry_from_row(cls, row: sqlite3.Row) -> SessionEntry:
        return SessionEntry(
            id=int(row["id"]),
            sequence=int(row["sequence"]),
            workspace_id=row["workspace_id"],
            conversation_id=row["conversation_id"],
            entry_type=row["entry_type"],
            run_id=row["run_id"],
            turn_id=row["turn_id"],
            message=cls._deserialize_json(
                row["message_json"], "message", optional=True
            ),
            tool_call=cls._deserialize_json(
                row["tool_call_json"], "tool_call", optional=True
            ),
            tool_result=cls._deserialize_json(
                row["tool_result_json"], "tool_result", optional=True
            ),
            payload=cls._deserialize_json(
                row["payload_json"], "payload", optional=False
            )
            or {},
            created_at=row["created_at"],
        )

    def append(
        self,
        conversation_id: str,
        entry_type: str,
        *,
        run_id: str,
        turn_id: str,
        workspace_id: Optional[str] = None,
        message: Any = None,
        tool_call: Any = None,
        tool_result: Any = None,
        payload: Any = None,
    ) -> SessionEntry:
        """Atomically append one typed entry and return its persisted snapshot."""
        conversation_id = self._validate_identifier(conversation_id, "conversation_id")
        workspace_id = self._normalize_workspace_id(workspace_id)
        entry_type = self._validate_identifier(entry_type, "entry_type")
        run_id = self._validate_identifier(run_id, "run_id")
        turn_id = self._validate_identifier(turn_id, "turn_id")
        message_value = self._json_object(message, "message", optional=True)
        tool_call_value = self._json_object(tool_call, "tool_call", optional=True)
        tool_result_value = self._json_object(tool_result, "tool_result", optional=True)
        payload_value = self._json_object(payload, "payload", optional=False) or {}

        with self._connection(write=True) as conn:
            exists = conn.execute(
                """
                SELECT 1 FROM conversations
                WHERE id = ? AND workspace_id = ?
                """,
                (conversation_id, workspace_id),
            ).fetchone()
            if exists is None:
                raise KeyError(f"Unknown conversation: {conversation_id}")
            row = conn.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM session_entries
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            created_at = self._utc_now()
            cursor = conn.execute(
                """
                INSERT INTO session_entries(
                    sequence, workspace_id, conversation_id, entry_type,
                    run_id, turn_id, message_json, tool_call_json,
                    tool_result_json, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sequence,
                    workspace_id,
                    conversation_id,
                    entry_type,
                    run_id,
                    turn_id,
                    self._serialize_json(message_value),
                    self._serialize_json(tool_call_value),
                    self._serialize_json(tool_result_value),
                    self._serialize_json(payload_value),
                    created_at,
                ),
            )
            persisted = conn.execute(
                "SELECT * FROM session_entries WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._entry_from_row(persisted)

    def append_event(
        self,
        conversation_id: str,
        event: AgentEvent,
        *,
        workspace_id: Optional[str] = None,
    ) -> SessionEntry:
        """Adapt and append one runtime ``AgentEvent`` without provider data."""
        try:
            raw_event_type = event.type
            entry_type = getattr(raw_event_type, "value", raw_event_type)
            message = event.message.to_dict() if event.message is not None else None
            tool_call = None
            if event.tool_call_id or event.tool_name or event.tool_arguments:
                tool_call = {
                    "id": event.tool_call_id,
                    "name": event.tool_name,
                    "arguments": dict(event.tool_arguments),
                }
            tool_result = (
                dict(event.tool_result) if event.tool_result is not None else None
            )
            payload = {
                "event_timestamp": event.timestamp,
                "delta": event.delta,
                "error": event.error,
                "is_error": event.is_error,
                "metadata": dict(event.metadata),
            }
            run_id = event.run_id
            turn_id = event.turn_id
        except AttributeError as error:
            raise TypeError("event must be AgentEvent-compatible") from error

        return self.append(
            conversation_id,
            entry_type,
            run_id=run_id,
            turn_id=turn_id,
            workspace_id=workspace_id,
            message=message,
            tool_call=tool_call,
            tool_result=tool_result,
            payload=payload,
        )

    def list_entries(
        self,
        conversation_id: str,
        *,
        workspace_id: Optional[str] = None,
        after_sequence: int = 0,
        limit: Optional[int] = None,
    ) -> list[SessionEntry]:
        """List persisted entries in append order without changing session state."""
        conversation_id = self._validate_identifier(conversation_id, "conversation_id")
        workspace_id = self._normalize_workspace_id(workspace_id)
        after_sequence = self._validate_non_negative_integer(
            after_sequence, "after_sequence"
        )
        if limit is not None:
            limit = self._validate_non_negative_integer(limit, "limit")
            if limit == 0:
                return []
        sql_limit = -1 if limit is None else limit
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM session_entries
                WHERE workspace_id = ?
                  AND conversation_id = ?
                  AND sequence > ?
                ORDER BY sequence ASC, id ASC
                LIMIT ?
                """,
                (workspace_id, conversation_id, after_sequence, sql_limit),
            ).fetchall()
        return [self._entry_from_row(row) for row in rows]

    def replay(
        self,
        conversation_id: str,
        *,
        workspace_id: Optional[str] = None,
        after_sequence: int = 0,
        limit: Optional[int] = None,
    ) -> list[SessionEntry]:
        """Replay the immutable entry log from a per-conversation cursor."""
        return self.list_entries(
            conversation_id,
            workspace_id=workspace_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    def clear_conversation_entries(
        self,
        conversation_id: str,
        *,
        workspace_id: Optional[str] = None,
    ) -> int:
        """Delete the persisted runtime log for one conversation."""
        conversation_id = self._validate_identifier(conversation_id, "conversation_id")
        workspace_id = self._normalize_workspace_id(workspace_id)
        with self._connection(write=True) as conn:
            exists = conn.execute(
                """
                SELECT 1 FROM conversations
                WHERE id = ? AND workspace_id = ?
                """,
                (conversation_id, workspace_id),
            ).fetchone()
            if exists is None:
                raise KeyError(f"Unknown conversation: {conversation_id}")
            cursor = conn.execute(
                """
                DELETE FROM session_entries
                WHERE conversation_id = ? AND workspace_id = ?
                """,
                (conversation_id, workspace_id),
            )
        return cursor.rowcount
