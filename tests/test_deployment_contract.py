"""Deployment security contract (P0).

The repository/image must never ship usable default Caddy credentials, and the
compose file must fail fast when the required auth variables are missing.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CADDYFILE = REPO_ROOT / "Caddyfile"
COMPOSE = REPO_ROOT / "docker-compose.yml"

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


def test_compose_redis_is_wired_and_failure_safe():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "REDIS_URL=redis://redis:6379/0" in text
    assert "redis:" in text
