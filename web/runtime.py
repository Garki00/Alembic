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
from tools.registry import build_tools


def create_chat_model(model_cfg: ModelConfig):
    """根据模型配置创建聊天模型（DeepSeek 或 OpenAI 兼容接口）。"""
    if not model_cfg.api_key:
        raise RuntimeError(f"模型「{model_cfg.name}」还没有配置 API Key，请在设置里填写。")
    if not model_cfg.model:
        raise RuntimeError(f"模型「{model_cfg.name}」还没有填写模型名称（如 deepseek-v4-flash）。")

    kwargs: dict[str, Any] = {
        "model": model_cfg.model,
        "api_key": model_cfg.api_key,
        "temperature": model_cfg.temperature,
    }
    if model_cfg.base_url:
        kwargs["base_url"] = model_cfg.base_url

    if model_cfg.provider == "openai":
        return ChatOpenAI(**kwargs)
    return ChatDeepSeek(**kwargs)


def tool_context(
    workspace: str,
    *,
    session_id: str = "default",
    asker=None,
    todo_store=None,
    process_manager=None,
    emit=None,
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
):
    """返回 ``(agent, summarizer)``，供一轮对话使用。

    ``tool_configs`` 是条目名 → 参数表（如 QWeather 凭据）。参数按这一轮
    注入到工具实例里，不再写进 ``os.environ``，因此并发会话不会互相污染。

    ``approval`` 为工具审批开关；网页端由 :class:`web.approvals.ApprovalBroker`
    提供「阻塞等待浏览器回执」的提问函数。``asker`` 是 ask_user_question 的
    提问通道（网页端由 :class:`web.questions.QuestionBroker` 提供）。

    ``engine`` 默认取 ``settings.agent_engine``：``react`` 走原生 ReAct 文本循环，
    ``tool_calling`` 走 LangChain ``create_agent``。
    """
    model = create_chat_model(model_cfg)
    ctx = tool_context(
        workspace,
        session_id=session_id,
        asker=asker,
        todo_store=todo_store,
        process_manager=process_manager,
        emit=emit,
    )
    tools = build_tools(set(enabled_tools), tool_configs or {}, ctx)
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
        agent = create_agent(
            model,
            tools=tools,
            system_prompt=system_prompt,
            middleware=build_middleware(approval),
        )
    # 同一个模型实例也用于生成上下文摘要
    return agent, model
