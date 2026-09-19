"""Unit tests for ProviderHealthTracker."""

import time

from artpm_agent.providers.health_tracker import ProviderHealthTracker


class TestProviderHealthTracker:
    """Test provider health tracking and circuit breaker logic."""

    def test_initial_state_healthy(self):
        """Test that all providers start in healthy state."""
        tracker = ProviderHealthTracker(
            failure_threshold=3,
            recovery_timeout=60.0,
        )
        assert tracker.is_healthy("openai")
        assert tracker.is_healthy("anthropic")
        assert tracker.is_healthy("zhipu")

    def test_record_success_keeps_healthy(self):
        """Test that recording success keeps provider healthy."""
        tracker = ProviderHealthTracker(failure_threshold=3, recovery_timeout=60.0)
        tracker.record_success("openai")
        assert tracker.is_healthy("openai")

    def test_single_failure_still_healthy(self):
        """Test that a single failure doesn't mark provider unhealthy."""
        tracker = ProviderHealthTracker(failure_threshold=3, recovery_timeout=60.0)
        tracker.record_failure("openai", Exception("test"))
        assert tracker.is_healthy("openai")

    def test_threshold_failures_mark_unhealthy(self):
        """Test that reaching failure threshold marks provider unhealthy."""
        tracker = ProviderHealthTracker(failure_threshold=3, recovery_timeout=60.0)
        for _ in range(3):
            tracker.record_failure("openai", Exception("test"))
        assert not tracker.is_healthy("openai")

    def test_success_resets_failure_count(self):
        """Test that success resets consecutive failure count."""
        tracker = ProviderHealthTracker(failure_threshold=3, recovery_timeout=60.0)
        tracker.record_failure("openai", Exception("test"))
        tracker.record_failure("openai", Exception("test"))
        tracker.record_success("openai")
        tracker.record_failure("openai", Exception("test"))
        # Should still be healthy after reset
        assert tracker.is_healthy("openai")

    def test_recovery_after_timeout(self):
        """Test that provider recovers after timeout expires."""
        tracker = ProviderHealthTracker(failure_threshold=2, recovery_timeout=0.1)
        tracker.record_failure("openai", Exception("test"))
        tracker.record_failure("openai", Exception("test"))
        assert not tracker.is_healthy("openai")

        # Wait for recovery timeout
        time.sleep(0.15)
        assert tracker.is_healthy("openai")

    def test_independent_provider_tracking(self):
        """Test that different providers are tracked independently."""
        tracker = ProviderHealthTracker(failure_threshold=2, recovery_timeout=60.0)
        tracker.record_failure("openai", Exception("test"))
        tracker.record_failure("openai", Exception("test"))

        assert not tracker.is_healthy("openai")
        assert tracker.is_healthy("anthropic")
        assert tracker.is_healthy("zhipu")

    def test_health_snapshot(self):
        """Test health snapshot returns current state."""
        tracker = ProviderHealthTracker(failure_threshold=2, recovery_timeout=60.0)
        tracker.record_failure("openai", Exception("test"))
        tracker.record_failure("openai", Exception("test"))
        tracker.record_success("anthropic")

        snapshot = tracker.health_snapshot()
        assert "openai" in snapshot
        assert snapshot["openai"]["healthy"] is False
        assert snapshot["openai"]["consecutive_failures"] == 2

    def test_zero_threshold_disables_circuit_breaker(self):
        """Test that zero threshold keeps provider always healthy."""
        tracker = ProviderHealthTracker(failure_threshold=0, recovery_timeout=60.0)
        for _ in range(10):
            tracker.record_failure("openai", Exception("test"))
        assert tracker.is_healthy("openai")

    def test_multiple_failure_types(self):
        """Test tracking different types of failures."""
        tracker = ProviderHealthTracker(failure_threshold=3, recovery_timeout=60.0)
        tracker.record_failure("openai", ConnectionError("network"))
        tracker.record_failure("openai", TimeoutError("timeout"))
        tracker.record_failure("openai", ValueError("invalid"))
        assert not tracker.is_healthy("openai")

    def test_concurrent_access_safety(self):
        """Test that tracker is safe for concurrent access."""
        import threading

        tracker = ProviderHealthTracker(failure_threshold=10, recovery_timeout=60.0)

        def worker():
            for _ in range(100):
                tracker.record_failure("openai", Exception("test"))
                tracker.is_healthy("openai")

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should reach unhealthy state
        assert not tracker.is_healthy("openai")
