# agents/subagents/runtime.py
"""Build and drive one child agent.

The child runs the same engine as its parent (native tool calling by default, the
text ReAct loop when configured), with its own session history, reduced tool
surface, and tighter call limits. Compaction uses the same prefix-replay
summarizer so a child turn keeps provider cache behaviour intact.
"""

from __future__ import annotations

import logging
from dataclasses import replace
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
from agents.subagents.admission import ModelAdmissionMiddleware, ModelRequestAdmission
from config.settings import settings
from prompts.builder import build_system_prompt
from tools.catalog import (
    TOOL_SEARCH_NAME,
    ToolActivation,
    build_contracts,
    resolve_exposure,
    turn_disallowed_tools,
)
from tools.registry import build_tools

logger = logging.getLogger(__name__)


class SubagentBuildError(RuntimeError):
    """Raised when a child agent cannot be assembled."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def resolve_model_config(parent: Any, name: str | None, store: Any = None):
    """Resolve a profile model name against the stored providers.

    An explicit name must resolve. Falling back to the parent model would run the
    task on a different model than the profile promised, so a missing model raises
    ``SubagentBuildError`` with the configured identifiers instead.

    Args:
        parent: Configuration inherited when the profile names no model.
        name: Model id, display name, or upstream model id from the profile.
        store: Optional ModelStore; callers pass the app's store when it differs
            from the process default.
    """
    if not name:
        return parent
    wanted = str(name).strip()
    if not wanted:
        return parent
    try:
        if store is None:
            from config.store import ModelStore

            store = ModelStore()
    except Exception as exc:  # storage failure must not silently change the model
        raise SubagentBuildError(
            "subagent_model_unavailable",
            f"Cannot read model configuration for subagent model {wanted!r}: {exc}",
        ) from exc
    target = wanted.lower()
    provider_filter = ""
    model_target = target
    if "/" in target:
        provider_part, _, rest = target.partition("/")
        if provider_part and rest:
            provider_filter = provider_part
            model_target = rest
    available: list[str] = []
    for provider in store.providers():
        if provider_filter and provider.id.lower() != provider_filter:
            continue
        for model in provider.models:
            label = f"{provider.id}/{model.model}"
            available.append(label)
            if model_target in {
                model.id.lower(),
                (model.name or "").lower(),
                model.model.lower(),
            }:
                if (
                    model.id == store.active_model_id()
                    and provider.id == store.active_provider_id()
                ):
                    active = store.active()
                    if active is None:
                        raise SubagentBuildError(
                            "subagent_model_unavailable",
                            f"Model {wanted!r} is the active model but has no usable configuration.",
                        )
                    return active
                return _config_from_provider(provider, model)
    listing = ", ".join(sorted(set(available))[:10]) or "(none configured)"
    raise SubagentBuildError(
        "subagent_model_unavailable",
        f"Subagent model {wanted!r} is not configured. Available: {listing}.",
    )


def apply_profile_thinking(cfg: Any, thinking: str | None):
    """Return a configuration with the profile reasoning level applied.

    ``none`` is always accepted. Other levels must be declared by the model and
    require reasoning support; otherwise the profile is rejected instead of
    silently running at a different level.
    """
    if not thinking:
        return cfg
    level = str(thinking).strip().lower()
    if level == "none":
        return replace(cfg, thinking="none") if cfg is not None else cfg
    if cfg is None:
        raise SubagentBuildError(
            "subagent_thinking_unavailable",
            f"Profile reasoning level {level!r} requires a resolvable model configuration.",
        )
    levels = {str(item).lower() for item in (getattr(cfg, "thinking_levels", None) or [])}
    if level not in levels:
        supported = ", ".join(sorted(levels | {"none"}))
        raise SubagentBuildError(
            "subagent_thinking_unavailable",
            f"Reasoning level {level!r} is not supported by model "
            f"{getattr(cfg, 'model', '')!r}; supported: {supported}.",
        )
    if not getattr(cfg, "supports_reasoning", False):
        raise SubagentBuildError(
            "subagent_thinking_unavailable",
            f"Model {getattr(cfg, 'model', '')!r} does not support reasoning levels.",
        )
    return replace(cfg, thinking=level)


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
    admission: ModelRequestAdmission | None = None,
    admission_cancel: Any = None,
    contracts: Sequence[Any] = (),
    exposure: Any = None,
    activation: Any = None,
    turn_disallowed: Sequence[str] = (),
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
        admission: Optional per-parent-session model request gate.
        admission_cancel: Cancellation event observed while waiting for a slot.
        contracts: Model-facing tool contracts for the child tool set.
        exposure: Pre-resolved exposure for the child request.
        activation: Child discovery state for deferred tools.
        turn_disallowed: Tools that cannot run in the child runtime.

    Returns:
        ``(agent, summarizer)`` compatible with :class:`ChatSession`.
    """
    resolved_engine = (engine or settings.agent_engine).strip().lower()
    model_tools = list(tools)
    model_limit = int(max_model_calls or settings.subagent_max_model_calls)
    tool_limit = int(max_tool_calls or settings.subagent_max_tool_calls)
    if exposure is None:
        exposure = resolve_exposure(
            contracts or build_contracts(model_tools),
            activation,
            turn_disallowed=turn_disallowed,
            search_enabled=any(
                str(getattr(tool, "name", "") or "") == TOOL_SEARCH_NAME for tool in model_tools
            ),
        )

    if resolved_engine == "react":
        agent = ReActAgent(
            model,
            model_tools,
            system_prompt,
            approval=approval,
            activation=activation,
            basic_names=[name for name in exposure.visible if name not in exposure.controls],
            extended_names=sorted(exposure.deferred),
            meta_names=sorted(exposure.controls),
            max_model_calls=model_limit,
            max_tool_calls=tool_limit,
            model_max_retries=settings.model_max_retries,
            max_output_chars=settings.max_tool_output_chars,
            admission=admission,
            admission_cancel=admission_cancel,
        )
        summarizer = PrefixSummarizer(
            model=model,
            system_prompt=system_prompt,
            engine="react",
            max_tokens=settings.compact_summary_max_tokens or None,
        )
        return agent, summarizer

    from agents.tool_exposure import ToolExposureMiddleware

    middleware = [ModelRetryMiddleware(max_retries=settings.model_max_retries)]
    if admission is not None:
        middleware.append(ModelAdmissionMiddleware(admission, admission_cancel))
    middleware.extend(
        [
            ModelCallLimitMiddleware(run_limit=model_limit, exit_behavior="end"),
            ToolCallLimitMiddleware(run_limit=tool_limit, exit_behavior="continue"),
            ToolExposureMiddleware(
                contracts=contracts or build_contracts(model_tools),
                exposure=exposure,
                activation=activation,
                tools=model_tools,
                turn_disallowed=turn_disallowed,
            ),
        ]
    )
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
        tools=lambda: [
            tool
            for tool in model_tools
            if str(getattr(tool, "name", "") or "") in exposure.visible
        ],
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
    deferred_tools: Sequence[str] = (),
    model: str = "",
) -> str:
    """Compose the child prompt from the shared base prompt and the profile."""
    from agents.subagents.prompts import build_subagent_system_prompt

    base = build_system_prompt(
        workspace,
        tools=set(tool_names),
        deferred_tools=deferred_tools,
        terminal=terminal,
        surface=surface,
        engine=engine,
        model=model,
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
