# 项目优缺点与优化路线图

分析日期：2026-10-09  
分析范围：仓库结构、架构/运维文档、Python 后端、React 前端、CI 配置和当前可运行的自动化检查。本文是基于本地代码与文档的工程评估，不代表已完成生产环境渗透、压力测试或外部服务验证。

## 结论

项目已有较成熟的离线优先基础：FastAPI 网关、React 主界面、Streamlit 兼容入口、工作流/权限、多租户隔离、SQLite/FAISS 本地存储、PostgreSQL/RLS 部署路径和大量后端测试均已落地。近期收益最高的工作不是继续扩充功能，而是强化 CI 的可拦截性、覆盖前端关键工作流，并明确单机 SQLite 与多实例生产的容量和安全边界。

## 优点

### 架构和安全边界

- 请求编排已有规范边界：`HarnessRuntime`/`run_turn()` 负责 API/UI 请求，`ArtPMAgent` 保持兼容外观，便于后续逐步迁移旧调用。
- 网关要求生产认证上下文，配置接口仅允许管理员访问，密钥不回传明文，配置更新采用白名单和整批校验。会话授权失败时回落到 `controlled`，方向上遵循失败时收紧权限。
- 多租户隔离不是只靠入口校验；文档明确要求隔离贯穿业务查询、写入、缓存和向量检索，并提供 PostgreSQL RLS 的生产路径。
- 插件和模型工具能力有 allowlist/schema/审批边界，外部 MCP 默认关闭；这些降低了把模型输出直接变成副作用的风险。

### 产品与运行能力

- 产品针对美术外包项目管理，报价、成本、任务、进度、交付、文档解析和通知等业务路径明确。
- 离线业务不依赖 LLM API Key；FAISS/SQLite 提供低运维本地路径，远程向量库、Redis、遥测和 Sentry 可按部署配置启用。
- React 主界面、Streamlit 兼容 UI、REST API 和 CLI 覆盖不同使用场景；前端端口 `1501`、Streamlit `8501`、网关 `8765` 已记录。
- 健康/就绪检查、结构化架构与质量文档、Alembic 迁移以及核心模块覆盖率门槛体现了不错的工程基础。

### 测试与构建现状

- 本次在本机执行后端离线筛选回归：`1436 passed, 89 deselected`。被排除项并未由这条命令验证。
- 前端 `pnpm typecheck`、`pnpm test` 与 `pnpm build` 均通过；当前 Vitest 发现 2 个测试，生产构建输出约 303.6 KB JavaScript（gzip 约 93 KB）。
- `git diff --check` 通过；分析开始前工作区干净，本次变更仅涉及项目分析文档、文档索引、前端包管理器声明和 CI 前端门禁。

## 需要优先关注的问题

### P0：CI 安全检查的结果不构成发布门禁

`.github/workflows/ci.yml` 将 mypy、Bandit、Safety 步骤配置为 `continue-on-error: true`。这使扫描失败或静态类型检查失败时工作流仍可继续。项目 README 又说明 mypy 目前属于渐进检查，因而不宜简单一刀切；更稳妥的做法是记录现有基线、限制新增告警，并为安全扫描制定可执行的阻断阈值和豁免流程。

### P1：CI 没有执行 React 的正式质量脚本

分析开始时，`frontend/package.json` 已提供 `typecheck`、`test`、`build`，且有 `pnpm-lock.yaml`，但 `.github/workflows/ci.yml` 没有 Node/pnpm job，因此本地前端验证不等于 pull request 会自动验证前端。本次已补充独立 frontend job，使用 `pnpm install --frozen-lockfile` 并运行已有三个脚本。

### P1：前端业务流测试覆盖薄弱，且有可见控件未完成行为

前端现有测试只有 `frontend/src/lib/utils.test.ts` 中的两个工具契约测试。聊天、切换会话、请求失败、访问模式保存、配置保存、归档/恢复/删除尚缺 store/API 层回归测试和浏览器级关键流程验证。`frontend/src/pages/chat.tsx` 中的附件按钮带有标签和图标，但没有 `onClick`、文件输入或上传状态；在产品上应实现完整流程或明确标记为未启用，避免看似可用但无响应。

### P1：部署模式必须明确，避免把 SQLite 当成共享多节点数据库

SQLite + WAL 是合适的本地/单实例默认：SQLite 官方说明 WAL 允许读写并行，但同一数据库文件仍只有一个 writer。当前连接池支持多个连接，并不能消除写入串行约束。生产 Compose 已走 PostgreSQL/RLS，这是合理方向；应明确禁止多个实例通过共享网络文件直接操作同一 SQLite 文件，并通过 PostgreSQL 并发/RLS 集成测试验证多租户部署。

### P2：多入口与兼容层带来一致性维护成本

README 声明 React 是新版主界面，Streamlit 仍作为现行兼容界面，另有 CLI 和 REST API。多入口便于迁移，但也增加配置、会话和交互重复实现的风险。需要维护主入口/兼容入口矩阵，关键业务规则尽量沉到共享服务层，并明确 Streamlit 的维护或退役条件。

### P2：长耗时模型处理与同步兼容路径的吞吐需要测量

API chat 支持异步 handler，但同步旧 handler 仍通过工作线程兼容；本地运行时和持久化也有进程内资源。FastAPI worker 能提升 CPU 并行处理能力，但不能自动解决进程内状态、后台任务生命周期或共享数据库的并发约束。应先压测端到端延迟、并发等待和错误率，再决定是否队列化任务、迁移 provider 客户端或增加 worker。

### P2：外部集成验证必须明确区分通过和跳过

PostgreSQL RLS、MCP、真实 Provider 等依赖外部服务或凭证，离线环境不能覆盖。CI/报告应逐项显示实际执行、通过、失败和跳过原因，部署发布清单应要求在可用的隔离测试环境验证关键集成，不能将 mock 或 skip 视为生产兼容性证明。

## 分阶段优化路线

### 第一阶段：让 CI 结果可信

1. 增加独立前端 job：固定 Node 主版本、启用 pnpm、执行 frozen-lockfile 安装以及类型检查、Vitest、生产构建。
2. 让 Bandit 和依赖漏洞扫描产生明确、可审计的失败条件；基线豁免要有责任人、原因和期限。
3. 对 mypy 建立基线或 changed-files 增量门禁，先阻止新增错误，再逐步提高覆盖，而不是把历史错误无限期静默放行。
4. 检查 workflow 的 action 版本、缓存键、最小权限和 artifact 上传行为；分开 PR 验证与主分支发布/推送职责。

验收：PR 上前端任一类型错误、测试失败或构建失败均使 CI 失败；安全告警有可追踪基线；集成跳过可见且有明确原因。

### 第二阶段：围绕用户关键路径补测试

1. 为 Zustand store 编写隔离 API 的测试：创建新会话、切换会话时忽略旧响应、发送失败、访问模式更新失败回滚、归档/恢复、删除和配置保存。
2. 为 FastAPI 增补/确认端到端契约：生产认证缺失、租户/workspace 错配、管理员配置权限、敏感值脱敏和审批策略边界。
3. 选取聊天、会话切换、审批/访问模式、配置保存和附件操作建立浏览器级流程；附件若暂不在本版本支持，移除交互暗示或禁用并解释状态。
4. 外部依赖通过可复现测试服务验证，离线套件保持无网络、稳定、快速。

验收：核心用户旅程至少有 API/store 回归；关键 UI 流程能在 CI 浏览器环境复现；所有可点击控件均有实际结果或明确禁用原因。

### 第三阶段：定义容量、恢复和生产数据边界

1. 把支持矩阵写入部署文档：本机/单实例使用本地 SQLite；多实例部署使用 PostgreSQL，不通过 NFS/共享盘复用同一个 SQLite 文件。
2. 为 SQLite 监控写事务时长、`SQLITE_BUSY`/锁等待、WAL 大小和备份完整性；执行实际备份恢复演练。
3. 对 PostgreSQL/RLS 跑非 owner 应用角色测试，覆盖跨租户读写拒绝、迁移和连接池会话上下文清理。
4. 为数据库升级定义备份、迁移前检查、失败恢复和版本兼容流程。

验收：支持矩阵可直接指导部署决策；有可复现的 PostgreSQL RLS 集成报告和 SQLite/生产库恢复演练记录。

### 第四阶段：按观测数据优化吞吐与模块边界

1. 分别压测 API 请求、模型等待、同步兼容 handler、数据库写入和向量检索，报告 p50/p95、错误率和资源占用。
2. 若长请求阻塞或队列积压明显，再引入有状态的任务记录、超时/取消、并发上限和后台 worker；避免把长模型调用无条件放入 FastAPI `BackgroundTasks`。
3. 按业务边界拆分 API 路由与依赖构造，保持权限/租户依赖共享，避免为追求目录整洁做大规模无验收重构。
4. 设定旧 Streamlit/兼容 facade 的维护级别和退役条件，减少入口间行为漂移。

验收：有基准和阈值支撑扩容决策；多 worker/多副本验证包含跨进程状态与数据库测试，而非只看启动成功。

## 推荐执行次序

按“前端 CI -> 安全门禁基线 -> store/API 关键路径测试 -> PostgreSQL RLS 与恢复演练 -> 压测和容量决策”推进。先做前三项即可显著降低合并回归与前端不可用风险；是否迁移数据库或增加 worker 应由目标部署规模和压测数据触发。

## 外部技术依据

- [FastAPI 并发与 async/await](https://fastapi.tiangolo.com/async/)
- [FastAPI Uvicorn workers 部署说明](https://fastapi.tiangolo.com/deployment/server-workers/)
- [SQLite WAL](https://sqlite.org/wal.html)
- [SQLite 适用场景与写并发边界](https://www.sqlite.org/whentouse.html)
- [OWASP Application Security Verification Standard](https://owasp.org/www-project-application-security-verification-standard/)
- [Vite 静态部署](https://vite.dev/guide/static-deploy.html)

外部资料只用于验证一般性部署、安全与数据库判断；具体结论以本仓库的实际配置、测试和目标负载为准。
