-- PostgreSQL Row-Level Security policies for tenant/workspace isolation.
-- Prerequisites: target tables must already exist with tenant_id and
-- workspace_id columns. The application must set LOCAL app.tenant_id and
-- app.workspace_id at the start of every transaction.

-- ============================================================================
-- Helper function: Get current authenticated tenant_id from session
-- ============================================================================
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS TEXT AS $$
BEGIN
    RETURN current_setting('app.tenant_id', true);
EXCEPTION
    WHEN OTHERS THEN
        RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER;

-- ============================================================================
-- Helper function: Get current authenticated workspace_id from session
-- ============================================================================
CREATE OR REPLACE FUNCTION current_workspace_id() RETURNS TEXT AS $$
BEGIN
    RETURN current_setting('app.workspace_id', true);
EXCEPTION
    WHEN OTHERS THEN
        RETURN NULL;
END;
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER;

-- ============================================================================
-- ConversationStore: conversations table
-- ============================================================================
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS conversations_tenant_isolation ON conversations;
DROP POLICY IF EXISTS conversations_insert ON conversations;
CREATE POLICY conversations_tenant_isolation ON conversations
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY conversations_insert ON conversations
    FOR INSERT
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

-- ============================================================================
-- ConversationStore: conversation_messages table
-- ============================================================================
ALTER TABLE conversation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_messages FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS conversation_messages_tenant_isolation ON conversation_messages;
DROP POLICY IF EXISTS conversation_messages_insert ON conversation_messages;
CREATE POLICY conversation_messages_tenant_isolation ON conversation_messages
    USING (
        conversation_id IN (
            SELECT id FROM conversations
            WHERE tenant_id = current_tenant_id() AND workspace_id = current_workspace_id()
        )
    );

CREATE POLICY conversation_messages_insert ON conversation_messages
    FOR INSERT
    WITH CHECK (
        conversation_id IN (
            SELECT id FROM conversations
            WHERE tenant_id = current_tenant_id() AND workspace_id = current_workspace_id()
        )
    );

-- ============================================================================
-- SessionStore: sessions table
-- ============================================================================
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS sessions_tenant_isolation ON sessions;
DROP POLICY IF EXISTS sessions_insert ON sessions;
CREATE POLICY sessions_tenant_isolation ON sessions
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY sessions_insert ON sessions
    FOR INSERT
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

-- ============================================================================
-- WorkspaceKnowledgeStore: knowledge_resources table
-- ============================================================================
ALTER TABLE knowledge_resources ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_resources FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS knowledge_resources_tenant_isolation ON knowledge_resources;
DROP POLICY IF EXISTS knowledge_resources_insert ON knowledge_resources;
DROP POLICY IF EXISTS knowledge_resources_update ON knowledge_resources;
CREATE POLICY knowledge_resources_tenant_isolation ON knowledge_resources
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY knowledge_resources_insert ON knowledge_resources
    FOR INSERT
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY knowledge_resources_update ON knowledge_resources
    FOR UPDATE
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id())
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

-- ============================================================================
-- WorkspaceKnowledgeStore: knowledge_versions table
-- ============================================================================
ALTER TABLE knowledge_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_versions FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS knowledge_versions_tenant_isolation ON knowledge_versions;
DROP POLICY IF EXISTS knowledge_versions_insert ON knowledge_versions;
CREATE POLICY knowledge_versions_tenant_isolation ON knowledge_versions
    USING (
        resource_id IN (
            SELECT id FROM knowledge_resources
            WHERE tenant_id = current_tenant_id() AND workspace_id = current_workspace_id()
        )
    );

CREATE POLICY knowledge_versions_insert ON knowledge_versions
    FOR INSERT
    WITH CHECK (
        resource_id IN (
            SELECT id FROM knowledge_resources
            WHERE tenant_id = current_tenant_id() AND workspace_id = current_workspace_id()
        )
    );

-- ============================================================================
-- EpisodeStore: episodes table
-- ============================================================================
ALTER TABLE episodes ENABLE ROW LEVEL SECURITY;
ALTER TABLE episodes FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS episodes_tenant_isolation ON episodes;
DROP POLICY IF EXISTS episodes_insert ON episodes;
CREATE POLICY episodes_tenant_isolation ON episodes
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY episodes_insert ON episodes
    FOR INSERT
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

-- ============================================================================
-- FeedbackStore: feedback table
-- ============================================================================
ALTER TABLE feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS feedback_tenant_isolation ON feedback;
DROP POLICY IF EXISTS feedback_insert ON feedback;
DROP POLICY IF EXISTS feedback_update ON feedback;
CREATE POLICY feedback_tenant_isolation ON feedback
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY feedback_insert ON feedback
    FOR INSERT
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY feedback_update ON feedback
    FOR UPDATE
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id())
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

-- ============================================================================
-- WorkflowStore: workflows table
-- ============================================================================
ALTER TABLE workflows ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflows FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS workflows_tenant_isolation ON workflows;
DROP POLICY IF EXISTS workflows_insert ON workflows;
DROP POLICY IF EXISTS workflows_update ON workflows;
CREATE POLICY workflows_tenant_isolation ON workflows
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY workflows_insert ON workflows
    FOR INSERT
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY workflows_update ON workflows
    FOR UPDATE
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id())
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

-- ============================================================================
-- ProfileStore: profiles table
-- ============================================================================
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE profiles FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS profiles_tenant_isolation ON profiles;
DROP POLICY IF EXISTS profiles_insert ON profiles;
DROP POLICY IF EXISTS profiles_update ON profiles;
CREATE POLICY profiles_tenant_isolation ON profiles
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY profiles_insert ON profiles
    FOR INSERT
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY profiles_update ON profiles
    FOR UPDATE
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id())
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

-- ============================================================================
-- OutboxStore: outbox_jobs table
-- ============================================================================
ALTER TABLE outbox_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox_jobs FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS outbox_jobs_tenant_isolation ON outbox_jobs;
DROP POLICY IF EXISTS outbox_jobs_insert ON outbox_jobs;
DROP POLICY IF EXISTS outbox_jobs_update ON outbox_jobs;
CREATE POLICY outbox_jobs_tenant_isolation ON outbox_jobs
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY outbox_jobs_insert ON outbox_jobs
    FOR INSERT
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

CREATE POLICY outbox_jobs_update ON outbox_jobs
    FOR UPDATE
    USING (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id())
    WITH CHECK (tenant_id = current_tenant_id() AND workspace_id = current_workspace_id());

-- ============================================================================
-- Performance notes
-- ============================================================================
-- RLS policies add a WHERE clause to every query. Ensure indexes exist on:
-- - (tenant_id, workspace_id) for all tables
-- - (tenant_id, workspace_id, created_at) for time-series queries
-- - (tenant_id, workspace_id, status) for outbox polling

-- Monitor query plans with EXPLAIN to verify index usage:
-- EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM episodes WHERE handler = 'skill';
