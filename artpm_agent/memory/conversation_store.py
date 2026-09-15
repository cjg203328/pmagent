"""Persistent conversation and message storage backed by SQLite."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping, Optional
from uuid import uuid4


class ConversationStore:
    """Own an isolated SQLite database for chat conversations."""

    SCHEMA_VERSION = 3
    BUSY_TIMEOUT_MS = 10_000
    DEFAULT_WORKSPACE_ID = "local-default"
    DEFAULT_PROFILE_ID = "local-default"
    DEFAULT_WORKSPACE_NAME = "默认工作区"
    DEFAULT_TITLE = "新对话"
    MAX_TITLE_LENGTH = 80
    MAX_WORKSPACE_NAME_LENGTH = 80
    ALLOWED_ROLES = frozenset({"user", "assistant", "system", "tool"})
    ALLOWED_STATUSES = frozenset({"pending", "complete", "error"})

    def __init__(self, db_path: str | Path):
        path = Path(db_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        self._enable_wal()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.db_path,
            timeout=self.BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("PRAGMA synchronous = NORMAL")
            return conn
        except BaseException:
            conn.close()
            raise

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
                CREATE TABLE IF NOT EXISTS chat_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM chat_schema_migrations"
            ).fetchone()
            current_version = int(row["version"])
            if current_version > self.SCHEMA_VERSION:
                raise RuntimeError(
                    "Conversation database schema is newer than this application supports"
                )

            if current_version < 1:
                conn.execute(
                    """
                    CREATE TABLE conversations (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        role TEXT NOT NULL
                            CHECK(role IN ('user', 'assistant', 'system', 'tool')),
                        content TEXT NOT NULL CHECK(length(trim(content)) > 0),
                        status TEXT NOT NULL DEFAULT 'complete'
                            CHECK(status IN ('pending', 'complete', 'error')),
                        model_id TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                            ON DELETE CASCADE,
                        UNIQUE(conversation_id, turn_id, role)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX idx_conversations_updated_at
                    ON conversations(updated_at DESC)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX idx_messages_conversation_id
                    ON messages(conversation_id, id)
                    """
                )
                conn.execute(
                    "INSERT INTO chat_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (1, self._utc_now()),
                )
                current_version = 1

            if current_version < 2:
                self._migrate_to_workspace_schema(conn)
                conn.execute(
                    "INSERT INTO chat_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (2, self._utc_now()),
                )

            if current_version < 3:
                columns = {
                    item[1] for item in conn.execute("PRAGMA table_info(workspaces)")
                }
                if "tenant_id" not in columns:
                    conn.execute("ALTER TABLE workspaces ADD COLUMN tenant_id TEXT")
                # Keep the built-in local workspace stable. Other legacy rows
                # remain unbound and are claimed by their first trusted tenant.
                conn.execute(
                    "UPDATE workspaces SET tenant_id = ? "
                    "WHERE id = ? AND (tenant_id IS NULL OR trim(tenant_id) = '')",
                    ("local", self.DEFAULT_WORKSPACE_ID),
                )
                conn.execute(
                    "INSERT INTO chat_schema_migrations(version, applied_at) VALUES (?, ?)",
                    (3, self._utc_now()),
                )

            self._ensure_default_workspace(conn)
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    "Conversation database contains invalid foreign key references"
                )

    def _migrate_to_workspace_schema(self, conn: sqlite3.Connection) -> None:
        """Add workspace ownership while preserving v1 conversations and messages."""
        conn.execute(
            """
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL CHECK(length(trim(profile_id)) > 0),
                name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                -- A workspace is globally unique in storage. Binding it to a
                -- tenant here prevents reuse under a different identity.
                tenant_id TEXT,
                settings_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX idx_workspaces_profile_updated_at
            ON workspaces(profile_id, updated_at DESC, created_at DESC, id DESC)
            """
        )
        self._ensure_default_workspace(conn)

        conn.execute(
            """
            CREATE TABLE conversations_v2 (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL DEFAULT 'local-default',
                title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(workspace_id) REFERENCES workspaces(id)
                    ON DELETE RESTRICT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO conversations_v2(
                id, workspace_id, title, created_at, updated_at
            )
            SELECT id, ?, title, created_at, updated_at FROM conversations
            """,
            (self.DEFAULT_WORKSPACE_ID,),
        )
        conn.execute(
            """
            CREATE TABLE messages_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                role TEXT NOT NULL
                    CHECK(role IN ('user', 'assistant', 'system', 'tool')),
                content TEXT NOT NULL CHECK(length(trim(content)) > 0),
                status TEXT NOT NULL DEFAULT 'complete'
                    CHECK(status IN ('pending', 'complete', 'error')),
                model_id TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations_v2(id)
                    ON DELETE CASCADE,
                UNIQUE(conversation_id, turn_id, role)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO messages_v2(
                id, conversation_id, turn_id, role, content, status,
                model_id, metadata_json, created_at
            )
            SELECT id, conversation_id, turn_id, role, content, status,
                   model_id, metadata_json, created_at
            FROM messages
            """
        )

        conn.execute("DROP TABLE messages")
        conn.execute("DROP TABLE conversations")
        conn.execute("ALTER TABLE conversations_v2 RENAME TO conversations")
        conn.execute("ALTER TABLE messages_v2 RENAME TO messages")
        conn.execute(
            """
            CREATE INDEX idx_conversations_workspace_updated_at
            ON conversations(
                workspace_id, updated_at DESC, created_at DESC, id DESC
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX idx_messages_conversation_id
            ON messages(conversation_id, id)
            """
        )

    def _ensure_default_workspace(self, conn: sqlite3.Connection) -> None:
        now = self._utc_now()
        conn.execute(
            """
            INSERT OR IGNORE INTO workspaces(
                id, profile_id, name, tenant_id, settings_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '{}', ?, ?)
            """,
            (
                self.DEFAULT_WORKSPACE_ID,
                self.DEFAULT_PROFILE_ID,
                self.DEFAULT_WORKSPACE_NAME,
                "local",
                now,
                now,
            ),
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
    def _normalize_title(cls, title: str) -> str:
        title = cls._validate_identifier(title, "title")
        if len(title) > cls.MAX_TITLE_LENGTH:
            raise ValueError(f"title cannot exceed {cls.MAX_TITLE_LENGTH} characters")
        return title

    @classmethod
    def _normalize_workspace_id(cls, workspace_id: Optional[str]) -> str:
        if workspace_id is None:
            return cls.DEFAULT_WORKSPACE_ID
        return cls._validate_identifier(workspace_id, "workspace_id")

    @classmethod
    def _normalize_workspace_name(cls, name: str) -> str:
        name = cls._validate_identifier(name, "name")
        if len(name) > cls.MAX_WORKSPACE_NAME_LENGTH:
            raise ValueError(
                f"name cannot exceed {cls.MAX_WORKSPACE_NAME_LENGTH} characters"
            )
        return name

    @staticmethod
    def _serialize_metadata(metadata: Optional[Mapping[str, Any]]) -> str:
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        return json.dumps(dict(metadata), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _serialize_settings(settings: Mapping[str, Any]) -> str:
        if not isinstance(settings, Mapping):
            raise ValueError("settings must be a mapping")
        return json.dumps(dict(settings), ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _workspace_from_row(row: sqlite3.Row) -> dict[str, Any]:
        workspace = dict(row)
        raw_settings = workspace.pop("settings_json", "{}")
        try:
            settings = json.loads(raw_settings)
        except (TypeError, json.JSONDecodeError):
            settings = {}
        workspace["settings"] = settings if isinstance(settings, dict) else {}
        return workspace

    @staticmethod
    def _conversation_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> dict[str, Any]:
        message = dict(row)
        raw_metadata = message.pop("metadata_json", "{}")
        try:
            metadata = json.loads(raw_metadata)
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        message["metadata"] = metadata if isinstance(metadata, dict) else {}
        return message

    def get_workspace(
        self,
        workspace_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Return one workspace, defaulting to the persistent local workspace."""
        workspace_id = self._normalize_workspace_id(workspace_id)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
        return self._workspace_from_row(row) if row is not None else None

    def create_workspace(
        self,
        workspace_id: str,
        name: str,
        *,
        tenant_id: str = "local",
        profile_id: str = DEFAULT_PROFILE_ID,
        settings: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        """Create one tenant-owned workspace.

        Workspace creation is kept in ``ConversationStore`` because it owns
        workspace metadata and transcript foreign keys. Knowledge and vector
        stores consume the resulting scope; they do not create workspaces.
        """
        workspace_id = self._normalize_workspace_id(workspace_id)
        name = self._normalize_workspace_name(name)
        tenant_id = self._validate_identifier(tenant_id, "tenant_id")
        profile_id = self._validate_identifier(profile_id, "profile_id")
        settings_json = self._serialize_settings(settings or {})
        now = self._utc_now()
        with self._connection(write=True) as conn:
            conn.execute(
                """
                INSERT INTO workspaces(
                    id, profile_id, name, tenant_id, settings_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    profile_id,
                    name,
                    tenant_id,
                    settings_json,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - guarded by the insert above
            raise RuntimeError("workspace was not persisted")
        return self._workspace_from_row(row)

    def ensure_workspace_tenant(self, workspace_id: str, tenant_id: str) -> bool:
        """Atomically bind a workspace to one tenant identity.

        Existing unbound legacy workspaces are claimed by the first trusted
        request. Once bound, another tenant can never reuse the workspace.
        """
        workspace_id = self._normalize_workspace_id(workspace_id)
        tenant_id = self._validate_identifier(tenant_id, "tenant_id")
        with self._connection(write=True) as conn:
            row = conn.execute(
                "SELECT tenant_id FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
            if row is None:
                return False
            current = str(row["tenant_id"] or "").strip()
            if current:
                return current == tenant_id
            cursor = conn.execute(
                "UPDATE workspaces SET tenant_id = ?, updated_at = ? "
                "WHERE id = ? AND (tenant_id IS NULL OR trim(tenant_id) = '')",
                (tenant_id, self._utc_now(), workspace_id),
            )
            if cursor.rowcount:
                return True
            # A concurrent writer won the claim; read its committed owner.
            owner = conn.execute(
                "SELECT tenant_id FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
            return bool(owner is not None and owner["tenant_id"] == tenant_id)

    def list_workspaces(
        self,
        *,
        profile_id: Optional[str] = DEFAULT_PROFILE_ID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List workspaces for one profile, newest first; ``None`` lists all."""
        if profile_id is not None:
            profile_id = self._validate_identifier(profile_id, "profile_id")
        limit = self._validate_non_negative_integer(limit, "limit")
        offset = self._validate_non_negative_integer(offset, "offset")
        if limit == 0:
            return []
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM workspaces
                WHERE (? IS NULL OR profile_id = ?)
                ORDER BY updated_at DESC, created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (profile_id, profile_id, limit, offset),
            ).fetchall()
        return [self._workspace_from_row(row) for row in rows]

    def update_workspace_settings(
        self,
        workspace_id: str,
        settings: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Replace and persist a workspace's JSON settings."""
        workspace_id = self._normalize_workspace_id(workspace_id)
        settings_json = self._serialize_settings(settings)
        with self._connection(write=True) as conn:
            cursor = conn.execute(
                """
                UPDATE workspaces
                SET settings_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (settings_json, self._utc_now(), workspace_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown workspace: {workspace_id}")
            row = conn.execute(
                "SELECT * FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
        return self._workspace_from_row(row)

    def create_conversation(
        self,
        title: str = DEFAULT_TITLE,
        *,
        conversation_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Create and return a conversation."""
        title = self._normalize_title(title)
        workspace_id = self._normalize_workspace_id(workspace_id)
        conversation_id = (
            self._validate_identifier(conversation_id, "conversation_id")
            if conversation_id is not None
            else uuid4().hex
        )
        now = self._utc_now()
        with self._connection(write=True) as conn:
            workspace_exists = conn.execute(
                "SELECT 1 FROM workspaces WHERE id = ?",
                (workspace_id,),
            ).fetchone()
            if workspace_exists is None:
                raise KeyError(f"Unknown workspace: {workspace_id}")
            conn.execute(
                """
                INSERT INTO conversations(
                    id, workspace_id, title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, workspace_id, title, now, now),
            )
            row = conn.execute(
                """
                SELECT * FROM conversations
                WHERE id = ? AND workspace_id = ?
                """,
                (conversation_id, workspace_id),
            ).fetchone()
        return self._conversation_from_row(row)

    def list_conversations(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List conversations from most recently updated to oldest."""
        workspace_id = self._normalize_workspace_id(workspace_id)
        limit = self._validate_non_negative_integer(limit, "limit")
        offset = self._validate_non_negative_integer(offset, "offset")
        if limit == 0:
            return []
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM conversations
                WHERE workspace_id = ?
                ORDER BY updated_at DESC, created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (workspace_id, limit, offset),
            ).fetchall()
        return [self._conversation_from_row(row) for row in rows]

    def get_conversation(
        self,
        conversation_id: str,
        *,
        workspace_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Return one conversation, or ``None`` when it does not exist."""
        conversation_id = self._validate_identifier(conversation_id, "conversation_id")
        workspace_id = self._normalize_workspace_id(workspace_id)
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM conversations
                WHERE id = ? AND workspace_id = ?
                """,
                (conversation_id, workspace_id),
            ).fetchone()
        return self._conversation_from_row(row) if row is not None else None

    def rename_conversation(
        self,
        conversation_id: str,
        title: str,
        *,
        workspace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Rename and return an existing conversation."""
        conversation_id = self._validate_identifier(conversation_id, "conversation_id")
        title = self._normalize_title(title)
        workspace_id = self._normalize_workspace_id(workspace_id)
        with self._connection(write=True) as conn:
            cursor = conn.execute(
                """
                UPDATE conversations SET title = ?, updated_at = ?
                WHERE id = ? AND workspace_id = ?
                """,
                (title, self._utc_now(), conversation_id, workspace_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown conversation: {conversation_id}")
            row = conn.execute(
                """
                SELECT * FROM conversations
                WHERE id = ? AND workspace_id = ?
                """,
                (conversation_id, workspace_id),
            ).fetchone()
        return self._conversation_from_row(row)

    def delete_conversation(
        self,
        conversation_id: str,
        *,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """Delete a conversation and all of its messages."""
        conversation_id = self._validate_identifier(conversation_id, "conversation_id")
        workspace_id = self._normalize_workspace_id(workspace_id)
        with self._connection(write=True) as conn:
            cursor = conn.execute(
                "DELETE FROM conversations WHERE id = ? AND workspace_id = ?",
                (conversation_id, workspace_id),
            )
        return cursor.rowcount > 0

    def clear_conversation(
        self,
        conversation_id: str,
        *,
        workspace_id: Optional[str] = None,
    ) -> int:
        """Delete all messages while retaining the conversation; return the count."""
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
                "DELETE FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (self._utc_now(), conversation_id),
            )
        return cursor.rowcount

    def clear_messages(
        self,
        conversation_id: str,
        *,
        workspace_id: Optional[str] = None,
    ) -> int:
        """Alias for :meth:`clear_conversation`."""
        return self.clear_conversation(
            conversation_id,
            workspace_id=workspace_id,
        )

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        status: str = "complete",
        turn_id: Optional[str] = None,
        model_id: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        workspace_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Append and return a message, atomically touching its conversation."""
        conversation_id = self._validate_identifier(conversation_id, "conversation_id")
        workspace_id = self._normalize_workspace_id(workspace_id)
        if role not in self.ALLOWED_ROLES:
            raise ValueError(f"Unsupported message role: {role}")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        if status not in self.ALLOWED_STATUSES:
            raise ValueError(f"Unsupported message status: {status}")
        turn_id = (
            self._validate_identifier(turn_id, "turn_id")
            if turn_id is not None
            else uuid4().hex
        )
        if model_id is not None:
            model_id = self._validate_identifier(model_id, "model_id")
        metadata_json = self._serialize_metadata(metadata)
        now = self._utc_now()

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
                INSERT INTO messages(
                    conversation_id, turn_id, role, content, status,
                    model_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    turn_id,
                    role,
                    content,
                    status,
                    model_id,
                    metadata_json,
                    now,
                ),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            row = conn.execute(
                "SELECT * FROM messages WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._message_from_row(row)

    def delete_message(
        self,
        message_id: int,
        *,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """Delete a message and touch its parent conversation."""
        message_id = self._validate_non_negative_integer(message_id, "message_id")
        workspace_id = self._normalize_workspace_id(workspace_id)
        if message_id == 0:
            return False
        with self._connection(write=True) as conn:
            row = conn.execute(
                """
                SELECT messages.conversation_id
                FROM messages
                JOIN conversations
                  ON conversations.id = messages.conversation_id
                WHERE messages.id = ? AND conversations.workspace_id = ?
                """,
                (message_id, workspace_id),
            ).fetchone()
            if row is None:
                return False
            cursor = conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (self._utc_now(), row["conversation_id"]),
            )
        return cursor.rowcount > 0

    def list_messages(
        self,
        conversation_id: str,
        *,
        limit: Optional[int] = None,
        before_message_id: Optional[int] = None,
        workspace_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """List messages chronologically, optionally taking the latest subset."""
        conversation_id = self._validate_identifier(conversation_id, "conversation_id")
        workspace_id = self._normalize_workspace_id(workspace_id)
        if limit is not None:
            limit = self._validate_non_negative_integer(limit, "limit")
            if limit == 0:
                return []
        if before_message_id is not None:
            before_message_id = self._validate_non_negative_integer(
                before_message_id, "before_message_id"
            )
        sql_limit = -1 if limit is None else limit
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT messages.* FROM messages
                JOIN conversations
                  ON conversations.id = messages.conversation_id
                WHERE messages.conversation_id = ?
                  AND conversations.workspace_id = ?
                  AND (? IS NULL OR messages.id < ?)
                ORDER BY messages.id DESC
                LIMIT ?
                """,
                (
                    conversation_id,
                    workspace_id,
                    before_message_id,
                    before_message_id,
                    sql_limit,
                ),
            ).fetchall()
        rows.reverse()
        return [self._message_from_row(row) for row in rows]

    def build_context(
        self,
        conversation_id: str,
        *,
        max_messages: int = 12,
        max_chars: int = 12_000,
        before_message_id: Optional[int] = None,
        workspace_id: Optional[str] = None,
    ) -> list[dict[str, str]]:
        """Build a bounded, chronological LLM history for one conversation."""
        conversation_id = self._validate_identifier(conversation_id, "conversation_id")
        workspace_id = self._normalize_workspace_id(workspace_id)
        max_messages = self._validate_non_negative_integer(max_messages, "max_messages")
        max_chars = self._validate_non_negative_integer(max_chars, "max_chars")
        if max_messages == 0 or max_chars == 0:
            return []
        if before_message_id is not None:
            before_message_id = self._validate_non_negative_integer(
                before_message_id, "before_message_id"
            )

        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT messages.role, messages.content FROM messages
                JOIN conversations
                  ON conversations.id = messages.conversation_id
                WHERE messages.conversation_id = ?
                  AND conversations.workspace_id = ?
                  AND messages.status = ?
                  AND messages.role IN (?, ?)
                  AND (? IS NULL OR messages.id < ?)
                ORDER BY messages.id DESC
                LIMIT ?
                """,
                (
                    conversation_id,
                    workspace_id,
                    "complete",
                    "user",
                    "assistant",
                    before_message_id,
                    before_message_id,
                    max_messages,
                ),
            ).fetchall()

        selected: list[dict[str, str]] = []
        remaining_chars = max_chars
        for row in rows:
            content = row["content"]
            if len(content) <= remaining_chars:
                selected.append({"role": row["role"], "content": content})
                remaining_chars -= len(content)
                continue
            if not selected and remaining_chars:
                selected.append(
                    {"role": row["role"], "content": content[:remaining_chars]}
                )
            break

        selected.reverse()
        return selected
