"""报价门禁脚本的退出码契约。

门禁的「通过」是机器信号，不是打印文案。因此「未验证」必须与「验证通过」用
不同的退出码区分——否则 CI 会在真值断言一条都没跑的情况下显示绿灯。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "quote_golden_check.py"

SPEC = importlib.util.spec_from_file_location("quote_golden_check", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
sys.modules["quote_golden_check"] = gate
SPEC.loader.exec_module(gate)


def test_exit_codes_are_distinct():
    codes = {
        gate.EXIT_OK,
        gate.EXIT_STRUCTURE_FAILED,
        gate.EXIT_VALUES_UNAVAILABLE,
        gate.EXIT_VALUES_NOT_RUN,
    }
    assert len(codes) == 4
    assert gate.EXIT_OK == 0
    assert all(code != 0 for code in codes - {0})


def test_values_without_cases_is_not_reported_as_pass(tmp_path, monkeypatch):
    """核心契约：没样本 = 未验证，退出码不得为 0。"""
    monkeypatch.setattr(gate, "CASES_DIR", tmp_path / "cases")

    code = gate._run_values()

    assert code == gate.EXIT_VALUES_NOT_RUN
    assert code != gate.EXIT_OK


def test_values_with_cases_is_still_not_a_pass(tmp_path, monkeypatch, capsys):
    """有样本但比对器未实现，同样不能算通过。"""
    cases = tmp_path / "cases"
    (cases / "g01-demo").mkdir(parents=True)
    monkeypatch.setattr(gate, "CASES_DIR", cases)

    code = gate._run_values()

    assert code == gate.EXIT_VALUES_UNAVAILABLE
    assert code != gate.EXIT_OK
    assert "不作为通过信号" in capsys.readouterr().out


def test_values_only_counts_directories(tmp_path, monkeypatch):
    """散落的文件不算样本，避免把一次误放的文件当成真值数据。"""
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "expected.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(gate, "CASES_DIR", cases)

    assert gate._existing_cases() == []
    assert gate._run_values() == gate.EXIT_VALUES_NOT_RUN


def test_values_lists_existing_sample_directories(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    (cases / "g02-seven").mkdir(parents=True)
    (cases / "g01-tencent").mkdir(parents=True)
    monkeypatch.setattr(gate, "CASES_DIR", cases)

    names = [path.name for path in gate._existing_cases()]
    assert names == ["g01-tencent", "g02-seven"]


def test_main_without_values_runs_structure_only(monkeypatch):
    called = {}

    def fake_structure():
        called["structure"] = True
        return gate.EXIT_OK

    def fake_values():
        called["values"] = True
        return gate.EXIT_VALUES_NOT_RUN

    monkeypatch.setattr(gate, "_run_structure", fake_structure)
    monkeypatch.setattr(gate, "_run_values", fake_values)

    assert gate.main([]) == gate.EXIT_OK
    assert called == {"structure": True}


def test_main_with_values_does_not_run_structure(monkeypatch):
    called = {}

    def fake_structure():
        called["structure"] = True
        return gate.EXIT_OK

    monkeypatch.setattr(gate, "_run_structure", fake_structure)
    monkeypatch.setattr(gate, "_run_values", lambda: gate.EXIT_VALUES_NOT_RUN)

    assert gate.main(["--values"]) == gate.EXIT_VALUES_NOT_RUN
    assert "structure" not in called


def test_cli_values_exit_code_is_non_zero():
    """端到端：真跑一次子进程，确认 CLI 契约与函数契约一致。"""
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--values"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    cases_dir = PROJECT_ROOT / "tests" / "golden" / "cases"
    has_samples = cases_dir.is_dir() and any(p.is_dir() for p in cases_dir.iterdir())

    if has_samples:
        assert process.returncode == gate.EXIT_VALUES_UNAVAILABLE
    else:
        assert process.returncode == gate.EXIT_VALUES_NOT_RUN
    assert process.returncode != 0


def test_cli_structure_run_passes():
    process = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == gate.EXIT_OK, process.stdout + process.stderr
    assert "13 passed" in process.stdout or "passed" in process.stdout


@pytest.mark.parametrize("flag", ["--help"])
def test_cli_help_documents_exit_code(flag):
    process = subprocess.run(
        [sys.executable, str(SCRIPT), flag],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 0
    assert "退出码" in process.stdout
