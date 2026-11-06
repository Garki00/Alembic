# tools/paths.py
"""工作区路径解析：文件 / 命令类工具唯一的边界检查。

所有落盘操作都必须经过 :func:`resolve_in_workspace`。两道关键处理：

1. 先 ``resolve()`` 再判断包含关系。symlink、``..``、Windows 的盘符相对
   路径都会在 resolve 之后露出真实位置，判断的是真实路径；
2. 用 ``Path.relative_to`` 而不是字符串 ``startswith``。后者会把
   ``D:\\work`` 误判为包含 ``D:\\workspace-evil``。
"""

from __future__ import annotations

from pathlib import Path


class WorkspacePathError(ValueError):
    """路径越界或无法解析；带上机读错误码供工具转成 ``Error:`` 输出。"""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def normalize_workspace(raw: str | Path | None) -> Path | None:
    """把外部传入的工作区字符串规范成绝对路径；空值返回 None。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve()
    except OSError:
        return None


def resolve_in_workspace(root: Path | None, raw: str) -> Path:
    """把相对路径解析到工作区内，并拒绝任何逃逸。

    Raises:
        WorkspacePathError: 没有工作区、路径非法或落在工作区之外。
    """
    if root is None:
        raise WorkspacePathError(
            "no_workspace",
            "当前没有可用的工作区，无法操作文件。",
            hint="请先在顶栏选择一个工作区目录。",
        )

    text = (raw or "").strip()
    candidate = Path(text).expanduser() if text else Path(".")
    if not candidate.is_absolute():
        candidate = root / candidate

    try:
        target = candidate.resolve()
    except OSError as exc:
        raise WorkspacePathError(
            "bad_path",
            f"路径无法解析：{text or '.'}（{exc}）。",
        ) from exc

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathError(
            "path_escape",
            f"路径 {text or '.'} 不在当前工作区内，已拒绝访问。",
            hint=f"当前工作区：{root}。请用相对路径（如 src/main.py）或工作区内的绝对路径。",
        ) from exc

    return target


def display_path(root: Path | None, target: Path) -> str:
    """把绝对路径转成相对工作区的显示名，便于模型复用。"""
    if root is None:
        return str(target)
    try:
        rel = target.relative_to(root)
    except ValueError:
        return str(target)
    return str(rel).replace("\\", "/") or "."
