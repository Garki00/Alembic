# utils/parallel_executor.py
"""
并行工具调用执行器

支持：
1. 并行执行独立的工具调用
2. 批量处理相似的工具调用
3. 依赖管理（DAG）
4. 错误恢复
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from utils.performance import get_performance_monitor


@dataclass
class ToolCall:
    """工具调用"""

    id: str
    tool_name: str
    parameters: Dict[str, Any]
    dependencies: Set[str] = None  # 依赖的其他工具调用ID

    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = set()


@dataclass
class ToolResult:
    """工具调用结果"""

    id: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    duration: float = 0.0


class ParallelExecutor:
    """并行执行器"""

    def __init__(
        self,
        max_workers: int = 10,
        enable_batching: bool = True,
        timeout: int = 300,
    ):
        self.max_workers = max_workers
        self.enable_batching = enable_batching
        self.timeout = timeout
        self.monitor = get_performance_monitor()

    def execute_parallel(
        self,
        tool_calls: List[ToolCall],
        tool_registry: Dict[str, Callable],
    ) -> Dict[str, ToolResult]:
        """并行执行工具调用（无依赖）"""
        if not tool_calls:
            return {}

        # 检查是否有依赖
        has_dependencies = any(call.dependencies for call in tool_calls)
        if has_dependencies:
            return self.execute_dag(tool_calls, tool_registry)

        # 尝试批处理
        if self.enable_batching:
            batched_calls = self._batch_similar_calls(tool_calls)
        else:
            batched_calls = {i: [call] for i, call in enumerate(tool_calls)}

        results = {}
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交所有批次
            future_to_batch = {}
            for batch_id, batch in batched_calls.items():
                future = executor.submit(self._execute_batch, batch, tool_registry)
                future_to_batch[future] = batch

            # 收集结果
            for future in as_completed(future_to_batch, timeout=self.timeout):
                batch = future_to_batch[future]
                try:
                    batch_results = future.result()
                    results.update(batch_results)
                except Exception as e:
                    # 批次失败，标记所有调用为失败
                    for call in batch:
                        results[call.id] = ToolResult(
                            id=call.id, success=False, error=str(e)
                        )

        # 更新性能指标
        duration = time.time() - start_time
        self.monitor.update_metrics(
            tool_calls=len(tool_calls),
            parallel_tasks=len(batched_calls),
            task_duration=duration,
        )

        return results

    def execute_dag(
        self,
        tool_calls: List[ToolCall],
        tool_registry: Dict[str, Callable],
    ) -> Dict[str, ToolResult]:
        """执行DAG（有依赖的工具调用）"""
        # 构建依赖图
        call_map = {call.id: call for call in tool_calls}
        results = {}

        # 拓扑排序
        layers = self._topological_sort(tool_calls)

        # 按层执行（每层内部并行）
        for layer_index, layer in enumerate(layers):
            print(f"执行第 {layer_index + 1}/{len(layers)} 层，{len(layer)} 个任务...")

            # 检查依赖是否都成功
            executable = []
            for call_id in layer:
                call = call_map[call_id]
                deps_satisfied = all(
                    dep_id in results and results[dep_id].success
                    for dep_id in call.dependencies
                )
                if deps_satisfied:
                    executable.append(call)
                else:
                    # 依赖失败，跳过
                    results[call_id] = ToolResult(
                        id=call_id, success=False, error="依赖失败"
                    )

            # 并行执行本层的任务
            if executable:
                layer_results = self.execute_parallel(executable, tool_registry)
                results.update(layer_results)

        return results

    def _batch_similar_calls(self, tool_calls: List[ToolCall]) -> Dict[int, List[ToolCall]]:
        """批处理相似的工具调用"""
        # 按工具名称分组
        groups: Dict[str, List[ToolCall]] = {}
        for call in tool_calls:
            if call.tool_name not in groups:
                groups[call.tool_name] = []
            groups[call.tool_name].append(call)

        # 转换为批次
        batches = {}
        batch_id = 0
        for tool_name, calls in groups.items():
            # 每批最多10个调用
            for i in range(0, len(calls), 10):
                batches[batch_id] = calls[i : i + 10]
                batch_id += 1

        return batches

    def _execute_batch(
        self,
        batch: List[ToolCall],
        tool_registry: Dict[str, Callable],
    ) -> Dict[str, ToolResult]:
        """执行一个批次的工具调用"""
        results = {}

        for call in batch:
            start_time = time.time()
            try:
                # 获取工具函数
                tool_func = tool_registry.get(call.tool_name)
                if not tool_func:
                    raise ValueError(f"工具 {call.tool_name} 未注册")

                # 执行工具
                result = tool_func(**call.parameters)
                duration = time.time() - start_time

                results[call.id] = ToolResult(
                    id=call.id, success=True, result=result, duration=duration
                )

            except Exception as e:
                duration = time.time() - start_time
                results[call.id] = ToolResult(
                    id=call.id, success=False, error=str(e), duration=duration
                )
                self.monitor.update_metrics(errors=1)

        return results

    def _topological_sort(self, tool_calls: List[ToolCall]) -> List[List[str]]:
        """拓扑排序，返回执行层次"""
        # 构建图
        graph = {call.id: call.dependencies for call in tool_calls}
        in_degree = {call.id: len(call.dependencies) for call in tool_calls}

        # 分层
        layers = []
        processed = set()

        while len(processed) < len(tool_calls):
            # 找出当前可执行的节点（入度为0）
            current_layer = [
                call_id
                for call_id, degree in in_degree.items()
                if degree == 0 and call_id not in processed
            ]

            if not current_layer:
                # 存在循环依赖
                raise ValueError("检测到循环依赖")

            layers.append(current_layer)
            processed.update(current_layer)

            # 更新入度
            for call_id in current_layer:
                for other_id, deps in graph.items():
                    if call_id in deps:
                        in_degree[other_id] -= 1

        return layers


class RetryExecutor:
    """带重试的执行器"""

    def __init__(
        self,
        max_retries: int = 3,
        enable_exponential_backoff: bool = True,
        base_delay: int = 1,
        max_delay: int = 30,
    ):
        self.max_retries = max_retries
        self.enable_exponential_backoff = enable_exponential_backoff
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.monitor = get_performance_monitor()

    def execute_with_retry(
        self,
        func: Callable,
        *args,
        **kwargs,
    ) -> Any:
        """执行函数，失败时重试"""
        last_error = None

        for attempt in range(self.max_retries + 1):
            try:
                result = func(*args, **kwargs)
                if attempt > 0:
                    print(f"✅ 第 {attempt + 1} 次尝试成功")
                return result

            except Exception as e:
                last_error = e
                self.monitor.update_metrics(errors=1)

                if attempt < self.max_retries:
                    # 计算延迟
                    if self.enable_exponential_backoff:
                        delay = min(self.base_delay * (2**attempt), self.max_delay)
                    else:
                        delay = self.base_delay

                    print(f"⚠️  第 {attempt + 1} 次尝试失败: {e}")
                    print(f"⏳ {delay}秒后重试...")
                    time.sleep(delay)

        # 所有重试都失败
        print(f"❌ 所有重试都失败: {last_error}")
        raise last_error


# 便捷函数

def parallel_execute(
    tool_calls: List[ToolCall],
    tool_registry: Dict[str, Callable],
    max_workers: Optional[int] = None,
) -> Dict[str, ToolResult]:
    """并行执行工具调用的便捷函数"""
    from config.settings_optimized import settings

    if max_workers is None:
        max_workers = settings.max_parallel_tool_calls

    executor = ParallelExecutor(
        max_workers=max_workers,
        enable_batching=settings.enable_tool_call_batching,
        timeout=settings.command_timeout,
    )

    return executor.execute_parallel(tool_calls, tool_registry)


def execute_with_retry(
    func: Callable,
    *args,
    max_retries: Optional[int] = None,
    **kwargs,
) -> Any:
    """带重试的执行便捷函数"""
    from config.settings_optimized import settings

    if max_retries is None:
        max_retries = settings.model_max_retries

    executor = RetryExecutor(
        max_retries=max_retries,
        enable_exponential_backoff=settings.enable_exponential_backoff,
        base_delay=settings.base_retry_delay,
        max_delay=settings.max_retry_delay,
    )

    return executor.execute_with_retry(func, *args, **kwargs)


# 使用示例

def example_parallel_file_reading():
    """示例：并行读取多个文件"""

    def read_file(file_path: str) -> str:
        """读取文件内容"""
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    # 创建工具调用列表
    file_paths = [
        "config/settings.py",
        "config/settings_optimized.py",
        "utils/performance.py",
        "utils/parallel_executor.py",
    ]

    tool_calls = [
        ToolCall(id=f"read_{i}", tool_name="read_file", parameters={"file_path": path})
        for i, path in enumerate(file_paths)
    ]

    # 注册工具
    tool_registry = {"read_file": read_file}

    # 并行执行
    print(f"并行读取 {len(file_paths)} 个文件...")
    results = parallel_execute(tool_calls, tool_registry)

    # 处理结果
    for call_id, result in results.items():
        if result.success:
            print(f"✅ {call_id}: 读取成功，{len(result.result)} 字符")
        else:
            print(f"❌ {call_id}: 读取失败 - {result.error}")


def example_dag_execution():
    """示例：DAG执行（有依赖的任务）"""

    def task_a() -> str:
        time.sleep(1)
        return "Task A done"

    def task_b() -> str:
        time.sleep(1)
        return "Task B done"

    def task_c(a_result: str, b_result: str) -> str:
        time.sleep(1)
        return f"Task C done (依赖: {a_result}, {b_result})"

    # 创建DAG
    # A 和 B 并行 → C 依赖 A 和 B
    tool_calls = [
        ToolCall(id="a", tool_name="task_a", parameters={}),
        ToolCall(id="b", tool_name="task_b", parameters={}),
        ToolCall(id="c", tool_name="task_c", parameters={}, dependencies={"a", "b"}),
    ]

    # 注册工具（实际使用时需要传递结果）
    tool_registry = {
        "task_a": task_a,
        "task_b": task_b,
        "task_c": lambda: task_c("A", "B"),
    }

    # 执行DAG
    print("执行DAG...")
    executor = ParallelExecutor(max_workers=5)
    results = executor.execute_dag(tool_calls, tool_registry)

    for call_id, result in results.items():
        print(f"{call_id}: {result.result if result.success else result.error}")


if __name__ == "__main__":
    # 测试并行文件读取
    print("=" * 80)
    print("测试并行文件读取")
    print("=" * 80)
    try:
        example_parallel_file_reading()
    except Exception as e:
        print(f"测试失败: {e}")

    print("\n" + "=" * 80)
    print("测试DAG执行")
    print("=" * 80)
    example_dag_execution()
