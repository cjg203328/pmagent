# Archived Documentation

Status: historical and read-only reference

This directory contains superseded reports, migration notes, screenshots,
roadmaps, MCP guides, and compatibility instructions. Its contents are not
current architecture, storage, API, configuration, or deployment contracts.

## Source Of Truth

Use the current documentation entry points instead:

- `README.md` for installation, startup, and product usage.
- `docs/INDEX.md` for the current documentation map.
- `docs/architecture/CURRENT.md` for runtime and data-boundary contracts.
- `docs/architecture/EXECUTION_MAP.md` for request and event ownership.
- `docs/architecture/STORAGE_CONTRACT.md` for persistence boundaries.
- `docs/operations/DEPENDENCY_PROFILES.md` for install profiles.
- `docs/operations/DEPLOY.md` for production deployment.

## Archive Rules

1. Do not add new implementation or configuration instructions here.
2. Do not link an archived file from a current contract, quickstart, or API
   specification. Link the current document instead.
3. Preserve archived files when they provide audit context; do not silently
   rewrite historical conclusions to match the current implementation.
4. When moving a document here, add a short replacement link in the current
   index or the document that superseded it.
5. Use a dated or clearly historical filename for new archived material.

When an archived document conflicts with a current document, the current
document wins. If the current contract is unclear, update the current
contract first and then archive the obsolete proposal.
