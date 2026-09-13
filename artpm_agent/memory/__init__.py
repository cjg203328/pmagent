"""Memory module — 对话压缩、跨会话记忆与知识库管理。"""
from .embeddings import (
    DeterministicEmbeddingProvider,
    EmbeddingProvider,
    create_embedding_provider,
)
from .sqlite_manager import SQLiteManager
from .vector_store import VectorStore
from .contracts import VectorBackend
from .memory_manager import MemoryManager
from .tencentdb_agent_memory import (
    TencentDBAgentMemoryClient,
    TencentDBAgentMemoryConfigurationError,
    TencentDBAgentMemoryError,
    TencentDBAgentMemoryScope,
    TencentDBAgentMemorySettings,
    create_tencentdb_agent_memory_client,
)
from .session_store import SessionEntry, SessionStore
from .workspace_knowledge_store import (
    KnowledgeProposalConflictError,
    WorkspaceKnowledgeStore,
)
from .wiki_store import WikiPageConflictError, WorkspaceWikiStore
from .conversation_compressor import ConversationCompressor, CompressionResult
from .cross_session_memory import CrossSessionMemory, MemoryItem
from .memory_injector import MemoryInjector

__all__ = [
    "SQLiteManager",
    "VectorStore",
    "VectorBackend",
    "DeterministicEmbeddingProvider",
    "EmbeddingProvider",
    "create_embedding_provider",
    "MemoryManager",
    "TencentDBAgentMemoryClient",
    "TencentDBAgentMemoryConfigurationError",
    "TencentDBAgentMemoryError",
    "TencentDBAgentMemoryScope",
    "TencentDBAgentMemorySettings",
    "create_tencentdb_agent_memory_client",
    "SessionEntry",
    "SessionStore",
    "KnowledgeProposalConflictError",
    "WorkspaceKnowledgeStore",
    "WikiPageConflictError",
    "WorkspaceWikiStore",
    # 新增 — 记忆系统核心
    "ConversationCompressor",
    "CompressionResult",
    "CrossSessionMemory",
    "MemoryItem",
    "MemoryInjector",
]
