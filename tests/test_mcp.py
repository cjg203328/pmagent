"""Live MCP transport contract.

The test is intentionally opt-in because it starts the configured MCP
transport and may require npm/network access.  Run it with
``ART_ENABLE_INTEGRATION=1`` and a valid ``SKILLS_FORGE_KEY``.
"""

from __future__ import annotations

import os
import shutil

import pytest
from dotenv import load_dotenv

from artpm_agent.core.mcp_client import MCPClient
from artpm_agent.core.mcp_client_stdio import (
    SKILLS_FORGE_ALLOWED_TOOLS,
    StdioMCPClient,
)
from artpm_agent.utils.llm_client import is_valid_api_key


@pytest.mark.integration
def test_mcp_connection() -> None:
    """Verify one real MCP handshake and the read-only tool boundary."""

    # Local deployments keep integration credentials in .env; CI can provide
    # the same values through the process environment instead.
    load_dotenv()
    if os.getenv("MCP_ENABLED", "false").lower() != "true":
        pytest.skip("set MCP_ENABLED=true for a live MCP integration run")

    api_key = os.getenv("SKILLS_FORGE_KEY", "").strip()
    if not is_valid_api_key(api_key):
        pytest.skip("SKILLS_FORGE_KEY is not configured for the integration run")

    transport = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
    client: StdioMCPClient | MCPClient
    if transport == "stdio":
        if shutil.which("npx") is None:
            pytest.skip("npx is required for the stdio MCP integration")
        client = StdioMCPClient(api_key=api_key, enabled=True)
    elif transport == "http":
        if not os.getenv("SKILLS_FORGE_URL", "").strip():
            pytest.skip("SKILLS_FORGE_URL is required for HTTP MCP integration")
        client = MCPClient(api_key=api_key)
    else:
        pytest.fail(f"unsupported MCP_TRANSPORT: {transport}")

    try:
        ok, message = client.ping()
        assert ok, message

        skills = client.list_skills()
        names = {str(item.get("name")) for item in skills}
        assert names, "MCP handshake returned no tools"
        assert names <= set(SKILLS_FORGE_ALLOWED_TOOLS), (
            "MCP exposed a tool outside the read-only allowlist: "
            + ", ".join(sorted(names - set(SKILLS_FORGE_ALLOWED_TOOLS)))
        )
    finally:
        client.close()
