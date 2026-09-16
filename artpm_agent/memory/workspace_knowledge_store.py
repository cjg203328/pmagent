"""Persistent, workspace-scoped knowledge and approved-rule storage."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock

from .embeddings import DeterministicEmbeddingProvider, EmbeddingProvider
from .knowledge import SCHEMA_VERSION as _SCHEMA_VERSION
from .knowledge_migrations import KnowledgeMigrationService
from .knowledge_projector import KnowledgeVectorProjector
from .knowledge_repository import (
    KnowledgeProposalConflictError,
    KnowledgeRepository,
)
from .knowledge_rule_service import KnowledgeRuleService
from .knowledge_search_service import KnowledgeSearchService
from .vector_store import VectorStore

_VECTOR_LOCKS_GUARD = Lock()
_VECTOR_SYNC_LOCKS: dict[str, RLock] = {}


def _vector_sync_lock(path: Path) -> RLock:
    key = str(path.resolve())
    with _VECTOR_LOCKS_GUARD:
        return _VECTOR_SYNC_LOCKS.setdefault(key, RLock())


class WorkspaceKnowledgeStore(
    KnowledgeMigrationService,
    KnowledgeRepository,
    KnowledgeRuleService,
    KnowledgeSearchService,
):
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
        embedding_provider: EmbeddingProvider | None = None,
        enable_vector_search: bool = True,
    ):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        self._enable_wal()
        self.embedding_provider = embedding_provider or DeterministicEmbeddingProvider()
        default_vector_path = (
            self.db_path.parent
            / "vector_store"
            / f"{self.db_path.stem}_workspace_knowledge"
        )
        self.vector_store_path = (
            Path(vector_store_path or default_vector_path).expanduser().resolve()
        )
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
        self._vector_projector = KnowledgeVectorProjector(self)
        self.sync_pending = bool(
            self._pending_index_count()
            or (self.vector_store is not None and self.vector_store.needs_rebuild)
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

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    # ===== Phase 2: 知识炼化（consolidation）写入接口 =====

    CONSOLIDATION_STATUSES = frozenset({"active", "superseded", "conflict"})


__all__ = ["KnowledgeProposalConflictError", "WorkspaceKnowledgeStore"]
