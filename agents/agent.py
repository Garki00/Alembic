# agents/agent.py
"""Agent 组装：模型、工具，以及重试 / 调用上限等运行保护中间件。

工具清单不再硬编码在这里，而是从 :mod:`tools.registry` 派生，
网页与命令行因此始终使用同一套工具。
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
    EXTENDED_TOOLS,
    META_TOOLS,
    ToolActivation,
    enabled_tool_names,
    entry_names_for_tools,
    visible_tool_names,
)
from tools.context import SessionRef, ToolContext
from tools.images import vision_from_model
from tools.model_view import with_compact_descriptions
from tools.registry import build_tools, default_enabled_names

from agents.approval import (
    ApprovalGate,
    ApprovalPolicy,
    console_asker,
    permission_tools,
    tool_risks,
)
from agents.react import ReActAgent


def cli_tool_context(
    *,
    session_ref: SessionRef | None = None,
    asker=None,
    todo_store=None,
    process_manager=None,
) -> ToolContext:
    """命令行模式的工具上下文：工作区默认是当前目录。

    ``session_ref`` / ``asker`` / ``todo_store`` / ``process_manager`` 由
    main.py 注入；命令行切换会话时只改 ``session_ref.session_id``，
    已装配的工具无需重建。
    """
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
    )


def cli_enabled_tools() -> set[str]:
    """命令行启用的条目：注册表里默认启用的那些。"""
    return default_enabled_names()


def build_default_tools(ctx: ToolContext | None = None) -> list:
    """按命令行配置装配工具实例。"""
    return build_tools(cli_enabled_tools(), {}, ctx or cli_tool_context())


def build_cli_approval() -> ApprovalGate | None:
    """命令行的审批开关：终端提问，默认开启。

    ``APPROVAL_ENABLED=false`` 只关闭弹审批；不可恢复的高危命令仍由
    命令策略拒绝。
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
    """创建对话模型；同一个模型也用于生成历史对话摘要。"""
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
    """必要的运行保护：

    - 模型调用失败自动重试（网络抖动、限流）；
    - 单轮模型调用数上限，避免 Agent 陷入死循环；
    - 单轮工具调用数上限，超出的工具调用会被拦截而不是继续堆积上下文；
    - 扩展工具可见性过滤（基础 + 已激活扩展）；
    - 审批（放在最内层，先让调用上限与可见性拦截，再问人）。
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
):
    """组装 Agent；参数可注入，方便测试或替换模型。

    ``engine="tool_calling"``（默认）使用 LangChain ``create_agent`` 的模型原生
    工具调用；``engine="react"`` 走原生 ReAct 文本循环（严格单步解析，作为可选
    兼容模式）。传了 ``middleware`` 时按 tool_calling 处理。
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
        return ReActAgent(
            chat_model,
            tools,
            system_prompt,
            approval=approval,
            activation=activation if managed else None,
            basic_names=BASIC_TOOLS if managed else (),
            extended_names=EXTENDED_TOOLS if managed else (),
            meta_names=META_TOOLS if managed else (),
            max_model_calls=settings.max_model_calls,
            max_tool_calls=settings.max_tool_calls,
            model_max_retries=settings.model_max_retries,
            max_output_chars=settings.max_tool_output_chars,
        )

    visibility = None
    if managed:
        from agents.tool_visibility import ToolVisibilityMiddleware

        visibility = ToolVisibilityMiddleware(
            activation=activation,
            basic_names=set(BASIC_TOOLS) & enabled_names,
            extended_names=set(EXTENDED_TOOLS) & enabled_names,
            meta_names=set(META_TOOLS) & enabled_names,
        )
    if middleware is None:
        middleware = build_middleware(approval, visibility=visibility)
    model_tools = with_compact_descriptions(tools)
    return create_agent(
        chat_model,
        tools=model_tools,
        system_prompt=system_prompt,
        middleware=middleware,
    )
