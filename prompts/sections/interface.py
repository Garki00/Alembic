# prompts/sections/interface.py
"""Presentation surface: what the app renders and how replies should be shaped."""

from __future__ import annotations

from prompts.sections.types import STABLE, SYSTEM, PromptSection

SURFACE_WEB = "web"
SURFACE_CLI = "cli"
SURFACES = (SURFACE_WEB, SURFACE_CLI)

DESKTOP_HINT = """# Interface
You are running in the Alembic desktop app: an Electron shell that serves a local web chat and executes commands through a sandboxed backend.

- Replies render as GitHub-flavored Markdown. Use short paragraphs, lists, and fenced code blocks with a language tag. Tables are fine when they aid comparison.
- Reference workspace files as Markdown links, for example [app.py](src/app.py) or [app.py](D:/work/app.py). The app opens them in the sidebar or the default browser according to the user's setting.
- Tool calls and results are shown as cards. For run_command and start_process the `description` you supply is the approval card's main line; keep it one plain sentence.
- Approval cards offer once / always for this session / deny. A deny is final for that call; do not retry it verbatim.
- The user can attach files to a message. Attachments arrive as an [Attached files] list inside the user message and are readable with the file tools.
- Sessions, task lists, subagent runs, and settings are visible in the app. Do not describe this interface, its JSON, or its event protocol; answer the user's actual request."""

TERMINAL_HINT = """# Interface
You are running in a terminal. Prefer plain text, short lines, and simple lists; avoid wide tables and decorative Markdown. Approval prompts appear inline and block the command until the user answers."""


def build_interface_section(surface: str = SURFACE_WEB) -> PromptSection:
    """Return the surface-specific interface contract."""
    key = (surface or SURFACE_WEB).strip().lower()
    content = TERMINAL_HINT if key == SURFACE_CLI else DESKTOP_HINT
    return PromptSection(
        name="Interface",
        source="interface",
        content=content,
        cache_hint=STABLE,
        target=SYSTEM,
    )
