# ArtPM Agent 优化实施进度计划（A+B+C）

> 状态看板，随推进更新。对应完整策略见 `项目诊断与优化策略.md`。
> 更新时间：2026-07-12

## 已完成（本会话）

- [x] **Git 安全网**：git init + 提交（已忽略 `.env`/`*.db`/`.streamlit`/`logs`），作为后续重构回滚点（commit `9eab448`）。远程待你提供 URL 后再 `git remote add`。
- [x] **删除死代码**（约 3434 行）：`app_old/backup/simple.py`、`core/{chat_agent,rag_system,tools,skills,llm_client}.py`、`core/mcp_client_real.py`，及其测试 `test_real_mcp.py`。保留 `core/mcp_client.py`（被 `mcp_skills.py` 使用）与 `utils/llm_client.py`（生产版）。
- [x] **合并散落测试**：包根 7 个旧 MCP 测试并入根 `tests/`，修正 `sys.path` 指向仓库根；`tests/` 现共 23 个测试文件。
- [x] **pyproject 可安装化**：`package-dir={"": "artpm_agent"}`，拆分 `ocr`/`vector`/`dev` 可选 extras；`requirements.txt` 改为指向 pyproject。已通过 `pip install -e . --no-deps` 验证 `import agent/core/skills/utils` 定位正确。
- [x] **修正诊断误判**：原报告误判 `tests/` 为空；实际根 `tests/` 已有 18 文件 + `conftest`（mock LLM）。测试评分由 2 修正为 6。

## 进行中 / 待办

### 阶段 0 — 工程化地基（P0）
- [x] git 安全网
- [x] pyproject + 可选 extras
- [x] 测试归位与路径修复
- [x] **移除内部 `sys.path.insert`（实际 7 处，非原报告说的 14）**：`app.py`/`health_check.py`/`parsers/excel_parser.py`/`skills/base_skill.py`/`skills/smart_progress_tracker.py`/`skills/smart_task_allocator.py`/`utils/cache.py`。依赖可编辑安装（`pip install -e .`）使 `agent`/`core`/`skills`/`utils` 可导入，无需路径注入。未使用的 `import sys`/`from pathlib import Path` 一并删除以防 ruff F401 红 CI。（任务 T6）
- [x] CI Python 版本对齐 3.13（原 3.10）；安装改为 `pip install -e ".[dev]"`（修复原 `requirements.txt` 指向问题）；`--cov=artpm_agent` 改为 `--cov=.` + pyproject `[tool.coverage]` omit tests（原包名扁平化后失效）。

### 阶段 1 — 测试护栏 + CI 真正跑起来（P0）
- [x] 为依赖本地 MCP server / 网络 / 密钥的测试加守卫：`conftest.py` 默认跳过 nodeid 含 `mcp` 的集成测试（设 `ART_ENABLE_INTEGRATION=1` 运行）。
- [x] 补充开发/质量依赖（pytest-cov / mypy / bandit / safety）并入 pyproject `dev` extra；CI 通过 `pip install -e ".[dev]"` 一次性装齐。
- [x] 在本地核实根 `tests/` 套件：实跑 **332 passed / 11 skipped / 0 failed（exit 0）**，依赖可编辑安装正常导入（覆盖率 74%）。MCP 集成测试按守卫默认跳过。
- [x] 覆盖率门禁：pyproject 设 `--cov=.` + `[tool.coverage]` omit tests；`pytest.ini` 加 `--cov-fail-under=50`（实跑 74% 通过）。
- [x] ruff 红线清理：修 6 个非 WIP 真问题（`data_masking.py` F401+E402、`exceptions.py` E402×2、`metrics.py` F401、`test_mcp.py` F541）；对 3 个 WIP 测试文件（`test_basic.py`/`test_mcp_enhanced.py`/`test_mcp_simple.py`）在 `ruff.toml` 加 per-file-ignore（不修改用户内容）。`ruff check` 现 **All checks passed**。
- [x] Streamlit 冒烟验证：`streamlit run artpm_agent/app.py --server.headless` 启动 HTTP 200，`app.py` 在移除 `sys.path.insert` 后可正常导入运行。

### 阶段 2 — 死代码清理与架构解耦（P1）
- [x] 删除死代码与旧 app 入口
- [ ] 拆分 `app.py`（3528 行 → 按页面模块，每页 ≤400 行）。
- [ ] 统一 MCP 客户端：`core/mcp_client.py`（基础）与 `core/mcp_client_enhanced.py` 职责收敛。
- [ ] 收敛 `app.py` 的 42 处 `except Exception` 为领域异常。

### 阶段 3 — 数据层现代化（P1）
- [ ] 引入 Alembic，从当前 schema 生成基线迁移。
- [ ] 模型逐步迁移到 SQLAlchemy 2.0 风格。
- [ ] `data/` 备份脚本与说明。

### 阶段 4–5 — 能力完善与产品化（P2）
- [ ] OCR / FAISS 真装 extra 并验证降级路径。
- [ ] 报价单"解析 → 人工确认 → 保存为项目"流程。
- [ ] 轻量鉴权 / 多用户隔离。
- [ ] 结构化日志脱敏。
- [ ] Agent 评估集 + 性能/成本监控面板。

## 重要提示
- 工作区存在**你进行中的未提交改动**（较首轮新增：`utils/chat_intent.py`、`config/default_config.json`、`tests/test_regressions.py`；原有：`agent.py`/`app.py`/`config.py`/`utils/llm_client.py`/`.env.example`/`tests/test_model_sync.py`/`tests/test_streamlit_app.py` 及我未提交的测试路径修正 `tests/test_basic.py`/`test_mcp_enhanced.py`/`test_mcp_simple.py`/`quick_verify.py`）。本轮改动（sys.path 清理、conftest、pytest.ini、pyproject、ci.yml）均未触碰这些文件的业务逻辑；后续重构涉及这些文件时我会先与你确认。
- Skills Forge MCP 未连接，开发任务按 `AGENTS.md` 回退到仓库约定。
