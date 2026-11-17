# tools/mock.py
"""Mock tool: returns the text configured in settings unchanged.

Its purpose is to verify the tool-parameter pipeline itself: a field declared in the
registry produces an input box in the settings panel, and after saving, the tool can read
the value in the next conversation turn. Adding similar tools requires only Python; the
frontend needs no changes.
"""

from __future__ import annotations

from langchain.tools import tool

from tools.errors import tool_error


def build_mock_tools(config: dict) -> list:
    """Build ``mock_lookup`` from the injected configuration."""
    text = str((config or {}).get("MOCK_TEXT") or "")

    @tool
    def mock_lookup(query: str = "") -> str:
        """Return the text content configured in the "Test data" tool.

        Args:
            query: Optional note or keyword; only echoed back and does not affect the returned content.

        Returns:
            The configured text; returns a description starting with "Error:" when not configured yet.
        """
        if not text.strip():
            return tool_error(
                "missing_config",
                'The "Test data" tool has no content configured yet.',
                hint='Enter some text under Settings → Tools → Test data → Content, save it, and retry.',
            )
        body = f"Test data: {text}"
        keyword = (query or "").strip()
        if keyword:
            body += f"\nRequest parameter: {keyword}"
        return body

    return [mock_lookup]
