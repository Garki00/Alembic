"""Build compact model-facing copies of tools without changing UI metadata."""

from __future__ import annotations

from typing import Any, Iterable

from prompts.tool_descriptions import TOOL_MODEL_DESCRIPTIONS


def with_compact_descriptions(tools: Iterable[Any]) -> list[Any]:
    compacted: list[Any] = []
    for tool in tools:
        name = str(getattr(tool, "name", "") or "")
        description = TOOL_MODEL_DESCRIPTIONS.get(name)
        if not description:
            compacted.append(tool)
            continue
        try:
            compacted.append(tool.model_copy(update={"description": description}))
        except Exception:
            compacted.append(tool)
    return compacted
