"""Executable ownership and readiness contracts for authoritative stores."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoreContract:
    """Static ownership rules for one source-of-truth store."""

    name: str
    authority: str
    required_tables: tuple[str, ...]
    trusted_scope: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoreReadiness:
    """Normalized readiness result safe for API serialization."""

    status: str
    authority: str
    message: str | None = None
    missing_tables: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status,
            "authority": self.authority,
        }
        if self.message:
            result["message"] = self.message
        if self.missing_tables:
            result["missing_tables"] = list(self.missing_tables)
        return result


@runtime_checkable
class SQLiteStorePort(Protocol):
    db_path: str | Path


@runtime_checkable
class SqlAlchemyStorePort(Protocol):
    @property
    def engine(self) -> object: ...


AUTHORITATIVE_STORE_CONTRACTS: Mapping[str, StoreContract] = MappingProxyType(
    {
        "business_store": StoreContract(
            name="business_store",
            authority="projects, tasks, members, quotes and delivery facts",
            required_tables=("projects", "tasks", "team_members"),
            trusted_scope=("tenant_id", "workspace_id"),
        ),
        "conversation_store": StoreContract(
            name="conversation_store",
            authority="workspace metadata and user-visible conversation messages",
            required_tables=("workspaces", "conversations", "messages"),
            trusted_scope=("tenant_id", "workspace_id"),
        ),
        "session_store": StoreContract(
            name="session_store",
            authority="append-only turn, tool, approval and lifecycle events",
            required_tables=("session_entries",),
            trusted_scope=("workspace_id", "conversation_id"),
        ),
        "knowledge_store": StoreContract(
            name="knowledge_store",
            authority="workspace resources, versions, accepted rules and index outbox",
            required_tables=(
                "knowledge_resources",
                "knowledge_versions",
                "knowledge_rules",
                "knowledge_index_outbox",
            ),
            trusted_scope=("tenant_id", "workspace_id"),
        ),
    }
)


def probe_sqlite_store(
    store: SQLiteStorePort,
    contract: StoreContract,
    *,
    timeout_seconds: float = 1.0,
) -> StoreReadiness:
    """Probe an initialized SQLite authority without changing its schema."""

    path = Path(str(store.db_path)).expanduser().resolve()
    if not path.is_file():
        return StoreReadiness(
            status="error",
            authority=contract.authority,
            message="store database is missing",
        )
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        with closing(
            sqlite3.connect(uri, timeout=timeout_seconds, uri=True)
        ) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("SELECT 1").fetchone()
            placeholders = ", ".join("?" for _ in contract.required_tables)
            rows = connection.execute(
                "SELECT name FROM sqlite_master "
                f"WHERE type = 'table' AND name IN ({placeholders})",
                contract.required_tables,
            ).fetchall()
    except (OSError, sqlite3.Error):
        return StoreReadiness(
            status="error",
            authority=contract.authority,
            message="store connection failed",
        )
    found = {str(row[0]) for row in rows}
    missing = tuple(sorted(set(contract.required_tables) - found))
    if missing:
        return StoreReadiness(
            status="error",
            authority=contract.authority,
            message="store schema is incomplete",
            missing_tables=missing,
        )
    return StoreReadiness(status="ok", authority=contract.authority)


def probe_sqlalchemy_store(
    store: SqlAlchemyStorePort,
    contract: StoreContract,
) -> StoreReadiness:
    """Probe the configured business authority through its SQLAlchemy engine."""

    try:
        from sqlalchemy import inspect, text

        engine = store.engine
        connect = getattr(engine, "connect")
        with connect() as connection:
            connection.execute(text("SELECT 1"))
        inspector = inspect(engine)
        if inspector is None:
            raise RuntimeError("store engine does not support schema inspection")
        found = set(inspector.get_table_names())
    except Exception:  # noqa: BLE001 - readiness normalizes backend failures
        return StoreReadiness(
            status="error",
            authority=contract.authority,
            message="store connection failed",
        )
    missing = tuple(sorted(set(contract.required_tables) - found))
    if missing:
        return StoreReadiness(
            status="error",
            authority=contract.authority,
            message="store schema is incomplete",
            missing_tables=missing,
        )
    return StoreReadiness(status="ok", authority=contract.authority)


def probe_store(
    store: object,
    contract: StoreContract,
) -> StoreReadiness:
    if isinstance(store, SqlAlchemyStorePort):
        return probe_sqlalchemy_store(store, contract)
    if isinstance(store, SQLiteStorePort):
        return probe_sqlite_store(store, contract)
    return StoreReadiness(
        status="error",
        authority=contract.authority,
        message="store does not expose a supported readiness port",
    )


__all__ = [
    "AUTHORITATIVE_STORE_CONTRACTS",
    "SQLiteStorePort",
    "SqlAlchemyStorePort",
    "StoreContract",
    "StoreReadiness",
    "probe_sqlalchemy_store",
    "probe_sqlite_store",
    "probe_store",
]
