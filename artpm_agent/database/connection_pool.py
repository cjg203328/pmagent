"""Database connection pool manager.

Centralized connection pooling for all SQLite databases to reduce overhead
and support higher concurrency.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import create_engine, event, pool
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)


class DatabaseConnectionManager:
    """Singleton connection pool manager for all databases."""

    _instance: Optional[DatabaseConnectionManager] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.engines: Dict[str, Engine] = {}
        self.session_factories: Dict[str, sessionmaker] = {}
        self._engine_lock = threading.RLock()
        self._initialized = True
        logger.info("DatabaseConnectionManager initialized")

    def get_engine(
        self,
        db_path: str | Path,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30.0,
        pool_recycle: int = 3600,
        **kwargs: Any
    ) -> Engine:
        """Get or create engine with connection pooling.

        Args:
            db_path: Path to SQLite database file
            pool_size: Number of permanent connections in pool
            max_overflow: Max additional connections beyond pool_size
            pool_timeout: Seconds to wait for available connection
            pool_recycle: Seconds before recycling a connection
            **kwargs: Additional engine options

        Returns:
            SQLAlchemy Engine with connection pooling
        """
        db_path = Path(db_path).resolve()
        db_key = str(db_path)

        with self._engine_lock:
            if db_key in self.engines:
                return self.engines[db_key]

            # Ensure parent directory exists
            db_path.parent.mkdir(parents=True, exist_ok=True)

            # Create engine with connection pooling
            connect_args = {
                "check_same_thread": False,
                "timeout": 30.0,
            }
            connect_args.update(kwargs.get("connect_args", {}))

            engine = create_engine(
                f"sqlite:///{db_path.as_posix()}",
                poolclass=pool.QueuePool,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_recycle=pool_recycle,
                pool_pre_ping=True,  # Test connection before use
                connect_args=connect_args,
                **{k: v for k, v in kwargs.items() if k != "connect_args"}
            )

            # Enable WAL mode for better concurrency
            @event.listens_for(engine, "connect")
            def set_sqlite_pragma(dbapi_conn, connection_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
                cursor.execute("PRAGMA temp_store=MEMORY")
                cursor.close()

            self.engines[db_key] = engine
            logger.info(
                f"Created engine for {db_path.name} "
                f"(pool={pool_size}, max_overflow={max_overflow})"
            )

            return engine

    def get_session_factory(self, db_path: str | Path, **engine_kwargs: Any) -> sessionmaker:
        """Get or create session factory for a database.

        Args:
            db_path: Path to SQLite database file
            **engine_kwargs: Options passed to get_engine()

        Returns:
            SQLAlchemy sessionmaker
        """
        db_path = Path(db_path).resolve()
        db_key = str(db_path)

        with self._engine_lock:
            if db_key not in self.session_factories:
                engine = self.get_engine(db_path, **engine_kwargs)
                self.session_factories[db_key] = sessionmaker(
                    bind=engine,
                    expire_on_commit=False,
                )

            return self.session_factories[db_key]

    def get_session(self, db_path: str | Path, **engine_kwargs: Any) -> Session:
        """Get a new session from the pool.

        Args:
            db_path: Path to SQLite database file
            **engine_kwargs: Options passed to get_engine()

        Returns:
            SQLAlchemy Session
        """
        factory = self.get_session_factory(db_path, **engine_kwargs)
        return factory()

    def close_all(self) -> None:
        """Close all engines and clear pools."""
        with self._engine_lock:
            for db_key, engine in self.engines.items():
                try:
                    engine.dispose()
                    logger.info(f"Disposed engine for {db_key}")
                except Exception as e:
                    logger.warning(f"Error disposing engine {db_key}: {e}")

            self.engines.clear()
            self.session_factories.clear()

    def get_pool_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all connection pools (for monitoring).

        Returns:
            Dictionary mapping db_path to pool statistics
        """
        status = {}
        with self._engine_lock:
            for db_key, engine in self.engines.items():
                pool_obj = engine.pool
                status[db_key] = {
                    "size": pool_obj.size(),
                    "checked_in": pool_obj.checkedin(),
                    "checked_out": pool_obj.checkedout(),
                    "overflow": pool_obj.overflow(),
                    "connection_count": pool_obj.size() + pool_obj.overflow(),
                }
        return status


# Global singleton instance
_db_manager: Optional[DatabaseConnectionManager] = None


def get_db_manager() -> DatabaseConnectionManager:
    """Get global database connection manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseConnectionManager()
    return _db_manager


def get_session(db_path: str | Path, **engine_kwargs: Any) -> Session:
    """Convenience function to get a session.

    Args:
        db_path: Path to SQLite database file
        **engine_kwargs: Options passed to get_engine()

    Returns:
        SQLAlchemy Session
    """
    return get_db_manager().get_session(db_path, **engine_kwargs)


def close_all_connections() -> None:
    """Convenience function to close all database connections."""
    manager = get_db_manager()
    manager.close_all()

