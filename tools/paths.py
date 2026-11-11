# tools/paths.py
"""工作区路径解析：文件 / 命令类工具唯一的边界检查。

所有落盘操作都必须经过 :func:`resolve_in_workspace`。三道关键处理：

1. 先 ``resolve()`` 再判断包含关系。symlink、``..``、Windows 的盘符相对
   路径都会在 resolve 之后露出真实位置，判断的是真实路径；
2. 用 ``Path.relative_to`` 而不是字符串 ``startswith``。后者会把
   ``D:\\work`` 误判为包含 ``D:\\workspace-evil``；
3. 在边界之内再排除凭据路径：即使工作区是用户主目录，``.ssh``、
   ``.aws``、私钥与 ``.env`` 也不允许被读取（``.env`` 可以写，但不能
   把内容读回来）。

第三层只覆盖文件工具。shell 命令由命令策略与审批负责，不能把它当成
操作系统级沙盒。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

Access = Literal["read", "write"]

#: 这些目录下的内容视为凭据，读取与写入都拒绝。
_SENSITIVE_DIRS = {".ssh", ".aws", ".kube", ".gnupg", ".docker"}

#: 读取敏感目录：.docker 的 config.json 里可能有 registry 凭据。
_SENSITIVE_READ_DIRS = _SENSITIVE_DIRS

#: 明确禁止读取的文件名。
_SENSITIVE_READ_NAMES = {
    ".netrc", ".npmrc", ".pypirc", "credentials", "id_rsa", "id_dsa",
    "id_ecdsa", "id_ed25519",
}

#: 明确禁止写入的文件名（.env 允许写、不允许读）。
_SENSITIVE_WRITE_NAMES = {".netrc"}

_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}

#: .env 的这些衍生文件名是模板，允许读取。
_ENV_TEMPLATE_SUFFIXES = {".example", ".sample", ".template", ".dist"}


class WorkspacePathError(ValueError):
    """路径越界、受保护或无法解析；带上机读错误码供工具转成 ``Error:`` 输出。"""

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


@lru_cache(maxsize=1)
def _app_home() -> Path | None:
    """应用数据目录（只解析一次供路径检查复用）。"""
    try:
        from config.settings import settings

        return Path(settings.app_home).expanduser().resolve()
    except (ImportError, OSError):
        return None


def protected_reason(target: Path, root: Path | None, access: Access = "read") -> str:
    """判断路径是否属于受保护的凭据路径；返回原因，空字符串表示放行。"""
    if root is None:
        return ""
    try:
        relative = target.relative_to(root)
    except ValueError:
        return ""
    parts = [part.lower() for part in relative.parts]
    dirs = _SENSITIVE_READ_DIRS if access == "read" else _SENSITIVE_DIRS
    for part in parts[:-1]:
        if part in dirs:
            return f"受保护的凭据目录 {part}"
    name = target.name.lower()
    if name in dirs:
        return f"受保护的凭据目录 {name}"

    # 应用自己的数据目录（models.json / tools.json / 会话）不在工作区语义内，
    # 即使被选成工作区也不允许 Agent 读写。
    app_home = _app_home()
    if app_home is not None and (target == app_home or app_home in target.parents):
        return "应用数据目录"

    if access == "read":
        if name == ".env" or (
            name.startswith(".env.") and not any(name.endswith(s) for s in _ENV_TEMPLATE_SUFFIXES)
        ):
            return "环境变量文件（可能包含密钥）"
        if name in _SENSITIVE_READ_NAMES or name.startswith(("id_rsa.", "id_ed25519.")):
            return f"凭据文件 {target.name}"
        if target.suffix.lower() in _SENSITIVE_SUFFIXES:
            return f"凭据文件 {target.name}"
    else:
        if name in _SENSITIVE_WRITE_NAMES:
            return f"凭据文件 {target.name}"

    return ""


def is_protected_read(target: Path, root: Path | None) -> bool:
    """递归搜索时快速跳过受保护的读取目标。"""
    return bool(protected_reason(target, root, "read"))


def resolve_in_workspace(root: Path | None, raw: str, *, access: Access = "read") -> Path:
    """把相对路径解析到工作区内，拒绝任何逃逸与受保护路径。

    Args:
        root: 工作区根目录。
        raw: 用户/模型给出的路径。
        access: ``read`` 会额外拒绝凭据文件；``write`` 只拒绝明确禁止写入的
            凭据路径（``.env`` 允许写入，但之后无法读回）。

    Raises:
        WorkspacePathError: 没有工作区、路径非法、越界或受保护。
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

    reason = protected_reason(target, root, access)
    if reason:
        raise WorkspacePathError(
            "protected_path",
            f"{reason}不在可访问范围内：{text or '.'}。",
            hint="凭据文件请直接在系统里管理；Agent 不会读取或修改它们。",
        )

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
