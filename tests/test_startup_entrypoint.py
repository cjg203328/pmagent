import json
import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import start_with_checks
from artpm_agent.voice import VoiceSettings


def test_child_processes_force_utf8_without_overriding_operator_values(monkeypatch):
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    environment = start_with_checks._subprocess_environment()

    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONIOENCODING"] == "utf-8"

    monkeypatch.setenv("PYTHONIOENCODING", "utf-8:backslashreplace")
    assert start_with_checks._subprocess_environment()["PYTHONIOENCODING"] == (
        "utf-8:backslashreplace"
    )


def test_child_process_environment_binds_log_role(monkeypatch):
    monkeypatch.setenv("ARTPM_PROCESS_ROLE", "parent")

    assert (
        start_with_checks._subprocess_environment("api")["ARTPM_PROCESS_ROLE"] == "api"
    )
    assert start_with_checks._subprocess_environment("ui")["ARTPM_PROCESS_ROLE"] == "ui"
    assert (
        start_with_checks._subprocess_environment("voice")["ARTPM_PROCESS_ROLE"]
        == "voice"
    )


def test_checked_startup_uses_the_current_python_environment():
    environment = {"PYTHONIOENCODING": "utf-8"}
    with (
        patch("start_with_checks.subprocess.run") as run,
        patch(
            "start_with_checks._subprocess_environment",
            return_value=environment,
        ),
    ):
        start_with_checks.start_streamlit()

    run.assert_called_once_with(
        start_with_checks.STREAMLIT_COMMAND,
        cwd=start_with_checks.project_root,
        env=environment,
        check=True,
    )


def test_disabled_voice_worker_does_not_spawn(monkeypatch):
    monkeypatch.setattr(
        VoiceSettings,
        "from_env",
        classmethod(lambda _cls: VoiceSettings(enabled=False)),
    )
    spawn = Mock()
    monkeypatch.setattr(start_with_checks.subprocess, "Popen", spawn)

    assert start_with_checks.start_voice_worker() is None
    spawn.assert_not_called()


def test_incomplete_voice_configuration_keeps_text_stack_available(monkeypatch):
    monkeypatch.setattr(
        VoiceSettings,
        "from_env",
        classmethod(lambda _cls: VoiceSettings(enabled=True)),
    )
    spawn = Mock()
    monkeypatch.setattr(start_with_checks.subprocess, "Popen", spawn)

    assert start_with_checks.start_voice_worker() is None
    spawn.assert_not_called()


def test_api_readiness_requires_strict_ready_payload(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode("utf-8")

    response = Response()
    monkeypatch.setattr(
        start_with_checks.url_request,
        "urlopen",
        lambda *_args, **_kwargs: response,
    )

    response.payload = {
        "service": "artpm-agent-api",
        "status": "degraded",
        "ready": False,
    }
    assert start_with_checks.api_is_ready() is False

    response.payload = {
        "service": "artpm-agent-api",
        "status": "ok",
        "ready": True,
    }
    assert start_with_checks.api_is_ready() is True
    assert start_with_checks._api_health_url().endswith("/ready")


def test_windows_restart_stops_identified_api_before_starting():
    script = Path("restart.bat").read_text(encoding="utf-8")

    assert "artpm-agent-api" in script
    assert "Get-NetTCPConnection" in script
    assert "Stop-Process" in script
    assert script.index("Stop-Process") < script.index('call "%~dp0start.bat"')


def test_legacy_health_check_entrypoint_executes_the_real_check():
    with patch("artpm_agent.health_check.run_health_check", return_value={}) as check:
        runpy.run_path("health_check.py", run_name="__main__")

    check.assert_called_once_with()


def test_legacy_health_check_entrypoint_propagates_unhealthy_exit_code():
    with patch(
        "artpm_agent.health_check.run_health_check",
        return_value={"overall_status": "unhealthy"},
    ):
        with pytest.raises(SystemExit) as raised:
            runpy.run_path("health_check.py", run_name="__main__")

    assert raised.value.code == 1


def test_agent_health_check_releases_runtime_resources():
    from artpm_agent import health_check

    agent = SimpleNamespace(
        list_skills=lambda: [],
        llm_client=object(),
        mcp_client=SimpleNamespace(enabled=False),
        unlimited_ocr_client=SimpleNamespace(
            runtime_status=lambda: SimpleNamespace(
                enabled=False,
                managed=False,
                package_available=False,
                available=False,
                detail="disabled",
            )
        ),
        close=Mock(),
    )
    with patch("artpm_agent.agent.ArtPMAgent", return_value=agent):
        result = health_check.check_agent()

    assert result["status"] == "ok"
    agent.close.assert_called_once_with()
