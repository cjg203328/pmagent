"""
日志系统 - 统一的日志管理
"""
import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """带颜色的日志格式化器"""

    COLORS = {
        'DEBUG': '\033[36m',  # Cyan
        'INFO': '\033[32m',   # Green
        'WARNING': '\033[33m',  # Yellow
        'ERROR': '\033[31m',  # Red
        'CRITICAL': '\033[35m',  # Magenta
    }
    RESET = '\033[0m'

    def format(self, record):
        if hasattr(sys.stdout, 'isatty') and sys.stdout.isatty():
            log_color = self.COLORS.get(record.levelname, self.RESET)
            record.levelname = f"{log_color}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logging(
    level: int = logging.INFO,
    log_dir: Optional[str] = None,
    log_file: Optional[str] = None
):
    """
    配置全局日志

    Args:
        level: 日志级别
        log_dir: 日志目录
        log_file: 日志文件名
    """
    # 确定日志目录
    if log_dir is None:
        log_dir = Path(__file__).parent.parent / "logs"
    else:
        log_dir = Path(log_dir)

    log_dir.mkdir(parents=True, exist_ok=True)

    # 确定日志文件
    if log_file is None:
        log_file = f"artpm_{datetime.now().strftime('%Y%m%d')}.log"

    log_path = log_dir / log_file

    # 日志格式
    file_format = logging.Formatter(
        '[%(asctime)s] %(levelname)-8s [%(name)s:%(lineno)d] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    console_format = ColoredFormatter(
        '[%(asctime)s] %(levelname)-8s [%(name)s] %(message)s',
        datefmt='%H:%M:%S'
    )

    # 创建根logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove and close previous handlers before reconfiguration. FileHandler
    # owns an open stream; clearing the list alone leaks that descriptor.
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
        handler.close()

    # 文件Handler
    file_handler = logging.FileHandler(log_path, encoding='utf-8')
    file_handler.setLevel(level)
    file_handler.setFormatter(file_format)
    root_logger.addHandler(file_handler)

    # 控制台Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)

    # 记录启动信息
    root_logger.info(f"日志系统初始化完成 - 日志文件: {log_path}")

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
