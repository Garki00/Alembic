# web/runtime.py
"""按网页端的当前设置组装模型与 Agent。"""

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
from prompts.system import build_system_prompt
from tools.context import SessionRef, ToolContext
from tools.images import vision_from_model
from tools.model_view import with_compact_descriptions
from tools.registry import build_tools
from tools.selection import select_tool_entries


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
    """根据模型配置创建聊天模型。

    ``provider`` 决定默认 Base URL；除 DeepSeek 走 :class:`ChatDeepSeek` 外，
    其余供应商都使用 :class:`ChatOpenAI` 的 OpenAI 兼容接口（Claude 走
    Anthropic 的兼容端点）。
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
    thinking = (model_cfg.thinking or "none").strip().lower()
    if thinking in {"low", "high", "max"}:
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
) -> ToolContext:
    """把网页端的工作区字符串变成工具上下文。"""
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
):
    """返回 ``(agent, summarizer)``，供一轮对话使用。

    ``tool_configs`` 是条目名 → 参数表（如 QWeather 凭据）。参数按这一轮
    注入到工具实例里，不再写进 ``os.environ``，因此并发会话不会互相污染。

    ``approval`` 为工具审批开关；网页端由 :class:`web.approvals.ApprovalBroker`
    提供「阻塞等待浏览器回执」的提问函数。``asker`` 是 ask_user_question 的
    提问通道（网页端由 :class:`web.questions.QuestionBroker` 提供）。

    ``engine`` 默认取 ``settings.agent_engine``：``tool_calling`` 走 LangChain
    ``create_agent`` 的模型原生工具调用（默认），``react`` 走原生 ReAct 文本循环
    （可选兼容模式，解析器严格单步）。
    """
    model = create_chat_model(model_cfg)
    ctx = tool_context(
        workspace,
        session_id=session_id,
        asker=asker,
        todo_store=todo_store,
        process_manager=process_manager,
        emit=emit,
        vision=vision_from_model(model),
    )
    selected_entries = select_tool_entries(set(enabled_tools), request_text)
    tools = build_tools(selected_entries, tool_configs or {}, ctx)
    system_prompt = build_system_prompt(str(ctx.workspace) if ctx.workspace else "")

    resolved_engine = (engine or settings.agent_engine).strip().lower()
    if resolved_engine == "react":
        agent = ReActAgent(
            model,
            tools,
            system_prompt,
            approval=approval,
            max_model_calls=settings.max_model_calls,
            max_tool_calls=settings.max_tool_calls,
            model_max_retries=settings.model_max_retries,
            max_output_chars=settings.max_tool_output_chars,
        )
    else:
        model_tools = with_compact_descriptions(tools)
        agent = create_agent(
            model,
            tools=model_tools,
            system_prompt=system_prompt,
            middleware=build_middleware(approval),
        )
    # 同一个模型实例也用于生成上下文摘要
    return agent, model
