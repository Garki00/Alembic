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
from typing import Any, Iterable, Mapping

from config.settings import settings

# 工具清单与参数定义只有一份，在 tools/registry.py；这里只做持久化与对外输出。
from tools.registry import TOOL_SPECS, ToolField, ToolSpec

__all__ = [
    "AppPaths",
    "ModelConfig",
    "ModelStore",
    "PreferencesStore",
    "ToolField",
    "ToolSpec",
    "ToolStore",
    "Workspace",
    "WorkspaceStore",
]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 工具配置结构版本：
# v2 起 run_command 默认开启（审批机制上线）；
# v3 起实时天气默认关闭（需要用户自己的和风天气凭据与 API Host）。
_TOOLS_SCHEMA_VERSION = 3

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
_THINKING_LEVELS = ("none", "low", "high", "max")
_DEFAULT_THINKING_LEVELS = ["low", "high", "max"]


def _thinking_level(value: Any) -> str:
    text = str(value or "none").strip().lower()
    return text if text in _THINKING_LEVELS else "none"


def _sort_levels(values: Iterable[str]) -> list[str]:
    chosen: list[str] = []
    for value in values or ():
        level = _thinking_level(value)
        if level not in chosen:
            chosen.append(level)
    chosen.sort(key=_THINKING_LEVELS.index)
    return chosen


def _derive_thinking_levels(raw: Any, current: Any) -> tuple[list[str], str]:
    """返回 (可选等级列表, 当前等级)。

    旧数据没有 thinking_levels 时，默认 low/high/max；如果旧的当前等级
    不在默认集合里（例如 none），把它补进去，避免升级后改变模型行为。
    """
    current_level = _thinking_level(current)
    if isinstance(raw, list) and raw:
        levels = _sort_levels(raw)
        if not levels:
            levels = list(_DEFAULT_THINKING_LEVELS)
        if current_level not in levels:
            current_level = levels[-1]
        return levels, current_level
    levels = list(_DEFAULT_THINKING_LEVELS)
    if current_level not in levels:
        levels = _sort_levels([*levels, current_level])
    return levels, current_level


def _slugify(text: str, fallback: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(text).lower())
    return slug.strip("-")[:32] or fallback


def _unique_id(taken: Iterable[str], base: str) -> str:
    slug = _slugify(base, "item")
    seen = {str(item) for item in taken if item}
    candidate = slug
    index = 2
    while candidate in seen:
        candidate = f"{slug}-{index}"
        index += 1
    return candidate


@dataclass
class ProviderModel:
    """供应商下的一个模型条目。"""

    id: str
    model: str
    name: str = ""
    thinking: str = "high"
    thinking_levels: list[str] = field(default_factory=lambda: list(_DEFAULT_THINKING_LEVELS))
    temperature: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model": self.model,
            "name": self.name,
            "thinking": self.thinking,
            "thinking_levels": list(self.thinking_levels),
            "temperature": self.temperature,
        }


@dataclass
class ProviderConfig:
    """一个模型供应商（一组共用 Base URL / API Key 的模型）。"""

    id: str
    provider: str = "custom"
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    models: list[ProviderModel] = field(default_factory=list)
    updated_at: str = field(default_factory=_now)

    def public(self) -> dict[str, Any]:
        """对前端输出：不返回原始 API Key。"""
        key = self.api_key or ""
        return {
            "id": self.id,
            "provider": self.provider,
            "name": self.name,
            "base_url": self.base_url,
            "api_key_set": bool(key),
            "api_key_hint": _mask(key),
            "models": [item.public() for item in self.models],
            "updated_at": self.updated_at,
        }


@dataclass
class ModelConfig:
    """当前激活模型的运行时视图（供应商配置 + 选中的模型）。"""

    id: str
    name: str
    provider: str = "custom"
    provider_id: str = ""
    provider_name: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    temperature: float = 0.0
    thinking: str = "high"
    thinking_levels: list[str] = field(default_factory=lambda: list(_DEFAULT_THINKING_LEVELS))
    updated_at: str = field(default_factory=_now)

    def public(self) -> dict[str, Any]:
        """对前端输出的结构：不返回原始 API Key。"""
        data = asdict(self)
        key = data.pop("api_key", "") or ""
        data["api_key_set"] = bool(key)
        data["api_key_hint"] = _mask(key)
        return data


class ModelStore:
    """供应商列表、每个供应商的模型列表，以及当前激活模型。"""

    SCHEMA_VERSION = 2
    DEFAULT_PROVIDER_ID = "prov-deepseek"
    DEFAULT_MODEL_ID = "deepseek-default"

    def __init__(self, path: str | Path | None = None) -> None:
        self.file = _JsonFile(path or settings.models_file)

    # ---- 读取 ----
    def _load(self) -> dict[str, Any]:
        data = self.file.load(self._seed())
        if "providers" not in data:
            data = self._migrate(data)
            self.file.save(data)
        return data

    def providers(self) -> list[ProviderConfig]:
        data = self._load()
        return [
            self._parse_provider(item)
            for item in data.get("providers", [])
            if isinstance(item, dict) and item.get("id")
        ]

    def get_provider(self, provider_id: str) -> ProviderConfig | None:
        for provider in self.providers():
            if provider.id == provider_id:
                return provider
        return None

    @property
    def active_provider_id(self) -> str:
        data = self._load()
        providers = data.get("providers", [])
        active = data.get("active_provider_id")
        if active and any(item.get("id") == active for item in providers):
            return str(active)
        return str(providers[0]["id"]) if providers else ""

    @property
    def active_model_id(self) -> str:
        data = self._load()
        provider_id = self.active_provider_id
        provider = next(
            (item for item in data.get("providers", []) if item.get("id") == provider_id),
            None,
        )
        models = (provider or {}).get("models", [])
        active = data.get("active_model_id")
        if active and any(item.get("id") == active for item in models):
            return str(active)
        return str(models[0]["id"]) if models else ""

    def active(self) -> ModelConfig | None:
        provider_id = self.active_provider_id
        model_id = self.active_model_id
        if not provider_id or not model_id:
            return None
        provider = self.get_provider(provider_id)
        if provider is None:
            return None
        model = next((item for item in provider.models if item.id == model_id), None)
        if model is None:
            return None
        return ModelConfig(
            id=model.id,
            name=model.name or model.model,
            provider=provider.provider,
            provider_id=provider.id,
            provider_name=provider.name,
            model=model.model,
            base_url=provider.base_url,
            api_key=provider.api_key,
            temperature=model.temperature,
            thinking=model.thinking,
            thinking_levels=list(model.thinking_levels),
            updated_at=provider.updated_at,
        )

    def needs_setup(self) -> bool:
        """是否还没有一个可用的供应商（Key + Base URL + 至少一个模型）。"""
        for provider in self.providers():
            if provider.api_key.strip() and provider.base_url.strip() and provider.models:
                return False
        return True

    # ---- 写入 ----
    def upsert_provider(self, payload: dict[str, Any]) -> ProviderConfig:
        data = self._load()
        providers: list[dict[str, Any]] = list(data.get("providers", []))
        provider_id = str(payload.get("id") or "").strip()
        existing: dict[str, Any] | None = None
        if provider_id:
            existing = next((item for item in providers if item.get("id") == provider_id), None)
        if existing is None:
            provider_id = provider_id or _unique_id(
                (item.get("id") for item in providers),
                f"prov-{payload.get('provider') or 'custom'}",
            )
            existing = {"id": provider_id, "api_key": ""}
            providers.append(existing)

        api_key = str(payload.get("api_key") or "").strip()
        if not api_key:  # 留空表示沿用旧 Key
            api_key = str(existing.get("api_key") or "")

        raw_models = payload.get("models")
        if raw_models is None:
            models = list(existing.get("models", []))
        else:
            models = self._normalize_models(raw_models, existing.get("models", []))

        existing.update(
            {
                "id": provider_id,
                "provider": str(payload.get("provider") or existing.get("provider") or "custom").strip(),
                "name": str(payload.get("name") or "").strip(),
                "base_url": str(payload.get("base_url") or "").strip(),
                "api_key": api_key,
                "models": models,
                "updated_at": _now(),
            }
        )
        data["providers"] = providers
        self._ensure_active(data)
        self.file.save(data)
        return self._parse_provider(existing)

    def delete_provider(self, provider_id: str) -> bool:
        data = self._load()
        providers = [item for item in data.get("providers", []) if item.get("id") != provider_id]
        if len(providers) == len(data.get("providers", [])):
            return False
        data["providers"] = providers
        if data.get("active_provider_id") == provider_id:
            data["active_provider_id"] = ""
            data["active_model_id"] = ""
        self._ensure_active(data)
        self.file.save(data)
        return True

    def set_active(self, provider_id: str, model_id: str = "") -> bool:
        data = self._load()
        provider = next(
            (item for item in data.get("providers", []) if item.get("id") == provider_id),
            None,
        )
        if provider is None:
            return False
        models = provider.get("models", [])
        if model_id and not any(item.get("id") == model_id for item in models):
            return False
        if not model_id:
            model_id = str(models[0]["id"]) if models else ""
        if not model_id:
            return False
        data["active_provider_id"] = provider_id
        data["active_model_id"] = model_id
        self.file.save(data)
        return True

    def set_thinking(self, provider_id: str, model_id: str, thinking: str) -> bool:
        """只更新某个模型的当前推理等级（必须在它的可选等级里）。"""
        data = self._load()
        provider = next(
            (item for item in data.get("providers", []) if item.get("id") == provider_id),
            None,
        )
        if provider is None:
            return False
        model = next(
            (item for item in provider.get("models", []) if item.get("id") == model_id),
            None,
        )
        if model is None:
            return False
        levels, _current = _derive_thinking_levels(
            model.get("thinking_levels"), model.get("thinking")
        )
        level = _thinking_level(thinking)
        if level not in levels:
            return False
        model["thinking_levels"] = levels
        model["thinking"] = level
        self.file.save(data)
        return True

    def ensure_saved(self) -> None:
        """首次运行时把默认配置落盘，便于用户直接编辑或备份。"""
        if not self.file.path.exists():
            self.file.save(self._seed())
            return
        self._load()

    # ---- 内部 ----
    def _seed(self) -> dict[str, Any]:
        """首次运行时的空配置；只有显式配置了 Key 与模型名才自动建一条。"""
        key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        model_name = os.getenv("MODEL_NAME", "").strip()
        if not (key and model_name):
            return {
                "version": self.SCHEMA_VERSION,
                "providers": [],
                "active_provider_id": "",
                "active_model_id": "",
            }
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
        return {
            "version": self.SCHEMA_VERSION,
            "providers": [
                {
                    "id": self.DEFAULT_PROVIDER_ID,
                    "provider": "deepseek",
                    "name": "",
                    "base_url": base_url,
                    "api_key": key,
                    "models": [
                        {
                            "id": self.DEFAULT_MODEL_ID,
                            "model": model_name,
                            "name": "",
                            "thinking": "high",
                            "thinking_levels": list(_DEFAULT_THINKING_LEVELS),
                            "temperature": 0,
                        }
                    ],
                    "updated_at": _now(),
                }
            ],
            "active_provider_id": self.DEFAULT_PROVIDER_ID,
            "active_model_id": self.DEFAULT_MODEL_ID,
        }

    def _migrate(self, data: dict[str, Any]) -> dict[str, Any]:
        """把旧的「扁平模型列表」迁移成「供应商 + 模型列表」。"""
        legacy = data.get("models") or []
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for item in legacy:
            if not isinstance(item, dict):
                continue
            provider = str(item.get("provider") or "custom").strip()
            base_url = str(item.get("base_url") or "").strip()
            api_key = str(item.get("api_key") or "")
            groups.setdefault((provider, base_url, api_key), []).append(item)

        providers: list[dict[str, Any]] = []
        active_legacy = str(data.get("active_id") or "")
        active_provider_id = ""
        active_model_id = ""
        for index, ((provider, base_url, api_key), items) in enumerate(groups.items(), 1):
            provider_id = f"prov-{index}-{_slugify(provider, 'provider')}"
            models: list[dict[str, Any]] = []
            seen_models: set[str] = set()
            for item in items:
                model = str(item.get("model") or "").strip()
                if not model or model in seen_models:
                    continue
                seen_models.add(model)
                model_id = str(item.get("id") or "").strip() or _unique_id(
                    (row["id"] for row in models), model
                )
                models.append(
                    {
                        "id": model_id,
                        "model": model,
                        "name": str(item.get("name") or "").strip(),
                        "thinking": "none",
                        "temperature": float(item.get("temperature") or 0),
                    }
                )
            if not models:
                continue
            providers.append(
                {
                    "id": provider_id,
                    "provider": provider,
                    "name": "",
                    "base_url": base_url,
                    "api_key": api_key,
                    "models": models,
                    "updated_at": str(items[0].get("updated_at") or _now()),
                }
            )
            if active_legacy and any(row["id"] == active_legacy for row in models):
                active_provider_id = provider_id
                active_model_id = active_legacy

        if not active_provider_id and providers:
            active_provider_id = str(providers[0]["id"])
            active_model_id = str(providers[0]["models"][0]["id"])
        return {
            "version": self.SCHEMA_VERSION,
            "providers": providers,
            "active_provider_id": active_provider_id,
            "active_model_id": active_model_id,
        }

    def _normalize_models(
        self,
        raw_models: Any,
        existing_models: Any,
    ) -> list[dict[str, Any]]:
        existing = {
            str(item.get("id")): item
            for item in (existing_models or [])
            if isinstance(item, dict) and item.get("id")
        }
        result: list[dict[str, Any]] = []
        seen_models: set[str] = set()
        for item in raw_models or []:
            if not isinstance(item, dict):
                continue
            model = str(item.get("model") or "").strip()
            if not model or model in seen_models:
                continue
            seen_models.add(model)
            model_id = str(item.get("id") or "").strip()
            if not model_id or any(row.get("id") == model_id for row in result):
                model_id = _unique_id((row.get("id", "") for row in result), model)
            previous = existing.get(model_id, {})
            raw_levels = item.get("thinking_levels")
            if raw_levels is None:
                raw_levels = previous.get("thinking_levels")
            current = item.get("thinking", previous.get("thinking", "max"))
            levels, current = _derive_thinking_levels(raw_levels, current)
            result.append(
                {
                    "id": model_id,
                    "model": model,
                    "name": str(item.get("name") or previous.get("name") or "").strip(),
                    "thinking": current,
                    "thinking_levels": levels,
                    "temperature": float(
                        item.get("temperature", previous.get("temperature", 0)) or 0
                    ),
                }
            )
        return result

    def _ensure_active(self, data: dict[str, Any]) -> None:
        providers = data.get("providers", [])
        provider = next(
            (item for item in providers if item.get("id") == data.get("active_provider_id")),
            None,
        )
        if provider is None or not provider.get("models"):
            provider = next((item for item in providers if item.get("models")), None)
            data["active_provider_id"] = provider["id"] if provider else ""
        if provider is None:
            data["active_model_id"] = ""
            return
        models = provider.get("models", [])
        if not any(item.get("id") == data.get("active_model_id") for item in models):
            data["active_model_id"] = models[0]["id"]

    @staticmethod
    def _parse_provider(item: dict[str, Any]) -> ProviderConfig:
        models: list[ProviderModel] = []
        for row in item.get("models", []):
            if not isinstance(row, dict):
                continue
            model = str(row.get("model") or "").strip()
            if not model:
                continue
            levels, current = _derive_thinking_levels(
                row.get("thinking_levels"), row.get("thinking")
            )
            models.append(
                ProviderModel(
                    id=str(row.get("id") or model),
                    model=model,
                    name=str(row.get("name") or ""),
                    thinking=current,
                    thinking_levels=levels,
                    temperature=float(row.get("temperature") or 0),
                )
            )
        return ProviderConfig(
            id=str(item.get("id", "")),
            provider=str(item.get("provider") or "custom"),
            name=str(item.get("name") or ""),
            base_url=str(item.get("base_url") or ""),
            api_key=str(item.get("api_key") or ""),
            models=models,
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
        # v2 → v3：实时天气改为默认关闭（需要用户自己的凭据与 API Host）。
        try:
            version = int(data.get("version", 1))
        except (TypeError, ValueError):
            version = _TOOLS_SCHEMA_VERSION
        if version < 2:
            tools.setdefault("run_command", {"config": {}})["enabled"] = True
        if version < 3:
            tools.setdefault("get_weather", {"config": {}})["enabled"] = False
        if version < _TOOLS_SCHEMA_VERSION:
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
        """新安装不自动添加工作区：由用户在界面上选择目录后再创建。"""
        return {
            "version": _WORKSPACE_SCHEMA_VERSION,
            "active_id": "",
            "workspaces": [],
        }


# ---------------------------------------------------------------------------
# 界面偏好（通用设置）
# ---------------------------------------------------------------------------
TRANSCRIPT_VIEW_MODES = ("compact", "standard", "detailed", "verbose")
COLLAPSE_TIMINGS = ("completion", "next_input")
PERMISSION_MODES = ("read_only", "approval_all", "workspace_write", "full_access")
TERMINAL_CHOICES = ("powershell", "cmd", "gitbash", "wsl")
LINK_TARGETS = ("sidebar", "browser")
DEFAULT_TRANSCRIPT_VIEW = "detailed"
DEFAULT_COLLAPSE_TIMING = "completion"
DEFAULT_PERMISSION_MODE = "workspace_write"
DEFAULT_TERMINAL = "powershell"
DEFAULT_LINK_TARGET = "sidebar"
PREFERENCES_VERSION = 2


def _normalize_preferences(data: Mapping[str, Any]) -> dict[str, Any]:
    mode = str(data.get("transcript_view") or DEFAULT_TRANSCRIPT_VIEW).strip().lower()
    if mode not in TRANSCRIPT_VIEW_MODES:
        mode = DEFAULT_TRANSCRIPT_VIEW
    timing = str(data.get("collapse_timing") or DEFAULT_COLLAPSE_TIMING).strip().lower()
    if timing not in COLLAPSE_TIMINGS:
        timing = DEFAULT_COLLAPSE_TIMING
    permission = str(data.get("permission_mode") or DEFAULT_PERMISSION_MODE).strip().lower()
    # v1 的 read_only 实际语义是「所有工具都要审批」。v2 起该语义改名为
    # approval_all，read_only 变成真正的只读；旧配置在读取时一次性迁移。
    try:
        version = int(data.get("version", 1))
    except (TypeError, ValueError):
        version = 1
    if version < PREFERENCES_VERSION and permission == "read_only":
        permission = "approval_all"
    if permission not in PERMISSION_MODES:
        permission = DEFAULT_PERMISSION_MODE
    terminal = str(data.get("terminal") or DEFAULT_TERMINAL).strip().lower()
    if terminal not in TERMINAL_CHOICES:
        terminal = DEFAULT_TERMINAL
    link_target = str(data.get("link_target") or DEFAULT_LINK_TARGET).strip().lower()
    if link_target not in LINK_TARGETS:
        link_target = DEFAULT_LINK_TARGET
    developer = data.get("developer_tools", False)
    if isinstance(developer, str):
        developer = developer.strip().lower() in {"1", "true", "on", "yes"}
    return {
        "transcript_view": mode,
        "collapse_timing": timing,
        "permission_mode": permission,
        "terminal": terminal,
        "link_target": link_target,
        "developer_tools": bool(developer),
    }


class PreferencesStore:
    """通用设置的持久化：工作步骤、权限、终端偏好与链接打开方式。

    这是纯本机偏好，不影响模型请求；但放在用户数据目录里，
    换浏览器/刷新后仍保持一致。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.file = _JsonFile(path or settings.preferences_file)

    def get(self) -> dict[str, Any]:
        return _normalize_preferences(
            self.file.load(
                {
                    "version": PREFERENCES_VERSION,
                    "transcript_view": DEFAULT_TRANSCRIPT_VIEW,
                    "collapse_timing": DEFAULT_COLLAPSE_TIMING,
                    "permission_mode": DEFAULT_PERMISSION_MODE,
                    "terminal": DEFAULT_TERMINAL,
                    "link_target": DEFAULT_LINK_TARGET,
                    "developer_tools": False,
                }
            )
        )

    def update(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        current = self.get()
        if "transcript_view" in patch:
            mode = str(patch["transcript_view"]).strip().lower()
            if mode not in TRANSCRIPT_VIEW_MODES:
                raise ValueError("transcript_view 只能是 compact / standard / detailed / verbose")
            current["transcript_view"] = mode
        if "collapse_timing" in patch:
            timing = str(patch["collapse_timing"]).strip().lower()
            if timing not in COLLAPSE_TIMINGS:
                raise ValueError("collapse_timing 只能是 completion / next_input")
            current["collapse_timing"] = timing
        if "permission_mode" in patch:
            permission = str(patch["permission_mode"]).strip().lower()
            if permission not in PERMISSION_MODES:
                raise ValueError(
                    "permission_mode 只能是 read_only / approval_all / workspace_write / full_access"
                )
            current["permission_mode"] = permission
        if "terminal" in patch:
            terminal = str(patch["terminal"]).strip().lower()
            if terminal not in TERMINAL_CHOICES:
                raise ValueError("terminal 只能是 powershell / cmd / gitbash / wsl")
            current["terminal"] = terminal
        if "link_target" in patch:
            target = str(patch["link_target"]).strip().lower()
            if target not in LINK_TARGETS:
                raise ValueError("link_target 只能是 sidebar / browser")
            current["link_target"] = target
        if "developer_tools" in patch:
            value = patch["developer_tools"]
            current["developer_tools"] = value if isinstance(value, bool) else (
                str(value).strip().lower() in {"1", "true", "on", "yes"}
            )
        payload = dict(current)
        payload["version"] = PREFERENCES_VERSION
        self.file.save(payload)
        return current


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
        self.preferences_file = (
            _resolve(settings.preferences_file) if data_dir is None else base / "preferences.json"
        )
