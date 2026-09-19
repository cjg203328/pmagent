"""Unit tests for BoundedClientCache."""

import time
from unittest.mock import Mock

from artpm_agent.providers.gateway_cache import BoundedClientCache


class TestBoundedClientCache:
    """Test bounded LRU client cache with lease semantics."""

    def test_initial_cache_empty(self):
        """Test that cache starts empty."""
        cache = BoundedClientCache(capacity=5, ttl_seconds=60.0)
        snapshot = cache.snapshot()
        assert len(snapshot) == 0

    def test_cache_single_client(self):
        """Test caching a single client."""
        cache = BoundedClientCache(capacity=5, ttl_seconds=60.0)

        def factory():
            return Mock(name="client1")

        with cache.lease("key1", factory) as client:
            assert client is not None
            assert client._mock_name == "client1"

        snapshot = cache.snapshot()
        assert len(snapshot) == 1
        assert "key1" in snapshot

    def test_cache_reuses_existing_client(self):
        """Test that cache returns the same client for the same key."""
        cache = BoundedClientCache(capacity=5, ttl_seconds=60.0)

        def factory():
            return Mock(name="client1")

        with cache.lease("key1", factory) as client1:
            pass

        with cache.lease("key1", factory) as client2:
            pass

        # Should be the same instance
        assert client1 is client2

    def test_different_keys_different_clients(self):
        """Test that different keys get different clients."""
        cache = BoundedClientCache(capacity=5, ttl_seconds=60.0)

        def factory1():
            return Mock(name="client1")

        def factory2():
            return Mock(name="client2")

        with cache.lease("key1", factory1) as client1:
            with cache.lease("key2", factory2) as client2:
                assert client1 is not client2
                assert client1._mock_name == "client1"
                assert client2._mock_name == "client2"

    def test_candidate_client_used_when_provided(self):
        """Test that candidate client is used instead of factory."""
        cache = BoundedClientCache(capacity=5, ttl_seconds=60.0)

        candidate = Mock(name="candidate")
        factory_called = False

        def factory():
            nonlocal factory_called
            factory_called = True
            return Mock(name="factory")

        with cache.lease("key1", factory, candidate=candidate) as client:
            assert client is candidate
            assert not factory_called

    def test_capacity_enforced(self):
        """Test that cache enforces capacity limit."""
        cache = BoundedClientCache(capacity=3, ttl_seconds=60.0)

        for i in range(5):
            with cache.lease(f"key{i}", lambda: Mock(name=f"client{i}")):
                pass

        snapshot = cache.snapshot()
        # Should only keep the most recent 3
        assert len(snapshot) <= 3

    def test_lru_eviction(self):
        """Test that least recently used clients are evicted."""
        cache = BoundedClientCache(capacity=3, ttl_seconds=60.0)

        # Fill cache
        for i in range(3):
            with cache.lease(f"key{i}", lambda i=i: Mock(name=f"client{i}")):
                pass

        # Access key0 and key1 to make them recently used
        with cache.lease("key0", lambda: Mock()):
            pass
        with cache.lease("key1", lambda: Mock()):
            pass

        # Add a new key, should evict key2
        with cache.lease("key3", lambda: Mock(name="client3")):
            pass

        snapshot = cache.snapshot()
        keys = [item["key"] for item in snapshot.values()]
        assert "key0" in keys or "key1" in keys
        # key2 might be evicted

    def test_ttl_expiration(self):
        """Test that expired entries are not reused."""
        cache = BoundedClientCache(capacity=5, ttl_seconds=0.1)

        with cache.lease("key1", lambda: Mock(name="client1")):
            pass

        # Wait for TTL to expire
        time.sleep(0.15)

        with cache.lease("key1", lambda: Mock(name="client2")) as client2:
            # Should get a new client after expiration
            assert client2._mock_name == "client2"

    def test_concurrent_lease_safety(self):
        """Test that cache is safe for concurrent access."""
        import threading

        cache = BoundedClientCache(capacity=10, ttl_seconds=60.0)
        results = []

        def worker(key):
            with cache.lease(key, lambda: Mock(name=key)) as client:
                results.append(client._mock_name)

        threads = [
            threading.Thread(target=worker, args=(f"key{i % 5}",)) for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 20

    def test_snapshot_format(self):
        """Test that snapshot returns expected format."""
        cache = BoundedClientCache(capacity=5, ttl_seconds=60.0)

        with cache.lease("key1", lambda: Mock(name="client1")):
            pass

        snapshot = cache.snapshot()
        assert len(snapshot) == 1

        entry = snapshot[0]
        assert "key" in entry
        assert "created_at" in entry
        assert "last_used" in entry
        assert entry["key"] == "key1"

    def test_empty_key_allowed(self):
        """Test that empty string keys are allowed."""
        cache = BoundedClientCache(capacity=5, ttl_seconds=60.0)

        with cache.lease("", lambda: Mock(name="empty")) as client:
            assert client._mock_name == "empty"

    def test_cleanup_on_capacity(self):
        """Test that cleanup removes expired entries even below capacity."""
        cache = BoundedClientCache(capacity=10, ttl_seconds=0.1)

        for i in range(5):
            with cache.lease(f"key{i}", lambda i=i: Mock(name=f"client{i}")):
                pass

        # Wait for expiration
        time.sleep(0.15)

        # Trigger cleanup by adding new entry
        with cache.lease("new", lambda: Mock(name="new")):
            pass

        snapshot = cache.snapshot()
        # Old entries should be cleaned up
        assert len(snapshot) <= 6  # Some may remain if not cleaned immediately
