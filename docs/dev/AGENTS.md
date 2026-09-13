# ArtPM Agent 开发规范

本文件是 ArtPM Agent 的开发协作规范，文件路径范围为 `docs/dev/` 及其子目录。
如果未来在仓库根目录新增更高层级的 `AGENTS.md`，根目录规范优先；系统、开发者和
用户指令始终优先于本文件。

## 1. 任务开始前

1. 先检查工作区，不覆盖已有改动：

   ```powershell
   git status --short --branch
   git diff --stat
   ```

2. 阅读与任务直接相关的代码、测试和文档。跨模块任务至少核对：
   `README.md`、`docs/operations/CURRENT_STATUS.md`、
   `docs/operations/QUALITY_GATES.md`、`docs/operations/DEPENDENCY_PROFILES.md`，
   以及 `.ai-memory/handoff.md`（存在时）。
3. 明确变更边界、兼容性要求、外部依赖和验证命令；不因发现无关问题而扩大范围。
4. 软件开发任务开始时，按仓库策略调用 Skills Forge 的 skill discovery：传入原始需求、
   相关文件路径，并只加载实际需要的技能。当前环境没有该 MCP 服务时，使用仓库现有约定
   和本地 `dev-expert` skill 继续工作，并在交付说明中注明降级。

## 2. 项目事实与架构边界

- ArtPM Agent 面向游戏美术外包项目管理，业务技能和本地文件能力以离线可用为默认。
- Streamlit 是主 UI，FastAPI 是 REST 网关，CLI 入口为 `python main.py` 或 `artpm-agent`。
- `HarnessRuntime` 与 `run_turn()` 是 API/UI 请求处理的规范边界；`ArtPMAgent` 仍是兼容 facade，
  不要在新功能中继续扩大对旧 facade 私有实现的依赖。
- API chat 使用 async 路由；尚未迁移的同步旧 Agent 通过线程隔离。新代码优先提供 async
  handler，并通过 `RequestServiceBundle` 注入请求级依赖。
- SQLite 与 FAISS 是离线默认实现；PostgreSQL、Qdrant、Redis、遥测和 Sentry 属于可选或
  部署侧集成。共享向量能力遵循 `VectorBackend` 契约，后端不可用时保持既有回退策略。
- 业务库与记忆库分离。涉及租户、workspace、会话、知识或规则审批的改动，必须显式保持
  workspace/tenant 隔离，不得依赖全局单例或未绑定上下文的查询。
- 模型工具调用先经过 JSON Schema 校验；写入型工具必须经过宿主审批，受
  `AGENT_MODEL_TOOL_CALLS_ENABLED` 控制。不得为了测试或调试绕过审批链路。

## 3. 实现原则

- 优先修复根因，沿用现有模块、依赖注入、错误码、日志和回退模式；不为一次性需求新增
  平行抽象或重复配置。
- 保持公共 API、CLI、环境变量、持久化格式和插件契约兼容。需要破坏兼容时，先补迁移、
  兼容适配和文档，并明确回滚方式。
- 数据库 schema 变更必须有 Alembic migration、升级路径和回归测试；禁止通过启动时删表、
  重建表或隐式清空数据解决 schema 不匹配。
- 外部服务调用必须可配置、可限时、可观测且可降级。单元测试不得依赖网络、真实模型、
  本地 MCP server 或生产数据库；集成测试必须显式 opt-in。
- 不关闭 TLS 校验，不把 API key、密码、token 或完整 `.env` 内容写入日志、文档、测试
  输出或提交。只检查配置结构时使用变量名或脱敏值。
- 文档和脚本使用 UTF-8；保持已有命名、目录和格式。除非用户明确要求，不改动归档文档、
  生成物、覆盖率产物或与任务无关的文件。

## 4. 工作区与编辑安全

- 修改前后都要识别工作区已有变更。已有改动视为用户或其他任务的有效内容，不执行
  `git reset --hard`、`git checkout --`、批量清理或覆盖式重写。
- 手工修改使用 `apply_patch`；不要用 shell 重定向、临时脚本或 Python 读写来替代补丁编辑。
- 不主动提交 commit、创建分支或推送远端，除非用户明确要求。
- 保持补丁聚焦：不顺手格式化整个仓库，不升级无关依赖，不修复未被本任务触及的既有缺陷。
- 若发现外部服务不可用，记录可复现的阻塞原因和下一步验证条件，不把 skip、mock 或静态
  检查描述为真实生产验证。

## 5. 质量门禁

默认先运行最小相关验证，再根据影响范围扩大。PowerShell 下使用以下命令：

```powershell
# 快速离线回归：不启动慢速 UI，不访问外部服务
powershell -ExecutionPolicy Bypass -File scripts/test_fast.ps1

# 完整离线回归：包含慢速 UI 测试
powershell -ExecutionPolicy Bypass -File scripts/test_all.ps1

# 显式外部集成：需要 MCP、网络、凭据或数据库时才运行
powershell -ExecutionPolicy Bypass -File scripts/test_integration.ps1

# 离线性能门禁
powershell -ExecutionPolicy Bypass -File scripts/test_benchmark.ps1

# 现代化边界核心覆盖率，门槛为 90%
powershell -ExecutionPolicy Bypass -File scripts/coverage_core.ps1

# 静态检查和语法检查
ruff check artpm_agent tests
python -m compileall -q artpm_agent
```

不使用 PowerShell 时，快速测试和核心覆盖率可直接运行：

```bash
python -m pytest -q --no-cov -m "not integration and not benchmark and not slow"
python scripts/coverage_core.py
```

按变更类型补充验证：

- 配置、启动、部署或依赖 profile：`python -m artpm_agent.tools.check_config`，并运行
  `tests/test_deployment_contract.py`、`tests/test_dependency_profiles.py` 等定向测试。
- API、runtime、事件总线或请求依赖：运行对应单元/契约测试，并覆盖 async 路径、同步兼容
  fallback、稳定错误码和异常边界。
- memory、vector、知识库、规则审批或数据库：覆盖 workspace/tenant 隔离、空数据、回退
  后端和迁移路径；PostgreSQL RLS 没有真实服务时只能保留明确 skip。
- UI、Streamlit 或启动脚本：运行相关测试；需要真实端口时先确认监听者属于本项目，避免
  停止其他项目的服务。
- provider、MCP、MinerU 或其他外部集成：默认使用 fake/fixture；真实验证须显式设置环境
  开关并使用最小请求、有限超时，不重复扩大外部故障影响。
- 仅文档变更：检查 Markdown 结构、链接和命令是否与当前脚本一致；不因文档改动强制运行
  全量慢速或外部集成测试。

`mypy artpm_agent` 是渐进式类型检查，当前可能包含既有基线错误；若未触及相关模块，不要
  把无关基线错误伪装成此次回归。若触及类型边界，应运行定向 mypy 并在交付中区分新增和
  既有错误。

## 6. 文档、记忆与交付

- 新增或调整开发流程时，同步检查 `docs/INDEX.md` 和受影响的开发文档链接。
- 影响架构、质量门禁、依赖 profile、部署或外部验证状态时，更新对应文档；不要仅修改
  Agent 规范而留下相互矛盾的命令或状态描述。
- 完成有实质影响的开发或规范沉淀后，在当天的 `.ai-memory/YYYYMMDD/daily.md` 追加简短
  记录，包含变更文件、关键决策、验证结果和未完成的外部阻塞。不要写入任何秘密。
- 交付说明按“改了什么、验证了什么、哪些未验证及原因”组织；明确列出测试跳过条件、
  已知基线问题和用户需要提供的外部条件。

## 7. 完成检查清单

- [ ] 工作区原有改动未被覆盖，补丁只涉及任务范围。
- [ ] 新代码遵循 runtime、tenant/workspace、审批、回退和依赖注入边界。
- [ ] 相关测试、lint、compile 或文档检查已运行，结果可复现。
- [ ] 外部集成未被误报为离线通过，秘密未进入文件或输出。
- [ ] 受影响的文档和 `.ai-memory` 日志已同步，未提交未经授权的 commit。
