# utils/performance.py
"""
Performance monitoring and adaptive tuning utilities.

Supports:
1. real-time system resource monitoring
2. automatic concurrency-limit adjustment
3. metric collection
4. optimization suggestion generation
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("⚠️  psutil 未安装，性能监控功能受限。安装: pip install psutil")


@dataclass
class PerformanceMetrics:
    """Performance metric data class."""

    timestamp: datetime = field(default_factory=datetime.now)
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    context_tokens_used: int = 0
    context_tokens_limit: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    parallel_tasks: int = 0
    task_duration: float = 0.0
    errors: int = 0

    @property
    def cache_hit_rate(self) -> float:
        """Cache hit rate."""
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0

    @property
    def context_usage_rate(self) -> float:
        """Context usage rate."""
        return (
            self.context_tokens_used / self.context_tokens_limit
            if self.context_tokens_limit > 0
            else 0.0
        )


class PerformanceMonitor:
    """Performance monitor."""

    def __init__(self, sample_interval: int = 10):
        self.sample_interval = sample_interval
        self.metrics_history: List[PerformanceMetrics] = []
        self.current_metrics = PerformanceMetrics()
        self._last_sample_time = time.time()

    def update_metrics(
        self,
        context_tokens_used: Optional[int] = None,
        context_tokens_limit: Optional[int] = None,
        model_calls: Optional[int] = None,
        tool_calls: Optional[int] = None,
        cache_hits: Optional[int] = None,
        cache_misses: Optional[int] = None,
        parallel_tasks: Optional[int] = None,
        task_duration: Optional[float] = None,
        errors: Optional[int] = None,
    ):
        """Update the performance metrics."""
        if context_tokens_used is not None:
            self.current_metrics.context_tokens_used = context_tokens_used
        if context_tokens_limit is not None:
            self.current_metrics.context_tokens_limit = context_tokens_limit
        if model_calls is not None:
            self.current_metrics.model_calls = model_calls
        if tool_calls is not None:
            self.current_metrics.tool_calls = tool_calls
        if cache_hits is not None:
            self.current_metrics.cache_hits += cache_hits
        if cache_misses is not None:
            self.current_metrics.cache_misses += cache_misses
        if parallel_tasks is not None:
            self.current_metrics.parallel_tasks = parallel_tasks
        if task_duration is not None:
            self.current_metrics.task_duration = task_duration
        if errors is not None:
            self.current_metrics.errors += errors

        # 定期采样系统资源
        current_time = time.time()
        if current_time - self._last_sample_time >= self.sample_interval:
            self._sample_system_resources()
            self._last_sample_time = current_time

    def _sample_system_resources(self):
        """Sample system resources."""
        if not PSUTIL_AVAILABLE:
            return

        self.current_metrics.cpu_percent = psutil.cpu_percent(interval=0.1)
        self.current_metrics.memory_percent = psutil.virtual_memory().percent
        self.current_metrics.timestamp = datetime.now()

        # 保存到历史记录
        self.metrics_history.append(self.current_metrics)

        # 限制历史记录长度（保留最近100条）
        if len(self.metrics_history) > 100:
            self.metrics_history = self.metrics_history[-100:]

    def get_current_metrics(self) -> PerformanceMetrics:
        """Get the current performance metrics."""
        return self.current_metrics

    def get_average_metrics(self, last_n: int = 10) -> PerformanceMetrics:
        """Get averaged performance metrics."""
        if not self.metrics_history:
            return self.current_metrics

        recent = self.metrics_history[-last_n:]
        avg = PerformanceMetrics()
        avg.cpu_percent = sum(m.cpu_percent for m in recent) / len(recent)
        avg.memory_percent = sum(m.memory_percent for m in recent) / len(recent)
        avg.cache_hits = sum(m.cache_hits for m in recent)
        avg.cache_misses = sum(m.cache_misses for m in recent)

        return avg

    def should_reduce_load(self, cpu_threshold: int = 80, memory_threshold: int = 85) -> bool:
        """Whether the load should be reduced."""
        if not PSUTIL_AVAILABLE:
            return False

        avg = self.get_average_metrics(last_n=5)
        return avg.cpu_percent > cpu_threshold or avg.memory_percent > memory_threshold

    def should_increase_load(self, cpu_threshold: int = 50, memory_threshold: int = 60) -> bool:
        """Whether the load can be increased."""
        if not PSUTIL_AVAILABLE:
            return False

        avg = self.get_average_metrics(last_n=5)
        return avg.cpu_percent < cpu_threshold and avg.memory_percent < memory_threshold

    def get_optimization_suggestions(self) -> List[str]:
        """Generate optimization suggestions."""
        suggestions = []
        metrics = self.current_metrics

        # 上下文使用率建议
        if metrics.context_usage_rate > 0.9:
            suggestions.append(
                "⚠️  上下文使用率超过90%，建议：\n"
                "  1. 启用智能压缩: ENABLE_SMART_COMPRESSION=true\n"
                "  2. 提高压缩触发比例: COMPACT_TRIGGER_RATIO=0.85\n"
                "  3. 增加压缩预留: COMPACT_RESERVE_TOKENS=16000"
            )

        # 缓存命中率建议
        if metrics.cache_hit_rate < 0.3 and (metrics.cache_hits + metrics.cache_misses) > 10:
            suggestions.append(
                "ℹ️  缓存命中率较低，建议：\n"
                "  1. 启用工具输出缓存: ENABLE_TOOL_OUTPUT_CACHE=true\n"
                "  2. 增加缓存TTL: TOOL_CACHE_TTL=600"
            )

        # CPU使用率建议
        avg = self.get_average_metrics()
        if avg.cpu_percent > 85:
            suggestions.append(
                "⚠️  CPU使用率过高，建议：\n"
                "  1. 降低并行度: MAX_PARALLEL_TOOL_CALLS=5\n"
                "  2. 减少并发Agent: MAX_CONCURRENT_AGENTS=3\n"
                "  3. 启用自适应限制: ENABLE_ADAPTIVE_LIMITS=true"
            )
        elif avg.cpu_percent < 40:
            suggestions.append(
                "✅ CPU使用率较低，可以：\n"
                "  1. 提高并行度: MAX_PARALLEL_TOOL_CALLS=10\n"
                "  2. 增加并发Agent: MAX_CONCURRENT_AGENTS=5"
            )

        # 内存使用率建议
        if avg.memory_percent > 85:
            suggestions.append(
                "⚠️  内存使用率过高，建议：\n"
                "  1. 降低上下文窗口: MODEL_CONTEXT_WINDOW=131072\n"
                "  2. 减少工具输出预算: MAX_TOOL_OUTPUT_TOKENS=16000\n"
                "  3. 启用增量压缩: ENABLE_INCREMENTAL_COMPRESSION=true"
            )

        # 错误率建议
        error_rate = metrics.errors / max(metrics.model_calls + metrics.tool_calls, 1)
        if error_rate > 0.1:
            suggestions.append(
                "⚠️  错误率较高，建议：\n"
                "  1. 启用自动恢复: ENABLE_AUTO_RECOVERY=true\n"
                "  2. 增加重试次数: MODEL_MAX_RETRIES=3\n"
                "  3. 启用指数退避: ENABLE_EXPONENTIAL_BACKOFF=true"
            )

        return suggestions if suggestions else ["✅ 性能良好，无需优化"]

    def print_report(self):
        """Print the performance report."""
        metrics = self.current_metrics

        print("\n" + "=" * 80)
        print("性能监控报告")
        print("=" * 80)

        # 系统资源
        print("\n【系统资源】")
        print(f"  CPU使用率: {metrics.cpu_percent:.1f}%")
        print(f"  内存使用率: {metrics.memory_percent:.1f}%")

        # 上下文使用
        print("\n【上下文使用】")
        print(
            f"  已用Token: {metrics.context_tokens_used:,} / {metrics.context_tokens_limit:,} "
            f"({metrics.context_usage_rate:.1%})"
        )

        # 调用统计
        print("\n【调用统计】")
        print(f"  模型调用: {metrics.model_calls} 次")
        print(f"  工具调用: {metrics.tool_calls} 次")
        print(f"  并行任务: {metrics.parallel_tasks} 个")

        # 缓存效率
        print("\n【缓存效率】")
        print(f"  缓存命中: {metrics.cache_hits} 次")
        print(f"  缓存未命中: {metrics.cache_misses} 次")
        print(f"  命中率: {metrics.cache_hit_rate:.1%}")

        # 性能指标
        print("\n【性能指标】")
        print(f"  任务耗时: {metrics.task_duration:.2f}s")
        print(f"  错误次数: {metrics.errors}")

        # 优化建议
        print("\n【优化建议】")
        for suggestion in self.get_optimization_suggestions():
            print(f"{suggestion}\n")

        print("=" * 80)


class AdaptiveLimitAdjuster:
    """Adaptive limit adjuster."""

    def __init__(
        self,
        monitor: PerformanceMonitor,
        base_parallel_calls: int = 10,
        base_concurrent_agents: int = 5,
        cpu_threshold: int = 80,
        memory_threshold: int = 85,
    ):
        self.monitor = monitor
        self.base_parallel_calls = base_parallel_calls
        self.base_concurrent_agents = base_concurrent_agents
        self.cpu_threshold = cpu_threshold
        self.memory_threshold = memory_threshold

        self.current_parallel_calls = base_parallel_calls
        self.current_concurrent_agents = base_concurrent_agents

    def adjust(self) -> Dict[str, int]:
        """Adjust the limits automatically."""
        if not PSUTIL_AVAILABLE:
            return {
                "max_parallel_tool_calls": self.current_parallel_calls,
                "max_concurrent_agents": self.current_concurrent_agents,
            }

        # 判断是否需要调整
        if self.monitor.should_reduce_load(self.cpu_threshold, self.memory_threshold):
            # 降低并发
            self.current_parallel_calls = max(1, self.current_parallel_calls - 2)
            self.current_concurrent_agents = max(1, self.current_concurrent_agents - 1)
            print(
                f"🔽 检测到高负载，降低并发：parallel_calls={self.current_parallel_calls}, "
                f"concurrent_agents={self.current_concurrent_agents}"
            )

        elif self.monitor.should_increase_load(50, 60):
            # 提升并发（不超过基准值）
            if self.current_parallel_calls < self.base_parallel_calls:
                self.current_parallel_calls = min(
                    self.base_parallel_calls, self.current_parallel_calls + 1
                )
            if self.current_concurrent_agents < self.base_concurrent_agents:
                self.current_concurrent_agents = min(
                    self.base_concurrent_agents, self.current_concurrent_agents + 1
                )
            print(
                f"🔼 检测到低负载，提升并发：parallel_calls={self.current_parallel_calls}, "
                f"concurrent_agents={self.current_concurrent_agents}"
            )

        return {
            "max_parallel_tool_calls": self.current_parallel_calls,
            "max_concurrent_agents": self.current_concurrent_agents,
        }


# 全局性能监控器实例
_global_monitor: Optional[PerformanceMonitor] = None


def get_performance_monitor() -> PerformanceMonitor:
    """Get the global performance monitor."""
    global _global_monitor
    if _global_monitor is None:
        from config.settings_optimized import settings

        _global_monitor = PerformanceMonitor(sample_interval=settings.performance_sample_interval)
    return _global_monitor


def init_performance_monitoring():
    """Initialize performance monitoring."""
    from config.settings_optimized import settings

    if not settings.enable_performance_monitoring:
        return None

    monitor = get_performance_monitor()
    print("✅ 性能监控已启用")

    if settings.enable_adaptive_limits:
        adjuster = AdaptiveLimitAdjuster(
            monitor=monitor,
            base_parallel_calls=settings.max_parallel_tool_calls,
            base_concurrent_agents=settings.max_concurrent_agents,
            cpu_threshold=settings.cpu_threshold,
            memory_threshold=settings.memory_threshold,
        )
        print("✅ 自适应限制调整已启用")
        return monitor, adjuster

    return monitor, None
