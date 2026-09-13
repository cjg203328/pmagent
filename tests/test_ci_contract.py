from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ci_keeps_functional_tests_and_coverage_in_separate_jobs():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pytest -q --no-cov" in workflow
    assert "coverage:" in workflow
    assert "--cov-fail-under=20" in workflow
    assert "focused modernization gate" in workflow
