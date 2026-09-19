"""Process-local startup and first-call performance metrics.

The module uses only the Python standard library so importing the core runtime
does not pull model, vector, document, or observability SDKs into the process.
"""

from __future__ import annotations

import importlib
import os
import sys
import time
from threading import RLock
from types import ModuleType
from typing import Any

_PROCESS_STARTED_AT = time.perf_counter()


def _rss_bytes() -> int:
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(ProcessMemoryCounters),
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            ok = psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(),
                ctypes.byref(counters),
                counters.cb,
            )
            return int(counters.WorkingSetSize) if ok else 0
        except (AttributeError, OSError, TypeError, ValueError):
            return 0
    try:
        import resource

        usage = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return usage if sys.platform == "darwin" else usage * 1024
    except (ImportError, OSError, ValueError):
        return 0


class PerformanceMetrics:
    """Thread-safe bounded metrics for startup diagnostics."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._imports: dict[str, dict[str, float | int]] = {}
        self._observations: dict[str, dict[str, float | int]] = {}
        self._first_model_call: dict[str, Any] | None = None
        self._first_model_inflight = False

    def observe(self, name: str, duration_ms: float) -> None:
        key = str(name or "").strip()
        if not key:
            return
        value = max(0.0, float(duration_ms))
        with self._lock:
            item = self._observations.setdefault(
                key,
                {"count": 0, "total_ms": 0.0, "last_ms": 0.0, "max_ms": 0.0},
            )
            item["count"] = int(item["count"]) + 1
            item["total_ms"] = float(item["total_ms"]) + value
            item["last_ms"] = value
            item["max_ms"] = max(float(item["max_ms"]), value)

    def record_import(self, module_name: str, duration_ms: float) -> None:
        key = str(module_name or "").strip()
        if not key:
            return
        value = max(0.0, float(duration_ms))
        with self._lock:
            item = self._imports.setdefault(
                key,
                {"count": 0, "total_ms": 0.0, "last_ms": 0.0, "max_ms": 0.0},
            )
            item["count"] = int(item["count"]) + 1
            item["total_ms"] = float(item["total_ms"]) + value
            item["last_ms"] = value
            item["max_ms"] = max(float(item["max_ms"]), value)

    def begin_first_model_call(self) -> float | None:
        with self._lock:
            if self._first_model_call is not None or self._first_model_inflight:
                return None
            self._first_model_inflight = True
        return time.perf_counter()

    def finish_first_model_call(self, token: float | None, *, success: bool) -> None:
        if token is None:
            return
        duration_ms = max(0.0, (time.perf_counter() - token) * 1000.0)
        with self._lock:
            if self._first_model_call is None:
                self._first_model_call = {
                    "count": 1,
                    "latency_ms": round(duration_ms, 3),
                    "success": bool(success),
                }
            self._first_model_inflight = False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            imports = {
                name: {key: round(value, 3) if isinstance(value, float) else value for key, value in item.items()}
                for name, item in sorted(self._imports.items())
            }
            observations = {
                name: {key: round(value, 3) if isinstance(value, float) else value for key, value in item.items()}
                for name, item in sorted(self._observations.items())
            }
            first_model_call = dict(
                self._first_model_call
                or {"count": 0, "latency_ms": None, "success": None}
            )
        return {
            "process": {
                "pid": os.getpid(),
                "uptime_ms": round((time.perf_counter() - _PROCESS_STARTED_AT) * 1000.0, 3),
                "rss_mb": round(_rss_bytes() / (1024 * 1024), 3),
            },
            "imports": imports,
            "observations": observations,
            "first_model_call": first_model_call,
        }

    def reset(self) -> None:
        with self._lock:
            self._imports.clear()
            self._observations.clear()
            self._first_model_call = None
            self._first_model_inflight = False


performance_metrics = PerformanceMetrics()


def import_module_timed(module_name: str) -> ModuleType:
    started = time.perf_counter()
    module = importlib.import_module(module_name)
    performance_metrics.record_import(
        module_name,
        (time.perf_counter() - started) * 1000.0,
    )
    return module


def begin_first_model_call() -> float | None:
    return performance_metrics.begin_first_model_call()


def finish_first_model_call(token: float | None, *, success: bool) -> None:
    performance_metrics.finish_first_model_call(token, success=success)


def observe_duration(name: str, duration_ms: float) -> None:
    performance_metrics.observe(name, duration_ms)


def performance_snapshot() -> dict[str, Any]:
    return performance_metrics.snapshot()


def reset_performance_metrics() -> None:
    performance_metrics.reset()


__all__ = [
    "begin_first_model_call",
    "finish_first_model_call",
    "import_module_timed",
    "observe_duration",
    "performance_metrics",
    "performance_snapshot",
    "reset_performance_metrics",
]
