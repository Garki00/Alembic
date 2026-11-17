# utils/tool_cache.py
"""
Tool output cache.

Supports:
1. cache keys derived from parameters
2. TTL (time to live)
3. LRU eviction
4. cache statistics
"""

import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from utils.performance import get_performance_monitor


@dataclass
class CacheEntry:
    """Cache entry."""

    key: str
    value: Any
    created_at: float
    ttl: int
    hits: int = 0

    def is_expired(self) -> bool:
        """Whether the entry has expired."""
        return time.time() - self.created_at > self.ttl


class ToolCache:
    """Tool output cache."""

    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.monitor = get_performance_monitor()
        self._hits = 0
        self._misses = 0

    def _generate_cache_key(self, tool_name: str, parameters: Dict[str, Any]) -> str:
        """Generate a cache key."""
        # 将参数转为JSON并排序，确保相同参数生成相同的键
        params_str = json.dumps(parameters, sort_keys=True, ensure_ascii=False)
        key_data = f"{tool_name}:{params_str}"
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]

    def get(self, tool_name: str, parameters: Dict[str, Any]) -> Optional[Any]:
        """Get a result from the cache."""
        key = self._generate_cache_key(tool_name, parameters)

        if key in self.cache:
            entry = self.cache[key]

            # 检查是否过期
            if entry.is_expired():
                del self.cache[key]
                self._misses += 1
                self.monitor.update_metrics(cache_misses=1)
                return None

            # 命中，更新访问计数和位置（LRU）
            entry.hits += 1
            self.cache.move_to_end(key)
            self._hits += 1
            self.monitor.update_metrics(cache_hits=1)

            print(f"🎯 缓存命中: {tool_name} (命中 {entry.hits} 次)")
            return entry.value

        # 未命中
        self._misses += 1
        self.monitor.update_metrics(cache_misses=1)
        return None

    def put(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        value: Any,
        ttl: Optional[int] = None,
    ):
        """Add an entry to the cache."""
        key = self._generate_cache_key(tool_name, parameters)
        ttl = ttl if ttl is not None else self.default_ttl

        # 创建缓存条目
        entry = CacheEntry(
            key=key, value=value, created_at=time.time(), ttl=ttl, hits=0
        )

        # 添加到缓存
        self.cache[key] = entry
        self.cache.move_to_end(key)

        # LRU淘汰
        while len(self.cache) > self.max_size:
            # 移除最旧的条目
            oldest_key, oldest_entry = self.cache.popitem(last=False)
            print(f"🗑️  缓存淘汰: {oldest_key} (命中 {oldest_entry.hits} 次)")

    def clear(self):
        """Clear the cache."""
        self.cache.clear()
        print("🧹 缓存已清空")

    def clear_expired(self):
        """Remove expired entries."""
        expired_keys = [key for key, entry in self.cache.items() if entry.is_expired()]
        for key in expired_keys:
            del self.cache[key]
        if expired_keys:
            print(f"🧹 清除了 {len(expired_keys)} 个过期缓存条目")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / max(self._hits + self._misses, 1),
        }

    def print_stats(self):
        """Print cache statistics."""
        stats = self.get_stats()
        print("\n" + "=" * 60)
        print("缓存统计")
        print("=" * 60)
        print(f"  缓存大小: {stats['size']} / {stats['max_size']}")
        print(f"  缓存命中: {stats['hits']} 次")
        print(f"  缓存未命中: {stats['misses']} 次")
        print(f"  命中率: {stats['hit_rate']:.1%}")
        print("=" * 60)


# 全局缓存实例
_global_cache: Optional[ToolCache] = None


def get_tool_cache() -> ToolCache:
    """Get the global tool cache."""
    global _global_cache
    if _global_cache is None:
        from config.settings_optimized import settings

        _global_cache = ToolCache(
            max_size=1000, default_ttl=settings.tool_cache_ttl
        )
    return _global_cache


def cached_tool_call(tool_name: str, ttl: Optional[int] = None):
    """Decorator that caches tool output."""

    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs) -> Any:
            from config.settings_optimized import settings

            # 如果未启用缓存，直接执行
            if not settings.enable_tool_output_cache:
                return func(*args, **kwargs)

            # 尝试从缓存获取
            cache = get_tool_cache()
            parameters = {"args": args, "kwargs": kwargs}
            cached_result = cache.get(tool_name, parameters)

            if cached_result is not None:
                return cached_result

            # 执行工具并缓存结果
            result = func(*args, **kwargs)
            cache.put(tool_name, parameters, result, ttl=ttl)

            return result

        return wrapper

    return decorator


# 使用示例

@cached_tool_call("read_file", ttl=300)
def read_file_cached(file_path: str) -> str:
    """Cached file read."""
    print(f"📖 实际读取文件: {file_path}")
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


@cached_tool_call("expensive_computation", ttl=600)
def expensive_computation(n: int) -> int:
    """Cached expensive computation."""
    print(f"🔢 实际执行计算: n={n}")
    time.sleep(2)  # 模拟耗时操作
    return sum(range(n))


def example_cache_usage():
    """Example: cache usage."""
    from config.settings_optimized import settings

    # 临时启用缓存（如果未启用）
    original = settings.enable_tool_output_cache
    settings.enable_tool_output_cache = True

    try:
        print("=" * 80)
        print("测试工具缓存")
        print("=" * 80)

        # 第一次调用 - 未命中，实际执行
        print("\n[1] 第一次调用...")
        start = time.time()
        result1 = expensive_computation(1000000)
        duration1 = time.time() - start
        print(f"结果: {result1}, 耗时: {duration1:.3f}s")

        # 第二次调用 - 命中缓存
        print("\n[2] 第二次调用（相同参数）...")
        start = time.time()
        result2 = expensive_computation(1000000)
        duration2 = time.time() - start
        print(f"结果: {result2}, 耗时: {duration2:.3f}s")
        print(f"提速: {duration1 / duration2:.1f}×")

        # 第三次调用 - 不同参数，未命中
        print("\n[3] 第三次调用（不同参数）...")
        start = time.time()
        result3 = expensive_computation(2000000)
        duration3 = time.time() - start
        print(f"结果: {result3}, 耗时: {duration3:.3f}s")

        # 打印统计
        cache = get_tool_cache()
        cache.print_stats()

    finally:
        # 恢复设置
        settings.enable_tool_output_cache = original


if __name__ == "__main__":
    example_cache_usage()
