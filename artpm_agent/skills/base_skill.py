"""
Base Skill Class - All skills must inherit from this
"""
from abc import ABC, abstractmethod
from functools import partial
from typing import Dict, Any, Tuple, List, Optional
import asyncio
import inspect
import threading
import time

try:
    from artpm_agent.utils.logger import get_logger
    LOGGER_AVAILABLE = True
except ImportError:
    LOGGER_AVAILABLE = False


class BaseSkill(ABC):
    """
    Skill基类 - 所有功能模块必须继承此类
    """

    # Class attributes (must be defined in subclasses)
    skill_name: str = ""           # Skill unique identifier
    description: str = ""          # Functionality description
    version: str = "1.0"
    author: str = ""

    # Input/Output schema for validation and documentation
    input_schema: Dict[str, Any] = {}
    output_schema: Dict[str, Any] = {}

    # Dependencies on other skills
    dependencies: List[str] = []

    # Whether LLM is required
    requires_llm: bool = False

    def __init__(self, context: Optional[Dict[str, Any]] = None):
        """
        Initialize skill

        Args:
            context: Global context with llm_client, memory, config, etc.
        """
        self.context = context or {}
        self.llm = context.get("llm_client") if context else None
        self.memory = context.get("memory") if context else None
        self.config = context.get("config", {}) if context else {}

        # 初始化logger
        if LOGGER_AVAILABLE:
            self.logger = get_logger(f"skills.{self.skill_name}")
        else:
            self.logger = None

    @abstractmethod
    def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute skill core logic

        Args:
            inputs: Input parameters (must conform to input_schema)

        Returns:
            Output result (must conform to output_schema)
        """
        pass

    def validate(self, inputs: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validate input parameters (can be overridden)

        Returns:
            (is_valid, error_message)
        """
        # Basic validation: check required fields
        required = self.input_schema.get("required", [])
        for field in required:
            if field not in inputs:
                return False, f"Missing required field: {field}"
        return True, ""

    def pre_execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Pre-execution processing (can be overridden)

        Args:
            inputs: Input parameters

        Returns:
            Processed inputs
        """
        return inputs

    def post_execute(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Post-execution processing (can be overridden)

        Args:
            result: Execution result

        Returns:
            Processed result
        """
        return result

    def run(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Complete execution flow (not recommended to override)

        Args:
            inputs: Input parameters

        Returns:
            Execution result
        """
        start_time = time.time()

        # 日志: 开始执行
        self._log(f"开始执行 - 输入参数: {list(inputs.keys())}", "INFO")

        # 1. Validation
        valid, error = self.validate(inputs)
        if not valid:
            self._log(f"输入验证失败: {error}", "ERROR")
            return {
                "success": False,
                "error": error,
                "skill": self.skill_name,
                "execution_time": 0
            }

        # 2. Pre-processing
        try:
            inputs = self.pre_execute(inputs)
        except Exception as e:
            self._log(f"预处理失败: {str(e)}", "ERROR")
            return {
                "success": False,
                "error": f"Pre-execution failed: {str(e)}",
                "skill": self.skill_name,
                "execution_time": time.time() - start_time
            }

        # 3. Execute
        try:
            result = self.execute(inputs)
            if inspect.isawaitable(result):
                result = self._wait_for_awaitable(result)
            if not isinstance(result, dict):
                raise TypeError(
                    f"Skill {self.skill_name} must return a dict, got {type(result).__name__}"
                )
            result.setdefault("success", True)
            result["skill"] = self.skill_name
        except Exception as e:
            self._log(f"执行失败: {str(e)}", "ERROR")
            import traceback
            self._log(f"堆栈跟踪:\n{traceback.format_exc()}", "ERROR")
            return {
                "success": False,
                "error": str(e),
                "skill": self.skill_name,
                "execution_time": time.time() - start_time
            }

        # 4. Post-processing
        try:
            result = self.post_execute(result)
        except Exception as e:
            self._log(f"后处理失败: {str(e)}", "ERROR")
            return {
                "success": False,
                "error": f"Post-execution failed: {str(e)}",
                "skill": self.skill_name,
                "execution_time": time.time() - start_time
            }

        # Add execution time
        execution_time = time.time() - start_time
        result["execution_time"] = execution_time

        # 日志: 执行成功
        self._log(f"执行成功 - 用时 {execution_time:.2f}秒", "INFO")

        return result

    @staticmethod
    def _wait_for_awaitable(awaitable):
        """Run an async skill from the synchronous router.

        Streamlit normally has no running loop in the script thread. When a
        caller already owns a loop (for example a notebook), use a short-lived
        worker thread instead of attempting nested ``asyncio.run`` calls.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        result = []
        error = []

        def runner():
            try:
                result.append(asyncio.run(awaitable))
            except BaseException as exc:
                error.append(exc)

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if error:
            raise error[0]
        return result[0]

    def get_info(self) -> Dict[str, Any]:
        """
        Get skill information (used for agent routing)

        Returns:
            Skill metadata
        """
        return {
            "skill_name": self.skill_name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "dependencies": self.dependencies,
            "requires_llm": self.requires_llm,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema
        }

    async def call_llm(self, prompt: str, **kwargs):
        """Call either a synchronous or asynchronous LLM client safely."""
        if self.llm is None:
            raise RuntimeError("LLM client is not configured")

        chat = self.llm.chat
        if inspect.iscoroutinefunction(chat):
            return await chat(prompt, **kwargs)

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, partial(chat, prompt, **kwargs))
        if inspect.isawaitable(result):
            return await result
        return result

    def _log(self, message: str, level: str = "INFO"):
        """
        Log message

        Args:
            message: Log message
            level: Log level (INFO, WARNING, ERROR)
        """
        if self.logger:
            # 使用真实的logger
            level_map = {
                "DEBUG": self.logger.debug,
                "INFO": self.logger.info,
                "WARNING": self.logger.warning,
                "ERROR": self.logger.error,
                "CRITICAL": self.logger.critical
            }
            log_func = level_map.get(level.upper(), self.logger.info)
            log_func(message)
        else:
            # 后备: 使用print
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{timestamp}] [{level}] [{self.skill_name}] {message}")
