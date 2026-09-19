"""Unit tests for ModelRequestLimiter."""

import threading
import time

from artpm_agent.providers.request_limiter import ModelRequestLimiter


class TestModelRequestLimiter:
    """Test concurrent request limiting per model."""

    def test_initial_no_limits(self):
        """Test that limiter starts with no active requests."""
        limiter = ModelRequestLimiter()
        snapshot = limiter.slot_snapshot()
        assert len(snapshot) == 0

    def test_single_slot_acquired(self):
        """Test acquiring a single slot."""
        limiter = ModelRequestLimiter()

        with limiter.acquire_slot("gpt-4", max_concurrent=5):
            snapshot = limiter.slot_snapshot()
            assert "gpt-4" in snapshot
            assert snapshot["gpt-4"]["active"] == 1
            assert snapshot["gpt-4"]["limit"] == 5

    def test_slot_released_after_context(self):
        """Test that slot is released after context exits."""
        limiter = ModelRequestLimiter()

        with limiter.acquire_slot("gpt-4", max_concurrent=5):
            pass

        snapshot = limiter.slot_snapshot()
        assert "gpt-4" in snapshot
        assert snapshot["gpt-4"]["active"] == 0

    def test_multiple_concurrent_slots(self):
        """Test multiple concurrent slots for same model."""
        limiter = ModelRequestLimiter()

        with limiter.acquire_slot("gpt-4", max_concurrent=3):
            with limiter.acquire_slot("gpt-4", max_concurrent=3):
                snapshot = limiter.slot_snapshot()
                assert snapshot["gpt-4"]["active"] == 2
                assert snapshot["gpt-4"]["limit"] == 3

    def test_different_models_independent(self):
        """Test that different models are tracked independently."""
        limiter = ModelRequestLimiter()

        with limiter.acquire_slot("gpt-4", max_concurrent=5):
            with limiter.acquire_slot("claude-3", max_concurrent=3):
                snapshot = limiter.slot_snapshot()
                assert snapshot["gpt-4"]["active"] == 1
                assert snapshot["gpt-4"]["limit"] == 5
                assert snapshot["claude-3"]["active"] == 1
                assert snapshot["claude-3"]["limit"] == 3

    def test_blocking_when_limit_reached(self):
        """Test that acquire blocks when limit is reached."""
        limiter = ModelRequestLimiter()
        acquired = []

        def worker(idx):
            with limiter.acquire_slot("gpt-4", max_concurrent=2):
                acquired.append(idx)
                time.sleep(0.1)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        start = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start

        # Should take at least 2 batches (0.1s * 2)
        assert elapsed >= 0.15
        assert len(acquired) == 4

    def test_zero_limit_allows_unlimited(self):
        """Test that zero or negative limit allows unlimited concurrency."""
        limiter = ModelRequestLimiter()

        with limiter.acquire_slot("gpt-4", max_concurrent=0):
            with limiter.acquire_slot("gpt-4", max_concurrent=0):
                with limiter.acquire_slot("gpt-4", max_concurrent=0):
                    snapshot = limiter.slot_snapshot()
                    # Should allow all without blocking
                    assert snapshot["gpt-4"]["active"] == 3

    def test_slot_released_on_exception(self):
        """Test that slot is released even when exception occurs."""
        limiter = ModelRequestLimiter()

        try:
            with limiter.acquire_slot("gpt-4", max_concurrent=5):
                raise ValueError("test error")
        except ValueError:
            pass

        snapshot = limiter.slot_snapshot()
        assert snapshot["gpt-4"]["active"] == 0

    def test_concurrent_access_safety(self):
        """Test that limiter is safe for concurrent access."""
        limiter = ModelRequestLimiter()
        errors = []

        def worker():
            try:
                with limiter.acquire_slot("gpt-4", max_concurrent=10):
                    time.sleep(0.01)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        snapshot = limiter.slot_snapshot()
        assert snapshot["gpt-4"]["active"] == 0

    def test_snapshot_format(self):
        """Test that snapshot returns expected format."""
        limiter = ModelRequestLimiter()

        with limiter.acquire_slot("gpt-4", max_concurrent=5):
            snapshot = limiter.slot_snapshot()

            assert "gpt-4" in snapshot
            entry = snapshot["gpt-4"]
            assert "active" in entry
            assert "limit" in entry
            assert isinstance(entry["active"], int)
            assert isinstance(entry["limit"], int)

    def test_model_with_special_characters(self):
        """Test models with special characters in names."""
        limiter = ModelRequestLimiter()

        with limiter.acquire_slot("gpt-4-32k-0613", max_concurrent=3):
            with limiter.acquire_slot("claude-3-opus-20240229", max_concurrent=2):
                snapshot = limiter.slot_snapshot()
                assert "gpt-4-32k-0613" in snapshot
                assert "claude-3-opus-20240229" in snapshot

    def test_empty_model_name(self):
        """Test that empty model name is handled."""
        limiter = ModelRequestLimiter()

        with limiter.acquire_slot("", max_concurrent=5):
            snapshot = limiter.slot_snapshot()
            assert "" in snapshot
            assert snapshot[""]["active"] == 1

    def test_limit_update_mid_execution(self):
        """Test that limit can be different for different acquisitions."""
        limiter = ModelRequestLimiter()

        with limiter.acquire_slot("gpt-4", max_concurrent=5):
            snapshot1 = limiter.slot_snapshot()
            assert snapshot1["gpt-4"]["limit"] == 5

        with limiter.acquire_slot("gpt-4", max_concurrent=10):
            snapshot2 = limiter.slot_snapshot()
            # Limit should be updated
            assert snapshot2["gpt-4"]["limit"] == 10
