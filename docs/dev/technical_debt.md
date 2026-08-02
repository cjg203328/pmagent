# 技术债务追踪

> 从 `artpm_agent/bugfixes/__init__.py` 迁移而来 — 原为 Python 模块内的文档，2026-07-22 转为纯 Markdown。

## 已识别的 Bug 与修复

### Critical
| ID | 描述 | 状态 | PR |
|----|------|------|-----|
| C001 | Database connection leak | ✅ fixed | connection_pool.py |
| C002 | Memory growth in long sessions | ✅ fixed | lazy_registry.py |

### High
| ID | 描述 | 状态 | PR |
|----|------|------|-----|
| H001 | Slow startup time | ✅ fixed | lazy_registry.py |
| H002 | Vector search performance | ✅ fixed | adaptive_vector_store.py |
| H003 | Intent routing accuracy | ✅ fixed | hybrid_intent_scorer.py |

### Medium
| ID | 描述 | 状态 | PR |
|----|------|------|-----|
| M001 | Error messages not user-friendly | ✅ fixed | error_messages.py |
| M002 | No progress feedback | ✅ fixed | streaming_progress.py |
| M003 | Model failover too aggressive | ✅ fixed | model_health_tracker.py |

### Low
| ID | 描述 | 状态 |
|----|------|------|
| L001 | TODO markers in code | 📝 documented |
| L002 | Missing type hints | 🔧 in_progress |

## TODO Markers（生产代码中）
- `artpm_agent/core/mcp_skills.py:74` — "实现更智能的解析"
- `artpm_agent/memory/cross_session_memory.py`
- `artpm_agent/memory/memory_injector.py`
- `artpm_agent/skills/base_skill.py`
- `artpm_agent/utils/logger.py`

## 已知局限
1. **连接池**: SQLite WAL 模式依赖文件系统；某些网络文件系统可能不兼容
2. **自适应向量存储**: IVF 索引需要至少 nlist 个向量用于训练，数据不足时降级为 Flat
3. **延迟技能加载**: 有依赖的技能必须按序加载
4. **流式进度**: 需要 UI 端支持回调
5. **混合意图评分**: 三种信号（关键词/embedding/LLM）联合最优

## 未来改进方向
1. 全代码 async/await
2. Agent 类分解
3. 全面 Pydantic 数据模型
4. GraphQL API 层
5. WebSocket 实时更新
6. 多租户
7. 高级缓存策略
8. 分布式追踪
9. 自动扩缩
10. 边缘部署
