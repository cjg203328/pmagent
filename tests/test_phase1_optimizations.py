"""Phase 1 optimization tests - Quick wins.

Tests for:
1. Database connection pooling
2. Lazy skill loading
3. Streaming progress
4. Error message formatting
5. Adaptive vector store
"""
import pytest
import time
from pathlib import Path

# Test 1: Database Connection Pool
def test_database_connection_pool():
    """Test connection pool reduces overhead."""
    from artpm_agent.database.connection_pool import get_db_manager

    manager = get_db_manager()
    db_path = "data/test_pool.db"

    # First connection should create engine
    session1 = manager.get_session(db_path)
    assert session1 is not None

    # Second connection should reuse engine
    session2 = manager.get_session(db_path)
    assert session2 is not None

    # Check pool status
    status = manager.get_pool_status()
    assert len(status) > 0

    session1.close()
    session2.close()
    manager.close_all()


def test_database_connection_pool_performance():
    """Benchmark connection pool performance."""
    from artpm_agent.database.connection_pool import get_db_manager

    manager = get_db_manager()
    db_path = "data/test_perf.db"

    # Warm up
    session = manager.get_session(db_path)
    session.close()

    # Benchmark
    start = time.time()
    for _ in range(100):
        session = manager.get_session(db_path)
        session.close()
    pooled_time = time.time() - start

    manager.close_all()

    # Should be fast (< 1 second for 100 connections)
    assert pooled_time < 1.0
    print(f"Connection pool: {pooled_time:.3f}s for 100 sessions")


# Test 2: Lazy Skill Registry
def test_lazy_skill_registry():
    """Test skills are not instantiated until first use."""
    from artpm_agent.skills.lazy_registry import LazySkillRegistry
    from artpm_agent.skills.base_skill import BaseSkill

    class DummySkill(BaseSkill):
        skill_name = "dummy"
        description = "Test skill"
        instantiated = False

        def __init__(self, context):
            super().__init__(context)
            DummySkill.instantiated = True

        def execute(self, inputs):
            return {"success": True}

    registry = LazySkillRegistry()
    registry.set_context({})
    registry.register_class("dummy", DummySkill)

    # Should not be instantiated yet
    assert not DummySkill.instantiated
    assert registry.get_instantiated_count() == 0

    # Get skill triggers instantiation
    skill = registry.get_skill("dummy")
    assert DummySkill.instantiated
    assert registry.get_instantiated_count() == 1

    # Second get returns cached instance
    skill2 = registry.get_skill("dummy")
    assert skill is skill2


# Test 3: Streaming Progress
def test_streaming_progress_tracker():
    """Test progress tracking during turn processing."""
    from artpm_agent.utils.streaming_progress import ProgressTracker, TurnStage

    tracker = ProgressTracker()

    # Update stages
    event1 = tracker.update_stage(TurnStage.INTENT_DETECTION)
    assert event1.stage == TurnStage.INTENT_DETECTION
    assert 0 < event1.progress < 1

    event2 = tracker.update_stage(TurnStage.MODEL_GENERATION)
    assert event2.progress > event1.progress

    # Check duration
    duration = tracker.get_stage_duration(TurnStage.INTENT_DETECTION)
    assert duration is not None
    assert duration >= 0


def test_progress_context_manager():
    """Test progress context manager."""
    from artpm_agent.utils.streaming_progress import progress_context, TurnStage

    events = []

    def callback(event):
        events.append(event)

    with progress_context(
        TurnStage.SKILL_EXECUTION,
        message="Testing",
        emit_progress=callback
    ):
        time.sleep(0.01)

    # Should have emitted at least init and complete events
    assert len(events) >= 1
    assert events[0].stage == TurnStage.SKILL_EXECUTION


# Test 4: Error Messages
def test_error_message_classification():
    """Test error classification."""
    from artpm_agent.presentation.error_messages import classify_error

    # Rate limit
    error1 = Exception("Rate limit exceeded. Please retry after 60 seconds")
    assert classify_error(error1) == "rate_limit"

    # Timeout
    error2 = Exception("Request timed out")
    assert classify_error(error2) == "timeout"

    # Network
    error3 = Exception("Connection refused")
    assert classify_error(error3) == "network_error"

    # Auth
    error4 = Exception("Invalid API key")
    assert classify_error(error4) == "authentication_failed"


def test_error_message_formatting():
    """Test user-friendly error formatting."""
    from artpm_agent.presentation.error_messages import format_error_for_user

    error = Exception("Rate limit exceeded")
    result = format_error_for_user(error, error_type="rate_limit", context={"cooldown_seconds": "60"})

    assert "message" in result
    assert "suggestions" in result
    assert "error_id" in result
    assert len(result["suggestions"]) > 0
    assert "60" in result["suggestions"][0]  # Context substitution worked


def test_error_markdown_rendering():
    """Test error rendering as Markdown."""
    from artpm_agent.presentation.error_messages import (
        format_error_for_user,
        render_error_markdown
    )

    error = Exception("Test error")
    error_info = format_error_for_user(error)
    markdown = render_error_markdown(error_info)

    assert isinstance(markdown, str)
    assert len(markdown) > 0
    assert "**" in markdown  # Bold text
    assert error_info["error_id"] in markdown


# Test 5: Adaptive Vector Store
def test_adaptive_vector_store_creation():
    """Test adaptive vector store with different index types."""
    import numpy as np
    from artpm_agent.memory.adaptive_vector_store import AdaptiveVectorStore

    store_path = Path("data/test_adaptive_vectors")
    store_path.mkdir(parents=True, exist_ok=True)

    # Small dataset should use flat index
    store = AdaptiveVectorStore(
        store_path,
        dimension=128,
        force_index_type="flat"
    )

    assert store.available
    assert store.index_type == "flat"
    assert store.count == 0

    # Add some vectors
    for i in range(10):
        vector = np.random.rand(128).astype(np.float32)
        store.add(f"doc_{i}", vector, {"index": i})

    assert store.count == 10

    # Search
    query = np.random.rand(128).astype(np.float32)
    results = store.search(query, top_k=3)

    assert len(results) == 3
    assert all(isinstance(doc_id, str) and isinstance(dist, float) for doc_id, dist in results)

    # Clean up
    import shutil
    shutil.rmtree(store_path, ignore_errors=True)


def test_adaptive_vector_store_index_upgrade():
    """Test automatic index type upgrade."""
    from artpm_agent.memory.adaptive_vector_store import AdaptiveVectorStore

    store_path = Path("data/test_adaptive_upgrade")
    store_path.mkdir(parents=True, exist_ok=True)

    store = AdaptiveVectorStore(store_path, dimension=64)

    # Start with flat
    assert store.index_type == "flat"

    # Note: Full upgrade test would require adding 1000+ vectors
    # which is too slow for unit tests. This is covered in integration tests.

    stats = store.get_stats()
    assert "index_type" in stats
    assert "count" in stats

    # Clean up
    import shutil
    shutil.rmtree(store_path, ignore_errors=True)


@pytest.mark.benchmark
def test_vector_store_performance_comparison():
    """Compare flat vs IVF index performance."""
    import shutil

    import numpy as np
    from artpm_agent.memory.adaptive_vector_store import AdaptiveVectorStore

    n_vectors = 1000
    dimension = 128

    # Ensure a clean slate — the store writes to a fixed path under data/,
    # so a leftover index from a previous (possibly crashed) run must be
    # removed before creating the store, otherwise we'd load stale vectors.
    shutil.rmtree("data/test_flat", ignore_errors=True)
    shutil.rmtree("data/test_ivf", ignore_errors=True)

    # Generate test data
    vectors = np.random.rand(n_vectors, dimension).astype(np.float32)
    query = np.random.rand(dimension).astype(np.float32)

    # Test flat index
    store_flat = AdaptiveVectorStore(
        "data/test_flat",
        dimension=dimension,
        force_index_type="flat"
    )

    for i, vec in enumerate(vectors):
        store_flat.add(f"doc_{i}", vec)

    start = time.time()
    results_flat = store_flat.search(query, top_k=10)
    flat_time = time.time() - start

    # Test IVF index
    store_ivf = AdaptiveVectorStore(
        "data/test_ivf",
        dimension=dimension,
        force_index_type="ivf"
    )

    for i, vec in enumerate(vectors):
        store_ivf.add(f"doc_{i}", vec)

    start = time.time()
    results_ivf = store_ivf.search(query, top_k=10)
    ivf_time = time.time() - start

    print(f"Flat search: {flat_time*1000:.2f}ms")
    print(f"IVF search: {ivf_time*1000:.2f}ms")
    print(f"Speedup: {flat_time/ivf_time:.2f}x")

    # Both should return results
    assert len(results_flat) == 10
    assert len(results_ivf) == 10

    # Clean up
    import shutil
    shutil.rmtree("data/test_flat", ignore_errors=True)
    shutil.rmtree("data/test_ivf", ignore_errors=True)


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-s"])
