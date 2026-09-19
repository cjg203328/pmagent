# PostgreSQL Production Deployment Guide

Status: current
Owner: pmagent maintainers

## Overview

This guide describes the PostgreSQL configuration requirements for production deployment of the Agent Runtime system. PostgreSQL is the required database for multi-tenant production deployments, providing Row-Level Security (RLS) for tenant isolation, ACID guarantees, and horizontal scalability.

**Local development** uses SQLite for simplicity. **Production** requires PostgreSQL 12+ with RLS policies deployed.

---

## Prerequisites

- PostgreSQL 12+ (RLS support required)
- Connection pooler (PgBouncer or AWS RDS Proxy recommended)
- Backup solution configured (pg_dump, WAL archiving, or managed backup)
- Monitoring (pg_stat_statements, slow query log)

---

## Database Setup

### 1. Create Database and User

```sql
-- Create database
CREATE DATABASE pmagent_production;

-- Create application user (restricted privileges)
CREATE USER pmagent_app WITH PASSWORD '<secure-password>';

-- Grant minimal privileges
GRANT CONNECT ON DATABASE pmagent_production TO pmagent_app;
GRANT USAGE ON SCHEMA public TO pmagent_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pmagent_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pmagent_app;

-- Grant sequence usage for auto-incrementing IDs
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pmagent_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO pmagent_app;
```

### 2. Deploy Schema

Apply the table schemas for all stores:

```bash
psql -U pmagent_app -d pmagent_production < database/postgresql/schema.sql
```

Expected tables:
- `conversations` - conversation metadata
- `conversation_messages` - chat messages
- `sessions` - turn execution logs
- `knowledge_resources` - workspace knowledge base
- `knowledge_versions` - knowledge version history
- `episodes` - turn outcome memory
- `feedback` - user preferences and corrections
- `workflows` - workflow definitions
- `profiles` - agent profiles
- `outbox_jobs` - async learning tail jobs

### 3. Deploy RLS Policies

Apply Row-Level Security policies for tenant isolation:

```bash
psql -U postgres -d pmagent_production < database/postgresql/rls_policies.sql
```

**Critical**: RLS policies must be applied by a superuser (`postgres`) before granting table access to `pmagent_app`.

Verify policies are active:

```sql
SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public';
```

---

## Connection Configuration

### Environment Variables

```bash
# PostgreSQL connection string
export POSTGRES_URL="postgresql://pmagent_app:<password>@<host>:5432/pmagent_production?sslmode=require"

# Connection pool settings
export POSTGRES_POOL_MIN_SIZE=5
export POSTGRES_POOL_MAX_SIZE=20
export POSTGRES_POOL_TIMEOUT=30

# Query timeout (prevent long-running queries)
export POSTGRES_STATEMENT_TIMEOUT="30s"
```

### Connection Pooler (PgBouncer)

Use a connection pooler to reduce connection overhead and improve scalability:

```ini
[databases]
pmagent_production = host=localhost port=5432 dbname=pmagent_production

[pgbouncer]
pool_mode = transaction
max_client_conn = 1000
default_pool_size = 25
reserve_pool_size = 5
reserve_pool_timeout = 3
```

**Important**: Session-level variables (`app.tenant_id`, `app.workspace_id`) require `pool_mode = transaction` or `session` to preserve RLS context.

---

## Tenant Isolation (RLS)

### How RLS Works

Every database connection must set session variables before executing queries:

```sql
SET LOCAL app.tenant_id = 'tenant_123';
SET LOCAL app.workspace_id = 'workspace_456';
```

RLS policies automatically append `WHERE tenant_id = current_tenant_id() AND workspace_id = current_workspace_id()` to all queries.

### Application Integration

The application layer must inject tenant context before every transaction:

```python
from artpm_agent.tenancy import TenantContextManager

# Authenticate and set tenant context
with TenantContextManager.activate(tenant_id, workspace_id, principal_id):
    # All database queries within this block are scoped to the tenant/workspace
    store.query(...)
```

The context manager automatically calls:

```python
cursor.execute("SET LOCAL app.tenant_id = %s", (tenant_id,))
cursor.execute("SET LOCAL app.workspace_id = %s", (workspace_id,))
```

### Testing RLS

Run integration tests to verify tenant isolation:

```bash
# Set PostgreSQL test URL
export POSTGRES_TEST_URL="postgresql://pmagent_app:<password>@localhost:5432/pmagent_test"

# Run RLS integration tests
pytest tests/test_postgresql_rls_integration.py -v
```

Expected behavior:
- Different `tenant_id` cannot access each other's data
- Same `tenant_id` but different `workspace_id` cannot access each other's data
- INSERT with mismatched tenant/workspace is rejected

---

## Performance Tuning

### Indexes

Ensure indexes exist for RLS queries:

```sql
-- Episodes
CREATE INDEX idx_episodes_scope ON episodes(tenant_id, workspace_id, created_at);
CREATE INDEX idx_episodes_handler ON episodes(handler);

-- Feedback
CREATE INDEX idx_feedback_workspace ON feedback(tenant_id, workspace_id, principal_id, active);

-- Knowledge Resources
CREATE INDEX idx_knowledge_resources_scope ON knowledge_resources(tenant_id, workspace_id);
CREATE INDEX idx_knowledge_resources_consolidation ON knowledge_resources(workspace_id, consolidation_status);

-- Outbox Jobs
CREATE INDEX idx_outbox_jobs_status_available ON outbox_jobs(status, available_at);
CREATE INDEX idx_outbox_jobs_scope ON outbox_jobs(tenant_id, workspace_id, created_at);
```

### Query Monitoring

Enable `pg_stat_statements` to identify slow queries:

```sql
-- In postgresql.conf
shared_preload_libraries = 'pg_stat_statements'
pg_stat_statements.track = all

-- Create extension
CREATE EXTENSION pg_stat_statements;

-- Query slowest queries
SELECT query, calls, total_exec_time, mean_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 20;
```

### Statement Timeout

Set query timeout to prevent long-running queries:

```sql
ALTER DATABASE pmagent_production SET statement_timeout = '30s';
```

---

## Backup and Recovery

### Logical Backup (pg_dump)

```bash
# Full database backup
pg_dump -U pmagent_app -d pmagent_production -F c -f backup_$(date +%Y%m%d).dump

# Restore
pg_restore -U pmagent_app -d pmagent_production -c backup_20260919.dump
```

### Continuous Backup (WAL Archiving)

Enable Write-Ahead Log (WAL) archiving for point-in-time recovery:

```ini
# In postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'cp %p /backup/wal_archive/%f'
```

### Managed Backups (AWS RDS)

If using AWS RDS, enable automated backups:

- Backup retention: 7-30 days
- Backup window: off-peak hours
- Point-in-time recovery enabled

---

## Monitoring and Alerts

### Key Metrics

- **Connection count**: Monitor `pg_stat_activity` to prevent pool exhaustion
- **Query latency**: Track `pg_stat_statements` for slow queries
- **Disk usage**: Alert on database size growth
- **Replication lag**: If using read replicas, monitor lag time

### Health Check Endpoint

The application exposes a `/ready` endpoint that checks PostgreSQL connectivity:

```bash
curl http://localhost:8000/ready
```

Expected response (200 OK):

```json
{
  "status": "ready",
  "postgres": "ok",
  "rls_policies": "deployed"
}
```

---

## Security Best Practices

1. **Least privilege**: Application user (`pmagent_app`) should not have `CREATE`, `DROP`, or `ALTER` privileges
2. **SSL/TLS required**: Always use `sslmode=require` in production
3. **Secrets management**: Store database passwords in AWS Secrets Manager or HashiCorp Vault
4. **Network isolation**: Database should not be publicly accessible (use VPC/private subnet)
5. **Audit logging**: Enable PostgreSQL audit extension (`pgaudit`) for compliance

---

## Migration from SQLite

For existing deployments migrating from SQLite to PostgreSQL:

1. Export SQLite data:
   ```bash
   python scripts/export_sqlite_to_csv.py
   ```

2. Import into PostgreSQL:
   ```bash
   psql -U pmagent_app -d pmagent_production -c "\COPY episodes FROM 'episodes.csv' CSV HEADER"
   ```

3. Verify data integrity:
   ```bash
   pytest tests/test_migration_integrity.py
   ```

4. Update environment variables to use PostgreSQL URL

5. Restart application and verify `/ready` endpoint

---

## Troubleshooting

### RLS Policies Not Applied

**Symptom**: Users can see data from other tenants

**Solution**:
```sql
-- Verify RLS is enabled
SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';

-- Re-apply policies
\i database/postgresql/rls_policies.sql
```

### Connection Pool Exhausted

**Symptom**: `FATAL: sorry, too many clients already`

**Solution**:
- Increase `max_connections` in `postgresql.conf`
- Use PgBouncer to multiplex connections
- Reduce `POSTGRES_POOL_MAX_SIZE` in application

### Slow Queries

**Symptom**: High query latency

**Solution**:
- Run `EXPLAIN ANALYZE` on slow queries
- Verify indexes exist for RLS columns (`tenant_id`, `workspace_id`)
- Consider partitioning large tables by `tenant_id`

---

## Scalability Considerations

### Read Replicas

For read-heavy workloads, configure PostgreSQL read replicas:

```bash
# Primary connection (writes)
export POSTGRES_PRIMARY_URL="postgresql://pmagent_app:<password>@primary.db:5432/pmagent_production"

# Read replica connection (reads)
export POSTGRES_REPLICA_URL="postgresql://pmagent_app:<password>@replica.db:5432/pmagent_production"
```

Route queries accordingly:
- Writes: `episodes.add()`, `feedback.add()` → Primary
- Reads: `episodes.list()`, `feedback.search()` → Replica

### Table Partitioning

For large tenants, partition tables by `tenant_id`:

```sql
-- Convert episodes to partitioned table
CREATE TABLE episodes_partitioned (LIKE episodes INCLUDING ALL) PARTITION BY LIST (tenant_id);

-- Create partitions per tenant
CREATE TABLE episodes_tenant_a PARTITION OF episodes_partitioned FOR VALUES IN ('tenant_a');
CREATE TABLE episodes_tenant_b PARTITION OF episodes_partitioned FOR VALUES IN ('tenant_b');
```

---

## References

- [PostgreSQL Row-Level Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)
- [PgBouncer Documentation](https://www.pgbouncer.org/usage.html)
- [AWS RDS Best Practices](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_BestPractices.html)
