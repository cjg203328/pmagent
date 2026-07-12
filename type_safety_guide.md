# 类型检查改进指南

## 1. 添加 mypy 静态类型检查

### 安装
```bash
pip install mypy
```

### 配置 mypy.ini
```ini
[mypy]
python_version = 3.10
warn_return_any = True
warn_unused_configs = True
disallow_untyped_defs = True
disallow_incomplete_defs = True

[mypy-streamlit.*]
ignore_missing_imports = True

[mypy-plotly.*]
ignore_missing_imports = True
```

### 运行检查
```bash
mypy artpm_agent --ignore-missing-imports
```

## 2. 改进现有类型注解

### Before:
```python
def calculate_profit(amount, cost):
    return amount - cost
```

### After:
```python
from typing import Optional
from decimal import Decimal

def calculate_profit(
    amount: Decimal,
    cost: Decimal,
    tax_rate: Optional[float] = None
) -> Decimal:
    """计算净利润"""
    net = amount - cost
    if tax_rate:
        net *= (1 - tax_rate)
    return net
```

## 3. 使用 Pydantic 进行运行时验证

```python
from pydantic import BaseModel, validator, Field

class QuoteRequest(BaseModel):
    """报价请求模型"""
    project_name: str = Field(..., min_length=1, max_length=200)
    total_amount: float = Field(..., gt=0)
    cost: float = Field(..., ge=0)
    
    @validator('cost')
    def cost_must_be_less_than_amount(cls, v, values):
        if 'total_amount' in values and v >= values['total_amount']:
            raise ValueError('成本不能大于等于总金额')
        return v
```

## 4. 建议的优先顺序
1. 核心业务逻辑 (skills/, database/)
2. 数据模型 (parsers/, models.py)
3. 工具函数 (utils/)
4. UI层 (app.py) - 可选
