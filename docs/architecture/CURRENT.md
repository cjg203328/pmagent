# Current Architecture Contract

Status: current
Owner: pmagent maintainers
Review cadence: every release or architecture-affecting change

Risk governance and planned decomposition are tracked in
[`OPTIMIZATION_STRATEGY.md`](./OPTIMIZATION_STRATEGY.md). It is a current
engineering strategy; archived reports are not implementation contracts.

## Runtime

`run_turn()` is the canonical turn boundary for Streamlit, REST and CLI.
`ArtPMAgent` and `RequestOrchestrator` are compatibility implementations and
must not receive new business logic. New clients should consume typed runtime
events and `TurnResult` rather than calling `chat()` or `stream_chat()`.

## Workspace

`ConversationStore` owns workspace metadata and conversation foreign keys.
The trusted `tenant_id` and `workspace_id` are part of every request scope.
`WorkspaceKnowledgeStore` consumes that scope and remains the only authority
for workspace knowledge resources, versions and accepted rules.

The first workspace API contracts are:

- `GET /v1/workspaces`
- `POST /v1/workspaces`
- `POST /v1/search`
- `POST /v1/chat/stream`

The API returns only workspaces visible to the authenticated tenant. Search
results are normalized to `RetrievalHit` and include citation metadata. A
vector index is a derived accelerator and never a source of truth.

## Retrieval

The lightweight retrieval path is:

```text
trusted scope -> RetrievalPlan -> WorkspaceKnowledgeStore
              -> bounded dense/literal search -> RetrievalHit/citation
```

`artpm_agent/retrieval/` is an adapter layer, not a second store. BM25,
rerank, web search and multi-backend fan-out are optional capabilities and must
not be required by the offline profile.

## Capabilities

The skill catalog remains separate from deployment readiness. Deployment
capabilities report `enabled`, `configured`, `ready` and `reason`; the UI must
not present an optional capability as usable until `ready` is true.

## Embed Boundary

Embed is optional and disabled by default. When enabled, the current minimal
contract is:

- `GET /embed/{channel}/config`
- `POST /embed/{channel}/exchange`
- `POST /embed/{channel}/session`
- `POST /embed/{channel}/chat`

Publish tokens are accepted only at the exchange boundary. Browser sessions
use short-lived HMAC tokens bound to channel, tenant, workspace and exact
`Origin`; requests are rate-limited and responses set `frame-ancestors` and
`no-store` headers. The widget must still validate `postMessage` source and
origin on the client side. Long-lived publish tokens must never enter browser
JavaScript.

## Source Of Truth

This file, `EXECUTION_MAP.md`, `PROJECT_STRUCTURE.md` and
`STORAGE_CONTRACT.md` define the current architecture. Documents under
`docs/archive/` are historical references only and are not contracts.
