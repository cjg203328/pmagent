"""Command-line entry point for the ArtPM REST gateway."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the ArtPM REST gateway.")
    parser.add_argument("--host", help="Address for the gateway listener")
    parser.add_argument("--port", help="TCP port for the gateway listener")
    return parser


def main(argv: Sequence[str] | None = None) -> Any:
    """Run uvicorn with the injectable FastAPI factory."""

    import uvicorn

    args = _parser().parse_args(argv)
    host = (
        args.host
        or os.getenv("ARTPM_API_HOST", "127.0.0.1").strip()
        or "127.0.0.1"
    )
    port_value = args.port or os.getenv("ARTPM_API_PORT", "8765")
    try:
        port = int(port_value)
    except ValueError as error:
        raise SystemExit("ARTPM_API_PORT or --port must be an integer") from error
    if not 1 <= port <= 65535:
        raise SystemExit("ARTPM_API_PORT or --port must be between 1 and 65535")
    return uvicorn.run(
        "artpm_agent.api:create_app",
        factory=True,
        host=host,
        port=port,
        proxy_headers=False,
    )


if __name__ == "__main__":  # pragma: no cover - exercised by the console script
    main(sys.argv[1:])
