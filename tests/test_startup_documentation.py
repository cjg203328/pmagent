"""Startup documentation must not advertise deleted launchers."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_documented_startup_commands_exist() -> None:
    quickstart = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    troubleshooting = (ROOT / "docs" / "TROUBLESHOOTING.md").read_text(
        encoding="utf-8"
    )

    assert "start_optimized" not in quickstart
    assert "start_optimized" not in troubleshooting
    assert "start.bat" in quickstart
    assert "start.sh" in quickstart
    assert "start.sh" in troubleshooting
    assert (ROOT / "start.bat").is_file()
    assert (ROOT / "start.sh").is_file()
    assert (ROOT / "start_with_checks.py").is_file()


def test_optimization_verifier_rejects_stale_launcher_names() -> None:
    verifier = (ROOT / "scripts" / "verify_optimization.py").read_text(
        encoding="utf-8"
    )

    assert 'stale = "start_optimized" in text' in verifier
    assert '"start.bat"' in verifier
    assert '"start.sh"' in verifier
