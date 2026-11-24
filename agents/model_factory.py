# agents/model_factory.py
"""Create a chat model from a stored provider/model configuration.

Both the web runtime and the CLI subagent runner use this factory so a profile
that names a model gets the same client the web UI would build.
"""

from __future__ import annotations

from typing import Any

from langchain_deepseek import ChatDeepSeek
from langchain_openai import ChatOpenAI

from agents.anthropic_messages import ChatAnthropicMessages
from config.store import ModelConfig

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
    anthropic_default = PROVIDER_ANTHROPIC_BASE_URLS.get(provider, "").rstrip("/")
    if target and (not current or (anthropic_default and current == anthropic_default)):
        return target
    return current or target


def create_chat_model(model_cfg: ModelConfig):
    """Create a chat model from its configuration.

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
        # Providers that do not support this parameter ignore or reject it; none omits it.
        kwargs["reasoning_effort"] = thinking

    if provider == "deepseek" and api_mode == API_MODE_OPENAI:
        return ChatDeepSeek(**kwargs)
    return ChatOpenAI(**kwargs)
