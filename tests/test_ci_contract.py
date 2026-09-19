import subprocess
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


def test_ci_uses_blocking_ruff_subset_and_covers_all_offline_benchmarks():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "ruff check artpm_agent tests --select E9,F63,F7,F82" in workflow
    assert "ruff check artpm_agent tests --output-format=github" not in workflow
    for benchmark_path in (
        "benchmarks/test_core_performance.py",
        "benchmarks/test_workspace_concurrency.py",
        "tests/test_phase1_optimizations.py",
    ):
        assert benchmark_path in workflow


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


def test_mypy_ratchet_has_strict_governed_packages_and_pinned_tool(
    monkeypatch,
):
    from scripts import mypy_ratchet

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    ratchet = (ROOT / "scripts" / "mypy_ratchet.py").read_text(encoding="utf-8")
    baseline = (ROOT / "scripts" / "mypy-baseline.txt").read_text(encoding="utf-8").strip()

    assert '"mypy==2.3.1"' in pyproject
    assert mypy_ratchet.STRICT_PACKAGES == (
        "artpm_agent/runtime",
        "artpm_agent/harness",
        "artpm_agent/api",
        "artpm_agent/tenancy",
    )
    assert mypy_ratchet.STRICT_OPTIONS == (
        "--strict",
        "--ignore-missing-imports",
        "--follow-imports=silent",
        "--no-pretty",
        "--show-error-codes",
        "--no-incremental",
    )
    assert "STAGED_MODULES" not in ratchet
    assert "ignore_errors" not in ratchet
    assert int(baseline) >= 0

    calls: list[list[str]] = []

    def fake_run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(mypy_ratchet, "_run_mypy", fake_run)

    assert mypy_ratchet.main() == 0
    assert calls[1] == [
        *mypy_ratchet.STRICT_PACKAGES,
        *mypy_ratchet.STRICT_OPTIONS,
    ]


def test_build_waits_for_all_release_quality_gates():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "needs: [test, coverage, types, security, postgres-rls, docker-smoke, slow-ui]" in workflow
