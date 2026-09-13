#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_password="$ARTPM_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE artpm_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'artpm_app')
\gexec

ALTER ROLE artpm_app NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
GRANT CONNECT, TEMPORARY ON DATABASE artpm TO artpm_app;
SQL
