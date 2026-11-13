# tools/context.py
"""工具运行时上下文：工作区边界、输出预算与跨轮共享的协作对象。

单独成模块是为了避免 ``registry -> files -> registry`` 的循环导入：
registry 依赖 ctx，文件/命令工具也依赖 ctx，但两者互不依赖。

这里的几个协作对象都由调用方（CLI / Web）注入：

- ``session``：当前会话引用。CLI 切换会话时原地改 ``session_id``，
  已经装配好的工具因此不需要重建；
- ``asker``：``ask_user_question`` 的提问通道（网页走 SSE，命令行走终端）；
- ``todo_store``：任务清单的持久化存储；
- ``process_manager``：后台进程管理器，按工作区共享；
- ``emit``：把工具内部事件（如 todo 更新）推给前端的回调，可为空。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # 仅用于类型标注，避免运行时循环导入
    from threading import Event

    from tools.catalog import ToolActivation
    from tools.processes import ProcessManager
    from tools.questions import Asker
    from tools.sandbox.base import SandboxPolicy, SandboxRunner
    from tools.todo import TodoStore


@dataclass
class SessionRef:
    """可变的会话引用；工具在真正调用时才读取 ``session_id``。"""

    session_id: str = "default"


@dataclass(frozen=True)
class ToolContext:
    """一次运行内所有工具共享的环境参数。

    Attributes:
        workspace: 文件与命令类工具的根目录；``None`` 表示没有工作区，
            此类工具会直接返回错误而不是退化成「任意路径可写」。
        max_output_chars: 单个工具输出的字符上限，超出部分截断并标注。
        command_timeout: 命令执行默认超时（秒）。
        session: 当前会话引用，todo 等按会话保存的工具使用它。
        asker: 向用户提问并等待回答；``None`` 时提问工具返回错误。
        todo_store: 任务清单存储；``None`` 时退化为进程内存储。
        process_manager: 后台进程管理器；``None`` 时按工作区创建默认实例。
        emit: 工具事件出口，网页端用它推送 ``todo`` 等 SSE 事件。
        vision: 视觉通道，``read_image`` 用它把图片交给当前模型理解。
        terminal: 命令工具优先使用的终端：powershell / cmd / gitbash / wsl。
        activation: 当前会话的扩展工具激活状态；``None`` 时扩展工具不可加载。
        enabled_tool_names: 用户设置里实际启用的具体工具名，供 tools_search 过滤。
        cancel_event: 停止生成时置位的取消信号，前台命令会轮询它并结束进程树。
        sandbox: 命令沙盒后端；``None`` 时命令工具必须失败关闭，不能直接执行。
        sandbox_policy: 本次运行的沙盒策略（工作区、网络、临时目录）。
    """

    workspace: Path | None = None
    max_output_chars: int = 20000
    command_timeout: float = 60.0
    terminal: str = "powershell"
    activation: "ToolActivation | None" = None
    enabled_tool_names: frozenset[str] = frozenset()
    session: SessionRef = field(default_factory=SessionRef)
    asker: Asker | None = None
    todo_store: TodoStore | None = None
    process_manager: ProcessManager | None = None
    emit: Callable[[str, dict[str, Any]], None] | None = None
    cancel_event: "Event | None" = None
    sandbox: "SandboxRunner | None" = None
    sandbox_policy: "SandboxPolicy | None" = None
    #: 视觉通道：``(data_url, prompt) -> 文字描述``。由运行时用当前模型注入；
    #: ``None`` 时 ``read_image`` 返回 ``vision_unavailable``。
    vision: Callable[[str, str], str] | None = None

    @property
    def session_id(self) -> str:
        """当前会话 ID 的快捷读取。"""
        return self.session.session_id or "default"

    def emit_event(self, kind: str, data: dict[str, Any]) -> None:
        """安全地推一个事件：没有前端（命令行）时静默忽略。"""
        if self.emit is None:
            return
        try:
            self.emit(kind, data)
        except Exception:  # 前端通道异常不应让工具失败
            pass
