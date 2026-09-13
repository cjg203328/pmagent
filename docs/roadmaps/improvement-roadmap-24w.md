# ArtPM Agent - 改进优先级与执行路线图

> **当前进度（2026-08-15）**：P0-1 的 ORM 租户查询隔离、P0-2 的默认请求编排权限门禁、
> P1-1 的数据库生命周期治理已落地并通过全量回归（1412 passed, 3 skipped）。本路线图中
> 未勾选项目仍表示尚未完成，不以旧报告中的静态估计替代代码验证。

## 风险评估矩阵 (2×2)

```
高影响 │
       │  ┌─────────────────────────┐
       │  │  P0-1: 多租户泄露       │  P1-1: DB 泄露
       │  │  P0-2: 权限绕过         │  P1-2: 异常掩盖
       │  │  (立即行动)              │  (本周内)
       │  └─────────────────────────┘
       │         ▲  ▲
       │         │  │
       │    高   │  │   中等
       └─────────┼──┼──────────
       低影响    │  │
              可能性高
```

## 执行路线图 (24周)

### Week 1-2: 安全加固 (P0)
- [ ] **多租户隔离审计** (3 days)
  - 检查所有 SELECT 查询是否包含 tenant_id 过滤
  - 创建 `with_tenant_scope()` 装饰器
  - 为 Project, Asset, Task, KnowledgeBase 表添加测试

- [ ] **权限检查下移** (2 days)
  - 在 AgentLoop 而非 API 层执行权限检查
  - 验证直接调用 RequestOrchestrator 时的权限

**交付物**: 
- `/security/tenant_isolation_checker.py` (检查工具)
- `/tests/test_tenant_isolation.py` (150+ 行测试)
- PR with security label

---

### Week 3-4: 资源管理 (P1)
- [ ] **数据库连接生命周期** (2 days)
  - 用 `__enter__`/`__exit__` 替换 atexit
  - 添加连接池监控

- [ ] **异常层级重构** (2 days)
  ```
  ArtPMException
  ├── TenantError
  ├── ToolError
  ├── PermissionError
  └── ValidationError
  ```

**交付物**:
- `/artpm_agent/exceptions.py` (新模块)
- `/artpm_agent/database/pool_manager.py` (改进)

---

### Week 5-6: 查询优化 (P1)
- [ ] **N+1 查询修复** (3 days)
  - 添加 `selectinload()` 到所有关键查询
  - 实现查询超时 (default: 5s)
  - 添加查询日志

- [ ] **数据库规范化** (1 day)
  - 字段命名一致性 (underscore_case)
  - 添加 CHECK 约束

**性能指标目标**:
- 查询 P99 < 1s
- 缓存命中率 > 60%

---

### Week 7-8: 类型安全 (P2)
- [ ] **TypedDict 定义** (2 days)
  ```python
  class ConfigDict(TypedDict):
      llm: LLMConfig
      memory: MemoryConfig
      database: DatabaseConfig
  ```

- [ ] **提升 mypy 严格性** (1 day)
  - `disallow_untyped_defs = true`
  - 修复所有类型错误

**指标**: 0 mypy 错误

---

### Week 9-10: 可观测性 (P2)
- [ ] **结构化日志** (2 days)
  - 集成 structlog
  - 统一日志格式 (JSON)

- [ ] **应用指标** (2 days)
  - 工具执行延迟直方图
  - 缓存命中率计数器
  - 多租户资源使用规表

**关键指标**:
```python
meters = {
    "tool_execution_ms": Histogram,
    "cache_hit_rate": Counter,
    "tenant_requests": Counter,
    "api_response_ms": Histogram,
}
```

---

### Week 11-12: 测试增强 (P2)
- [ ] **集成测试扩展** (2 days)
  - 工作流 e2e 测试
  - 多租户场景测试
  - 并发工具执行测试

- [ ] **性能基准** (1 day)
  - 建立基准 (baseline)
  - 添加性能回归检查

**目标**: 150+ 个测试, 覆盖 > 85%

---

### Week 13-14: 代码去重 (P3)
- [ ] **提取公共工具** (1 day)
  ```
  artpm_agent/utils/datetime.py (_utc_now)
  artpm_agent/utils/converters.py (配置转换)
  artpm_agent/memory/vector_store_base.py (基类)
  ```

- [ ] **命名规范化** (1 day)
  - 统一 ORM 字段命名

---

### Week 15-16: 性能优化 (P2)
- [ ] **缓存预热** (2 days)
  - 启动时加载热点数据
  - 定期更新策略

- [ ] **UI 异步化** (2 days)
  - Streamlit 改用 async 模式
  - 后台任务支持

**性能指标**:
- 启动时间 < 3s
- 响应延迟 P95 < 2s

---

### Week 17-18: 安全加固续 (P2)
- [ ] **路径遍历防护** (1 day)
  ```python
  def safe_read_file(path: str, allowed_dir: str) -> str:
      abs_path = Path(path).resolve()
      abs_allowed = Path(allowed_dir).resolve()
      if not str(abs_path).startswith(str(abs_allowed)):
          raise PermissionError
      return abs_path.read_text()
  ```

- [ ] **敏感数据脱敏** (1 day)
  - API Key, Email, Phone 正则替换
  - 日志存储加密

- [ ] **依赖审计自动化** (0.5 days)
  - CI: `bandit`, `safety`

---

### Week 19-20: 文档完善 (P3)
- [ ] **API 文档生成** (1 day)
  - Sphinx + autodoc
  - 发布到 RTD

- [ ] **架构文档** (1 day)
  - 数据流图
  - 部署指南

---

### Week 21-22: 生产部署准备 (P1)
- [ ] **多租户合规检查** (1 day)
  - GDPR 数据隔离
  - 审计日志

- [ ] **故障转移测试** (1 day)
  - LLM 提供商故障
  - 缓存不可用

- [ ] **压力测试** (1 day)
  - 1000+ 并发用户
  - 大文件上传 (> 100MB)

**目标**: 99.9% 可用性 SLA

---

### Week 23-24: 优化与收官 (P3)
- [ ] **性能优化** (2 days)
  - 向量搜索加速
  - 内存优化

- [ ] **回归测试** (1 day)
  - 完整功能验证
  - 性能基准对比

- [ ] **发布** (1 day)
  - 版本号 0.3.0
  - 变更日志

---

## 关键依赖关系

```
Week 1-2 (安全)
    ↓
Week 3-4 (资源)
    ↓
Week 5-6 (查询) ──→ Week 15-16 (性能优化)
    ↓
Week 7-8 (类型)
    ├─→ Week 9-10 (可观测性)
    └─→ Week 11-12 (测试)
        ↓
Week 17-18 (安全续)
    ↓
Week 21-22 (生产准备)
    ↓
Week 23-24 (收官)
```

---

## 资源分配

| 角色 | 周数 | 任务 |
|-----|-----|------|
| **安全工程师** | 2 + 1 = 3 | P0 多租户, P2 数据脱敏 |
| **后端工程师** | 4 + 4 + 2 = 10 | P1 资源/查询, P2 缓存 |
| **测试工程师** | 2 + 2 = 4 | 集成测试, 性能基准 |
| **DevOps** | 1 | 部署, 监控 |
| **技术文档** | 1 + 1 = 2 | API 文档, 架构文档 |

**总投入**: ~19 人·周 (3-4 人团队 × 6 周)

---

## 质量门禁 (上线前必须)

### 功能验收

- [ ] 所有 P0 问题修复
- [ ] 多租户隔离 100% 验证
- [ ] 权限检查覆盖所有路径
- [ ] 无资源泄露 (连接、内存)

### 性能要求

| 指标 | 目标 | 当前 |
|-----|-----|------|
| API 响应 P99 | < 2s | ? (需基准) |
| 缓存命中率 | > 60% | ? |
| 启动时间 | < 3s | ? |
| 内存占用 (空闲) | < 512MB | ? |

### 安全审计

- [ ] 无 SQL 注入漏洞
- [ ] 无路径遍历漏洞
- [ ] 敏感数据脱敏
- [ ] 依赖安全检查通过
- [ ] 渗透测试合格

### 可观测性

- [ ] 日志覆盖所有异常路径
- [ ] 关键指标可视化
- [ ] 分布式追踪链路完整
- [ ] Sentry 告警配置

### 测试覆盖

- [ ] 单元测试 > 85%
- [ ] 集成测试覆盖关键流程
- [ ] 压力测试通过
- [ ] 恢复性测试通过

---

## 成功指标 (KPI)

| KPI | Week 24 目标 | 测量方式 |
|-----|-------------|---------|
| 代码质量 | 0 Critical issues | SonarQube |
| 安全 | 0 P0/P1 vulnerabilities | Bandit + Safety |
| 性能 | P99 < 2s | APM (Grafana) |
| 可靠性 | 99.9% uptime | New Relic |
| 测试覆盖 | 85%+ | Coverage.py |
| 技术债 | 50% 减少 | Issue tracking |

---

## 风险缓解

| 风险 | 缓解策略 |
|------|---------|
| **范围爬坡** | 严格的优先级排序, 每周 sprint 评审 |
| **类型检查超时** | 增量迁移到 strict mypy (Week 7-8 集中) |
| **性能回退** | 建立基准 + CI 性能检查 (Week 11) |
| **多租户复杂性** | 创建专用测试矩阵 (Week 1-2) |
| **依赖冲突** | LangChain → Anthropic SDK 迁移计划 |

---

## 相关文档

- 主分析报告: `../analysis/deep-analysis-artpm-2026.md`
- API 文档: `docs/operations/API_GATEWAY_DOCUMENTATION.md`
- 生产现代化: `docs/operations/PRODUCTION_MODERNIZATION.md`
- 项目配置: `pyproject.toml`
