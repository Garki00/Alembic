# web/runtime.py
"""Build the model and agent from the web frontend's current settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from langchain.agents import create_agent
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from agents.agent import build_middleware
from agents.approval import ApprovalGate
from agents.react import ReActAgent
from config.settings import settings
from config.store import ModelConfig
from prompts.system import SURFACE_WEB, build_system_prompt
from tools.context import SessionRef, ToolContext
from tools.images import vision_from_model
from tools.model_view import with_compact_descriptions
from tools.catalog import (
    BASIC_TOOLS,
    EXTENDED_TOOLS,
    META_TOOLS,
    ToolActivation,
    enabled_tool_names,
    visible_tool_names,
)
from tools.registry import build_tools
from tools.sandbox.base import SandboxPolicy


#: 各供应商的默认 Base URL。所有供应商都走 OpenAI 兼容的 /chat/completions；
#: Claude 使用 Anthropic 提供的 OpenAI 兼容端点（/v1）。
PROVIDER_BASE_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "claude": "https://api.anthropic.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
}


def create_chat_model(model_cfg: ModelConfig):
    """Create a chat model from its configuration.

    ``provider`` selects the default base URL. All providers use an
    OpenAI-compatible interface: DeepSeek uses :class:`ChatDeepSeek`; every
    other provider, including Claude through Anthropic's compatible endpoint,
    uses :class:`ChatOpenAI`.

    Args:
        model_cfg: Model configuration with credentials and endpoint details.

    Returns:
        A chat model instance for the configured provider.

    Raises:
        RuntimeError: If the API key, model ID, or base URL is missing.
    """
    if not model_cfg.api_key:
        raise RuntimeError(f"模型「{model_cfg.name}」还没有配置 API Key，请在设置里填写。")
    if not model_cfg.model:
        raise RuntimeError(f"模型「{model_cfg.name}」还没有填写模型 ID。")

    provider = (model_cfg.provider or "custom").strip().lower()
    base_url = (model_cfg.base_url or "").strip() or PROVIDER_BASE_URLS.get(provider, "")
    if not base_url:
        raise RuntimeError("自定义供应商需要填写 Base URL（例如 https://api.example.com/v1）。")

    kwargs: dict[str, Any] = {
        "model": model_cfg.model,
        "api_key": model_cfg.api_key,
        "temperature": model_cfg.temperature,
        "base_url": base_url,
    }
    if model_cfg.max_output_tokens:
        kwargs["max_tokens"] = int(model_cfg.max_output_tokens)
    thinking = (model_cfg.thinking or "none").strip().lower()
    if model_cfg.supports_reasoning and thinking in {"low", "high", "max"}:
        # 供应商若不支持该参数会自行忽略或报错；none 表示不显式指定。
        kwargs["reasoning_effort"] = thinking

    if provider == "deepseek":
        return ChatDeepSeek(**kwargs)
    return ChatOpenAI(**kwargs)


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
    tools = build_tools(enabled_entries, tool_configs or {}, ctx)
    resolved_engine = (engine or settings.agent_engine).strip().lower()
    visible = visible_tool_names(enabled_names, active)
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
            extended_names=EXTENDED_TOOLS,
            meta_names=META_TOOLS,
            max_model_calls=settings.max_model_calls,
            max_tool_calls=settings.max_tool_calls,
            model_max_retries=settings.model_max_retries,
            max_output_chars=settings.max_tool_output_chars,
        )
    else:
        from agents.tool_visibility import ToolVisibilityMiddleware

        visibility = ToolVisibilityMiddleware(
            activation=active,
            basic_names=set(BASIC_TOOLS) & enabled_names,
            extended_names=set(EXTENDED_TOOLS) & enabled_names,
            meta_names=set(META_TOOLS) & enabled_names,
        )
        model_tools = with_compact_descriptions(tools)
        agent = create_agent(
            model,
            tools=model_tools,
            system_prompt=system_prompt,
            middleware=build_middleware(approval, visibility=visibility),
        )
    # 同一个模型实例也用于生成上下文摘要
    return agent, model
