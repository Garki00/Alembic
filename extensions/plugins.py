"""Locally installed, trusted Python tools (not a sandbox).

Version 1 plugin.json contains id, name and tools with name, description,
inputSchema and entrypoint (relative ``file.py:function``). Discovery never
imports extension code. The registry must keep these tools disabled by default,
classify them as exec, and obtain approval before invoking the built tool.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.util
import importlib.abc
import importlib.machinery
import inspect
import json
from pathlib import Path, PureWindowsPath
import re
import sys
import types
from typing import Any, Iterable
from dataclasses import dataclass
from uuid import uuid4

from jsonschema.validators import validator_for
from langchain_core.tools import StructuredTool
from tools.context import ToolContext


@dataclass(frozen=True)
class PluginTool:
    name: str
    label: str
    description: str
    source_id: str
    input_schema: dict[str, Any]
    root: Path
    entrypoint: str


def _entrypoint(root: Path, value: str) -> tuple[Path, str]:
    if not isinstance(value, str) or value.count(":") != 1:
        raise ValueError("entrypoint must be relative file.py:function")
    filename, function = value.split(":")
    relative = Path(filename)
    if (relative.is_absolute() or PureWindowsPath(filename).drive
            or ".." in relative.parts or ".." in PureWindowsPath(filename).parts
            or "\\" in filename or not function.isidentifier()
            or relative.suffix != ".py"):
        raise ValueError("entrypoint must be a contained relative Python file:function")
    # Resolve at discovery AND execution, so replacing a file with a symlink
    # after discovery cannot redirect the loader outside the installation.
    path = (root / relative).resolve(strict=True)
    if not path.is_relative_to(root.absolute()) or not path.is_file():
        raise ValueError("entrypoint escapes plugin directory or is not a file")
    return path, function


def _name(source: str, tool: str) -> str:
    raw = f"plugin_{source}_{tool}"
    if len(raw) <= 64:
        return raw
    return raw[:47] + "_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def discover_tools(roots: Iterable[str | Path]) -> tuple[list[PluginTool], list[str]]:
    """Scan each root and its immediate child directories; isolate bad entries."""
    tools: list[PluginTool] = []
    errors: list[str] = []
    seen_paths: set[Path] = set()
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for configured in roots:
        root = Path(configured).expanduser()
        try:
            if not root.exists():
                continue
            manifests = ([root / "plugin.json"] if (root / "plugin.json").is_file() else [])
            manifests += sorted((child / "plugin.json" for child in root.iterdir()
                                 if child.is_dir() and (child / "plugin.json").is_file()), key=str)
        except (OSError, ValueError) as exc:
            errors.append(f"{root}: {exc}")
            continue
        for manifest in manifests:
            try:
                resolved = manifest.resolve(strict=True)
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                installation = manifest.parent.resolve(strict=True)
                if not resolved.is_relative_to(installation):
                    raise ValueError("manifest escapes plugin directory")
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
                    raise ValueError("unsupported manifest version (expected version: 1)")
                source = data.get("id")
                if not isinstance(source, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", source):
                    raise ValueError("id must contain only letters, digits, underscores and hyphens")
                if not isinstance(data.get("name"), str) or not data["name"].strip():
                    raise ValueError("plugin name must be a nonempty string")
                if not isinstance(data.get("tools"), list):
                    raise ValueError("tools must be an array")
                if source in seen_ids:
                    raise ValueError(f"duplicate plugin id: {source}")
                seen_ids.add(source)
                local_names: set[str] = set()
                for index, definition in enumerate(data["tools"]):
                    try:
                        if not isinstance(definition, dict):
                            raise ValueError("tool must be an object")
                        tool = definition.get("name")
                        if not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", tool):
                            raise ValueError("invalid tool name")
                        name = _name(source, tool)
                        if tool in local_names or name in seen_names:
                            raise ValueError(f"duplicate tool name: {tool}")
                        local_names.add(tool)
                        description = definition.get("description")
                        if not isinstance(description, str):
                            raise ValueError("description must be a string")
                        schema = definition.get("inputSchema")
                        if not isinstance(schema, dict) or schema.get("type") != "object":
                            raise ValueError("inputSchema must be an object JSON Schema")
                        validator_for(schema).check_schema(schema)
                        entrypoint = definition.get("entrypoint")
                        _entrypoint(installation, entrypoint)
                        tools.append(PluginTool(name, f"{data['name']}: {tool}", description,
                                                source, copy.deepcopy(schema), installation, entrypoint))
                        seen_names.add(name)
                    except Exception as exc:
                        errors.append(f"{manifest}: tools[{index}]: {exc}")
            except Exception as exc:
                errors.append(f"{manifest}: {exc}")
    return tools, errors


class _FreshSourceLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        return compile(self.get_data(self.path), self.path, "exec")


class _PluginFinder(importlib.abc.MetaPathFinder):
    def __init__(self, namespace: str, root: Path):
        self.namespace = namespace
        self.root = root.resolve()

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(self.namespace + "."):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and spec.origin:
            origin = Path(spec.origin).resolve()
            if not origin.is_relative_to(self.root):
                raise ImportError("relative plugin import escapes installation")
            if isinstance(spec.loader, importlib.machinery.SourceFileLoader):
                spec.loader = _FreshSourceLoader(fullname, str(origin))
        return spec


class _PluginStructuredTool(StructuredTool):
    @property
    def tool_call_schema(self) -> dict[str, Any]:
        # LangChain normally injects the description into dict schemas. Keep
        # the installed manifest schema unchanged, including its own metadata.
        return copy.deepcopy(self.args_schema)


def build_plugin_tool(plugin: PluginTool, ctx: ToolContext, config: dict) -> StructuredTool:
    """Build metadata only; import trusted native code when approved execution runs.

    Each call uses a fresh private package generation. Relative imports work
    without modifying sys.path; installed third-party packages remain available.
    """
    schema = copy.deepcopy(plugin.input_schema)
    validator = validator_for(schema)(schema)
    plugin_config = copy.deepcopy(config)

    async def execute(arguments: dict) -> Any:
        validator.validate(arguments)
        path, function = _entrypoint(plugin.root, plugin.entrypoint)
        package_name = "_alembic_plugin_" + uuid4().hex
        package = types.ModuleType(package_name)
        package.__path__ = [str(path.parent)]
        package.__package__ = package_name
        finder = _PluginFinder(package_name, plugin.root)
        sys.meta_path.insert(0, finder)
        sys.modules[package_name] = package
        module_name = package_name + ".entry"
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            # Compile current bytes directly: timestamp/size based pyc caches
            # can otherwise execute old code after a quick same-size refresh.
            exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
            target = getattr(module, function)
            if not callable(target):
                raise TypeError(f"{plugin.entrypoint} is not callable")
            result = target(arguments, ctx, copy.deepcopy(plugin_config))
            if inspect.isawaitable(result):
                result = await result
            return result
        finally:
            if finder in sys.meta_path:
                sys.meta_path.remove(finder)
            for key in list(sys.modules):
                if key == package_name or key.startswith(package_name + "."):
                    sys.modules.pop(key, None)

    def invoke(**arguments: Any) -> Any:
        return asyncio.run(execute(arguments))

    async def ainvoke(**arguments: Any) -> Any:
        return await execute(arguments)

    return _PluginStructuredTool(name=plugin.name, description=plugin.description,
                          args_schema=schema, func=invoke, coroutine=ainvoke)
