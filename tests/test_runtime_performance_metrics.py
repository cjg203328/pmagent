from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


def test_performance_metrics_capture_rss_import_and_first_model_call():
    from artpm_agent.runtime.performance import (
        begin_first_model_call,
        finish_first_model_call,
        import_module_timed,
        performance_snapshot,
        reset_performance_metrics,
    )

    reset_performance_metrics()
    assert import_module_timed("email.message") is not None
    token = begin_first_model_call()
    finish_first_model_call(token, success=True)
    finish_first_model_call(token, success=True)

    snapshot = performance_snapshot()
    assert snapshot["process"]["rss_mb"] > 0
    assert snapshot["process"]["uptime_ms"] >= 0
    assert snapshot["imports"]["email.message"]["count"] == 1
    assert snapshot["first_model_call"]["count"] == 1
    assert snapshot["first_model_call"]["success"] is True


def test_core_runtime_import_stays_lightweight():
    root = Path(__file__).resolve().parents[1]
    code = """
import json, sys, time
started = time.perf_counter()
import artpm_agent.runtime.factory
from artpm_agent.runtime.performance import performance_snapshot
blocked = sorted(name for name in sys.modules if name.split('.')[0] in {
    'anthropic', 'docx', 'faiss', 'fitz', 'langchain', 'mcp', 'openai',
    'openpyxl', 'pandas', 'pdfplumber', 'paddleocr'
})
snapshot = performance_snapshot()
print(json.dumps({
    'elapsed_ms': (time.perf_counter() - started) * 1000,
    'rss_mb': snapshot['process']['rss_mb'],
    'blocked': blocked,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert payload["elapsed_ms"] < 1500
    assert payload["rss_mb"] < 100
    assert payload["blocked"] == []
