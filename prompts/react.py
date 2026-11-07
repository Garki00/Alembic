# prompts/react.py
"""English ReAct prompt: tool catalogue, format contract and scratchpad."""

from __future__ import annotations

import json
from typing import Iterable, Sequence

REACT_SYSTEM_PROMPT = """You are a ReAct agent operating inside a local workspace.

You solve tasks in explicit steps:
1. Think about what you know and what you still need.
2. Call at most one tool when an action is required.
3. Read the observation, then decide the next step.
4. Stop with a final answer when the task is complete.

Rules:
- Never invent an observation. Only the tool result is an observation.
- Never claim you used a tool that was not actually called.
- If a tool returns an error, read the error code and adjust your plan.
- Do not repeat the same failing call unchanged.
- Keep thoughts short and operational; they are shown to the user as progress.
  Never put secrets, API keys or private file contents in a Thought.
- The final answer must be self-contained and written in the user's language.
"""

REACT_PROMPT_TEMPLATE = """You have access to these tools:

{tools}

Use exactly this format. Each assistant message contains ONE step only.

When a tool is needed, output exactly these three lines and then STOP:

Thought: brief reasoning about the next step
Action: one of [{tool_names}]
Action Input: a JSON object with the tool arguments

After you stop, the system appends the Observation and calls you again.
Continue from the Scratchpad below.

When the task is complete, output:

Thought: I now know the final answer
Final Answer: the complete answer to the user

Rules:
- Exactly one step per message: either one Action (with its Action Input) or one Final Answer.
- Never write an Observation line yourself. Only the system appends Observations.
- Never put an Action and a Final Answer in the same message: run the tool, read the
  Observation, and only then answer.
- Do not repeat earlier steps or observations.
- Action must be exactly one of the tool names above.
- Action Input must be a valid JSON object. Use {{}} when the tool takes no arguments.
- If no tool is needed, write the Final Answer directly without an Action.
- Do not include the words "Action" or "Action Input" inside the Final Answer.
- Answer in the same language as the user's latest message.

Conversation so far:
{history}

Latest user request:
{question}

Scratchpad (previous ReAct steps in this turn):
{scratchpad}
"""

# English summaries used in the ReAct prompt. The tool docstrings stay in the
# project's working language (Chinese) for UI/debugging; these summaries keep the
# model-facing prompt fully English. Keep this map in sync when adding tools.
TOOL_SUMMARIES = {
    "get_current_time": "Get the current time in local time or a named timezone/city.",
    "list_timezones": "List supported regions and IANA timezones, optionally filtered by keyword.",
    "get_weather": "Get current weather for a city (requires QWeather credentials).",
    "list_dir": "List files and subdirectories inside the workspace.",
    "read_file": "Read a UTF-8 text file with line numbers; supports offset/limit.",
    "search_files": "Search file contents with a regular expression, optionally filtered by glob.",
    "file_stat": "Show file/directory metadata: type, size, mtime and text line count.",
    "read_document": "Extract text from PDF, Word, Excel, PowerPoint or plain-text files; supports offset/limit.",
    "write_file": "Write a complete file (creates parent directories). Read an existing file first.",
    "edit_file": "Replace an exact string in a file; prefer this for targeted edits.",
    "make_dir": "Create a directory, including missing parents.",
    "delete_file": "Delete a file or directory. Non-empty directories require recursive=true.",
    "move_file": "Move or rename a file/directory inside the workspace.",
    "copy_file": "Copy a file or directory inside the workspace.",
    "fetch_url": "Fetch a web page or HTTP endpoint and return readable text (private hosts blocked by default).",
    "todo_write": "Replace the session task list. Each item is {content: string, status: pending|in_progress|completed}.",
    "ask_user_question": "Ask the user for missing information or a choice (options is a list of strings), then wait for the answer.",
    "run_command": "Run a shell command in the workspace and return exit code plus stdout/stderr.",
    "start_process": "Start a long-running background command (dev server, watcher) and return its process id.",
    "stop_process": "Stop a background process started by this app, including its child processes.",
    "list_processes": "List background processes started by this app and show their status.",
    "read_process_logs": "Read the tail of a background process log.",
    "mock_lookup": "Return the configured mock text (testing tool).",
}


def format_tools(tools: Sequence) -> str:
    """Render tool names, English summaries and JSON argument schemas."""
    blocks: list[str] = []
    for tool in tools:
        name = getattr(tool, "name", "") or "tool"
        description = TOOL_SUMMARIES.get(name, "")
        if not description:
            raw = (getattr(tool, "description", "") or "").strip()
            description = raw.splitlines()[0] if raw else ""
        args = getattr(tool, "args", None) or {}
        try:
            schema = json.dumps(args, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            schema = "{}"
        blocks.append(f"- {name}: {description}\n  Arguments schema: {schema}")
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
    """Build the human-side ReAct prompt for one model call."""
    return REACT_PROMPT_TEMPLATE.format(
        tools=format_tools(tools),
        tool_names=tool_names(tools),
        history=history or "(new conversation)",
        question=question or "(no new question)",
        scratchpad=scratchpad.strip() or "(empty; start with a Thought)",
    )
