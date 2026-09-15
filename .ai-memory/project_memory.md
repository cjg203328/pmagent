# ArtPM Agent Project Memory

## Glossary

暂无项目术语漂移记录。

## Lessons Learned

### Known Issue: api.anthropic.li 模型目录可用但推理端无响应

**发现日期**: 2026-09-11
**问题类型**: 第三方依赖风险
**严重度**: 严重

#### 现象

TLS 校验正常，鉴权 `/v1/models` 返回 200 且包含 `qwen3.8-27b-uncensored`，但 `/chat/completions` 在 45 秒和 120 秒超时窗口内均未返回响应头。

#### 根因

已排除本机 TLS 信任链、模型标识缺失和基础鉴权问题。故障边界位于外部网关的推理路径；其内部排队、模型实例或账号侧状态无法从本地进一步确认。

#### 规避方案

先用模型目录确认基础连通性，再用单次、有限时的最小流式请求验证推理。达到 120 秒上限后停止重试，不关闭 TLS 校验，也不继续扩大客户端超时；等待上游恢复或切换到已确认可推理的兼容网关。

#### 相关文件

- `.env`
- `artpm_agent/utils/langchain_client.py`
- `artpm_agent/utils/llm_client.py`

#### 状态

未修复，外部阻塞。

## Decision Record: 生命周期事件与遗留外键迁移

**日期**: 2026-09-16
**问题**: 生产入口同时启用实时 EventBus 和 SessionStore 时，回合生命周期事件不能丢失；遗留表外键类型必须与整数主键一致。

### 决策

**选择**: EventBus 发布与 SessionStore 追加分开执行、分开容错；通过 Alembic `d4e5f6a7b8c9` 在升级时校验并转换四个遗留外键列。
**理由**: 实时观测故障不能阻断 durable audit；显式迁移才能修复已在旧 head 的数据库，且非整数历史值必须人工清理而不能静默截断。
**撤销条件**: 若所有宿主统一接入带会话作用域的 durable EventBus sink，或遗留表被正式下线并完成数据归档，可重新评估实现。

## Known Issue: 外部发布服务未在本机验证

**发现日期**: 2026-09-16
**问题类型**: 环境边界
**严重度**: 提示

### 现象

本机无 Docker 命令，PostgreSQL RLS 测试因未配置 `ARTPM_TEST_POSTGRES_URL` 跳过。

### 规避方案

CI 中运行 `docker-smoke` 和 `postgres-rls`；本地继续使用临时 SQLite 迁移验证和明确的集成 skip。

### 状态

待外部环境验证。
