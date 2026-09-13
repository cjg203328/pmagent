"""Run PostgreSQL migrations as the owner, then grant application DML only."""

from __future__ import annotations

import os
import re

from sqlalchemy import create_engine, text

from artpm_agent.database.migrate import upgrade_head


_ROLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def main() -> int:
    url = os.environ.get("DATABASE_ADMIN_URL", "").strip()
    role = os.environ.get("DATABASE_APP_ROLE", "artpm_app").strip()
    if not url:
        raise RuntimeError("DATABASE_ADMIN_URL is required")
    if not _ROLE.fullmatch(role):
        raise RuntimeError("DATABASE_APP_ROLE has an invalid format")

    engine = create_engine(url, pool_pre_ping=True)
    try:
        upgrade_head(engine)
        quoted_role = engine.dialect.identifier_preparer.quote(role)
        with engine.begin() as connection:
            connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {quoted_role}"))
            connection.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    f"IN SCHEMA public TO {quoted_role}"
                )
            )
            connection.execute(
                text(
                    f"GRANT USAGE, SELECT ON ALL SEQUENCES "
                    f"IN SCHEMA public TO {quoted_role}"
                )
            )
            connection.execute(
                text(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {quoted_role}"
                )
            )
            connection.execute(
                text(
                    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                    f"GRANT USAGE, SELECT ON SEQUENCES TO {quoted_role}"
                )
            )
            connection.execute(
                text(
                    f"REVOKE INSERT, UPDATE, DELETE ON TABLE alembic_version "
                    f"FROM {quoted_role}"
                )
            )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
