# Storage Contract

Status: current
Owner: pmagent maintainers

| Store | Authority | May contain | Must not contain |
| --- | --- | --- | --- |
| BusinessStore | business database | projects, tasks, members, quotes, delivery facts | chat transcript or vector-only metadata |
| ConversationStore | conversation database | user-visible messages and workspace metadata | tool audit facts or provider secrets |
| SessionStore | append-only session log | turn, tool, approval and lifecycle events | editable business facts |
| WorkspaceKnowledgeStore | workspace knowledge database | resources, versions, accepted rules and ingestion proposals | arbitrary cross-workspace records |
| VectorIndex | derived index | embeddings and retrieval metadata | authoritative knowledge facts |
| ResponseCache | derived cache | idempotent model responses | permissions or business truth |
| Telemetry/Episode/Feedback | diagnostic stores | latency, usage, outcome and feedback | direct writes to business truth |

Every read and write must carry the trusted tenant/workspace scope. Vector
indexes must be rebuildable from the authority store. Cache deletion must not
change correctness. New code must use `StorageRegistry` or an injected store
instead of constructing paths independently in UI and API layers.

Duplicate knowledge facts in `KnowledgeBase`, `MemoryManager.documents` and
`WorkspaceKnowledgeStore.knowledge_resources` are migration targets. New
features must not add a fourth authority.
