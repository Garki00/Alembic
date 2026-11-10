"""Compact English ReAct prompt and model-facing tool catalogue."""

from __future__ import annotations

import json
from typing import Iterable, Sequence

from prompts.tool_descriptions import TOOL_MODEL_DESCRIPTIONS

REACT_SYSTEM_PROMPT = """Use ReAct one tool step at a time.
Never invent observations or results. Adjust after errors.
Keep thoughts brief and free of secrets. Reply in the user's language."""

REACT_PROMPT_TEMPLATE = """Tools:
{tools}

Reply with exactly one step.

For a tool action:
Thought: short reason
Action: one listed tool name
Action Input: a JSON object

Stop after Action Input and wait for the system Observation.

For a final answer:
Thought: done
Final Answer: complete answer

Rules:
- Never write Observation; only the system appends it.
- Never combine Action and Final Answer.
- Use {{}} for a tool with no arguments.
- Do not mention Action fields in Final Answer.

History:
{history}

Request:
{question}

Scratchpad:
{scratchpad}
"""

TOOL_SUMMARIES = TOOL_MODEL_DESCRIPTIONS


def format_tools(tools: Sequence) -> str:
    """Render compact tool names, schemas, and English descriptions."""
    blocks: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", "") or "tool"
        description = TOOL_MODEL_DESCRIPTIONS.get(name, "")
        if not description:
            raw = (getattr(tool, "description", "") or "").strip()
            description = raw.splitlines()[0] if raw else ""
        args = getattr(tool, "args", None) or {}
        try:
            schema = json.dumps(
                args,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            schema = "{}"
        blocks.append(f"- {name} {schema}: {description}")
    return "\n".join(blocks) if blocks else "(no tools available)"


def tool_names(tools: Iterable) -> str:
    names = [getattr(tool, "name", "") for tool in tools]
    return ", ".join(name for name in names if name)


def build_react_prompt(
    *,
    tools: Sequence,
    history: str,
    question: str,
    scratchpad: str,
) -> str:
    """Build the dynamic human-side ReAct prompt for one model call."""
    return REACT_PROMPT_TEMPLATE.format(
        tools=format_tools(tools),
        history=history or "(new conversation)",
        question=question or "(no new question)",
        scratchpad=scratchpad.strip() or "(start)",
    )
