# web/runtime.py
"""Build the model and agent from the web frontend's current settings."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from langchain.agents import create_agent

from agents.agent import build_middleware
from agents.approval import ApprovalGate
from agents.compaction import PrefixSummarizer
from agents.react import ReActAgent
from agents.subagents.manager import LaunchContext, SubagentManager
from config.settings import settings
from config.store import ModelConfig
from prompts.system import SURFACE_WEB, build_system_prompt
from tools.context import SessionRef, ToolContext
from tools.images import vision_from_model
from tools.model_view import with_compact_descriptions
from tools.catalog import (
    BASIC_TOOLS,
    extended_tool_names,
    META_TOOLS,
    ToolActivation,
    enabled_tool_names,
    visible_tool_names,
)
from tools.registry import build_tools
from tools.sandbox.base import SandboxPolicy


from agents.model_factory import (
    API_MODE_ANTHROPIC,
    API_MODE_OPENAI,
    ANTHROPIC_PROVIDERS,
    PROVIDER_ANTHROPIC_BASE_URLS,
    PROVIDER_BASE_URLS,
    PROVIDER_OPENAI_BASE_URLS,
    create_chat_model,
    resolve_api_mode,
    resolve_base_url,
)


def tool_context(
    workspace: str,
    *,
    session_id: str = "default",
    asker=None,
    todo_store=None,
    process_manager=None,
    emit=None,
    vision=None,
    terminal: str = "powershell",
    activation: ToolActivation | None = None,
    enabled_names: frozenset[str] = frozenset(),
    cancel_event=None,
    sandbox=None,
    sandbox_policy: SandboxPolicy | None = None,
) -> ToolContext:
    """Convert the web frontend's workspace string into a tool context."""
    text = (workspace or "").strip()
    root = Path(text).expanduser().resolve() if text else Path.cwd().resolve()
    return ToolContext(
        workspace=root,
        max_output_chars=settings.max_tool_output_chars,
        command_timeout=float(settings.command_timeout),
        session=SessionRef(session_id or "default"),
        asker=asker,
        todo_store=todo_store,
        process_manager=process_manager,
        emit=emit,
        vision=vision,
        terminal=terminal,
        activation=activation,
        enabled_tool_names=enabled_names,
        cancel_event=cancel_event,
        sandbox=sandbox,
        sandbox_policy=sandbox_policy,
    )


def build_runtime(
    model_cfg: ModelConfig,
    enabled_tools: set[str],
    workspace: str,
    tool_configs: Mapping[str, Mapping[str, str]] | None = None,
    approval: ApprovalGate | None = None,
    *,
    asker=None,
    session_id: str = "default",
    todo_store=None,
    process_manager=None,
    emit=None,
    engine: str | None = None,
    request_text: str | None = None,
    terminal: str = "powershell",
    activation: ToolActivation | None = None,
    cancel_event=None,
    sandbox=None,
    sandbox_config: Mapping[str, Any] | None = None,
    subagents: SubagentManager | None = None,
    workspace_id: str = "",
    subagent_models: Mapping[str, str] | None = None,
):
    """Return ``(agent, summarizer)`` for one conversation turn.

    ``tool_configs`` maps entry names to parameter maps (for example QWeather
    credentials). Parameters are injected into tool instances for this turn
    instead of being written to ``os.environ``, so concurrent sessions do not
    interfere with each other.

    ``approval`` is the tool approval gate. On the web frontend,
    :class:`web.approvals.ApprovalBroker` supplies the function that blocks for
    a browser decision. ``asker`` is the question channel for
    ask_user_question, supplied on the web frontend by
    :class:`web.questions.QuestionBroker`.

    ``engine`` defaults to ``settings.agent_engine``: ``tool_calling`` uses
    LangChain ``create_agent`` native tool calling (the default), while
    ``react`` uses the native ReAct text loop (an optional compatibility mode
    with a strict single-step parser).

    ``activation`` is the caller's per-session activation state for extended
    tools. The agent assembles all enabled tools at once, and
    ``ToolVisibilityMiddleware`` filters the model schema according to the
    activation state.
    """
    model = create_chat_model(model_cfg)
    enabled_entries = set(enabled_tools)
    enabled_names = enabled_tool_names(enabled_entries)
    active = activation or ToolActivation()
    cfg = dict(sandbox_config or {})
    policy = None
    if sandbox is not None and workspace:
        policy = SandboxPolicy(
            workspace=Path(workspace).expanduser().resolve(),
            session_id=session_id,
            network=str(cfg.get("network") or "offline"),
            allowed_domains=tuple(cfg.get("allowed_domains") or ()),
            allowed_ports=tuple(int(item) for item in (cfg.get("allowed_ports") or (443, 80))),
            proxy_url=f"http://127.0.0.1:{cfg.get('proxy_port', 47831)}",
        )
    ctx = tool_context(
        workspace,
        session_id=session_id,
        asker=asker,
        todo_store=todo_store,
        process_manager=process_manager,
        emit=emit,
        vision=vision_from_model(model) if model_cfg.supports_images else None,
        terminal=terminal,
        activation=active,
        enabled_names=enabled_names,
        cancel_event=cancel_event,
        sandbox=sandbox,
        sandbox_policy=policy,
    )
    visible = visible_tool_names(enabled_names, active)
    resolved_engine = (engine or settings.agent_engine).strip().lower()
    if subagents is not None:
        service = subagents.bind(
            LaunchContext(
                session_id=session_id,
                workspace=ctx.workspace or Path.cwd(),
                workspace_id=workspace_id,
                model=model,
                model_cfg=model_cfg,
                model_factory=create_chat_model,
                builtin_model_overrides=dict(subagent_models or {}),
                enabled_tools=enabled_entries,
                visible_tools=set(visible),
                tool_configs=dict(tool_configs or {}),
                approval=approval,
                asker=asker,
                emit=emit,
                terminal=terminal,
                surface=SURFACE_WEB,
                engine=resolved_engine,
                activation=active,
                sandbox=sandbox,
                sandbox_policy=policy,
                sandbox_config=dict(cfg),
                cancel_event=cancel_event,
                vision=ctx.vision,
                todo_store=todo_store,
                process_manager=process_manager,
            )
        )
        ctx = replace(ctx, subagents=service)
    tools = build_tools(enabled_entries, tool_configs or {}, ctx)
    system_prompt = build_system_prompt(
        str(ctx.workspace) if ctx.workspace else "",
        tools=visible,
        terminal=terminal,
        surface=SURFACE_WEB,
        engine=resolved_engine,
    )

    if resolved_engine == "react":
        agent = ReActAgent(
            model,
            tools,
            system_prompt,
            approval=approval,
            activation=active,
            basic_names=BASIC_TOOLS,
            extended_names=extended_tool_names(),
            meta_names=META_TOOLS,
            max_model_calls=settings.max_model_calls,
            max_tool_calls=settings.max_tool_calls,
            model_max_retries=settings.model_max_retries,
            max_output_chars=settings.max_tool_output_chars,
        )
        summarizer = PrefixSummarizer(
            model=model,
            system_prompt=system_prompt,
            engine="react",
            max_tokens=settings.compact_summary_max_tokens or None,
        )
    else:
        from agents.tool_visibility import ToolVisibilityMiddleware

        visibility = ToolVisibilityMiddleware(
            activation=active,
            basic_names=set(BASIC_TOOLS) & enabled_names,
            extended_names=extended_tool_names() & enabled_names,
            meta_names=set(META_TOOLS) & enabled_names,
        )
        model_tools = with_compact_descriptions(tools)
        agent = create_agent(
            model,
            tools=model_tools,
            system_prompt=system_prompt,
            middleware=build_middleware(approval, visibility=visibility),
        )

        def _live_tools() -> list[Any]:
            """The tool schemas the last live request actually carried."""
            names = visible_tool_names(enabled_names, active)
            return [
                tool
                for tool in model_tools
                if str(getattr(tool, "name", "") or "") in names
            ]

        # 压缩用的模型、系统提示词与工具声明必须和真实请求完全一致，否则回放
        # 不再是同一段字节，提供方的前缀缓存也就无从复用。工具声明用回调读取，
        # 让工具可见集变化后仍然对齐最后一次真实请求。
        summarizer = PrefixSummarizer(
            model=model,
            system_prompt=system_prompt,
            tools=_live_tools,
            engine="tool_calling",
            max_tokens=settings.compact_summary_max_tokens or None,
        )
    return agent, summarizer
