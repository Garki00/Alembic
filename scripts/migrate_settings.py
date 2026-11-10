#!/usr/bin/env python3
"""
Settings 迁移工具

帮助从旧的 settings.py 迁移到优化后的 settings_optimized.py。
生成推荐的 .env 配置文件。
"""

import os
import sys
from pathlib import Path
from typing import Literal

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))


def detect_environment() -> Literal["development", "production", "limited"]:
    """检测运行环境，返回推荐的配置方案。"""
    import psutil

    # 获取系统资源
    cpu_count = psutil.cpu_count()
    memory_gb = psutil.virtual_memory().total / (1024**3)

    print(f"检测到系统配置：")
    print(f"  CPU核心数: {cpu_count}")
    print(f"  内存: {memory_gb:.1f} GB")

    # 根据资源判断环境
    if cpu_count >= 8 and memory_gb >= 16:
        print(f"\n推荐使用：高性能配置（development）")
        return "development"
    elif cpu_count >= 4 and memory_gb >= 8:
        print(f"\n推荐使用：平衡配置（production）")
        return "production"
    else:
        print(f"\n推荐使用：保守配置（limited）")
        return "limited"


def generate_env_file(
    mode: Literal["development", "production", "limited"],
    output_path: Path,
) -> None:
    """生成 .env 配置文件。"""

    if mode == "development":
        config = """# ===== 高性能配置（开发环境） =====
# 自动生成于 Settings 优化迁移工具

# 执行模式：激进
EXECUTION_MODE=aggressive

# 扩大上下文窗口
MODEL_CONTEXT_WINDOW=200000
COMPACT_TRIGGER_RATIO=0.85
COMPACT_RESERVE_TOKENS=16000
MAX_CONTEXT_MESSAGES=100
KEEP_RECENT_MESSAGES=16
MAX_TOOL_OUTPUT_TOKENS=32000

# 提升任务能力
MAX_MODEL_CALLS=40
MAX_TOOL_CALLS=100
MODEL_MAX_RETRIES=3

# 启用并行处理
ENABLE_PARALLEL_TOOL_CALLS=true
MAX_PARALLEL_TOOL_CALLS=10
ENABLE_TOOL_CALL_BATCHING=true

# 扩展超时
COMMAND_TIMEOUT=300
LONG_RUNNING_TIMEOUT=1800
MAX_TOOL_OUTPUT_CHARS=100000

# 启用智能优化
ENABLE_TOOL_OUTPUT_CACHE=true
TOOL_CACHE_TTL=300
ENABLE_SMART_COMPRESSION=true
PRIORITY_TOOLS=read_file,search_files,run_command,list_directory
ENABLE_INCREMENTAL_COMPRESSION=true

# 任务分解与多Agent
ENABLE_TASK_DECOMPOSITION=true
TASK_COMPLEXITY_THRESHOLD=5
MAX_SUBTASKS=20
ENABLE_TASK_DAG=true

# 多Agent协作
ENABLE_MULTI_AGENT=true
MAX_CONCURRENT_AGENTS=5
AGENT_ROLES=planner,coder,reviewer,tester,documenter
ENABLE_AGENT_COMMUNICATION=true

# 错误恢复
ENABLE_AUTO_RECOVERY=true
MAX_RECOVERY_ATTEMPTS=3
ENABLE_EXPONENTIAL_BACKOFF=true
BASE_RETRY_DELAY=1
MAX_RETRY_DELAY=30

# 审批优化
APPROVAL_ENABLED=true
APPROVAL_TIMEOUT=600
QUESTION_TIMEOUT=600
ENABLE_BATCH_APPROVAL=true
TRUSTED_OPERATIONS=read_file,list_directory,search_files

# 性能监控
ENABLE_PERFORMANCE_MONITORING=true
ENABLE_ADAPTIVE_LIMITS=true
PERFORMANCE_SAMPLE_INTERVAL=10
CPU_THRESHOLD=80
MEMORY_THRESHOLD=85

# 日志
LOG_LEVEL=INFO
ENABLE_VERBOSE_LOGGING=false
ENABLE_PROFILING=false
LOG_ROTATION_SIZE=100
LOG_BACKUP_COUNT=5
"""

    elif mode == "production":
        config = """# ===== 平衡配置（生产环境） =====
# 自动生成于 Settings 优化迁移工具

# 执行模式：自适应
EXECUTION_MODE=adaptive

# 适度扩大上下文
MODEL_CONTEXT_WINDOW=150000
COMPACT_TRIGGER_RATIO=0.82
COMPACT_RESERVE_TOKENS=12000
MAX_CONTEXT_MESSAGES=60
KEEP_RECENT_MESSAGES=12
MAX_TOOL_OUTPUT_TOKENS=24000

# 适度提升能力
MAX_MODEL_CALLS=20
MAX_TOOL_CALLS=50
MODEL_MAX_RETRIES=3

# 启用核心优化
ENABLE_PARALLEL_TOOL_CALLS=true
MAX_PARALLEL_TOOL_CALLS=5
ENABLE_TOOL_CALL_BATCHING=true

# 适度超时
COMMAND_TIMEOUT=180
LONG_RUNNING_TIMEOUT=900
MAX_TOOL_OUTPUT_CHARS=50000

# 启用智能优化
ENABLE_TOOL_OUTPUT_CACHE=true
TOOL_CACHE_TTL=300
ENABLE_SMART_COMPRESSION=true
ENABLE_INCREMENTAL_COMPRESSION=true

# 基础任务分解
ENABLE_TASK_DECOMPOSITION=true
TASK_COMPLEXITY_THRESHOLD=7
MAX_SUBTASKS=15
ENABLE_TASK_DAG=true

# 基础多Agent
ENABLE_MULTI_AGENT=true
MAX_CONCURRENT_AGENTS=3
AGENT_ROLES=planner,coder,reviewer

# 错误恢复
ENABLE_AUTO_RECOVERY=true
MAX_RECOVERY_ATTEMPTS=2
ENABLE_EXPONENTIAL_BACKOFF=true

# 审批
APPROVAL_ENABLED=true
APPROVAL_TIMEOUT=600
ENABLE_BATCH_APPROVAL=true
TRUSTED_OPERATIONS=read_file,list_directory,search_files

# 性能监控
ENABLE_PERFORMANCE_MONITORING=true
ENABLE_ADAPTIVE_LIMITS=true
CPU_THRESHOLD=75
MEMORY_THRESHOLD=80

# 日志
LOG_LEVEL=INFO
LOG_ROTATION_SIZE=100
LOG_BACKUP_COUNT=5
"""

    else:  # limited
        config = """# ===== 保守配置（资源受限环境） =====
# 自动生成于 Settings 优化迁移工具

# 执行模式：保守
EXECUTION_MODE=conservative

# 保持较小的上下文
MODEL_CONTEXT_WINDOW=131072
COMPACT_TRIGGER_RATIO=0.75
COMPACT_RESERVE_TOKENS=8000
MAX_CONTEXT_MESSAGES=40
KEEP_RECENT_MESSAGES=8
MAX_TOOL_OUTPUT_TOKENS=16000

# 基础能力
MAX_MODEL_CALLS=8
MAX_TOOL_CALLS=15
MODEL_MAX_RETRIES=2

# 选择性启用优化
ENABLE_PARALLEL_TOOL_CALLS=false
ENABLE_TOOL_CALL_BATCHING=true

# 基础超时
COMMAND_TIMEOUT=120
MAX_TOOL_OUTPUT_CHARS=30000

# 启用缓存和压缩
ENABLE_TOOL_OUTPUT_CACHE=true
TOOL_CACHE_TTL=180
ENABLE_SMART_COMPRESSION=true

# 禁用高级功能
ENABLE_TASK_DECOMPOSITION=false
ENABLE_MULTI_AGENT=false

# 错误恢复
ENABLE_AUTO_RECOVERY=true
MAX_RECOVERY_ATTEMPTS=2

# 审批
APPROVAL_ENABLED=true
APPROVAL_TIMEOUT=300
TRUSTED_OPERATIONS=read_file,list_directory

# 性能监控
ENABLE_PERFORMANCE_MONITORING=false
ENABLE_ADAPTIVE_LIMITS=false

# 日志
LOG_LEVEL=WARNING
LOG_ROTATION_SIZE=50
LOG_BACKUP_COUNT=3
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(config)

    print(f"\n✅ 配置文件已生成: {output_path}")


def backup_existing_env(env_path: Path) -> None:
    """备份现有的 .env 文件。"""
    if env_path.exists():
        backup_path = env_path.with_suffix(".env.backup")
        counter = 1
        while backup_path.exists():
            backup_path = env_path.with_name(f".env.backup.{counter}")
            counter += 1

        import shutil

        shutil.copy2(env_path, backup_path)
        print(f"✅ 已备份现有配置到: {backup_path}")


def compare_settings() -> None:
    """对比新旧配置的差异。"""
    print("\n" + "=" * 80)
    print("配置对比")
    print("=" * 80)

    comparisons = [
        ("上下文窗口", "131,072", "200,000", "+53%"),
        ("压缩触发比例", "0.80", "0.85", "+6%"),
        ("消息历史上限", "40", "100", "+150%"),
        ("模型调用上限", "8", "20-40", "+150%-400%"),
        ("工具调用上限", "15", "50-100", "+233%-567%"),
        ("工具输出字符", "20K", "100K", "+400%"),
        ("命令超时", "60s", "300s", "+400%"),
        ("并行工具调用", "❌ 不支持", "✅ 支持（最多10个）", "新增"),
        ("工具输出缓存", "❌ 不支持", "✅ 支持", "新增"),
        ("任务自动分解", "❌ 不支持", "✅ 支持", "新增"),
        ("多Agent协作", "❌ 不支持", "✅ 支持（最多5个）", "新增"),
        ("自动错误恢复", "❌ 不支持", "✅ 支持", "新增"),
        ("性能自适应", "❌ 不支持", "✅ 支持", "新增"),
    ]

    print(f"\n{'功能':<20} {'原始配置':<20} {'优化配置':<25} {'提升':<15}")
    print("-" * 80)
    for feature, old, new, improvement in comparisons:
        print(f"{feature:<20} {old:<20} {new:<25} {improvement:<15}")


def main():
    print("=" * 80)
    print("Settings 优化迁移工具")
    print("=" * 80)

    # 检测环境
    print("\n[1/4] 检测系统环境...")
    try:
        environment = detect_environment()
    except Exception as e:
        print(f"⚠️  检测失败: {e}")
        print("将使用默认的平衡配置")
        environment = "production"

    # 用户确认
    print(f"\n[2/4] 确认配置方案...")
    print(f"\n推荐方案: {environment}")
    print(f"  development: 高性能，适合开发环境")
    print(f"  production: 平衡性能与资源消耗")
    print(f"  limited: 最小资源消耗")

    choice = (
        input(f"\n使用推荐方案吗？[Y/n] （或输入 dev/prod/limited 选择其他方案）: ")
        .strip()
        .lower()
    )

    if choice in {"dev", "development"}:
        environment = "development"
    elif choice in {"prod", "production"}:
        environment = "production"
    elif choice in {"limited", "conservative"}:
        environment = "limited"
    elif choice and choice != "y":
        print("保持推荐方案")

    # 对比配置
    print("\n[3/4] 配置对比...")
    compare_settings()

    # 生成配置文件
    print(f"\n[4/4] 生成配置文件...")

    project_root = Path(__file__).parent.parent
    env_path = project_root / ".env"

    # 备份现有配置
    if env_path.exists():
        backup_existing_env(env_path)

    # 生成新配置
    generate_env_file(environment, env_path)

    print("\n" + "=" * 80)
    print("迁移完成！")
    print("=" * 80)
    print(f"\n下一步：")
    print(f"1. 检查生成的 .env 文件: {env_path}")
    print(f"2. 根据需要调整配置参数")
    print(f"3. 将 config/settings.py 替换为 config/settings_optimized.py")
    print(f"   或者在代码中导入: from config.settings_optimized import settings")
    print(f"4. 重启应用以应用新配置")
    print(f"\n详细文档: config/OPTIMIZATION_GUIDE.md")

    # 显示关键提示
    print(f"\n⚠️  重要提示：")
    print(f"  - 新配置会增加资源消耗（CPU、内存、API调用）")
    print(f"  - 建议先在测试环境验证效果")
    print(f"  - 可以通过环境变量动态调整参数")
    print(f"  - 启用性能监控以观察实际效果：ENABLE_PERFORMANCE_MONITORING=true")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ 用户取消操作")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 错误: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
