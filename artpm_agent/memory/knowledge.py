"""Knowledge authority boundary exports.

This namespace groups pure schema, validation, serialization, and vector
helpers without changing the historical ``memory.workspace_knowledge_store``
import path.
"""

from .resources import (
    content_hash,
    deserialize_json,
    normalize_filter,
    normalize_searchable_text,
    optional_text,
    required_text,
    serialize_json,
    validate_limit,
)
from .rules import (
    CONFIRMER_TYPES,
    RULE_STATUSES,
    normalize_rule_statement,
    validate_confirmer_type,
    validate_rule_status,
)
from .schema import SCHEMA_VERSION, schema_contract
from .serializers import serialize_resource, serialize_rule, serialize_version
from .vector_index import chunk_text

__all__ = [
    "CONFIRMER_TYPES",
    "RULE_STATUSES",
    "SCHEMA_VERSION",
    "chunk_text",
    "content_hash",
    "deserialize_json",
    "normalize_filter",
    "normalize_rule_statement",
    "normalize_searchable_text",
    "optional_text",
    "required_text",
    "schema_contract",
    "serialize_json",
    "serialize_resource",
    "serialize_rule",
    "serialize_version",
    "validate_confirmer_type",
    "validate_limit",
    "validate_rule_status",
]
