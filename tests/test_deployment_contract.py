"""Deployment security contract (P0).

The repository/image must never ship usable default Caddy credentials, and the
compose file must fail fast when the required auth variables are missing.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CADDYFILE = REPO_ROOT / "Caddyfile"
COMPOSE = REPO_ROOT / "docker-compose.yml"
README = REPO_ROOT / "README.md"
DOCKERFILE = REPO_ROOT / "Dockerfile"

# The previously-shipped default password hash must never reappear.
_FORBIDDEN_DEFAULT_HASH = "$2a$12$s/ueo9wac8n66IhDGVLKluOMBrsoikl1EuXZEKt4lTjVSovTvjAmu"


def test_caddyfile_has_no_default_credentials():
    text = CADDYFILE.read_text(encoding="utf-8")
    assert _FORBIDDEN_DEFAULT_HASH not in text
    assert "ArtPM@2026" not in text
    # Credentials must come from variables only.
    assert "{$BASIC_AUTH_USER}" in text
    assert "{$BASIC_AUTH_HASH}" in text


def test_compose_requires_basic_auth_variables():
    text = COMPOSE.read_text(encoding="utf-8")
    # `${VAR:?...}` makes compose fail at config time when the var is unset.
    assert "${BASIC_AUTH_USER:?" in text
    assert "${BASIC_AUTH_HASH:?" in text
    # No inline default hash remains in compose either.
    assert _FORBIDDEN_DEFAULT_HASH not in text


def test_compose_requires_database_and_observability_secrets():
    text = COMPOSE.read_text(encoding="utf-8")
    for variable in (
        "POSTGRES_PASSWORD",
        "POSTGRES_ADMIN_PASSWORD",
        "GRAFANA_ADMIN_PASSWORD",
    ):
        assert f"${{{variable}:?" in text
    assert "ARTPM_SQLITE_FALLBACK=false" in text
    assert "artpm-local" not in text
    assert "artpm-admin-local" not in text
    assert "GRAFANA_ADMIN_PASSWORD:-admin" not in text


def test_compose_forces_server_deployment_mode_for_app_and_api():
    text = COMPOSE.read_text(encoding="utf-8")

    assert text.count("ARTPM_DEPLOYMENT_MODE=server") == 2


def test_compose_redis_is_wired_and_failure_safe():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "REDIS_URL=redis://redis:6379/0" in text
    assert "redis:" in text


def test_postgres_migration_assets_are_packaged_and_app_role_is_not_owner():
    compose = COMPOSE.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "service_completed_successfully" in compose
    assert "DATABASE_APP_ROLE: artpm_app" in compose
    assert "ARTPM_ALEMBIC_DIR: /app/alembic" in compose
    assert "COPY alembic /app/alembic" in dockerfile
    assert "COPY scripts/migrate_postgres.py" in dockerfile


def test_compose_api_and_health_probe_contract():
    compose = COMPOSE.read_text(encoding="utf-8")
    caddy = CADDYFILE.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")

    assert "\n  artpm-api:\n" in compose
    assert 'command: ["python", "-m", "artpm_agent.api"]' in compose
    assert "ARTPM_API_PORT=8765" in compose
    assert "localhost:8765/ready" in compose
    assert "localhost:8501/_stcore/health" in compose
    assert "artpm-api:8765" in caddy
    assert "handle_path /api/*" in caddy
    for header in (
        "X-Gateway-Token",
        "X-Tenant-ID",
        "X-Workspace-ID",
        "X-Actor-ID",
    ):
        assert f"header_up {header}" in caddy
        assert f"header_up -{header}" in caddy
    for variable in (
        "ARTPM_GATEWAY_SHARED_SECRET",
        "ARTPM_GATEWAY_TENANT_ID",
        "ARTPM_GATEWAY_WORKSPACE_ID",
        "ARTPM_GATEWAY_ACTOR_ID",
    ):
        assert variable in compose
    assert "EXPOSE 8501 8765" in dockerfile

    # README is the user-facing port contract: Streamlit remains 8501 and the
    # REST gateway is 8765.  Keep the assertions broad enough for prose edits.
    assert "8501" in readme
    assert "8765" in readme
    assert "REST API" in readme or "REST API 网关" in readme
    assert "Caddy" in readme and "80/443" in readme


def test_grafana_is_not_exposed_on_a_public_interface():
    text = COMPOSE.read_text(encoding="utf-8")
    assert '"127.0.0.1:3000:3000"' in text
    assert '"3000:3000"' not in text


def test_compose_env_and_non_root_log_contract():
    compose = COMPOSE.read_text(encoding="utf-8")
    config = (REPO_ROOT / "artpm_agent" / "config.py").read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "./.env:/app/.env:ro" in compose
    assert "Path.cwd() / \".env\"" in config
    assert "ARTPM_ENV_FILE" in config
    assert "ARTPM_LOG_DIR" in config
    assert "./logs:/app/logs" in compose
    assert "ARTPM_LOG_DIR=/app/logs" in compose
    assert "USER appuser" in dockerfile
