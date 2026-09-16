"""Workspace knowledge schema contract.

The transactional migration implementation remains in
``workspace_knowledge_store.py`` while this module exposes the stable facts
that other layers may use without importing SQLite row internals.
"""

from __future__ import annotations

SCHEMA_VERSION = 7
MIGRATION_TABLE = "knowledge_schema_migrations"

RESOURCE_TABLE = "knowledge_resources"
VERSION_TABLE = "knowledge_versions"
RULE_TABLE = "knowledge_rules"
INGESTION_PROPOSAL_TABLE = "knowledge_ingestion_proposals"
INGESTION_EVENT_TABLE = "knowledge_ingestion_events"
INDEX_OUTBOX_TABLE = "knowledge_index_outbox"

KNOWLEDGE_TABLES = (
    RESOURCE_TABLE,
    VERSION_TABLE,
    RULE_TABLE,
    INGESTION_PROPOSAL_TABLE,
    INGESTION_EVENT_TABLE,
    INDEX_OUTBOX_TABLE,
)


def schema_contract() -> dict[str, object]:
    """Return a serializable description of the knowledge authority boundary."""
    return {
        "version": SCHEMA_VERSION,
        "migration_table": MIGRATION_TABLE,
        "tables": KNOWLEDGE_TABLES,
        "scope_columns": ("tenant_id", "workspace_id"),
        "rebuildable_layers": ("vector_index", "cache"),
        "outbox_states": ("pending", "processing", "done", "dead"),
    }


__all__ = [
    "INDEX_OUTBOX_TABLE",
    "INGESTION_EVENT_TABLE",
    "INGESTION_PROPOSAL_TABLE",
    "KNOWLEDGE_TABLES",
    "MIGRATION_TABLE",
    "RESOURCE_TABLE",
    "RULE_TABLE",
    "SCHEMA_VERSION",
    "VERSION_TABLE",
    "schema_contract",
]
