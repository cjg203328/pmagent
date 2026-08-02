"""
日志系统 - 统一的日志管理
"""

import logging
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
import os
import re
import sys
from pathlib import Path
from typing import Optional

from .data_masking import SensitiveDataFilter


DEFAULT_LOG_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_LOG_BACKUP_COUNT = 14
_PROCESS_ROLE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class ColoredFormatter(logging.Formatter):
    """带颜色的日志格式化器"""

    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            log_color = self.COLORS.get(record.levelname, self.RESET)
            record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        return super().format(record)


def _positive_int(value: object, default: int, *, minimum: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _log_level(value: int | str | None) -> int:
    if value is None:
        value = os.getenv("ARTPM_LOG_LEVEL", "INFO")
    if isinstance(value, bool):
        return logging.INFO
    if isinstance(value, int):
        return value
    return logging._nameToLevel.get(str(value).strip().upper(), logging.INFO)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _default_log_file() -> str:
    """Return a process-specific default to make Windows rotation reliable."""

    role = os.getenv("ARTPM_PROCESS_ROLE", "").strip().lower()
    if role and _PROCESS_ROLE.fullmatch(role):
        return f"artpm-{role}.log"
    return "artpm.log"


def setup_logging(
    level: int | str | None = None,
    log_dir: Optional[str | Path] = None,
    log_file: Optional[str] = None,
    *,
    rotation: Optional[str] = None,
    max_bytes: Optional[int] = None,
    backup_count: Optional[int] = None,
    when: Optional[str] = None,
    interval: Optional[int] = None,
    utc: Optional[bool] = None,
    console: bool = True,
) -> logging.Logger:
    """
    配置全局日志

    Args:
        level: 日志级别
        log_dir: 日志目录
        log_file: 日志文件名
        rotation: time（按时间）、size（按大小）或 none
        max_bytes: size 模式单文件上限
        backup_count: 保留的历史日志数量
        when: time 模式轮转周期（默认 midnight）
        interval: time 模式轮转间隔
        utc: time 模式是否使用 UTC
        console: 是否同时输出到控制台
    """
    # 确定日志目录
    configured_dir = log_dir or os.getenv("ARTPM_LOG_DIR")
    log_dir = (
        Path(configured_dir).expanduser()
        if configured_dir
        else Path(__file__).parent.parent / "logs"
    )

    log_dir.mkdir(parents=True, exist_ok=True)

    # 确定日志文件
    log_file = log_file or os.getenv("ARTPM_LOG_FILE") or _default_log_file()
    if Path(log_file).name != log_file:
        raise ValueError("log_file must be a file name without directory components")

    log_path = log_dir / log_file

    # 日志格式
    file_format = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s [%(name)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_format = ColoredFormatter(
        "[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s", datefmt="%H:%M:%S"
    )

    # 创建根logger
    root_logger = logging.getLogger()
    resolved_level = _log_level(level)
    root_logger.setLevel(resolved_level)

    # Remove and close previous handlers before reconfiguration. FileHandler
    # owns an open stream; clearing the list alone leaks that descriptor.
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    rotation_mode = (rotation or os.getenv("ARTPM_LOG_ROTATION") or "time").lower()
    rotation_mode = {
        "daily": "time",
        "timed": "time",
        "off": "none",
    }.get(rotation_mode, rotation_mode)
    if rotation_mode not in {"time", "size", "none"}:
        raise ValueError("rotation must be one of: time, size, none")

    backups = _positive_int(
        backup_count
        if backup_count is not None
        else os.getenv("ARTPM_LOG_BACKUP_COUNT"),
        DEFAULT_LOG_BACKUP_COUNT,
    )
    if rotation_mode == "time":
        rotate_when = when or os.getenv("ARTPM_LOG_WHEN") or "midnight"
        rotate_interval = _positive_int(
            interval if interval is not None else os.getenv("ARTPM_LOG_INTERVAL"),
            1,
        )
        use_utc = _env_bool("ARTPM_LOG_UTC", False) if utc is None else bool(utc)
        file_handler = TimedRotatingFileHandler(
            log_path,
            when=rotate_when,
            interval=rotate_interval,
            backupCount=backups,
            encoding="utf-8",
            delay=True,
            utc=use_utc,
        )
    elif rotation_mode == "size":
        size_limit = _positive_int(
            max_bytes if max_bytes is not None else os.getenv("ARTPM_LOG_MAX_BYTES"),
            DEFAULT_LOG_MAX_BYTES,
        )
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=size_limit,
            backupCount=backups,
            encoding="utf-8",
            delay=True,
        )
    else:
        file_handler = logging.FileHandler(log_path, encoding="utf-8", delay=True)

    sensitive_filter = SensitiveDataFilter()
    file_handler.addFilter(sensitive_filter)
    file_handler.setLevel(resolved_level)
    file_handler.setFormatter(file_format)
    file_handler._artpm_managed = True
    root_logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.addFilter(sensitive_filter)
        console_handler.setLevel(resolved_level)
        console_handler.setFormatter(console_format)
        console_handler._artpm_managed = True
        root_logger.addHandler(console_handler)

    # 记录启动信息
    root_logger.info(
        "日志系统初始化完成 - 日志文件: %s, 轮转模式: %s, 保留: %s",
        log_path,
        rotation_mode,
        backups,
    )

    return root_logger


def get_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    """
    获取logger实例

    Args:
        name: Logger名称 (通常使用 __name__)
        level: 日志级别 (可选, 默认继承根logger)

    Returns:
        Logger实例
    """
    logger = logging.getLogger(name)

    if level is not None:
        logger.setLevel(level)

    return logger


# 使用示例
if __name__ == "__main__":
    # 初始化日志系统
    setup_logging(level=logging.DEBUG)

    # 获取logger
    logger = get_logger(__name__)

    # 测试日志
    logger.debug("这是DEBUG消息")
    logger.info("这是INFO消息")
    logger.warning("这是WARNING消息")
    logger.error("这是ERROR消息")
    logger.critical("这是CRITICAL消息")
