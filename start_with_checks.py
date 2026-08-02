#!/usr/bin/env python
"""Start the local API gateway and Streamlit UI with pre-flight checks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib import error as url_error
from urllib import request as url_request


project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root))

API_COMMAND = [sys.executable, "-m", "artpm_agent.api"]
VOICE_COMMAND = [sys.executable, "-m", "artpm_agent.voice.worker", "start"]
STREAMLIT_COMMAND = [
    sys.executable,
    "-m",
    "streamlit",
    "run",
    "artpm_agent/app.py",
    "--server.address",
    "127.0.0.1",
    "--server.port",
    "8501",
    "--server.headless",
    "true",
    "--browser.gatherUsageStats",
    "false",
    # End-user startup favors a coherent frontend build over hot reload.
    "--server.fileWatcherType",
    "none",
    "--server.runOnSave",
    "false",
]


def run_config_check() -> bool:
    """Run the existing configuration check and return whether it passed."""

    print("=" * 70)
    print("ArtPM Agent pre-flight configuration check")
    print("=" * 70)
    print()

    try:
        from artpm_agent.tools.check_config import main as check_config_main

        check_config_main()
        return True
    except SystemExit as error:
        return error.code == 0
    except Exception as error:  # noqa: BLE001 - a warning must not hide the app
        print(f"[WARN] Configuration check failed: {error}")
        print("       Startup will continue, but runtime features may be unavailable.")
        print()
        return True


def _subprocess_environment(process_role: str | None = None) -> dict[str, str]:
    """Use deterministic UTF-8 output for redirected Windows service logs."""

    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    if process_role:
        environment["ARTPM_PROCESS_ROLE"] = process_role
    return environment


def _configure_standard_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _api_health_url() -> str:
    host = os.getenv("ARTPM_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = os.getenv("ARTPM_API_PORT", "8765").strip() or "8765"
    return f"http://{host}:{port}/ready"


def api_is_ready(*, timeout: float = 0.6) -> bool:
    """Return True only when the expected ArtPM gateway answers health checks."""

    try:
        with url_request.urlopen(_api_health_url(), timeout=timeout) as response:
            if response.status != 200:
                return False
            payload: Any = json.load(response)
    except (OSError, ValueError, url_error.URLError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("service") == "artpm-agent-api"
        and payload.get("status") == "ok"
        and payload.get("ready") is True
    )


def _wait_for_api(process: subprocess.Popen[Any], *, timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if api_is_ready():
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.15)
    return False


def start_api() -> subprocess.Popen[Any] | None:
    """Start the local gateway, or reuse an already healthy local instance."""

    if api_is_ready():
        print(f"API gateway already ready: {_api_health_url()}")
        return None

    environment = _subprocess_environment("api")
    environment.setdefault("ARTPM_API_HOST", "127.0.0.1")
    environment.setdefault("ARTPM_API_PORT", "8765")
    print("Starting API gateway at http://127.0.0.1:8765 ...")
    process = subprocess.Popen(
        API_COMMAND,
        cwd=project_root,
        env=environment,
    )
    if _wait_for_api(process):
        print("API gateway is ready.")
        return process

    stop_api(process)
    raise RuntimeError(
        "API gateway did not become ready. Check whether port 8765 is available "
        "and review the startup output above."
    )


def stop_api(process: subprocess.Popen[Any] | None) -> None:
    """Stop only the API process owned by this startup invocation."""

    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def start_voice_worker() -> subprocess.Popen[Any] | None:
    """Start the optional media worker without making text startup depend on it."""

    try:
        from dotenv import load_dotenv

        load_dotenv(project_root / ".env", override=False)
        from artpm_agent.voice import VoiceSettings

        settings = VoiceSettings.from_env()
        status = settings.public_status()
    except Exception as error:  # noqa: BLE001 - optional service stays isolated
        print(f"[WARN] Voice configuration could not be read: {type(error).__name__}")
        return None
    if not settings.enabled:
        return None
    if not status.get("realtime_ready"):
        missing = ", ".join(status.get("missing", [])) or "provider configuration"
        print(f"[WARN] Voice worker was not started; missing: {missing}.")
        print("       Text chat remains available.")
        return None
    print("Starting LiveKit voice worker ...")
    process = subprocess.Popen(
        VOICE_COMMAND,
        cwd=project_root,
        env=_subprocess_environment("voice"),
    )
    time.sleep(0.6)
    if process.poll() is not None:
        print("[WARN] Voice worker exited during startup; text chat remains available.")
        return None
    print("Voice worker is running.")
    return process


def start_streamlit() -> None:
    """Start the Streamlit application in the current terminal."""

    print("=" * 70)
    print("Starting ArtPM Agent UI at http://127.0.0.1:8501")
    print("=" * 70)
    print()

    try:
        subprocess.run(
            STREAMLIT_COMMAND,
            cwd=project_root,
            env=_subprocess_environment("ui"),
            check=True,
        )
    except subprocess.CalledProcessError as error:
        print(f"[ERROR] Streamlit failed to start: {error}")
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        print("\nArtPM Agent stopped.")
        raise SystemExit(0) from None


def start_stack() -> None:
    """Run API and UI as one local lifecycle."""

    api_process: subprocess.Popen[Any] | None = None
    voice_process: subprocess.Popen[Any] | None = None
    try:
        api_process = start_api()
        voice_process = start_voice_worker()
        start_streamlit()
    except RuntimeError as error:
        print(f"[ERROR] {error}")
        raise SystemExit(1) from error
    finally:
        stop_api(voice_process)
        stop_api(api_process)


def main() -> None:
    """Run configuration checks and launch the complete local stack."""

    _configure_standard_streams()
    if not run_config_check():
        print()
        print("[ERROR] Configuration checks reported blocking errors.")
        response = input("Continue startup anyway? (y/N): ")
        if response.lower() not in ("y", "yes"):
            print("Startup cancelled.")
            raise SystemExit(1)

    start_stack()


if __name__ == "__main__":
    main()
