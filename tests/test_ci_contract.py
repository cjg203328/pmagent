from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ci_keeps_functional_tests_and_coverage_in_separate_jobs():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pytest -q --no-cov" in workflow
    assert "coverage:" in workflow
    assert "--cov-fail-under=20" in workflow
    assert "focused modernization gate" in workflow


def test_ci_quality_checks_are_blocking_and_use_ratcheted_types():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "python scripts/mypy_ratchet.py" in workflow
    assert "types:" in workflow
    assert "name: Staged mypy ratchet" in workflow
    assert "python-version: '3.11'" in workflow
    assert "continue-on-error: true" not in workflow
    assert "bandit -r artpm_agent -lll -iii" in workflow
    assert "pip-audit -r requirements-security.txt" in workflow
    assert "postgres-rls:" in workflow
    assert "docker-smoke:" in workflow
    assert "slow-ui:" in workflow


def test_ci_postgres_and_compose_smoke_jobs_are_fail_closed():
    """Keep integration jobs from silently becoming skipped or best-effort."""
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    # PostgreSQL RLS must run against the non-owner role after an explicit
    # migration; the test fixture skips when either URL is absent.
    for value in (
        "DATABASE_ADMIN_URL:",
        "DATABASE_APP_ROLE: artpm_app",
        "ARTPM_TEST_POSTGRES_ADMIN_URL:",
        "ARTPM_TEST_POSTGRES_URL:",
        "ART_ENABLE_INTEGRATION: \"1\"",
        "ARTPM_SQLITE_FALLBACK: \"false\"",
        "CREATE ROLE artpm_app LOGIN",
        "python scripts/migrate_postgres.py",
    ):
        assert value in workflow

    # Compose smoke must assert readiness (not merely start), verify the
    # non-root/mounted runtime contract, and always clean up its stack.
    for value in (
        "docker compose config --quiet",
        "docker compose up -d --build",
        "api_ready=0",
        "API readiness probe did not pass",
        'test \"$(id -u)\" = \"10001\"',
        "test -r /app/.env",
        "test ! -w /app/.env",
        "test -w /app/logs",
        "ARTPM_LOG_DIR",
        "https://localhost/api/ready",
        "X-Workspace-ID",
        "if: failure()",
        "run: docker compose down -v",
    ):
        assert value in workflow


def test_mypy_ratchet_has_a_strict_staged_boundary_and_pinned_tool():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ratchet = (ROOT / "scripts" / "mypy_ratchet.py").read_text(encoding="utf-8")
    baseline = (ROOT / "scripts" / "mypy-baseline.txt").read_text(encoding="utf-8").strip()

    assert '"mypy==2.3.1"' in pyproject
    assert "STAGED_MODULES" in ratchet
    assert '"--strict"' in ratchet
    assert '"--follow-imports=skip"' in ratchet
    assert baseline == "363"


def test_build_waits_for_all_release_quality_gates():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "needs: [test, coverage, types, security, postgres-rls, docker-smoke, slow-ui]" in workflow
