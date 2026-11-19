"""Native Anthropic Messages API chat model.

The OpenAI-compatible path cannot express Anthropic's explicit prompt-cache
breakpoints, so Claude routes use this client instead: the request body follows
``POST /v1/messages`` and carries ``cache_control`` markers at the planned
anchors. Tool calling, streaming and usage reporting (including the cache
buckets) are supported so the agent loop behaves the same as on the compatible
endpoints.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from typing import Any

import httpx
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field, SecretStr

from agents.prompt_cache import cache_policy, plan_breakpoints

__all__ = ["ChatAnthropicMessages"]

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_BASE = "https://api.anthropic.com"

#: Marker shape for an ephemeral cache breakpoint.
_CACHE_CONTROL = {"type": "ephemeral"}


def _messages_url(base_url: str) -> str:
    """Normalize a base URL that may or may not include ``/v1``."""
    base = (base_url or _DEFAULT_BASE).strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/v1/messages"


def _data_url(block: dict[str, Any]) -> tuple[str, str] | None:
    """Return ``(media_type, data)`` for an OpenAI-style image block."""
    image = block.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    if not isinstance(url, str) or not url.startswith("data:"):
        return None
    header, _, data = url.partition(",")
    media_type = header[5:].split(";")[0] or "image/png"
    return media_type, data


def _human_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content or "")}]
    blocks: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, str):
            blocks.append({"type": "text", "text": block})
            continue
        if not isinstance(block, dict):
            blocks.append({"type": "text", "text": str(block)})
            continue
        kind = str(block.get("type") or "text")
        if kind == "text":
            blocks.append({"type": "text", "text": str(block.get("text") or "")})
        elif kind in {"image_url", "input_image"}:
            parsed = _data_url(block)
            if parsed is not None:
                media_type, data = parsed
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    }
                )
    return blocks or [{"type": "text", "text": ""}]


def _assistant_blocks(message: AIMessage) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    content = message.content
    if isinstance(content, str):
        if content:
            blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                blocks.append({"type": "text", "text": block})
            elif isinstance(block, dict) and block.get("type") == "text":
                blocks.append({"type": "text", "text": str(block.get("text") or "")})
    for call in getattr(message, "tool_calls", None) or []:
        blocks.append(
            {
                "type": "tool_use",
                "id": str(call.get("id") or ""),
                "name": str(call.get("name") or ""),
                "input": call.get("args") or {},
            }
        )
    return blocks or [{"type": "text", "text": ""}]


def _tool_result_blocks(message: ToolMessage) -> list[dict[str, Any]]:
    content = message.content
    if isinstance(content, list):
        text = " ".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    else:
        text = str(content or "")
    return [
        {
            "type": "tool_result",
            "tool_use_id": str(getattr(message, "tool_call_id", "") or ""),
            "content": text,
        }
    ]


class ChatAnthropicMessages(BaseChatModel):
    """Chat model backed by Anthropic's native Messages API."""

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    model_name: str = Field(alias="model")
    api_key: SecretStr = Field(default=SecretStr(""))
    base_url: str = _DEFAULT_BASE
    temperature: float = 0.0
    max_tokens: int = 8192
    timeout: float = 300.0
    #: Logical cache anchors requested by the runtime; see ``plan_breakpoints``.
    cache_anchors: tuple[str, ...] = ()
    has_first_turn: bool = False

    @property
    def _llm_type(self) -> str:
        return "anthropic-messages"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "base_url": self.base_url,
            "temperature": self.temperature,
        }

    # ---- 构建请求 ----
    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Bind tool declarations; they are converted to Anthropic tools on send."""
        return self.bind(tools=list(tools), tool_choice=tool_choice, **kwargs)

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key.get_secret_value(),
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _policy(self):
        return cache_policy(self.model_name)

    def _anchors(self, messages: Sequence[BaseMessage]) -> tuple[str, ...]:
        policy = self._policy()
        if not policy.explicit:
            return ()
        if self.cache_anchors:
            return tuple(self.cache_anchors)[: max(1, policy.breakpoints)]
        # A second user turn means the first exchange is part of the prefix and
        # can carry its own breakpoint.
        turns = sum(1 for message in messages if isinstance(message, HumanMessage))
        return plan_breakpoints(
            policy, has_first_turn=self.has_first_turn or turns >= 2
        )

    def _payload(
        self,
        messages: Sequence[BaseMessage],
        *,
        tools: Sequence[Any] | None = None,
        tool_choice: Any = None,
        stop: Sequence[str] | None = None,
        max_tokens: int | None = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        anchors = set(self._anchors(messages))
        system_blocks: list[dict[str, Any]] = []
        converted: list[dict[str, Any]] = []

        def push(role: str, blocks: list[dict[str, Any]]) -> None:
            if not blocks:
                return
            if converted and converted[-1]["role"] == role:
                converted[-1]["content"].extend(blocks)
            else:
                converted.append({"role": role, "content": list(blocks)})

        for message in messages:
            if isinstance(message, SystemMessage):
                text = message.content if isinstance(message.content, str) else str(message.content)
                if text:
                    system_blocks.append({"type": "text", "text": text})
                continue
            if isinstance(message, ToolMessage):
                push("user", _tool_result_blocks(message))
                continue
            if isinstance(message, AIMessage):
                push("assistant", _assistant_blocks(message))
                continue
            push("user", _human_blocks(message.content))

        if system_blocks and "prefix" in anchors:
            system_blocks[-1]["cache_control"] = dict(_CACHE_CONTROL)
        if "first_turn" in anchors:
            # The preserved first exchange ends at the first assistant turn that
            # closes it; mark its last block so the provider caches past it.
            for item in converted:
                if item["role"] == "assistant":
                    item["content"][-1]["cache_control"] = dict(_CACHE_CONTROL)
                    break
        if "tail" in anchors and converted:
            converted[-1]["content"][-1]["cache_control"] = dict(_CACHE_CONTROL)

        payload: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": int(max_tokens or self.max_tokens),
            "messages": converted,
        }
        if system_blocks:
            payload["system"] = system_blocks
        if self.temperature is not None:
            payload["temperature"] = float(self.temperature)
        if stop:
            payload["stop_sequences"] = [str(item) for item in stop if str(item)]
        if tools:
            payload["tools"] = self._convert_tools(tools)
            choice = self._tool_choice(tool_choice)
            if choice is not None:
                payload["tool_choice"] = choice
        if stream:
            payload["stream"] = True
        return payload

    @staticmethod
    def _convert_tools(tools: Sequence[Any]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            spec = convert_to_openai_tool(tool)
            function = spec.get("function") or {}
            name = str(function.get("name") or spec.get("name") or "")
            if not name:
                continue
            converted.append(
                {
                    "name": name,
                    "description": str(function.get("description") or ""),
                    "input_schema": function.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        return converted

    @staticmethod
    def _tool_choice(choice: Any) -> dict[str, Any] | None:
        if choice in (None, "", "auto"):
            return None
        if choice == "any" or choice == "required":
            return {"type": "any"}
        if isinstance(choice, str):
            return {"type": "tool", "name": choice}
        if isinstance(choice, dict):
            name = (choice.get("function") or {}).get("name") or choice.get("name")
            if name:
                return {"type": "tool", "name": str(name)}
        return None

    # ---- 响应解析 ----
    def _usage(self, usage: dict[str, Any]) -> dict[str, Any]:
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        prompt = int(usage.get("input_tokens") or 0) + cache_read + cache_write
        output = int(usage.get("output_tokens") or 0)
        details: dict[str, int] = {}
        if cache_read:
            details["cache_read"] = cache_read
        if cache_write:
            details["cache_creation"] = cache_write
        metadata: dict[str, Any] = {
            "input_tokens": prompt,
            "output_tokens": output,
            "total_tokens": prompt + output,
        }
        if details:
            metadata["input_token_details"] = details
        return metadata

    def _result(self, data: dict[str, Any]) -> ChatResult:
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in data.get("content") or []:
            kind = block.get("type")
            if kind == "text":
                text_parts.append(str(block.get("text") or ""))
            elif kind == "tool_use":
                tool_calls.append(
                    {
                        "name": str(block.get("name") or ""),
                        "args": block.get("input") or {},
                        "id": str(block.get("id") or ""),
                        "type": "tool_call",
                    }
                )
        message = AIMessage(
            content="\n".join(part for part in text_parts if part),
            tool_calls=tool_calls,
            usage_metadata=self._usage(data.get("usage") or {}),
            response_metadata={"stop_reason": data.get("stop_reason")},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _error(response: httpx.Response) -> RuntimeError:
        try:
            body = response.json()
        except ValueError:
            body = {}
        detail = body.get("error") if isinstance(body, dict) else None
        message = ""
        if isinstance(detail, dict):
            message = str(detail.get("message") or "")
        if not message:
            message = response.text[:400]
        return RuntimeError(f"Anthropic API error ({response.status_code}): {message}")

    # ---- 调用 ----
    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        payload = self._payload(
            messages,
            tools=kwargs.get("tools"),
            tool_choice=kwargs.get("tool_choice"),
            stop=stop,
            max_tokens=kwargs.get("max_tokens"),
        )
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                _messages_url(self.base_url), headers=self._headers(), json=payload
            )
        if response.status_code >= 400:
            raise self._error(response)
        return self._result(response.json())

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        payload = self._payload(
            messages,
            tools=kwargs.get("tools"),
            tool_choice=kwargs.get("tool_choice"),
            stop=stop,
            max_tokens=kwargs.get("max_tokens"),
            stream=True,
        )
        usage: dict[str, Any] = {}
        tool_index = -1
        tool_state: dict[str, Any] | None = None
        with httpx.Client(timeout=self.timeout) as client:
            with client.stream(
                "POST", _messages_url(self.base_url), headers=self._headers(), json=payload
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    raise self._error(response)
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        continue
                    kind = event.get("type")
                    if kind == "message_start":
                        usage.update(event.get("message", {}).get("usage") or {})
                    elif kind == "content_block_start":
                        block = event.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            tool_index += 1
                            tool_state = {
                                "id": str(block.get("id") or ""),
                                "name": str(block.get("name") or ""),
                                "args": "",
                                "index": tool_index,
                            }
                    elif kind == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            text = str(delta.get("text") or "")
                            if text:
                                yield ChatGenerationChunk(
                                    message=AIMessageChunk(content=text)
                                )
                        elif delta.get("type") == "input_json_delta" and tool_state is not None:
                            tool_state["args"] += str(delta.get("partial_json") or "")
                    elif kind == "content_block_stop":
                        if tool_state is not None:
                            chunk = AIMessageChunk(
                                content="",
                                tool_call_chunks=[
                                    {
                                        "name": tool_state["name"],
                                        "args": tool_state["args"],
                                        "id": tool_state["id"],
                                        "index": tool_state["index"],
                                    }
                                ],
                            )
                            tool_state = None
                            yield ChatGenerationChunk(message=chunk)
                    elif kind == "message_delta":
                        usage.update(event.get("usage") or {})
                        metadata = self._usage(usage)
                        yield ChatGenerationChunk(
                            message=AIMessageChunk(content="", usage_metadata=metadata)
                        )
