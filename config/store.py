# config/store.py
"""网页工作区的持久化配置：模型列表、工具开关与参数、工作区选择。

所有内容都保存在项目内的 ``data/`` 目录（已被 .gitignore 忽略）：

- ``data/models.json``：模型列表 + 当前激活模型；
- ``data/tools.json``：每个工具的启用状态与参数（如和风天气密钥）；
- ``data/workspace.json``：当前工作区与最近使用目录。

API Key 只存在本地，接口返回时只给掩码。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from config.settings import settings

# 工具清单与参数定义只有一份，在 tools/registry.py；这里只做持久化与对外输出。
from tools.registry import TOOL_SPECS, ToolField, ToolSpec

__all__ = [
    "AppPaths",
    "ModelConfig",
    "ModelStore",
    "ToolField",
    "ToolSpec",
    "ToolStore",
    "Workspace",
    "WorkspaceStore",
]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 工具配置结构版本：v2 起 run_command 默认开启（审批机制上线）
_TOOLS_SCHEMA_VERSION = 2

# 工作区配置结构版本：v2 起从「单个当前目录 + 最近使用」变成命名工作区列表
_WORKSPACE_SCHEMA_VERSION = 2


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _resolve(path: str | Path) -> Path:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = _PROJECT_ROOT / target
    return target


class _JsonFile:
    """极简 JSON 文件读写：懒加载、原子写入、线程安全。"""

    def __init__(self, path: str | Path) -> None:
        self.path = _resolve(path)
        self._lock = threading.RLock()
        self._cached: dict[str, Any] | None = None

    def load(self, default: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._cached is not None:
                return json.loads(json.dumps(self._cached))
            if not self.path.exists():
                self._cached = json.loads(json.dumps(default))
                return json.loads(json.dumps(default))
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = json.loads(json.dumps(default))
            if not isinstance(data, dict):
                data = json.loads(json.dumps(default))
            self._cached = data
            return json.loads(json.dumps(data))

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._cached = json.loads(json.dumps(data))
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError:
                # 只读环境：保留内存态即可，不阻断请求
                pass


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------
@dataclass
class ModelConfig:
    """一个可切换的模型配置（DeepSeek / OpenAI 兼容接口）。"""

    id: str
    name: str
    provider: str = "deepseek"  # deepseek | openai
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.0
    updated_at: str = field(default_factory=_now)

    def public(self) -> dict[str, Any]:
        """对前端输出的结构：不返回原始 API Key。"""
        data = asdict(self)
        key = data.pop("api_key", "") or ""
        data["api_key_set"] = bool(key)
        data["api_key_hint"] = f"••••{key[-4:]}" if len(key) >= 4 else ("••••" if key else "")
        return data


class ModelStore:
    """模型列表与当前激活模型。"""

    DEFAULT_ID = "deepseek-default"

    def __init__(self, path: str | Path | None = None) -> None:
        self.file = _JsonFile(path or settings.models_file)

    # ---- 读取 ----
    def list(self) -> list[ModelConfig]:
        data = self.file.load(self._seed())
        return [self._parse(item) for item in data.get("models", []) if item.get("id")]

    def get(self, model_id: str) -> ModelConfig | None:
        for item in self.list():
            if item.id == model_id:
                return item
        return None

    @property
    def active_id(self) -> str:
        data = self.file.load(self._seed())
        models = data.get("models", [])
        active = data.get("active_id")
        if active and any(item.get("id") == active for item in models):
            return active
        return models[0]["id"] if models else ""

    def active(self) -> ModelConfig | None:
        model_id = self.active_id
        return self.get(model_id) if model_id else None

    # ---- 写入 ----
    def upsert(self, payload: dict[str, Any]) -> ModelConfig:
        data = self.file.load(self._seed())
        models: list[dict[str, Any]] = list(data.get("models", []))
        model_id = (payload.get("id") or "").strip()
        existing: dict[str, Any] | None = None
        if model_id:
            existing = next((m for m in models if m.get("id") == model_id), None)
        if existing is None:
            model_id = model_id or self._new_id(models, payload)
            existing = {"id": model_id, "api_key": ""}
            models.append(existing)

        api_key = (payload.get("api_key") or "").strip()
        if not api_key:  # 留空表示沿用旧 Key
            api_key = existing.get("api_key", "")

        existing.update(
            {
                "id": model_id,
                "name": (payload.get("name") or existing.get("name") or model_id).strip(),
                "provider": (payload.get("provider") or existing.get("provider") or "deepseek").strip(),
                "model": (payload.get("model") or existing.get("model") or "").strip(),
                "base_url": (payload.get("base_url") or "").strip(),
                "api_key": api_key,
                "temperature": float(payload.get("temperature") or 0),
                "updated_at": _now(),
            }
        )
        data["models"] = models
        data.setdefault("active_id", model_id)
        self.file.save(data)
        return self._parse(existing)

    def delete(self, model_id: str) -> bool:
        data = self.file.load(self._seed())
        models = [item for item in data.get("models", []) if item.get("id") != model_id]
        if len(models) == len(data.get("models", [])):
            return False
        data["models"] = models
        if data.get("active_id") == model_id:
            data["active_id"] = models[0]["id"] if models else ""
        self.file.save(data)
        return True

    def set_active(self, model_id: str) -> bool:
        data = self.file.load(self._seed())
        if not any(item.get("id") == model_id for item in data.get("models", [])):
            return False
        data["active_id"] = model_id
        self.file.save(data)
        return True

    def ensure_saved(self) -> None:
        """首次运行时把默认配置落盘，便于用户直接编辑或备份。"""
        if not self.file.path.exists():
            self.file.save(self._seed())

    # ---- 内部 ----
    def _seed(self) -> dict[str, Any]:
        """首次运行时用 .env 里的配置生成默认模型。"""
        key = os.getenv("DEEPSEEK_API_KEY", "")
        model_name = os.getenv("MODEL_NAME", "deepseek-v4-flash")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        return {
            "models": [
                {
                    "id": self.DEFAULT_ID,
                    "name": f"DeepSeek · {model_name}",
                    "provider": "deepseek",
                    "model": model_name,
                    "base_url": base_url,
                    "api_key": key,
                    "temperature": 0,
                    "updated_at": _now(),
                }
            ],
            "active_id": self.DEFAULT_ID,
        }

    def _new_id(self, models: Iterable[dict[str, Any]], payload: dict[str, Any]) -> str:
        base = (payload.get("model") or payload.get("name") or "model").strip().lower()
        slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in base)[:32].strip("-")
        slug = slug or "model"
        taken = {item.get("id") for item in models}
        candidate = slug
        index = 2
        while candidate in taken:
            candidate = f"{slug}-{index}"
            index += 1
        return candidate

    @staticmethod
    def _parse(item: dict[str, Any]) -> ModelConfig:
        return ModelConfig(
            id=str(item.get("id", "")),
            name=str(item.get("name") or item.get("id", "")),
            provider=str(item.get("provider") or "deepseek"),
            model=str(item.get("model") or ""),
            base_url=str(item.get("base_url") or ""),
            api_key=str(item.get("api_key") or ""),
            temperature=float(item.get("temperature") or 0),
            updated_at=str(item.get("updated_at") or ""),
        )


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
# ToolField / ToolSpec / TOOL_SPECS 定义在 tools/registry.py（文件顶部导入），
# 与 Agent 实际可用的工具保持同一事实来源。


def _mask(value: str) -> str:
    """把密钥变成可展示的掩码，只留末 4 位。"""
    if not value:
        return ""
    return f"••••{value[-4:]}" if len(value) >= 4 else "••••"


def _mask_multiline(value: str) -> str:
    """多行密钥（如 PEM）不展示片段，只说明长度。"""
    return f"{len(value)} 字符" if value else ""


class ToolStore:
    """工具启用状态与参数。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.file = _JsonFile(path or settings.tools_file)

    def state(self) -> dict[str, dict[str, Any]]:
        data = self.file.load(self._seed())
        state: dict[str, dict[str, Any]] = {}
        for name, spec in TOOL_SPECS.items():
            entry = data.get("tools", {}).get(name, {})
            stored = entry.get("config", {}) or {}
            config = {}
            for f in spec.fields:
                value = stored.get(f.key)
                config[f.key] = str(f.default if value is None else value)
            state[name] = {
                "enabled": bool(entry.get("enabled", spec.default_enabled)),
                "config": config,
            }
        return state

    def update(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        config: dict[str, Any] | None = None,
        clear: list[str] | None = None,
    ) -> bool:
        if name not in TOOL_SPECS:
            return False
        spec = TOOL_SPECS[name]
        data = self.file.load(self._seed())
        tools = data.setdefault("tools", {})
        entry = tools.setdefault(name, {"enabled": spec.default_enabled, "config": {}})
        current = entry.setdefault("config", {})
        if enabled is not None:
            entry["enabled"] = bool(enabled)

        valid_keys = {field.key for field in spec.fields}
        # 显式清除（留空表示“保持原值”，所以清空必须有单独入口）
        for key in clear or []:
            if key in valid_keys:
                current[key] = ""

        if config:
            for field in spec.fields:
                if field.key not in config or config[field.key] is None:
                    continue
                value = str(config[field.key])
                # 密钥：留空或回传掩码都表示“不修改”，避免把真实密钥覆盖成 ••••
                if field.secret and (not value.strip() or value.startswith("••••")):
                    continue
                current[field.key] = field.normalize(value)
        self.file.save(data)
        return True

    def enabled_tools(self) -> set[str]:
        return {name for name, entry in self.state().items() if entry["enabled"]}

    def configs(self) -> dict[str, dict[str, str]]:
        """启用条目的参数表（含密钥），装配 Agent 时按条目注入。

        这是替代 ``apply_env`` 的关键一步：以前把参数写进 ``os.environ``，
        多个会话并发、各用一套凭据时会互相覆盖，而且进程内任何代码都能读到。
        现在参数跟着这一轮 Agent 走，作用域就是这次装配。
        """
        return {
            name: dict(entry["config"])
            for name, entry in self.state().items()
            if entry["enabled"]
        }

    def public(self) -> list[dict[str, Any]]:
        state = self.state()
        result: list[dict[str, Any]] = []
        for spec in TOOL_SPECS.values():
            config = state[spec.name]["config"]
            fields = []
            for f in spec.fields:
                raw = config.get(f.key, "")
                item = f.public()
                item.update(
                    {
                        # 密钥永不回显：前端留空即表示保持原值
                        "value": "" if f.secret else raw,
                        "value_set": bool(raw),
                        "hint": (_mask_multiline(raw) if f.multiline else _mask(raw)) if f.secret else "",
                    }
                )
                fields.append(item)
            result.append(
                {
                    "name": spec.name,
                    "label": spec.label,
                    "description": spec.description,
                    "risk": spec.risk,
                    "tool_names": list(spec.tool_names),
                    "default_enabled": spec.default_enabled,
                    "enabled": state[spec.name]["enabled"],
                    "fields": fields,
                }
            )
        return result

    def ensure_saved(self) -> None:
        """把默认配置落盘；已有文件则补齐新增条目并做一次性迁移。"""
        if not self.file.path.exists():
            self.file.save(self._seed())
            return

        data = self.file.load(self._seed())
        tools = data.setdefault("tools", {})
        changed = False
        for name, spec in TOOL_SPECS.items():
            if name in tools:
                continue
            tools[name] = {
                "enabled": spec.default_enabled,
                "config": {f.key: os.getenv(f.key, f.default) for f in spec.fields},
            }
            changed = True

        # v1 → v2：审批上线后 run_command 默认打开。v1 里它从未被真正启用过，
        # 旧的 enabled=false 只是当时的默认值，不是用户的选择，因此直接迁移。
        try:
            version = int(data.get("version", 1))
        except (TypeError, ValueError):
            version = _TOOLS_SCHEMA_VERSION
        if version < _TOOLS_SCHEMA_VERSION:
            tools.setdefault("run_command", {"config": {}})["enabled"] = True
            data["version"] = _TOOLS_SCHEMA_VERSION
            changed = True

        if changed:
            self.file.save(data)

    def _seed(self) -> dict[str, Any]:
        tools = {}
        for name, spec in TOOL_SPECS.items():
            tools[name] = {
                "enabled": spec.default_enabled,
                "config": {f.key: os.getenv(f.key, f.default) for f in spec.fields},
            }
        return {"version": _TOOLS_SCHEMA_VERSION, "tools": tools}


# ---------------------------------------------------------------------------
# 工作区
# ---------------------------------------------------------------------------
@dataclass
class Workspace:
    """一个命名工作区，会话按它分组显示在左侧栏。"""

    id: str
    name: str
    path: str
    created_at: str = field(default_factory=_now)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "path": self.path,
            "created_at": self.created_at,
        }


class WorkspaceStore:
    """命名工作区列表（data/workspace.json）。

    旧格式是 ``{"current": ..., "recent": [...]}``，首次读取时迁移成
    ``{"version": 2, "active_id": ..., "workspaces": [...]}``。
    """

    MAX_NAME = 40

    def __init__(self, path: str | Path | None = None, default: str | Path | None = None) -> None:
        self.file = _JsonFile(path or settings.workspace_file)
        self.default_path = str(_resolve(default or _PROJECT_ROOT))

    # ---- 读取 ----
    def list(self) -> list[Workspace]:
        data = self._load()
        return [
            Workspace(
                id=str(item.get("id", "")),
                name=str(item.get("name") or item.get("path") or ""),
                path=str(item.get("path", "")),
                created_at=str(item.get("created_at", "")),
            )
            for item in data.get("workspaces", [])
            if item.get("id") and item.get("path")
        ]

    def get(self, workspace_id: str) -> Workspace | None:
        for workspace in self.list():
            if workspace.id == workspace_id:
                return workspace
        return None

    @property
    def active_id(self) -> str:
        data = self._load()
        active = str(data.get("active_id") or "")
        workspaces = data.get("workspaces", [])
        if active and any(item.get("id") == active for item in workspaces):
            return active
        return str(workspaces[0]["id"]) if workspaces else ""

    def active(self) -> Workspace | None:
        return self.get(self.active_id)

    def state(self) -> dict[str, Any]:
        return {
            "workspaces": [workspace.public() for workspace in self.list()],
            "active_id": self.active_id,
        }

    # ---- 写入 ----
    def set_active(self, workspace_id: str) -> bool:
        data = self._load()
        if not any(item.get("id") == workspace_id for item in data.get("workspaces", [])):
            return False
        data["active_id"] = workspace_id
        self.file.save(data)
        return True

    def add(self, path: str, name: str = "") -> Workspace:
        target = _resolve(path)
        if not target.is_dir():
            raise ValueError(f"目录不存在：{target}")

        data = self._load()
        if any(item.get("path") == str(target) for item in data.get("workspaces", [])):
            raise ValueError("该目录已经添加过了")

        workspace = Workspace(
            id=f"ws-{uuid.uuid4().hex[:8]}",
            name=self._clean_name(name) or target.name or str(target),
            path=str(target),
        )
        data.setdefault("workspaces", []).append(workspace.public())
        data["active_id"] = workspace.id
        self.file.save(data)
        return workspace

    def rename(self, workspace_id: str, name: str) -> bool:
        clean = self._clean_name(name)
        if not clean:
            return False
        data = self._load()
        for item in data.get("workspaces", []):
            if item.get("id") == workspace_id:
                item["name"] = clean
                self.file.save(data)
                return True
        return False

    def remove(self, workspace_id: str) -> bool:
        data = self._load()
        remaining = [item for item in data.get("workspaces", []) if item.get("id") != workspace_id]
        if len(remaining) == len(data.get("workspaces", [])):
            return False
        data["workspaces"] = remaining
        if data.get("active_id") == workspace_id:
            data["active_id"] = remaining[0]["id"] if remaining else ""
        self.file.save(data)
        return True

    def ensure_saved(self) -> None:
        """首次运行时把默认工作区落盘，方便直接编辑或备份。"""
        if not self.file.path.exists():
            self.file.save(self._seed())

    def browse(self, path: str | None = None) -> dict[str, Any]:
        """列出某个目录下的子目录，供前端添加工作区。"""
        target = _resolve(path) if path else self._default_dir()
        if not target.is_dir():
            raise ValueError(f"目录不存在：{target}")
        dirs: list[dict[str, str]] = []
        try:
            for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
                if not child.is_dir() or child.name.startswith("."):
                    continue
                if child.name in {"__pycache__", "node_modules", ".venv"}:
                    continue
                dirs.append({"name": child.name, "path": str(child)})
        except OSError as exc:
            raise ValueError(f"无法读取目录：{exc}") from exc
        parent = target.parent if target.parent != target else None
        return {
            "path": str(target),
            "parent": str(parent) if parent else None,
            "dirs": dirs,
        }

    # ---- 内部 ----
    def _default_dir(self) -> Path:
        active = self.active()
        return Path(active.path) if active else Path(self.default_path)

    def _clean_name(self, name: str) -> str:
        return " ".join(str(name or "").split())[: self.MAX_NAME]

    def _load(self) -> dict[str, Any]:
        data = self.file.load(self._seed())
        if isinstance(data.get("workspaces"), list):
            return data
        return self._migrate(data)

    def _migrate(self, data: dict[str, Any]) -> dict[str, Any]:
        """把旧的「当前目录 + 最近使用」迁移成命名工作区列表。"""
        paths: list[str] = []
        current = str(data.get("current") or "")
        if current:
            paths.append(current)
        for item in data.get("recent") or []:
            if item and str(item) not in paths:
                paths.append(str(item))

        workspaces: list[dict[str, Any]] = []
        for raw in paths:
            resolved = _resolve(raw)
            if not resolved.is_dir():
                continue
            workspace = Workspace(
                id=f"ws-{uuid.uuid4().hex[:8]}",
                name=resolved.name or str(resolved),
                path=str(resolved),
            )
            workspaces.append(workspace.public())

        if not workspaces:
            seeded = self._seed()
            self.file.save(seeded)
            return seeded

        active = next(
            (item["id"] for item in workspaces if item["path"] == current),
            workspaces[0]["id"],
        )
        migrated = {
            "version": _WORKSPACE_SCHEMA_VERSION,
            "active_id": active,
            "workspaces": workspaces,
        }
        self.file.save(migrated)
        return migrated

    def _seed(self) -> dict[str, Any]:
        path = Path(self.default_path)
        workspace = Workspace(id="ws-default", name=path.name or "workspace", path=str(path))
        return {
            "version": _WORKSPACE_SCHEMA_VERSION,
            "active_id": workspace.id,
            "workspaces": [workspace.public()],
        }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
class AppPaths:
    """网页应用的路径集合，方便测试注入临时目录。"""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        base = _resolve(data_dir or settings.data_dir)
        self.data_dir = base
        self.models_file = base / "models.json"
        self.tools_file = base / "tools.json"
        self.workspace_file = base / "workspace.json"
