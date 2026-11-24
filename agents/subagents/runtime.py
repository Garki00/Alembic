# agents/subagents/runtime.py
"""Build and drive one child agent.

The child runs the same engine as its parent (native tool calling by default, the
text ReAct loop when configured), with its own session history, reduced tool
surface, and tighter call limits. Compaction uses the same prefix-replay
summarizer so a child turn keeps provider cache behaviour intact.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Mapping, Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
)

from agents.approval import ApprovalGate, ApprovalMiddleware
from agents.compaction import PrefixSummarizer
from agents.context import ChatSession, SessionStore
from agents.react import ReActAgent
from config.settings import settings
from prompts.system import build_system_prompt
from tools.model_view import with_compact_descriptions
from tools.registry import build_tools

logger = logging.getLogger(__name__)


class SubagentBuildError(RuntimeError):
    """Raised when a child agent cannot be assembled."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def resolve_model_config(parent: Any, name: str | None):
    """Resolve a profile model name against the stored providers.

    A profile may name a model id or display name. When it cannot be resolved the
    parent model is used, so a stale profile never breaks delegation.
    """
    if not name:
        return parent
    wanted = str(name).strip()
    if not wanted:
        return parent
    try:
        from config.store import ModelStore

        store = ModelStore()
    except Exception:  # pragma: no cover - storage issues fall back to the parent
        return parent
    target = wanted.lower()
    for provider in store.providers():
        for model in provider.models:
            if target in {model.id.lower(), (model.name or "").lower(), model.model.lower()}:
                if (
                    model.id == store.active_model_id()
                    and provider.id == store.active_provider_id()
                ):
                    return store.active() or parent
                return _config_from_provider(provider, model)
    return parent


def _config_from_provider(provider: Any, model: Any):
    """Build a ModelConfig-shaped value for one provider model."""
    from config.store import ModelConfig

    return ModelConfig(
        id=model.id,
        name=model.name or model.model,
        provider=provider.provider or "custom",
        provider_id=provider.id,
        provider_name=provider.name,
        model=model.model,
        base_url=provider.base_url,
        api_key=provider.api_key,
        api_mode=provider.api_mode,
        temperature=model.temperature,
        thinking=model.thinking,
        thinking_levels=list(model.thinking_levels),
        supports_reasoning=model.supports_reasoning,
        supports_images=model.supports_images,
        context_length=model.context_length,
        max_output_tokens=model.max_output_tokens,
    )


def build_child_agent(
    *,
    model: Any,
    tools: Sequence[Any],
    system_prompt: str,
    approval: ApprovalGate | None,
    max_model_calls: int | None = None,
    max_tool_calls: int | None = None,
    engine: str | None = None,
):
    """Assemble the child agent and its compaction summarizer.

    Args:
        model: Chat model instance shared with the child session.
        tools: Pre-filtered tool instances for the child.
        system_prompt: Fully composed child system prompt.
        approval: Approval gate; the caller supplies a child-scoped dispatcher.
        max_model_calls: Per-run model call budget.
        max_tool_calls: Per-run tool call budget.
        engine: ``tool_calling`` or ``react``; defaults to the runtime setting.

    Returns:
        ``(agent, summarizer)`` compatible with :class:`ChatSession`.
    """
    resolved_engine = (engine or settings.agent_engine).strip().lower()
    model_tools = with_compact_descriptions(list(tools))
    model_limit = int(max_model_calls or settings.subagent_max_model_calls)
    tool_limit = int(max_tool_calls or settings.subagent_max_tool_calls)

    if resolved_engine == "react":
        agent = ReActAgent(
            model,
            list(tools),
            system_prompt,
            approval=approval,
            max_model_calls=model_limit,
            max_tool_calls=tool_limit,
            model_max_retries=settings.model_max_retries,
            max_output_chars=settings.max_tool_output_chars,
        )
        summarizer = PrefixSummarizer(
            model=model,
            system_prompt=system_prompt,
            engine="react",
            max_tokens=settings.compact_summary_max_tokens or None,
        )
        return agent, summarizer

    middleware = [
        ModelRetryMiddleware(max_retries=settings.model_max_retries),
        ModelCallLimitMiddleware(run_limit=model_limit, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=tool_limit, exit_behavior="continue"),
    ]
    if approval is not None:
        middleware.append(ApprovalMiddleware(approval))
    agent = create_agent(
        model,
        tools=model_tools,
        system_prompt=system_prompt,
        middleware=middleware,
    )
    summarizer = PrefixSummarizer(
        model=model,
        system_prompt=system_prompt,
        tools=lambda: model_tools,
        engine="tool_calling",
        max_tokens=settings.compact_summary_max_tokens or None,
    )
    return agent, summarizer


def build_child_tools(
    *,
    enabled_entries: set[str],
    tool_configs: Mapping[str, Mapping[str, str]],
    tool_names: Sequence[str],
    tool_ctx: Any,
) -> list[Any]:
    """Build only the tools the child surface allows."""
    from tools.registry import TOOL_SPECS

    allowed = set(tool_names)
    entries = {
        entry
        for entry in enabled_entries
        if allowed & set(TOOL_SPECS[entry].tool_names)
    }
    if not entries:
        return []
    return build_tools(entries, tool_configs, tool_ctx)


def build_child_system_prompt(
    *,
    profile_prompt: str,
    workspace: str,
    tool_names: Sequence[str],
    terminal: str,
    surface: str,
    engine: str,
    project_context: str | None,
) -> str:
    """Compose the child prompt from the shared base prompt and the profile."""
    from agents.subagents.prompts import build_subagent_system_prompt

    base = build_system_prompt(
        workspace,
        tools=set(tool_names),
        terminal=terminal,
        surface=surface,
        engine=engine,
        project_context=project_context,
    )
    return build_subagent_system_prompt(profile_prompt=profile_prompt, base_prompt=base)


def build_child_session(
    *,
    agent: Any,
    summarizer: Any,
    child_session_id: str,
    store_dir: str | Path,
    workspace_id: str,
    workspace_path: str,
    model_cfg: Any,
) -> ChatSession:
    """Create the persisted child session under the subagent store."""
    return ChatSession(
        agent,
        child_session_id,
        store=SessionStore(store_dir),
        summarizer=summarizer,
        workspace_id=workspace_id,
        workspace_path=workspace_path,
        model_context_window=getattr(model_cfg, "context_length", 0) or None,
        max_output_tokens=getattr(model_cfg, "max_output_tokens", 0) or None,
        generate_title=False,
        hydrate=False,
    )
