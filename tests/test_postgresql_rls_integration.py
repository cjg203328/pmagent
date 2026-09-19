"""PostgreSQL RLS integration tests for multi-tenant isolation.

This test suite validates that Row-Level Security policies correctly enforce
tenant and workspace boundaries in PostgreSQL. It requires a running PostgreSQL
instance with the RLS policies deployed.

Test scenarios:
1. Different tenant_id cannot access each other's data
2. Same tenant_id but different workspace_id cannot access each other's data
3. ConversationStore, SessionStore, WorkspaceKnowledgeStore, WorkflowStore RLS works
4. Episode and Feedback stores enforce tenant isolation

Setup requirements:
- PostgreSQL 12+ running locally or via docker-compose
- Database initialized with schema and RLS policies
- Connection string in environment: POSTGRES_TEST_URL

Skip behavior:
- Tests are skipped if PostgreSQL is unavailable
- Tests are skipped if POSTGRES_TEST_URL is not set
"""

import os
import pytest

# Skip all tests if PostgreSQL is not configured
POSTGRES_URL = os.getenv("POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="PostgreSQL integration tests require POSTGRES_TEST_URL environment variable",
)


@pytest.fixture
def postgres_connection():
    """Provide a PostgreSQL connection for testing."""
    if not POSTGRES_URL:
        pytest.skip("POSTGRES_TEST_URL not set")

    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not installed")

    conn = psycopg2.connect(POSTGRES_URL)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def authenticated_connection(postgres_connection):
    """Factory for connections with different tenant/workspace contexts."""

    def _connect(tenant_id: str, workspace_id: str):
        cursor = postgres_connection.cursor()
        cursor.execute("SET LOCAL app.tenant_id = %s", (tenant_id,))
        cursor.execute("SET LOCAL app.workspace_id = %s", (workspace_id,))
        return cursor

    return _connect


def test_rls_policies_exist(postgres_connection):
    """Verify that RLS policies are deployed on all required tables."""
    cursor = postgres_connection.cursor()
    cursor.execute("""
        SELECT tablename, policyname
        FROM pg_policies
        WHERE schemaname = 'public'
        ORDER BY tablename, policyname
    """)
    policies = cursor.fetchall()

    # Expected tables with RLS policies
    expected_tables = {
        "conversations",
        "episodes",
        "feedback",
        "knowledge_resources",
        "outbox_jobs",
        "profiles",
        "sessions",
        "workflows",
    }

    tables_with_policies = {row[0] for row in policies}

    # Verify all expected tables have at least one policy
    for table in expected_tables:
        assert table in tables_with_policies, f"Table {table} missing RLS policies"


def test_episode_store_tenant_isolation(authenticated_connection, postgres_connection):
    """Test that episodes are isolated by tenant_id."""
    # Setup: Insert episode for tenant_a
    cursor_a = authenticated_connection("tenant_a", "workspace_1")
    cursor_a.execute("""
        INSERT INTO episodes (id, turn_id, conversation_id, handler, success, tenant_id, workspace_id, created_at)
        VALUES ('ep_a', 'turn_a', 'conv_a', 'model', 1, 'tenant_a', 'workspace_1', NOW())
    """)
    postgres_connection.commit()

    # Setup: Insert episode for tenant_b
    cursor_b = authenticated_connection("tenant_b", "workspace_1")
    cursor_b.execute("""
        INSERT INTO episodes (id, turn_id, conversation_id, handler, success, tenant_id, workspace_id, created_at)
        VALUES ('ep_b', 'turn_b', 'conv_b', 'skill', 1, 'tenant_b', 'workspace_1', NOW())
    """)
    postgres_connection.commit()

    # Test: tenant_a can only see its own episode
    cursor_a = authenticated_connection("tenant_a", "workspace_1")
    cursor_a.execute("SELECT id FROM episodes")
    rows = cursor_a.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "ep_a"

    # Test: tenant_b can only see its own episode
    cursor_b = authenticated_connection("tenant_b", "workspace_1")
    cursor_b.execute("SELECT id FROM episodes")
    rows = cursor_b.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "ep_b"

    # Cleanup
    cursor_a = authenticated_connection("tenant_a", "workspace_1")
    cursor_a.execute("DELETE FROM episodes WHERE id = 'ep_a'")
    cursor_b = authenticated_connection("tenant_b", "workspace_1")
    cursor_b.execute("DELETE FROM episodes WHERE id = 'ep_b'")
    postgres_connection.commit()


def test_feedback_store_workspace_isolation(
    authenticated_connection, postgres_connection
):
    """Test that feedback is isolated by workspace_id within same tenant."""
    # Setup: Insert feedback for workspace_1
    cursor_1 = authenticated_connection("tenant_a", "workspace_1")
    cursor_1.execute("""
        INSERT INTO feedback (id, kind, content, scope, tenant_id, workspace_id, weight, active, created_at)
        VALUES ('fb_1', 'preference', 'concise replies', 'global', 'tenant_a', 'workspace_1', 1.0, 1, NOW())
    """)
    postgres_connection.commit()

    # Setup: Insert feedback for workspace_2
    cursor_2 = authenticated_connection("tenant_a", "workspace_2")
    cursor_2.execute("""
        INSERT INTO feedback (id, kind, content, scope, tenant_id, workspace_id, weight, active, created_at)
        VALUES ('fb_2', 'correction', 'avoid jargon', 'global', 'tenant_a', 'workspace_2', 1.0, 1, NOW())
    """)
    postgres_connection.commit()

    # Test: workspace_1 can only see its own feedback
    cursor_1 = authenticated_connection("tenant_a", "workspace_1")
    cursor_1.execute("SELECT id FROM feedback")
    rows = cursor_1.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "fb_1"

    # Test: workspace_2 can only see its own feedback
    cursor_2 = authenticated_connection("tenant_a", "workspace_2")
    cursor_2.execute("SELECT id FROM feedback")
    rows = cursor_2.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "fb_2"

    # Cleanup
    cursor_1 = authenticated_connection("tenant_a", "workspace_1")
    cursor_1.execute("DELETE FROM feedback WHERE id = 'fb_1'")
    cursor_2 = authenticated_connection("tenant_a", "workspace_2")
    cursor_2.execute("DELETE FROM feedback WHERE id = 'fb_2'")
    postgres_connection.commit()


def test_knowledge_resources_rls(authenticated_connection, postgres_connection):
    """Test that knowledge resources are isolated by tenant and workspace."""
    # Setup: Insert resource for tenant_a/workspace_1
    cursor_a1 = authenticated_connection("tenant_a", "workspace_1")
    cursor_a1.execute("""
        INSERT INTO knowledge_resources (
            id, tenant_id, workspace_id, resource_key, resource_type,
            current_version, consolidation_status, created_at, updated_at
        )
        VALUES (
            'kr_a1', 'tenant_a', 'workspace_1', 'doc_a1', 'document',
            1, 'active', NOW(), NOW()
        )
    """)
    postgres_connection.commit()

    # Setup: Insert resource for tenant_b/workspace_1
    cursor_b1 = authenticated_connection("tenant_b", "workspace_1")
    cursor_b1.execute("""
        INSERT INTO knowledge_resources (
            id, tenant_id, workspace_id, resource_key, resource_type,
            current_version, consolidation_status, created_at, updated_at
        )
        VALUES (
            'kr_b1', 'tenant_b', 'workspace_1', 'doc_b1', 'document',
            1, 'active', NOW(), NOW()
        )
    """)
    postgres_connection.commit()

    # Test: tenant_a cannot see tenant_b's resources
    cursor_a1 = authenticated_connection("tenant_a", "workspace_1")
    cursor_a1.execute("SELECT id FROM knowledge_resources")
    rows = cursor_a1.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "kr_a1"

    # Test: tenant_b cannot see tenant_a's resources
    cursor_b1 = authenticated_connection("tenant_b", "workspace_1")
    cursor_b1.execute("SELECT id FROM knowledge_resources")
    rows = cursor_b1.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "kr_b1"

    # Cleanup
    cursor_a1 = authenticated_connection("tenant_a", "workspace_1")
    cursor_a1.execute("DELETE FROM knowledge_resources WHERE id = 'kr_a1'")
    cursor_b1 = authenticated_connection("tenant_b", "workspace_1")
    cursor_b1.execute("DELETE FROM knowledge_resources WHERE id = 'kr_b1'")
    postgres_connection.commit()


def test_outbox_jobs_rls(authenticated_connection, postgres_connection):
    """Test that outbox jobs are isolated by tenant and workspace."""
    # Setup: Insert job for tenant_a/workspace_1
    cursor_a1 = authenticated_connection("tenant_a", "workspace_1")
    cursor_a1.execute("""
        INSERT INTO outbox_jobs (
            id, tenant_id, workspace_id, turn_id, job_type, payload,
            status, attempts, available_at, created_at, updated_at
        )
        VALUES (
            'job_a1', 'tenant_a', 'workspace_1', 'turn_a1', 'feedback', '{}',
            'pending', 0, NOW(), NOW(), NOW()
        )
    """)
    postgres_connection.commit()

    # Setup: Insert job for tenant_a/workspace_2
    cursor_a2 = authenticated_connection("tenant_a", "workspace_2")
    cursor_a2.execute("""
        INSERT INTO outbox_jobs (
            id, tenant_id, workspace_id, turn_id, job_type, payload,
            status, attempts, available_at, created_at, updated_at
        )
        VALUES (
            'job_a2', 'tenant_a', 'workspace_2', 'turn_a2', 'episode', '{}',
            'pending', 0, NOW(), NOW(), NOW()
        )
    """)
    postgres_connection.commit()

    # Test: workspace_1 cannot see workspace_2's jobs
    cursor_a1 = authenticated_connection("tenant_a", "workspace_1")
    cursor_a1.execute("SELECT id FROM outbox_jobs")
    rows = cursor_a1.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "job_a1"

    # Test: workspace_2 cannot see workspace_1's jobs
    cursor_a2 = authenticated_connection("tenant_a", "workspace_2")
    cursor_a2.execute("SELECT id FROM outbox_jobs")
    rows = cursor_a2.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "job_a2"

    # Cleanup
    cursor_a1 = authenticated_connection("tenant_a", "workspace_1")
    cursor_a1.execute("DELETE FROM outbox_jobs WHERE id = 'job_a1'")
    cursor_a2 = authenticated_connection("tenant_a", "workspace_2")
    cursor_a2.execute("DELETE FROM outbox_jobs WHERE id = 'job_a2'")
    postgres_connection.commit()


def test_rls_insert_prevention(authenticated_connection, postgres_connection):
    """Test that RLS prevents inserting data with wrong tenant/workspace."""
    cursor = authenticated_connection("tenant_a", "workspace_1")

    # Attempt to insert episode with mismatched tenant_id
    with pytest.raises(Exception):  # Should raise a CHECK constraint or RLS violation
        cursor.execute("""
            INSERT INTO episodes (id, turn_id, conversation_id, handler, success, tenant_id, workspace_id, created_at)
            VALUES ('ep_x', 'turn_x', 'conv_x', 'model', 1, 'tenant_b', 'workspace_1', NOW())
        """)
        postgres_connection.commit()

    postgres_connection.rollback()
