# Alembic 数据库迁移配置指南

## 安装
```bash
pip install alembic
```

## 初始化
```bash
# Run from the repository root
alembic init migrations
```

## 配置 alembic.ini
```ini
sqlalchemy.url = sqlite:///./data/artpm.db
```

## 配置 migrations/env.py
```python
from database.models import Base
target_metadata = Base.metadata
```

## 创建初始迁移
```bash
alembic revision --autogenerate -m "Initial schema"
alembic upgrade head
```

## 后续变更流程
1. 修改 models.py
2. `alembic revision --autogenerate -m "描述"`
3. 检查生成的迁移脚本
4. `alembic upgrade head`

## 回滚
```bash
alembic downgrade -1  # 回滚一个版本
```
