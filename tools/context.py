# tools/context.py
"""工具运行时上下文：工作区边界与输出预算，由调用方（CLI / 网页）注入。

单独成模块是为了避免 ``registry -> files -> registry`` 的循环导入：
registry 依赖 ctx，文件/命令工具也依赖 ctx，但两者互不依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ToolContext:
    """一次运行内所有工具共享的环境参数。

    Attributes:
        workspace: 文件与命令类工具的根目录；``None`` 表示没有工作区，
            此类工具会直接返回错误而不是退化成「任意路径可写」。
        max_output_chars: 单个工具输出的字符上限，超出部分截断并标注。
        command_timeout: 命令执行默认超时（秒）。
    """

    workspace: Path | None = None
    max_output_chars: int = 20000
    command_timeout: float = 60.0
