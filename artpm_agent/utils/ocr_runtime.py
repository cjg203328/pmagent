"""Lazy lifecycle management for the deployment-owned OCR runtime.

The chat application never downloads models or creates an OCR environment. A
deployment bundle may provide ``runtime.json`` plus all required files. The
manager starts that bundle only when an attachment needs OCR, coordinates starts
across processes, and only terminates processes it created itself.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from threading import RLock
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4


MAX_MANIFEST_BYTES = 128 * 1024
MAX_COMMAND_ARGUMENTS = 128
MAX_ARGUMENT_CHARS = 8192
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DEFAULT_RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtime" / "unlimited_ocr"
_FAST_STARTUP_POLL_SECONDS = 0.05
_NORMAL_STARTUP_POLL_SECONDS = 0.25
_FAST_STARTUP_WINDOW_SECONDS = 1.0
_PROCESS_STOP_TIMEOUT_SECONDS = 1.0


class OCRRuntimeError(RuntimeError):
    """The bundled OCR runtime could not be validated or started."""


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


@dataclass(frozen=True)
class OCRRuntimeConfig:
    """Internal runtime policy loaded from application/deployment config only."""

    enabled: bool = False
    managed: bool = True
    manifest_path: str = ""
    startup_timeout: float = 90.0
    health_interval: float = 10.0
    restart_cooldown: float = 5.0
    max_restarts: int = 2
    lock_timeout: float = 180.0
    state_dir: str = "./temp/unlimited_ocr"
    log_path: str = "./logs/unlimited-ocr-runtime.log"

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any] | None) -> "OCRRuntimeConfig":
        source: Mapping[str, Any] = values or {}
        nested = source.get("unlimited_ocr")
        if isinstance(nested, Mapping):
            source = nested
        runtime = source.get("runtime")
        runtime_values: Mapping[str, Any] = runtime if isinstance(runtime, Mapping) else {}
        return cls(
            enabled=_as_bool(source.get("enabled"), False),
            managed=_as_bool(runtime_values.get("managed"), True),
            manifest_path=str(runtime_values.get("manifest_path") or "").strip(),
            startup_timeout=_bounded_float(
                runtime_values.get("startup_timeout"), 90.0, 1.0, 900.0
            ),
            health_interval=_bounded_float(
                runtime_values.get("health_interval"), 10.0, 0.0, 300.0
            ),
            restart_cooldown=_bounded_float(
                runtime_values.get("restart_cooldown"), 5.0, 0.0, 300.0
            ),
            max_restarts=_bounded_int(
                runtime_values.get("max_restarts"), 2, 0, 20
            ),
            lock_timeout=_bounded_float(
                runtime_values.get("lock_timeout"), 180.0, 5.0, 1800.0
            ),
            state_dir=str(
                runtime_values.get("state_dir") or "./temp/unlimited_ocr"
            ).strip(),
            log_path=str(
                runtime_values.get("log_path")
                or "./logs/unlimited-ocr-runtime.log"
            ).strip(),
        )

    @property
    def manifest(self) -> Path:
        if self.manifest_path:
            return Path(self.manifest_path).expanduser().resolve()
        return (_DEFAULT_RUNTIME_DIR / "runtime.json").resolve()


@dataclass(frozen=True)
class OCRRuntimeManifest:
    command: tuple[str, ...]
    cwd: Path
    environment: dict[str, str]
    required_paths: tuple[Path, ...]


@dataclass(frozen=True)
class OCRRuntimeStatus:
    """Read-only runtime state suitable for diagnostics, never user settings."""

    enabled: bool
    managed: bool
    package_available: bool
    available: bool
    owned: bool
    pid: int | None
    restart_count: int
    detail: str


class OCRRuntimeManager:
    """Start and recover one local OCR sidecar on demand."""

    def __init__(
        self,
        config: OCRRuntimeConfig,
        *,
        base_url: str,
        probe: Callable[[], bool],
        logger: Any = None,
        popen_factory: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
    ) -> None:
        self.config = config
        self.base_url = base_url
        self._probe = probe
        self._logger = logger
        self._popen_factory = popen_factory
        self._lock = RLock()
        self._process: subprocess.Popen[Any] | None = None
        self._last_checked_at = 0.0
        self._last_available = False
        self._last_detail = "dormant"
        self._launch_attempts = 0
        self._restart_count = 0
        self._last_launch_at = 0.0

    @property
    def _state_dir(self) -> Path:
        return Path(self.config.state_dir).expanduser().resolve()

    @property
    def _start_lock_path(self) -> Path:
        return self._state_dir / "startup.lock"

    @property
    def _log_path(self) -> Path:
        return Path(self.config.log_path).expanduser().resolve()

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self._logger is None:
            return
        writer = getattr(self._logger, level, None)
        if callable(writer):
            writer(message, *args)

    def _is_local_endpoint(self) -> bool:
        try:
            host = (urlsplit(self.base_url).hostname or "").rstrip(".").lower()
            if host == "localhost":
                return True
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _probe_now(self) -> bool:
        try:
            return bool(self._probe())
        except Exception as error:
            self._log("debug", "OCR health probe failed: %s", error)
            return False

    def _record_probe(self, available: bool, detail: str | None = None) -> bool:
        self._last_checked_at = time.monotonic()
        self._last_available = available
        if detail:
            self._last_detail = detail
        elif available:
            self._last_detail = "ready"
        return available

    def ensure_ready(self, *, cache_ttl: float | None = None) -> bool:
        """Probe and lazily start the packaged runtime when OCR is first needed."""
        if not self.config.enabled:
            return self._record_probe(False, "disabled")

        ttl = self.config.health_interval if cache_ttl is None else max(0.0, cache_ttl)
        now = time.monotonic()
        if self._last_checked_at and now - self._last_checked_at < ttl:
            return self._last_available

        with self._lock:
            now = time.monotonic()
            if self._last_checked_at and now - self._last_checked_at < ttl:
                return self._last_available
            if self._probe_now():
                return self._record_probe(True)
            self._record_probe(False, "service unavailable")
            if not self.config.managed:
                return False
            if not self._is_local_endpoint():
                self._last_detail = "remote endpoint is not application-managed"
                return False
            return self._start_or_wait_locked()

    def recover(self) -> bool:
        """Recover a failed OCR request and return whether one retry is safe."""
        if not self.config.enabled:
            return False
        with self._lock:
            self._last_checked_at = 0.0
            if self._probe_now():
                return self._record_probe(True)
            self._record_probe(False, "runtime health check failed")
            if not self.config.managed or not self._is_local_endpoint():
                return False
            self._terminate_owned_locked()
            return self._start_or_wait_locked()

    def inspect(self, *, refresh: bool = False) -> OCRRuntimeStatus:
        """Return diagnostics without ever starting the runtime."""
        with self._lock:
            if refresh and self.config.enabled:
                self._record_probe(self._probe_now())
            package_available = False
            if self.config.managed:
                try:
                    self._load_manifest()
                    package_available = True
                except OCRRuntimeError:
                    package_available = False
            process = self._process
            owned = process is not None and process.poll() is None
            return OCRRuntimeStatus(
                enabled=self.config.enabled,
                managed=self.config.managed,
                package_available=package_available,
                available=self._last_available,
                owned=owned,
                pid=process.pid if owned else None,
                restart_count=self._restart_count,
                detail=self._last_detail,
            )

    def stop_owned(self) -> None:
        """Stop only a process launched by this manager (primarily for shutdown/tests)."""
        with self._lock:
            self._terminate_owned_locked()
            self._record_probe(False, "stopped")

    def _load_manifest(self) -> OCRRuntimeManifest:
        path = self.config.manifest
        try:
            size = path.stat().st_size
        except OSError as error:
            raise OCRRuntimeError("runtime package is not installed") from error
        if size <= 0 or size > MAX_MANIFEST_BYTES:
            raise OCRRuntimeError("runtime manifest has an invalid size")
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise OCRRuntimeError("runtime manifest is invalid") from error
        if not isinstance(payload, Mapping):
            raise OCRRuntimeError("runtime manifest must be an object")
        if not _as_bool(payload.get("enabled"), True):
            raise OCRRuntimeError("runtime package is disabled")

        raw_command = payload.get("command")
        if (
            not isinstance(raw_command, Sequence)
            or isinstance(raw_command, (str, bytes))
            or not raw_command
            or len(raw_command) > MAX_COMMAND_ARGUMENTS
        ):
            raise OCRRuntimeError("runtime command must be a bounded argument list")

        runtime_dir = path.parent.resolve()
        package_dir = Path(__file__).resolve().parents[1]
        project_dir = package_dir.parent
        raw_model_dir = str(payload.get("model_dir") or "models")
        model_dir = self._resolve_runtime_path(runtime_dir, raw_model_dir)
        replacements = {
            "{python}": sys.executable,
            "{runtime_dir}": str(runtime_dir),
            "{package_dir}": str(package_dir),
            "{project_dir}": str(project_dir),
            "{model_dir}": str(model_dir),
        }

        command = tuple(
            self._expand_manifest_value(value, replacements, "command argument")
            for value in raw_command
        )
        raw_cwd = str(payload.get("cwd") or "{runtime_dir}")
        cwd_value = self._expand_manifest_value(raw_cwd, replacements, "cwd")
        cwd = self._resolve_runtime_path(runtime_dir, cwd_value)
        if not cwd.is_dir():
            raise OCRRuntimeError("runtime working directory is missing")

        raw_required = payload.get("required_paths")
        if (
            not isinstance(raw_required, Sequence)
            or isinstance(raw_required, (str, bytes))
            or not raw_required
            or len(raw_required) > 128
        ):
            raise OCRRuntimeError("runtime manifest must declare packaged files")
        required_paths = tuple(
            self._resolve_runtime_path(
                runtime_dir,
                self._expand_manifest_value(value, replacements, "required path"),
            )
            for value in raw_required
        )
        if any(not required.exists() for required in required_paths):
            raise OCRRuntimeError("runtime package is incomplete")

        raw_environment = payload.get("environment") or {}
        if not isinstance(raw_environment, Mapping) or len(raw_environment) > 128:
            raise OCRRuntimeError("runtime environment must be a bounded object")
        environment: dict[str, str] = {}
        for raw_name, raw_value in raw_environment.items():
            name = str(raw_name)
            if not _ENVIRONMENT_NAME.fullmatch(name):
                raise OCRRuntimeError("runtime environment contains an invalid name")
            environment[name] = self._expand_manifest_value(
                raw_value, replacements, "environment value"
            )

        return OCRRuntimeManifest(command, cwd, environment, required_paths)

    @staticmethod
    def _expand_manifest_value(
        value: Any,
        replacements: Mapping[str, str],
        field: str,
    ) -> str:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise OCRRuntimeError(f"runtime {field} is invalid")
        expanded = value
        for marker, replacement in replacements.items():
            expanded = expanded.replace(marker, replacement)
        if len(expanded) > MAX_ARGUMENT_CHARS:
            raise OCRRuntimeError(f"runtime {field} is too long")
        return expanded

    @staticmethod
    def _resolve_runtime_path(runtime_dir: Path, value: str) -> Path:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = runtime_dir / candidate
        return candidate.resolve()

    def _start_or_wait_locked(self) -> bool:
        try:
            manifest = self._load_manifest()
        except OCRRuntimeError as error:
            self._last_detail = str(error)
            return False

        token = self._acquire_start_lock()
        if token is None:
            if self._wait_for_other_starter():
                return True
            token = self._acquire_start_lock()
            if token is None:
                self._last_detail = "another process is managing OCR startup"
                return False

        try:
            if self._endpoint_port_is_open() and self._probe_now():
                return self._record_probe(True)
            # A failed health check on a process owned by this manager is a
            # restartable fault. Never terminate an unowned process below.
            if self._process is not None and self._process.poll() is None:
                self._terminate_owned_locked()
            if self._endpoint_port_is_open():
                self._last_detail = "OCR endpoint is occupied but unhealthy"
                return False

            is_restart = self._launch_attempts > 0
            if is_restart:
                if self._restart_count >= self.config.max_restarts:
                    self._last_detail = "OCR restart limit reached"
                    return False
                elapsed = time.monotonic() - self._last_launch_at
                if elapsed < self.config.restart_cooldown:
                    self._last_detail = "OCR restart cooling down"
                    return False
                self._restart_count += 1

            try:
                self._launch(manifest)
            except OCRRuntimeError as error:
                self._last_detail = str(error)
                return self._record_probe(False, self._last_detail)
            return self._wait_until_ready()
        finally:
            self._release_start_lock(token)

    def _launch(self, manifest: OCRRuntimeManifest) -> None:
        self._terminate_owned_locked()
        log_path = self._log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        environment = os.environ.copy()
        environment.update(manifest.environment)
        # Runtime assets must already be in the deployment bundle. Never fetch
        # model weights or Python packages from a business-user session.
        environment.setdefault("HF_HUB_OFFLINE", "1")
        environment.setdefault("TRANSFORMERS_OFFLINE", "1")
        environment["ARTPM_MANAGED_OCR_RUNTIME"] = "1"
        kwargs: dict[str, Any] = {
            "cwd": str(manifest.cwd),
            "env": environment,
            "stdin": subprocess.DEVNULL,
            "stdout": None,
            "stderr": subprocess.STDOUT,
            "shell": False,
        }
        if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        with log_path.open("ab", buffering=0) as log_stream:
            kwargs["stdout"] = log_stream
            try:
                self._process = self._popen_factory(list(manifest.command), **kwargs)
            except (OSError, ValueError) as error:
                self._process = None
                raise OCRRuntimeError("runtime process could not be started") from error
        self._launch_attempts += 1
        self._last_launch_at = time.monotonic()
        self._last_detail = "starting"
        self._log("info", "Started managed OCR runtime (pid=%s)", self._process.pid)

    def _wait_until_ready(self) -> bool:
        started_at = time.monotonic()
        deadline = started_at + self.config.startup_timeout
        while time.monotonic() < deadline:
            process = self._process
            if process is None:
                break
            return_code = process.poll()
            if return_code is not None:
                self._last_detail = f"OCR runtime exited during startup ({return_code})"
                return self._record_probe(False, self._last_detail)
            if self._endpoint_port_is_open() and self._probe_now():
                return self._record_probe(True)
            time.sleep(self._startup_poll_interval(started_at))
        self._terminate_owned_locked()
        return self._record_probe(False, "OCR runtime startup timed out")

    def _wait_for_other_starter(self) -> bool:
        started_at = time.monotonic()
        deadline = started_at + self.config.startup_timeout
        while time.monotonic() < deadline:
            if self._probe_now():
                return self._record_probe(True)
            if not self._start_lock_path.exists():
                return False
            time.sleep(self._startup_poll_interval(started_at))
        return False

    @staticmethod
    def _startup_poll_interval(started_at: float) -> float:
        elapsed = time.monotonic() - started_at
        if elapsed < _FAST_STARTUP_WINDOW_SECONDS:
            return _FAST_STARTUP_POLL_SECONDS
        return _NORMAL_STARTUP_POLL_SECONDS

    def _endpoint_port_is_open(self) -> bool:
        parsed = urlsplit(self.base_url)
        host = parsed.hostname
        if not host:
            return False
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            return False

    def _acquire_start_lock(self) -> str | None:
        lock_path = self._start_lock_path
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            token = uuid4().hex
            payload = json.dumps(
                {"pid": os.getpid(), "token": token, "created_at": time.time()}
            ).encode("utf-8")
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if self._lock_is_stale(lock_path):
                    try:
                        lock_path.unlink()
                    except OSError:
                        return None
                    continue
                return None
            try:
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)
            return token
        return None

    def _lock_is_stale(self, path: Path) -> bool:
        try:
            age = max(0.0, time.time() - path.stat().st_mtime)
            payload = json.loads(path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid", 0))
            created_at = float(payload.get("created_at", 0.0))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return age > 2.0 if "age" in locals() else False
        if time.time() - created_at > self.config.lock_timeout:
            return True
        return pid <= 0 or not self._pid_is_alive(pid)

    @staticmethod
    def _pid_is_alive(pid: int) -> bool:
        if pid == os.getpid():
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True

    def _release_start_lock(self, token: str) -> None:
        path = self._start_lock_path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("token") == token:
                path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            return

    def _terminate_owned_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=_PROCESS_STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                self._log("warning", "Managed OCR runtime did not exit after kill")
        except OSError:
            return


__all__ = [
    "OCRRuntimeConfig",
    "OCRRuntimeError",
    "OCRRuntimeManager",
    "OCRRuntimeManifest",
    "OCRRuntimeStatus",
]
