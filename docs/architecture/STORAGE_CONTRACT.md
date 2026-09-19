# Storage Contract

Status: current
Owner: pmagent maintainers

## Scope Invariant

The trusted scope is `(tenant_id, workspace_id)`. `profile_id` selects model
and workflow behavior but is not a substitute for tenant authorization. Every
authority read/write, cache key, lock key and generated artifact path must
carry the trusted tenant/workspace scope. A caller-provided scope is rejected
when it conflicts with the host-authenticated context.

| Store | Authority | May contain | Must not contain |
| --- | --- | --- | --- |
| BusinessStore | business database | projects, tasks, members, quotes, delivery facts | chat transcript or vector-only metadata |
| ConversationStore | conversation database | user-visible messages and workspace metadata | tool audit facts or provider secrets |
| SessionStore | append-only session log | turn, tool, approval and lifecycle events | editable business facts |
| WorkspaceKnowledgeStore | workspace knowledge database | resources, versions, accepted rules, ingestion proposals and index outbox | arbitrary cross-workspace records |
| VectorIndex | derived index | embeddings and retrieval metadata | authoritative knowledge facts |
| ResponseCache | derived cache | idempotent model responses | permissions or business truth |
| Telemetry/Episode/Feedback | diagnostic stores | latency, usage, outcome and feedback | direct writes to business truth |

Every read and write must carry the trusted tenant/workspace scope. Vector
indexes must be rebuildable from the authority store. Cache deletion must not
change correctness. New code must use `StorageRegistry` or an injected store
instead of constructing paths independently in UI and API layers.

`StorageRegistry` is process-owned by `RuntimeFactory` and constructs
Conversation, Session, Permission, Workflow, Profile, Knowledge, Wiki,
attachment and artifact services lazily. The Streamlit session state contains
compatibility aliases only; it is not a second dependency container.

`StorageRegistry.readiness()` checks the authoritative stores with a short
cache TTL and returns a per-store status. Production `/ready` is fail-closed:
all required authorities must be reachable and schema-compatible. Optional
vector, model, OCR and MCP capabilities are reported separately and do not
make the core readiness probe pass or fail.

Logical ownership is independent from physical files. A local deployment may
place ConversationStore, SessionStore, WorkspaceKnowledgeStore and derived
indexes below one data root, but each store still owns its schema, lifecycle,
scope checks and migration history. Sharing a SQLite file is not permission to
join tables or copy records across those boundaries.

`MemoryManager.save_document()` is a counted compatibility facade. Canonical
runtimes bind it to `WorkspaceKnowledgeStore`; unbound writes are retired and
raise `LegacyWorkspaceWriteRetiredError`. Migration tests may opt into the old
path explicitly, but new workspace facts must enter
`WorkspaceKnowledgeStore.knowledge_resources`.

Retention, export, compaction, user deletion, tenant offboarding, and derived
vector cleanup are governed by `MEMORY_LIFECYCLE.md` and the executable
`MemoryLifecycleService`. A deletion is not complete while vector cleanup is
pending or dead-lettered.

## Operational Invariants

- Workflow and artifact coordinators are bounded by cache capacity and TTL;
  expired entries are closed before removal.
- Model clients use bounded cache capacity, TTL and in-flight request limits;
  `close()` drains all clients.
- Session writes are durable before best-effort live event publication.
- Tool spill files are scoped, size-bounded, TTL-cleaned and never read from an
  arbitrary path supplied by a model.
- Each authority has contract tests for initialization, readiness, scope
  isolation and concurrent access. A derived cache or vector index can be
  deleted and rebuilt without changing authoritative results.
