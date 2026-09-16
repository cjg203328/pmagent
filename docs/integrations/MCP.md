# MCP Integration

Status: current integration guide

MCP is an optional integration. The application remains usable without a
remote MCP server and keeps local file tools available through the built-in
backend. The public client is `artpm_agent.core.mcp_client_unified.UnifiedMCPClient`;
do not import a transport implementation directly from application code.

## Local Defaults

The local backend is loaded lazily and does not require an API key. It exposes
the built-in read/search/data tools through the unified client. The remote
Skills Forge backend is disabled unless both `MCP_ENABLED=true` and a valid
`SKILLS_FORGE_KEY` are configured.

```python
from artpm_agent.core.mcp_client_unified import get_unified_mcp_client

client = get_unified_mcp_client(workspace_path=".")
tools = client.list_tools()
result = client.call_tool("read_file", {"path": "README.md"})
```

`call_tool` is the synchronous bridge for local tools. Async skill calls use
`await client.call_skill(name, params)`. The client selects the local backend
first and only uses the remote backend for the read-only allowlist defined in
`artpm_agent/core/mcp_client_stdio.py`.

## Optional Skills Forge

```powershell
$env:MCP_ENABLED = "true"
$env:MCP_TRANSPORT = "stdio"   # `http` is also supported when configured
$env:SKILLS_FORGE_KEY = "<key>"
python -m pytest -q tests/test_mcp.py --no-cov
```

For `http` transport, set `SKILLS_FORGE_URL` as well. Remote verification is
an explicit integration test and is skipped by the offline suite unless
`ART_ENABLE_INTEGRATION=1` is set. Never commit keys or a local
`.claude/mcp_config.json` file.

## Lifecycle And Safety

- Call `client.close()` when a long-lived host shuts down; the operation is
  idempotent and releases the stdio worker when one was started.
- Remote tools are filtered to the read-only allowlist. Command execution and
  other mutation tools are not enabled by this application contract.
- A remote connection failure is reported as a degraded optional capability;
  it must not make the local UI, API, or CLI unavailable.
- Async calls isolate blocking HTTP, file parsing, data analysis, and approved
  command execution in worker threads so MCP work cannot stall the host event
  loop.
- MCP tool execution still runs inside the Harness approval and tenant/workspace
  boundaries. MCP does not become a second request-processing entrypoint.

The old transport-specific guides and completion reports are retained under
`docs/archive/mcp/` for historical context only. They may mention files that
no longer exist and are not current setup instructions.
