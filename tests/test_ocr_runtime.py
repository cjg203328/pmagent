from __future__ import annotations

import json
from pathlib import Path
import socket
import threading
import time


APP_ROOT = Path(__file__).resolve().parents[1] / "artpm_agent"

from artpm_agent.utils.unlimited_ocr import UnlimitedOCRClient


SERVER_SOURCE = r'''
import http.server
import json
import pathlib
import sys

port = int(sys.argv[1])
counter = pathlib.Path(sys.argv[2])
counter.write_text(str(int(counter.read_text() or "0") + 1) if counter.exists() else "1")

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b"ok"
        elif self.path == "/v1/models":
            body = json.dumps({"data": [{"id": "Unlimited-OCR"}]}).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return

http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_count(path: Path, expected: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if path.exists() and path.read_text() == str(expected):
            return
        time.sleep(0.05)
    assert path.exists()
    assert path.read_text() == str(expected)


def _client(tmp_path: Path, port: int) -> UnlimitedOCRClient:
    return UnlimitedOCRClient(
        {
            "enabled": True,
            "base_url": f"http://127.0.0.1:{port}",
            "model": "Unlimited-OCR",
            "timeout": 5,
            "runtime": {
                "managed": True,
                "manifest_path": str(tmp_path / "runtime.json"),
                "startup_timeout": 10,
                "health_interval": 0,
                "restart_cooldown": 0,
                "max_restarts": 2,
                "state_dir": str(tmp_path / "state"),
                "log_path": str(tmp_path / "runtime.log"),
            },
        }
    )


def test_runtime_is_lazy_single_instance_and_recovers(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(SERVER_SOURCE, encoding="utf-8")
    counter = tmp_path / "starts.txt"
    port = _free_port()
    manifest = {
        "enabled": True,
        "command": ["{python}", str(script), str(port), str(counter)],
        "cwd": str(tmp_path),
        "required_paths": [str(script)],
        "environment": {"HF_HUB_OFFLINE": "1"},
    }
    (tmp_path / "runtime.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    first = _client(tmp_path, port)
    second = _client(tmp_path, port)
    assert not first.runtime_status().available
    assert not counter.exists()

    results: list[bool] = []
    threads = [
        threading.Thread(
            target=lambda client: results.append(client.ready(cache_ttl=0)),
            args=(client,),
        )
        for client in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert len(results) == 2
    assert all(results)
    _wait_for_count(counter, 1)
    assert first.runtime_status().owned or second.runtime_status().owned

    owner = first if first.runtime_status().owned else second
    observer = second if owner is first else first
    owner.runtime_manager.stop_owned()
    assert observer.ready(cache_ttl=0) is True
    _wait_for_count(counter, 2)
    owner.runtime_manager.stop_owned()
    observer.runtime_manager.stop_owned()


def test_missing_runtime_package_is_a_read_only_degradation(tmp_path):
    client = UnlimitedOCRClient(
        {
            "enabled": True,
            "base_url": "http://127.0.0.1:65530",
            "runtime": {
                "managed": True,
                "manifest_path": str(tmp_path / "missing.json"),
                "state_dir": str(tmp_path / "state"),
            },
        }
    )
    assert client.ready(cache_ttl=0) is False
    status = client.runtime_status()
    assert status.package_available is False
    assert "not installed" in status.detail
