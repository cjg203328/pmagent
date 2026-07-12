"""
统一异常处理体系
"""

class ArtPMError(Exception):
    """基础异常类"""
    def __init__(self, message: str, code: str = "UNKNOWN", details: dict = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)

# 业务异常
class SkillExecutionError(ArtPMError):
    """Skill执行失败"""
    def __init__(self, skill_name: str, message: str, **details):
        super().__init__(
            message=f"Skill '{skill_name}' 执行失败: {message}",
            code="SKILL_EXECUTION_ERROR",
            details={"skill_name": skill_name, **details}
        )

class DataValidationError(ArtPMError):
    """数据验证失败"""
    def __init__(self, field: str, message: str):
        super().__init__(
            message=f"字段 '{field}' 验证失败: {message}",
            code="DATA_VALIDATION_ERROR",
            details={"field": field}
        )

class DatabaseError(ArtPMError):
    """数据库操作失败"""
    pass

class ParserError(ArtPMError):
    """文件解析失败"""
    pass

# 集成异常
class LLMError(ArtPMError):
    """LLM调用失败"""
    pass

class MCPError(ArtPMError):
    """MCP工具调用失败"""
    pass

# 异常处理装饰器
from functools import wraps
from utils.logger import get_logger

logger = get_logger(__name__)

def handle_skill_errors(skill_name: str):
    """Skill统一异常处理"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except ArtPMError:
                raise  # 已知业务异常直接抛出
            except Exception as e:
                logger.error(f"Skill {skill_name} 未预期异常: {e}", exc_info=True)
                raise SkillExecutionError(
                    skill_name=skill_name,
                    message=str(e),
                    original_exception=type(e).__name__
                )
        return wrapper
    return decorator

# 使用示例:
# @handle_skill_errors("quote_calculator")
# async def execute(self, inputs):
#     ...
