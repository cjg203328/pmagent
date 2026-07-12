"""
缓存系统 - 提升性能,减少重复计算
"""
import pickle
import json
import hashlib
import inspect
import os
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Any, Optional, Callable
from functools import wraps

try:
    from utils.logger import get_logger
    logger = get_logger(__name__)
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class DiskCache:
    """磁盘缓存系统"""

    def __init__(self, cache_dir: str = ".cache", ttl: int = 3600):
        """
        初始化缓存

        Args:
            cache_dir: 缓存目录
            ttl: 缓存过期时间(秒)
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if ttl < 0:
            raise ValueError("ttl cannot be negative")
        self.ttl = ttl
        self._lock = threading.RLock()
        logger.debug(f"DiskCache初始化: {self.cache_dir}, TTL={ttl}秒")

    def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        cache_file = self._get_cache_file(key)

        with self._lock:
            if not cache_file.exists():
                logger.debug(f"缓存未命中: {key}")
                return None

            try:
                with open(cache_file, 'rb') as f:
                    data = pickle.load(f)

                # 检查是否过期
                if datetime.now() > data["expires_at"]:
                    logger.debug(f"缓存已过期: {key}")
                    cache_file.unlink(missing_ok=True)
                    return None

                logger.debug(f"缓存命中: {key}")
                return data["value"]

            except (OSError, KeyError, TypeError, pickle.UnpicklingError, EOFError) as e:
                logger.warning(f"读取缓存失败 {key}: {e}")
                cache_file.unlink(missing_ok=True)
                return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """设置缓存"""
        cache_file = self._get_cache_file(key)

        ttl = self.ttl if ttl is None else ttl
        if ttl < 0:
            raise ValueError("ttl cannot be negative")
        expires_at = datetime.now() + timedelta(seconds=ttl)

        data = {
            "value": value,
            "expires_at": expires_at,
            "created_at": datetime.now()
        }

        temp_file = cache_file.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            with self._lock:
                with open(temp_file, 'wb') as f:
                    pickle.dump(data, f)
                os.replace(temp_file, cache_file)
            logger.debug(f"缓存已保存: {key}, TTL={ttl}秒")
        except (OSError, pickle.PickleError, TypeError) as e:
            temp_file.unlink(missing_ok=True)
            logger.warning(f"保存缓存失败 {key}: {e}")

    def contains(self, key: str) -> bool:
        cache_file = self._get_cache_file(key)
        if not cache_file.exists():
            return False
        self.get(key)
        return cache_file.exists()

    def delete(self, key: str):
        """删除缓存"""
        cache_file = self._get_cache_file(key)
        if cache_file.exists():
            cache_file.unlink()
            logger.debug(f"缓存已删除: {key}")

    def clear(self):
        """清空所有缓存"""
        count = 0
        for cache_file in self.cache_dir.glob("*.pkl"):
            cache_file.unlink()
            count += 1
        logger.info(f"缓存已清空: 删除{count}个文件")

    def _get_cache_file(self, key: str) -> Path:
        """获取缓存文件路径"""
        key_hash = hashlib.md5(key.encode()).hexdigest()
        return self.cache_dir / f"{key_hash}.pkl"

    def cleanup_expired(self):
        """清理过期缓存"""
        count = 0
        for cache_file in self.cache_dir.glob("*.pkl"):
            try:
                with open(cache_file, 'rb') as f:
                    data = pickle.load(f)
                if datetime.now() > data["expires_at"]:
                    cache_file.unlink()
                    count += 1
            except (OSError, KeyError, TypeError, pickle.UnpicklingError, EOFError) as error:
                logger.warning(f"删除损坏的缓存文件 {cache_file.name}: {error}")
                cache_file.unlink(missing_ok=True)
                count += 1

        if count > 0:
            logger.info(f"清理过期缓存: 删除{count}个文件")


class MemoryCache:
    """内存缓存系统(更快但不持久)"""

    def __init__(self, max_size: int = 1000, ttl: int = 3600):
        """
        初始化内存缓存

        Args:
            max_size: 最大缓存条目数
            ttl: 缓存过期时间(秒)
        """
        if max_size <= 0:
            raise ValueError("max_size must be greater than zero")
        if ttl < 0:
            raise ValueError("ttl cannot be negative")
        self.cache = {}
        self.max_size = max_size
        self.ttl = ttl
        self._lock = threading.RLock()
        logger.debug(f"MemoryCache初始化: max_size={max_size}, TTL={ttl}秒")

    def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        with self._lock:
            if key not in self.cache:
                return None

            data = self.cache[key]
            if datetime.now() > data["expires_at"]:
                del self.cache[key]
                return None

            return data["value"]

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """设置缓存"""
        # 如果缓存满了,删除最旧的
        ttl = self.ttl if ttl is None else ttl
        if ttl < 0:
            raise ValueError("ttl cannot be negative")
        expires_at = datetime.now() + timedelta(seconds=ttl)

        with self._lock:
            if key not in self.cache and len(self.cache) >= self.max_size:
                oldest_key = min(self.cache.keys(), key=lambda k: self.cache[k]["created_at"])
                del self.cache[oldest_key]

            self.cache[key] = {
                "value": value,
                "expires_at": expires_at,
                "created_at": datetime.now()
            }

    def contains(self, key: str) -> bool:
        with self._lock:
            if key not in self.cache:
                return False
        self.get(key)
        with self._lock:
            return key in self.cache

    def delete(self, key: str):
        """删除缓存"""
        if key in self.cache:
            del self.cache[key]

    def clear(self):
        """清空所有缓存"""
        count = len(self.cache)
        self.cache.clear()
        logger.info(f"内存缓存已清空: 删除{count}个条目")

    def cleanup_expired(self):
        """清理过期缓存"""
        now = datetime.now()
        expired_keys = [
            key for key, data in self.cache.items()
            if now > data["expires_at"]
        ]

        for key in expired_keys:
            del self.cache[key]

        if expired_keys:
            logger.info(f"清理过期缓存: 删除{len(expired_keys)}个条目")


def cached(cache_type: str = "memory", ttl: int = 3600, key_prefix: str = ""):
    """
    缓存装饰器

    Args:
        cache_type: 缓存类型 ("memory" 或 "disk")
        ttl: 缓存过期时间(秒)
        key_prefix: 缓存键前缀

    Usage:
        @cached(cache_type="memory", ttl=300)
        def expensive_function(param1, param2):
            # 执行耗时操作
            return result
    """
    # 创建缓存实例
    if cache_type == "disk":
        cache = DiskCache(ttl=ttl)
    else:
        cache = MemoryCache(ttl=ttl)

    def decorator(func: Callable) -> Callable:
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                cache_key = _generate_cache_key(func, args, kwargs, key_prefix)
                if cache.contains(cache_key):
                    return cache.get(cache_key)
                result = await func(*args, **kwargs)
                cache.set(cache_key, result, ttl=ttl)
                return result

            return async_wrapper

        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key = _generate_cache_key(func, args, kwargs, key_prefix)

            # 尝试从缓存获取
            if cache.contains(cache_key):
                cached_value = cache.get(cache_key)
                logger.debug(f"使用缓存结果: {func.__name__}")
                return cached_value

            # 执行函数
            logger.debug(f"执行函数(未缓存): {func.__name__}")
            result = func(*args, **kwargs)

            # 保存到缓存
            cache.set(cache_key, result, ttl=ttl)

            return result

        return wrapper

    return decorator


def _generate_cache_key(func: Callable, args: tuple, kwargs: dict, prefix: str = "") -> str:
    """生成缓存键"""
    # 函数名
    func_name = f"{func.__module__}.{func.__name__}"

    # 参数序列化
    args_str = json.dumps(args, sort_keys=True, default=str)
    kwargs_str = json.dumps(kwargs, sort_keys=True, default=str)

    # 组合
    key_parts = [prefix, func_name, args_str, kwargs_str]
    key = "|".join(filter(None, key_parts))

    # Hash
    return hashlib.md5(key.encode()).hexdigest()


# 全局缓存实例
memory_cache = MemoryCache(max_size=1000, ttl=3600)
disk_cache = DiskCache(cache_dir=".cache", ttl=86400)  # 24小时


# 使用示例
if __name__ == "__main__":
    from time import sleep
    from utils.logger import setup_logging

    setup_logging()

    # 示例1: 使用装饰器
    @cached(cache_type="memory", ttl=10)
    def expensive_calculation(n):
        """模拟耗时计算"""
        print(f"执行耗时计算: {n}")
        sleep(2)
        return n * n

    print("第一次调用:")
    result1 = expensive_calculation(10)  # 执行2秒
    print(f"结果: {result1}")

    print("\n第二次调用(缓存):")
    result2 = expensive_calculation(10)  # 立即返回
    print(f"结果: {result2}")

    # 示例2: 直接使用缓存
    cache = MemoryCache(ttl=60)

    cache.set("user:123", {"name": "张三", "role": "PM"})
    print(f"\n缓存的用户: {cache.get('user:123')}")

    # 示例3: 磁盘缓存
    disk = DiskCache(cache_dir=".cache", ttl=300)
    disk.set("config", {"api_url": "https://api.example.com"})
    print(f"\n磁盘缓存的配置: {disk.get('config')}")

    # 清理
    cache.clear()
    disk.clear()
