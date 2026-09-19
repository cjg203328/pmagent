# Current Architecture Contract

Status: current
Owner: pmagent maintainers
Review cadence: every release or architecture-affecting change

Risk governance and planned decomposition are tracked in
[`OPTIMIZATION_STRATEGY.md`](./OPTIMIZATION_STRATEGY.md). It is a current
engineering strategy; archived reports are not implementation contracts.

## Scope and Construction

Every request carries a server-authenticated `(tenant_id, workspace_id)` and
an explicit `profile_id`. `RuntimeFactory` constructs workflow coordinators,
artifact generators and artifact coordinators from that triple. These scoped
runtimes use bounded LRU/TTL caches and are never taken from raw Streamlit
session state. A profile supplied by a model or client cannot override the
trusted host scope.

## Runtime

`run_turn()` is the canonical turn boundary for Streamlit, REST and CLI.
`ArtPMAgent` and `RequestOrchestrator` are compatibility implementations and
must not receive new business logic. New clients should consume typed runtime
events and `TurnResult` rather than calling `chat()` or `stream_chat()`.

`LocalHarnessRuntime` is the canonical in-process host for those entrypoints.
It owns the request-scoped router and service bundle, then delegates exactly
once to `run_turn()`. `LegacyAgentRuntimeAdapter` remains a compatibility
adapter for external callers and is observable as `runtime_kind=legacy_adapter`.
Each result carries the additive `harness_contract_version` and `runtime_kind`
metadata fields. Hosts must treat unknown metadata keys as forward-compatible.

The process-local runtime counters are exposed by the health response under
`runtime_counters`. They measure current-worker activity only; durable
telemetry remains the source for historical analysis. Attachment parsing is
owned by the Harness. A turn may reuse one parser snapshot, which is counted
separately from a new parse attempt and a parser failure.

`RuntimeFactory` owns one process Agent and one `StorageRegistry`. API, UI and
CLI obtain stores, learning services and workspace coordinators from this
factory. Coordinator cache keys include tenant, workspace and profile. API
chat locking is workspace-scoped, so unrelated workspaces are not serialized.
Health diagnostics expose RSS, uptime, timed imports and first model-call
latency; each turn reports intent/parse/retrieval invocation counts.

Tool execution is bounded at both loop and worker layers: maximum turns and
calls, worker concurrency, per-call timeout, cancellation, schema validation,
last-turn side-effect rejection and result-size limits are enforced before a
tool result is returned. Oversized results spill only below the same tenant and
workspace scope, with TTL cleanup and explicit deletion. EventBus publication
is best-effort after durable SessionStore persistence; failures increment a
counter and emit a structured warning without losing the session record.

The API has a pure `serializers.py` boundary for JSON normalization, public
stream event payloads and SSE framing, plus dedicated `routers/system.py`,
`routers/capabilities.py`, `routers/permissions.py` and `routers/workflows.py`
modules. Workspace/search, chat, voice and embed routes remain in `api/app.py`
as the next extraction wave; the route contract is covered while the
compatibility facade remains stable. `api/contracts.py` and
`runtime/service_ports.py` define the typed cross-module ports used by the
gateway and turn bundle.

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
Knowledge writes enqueue `knowledge_index_outbox` in the same SQLite
transaction. After commit, `KnowledgeVectorProjector` claims that work with
leases, bounded exponential retry, and dead-letter visibility. While work is
pending or failed, retrieval uses the authoritative
literal path; a full index can always be rebuilt from current resource rows.

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

`GET /ready` probes every configured authoritative Store (business,
conversation, session and knowledge) through
`StorageRegistry`. It does not initialize an LLM, OCR backend or vector
provider. A failed authority returns HTTP 503 with per-store status so an
operator can distinguish a dependency outage from an optional capability
being disabled.

The repository architecture graph is generated on demand with
`python scripts/build_graph.py`. It performs a freshness check, writes only
to `.ai-memory/knowledge-graph/`, bounds queried subgraphs to 80 nodes and
reports cycles. Graph output accelerates investigation; independent `rg`
evidence and executable tests remain the final authority.

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

This file, `EXECUTION_MAP.md`, `PROJECT_STRUCTURE.md`, `STORAGE_CONTRACT.md`
and `P2_MODULE_BOUNDARIES.md` define the current architecture. Documents under
`docs/archive/` are historical references only and are not contracts.
