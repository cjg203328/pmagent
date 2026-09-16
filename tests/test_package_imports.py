import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_package_qualified_imports_work_without_pytest_path_injection():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import artpm_agent.config, artpm_agent.agent, "
                "artpm_agent.providers, artpm_agent.harness, "
                "artpm_agent.internal.chat_harness_integration"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_legacy_agent_shim_resolves_to_package_class():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import agent; from artpm_agent.agent import ArtPMAgent; "
                "assert agent.ArtPMAgent is ArtPMAgent"
            ),
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_built_wheel_contains_and_imports_application_modules(tmp_path):
    wheel_dir = tmp_path / "wheel"
    install_dir = tmp_path / "installed"
    wheel_dir.mkdir()

    uv = shutil.which("uv")
    assert uv, "uv is required to build the package in the managed test runtime"
    build = subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(wheel_dir), str(PROJECT_ROOT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert build.returncode == 0, build.stderr
    wheel = next(wheel_dir.glob("artpm_agent-*.whl"))

    install_dir.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(install_dir)

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(install_dir)
    environment["ARTPM_INSTALLED_ROOT"] = str(install_dir)
    smoke = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os; from pathlib import Path; "
                "import artpm_agent, artpm_agent.app, artpm_agent.ui_helpers, "
                "artpm_agent.ui_style, artpm_agent.views.chat, app, agent; "
                "from artpm_agent.main import main; "
                "root = Path(os.environ['ARTPM_INSTALLED_ROOT']).resolve(); "
                "assert Path(artpm_agent.__file__).resolve().is_relative_to(root)"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert smoke.returncode == 0, smoke.stderr
