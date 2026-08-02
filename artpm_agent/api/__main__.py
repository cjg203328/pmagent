"""Command-line entry point for the ArtPM REST gateway."""

from __future__ import annotations

import os
from typing import Any


def main() -> Any:
    """Run uvicorn with the injectable FastAPI factory."""

    import uvicorn

    host = os.getenv("ARTPM_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(os.getenv("ARTPM_API_PORT", "8765"))
    except ValueError as error:
        raise SystemExit("ARTPM_API_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise SystemExit("ARTPM_API_PORT must be between 1 and 65535")
    return uvicorn.run(
        "artpm_agent.api:create_app",
        factory=True,
        host=host,
        port=port,
        proxy_headers=False,
    )


if __name__ == "__main__":  # pragma: no cover - exercised by the console script
    main()

