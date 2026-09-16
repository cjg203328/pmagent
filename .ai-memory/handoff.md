## Handoff Checkpoint

**更新时间**: 2026-09-16 23:58
**当前目标**: 修复 API 根入口、收敛工作流运行时边界、完成全量验证并推送当前分支
**当前阶段**: 本地实现与验证完成，无代码待办；提交状态以 Git HEAD 为准
**完成度**: 100%（外部 PostgreSQL RLS 和浏览器本地地址可视化除外）

### 已完成

- `GET /` 返回服务元数据与健康、就绪、文档链接；定向 API 契约测试通过。
- API/UI 共用 `ScopedWorkflowAgent`，API 复用 `WorkflowCoordinator.engine`，UI 按 tenant/workspace/profile 重建协调器。
- UI EpisodeStore 与 ConsolidationScheduler 按会话懒加载缓存，协调器成为内置工作流初始化的唯一入口。
- Streamlit 旧会话路由缺少 `for_tenant()` 的兼容回归已修复，权限卡回归 `24 passed`。
- 快速回归 `1480 passed, 89 deselected`；全量回归 `1548 passed, 22 deselected`。
- 集成回归 `20 passed, 1 skipped`；核心覆盖率 59 项通过，总覆盖率 91%。
- benchmark、优化验证 15/15、Ruff、compileall、配置检查、`uv lock --check` 和 `git diff --check` 通过。
- 新进程已启动：API `127.0.0.1:8765` 与 UI `127.0.0.1:8501`；根入口、`/health`、`/ready`、Streamlit health 均返回 200。

### 未完成

- PostgreSQL RLS 真实连接测试：未配置 `ARTPM_TEST_POSTGRES_URL`，测试明确跳过。
- 内置浏览器可视化：浏览器客户端拦截 localhost，返回 `ERR_BLOCKED_BY_CLIENT`；HTTP 客户端验证已通过。

### 关键决策

- API 始终强制租户绑定；仅 UI 会话内兼容路由允许缺少 `for_tenant()`。
- 工作流引擎只由 `WorkflowCoordinator` 创建，宿主不再重复构造。
- 学习存储按 UI 会话缓存，避免每回合重复 SQLite 初始化。

### 恢复入口

- **首读文件**: `.ai-memory/handoff.md`, `artpm_agent/api/app.py`, `artpm_agent/api/services.py`, `artpm_agent/ui_helpers.py`
- **关键命令**: `powershell -ExecutionPolicy Bypass -File scripts/test_fast.ps1`; `powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1`; `powershell -ExecutionPolicy Bypass -File scripts/test_integration.ps1`
- **验证路径**: `Invoke-WebRequest http://127.0.0.1:8765/`; `Invoke-WebRequest http://127.0.0.1:8765/ready`; `Invoke-WebRequest http://127.0.0.1:8501/_stcore/health`

### 阻塞项

- 无代码阻塞；外部 PostgreSQL 测试需提供一次性 `ARTPM_TEST_POSTGRES_URL`。

## Handoff Checkpoint - 2026-09-17 01:08

**当前目标**: P0-P3 runtime/storage/UI/dependency convergence, full verification and remote push
**当前阶段**: Implementation and verification complete; pending Git commit/push only

### 已完成

- Runtime performance/RSS/import/first-model metrics and per-turn invocation counters.
- Process-owned RuntimeFactory/StorageRegistry across API/UI/CLI; workspace-scoped API locking and tenant-aware coordinator caching.
- Workspace knowledge schema v6 durable vector outbox, projector, rebuild and literal fallback.
- Lazy UI document/editing/MinerU imports and lazy RequestOrchestrator OCR/MinerU/document capabilities.
- AgentFactory, ProviderPort and observable legacy facade boundaries.
- Composable dependency profiles with quality-only `dev`; README/current architecture docs and `uv.lock` updated.
- Fast `1495 passed`; full `1561 passed`; integration `20 passed, 1 skipped`; coverage 91%; optimization 15/15.
- API/UI restarted and healthy at `127.0.0.1:8765` / `127.0.0.1:8501`.

### 已知限制

- PostgreSQL RLS live test remains skipped because `ARTPM_TEST_POSTGRES_URL` is not configured locally.

## Handoff Checkpoint - 2026-09-17 02:06

**当前目标**: Memory lifecycle governance, knowledge/chat decomposition, projector reliability, RLS and concurrency verification
**当前阶段**: Implementation and verification complete; pending Git commit/push and service restart

### 已完成

- Schema v7 outbox with atomic leases, exponential retry, dead letters and lag/queue metrics.
- Executable retention/export/compaction/workspace deletion/tenant offboarding/vector cleanup contract.
- `WorkspaceKnowledgeStore` reduced to a compatibility facade over migration, repository, rule and search services.
- Unbound `MemoryManager.save_document()` retired after canonical live counter remained zero.
- Chat execution, feedback, message rendering, state and welcome controls split behind compatibility functions.
- RLS integration contract expanded; workspace concurrency benchmark added.
- Full suite `1572 passed, 22 skipped`; integration `18 passed, 2 skipped`; benchmark and optimization 15/15 passed.

### 已知限制

- PostgreSQL RLS live assertions require `ARTPM_TEST_POSTGRES_URL` and `ARTPM_TEST_POSTGRES_ADMIN_URL`; both are absent locally.
- Repository-wide unrestricted Ruff still reports historical lint debt outside the changed surface; targeted changed/new modules and the project optimization Ruff gate pass.

### 恢复入口

- **首读文件**: `docs/architecture/MEMORY_LIFECYCLE.md`, `artpm_agent/memory/lifecycle.py`, `artpm_agent/memory/knowledge_projector.py`, `artpm_agent/views/chat_execution.py`
- **关键命令**: `.venv/Scripts/python -m pytest -q`; `.venv/Scripts/python -m benchmarks.workspace_concurrency`; `.venv/Scripts/python scripts/verify_optimization.py`
