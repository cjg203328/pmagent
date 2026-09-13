"""Workspace Wiki pages with a durable projection into the RAG knowledge store."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator, Mapping, Optional
import unicodedata
from uuid import uuid4

from .workspace_knowledge_store import WorkspaceKnowledgeStore
from artpm_agent.tenancy import TenantContextManager, WorkspaceAccessDenied


class WikiPageConflictError(RuntimeError):
    """Raised when a Wiki write loses its optimistic-concurrency check."""


class WorkspaceWikiStore:
    """Keep versioned Markdown Wiki pages and their RAG projection aligned.

    Wiki pages are the editable source of truth. Published pages are projected
    into ``WorkspaceKnowledgeStore`` with a stable ``source_type=wiki`` and
    ``source_id=page_id``. A failed projection is persisted as ``error`` and
    can be retried by ``reconcile()`` without losing the page revision.
    """

    SCHEMA_VERSION = 2
    DEFAULT_TENANT_ID = WorkspaceKnowledgeStore.DEFAULT_TENANT_ID
    DEFAULT_WORKSPACE_ID = WorkspaceKnowledgeStore.DEFAULT_WORKSPACE_ID
    MAX_TITLE_CHARS = 240
    MAX_SLUG_CHARS = 160
    MAX_MARKDOWN_CHARS = 2_000_000
    MAX_METADATA_BYTES = 64 * 1024
    MAX_SYNC_ERROR_CHARS = 1_000
    PAGE_STATUSES = frozenset({"draft", "published", "archived"})
    SYNC_STATUSES = frozenset({"not_indexed", "pending", "synced", "error"})

    def __init__(self, knowledge_store: WorkspaceKnowledgeStore):
        db_path = getattr(knowledge_store, "db_path", None)
        if knowledge_store is None or db_path is None:
            raise ValueError("WorkspaceWikiStore requires a knowledge store")
        self.knowledge_store = knowledge_store
        self.db_path = Path(db_path).expanduser().resolve()
        self._migrate()
        self._enable_wal()

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
                CREATE TABLE IF NOT EXISTS wiki_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version "
                "FROM wiki_schema_migrations"
            ).fetchone()
            current_version = int(row["version"])
            if current_version > self.SCHEMA_VERSION:
                raise RuntimeError(
                    "Wiki database schema is newer than this application supports"
                )
            if current_version < 1:
                connection.executescript(
                    """
                    CREATE TABLE wiki_pages (
                        id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL DEFAULT 'local',
                        workspace_id TEXT NOT NULL,
                        slug TEXT NOT NULL,
                        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                        markdown TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL
                            CHECK(status IN ('draft', 'published', 'archived')),
                        version INTEGER NOT NULL CHECK(version >= 1),
                        content_hash TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        rag_resource_id TEXT,
                        rag_version INTEGER,
                        rag_content_hash TEXT,
                        sync_status TEXT NOT NULL
                            CHECK(sync_status IN ('not_indexed', 'pending', 'synced', 'error')),
                        sync_error TEXT,
                        created_by TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        published_at TEXT,
                        archived_at TEXT,
                        UNIQUE(tenant_id, workspace_id, slug)
                    );

                    CREATE INDEX wiki_pages_workspace_status_updated_idx
                    ON wiki_pages(tenant_id, workspace_id, status, updated_at DESC, id DESC);

                    CREATE INDEX wiki_pages_pending_sync_idx
                    ON wiki_pages(tenant_id, workspace_id, sync_status, updated_at ASC, id ASC);

                    CREATE TABLE wiki_page_versions (
                        page_id TEXT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
                        tenant_id TEXT NOT NULL DEFAULT 'local',
                        version INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        markdown TEXT NOT NULL,
                        status TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        actor TEXT,
                        change_note TEXT,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(page_id, version)
                    );

                    CREATE TABLE wiki_sync_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tenant_id TEXT NOT NULL DEFAULT 'local',
                        workspace_id TEXT NOT NULL,
                        page_id TEXT NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
                        page_version INTEGER NOT NULL,
                        event_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX wiki_sync_events_page_idx
                    ON wiki_sync_events(page_id, id DESC);
                    """
                )
                connection.execute(
                    "INSERT INTO wiki_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (1, self._utc_now()),
                )
                current_version = 1

            if current_version < 2:
                self._migrate_tenant_scope(connection)
                connection.execute(
                    "INSERT INTO wiki_schema_migrations(version, applied_at) "
                    "VALUES (?, ?)",
                    (2, self._utc_now()),
                )

    @staticmethod
    def _migrate_tenant_scope(connection: sqlite3.Connection) -> None:
        """Rebuild the v1 Wiki tables with tenant-aware keys."""

        connection.execute(
            "ALTER TABLE wiki_sync_events RENAME TO wiki_sync_events_scope_legacy"
        )
        connection.execute(
            "ALTER TABLE wiki_page_versions RENAME TO wiki_page_versions_scope_legacy"
        )
        connection.execute(
            "ALTER TABLE wiki_pages RENAME TO wiki_pages_scope_legacy"
        )
        connection.executescript(
            """
            CREATE TABLE wiki_pages_scope_new (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'local',
                workspace_id TEXT NOT NULL,
                slug TEXT NOT NULL,
                title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                markdown TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL
                    CHECK(status IN ('draft', 'published', 'archived')),
                version INTEGER NOT NULL CHECK(version >= 1),
                content_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                rag_resource_id TEXT,
                rag_version INTEGER,
                rag_content_hash TEXT,
                sync_status TEXT NOT NULL
                    CHECK(sync_status IN ('not_indexed', 'pending', 'synced', 'error')),
                sync_error TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                published_at TEXT,
                archived_at TEXT,
                UNIQUE(tenant_id, workspace_id, slug)
            );

            CREATE TABLE wiki_page_versions_scope_new (
                page_id TEXT NOT NULL REFERENCES wiki_pages_scope_new(id) ON DELETE CASCADE,
                tenant_id TEXT NOT NULL DEFAULT 'local',
                version INTEGER NOT NULL,
                title TEXT NOT NULL,
                markdown TEXT NOT NULL,
                status TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                actor TEXT,
                change_note TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(page_id, version)
            );

            CREATE TABLE wiki_sync_events_scope_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL DEFAULT 'local',
                workspace_id TEXT NOT NULL,
                page_id TEXT NOT NULL REFERENCES wiki_pages_scope_new(id) ON DELETE CASCADE,
                page_version INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO wiki_pages_scope_new(
                id, tenant_id, workspace_id, slug, title, markdown, status,
                version, content_hash, metadata_json, rag_resource_id, rag_version,
                rag_content_hash, sync_status, sync_error, created_by, created_at,
                updated_at, published_at, archived_at
            )
            SELECT id, 'local', workspace_id, slug, title, markdown, status,
                version, content_hash, metadata_json, rag_resource_id, rag_version,
                rag_content_hash, sync_status, sync_error, created_by, created_at,
                updated_at, published_at, archived_at
            FROM wiki_pages_scope_legacy
            """
        )
        connection.execute(
            """
            INSERT INTO wiki_page_versions_scope_new(
                page_id, tenant_id, version, title, markdown, status, content_hash,
                metadata_json, actor, change_note, created_at
            )
            SELECT page_id, 'local', version, title, markdown, status, content_hash,
                metadata_json, actor, change_note, created_at
            FROM wiki_page_versions_scope_legacy
            """
        )
        connection.execute(
            """
            INSERT INTO wiki_sync_events_scope_new(
                id, tenant_id, workspace_id, page_id, page_version, event_type,
                status, payload_json, created_at
            )
            SELECT id, 'local', workspace_id, page_id, page_version, event_type,
                status, payload_json, created_at
            FROM wiki_sync_events_scope_legacy
            """
        )
        connection.execute("DROP TABLE wiki_sync_events_scope_legacy")
        connection.execute("DROP TABLE wiki_page_versions_scope_legacy")
        connection.execute("DROP TABLE wiki_pages_scope_legacy")
        connection.execute(
            "ALTER TABLE wiki_pages_scope_new RENAME TO wiki_pages"
        )
        connection.execute(
            "ALTER TABLE wiki_page_versions_scope_new RENAME TO wiki_page_versions"
        )
        connection.execute(
            "ALTER TABLE wiki_sync_events_scope_new RENAME TO wiki_sync_events"
        )
        connection.executescript(
            """
            CREATE INDEX wiki_pages_workspace_status_updated_idx
            ON wiki_pages(tenant_id, workspace_id, status, updated_at DESC, id DESC);
            CREATE INDEX wiki_pages_pending_sync_idx
            ON wiki_pages(tenant_id, workspace_id, sync_status, updated_at ASC, id ASC);
            CREATE INDEX wiki_sync_events_page_idx
            ON wiki_sync_events(tenant_id, page_id, id DESC);
            """
        )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    @classmethod
    def _required_text(cls, value: Any, field: str, *, max_chars: int) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field} is required")
        if len(text) > max_chars:
            raise ValueError(f"{field} exceeds {max_chars} characters")
        return text

    @staticmethod
    def _optional_text(value: Any, *, max_chars: int) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if len(text) > max_chars:
            raise ValueError(f"text exceeds {max_chars} characters")
        return text

    def _resolve_scope(
        self,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> tuple[str, str]:
        current = TenantContextManager.get_current()
        if current is not None:
            requested_workspace = workspace_id
            if requested_workspace in {None, "", self.DEFAULT_WORKSPACE_ID}:
                requested_workspace = None
            if tenant_id not in {None, "", current.tenant_id}:
                raise WorkspaceAccessDenied(
                    "tenant does not match the authenticated context"
                )
            resolved_workspace = current.require_workspace(requested_workspace)
            self._claim_legacy_scope(current.tenant_id, resolved_workspace)
            return current.tenant_id, resolved_workspace
        resolved_tenant = self._required_text(
            tenant_id or self.DEFAULT_TENANT_ID,
            "tenant_id",
            max_chars=128,
        )
        resolved_workspace = self._required_text(
            workspace_id or self.DEFAULT_WORKSPACE_ID,
            "workspace_id",
            max_chars=240,
        )
        return resolved_tenant, resolved_workspace

    def _claim_legacy_scope(self, tenant_id: str, workspace_id: str) -> bool:
        if tenant_id == self.DEFAULT_TENANT_ID:
            return False
        claimed = False
        with self._connection(write=True) as connection:
            explicit = connection.execute(
                """
                SELECT 1 FROM wiki_pages
                WHERE workspace_id = ? AND tenant_id NOT IN (?, ?)
                LIMIT 1
                """,
                (workspace_id, self.DEFAULT_TENANT_ID, tenant_id),
            ).fetchone()
            if explicit is not None:
                return False
            for table in ("wiki_pages", "wiki_page_versions", "wiki_sync_events"):
                if table == "wiki_page_versions":
                    cursor = connection.execute(
                        """
                        UPDATE wiki_page_versions
                        SET tenant_id = ?
                        WHERE tenant_id = ? AND page_id IN (
                            SELECT id FROM wiki_pages
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
                else:
                    cursor = connection.execute(
                        f"""
                        UPDATE {table}
                        SET tenant_id = ?
                        WHERE tenant_id = ? AND workspace_id = ?
                        """,
                        (tenant_id, self.DEFAULT_TENANT_ID, workspace_id),
                    )
                claimed = claimed or cursor.rowcount > 0
        return claimed

    @classmethod
    def _markdown(cls, value: Any) -> str:
        text = "" if value is None else str(value)
        if len(text) > cls.MAX_MARKDOWN_CHARS:
            raise ValueError(
                f"markdown exceeds {cls.MAX_MARKDOWN_CHARS} characters"
            )
        return text

    @classmethod
    def _metadata_json(cls, value: Optional[Mapping[str, Any]]) -> str:
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")
        try:
            encoded = json.dumps(
                dict(value),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as error:
            raise ValueError("metadata must be JSON serializable") from error
        if len(encoded.encode("utf-8")) > cls.MAX_METADATA_BYTES:
            raise ValueError("metadata exceeds the allowed size")
        return encoded

    @staticmethod
    def _decode_json(value: Any, fallback: Any) -> Any:
        try:
            decoded = json.loads(value or "")
        except (TypeError, ValueError):
            return fallback
        return decoded if isinstance(decoded, type(fallback)) else fallback

    @classmethod
    def _normalize_slug(cls, value: Any, *, title: str) -> str:
        candidate = str(value if value is not None else title).strip()
        candidate = unicodedata.normalize("NFKC", candidate).casefold()
        candidate = re.sub(r"[^\w\u4e00-\u9fff]+", "-", candidate)
        candidate = candidate.strip("-_")
        if not candidate:
            candidate = f"page-{uuid4().hex[:10]}"
        if len(candidate) > cls.MAX_SLUG_CHARS:
            candidate = candidate[: cls.MAX_SLUG_CHARS].rstrip("-_")
        return candidate

    @staticmethod
    def _content_hash(title: str, markdown: str, metadata_json: str) -> str:
        payload = "\0".join((title, markdown, metadata_json))
        return sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _searchable_text(title: str, markdown: str) -> str:
        """Retain readable Markdown text while removing link and heading syntax."""
        text = re.sub(r"(?s)\A---\s*\n.*?\n---\s*\n", "", markdown)
        text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
        text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
        text = re.sub(r"(?m)^\s{0,3}[-*+]\s+", "", text)
        text = re.sub(r"(?m)^\s{0,3}>\s?", "", text)
        text = text.replace("`", "")
        text = re.sub(r"[*_~]", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return f"{title}\n\n{text}".strip()

    @staticmethod
    def _validate_expected_version(expected_version: Optional[int]) -> None:
        if expected_version is None:
            return
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 1
        ):
            raise ValueError("expected_version must be a positive integer")

    def _page_row(
        self,
        connection: sqlite3.Connection,
        page_id: str,
        workspace_id: str,
        tenant_id: str,
    ) -> Optional[sqlite3.Row]:
        return connection.execute(
            """
            SELECT * FROM wiki_pages
            WHERE id = ? AND tenant_id = ? AND workspace_id = ?
            """,
            (page_id, tenant_id, workspace_id),
        ).fetchone()

    def _ensure_page_version(
        self,
        row: sqlite3.Row,
        expected_version: Optional[int],
    ) -> None:
        self._validate_expected_version(expected_version)
        if expected_version is not None and int(row["version"]) != expected_version:
            raise WikiPageConflictError("Wiki page changed before this update")

    def _record_event(
        self,
        connection: sqlite3.Connection,
        *,
        tenant_id: str,
        workspace_id: str,
        page_id: str,
        page_version: int,
        event_type: str,
        status: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO wiki_sync_events(
                tenant_id, workspace_id, page_id, page_version, event_type, status,
                payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tenant_id,
                workspace_id,
                page_id,
                page_version,
                event_type,
                status,
                self._metadata_json(payload),
                self._utc_now(),
            ),
        )

    def _insert_version(
        self,
        connection: sqlite3.Connection,
        *,
        page_id: str,
        tenant_id: str,
        version: int,
        title: str,
        markdown: str,
        status: str,
        content_hash: str,
        metadata_json: str,
        actor: Optional[str],
        change_note: Optional[str],
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO wiki_page_versions(
                page_id, tenant_id, version, title, markdown, status, content_hash,
                metadata_json, actor, change_note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                page_id,
                tenant_id,
                version,
                title,
                markdown,
                status,
                content_hash,
                metadata_json,
                actor,
                change_note,
                now,
            ),
        )

    @classmethod
    def _page_record(
        cls,
        row: sqlite3.Row,
        *,
        include_markdown: bool = True,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": row["id"],
            "tenant_id": row["tenant_id"],
            "workspace_id": row["workspace_id"],
            "slug": row["slug"],
            "title": row["title"],
            "status": row["status"],
            "version": int(row["version"]),
            "content_hash": row["content_hash"],
            "metadata": cls._decode_json(row["metadata_json"], {}),
            "rag": {
                "resource_id": row["rag_resource_id"],
                "version": row["rag_version"],
                "content_hash": row["rag_content_hash"],
            },
            "sync": {
                "status": row["sync_status"],
                "error": row["sync_error"],
            },
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "published_at": row["published_at"],
            "archived_at": row["archived_at"],
        }
        if include_markdown:
            record["markdown"] = row["markdown"]
        return record

    @classmethod
    def _version_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "version": int(row["version"]),
            "title": row["title"],
            "markdown": row["markdown"],
            "status": row["status"],
            "content_hash": row["content_hash"],
            "metadata": cls._decode_json(row["metadata_json"], {}),
            "actor": row["actor"],
            "change_note": row["change_note"],
            "created_at": row["created_at"],
        }

    def create_page(
        self,
        *,
        title: str,
        markdown: str = "",
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
        slug: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        actor: Optional[str] = None,
        publish: bool = False,
        change_note: Optional[str] = None,
    ) -> dict[str, Any]:
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        title = self._required_text(title, "title", max_chars=self.MAX_TITLE_CHARS)
        markdown = self._markdown(markdown)
        slug = self._normalize_slug(slug, title=title)
        metadata_json = self._metadata_json(metadata)
        actor = self._optional_text(actor, max_chars=240)
        change_note = self._optional_text(change_note, max_chars=1_000)
        status = "published" if publish else "draft"
        sync_status = "pending" if publish else "not_indexed"
        page_id = uuid4().hex
        now = self._utc_now()
        content_hash = self._content_hash(title, markdown, metadata_json)

        try:
            with self._connection(write=True) as connection:
                connection.execute(
                    """
                    INSERT INTO wiki_pages(
                        id, tenant_id, workspace_id, slug, title, markdown, status, version,
                        content_hash, metadata_json, sync_status, created_by,
                        created_at, updated_at, published_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        page_id,
                        tenant_id,
                        workspace_id,
                        slug,
                        title,
                        markdown,
                        status,
                        content_hash,
                        metadata_json,
                        sync_status,
                        actor,
                        now,
                        now,
                        now if publish else None,
                    ),
                )
                self._insert_version(
                    connection,
                    page_id=page_id,
                    tenant_id=tenant_id,
                    version=1,
                    title=title,
                    markdown=markdown,
                    status=status,
                    content_hash=content_hash,
                    metadata_json=metadata_json,
                    actor=actor,
                    change_note=change_note,
                    now=now,
                )
                self._record_event(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    page_id=page_id,
                    page_version=1,
                    event_type="wiki.created",
                    status=sync_status,
                    payload={"published": publish},
                )
        except sqlite3.IntegrityError as error:
            if "slug" in str(error):
                raise ValueError("a Wiki page already uses this slug") from error
            raise

        if publish:
            return self.sync_page(
                page_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        page = self.get_page(
            page_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        assert page is not None
        return page

    def get_page(
        self,
        page_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        page_id = self._required_text(page_id, "page_id", max_chars=240)
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        with self._connection() as connection:
            row = self._page_row(connection, page_id, workspace_id, tenant_id)
        return self._page_record(row) if row is not None else None

    def get_page_by_slug(
        self,
        slug: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        slug = self._normalize_slug(slug, title="page")
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM wiki_pages
                WHERE tenant_id = ? AND workspace_id = ? AND slug = ?
                """,
                (tenant_id, workspace_id, slug),
            ).fetchone()
        return self._page_record(row) if row is not None else None

    def list_pages(
        self,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        include_archived: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        if status is not None and status not in self.PAGE_STATUSES:
            raise ValueError("unsupported page status")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses = ["tenant_id = ?", "workspace_id = ?"]
        parameters: list[Any] = [tenant_id, workspace_id]
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status)
        elif not include_archived:
            clauses.append("status != 'archived'")
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM wiki_pages WHERE "
                + " AND ".join(clauses)
                + " ORDER BY updated_at DESC, id DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [self._page_record(row, include_markdown=False) for row in rows]

    def list_versions(
        self,
        page_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        page_id = self._required_text(page_id, "page_id", max_chars=240)
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connection() as connection:
            if self._page_row(connection, page_id, workspace_id, tenant_id) is None:
                return []
            rows = connection.execute(
                "SELECT * FROM wiki_page_versions WHERE page_id = ? AND tenant_id = ? "
                "ORDER BY version DESC LIMIT ?",
                (page_id, tenant_id, limit),
            ).fetchall()
        return [self._version_record(row) for row in rows]

    def update_page(
        self,
        page_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
        title: Optional[str] = None,
        markdown: Optional[str] = None,
        slug: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        actor: Optional[str] = None,
        change_note: Optional[str] = None,
        expected_version: Optional[int] = None,
        publish: bool = False,
    ) -> dict[str, Any]:
        page_id = self._required_text(page_id, "page_id", max_chars=240)
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        self._validate_expected_version(expected_version)
        actor = self._optional_text(actor, max_chars=240)
        change_note = self._optional_text(change_note, max_chars=1_000)

        with self._connection(write=True) as connection:
            current = self._page_row(connection, page_id, workspace_id, tenant_id)
            if current is None:
                raise KeyError("Wiki page was not found in this workspace")
            self._ensure_page_version(current, expected_version)
            if current["status"] == "archived":
                raise ValueError("archived Wiki pages cannot be edited")

            next_title = (
                self._required_text(
                    title, "title", max_chars=self.MAX_TITLE_CHARS
                )
                if title is not None
                else current["title"]
            )
            next_markdown = (
                self._markdown(markdown)
                if markdown is not None
                else current["markdown"]
            )
            next_slug = (
                self._normalize_slug(slug, title=next_title)
                if slug is not None
                else current["slug"]
            )
            next_metadata_json = (
                self._metadata_json(metadata)
                if metadata is not None
                else current["metadata_json"]
            )
            next_status = "published" if publish else current["status"]
            next_content_hash = self._content_hash(
                next_title, next_markdown, next_metadata_json
            )
            changed = any(
                (
                    next_title != current["title"],
                    next_markdown != current["markdown"],
                    next_slug != current["slug"],
                    next_metadata_json != current["metadata_json"],
                    next_status != current["status"],
                )
            )
            if changed:
                next_version = int(current["version"]) + 1
                now = self._utc_now()
                sync_status = (
                    "pending" if next_status == "published" else "not_indexed"
                )
                connection.execute(
                    """
                    UPDATE wiki_pages
                    SET slug = ?, title = ?, markdown = ?, status = ?, version = ?,
                        content_hash = ?, metadata_json = ?, sync_status = ?,
                        sync_error = NULL, updated_at = ?,
                        published_at = COALESCE(published_at, ?)
                    WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                    """,
                    (
                        next_slug,
                        next_title,
                        next_markdown,
                        next_status,
                        next_version,
                        next_content_hash,
                        next_metadata_json,
                        sync_status,
                        now,
                        now if next_status == "published" else None,
                        page_id,
                        tenant_id,
                        workspace_id,
                    ),
                )
                self._insert_version(
                    connection,
                    page_id=page_id,
                    tenant_id=tenant_id,
                    version=next_version,
                    title=next_title,
                    markdown=next_markdown,
                    status=next_status,
                    content_hash=next_content_hash,
                    metadata_json=next_metadata_json,
                    actor=actor,
                    change_note=change_note,
                    now=now,
                )
                self._record_event(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    page_id=page_id,
                    page_version=next_version,
                    event_type=("wiki.published" if publish else "wiki.updated"),
                    status=sync_status,
                    payload={"changed": True},
                )

        page = self.get_page(
            page_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        assert page is not None
        if page["status"] == "published" and (
            changed or page["sync"]["status"] != "synced"
        ):
            return self.sync_page(
                page_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
            )
        return page

    def publish_page(
        self,
        page_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> dict[str, Any]:
        return self.update_page(
            page_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor=actor,
            expected_version=expected_version,
            publish=True,
            change_note="Published to RAG knowledge",
        )

    def archive_page(
        self,
        page_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
        change_note: Optional[str] = None,
    ) -> dict[str, Any]:
        page_id = self._required_text(page_id, "page_id", max_chars=240)
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        self._validate_expected_version(expected_version)
        actor = self._optional_text(actor, max_chars=240)
        change_note = self._optional_text(change_note, max_chars=1_000)

        with self._connection(write=True) as connection:
            current = self._page_row(connection, page_id, workspace_id, tenant_id)
            if current is None:
                raise KeyError("Wiki page was not found in this workspace")
            self._ensure_page_version(current, expected_version)
            if current["status"] == "archived":
                page = self._page_record(current)
            else:
                next_version = int(current["version"]) + 1
                now = self._utc_now()
                connection.execute(
                    """
                    UPDATE wiki_pages
                    SET status = 'archived', version = ?, sync_status = 'pending',
                        sync_error = NULL, updated_at = ?, archived_at = ?
                    WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                    """,
                    (next_version, now, now, page_id, tenant_id, workspace_id),
                )
                self._insert_version(
                    connection,
                    page_id=page_id,
                    tenant_id=tenant_id,
                    version=next_version,
                    title=current["title"],
                    markdown=current["markdown"],
                    status="archived",
                    content_hash=current["content_hash"],
                    metadata_json=current["metadata_json"],
                    actor=actor,
                    change_note=change_note or "Archived Wiki page",
                    now=now,
                )
                self._record_event(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    page_id=page_id,
                    page_version=next_version,
                    event_type="wiki.archived",
                    status="pending",
                )
                page = None

        if page is not None:
            return page
        return self.sync_page(
            page_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    def restore_version(
        self,
        page_id: str,
        version: int,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
        actor: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> dict[str, Any]:
        page_id = self._required_text(page_id, "page_id", max_chars=240)
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")

        with self._connection() as connection:
            current = self._page_row(connection, page_id, workspace_id, tenant_id)
            if current is None:
                raise KeyError("Wiki page was not found in this workspace")
            if current["status"] == "archived":
                raise ValueError("archived Wiki pages must be republished separately")
            self._ensure_page_version(current, expected_version)
            target = connection.execute(
                """
                SELECT * FROM wiki_page_versions
                WHERE page_id = ? AND tenant_id = ? AND version = ?
                """,
                (page_id, tenant_id, version),
            ).fetchone()
            if target is None:
                raise KeyError("Wiki page version was not found")

        return self.update_page(
            page_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            title=target["title"],
            markdown=target["markdown"],
            metadata=self._decode_json(target["metadata_json"], {}),
            actor=actor,
            change_note=f"Restored Wiki version {version}",
            expected_version=expected_version,
            publish=current["status"] == "published",
        )

    def _record_sync_success(
        self,
        *,
        page_id: str,
        tenant_id: str,
        workspace_id: str,
        expected_page_version: int,
        resource: Optional[Mapping[str, Any]],
        event_type: str,
    ) -> dict[str, Any]:
        with self._connection(write=True) as connection:
            current = self._page_row(
                connection, page_id, workspace_id, tenant_id
            )
            if current is None:
                raise KeyError("Wiki page was removed during synchronization")
            if int(current["version"]) != expected_page_version:
                next_status = (
                    "pending"
                    if current["status"] in {"published", "archived"}
                    else "not_indexed"
                )
                connection.execute(
                    "UPDATE wiki_pages SET sync_status = ?, sync_error = NULL "
                    "WHERE id = ? AND tenant_id = ? AND workspace_id = ?",
                    (next_status, page_id, tenant_id, workspace_id),
                )
                self._record_event(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    page_id=page_id,
                    page_version=int(current["version"]),
                    event_type="rag.sync_superseded",
                    status=next_status,
                )
            else:
                rag_version = None
                rag_hash = None
                resource_id = current["rag_resource_id"]
                if resource is not None:
                    resource_id = resource.get("id") or resource_id
                    rag_version = resource.get("current_version")
                    version_record = resource.get("version")
                    if isinstance(version_record, Mapping):
                        rag_hash = version_record.get("content_hash")
                connection.execute(
                    """
                    UPDATE wiki_pages
                    SET rag_resource_id = ?, rag_version = ?, rag_content_hash = ?,
                        sync_status = 'synced', sync_error = NULL, updated_at = ?
                    WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                    """,
                    (
                        resource_id,
                        rag_version,
                        rag_hash,
                        self._utc_now(),
                        page_id,
                        tenant_id,
                        workspace_id,
                    ),
                )
                self._record_event(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    page_id=page_id,
                    page_version=expected_page_version,
                    event_type=event_type,
                    status="synced",
                    payload={"resource_id": resource_id, "rag_version": rag_version},
                )
        page = self.get_page(
            page_id, tenant_id=tenant_id, workspace_id=workspace_id
        )
        assert page is not None
        return page

    def _record_sync_error(
        self,
        *,
        page_id: str,
        tenant_id: str,
        workspace_id: str,
        expected_page_version: int,
        error: Exception,
    ) -> dict[str, Any]:
        message = str(error).strip() or error.__class__.__name__
        message = message[: self.MAX_SYNC_ERROR_CHARS]
        with self._connection(write=True) as connection:
            current = self._page_row(
                connection, page_id, workspace_id, tenant_id
            )
            if current is None:
                raise KeyError("Wiki page was removed during synchronization")
            if int(current["version"]) == expected_page_version:
                connection.execute(
                    """
                    UPDATE wiki_pages
                    SET sync_status = 'error', sync_error = ?, updated_at = ?
                    WHERE id = ? AND tenant_id = ? AND workspace_id = ?
                    """,
                    (
                        message,
                        self._utc_now(),
                        page_id,
                        tenant_id,
                        workspace_id,
                    ),
                )
                self._record_event(
                    connection,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    page_id=page_id,
                    page_version=expected_page_version,
                    event_type="rag.sync_failed",
                    status="error",
                    payload={"error": message},
                )
        page = self.get_page(
            page_id, tenant_id=tenant_id, workspace_id=workspace_id
        )
        assert page is not None
        return page

    def sync_page(
        self,
        page_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Synchronize one published or archived page with its RAG resource."""
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        page = self.get_page(
            page_id, tenant_id=tenant_id, workspace_id=workspace_id
        )
        if page is None:
            raise KeyError("Wiki page was not found in this workspace")
        page_version = int(page["version"])
        status = page["status"]
        if status == "draft":
            return page

        try:
            if status == "published":
                resource = self.knowledge_store.ingest_resource(
                    title=page["title"],
                    searchable_text=self._searchable_text(
                        page["title"], page["markdown"]
                    ),
                    resource_type="wiki",
                    source_type="wiki",
                    workspace_id=page["workspace_id"],
                    tenant_id=tenant_id,
                    source_uri=f"wiki://{page['workspace_id']}/{page['slug']}",
                    source_id=page["id"],
                    mime_type="text/markdown",
                    metadata={
                        "wiki": {
                            "page_id": page["id"],
                            "slug": page["slug"],
                            "page_version": page_version,
                        }
                    },
                    version_metadata={
                        "wiki_page_id": page["id"],
                        "wiki_page_version": page_version,
                        "wiki_slug": page["slug"],
                    },
                    created_by=page["created_by"],
                    change_note=f"Synchronized Wiki page version {page_version}",
                )
                return self._record_sync_success(
                    page_id=page["id"],
                    tenant_id=tenant_id,
                    workspace_id=page["workspace_id"],
                    expected_page_version=page_version,
                    resource=resource,
                    event_type="rag.synced",
                )

            resource = None
            resource_id = page["rag"]["resource_id"]
            if resource_id:
                    resource = self.knowledge_store.get_resource(
                    resource_id,
                    workspace_id=page["workspace_id"],
                    tenant_id=tenant_id,
                )
            if resource is None:
                resource = self.knowledge_store.get_resource_by_source(
                    source_type="wiki",
                    source_id=page["id"],
                    workspace_id=page["workspace_id"],
                    tenant_id=tenant_id,
                )
            if resource is not None:
                self.knowledge_store.archive_resource(
                    resource["id"],
                    workspace_id=page["workspace_id"],
                    tenant_id=tenant_id,
                )
            return self._record_sync_success(
                page_id=page["id"],
                tenant_id=tenant_id,
                workspace_id=page["workspace_id"],
                expected_page_version=page_version,
                resource=resource,
                event_type="rag.archived",
            )
        except Exception as error:  # noqa: BLE001 - retryable durable projection
            return self._record_sync_error(
                page_id=page["id"],
                tenant_id=tenant_id,
                workspace_id=page["workspace_id"],
                expected_page_version=page_version,
                error=error,
            )

    def reconcile(
        self,
        *,
        workspace_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, int]:
        """Retry failed or pending Wiki-to-RAG projections."""
        if workspace_id is not None:
            workspace_id = self._required_text(
                workspace_id, "workspace_id", max_chars=240
            )
        current = TenantContextManager.get_current()
        if current is not None:
            if tenant_id not in {None, "", current.tenant_id}:
                raise WorkspaceAccessDenied(
                    "tenant does not match the authenticated context"
                )
            tenant_id = current.tenant_id
            workspace_id = current.require_workspace(workspace_id)
        else:
            tenant_id = self._required_text(
                tenant_id or self.DEFAULT_TENANT_ID,
                "tenant_id",
                max_chars=128,
            )
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        clauses = [
            "tenant_id = ?",
            "status IN ('published', 'archived')",
            "sync_status != 'synced'",
        ]
        parameters: list[Any] = [tenant_id]
        if workspace_id is not None:
            clauses.append("workspace_id = ?")
            parameters.append(workspace_id)
        parameters.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, tenant_id, workspace_id FROM wiki_pages WHERE "
                + " AND ".join(clauses)
                + " ORDER BY updated_at ASC, id ASC LIMIT ?",
                parameters,
            ).fetchall()

        summary = {"attempted": 0, "synced": 0, "errors": 0}
        for row in rows:
            summary["attempted"] += 1
            page = self.sync_page(
                row["id"],
                tenant_id=row["tenant_id"],
                workspace_id=row["workspace_id"],
            )
            if page["sync"]["status"] == "synced":
                summary["synced"] += 1
            else:
                summary["errors"] += 1
        return summary

    def list_sync_events(
        self,
        page_id: str,
        *,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        tenant_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        page_id = self._required_text(page_id, "page_id", max_chars=240)
        tenant_id, workspace_id = self._resolve_scope(workspace_id, tenant_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connection() as connection:
            if self._page_row(connection, page_id, workspace_id, tenant_id) is None:
                return []
            rows = connection.execute(
                "SELECT * FROM wiki_sync_events "
                "WHERE page_id = ? AND tenant_id = ? AND workspace_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (page_id, tenant_id, workspace_id, limit),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "tenant_id": row["tenant_id"],
                "workspace_id": row["workspace_id"],
                "page_id": row["page_id"],
                "page_version": int(row["page_version"]),
                "event_type": row["event_type"],
                "status": row["status"],
                "payload": self._decode_json(row["payload_json"], {}),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
