"""Memory module"""
from .embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
    create_embedding_provider,
)
from .sqlite_manager import SQLiteManager
from .vector_store import VectorStore
from .memory_manager import MemoryManager
from .session_store import SessionEntry, SessionStore
from .workspace_knowledge_store import (
    KnowledgeProposalConflictError,
    WorkspaceKnowledgeStore,
)

__all__ = [
    "SQLiteManager",
    "VectorStore",
    "DeterministicEmbeddingProvider",
    "EmbeddingProvider",
    "create_embedding_provider",
    "MemoryManager",
    "SessionEntry",
    "SessionStore",
    "KnowledgeProposalConflictError",
    "WorkspaceKnowledgeStore",
]
