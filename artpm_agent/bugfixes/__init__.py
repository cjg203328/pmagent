"""Bug fixes and improvements summary.

This module documents all identified issues and their fixes.
"""

# ============================================================================
# Phase 5: Bug Fixes and Code Quality Improvements
# ============================================================================

"""
IDENTIFIED ISSUES AND FIXES:

1. Import Error in test_turn_flow.py
   - Issue: Missing import for contextmanager
   - Fix: Corrected import statement
   - Status: Fixed in integration tests

2. TODO Markers in Production Code
   - Files with TODO/FIXME:
     * artpm_agent/core/mcp_skills.py
     * artpm_agent/memory/cross_session_memory.py
     * artpm_agent/memory/memory_injector.py
     * artpm_agent/skills/base_skill.py
     * artpm_agent/utils/logger.py
   - Action: Document TODOs, prioritize for future phases
   - Status: Tracked but non-blocking

3. Test Naming Conventions
   - Issue: Some tests use descriptive names that look like errors
   - Fix: These are intentional test names (e.g., "test_error_result")
   - Status: No action needed

4. Phase 1 Optimizations Integration
   - Issue: New modules not yet integrated into main codebase
   - Fix: Integration adapters created (see below)
   - Status: Ready for integration

## INTEGRATION ADAPTERS
"""

from typing import Any, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class OptimizationRegistry:
    """Central registry for enabling/disabling optimizations."""

    _instance = None
    _enabled_optimizations: Dict[str, bool] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_defaults()
        return cls._instance

    def _init_defaults(self):
        """Initialize default optimization settings."""
        self._enabled_optimizations = {
            # Phase 1: Quick wins
            "connection_pooling": True,
            "lazy_skill_loading": True,
            "streaming_progress": True,
            "friendly_errors": True,
            "adaptive_vectors": True,

            # Phase 2: Algorithm optimizations
            "hybrid_intent_scoring": True,
            "model_health_tracking": True,

            # Phase 3: Architecture (future)
            "async_support": False,  # Not yet implemented
            "agent_decomposition": False,  # Not yet implemented
        }

    def is_enabled(self, optimization_name: str) -> bool:
        """Check if optimization is enabled."""
        return self._enabled_optimizations.get(optimization_name, False)

    def enable(self, optimization_name: str) -> None:
        """Enable an optimization."""
        self._enabled_optimizations[optimization_name] = True
        logger.info(f"Enabled optimization: {optimization_name}")

    def disable(self, optimization_name: str) -> None:
        """Disable an optimization."""
        self._enabled_optimizations[optimization_name] = False
        logger.info(f"Disabled optimization: {optimization_name}")

    def get_status(self) -> Dict[str, bool]:
        """Get status of all optimizations."""
        return dict(self._enabled_optimizations)


def get_optimization_registry() -> OptimizationRegistry:
    """Get global optimization registry."""
    return OptimizationRegistry()


# ============================================================================
# BACKWARD COMPATIBILITY WRAPPERS
# ============================================================================

def get_database_session_safe(db_path: str, **kwargs) -> Any:
    """Get database session with optional connection pooling.

    Falls back to direct connection if pooling disabled.
    """
    registry = get_optimization_registry()

    if registry.is_enabled("connection_pooling"):
        try:
            from artpm_agent.database.connection_pool import get_session
            return get_session(db_path, **kwargs)
        except Exception as e:
            logger.warning(f"Connection pooling failed, using direct connection: {e}")

    # Fallback to direct connection
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{db_path}")
    Session = sessionmaker(bind=engine)
    return Session()


def format_error_safe(error: Exception, **kwargs) -> str:
    """Format error with optional friendly messages.

    Falls back to str(error) if formatter unavailable.
    """
    registry = get_optimization_registry()

    if registry.is_enabled("friendly_errors"):
        try:
            from artpm_agent.presentation.error_messages import (
                format_error_for_user,
                render_error_markdown
            )
            error_info = format_error_for_user(error, **kwargs)
            return render_error_markdown(error_info)
        except Exception as e:
            logger.warning(f"Friendly error formatting failed: {e}")

    # Fallback to simple string
    return f"错误: {str(error)}"


def get_vector_store_safe(store_path: str, **kwargs) -> Any:
    """Get vector store with optional adaptive indexing.

    Falls back to standard VectorStore if adaptive unavailable.
    """
    registry = get_optimization_registry()

    if registry.is_enabled("adaptive_vectors"):
        try:
            from artpm_agent.memory.adaptive_vector_store import AdaptiveVectorStore
            return AdaptiveVectorStore(store_path, **kwargs)
        except Exception as e:
            logger.warning(f"Adaptive vector store failed, using standard: {e}")

    # Fallback to standard vector store
    from artpm_agent.memory.vector_store import VectorStore
    return VectorStore(store_path, **kwargs)


# ============================================================================
# KNOWN LIMITATIONS
# ============================================================================

"""
CURRENT LIMITATIONS:

1. Connection Pooling
   - SQLite WAL mode requires filesystem support
   - Some network filesystems may not work
   - Workaround: Disable connection_pooling for unsupported filesystems

2. Adaptive Vector Store
   - IVF index requires at least nlist vectors for training
   - Falls back to Flat index if insufficient data
   - No action needed - automatic fallback

3. Lazy Skill Loading
   - Skills with inter-dependencies must be loaded in order
   - Current implementation handles this via initialization order
   - Future: Dependency graph resolution

4. Streaming Progress
   - Requires UI support for progress callbacks
   - Gracefully degrades if no callback provided
   - No action needed

5. Hybrid Intent Scoring
   - Requires all three signals (keyword, embedding, LLM) for optimal performance
   - Works with partial signals but confidence may be lower
   - Future: Better handling of missing signals

## FUTURE IMPROVEMENTS

1. Async/await support throughout codebase
2. Complete Agent class decomposition
3. Pydantic models for all data structures
4. GraphQL API layer
5. WebSocket support for real-time updates
6. Multi-tenancy support
7. Advanced caching strategies
8. Distributed tracing
9. Auto-scaling support
10. Edge deployment capabilities
"""

# ============================================================================
# BUG FIX CHECKLIST
# ============================================================================

BUG_FIX_CHECKLIST = {
    "critical": [
        {"id": "C001", "description": "Database connection leak", "status": "fixed", "pr": "connection_pool.py"},
        {"id": "C002", "description": "Memory growth in long sessions", "status": "fixed", "pr": "lazy_registry.py"},
    ],
    "high": [
        {"id": "H001", "description": "Slow startup time", "status": "fixed", "pr": "lazy_registry.py"},
        {"id": "H002", "description": "Vector search performance", "status": "fixed", "pr": "adaptive_vector_store.py"},
        {"id": "H003", "description": "Intent routing accuracy", "status": "fixed", "pr": "hybrid_intent_scorer.py"},
    ],
    "medium": [
        {"id": "M001", "description": "Error messages not user-friendly", "status": "fixed", "pr": "error_messages.py"},
        {"id": "M002", "description": "No progress feedback", "status": "fixed", "pr": "streaming_progress.py"},
        {"id": "M003", "description": "Model failover too aggressive", "status": "fixed", "pr": "model_health_tracker.py"},
    ],
    "low": [
        {"id": "L001", "description": "TODO markers in code", "status": "documented", "pr": "N/A"},
        {"id": "L002", "description": "Missing type hints", "status": "in_progress", "pr": "Phase 3"},
    ]
}


def get_bug_fix_summary() -> Dict[str, Any]:
    """Get summary of bug fixes."""
    summary = {
        "total": 0,
        "fixed": 0,
        "in_progress": 0,
        "documented": 0
    }

    for severity, bugs in BUG_FIX_CHECKLIST.items():
        for bug in bugs:
            summary["total"] += 1
            if bug["status"] == "fixed":
                summary["fixed"] += 1
            elif bug["status"] == "in_progress":
                summary["in_progress"] += 1
            elif bug["status"] == "documented":
                summary["documented"] += 1

    return summary


if __name__ == "__main__":
    # Print bug fix summary
    summary = get_bug_fix_summary()
    print("Bug Fix Summary:")
    print(f"  Total: {summary['total']}")
    print(f"  Fixed: {summary['fixed']}")
    print(f"  In Progress: {summary['in_progress']}")
    print(f"  Documented: {summary['documented']}")
    print(f"  Fix Rate: {summary['fixed']/summary['total']*100:.1f}%")
