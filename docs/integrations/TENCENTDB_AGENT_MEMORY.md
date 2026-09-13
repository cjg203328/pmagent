# TencentDB Agent Memory Integration

This project integrates [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) as an optional HTTP sidecar. The upstream Node.js gateway remains independently deployed and owns its L0-L3 extraction, persona, scene, and recall pipeline.

## Runtime Flow

1. Before a Harness turn, ArtPM calls the gateway `POST /recall` and injects its returned context.
2. After a successful non-approval turn, ArtPM calls `POST /capture` with the user and assistant messages.
3. Gateway failure is best-effort: the user request continues without remote-memory context.

The adapter uses the upstream v1 capture/recall API because it activates the complete L0-L3 pipeline. It does not vendor, start, or manage the Node.js gateway process.

## Scope Mapping

ArtPM only enables remote memory when the turn has a trusted `TenantContext`. It maps:

| ArtPM scope | Gateway field |
| --- | --- |
| tenant + workspace + principal + agent | `session_key` |
| tenant + workspace + principal + agent + conversation | `session_id` |

Both identifiers are HMAC-derived with `TENCENTDB_AGENT_MEMORY_SCOPE_SECRET`; raw tenant, workspace, user, and conversation IDs are never sent as gateway keys. A missing or forged non-local scope fails closed and skips remote recall/capture.

## Configuration

Set these deployment variables after running the upstream gateway on a trusted network:

```dotenv
TENCENTDB_AGENT_MEMORY_ENABLED=true
TENCENTDB_AGENT_MEMORY_BASE_URL=https://memory.example.internal
TENCENTDB_AGENT_MEMORY_API_KEY=<same-value-as-TDAI_GATEWAY_API_KEY>
TENCENTDB_AGENT_MEMORY_SCOPE_SECRET=<long-random-secret>
TENCENTDB_AGENT_MEMORY_TIMEOUT_SECONDS=3
```

For a local developer gateway at `http://127.0.0.1:8420`, an API key is optional. A non-loopback HTTP URL is rejected unless `TENCENTDB_AGENT_MEMORY_ALLOW_INSECURE_HTTP=true` is explicitly configured; remote gateways also require an API key.

The gateway can use its own LLM and storage configuration. Follow the upstream project for its Node.js version, SQLite/TCVDB deployment, and `TDAI_GATEWAY_API_KEY` configuration.
