"""Keep the repository layout required by the project contract executable."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_required_project_directories_exist_with_documentation() -> None:
    required = (
        "doc",
        "prototype",
        "project/frontend",
        "project/backend",
        "database",
        "utils",
    )

    for relative in required:
        directory = ROOT / relative
        assert directory.is_dir(), f"missing required directory: {relative}"
        assert (directory / "README.md").is_file(), (
            f"missing directory documentation: {relative}/README.md"
        )
