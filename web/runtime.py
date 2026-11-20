# web/runtime.py
"""Build the model and agent from the web frontend's current settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from langchain.agents import create_agent
from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from agents.agent import build_middleware
from agents.anthropic_messages import ChatAnthropicMessages
from agents.approval import ApprovalGate
from agents.compaction import PrefixSummarizer
from agents.react import ReActAgent
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


#: Default base URL per provider for the OpenAI-compatible protocol.
PROVIDER_BASE_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "claude": "https://api.anthropic.com",
    "anthropic": "https://api.anthropic.com",
    "xai": "https://api.x.ai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "moonshot": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
    "minimax": "https://api.minimax.chat/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
}

#: Providers that also publish an Anthropic-compatible endpoint.
PROVIDER_ANTHROPIC_BASE_URLS: dict[str, str] = {
    "claude": "https://api.anthropic.com",
    "anthropic": "https://api.anthropic.com",
    "deepseek": "https://api.deepseek.com/anthropic",
    "moonshot": "https://api.moonshot.cn/anthropic",
    "zhipu": "https://open.bigmodel.cn/api/anthropic",
    "minimax": "https://api.minimax.chat/anthropic",
}

#: Providers whose OpenAI-compatible endpoint lives at a different path than
#: their native default.
PROVIDER_OPENAI_BASE_URLS: dict[str, str] = {
    "claude": "https://api.anthropic.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
}

#: Provider ids whose default protocol is the native Anthropic Messages API.
ANTHROPIC_PROVIDERS = {"claude", "anthropic"}

API_MODE_OPENAI = "openai"
API_MODE_ANTHROPIC = "anthropic"


def resolve_api_mode(provider: str, api_mode: str | None) -> str:
    """Resolve the wire protocol: explicit setting first, provider default second."""
    mode = str(api_mode or "").strip().lower()
    if mode in {API_MODE_OPENAI, API_MODE_ANTHROPIC}:
        return mode
    return API_MODE_ANTHROPIC if provider in ANTHROPIC_PROVIDERS else API_MODE_OPENAI


def resolve_base_url(provider: str, api_mode: str, base_url: str) -> str:
    """Return the base URL that matches the selected protocol.

    A stored URL that equals the provider's default for the *other* protocol is
    treated as "not customized yet" and swapped; anything else is kept as-is so
    custom and proxy endpoints are never rewritten.
    """
    current = (base_url or "").strip().rstrip("/")
    if api_mode == API_MODE_ANTHROPIC:
        target = PROVIDER_ANTHROPIC_BASE_URLS.get(provider, "")
        other = PROVIDER_BASE_URLS.get(provider, "").rstrip("/")
        if target and (not current or current == other):
            return target
        return current or target
    target = PROVIDER_OPENAI_BASE_URLS.get(provider) or PROVIDER_BASE_URLS.get(provider, "")
    anthopic_default = PROVIDER_ANTHROPIC_BASE_URLS.get(provider, "").rstrip("/")
    if target and (not current or (anthopic_default and current == anthopic_default)):
        return target
    return current or target


def create_chat_model(model_cfg: ModelConfig):
    """Create a chat model from its configuration.

    ``provider`` selects the default base URL; the provider's ``api_mode``
    selects the wire protocol (native Anthropic Messages API or an
    OpenAI-compatible endpoint) and falls back to the provider default. The
    cache policy is later resolved from the concrete model id, not from this
    provider label.

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
    api_mode = resolve_api_mode(provider, getattr(model_cfg, "api_mode", ""))
    base_url = resolve_base_url(provider, api_mode, model_cfg.base_url or "")
    if not base_url:
        raise RuntimeError("自定义供应商需要填写 Base URL（例如 https://api.example.com/v1）。")

    if api_mode == API_MODE_ANTHROPIC:
        return ChatAnthropicMessages(
            model_name=model_cfg.model,
            api_key=model_cfg.api_key,
            base_url=base_url,
            temperature=model_cfg.temperature,
            max_tokens=int(model_cfg.max_output_tokens or 8192),
        )

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

    if provider == "deepseek" and api_mode == API_MODE_OPENAI:
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
