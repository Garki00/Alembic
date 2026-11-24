# agents/agent.py
"""Agent assembly: model, tools, and runtime guards such as retries and call limits.

The tool list is not hard-coded here; it is derived from :mod:`tools.registry`,
so the web UI and the CLI always share one tool set.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
)
from langchain_deepseek import ChatDeepSeek

from config.settings import settings
from prompts.system import SURFACE_CLI, build_system_prompt
from tools.catalog import (
    BASIC_TOOLS,
    extended_tool_names,
    META_TOOLS,
    ToolActivation,
    enabled_tool_names,
    entry_names_for_tools,
    visible_tool_names,
)
from tools.context import SessionRef, ToolContext
from tools.images import vision_from_model
from tools.model_view import with_compact_descriptions
from tools.registry import build_tools

from agents.approval import (
    ApprovalGate,
    ApprovalPolicy,
    console_asker,
    permission_tools,
    tool_risks,
)
from agents.compaction import PrefixSummarizer
from agents.react import ReActAgent


def cli_tool_context(
    *,
    session_ref: SessionRef | None = None,
    asker=None,
    todo_store=None,
    process_manager=None,
    sandbox=None,
    sandbox_policy=None,
) -> ToolContext:
    """Tool context for CLI mode; the workspace defaults to the current directory."""
    workspace = Path(settings.workspace_dir or Path.cwd()).expanduser().resolve()
    return ToolContext(
        workspace=workspace,
        max_output_chars=settings.max_tool_output_chars,
        command_timeout=float(settings.command_timeout),
        terminal=settings.terminal_preference,
        session=session_ref or SessionRef(settings.session_id),
        asker=asker,
        todo_store=todo_store,
        process_manager=process_manager,
        sandbox=sandbox,
        sandbox_policy=sandbox_policy,
    )


def cli_enabled_tools() -> set[str]:
    """Load extensions before reading persisted CLI tool enable states."""
    from extensions.manager import get_default_manager
    from config.store import ToolStore

    get_default_manager()
    return ToolStore().enabled_tools()


def build_default_tools(ctx: ToolContext | None = None) -> list:
    """Build tool instances from the CLI configuration."""
    from config.store import ToolStore

    enabled = cli_enabled_tools()
    return build_tools(enabled, ToolStore().configs(), ctx or cli_tool_context())


def build_cli_approval() -> ApprovalGate | None:
    """CLI approval switch: asks on the terminal, enabled by default.

    ``APPROVAL_ENABLED=false`` only disables approval prompts; irreversible
    high-risk commands are still rejected by the command policy.
    """
    enabled = cli_enabled_tools()
    gated = permission_tools(enabled, "workspace_write") if settings.approval_enabled else {}
    return ApprovalGate(
        policy=ApprovalPolicy(
            gated,
            mode="workspace_write",
            risks=tool_risks(enabled),
        ),
        ask=console_asker,
    )


def build_model() -> ChatDeepSeek:
    """Create the chat model; the same model also summarizes earlier conversation."""
    if not settings.deepseek_api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY，请在 .env 中配置后再运行。")
    if not settings.model_name:
        raise RuntimeError("缺少 MODEL_NAME，请在 .env 中填写模型名称后再运行。")
    return ChatDeepSeek(
        model=settings.model_name,
        temperature=0,
        api_key=settings.deepseek_api_key,
    )


def build_middleware(
    approval: "ApprovalGate | None" = None,
    *,
    visibility=None,
) -> list[AgentMiddleware]:
    """Runtime guard middleware:

    - retry failed model calls automatically (network jitter, rate limits);
    - cap model calls per turn so the agent cannot loop forever;
    - cap tool calls per turn; excess calls are blocked instead of piling up in the context;
    - filter visibility of extended tools (basic tools plus activated extensions);
    - approval, as the innermost layer, so call limits and visibility block first and a human is asked last.
    """

    middleware: list[AgentMiddleware] = [
        ModelRetryMiddleware(max_retries=settings.model_max_retries),
        ModelCallLimitMiddleware(
            run_limit=settings.max_model_calls,
            exit_behavior="end",
        ),
        ToolCallLimitMiddleware(
            run_limit=settings.max_tool_calls,
            exit_behavior="continue",
        ),
    ]
    if visibility is not None:
        middleware.append(visibility)
    if approval is not None:
        from agents.approval import ApprovalMiddleware

        middleware.append(ApprovalMiddleware(approval))
    return middleware


def build_agent(
    *,
    model=None,
    tools=None,
    middleware=None,
    approval: ApprovalGate | None = None,
    ctx: ToolContext | None = None,
    engine: str | None = None,
    subagents=None,
):
    """Assemble the agent; dependencies are injectable for tests or model substitution.

    ``engine="tool_calling"`` (default) uses native tool calling from LangChain
    ``create_agent``; ``engine="react"`` runs the native ReAct text loop (strict
    single-step parsing, an optional compatibility mode). Passing ``middleware``
    is treated as tool_calling.
    """
    agent, _summarizer = assemble_agent(
        model=model,
        tools=tools,
        middleware=middleware,
        approval=approval,
        ctx=ctx,
        engine=engine,
        subagents=subagents,
    )
    return agent


def assemble_agent(
    *,
    model=None,
    tools=None,
    middleware=None,
    approval: ApprovalGate | None = None,
    ctx: ToolContext | None = None,
    engine: str | None = None,
    subagents=None,
):
    """Return ``(agent, summarizer)`` so compaction can replay the live prefix.

    The summarizer must see the same system prompt and the same tool declarations
    as the agent itself; otherwise its auxiliary request stops matching the
    conversation's cached prefix at the first differing byte.
    """
    resolved_engine = (engine or settings.agent_engine).strip().lower()
    chat_model = model or build_model()
    tool_ctx = ctx or cli_tool_context()
    if tool_ctx.vision is None:
        tool_ctx = replace(tool_ctx, vision=vision_from_model(chat_model))
    managed = tools is None
    enabled_entries = cli_enabled_tools() if managed else entry_names_for_tools(
        {str(getattr(tool, "name", "")) for tool in tools}
    )
    enabled_names = enabled_tool_names(enabled_entries)
    activation = tool_ctx.activation or ToolActivation()
    updates: dict = {}
    if tool_ctx.activation is None:
        updates["activation"] = activation
    if not tool_ctx.enabled_tool_names:
        updates["enabled_tool_names"] = enabled_names
    if updates:
        tool_ctx = replace(tool_ctx, **updates)
    visible = (
        visible_tool_names(enabled_names, activation)
        if managed
        else {str(getattr(tool, "name", "")) for tool in (tools or [])}
    )
    if subagents is not None and tool_ctx.subagents is None:
        from agents.model_factory import create_chat_model
        from agents.subagents.manager import LaunchContext

        tool_ctx = replace(
            tool_ctx,
            subagents=subagents.bind(
                LaunchContext(
                    session_id=tool_ctx.session_id,
                    workspace=tool_ctx.workspace or Path.cwd(),
                    model=chat_model,
                    model_factory=create_chat_model,
                    enabled_tools=set(enabled_entries),
                    visible_tools=set(visible),
                    tool_configs={},
                    approval=approval,
                    asker=tool_ctx.asker,
                    emit=tool_ctx.emit,
                    terminal=tool_ctx.terminal,
                    surface=SURFACE_CLI,
                    engine=resolved_engine,
                    activation=activation,
                    sandbox=tool_ctx.sandbox,
                    sandbox_policy=tool_ctx.sandbox_policy,
                    cancel_event=tool_ctx.cancel_event,
                    vision=tool_ctx.vision,
                    todo_store=tool_ctx.todo_store,
                    process_manager=tool_ctx.process_manager,
                )
            ),
        )
    if managed:
        tools = build_default_tools(tool_ctx)
    tool_names = (
        visible_tool_names(enabled_names, activation)
        if managed
        else {str(getattr(tool, "name", "")) for tool in tools}
    )
    effective_engine = "tool_calling" if middleware is not None else resolved_engine
    system_prompt = build_system_prompt(
        str(tool_ctx.workspace) if tool_ctx.workspace else "",
        tools=tool_names,
        terminal=tool_ctx.terminal,
        surface=SURFACE_CLI,
        engine=effective_engine,
    )

    if resolved_engine == "react" and middleware is None:
        agent = ReActAgent(
            chat_model,
            tools,
            system_prompt,
            approval=approval,
            activation=activation if managed else None,
            basic_names=BASIC_TOOLS if managed else (),
            extended_names=extended_tool_names() if managed else (),
            meta_names=META_TOOLS if managed else (),
            max_model_calls=settings.max_model_calls,
            max_tool_calls=settings.max_tool_calls,
            model_max_retries=settings.model_max_retries,
            max_output_chars=settings.max_tool_output_chars,
        )
        summarizer = PrefixSummarizer(
            model=chat_model,
            system_prompt=system_prompt,
            engine="react",
            max_tokens=settings.compact_summary_max_tokens or None,
        )
        return agent, summarizer

    visibility = None
    if managed:
        from agents.tool_visibility import ToolVisibilityMiddleware

        visibility = ToolVisibilityMiddleware(
            activation=activation,
            basic_names=set(BASIC_TOOLS) & enabled_names,
            extended_names=extended_tool_names() & enabled_names,
            meta_names=set(META_TOOLS) & enabled_names,
        )
    if middleware is None:
        middleware = build_middleware(approval, visibility=visibility)
    model_tools = with_compact_descriptions(tools)
    agent = create_agent(
        chat_model,
        tools=model_tools,
        system_prompt=system_prompt,
        middleware=middleware,
    )
    summarizer = PrefixSummarizer(
        model=chat_model,
        system_prompt=system_prompt,
        tools=model_tools if not managed else (
            lambda: [
                tool
                for tool in model_tools
                if str(getattr(tool, "name", "") or "")
                in visible_tool_names(enabled_names, activation)
            ]
        ),
        engine="tool_calling",
        max_tokens=settings.compact_summary_max_tokens or None,
    )
    return agent, summarizer
