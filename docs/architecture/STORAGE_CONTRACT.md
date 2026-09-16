# Storage Contract

Status: current
Owner: pmagent maintainers

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

Logical ownership is independent from physical files. A local deployment may
place ConversationStore, SessionStore, WorkspaceKnowledgeStore and derived
indexes below one data root, but each store still owns its schema, lifecycle,
scope checks and migration history. Sharing a SQLite file is not permission to
join tables or copy records across those boundaries.

`MemoryManager.save_document()` is a counted compatibility facade. Canonical
runtimes bind it to `WorkspaceKnowledgeStore`; unbound standalone instances
exist only for compatibility tests and legacy integrations. New workspace
facts must enter `WorkspaceKnowledgeStore.knowledge_resources`.
