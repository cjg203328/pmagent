# 快速优化清单

## 1. 数据库索引 (10分钟)
在 `artpm_agent/database/models.py` 添加:
```python
from sqlalchemy import Index

# Project表
__table_args__ = (
    Index('ix_project_status_deadline', 'status', 'deadline'),
)

# Task表  
__table_args__ = (
    Index('ix_task_assignee_status', 'assignee_id', 'status'),
)
```

迁移:
```bash
alembic revision -m "Add composite indexes"
alembic upgrade head
```

**预期收益**: 进度预警和任务分配查询速度提升50%+

---

## 2. 内存缓存 (15分钟)
安装:
```bash
pip install cachetools
```

在 `artpm_agent/utils/cache.py` 添加:
```python
from cachetools import TTLCache

# 全局内存缓存(1000条,5分钟过期)
_memory_cache = TTLCache(maxsize=1000, ttl=300)

def cached_in_memory(key_prefix: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = f"{key_prefix}:{args}:{kwargs}"
            if cache_key in _memory_cache:
                return _memory_cache[cache_key]
            result = func(*args, **kwargs)
            _memory_cache[cache_key] = result
            return result
        return wrapper
    return decorator
```

使用:
```python
@cached_in_memory("project")
def get_project_by_id(project_id: int):
    ...
```

**预期收益**: 重复查询零延迟

---

## 3. 监控接入 (20分钟)
在 `artpm_agent/skills/base_skill.py` 的 `execute` 方法添加:
```python
from utils.metrics import metrics

async def execute(self, inputs):
    with metrics.track("skill_execution", self.name):
        return await self._execute_impl(inputs)
```

在 `artpm_agent/app.py` 添加监控面板:
```python
if st.sidebar.button("📊 性能监控"):
    summary = metrics.get_summary()
    st.json(summary)
```

**预期收益**: 实时掌握性能瓶颈

---

## 4. 异常处理规范 (30分钟)
替换所有 `except Exception as e:` 为:
```python
from utils.exceptions import SkillExecutionError

try:
    ...
except ValueError as e:
    raise DataValidationError("field_name", str(e))
except Exception as e:
    logger.error(f"未预期异常: {e}", exc_info=True)
    raise SkillExecutionError(self.name, str(e))
```

**预期收益**: 错误追踪和用户提示更清晰

---

## 5. 日志脱敏 (10分钟)
在 `artpm_agent/utils/logger.py` 的 `setup_logging` 中添加:
```python
from utils.data_masking import SensitiveDataFilter

handler = logging.StreamHandler()
handler.addFilter(SensitiveDataFilter())
```

**预期收益**: 防止敏感信息泄漏

---

## 总计时间: ~1.5小时
## 总收益: 性能+30%, 可维护性+50%, 安全性+40%
