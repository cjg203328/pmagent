"""
SQLite Database Manager
"""
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from contextlib import contextmanager


class SQLiteManager:
    """SQLite Database Manager"""

    TABLE_COLUMNS = {
        "projects": {"id", "name", "client_name", "status", "start_date", "deadline", "total_quote", "total_cost", "profit_rate", "created_at", "updated_at"},
        "tasks": {"id", "project_id", "name", "category", "estimated_days", "actual_days", "status", "deadline", "priority", "created_at"},
        "task_assignments": {"id", "task_id", "staff_id", "workload_ratio", "assigned_at"},
        "staff": {"id", "name", "level", "skills", "daily_cost", "contact_wecom", "status", "created_at"},
        "quotes": {"id", "project_id", "document_type", "file_path", "file_hash", "parsed_data", "profit_analysis", "confidence", "created_at"},
        "progress_updates": {"id", "task_id", "progress", "note", "updated_by", "updated_at"},
        "reminders": {"id", "task_id", "reminder_type", "content", "recipients", "channel", "sent_status", "scheduled_at", "sent_at"},
        "documents": {"id", "tenant_id", "workspace_id", "document_type", "source", "file_path", "file_hash", "extracted_data", "raw_text", "confidence", "created_at"},
        "operation_logs": {"id", "operation", "skill_name", "inputs", "outputs", "success", "error", "created_at"},
    }
    _PRIMARY_SCHEMA_DROP_ORDER = (
        "task_assignments",
        "progress_updates",
        "reminders",
        "quotes",
        "tasks",
        "staff",
        "documents",
        "operation_logs",
        "projects",
    )

    def __init__(self, db_path: str, *, initialize_schema: bool = True):
        """
        Initialize database manager

        Args:
            db_path: Database file path
        """
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # Auxiliary stores (episodes, feedback, strategies, reflection runs)
        # use this connection helper but own their schema.  Avoid creating the
        # unrelated project/legacy tables in every auxiliary database.
        if initialize_schema:
            self._init_schema()

    @contextmanager
    def get_connection(self):
        """Get database connection context manager"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        finally:
            conn.close()

    def _init_schema(self):
        """Initialize database schema"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Projects table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    client_name TEXT,
                    status TEXT DEFAULT 'pending',
                    start_date DATE,
                    deadline DATE,
                    total_quote REAL,
                    total_cost REAL,
                    profit_rate REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Tasks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT,
                    estimated_days REAL,
                    actual_days REAL,
                    status TEXT DEFAULT 'pending',
                    deadline DATE,
                    priority TEXT DEFAULT 'normal',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                )
            """)

            # Task assignments table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS task_assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    staff_id TEXT NOT NULL,
                    workload_ratio REAL DEFAULT 1.0,
                    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id),
                    FOREIGN KEY (staff_id) REFERENCES staff(id)
                )
            """)

            # Staff table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS staff (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    level TEXT,
                    skills TEXT,
                    daily_cost REAL,
                    contact_wecom TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Quotes table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS quotes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    document_type TEXT,
                    file_path TEXT,
                    file_hash TEXT,
                    parsed_data TEXT,
                    profit_analysis TEXT,
                    confidence REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES projects(id)
                )
            """)

            # Progress updates table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS progress_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    progress REAL,
                    note TEXT,
                    updated_by TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                )
            """)

            # Reminders table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT,
                    reminder_type TEXT,
                    content TEXT,
                    recipients TEXT,
                    channel TEXT,
                    sent_status TEXT DEFAULT 'pending',
                    scheduled_at TIMESTAMP,
                    sent_at TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                )
            """)

            # Documents table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'local',
                    workspace_id TEXT NOT NULL DEFAULT 'local-default',
                    document_type TEXT,
                    source TEXT,
                    file_path TEXT,
                    file_hash TEXT,
                    extracted_data TEXT,
                    raw_text TEXT,
                    confidence REAL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            self._ensure_documents_workspace_scope(conn)

            # Operation logs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS operation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT,
                    skill_name TEXT,
                    inputs TEXT,
                    outputs TEXT,
                    success BOOLEAN,
                    error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project_status ON tasks(project_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(document_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_workspace_type ON documents(workspace_id, document_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_documents_tenant_workspace_type ON documents(tenant_id, workspace_id, document_type)")

    @staticmethod
    def _ensure_documents_workspace_scope(conn: sqlite3.Connection) -> None:
        """Migrate the legacy document store before accepting scoped queries."""

        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        if "workspace_id" not in columns:
            conn.execute(
                "ALTER TABLE documents ADD COLUMN workspace_id TEXT NOT NULL "
                "DEFAULT 'local-default'"
            )
        if "tenant_id" not in columns:
            conn.execute(
                "ALTER TABLE documents ADD COLUMN tenant_id TEXT NOT NULL "
                "DEFAULT 'local'"
            )

    def remove_empty_primary_schema_scaffold(self) -> bool:
        """Remove the old auto-created business schema from an auxiliary DB.

        Older auxiliary stores inherited all primary tables from this manager.
        Cleanup is deliberately fail-closed: every expected table must have the
        exact known columns, contain no rows, and have no custom trigger.  If
        any check fails, no table is dropped and user data is preserved.
        """
        expected_tables = set(self.TABLE_COLUMNS)
        with self.get_connection() as conn:
            existing_tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if not expected_tables <= existing_tables:
                return False

            placeholders = ", ".join("?" for _ in expected_tables)
            trigger = conn.execute(
                "SELECT 1 FROM sqlite_master "
                f"WHERE type = 'trigger' AND tbl_name IN ({placeholders}) LIMIT 1",
                tuple(sorted(expected_tables)),
            ).fetchone()
            if trigger is not None:
                return False

            # An auxiliary table with a foreign key into the scaffold would
            # make dropping a parent unsafe.  Preserve the whole schema in
            # that case instead of partially migrating it.
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall():
                table = str(row[0])
                if table.startswith("sqlite_") or table in expected_tables:
                    continue
                foreign_keys = conn.execute(
                    f'PRAGMA foreign_key_list("{table}")'
                ).fetchall()
                if any(str(foreign_key[2]) in expected_tables for foreign_key in foreign_keys):
                    return False

            for table, expected_columns in self.TABLE_COLUMNS.items():
                columns = {
                    str(row[1])
                    for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
                }
                if columns != expected_columns:
                    return False
                if conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                    return False

            for table in self._PRIMARY_SCHEMA_DROP_ORDER:
                conn.execute(f'DROP TABLE "{table}"')
        return True

    def _validate_identifiers(self, table: str, columns=None) -> None:
        allowed = self.TABLE_COLUMNS.get(table)
        if allowed is None:
            raise ValueError(f"Unknown table: {table}")
        invalid = set(columns or ()) - allowed
        if invalid:
            raise ValueError(f"Unknown columns for {table}: {', '.join(sorted(invalid))}")

    def insert(self, table: str, data: Dict[str, Any]) -> str:
        """
        Insert record

        Args:
            table: Table name
            data: Data dictionary

        Returns:
            Record ID
        """
        self._validate_identifiers(table, data.keys())
        if not data:
            raise ValueError("Insert data cannot be empty")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            columns = ', '.join(data.keys())
            placeholders = ', '.join(['?' for _ in data])
            query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
            cursor.execute(query, tuple(data.values()))
            return data.get('id', str(cursor.lastrowid))

    def update(self, table: str, record_id: str, data: Dict[str, Any]) -> bool:
        """
        Update record

        Args:
            table: Table name
            record_id: Record ID
            data: Data dictionary

        Returns:
            Success status
        """
        self._validate_identifiers(table, data.keys())
        if not data:
            return False
        with self.get_connection() as conn:
            cursor = conn.cursor()
            set_clause = ', '.join([f"{k} = ?" for k in data.keys()])
            query = f"UPDATE {table} SET {set_clause} WHERE id = ?"
            cursor.execute(query, tuple(list(data.values()) + [record_id]))
            return cursor.rowcount > 0

    def delete(self, table: str, record_id: str) -> bool:
        """
        Delete record

        Args:
            table: Table name
            record_id: Record ID

        Returns:
            Success status
        """
        self._validate_identifiers(table)
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM {table} WHERE id = ?", (record_id,))
            return cursor.rowcount > 0

    def query(self, table: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict]:
        """
        Query records

        Args:
            table: Table name
            filters: Filter conditions

        Returns:
            List of records
        """
        self._validate_identifiers(table, filters.keys() if filters else ())
        with self.get_connection() as conn:
            cursor = conn.cursor()

            if filters:
                where_clause = ' AND '.join([f"{k} = ?" for k in filters.keys()])
                query = f"SELECT * FROM {table} WHERE {where_clause}"
                cursor.execute(query, tuple(filters.values()))
            else:
                cursor.execute(f"SELECT * FROM {table}")

            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def query_sql(self, sql: str, params: Optional[Tuple] = None) -> List[Tuple]:
        """
        Execute raw SQL query

        Args:
            sql: SQL query
            params: Query parameters

        Returns:
            Query results
        """
        statement = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if statement not in {"SELECT", "WITH"}:
            raise ValueError("query_sql only accepts read-only statements")
        with self.get_connection() as conn:
            conn.execute("PRAGMA query_only = ON")
            cursor = conn.cursor()
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            return cursor.fetchall()

    def get_by_id(self, table: str, record_id: str) -> Optional[Dict]:
        """
        Get record by ID

        Args:
            table: Table name
            record_id: Record ID

        Returns:
            Record or None
        """
        results = self.query(table, {"id": record_id})
        return results[0] if results else None

    def get_by_ids(self, table: str, record_ids: Iterable[Any]) -> List[Dict]:
        """Return records for several IDs with one connection and batched SQL.

        SQLite limits the number of bound parameters per statement. Chunking
        keeps this helper useful for callers beyond the small chat top-k while
        preserving the caller's ID order in the returned rows.
        """
        self._validate_identifiers(table, {"id"})
        if isinstance(record_ids, (str, bytes)):
            ids = [record_ids]
        else:
            ids = list(dict.fromkeys(record_ids or ()))
        if not ids:
            return []

        found: Dict[str, Dict[str, Any]] = {}
        with self.get_connection() as conn:
            for start in range(0, len(ids), 500):
                batch = ids[start : start + 500]
                placeholders = ", ".join("?" for _ in batch)
                rows = conn.execute(
                    f"SELECT * FROM {table} WHERE id IN ({placeholders})",
                    tuple(batch),
                ).fetchall()
                for row in rows:
                    found[str(row["id"])] = dict(row)

        return [found[str(record_id)] for record_id in ids if str(record_id) in found]
