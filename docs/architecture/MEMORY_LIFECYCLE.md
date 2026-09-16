# Memory Lifecycle Contract

Status: current
Owner: pmagent maintainers

This contract governs authoritative knowledge, conversations, runtime events,
learning memory, derived vector indexes, and caches. Archive documents are not
part of this contract.

## Retention

| Data | Default retention | Rule |
| --- | ---: | --- |
| Current workspace knowledge | indefinite | never expires automatically |
| Accepted rules | indefinite | revoke or explicitly delete |
| Knowledge versions | latest 10 per resource | current version is always retained |
| Conversations | 365 days | deletion remains an explicit user action |
| Session events | 180 days | compaction may remove older operational events |
| Episodes, feedback, strategies | 365 days | scoped to tenant/workspace/principal |
| Completed vector outbox rows | 30 days | safe to compact after projection |
| Dead letters | 90 days | visible until operator retry or expiry |
| Vector index and cache | no authority | may be deleted and rebuilt at any time |

`MemoryRetentionPolicy` is the executable default. Production deployments may
choose shorter diagnostic retention, but must not silently expire current
knowledge or accepted rules.

## User Deletion

Workspace deletion requires an exact `tenant_id:workspace_id` confirmation.
Tenant offboarding requires an exact `tenant_id` confirmation. Both operations
are scope-checked against workspace ownership and purge:

- conversations, messages, session entries, workflow runs, and permission requests;
- knowledge resources, versions, rules, ingestion proposals, and audit events;
- episodes, feedback, strategies, and meta-memory when bound by `RuntimeFactory`;
- derived vector entries through a durable rebuild tombstone.

The operation reports `complete` only after the vector rebuild tombstone is
projected. If the vector backend is unavailable or dead-lettered, it reports
`index_cleanup_pending`; operators must not represent that state as completed
offboarding.

## Export

`MemoryLifecycleService.export_workspace()` returns a JSON-serializable,
tenant-scoped export containing workspace metadata, conversations, messages,
session events, knowledge resources and versions, rules, ingestion records,
and the active retention policy. It is read-only and does not include provider
credentials, cache entries, or vector embeddings.

## Compaction

Compaction is bounded and authority-preserving. It may remove superseded
knowledge versions beyond the configured per-resource limit, expired session
events, completed outbox rows, and expired dead letters. It must never remove a
current resource version, active knowledge fact, or accepted rule.

## Vector Cleanup

The vector index is a derived projection. Every authoritative mutation queues
outbox work in the same SQLite transaction. Projectors atomically claim work
with a lease, retry with exponential backoff, and dead-letter after the bounded
attempt limit. Search falls back to literal retrieval whenever pending,
processing, or dead work exists. Queue depth, dead-letter count, oldest lag,
and next retry time are exposed through `vector_status()`.
