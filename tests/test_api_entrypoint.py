from __future__ import annotations

import pytest

from artpm_agent.api import __main__ as api_main


def test_api_entrypoint_uses_factory_and_local_defaults(monkeypatch):
    captured = {}
    monkeypatch.delenv("ARTPM_API_HOST", raising=False)
    monkeypatch.delenv("ARTPM_API_PORT", raising=False)
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, **options: captured.update(app=app, **options),
    )

    api_main.main([])

    assert captured == {
        "app": "artpm_agent.api:create_app",
        "factory": True,
        "host": "127.0.0.1",
        "port": 8765,
        "proxy_headers": False,
    }


@pytest.mark.parametrize("value", ["invalid", "0", "65536"])
def test_api_entrypoint_rejects_invalid_port(monkeypatch, value):
    monkeypatch.setenv("ARTPM_API_PORT", value)

    with pytest.raises(SystemExit, match="ARTPM_API_PORT"):
        api_main.main([])


def test_api_entrypoint_help_exits_before_starting_server(monkeypatch, capsys):
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *_args, **_kwargs: pytest.fail("uvicorn must not start for --help"),
    )

    with pytest.raises(SystemExit) as error:
        api_main.main(["--help"])

    assert error.value.code == 0
    assert "Run the ArtPM REST gateway" in capsys.readouterr().out


def test_api_entrypoint_flags_override_environment(monkeypatch):
    captured = {}
    monkeypatch.setenv("ARTPM_API_HOST", "127.0.0.1")
    monkeypatch.setenv("ARTPM_API_PORT", "8765")
    monkeypatch.setattr(
        "uvicorn.run",
        lambda app, **options: captured.update(app=app, **options),
    )

    api_main.main(["--host", "0.0.0.0", "--port", "9000"])

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9000
