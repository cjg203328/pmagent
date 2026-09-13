from __future__ import annotations

import pytest

from scripts import migrate_postgres


def test_migration_runner_requires_admin_url(monkeypatch):
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_ADMIN_URL"):
        migrate_postgres.main()


def test_migration_runner_rejects_invalid_role(monkeypatch):
    monkeypatch.setenv("DATABASE_ADMIN_URL", "postgresql://unused")
    monkeypatch.setenv("DATABASE_APP_ROLE", "role;drop")
    with pytest.raises(RuntimeError, match="invalid format"):
        migrate_postgres.main()
