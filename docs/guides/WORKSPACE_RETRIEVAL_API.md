# Workspace and Retrieval API

The REST gateway now exposes a small, provider-neutral contract for the first
workspace-aware web client.

## Workspace list

```http
GET /v1/workspaces
X-Workspace-ID: local-default
X-Actor-ID: alice
X-Tenant-ID: tenant-a
```

The response contains only workspaces owned by the trusted tenant. The
workspace header is the current UI selection and must be sent again after a
switch.

## Workspace creation

`POST /v1/workspaces` requires an `admin` actor role. The request body is:

```json
{
  "id": "art-team",
  "name": "Art Team",
  "profile_id": "local-default",
  "settings": {}
}
```

The server assigns tenant ownership from trusted headers. A client cannot
create a workspace for another tenant by changing the request body.

## Knowledge search

```http
POST /v1/search
X-Workspace-ID: art-team
X-Actor-ID: alice
X-Tenant-ID: tenant-a
Content-Type: application/json
```

```json
{
  "query": "角色贴图报价",
  "limit": 10,
  "include_rules": true
}
```

Search is bounded to the authenticated workspace. The response contains
stable `id`, `title`, `text`, `score`, `match_type`, `source_uri` and citation
metadata. It is safe for a future references drawer or SSE client to consume.

The endpoint is an adapter over `WorkspaceKnowledgeStore`; it does not create
a second vector or knowledge database. Dense/literal search remains local and
optional reranking can be added behind the retrieval contract later.

## Event stream

`POST /v1/chat/stream` exposes the same authenticated workspace scope through
Server-Sent Events. Each frame carries a stable `run_id`, `turn_id` and SSE
event id. The lifecycle is `turn_start`, retrieval/tool/message events,
`snapshot`, then `turn_end`; provider exception text is redacted to a stable
`chat_failed` marker. Clients should persist the last event id and use the
snapshot as the reconnect state boundary.

## Web/embed

The browser client should use these same workspace and citation contracts. The
opt-in embed adapter provides `/embed/{channel}/config`, `/exchange`,
`/session` and `/chat`. Configure `ARTPM_EMBED_ENABLED=1`, a signing secret,
channel workspace mapping and exact allowed origins. The publish token belongs
to the embedding server and is exchanged for a short-lived browser token; it
must not be bundled into widget JavaScript.
