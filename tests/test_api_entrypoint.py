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

    api_main.main()

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
        api_main.main()
