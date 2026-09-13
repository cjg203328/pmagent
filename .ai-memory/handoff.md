## Handoff Checkpoint

## 2026-09-13 final offline verification

- Workflow recovery requires an explicit read-only recovery lease. Stale
  side-effect steps fail closed and emit compensation audit events; normal
  retries remain available only with a bounded explicit idempotency contract.
- Strategy memory rendering remains compatible with minimal store records.
  Workflow stores without a conversation `workspaces` table fall back to the
  local tenant during coordinator construction.
- Workflow override reset resolves the effective tenant before deletion, so
  settings reset restores definition defaults in the active scope.
- Verification: `scripts/test_fast.ps1` passed (`1400 passed, 89 deselected`);
  `scripts/test_all.ps1` passed (`1468 passed, 22 deselected`).
- Verification: `ruff check artpm_agent tests`, `python -m compileall -q
  artpm_agent`, and `git diff --check` passed.
- External boundary: PostgreSQL RLS remains skipped without
  `ARTPM_TEST_POSTGRES_URL`; real Qdrant, TencentDB, MCP, and external model
  services were not claimed as verified in this run.

**更新时间**: 2026-09-13
**当前目标**: 按阶段完成项目架构、上下文/记忆、意图、日志和工具编排优化，并收敛文档与验证入口
**当前阶段**: 代码、文档与离线质量门禁完成，外部 PostgreSQL 仍待接入
**完成度**: 100%（外部 RLS 实测除外）

### 已完成

- 本地配置有效: `python -m artpm_agent.tools.check_config` 通过 -> `.env`
- Compose 提供内部 `artpm-api:8765`，Caddy 对外 `/api/*` 转发，Streamlit/API 探针分别为 `/_stcore/health` 与 `/ready`
- `alembic` 已纳入 runtime 依赖；PostgreSQL tenant/workspace RLS migration 资产已打包
- API gateway 对 legacy facade 使用 tenant-scoped runtime adapter；同 workspace 跨 tenant 会被拒绝
- `.env` 已切换到可用智谱 `glm-4-flash`，配置检查与真实聊天通过
- MCP stdio 真实握手通过，返回 4 个 allowlist 工具；MinerU 3.4.4 真实 DOCX 本地转换通过
- EventBus sink token 解绑修复，定向测试通过
- NumPy 依赖已限制 `<2.5.0`，避开 Python 3.10 目标无法解析的 PEP 695 stubs
- TLS 与模型目录有效: curl/requests 证书校验通过，鉴权 `/v1/models` 返回 200 且目标模型存在 -> `.env`
- 本地服务已恢复: UI `/_stcore/health` 返回 200，API `/ready` 返回 `ready=true` -> `start_with_checks.py`
- 本地回归通过: 19 个配置、部署契约和 LangChain 适配器测试通过 -> `tests/test_check_config.py`, `tests/test_deployment_contract.py`, `tests/test_langchain_client.py`
- 请求主链已收敛为 `TurnContext -> run_turn() -> Handler -> TurnEventRecorder`，API/UI 共用请求级服务包，legacy facade 仅保留兼容入口
- 已补齐上下文、记忆检索、意图识别、工具审批、事件总线、Episode 落盘和反思学习的租户、workspace、主体隔离回归
- `all_principals=True` 只在当前认证租户和 workspace 内扩大范围；有认证主体时，反思任务不能写入其他主体偏好
- 当前定向回归通过: `tests/test_meta_memory.py`, `tests/test_reflection.py`, `tests/test_harness_runtime.py`, `tests/test_api_gateway.py` 共 `75 passed`
- 当前文档相对链接检查通过: 现行 `README.md` 与非归档 `docs/` 共检查 116 条相对链接，缺失 0 条；`REAL_MCP_QUICKSTART.md` 存在
- 目录映射、执行映射、开发规范和 README 已同步到 `docs/architecture/`, `docs/dev/AGENTS.md` 与 `README.md`

### 未完成

- PostgreSQL RLS 真实连接: 本机无 Docker/psql，5432 未监听，也未提供 `ARTPM_TEST_POSTGRES_URL`; integration test 会明确 skip
- Mypy 尚有 401 个既有项目类型错误（79 个文件）；依赖 stub 语法阻塞已解除
- legacy `RequestOrchestrator.chat()` 仍由 CLI/Streamlit 兼容入口保留，后续可按宿主迁移到纯 `HarnessRuntime`
- 本轮快速离线回归通过: `1383 passed, 89 deselected`
- 本轮完整离线回归通过: `1450 passed, 22 deselected`
- `ruff check artpm_agent tests` 与 `python -m compileall -q artpm_agent` 均通过

### 关键决策

- 不关闭 TLS 校验: 当前 TLS 已正常，关闭校验会引入不必要的安全风险；若证书问题复现再针对信任链处理
- 不继续增加超时: 客户端上限 120 秒已覆盖合理冷启动窗口；更长等待会恶化本地体验且无法修复上游故障
- 保留当前 provider/model: 模型目录确认该模型存在；在没有可用替代模型证据前不擅自改部署目标
- 不在没有外部数据库服务时声称 RLS 已通过；只保留可重现的 skip 原因与连接指引

### 恢复入口

- **首读文件**: `.ai-memory/handoff.md`, `.env`, `artpm_agent/utils/langchain_client.py`
- **关键命令**: `python -m artpm_agent.tools.check_config`; `powershell -ExecutionPolicy Bypass -File scripts/test_fast.ps1`; `powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1`; `ruff check artpm_agent tests`; `python -m compileall -q artpm_agent`
- **验证路径**: 智谱 `glm-4-flash` 已完成最小聊天；MCP 用 `ART_ENABLE_INTEGRATION=1 pytest tests/test_mcp.py -q`；PostgreSQL 需提供一次性 `ARTPM_TEST_POSTGRES_URL` 后运行 RLS 测试

### 阻塞项

- PostgreSQL 外部服务未提供: 需 Docker/psql 或一次性 `ARTPM_TEST_POSTGRES_URL` 才能完成 RLS 实测
