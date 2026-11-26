"""Compact English ReAct prompt and model-facing tool catalogue."""

from __future__ import annotations

import json
from typing import Iterable, Sequence

REACT_SYSTEM_PROMPT = """Use ReAct one tool step at a time.
Never invent observations or results. Adjust after errors.
Keep thoughts brief and free of secrets. Reply in the user's language."""

REACT_PROMPT_TEMPLATE = """Tools:
{tools}

Deferred tools (schemas not loaded):
{deferred}

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
- A deferred tool is not callable by its own name. Call tools_search first, then
  call deferred_tool_call with {{"name": "<tool>", "input": {{...}}}}.

History:
{history}

Request:
{question}

Scratchpad:
{scratchpad}
"""


def format_tools(tools: Sequence) -> str:
    """Render tool names, JSON schemas, and the descriptions their modules declare."""
    blocks: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", "") or "tool"
        description = (getattr(tool, "description", "") or "").strip()
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
    deferred: Iterable[str] = (),
) -> str:
    """Build the dynamic human-side ReAct prompt for one model call."""
    deferred_names = sorted(name for name in deferred if name)
    return REACT_PROMPT_TEMPLATE.format(
        tools=format_tools(tools),
        deferred="\n".join(f"- {name}" for name in deferred_names) or "(none)",
        history=history or "(new conversation)",
        question=question or "(no new question)",
        scratchpad=scratchpad.strip() or "(start)",
    )
