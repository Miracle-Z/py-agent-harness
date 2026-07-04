from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from agent_harness.messages.models import Message, ToolCall
from agent_harness.tools.base import ToolDefinition


class OpenAIClient:
    # 学习说明：OpenAI Chat Completions 的 tool_calls 协议和项目内部 Message 模型很接近。
    # 这里把 ToolDefinition 包装成 function tool，再把 OpenAI tool_calls 还原成统一 ToolCall。
    def __init__(
        self,
        model: str,
        max_tokens: int = 1024,
        api_key: str | None = None,
        base_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, base_url=base_url)

        self.model = model
        self.max_tokens = max_tokens
        self._client: Any = client

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> Message:
        kwargs: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._to_openai_messages(messages),
        }
        if tools:
            kwargs["tools"] = [self._to_openai_tool(tool) for tool in tools]

        response = await self._client.chat.completions.create(**kwargs)
        return self._from_openai_response(response)

    def _to_openai_messages(self, messages: Sequence[Message]) -> list[dict[str, object]]:
        openai_messages: list[dict[str, object]] = []
        for message in messages:
            if message.role == "tool":
                openai_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": message.tool_call_id or "",
                        "content": message.content,
                    }
                )
                continue

            if message.role == "assistant" and message.tool_calls:
                openai_message: dict[str, object] = {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.name,
                                "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                            },
                        }
                        for tool_call in message.tool_calls
                    ],
                }
                openai_messages.append(openai_message)
                continue

            openai_messages.append({"role": message.role, "content": message.content})
        return openai_messages

    def _to_openai_tool(self, tool: ToolDefinition) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            },
        }

    def _from_openai_response(self, response: object) -> Message:
        if isinstance(response, str):
            if _looks_like_html(response):
                msg = (
                    "OpenAI-compatible endpoint returned HTML instead of JSON. "
                    "Check OPENAI_BASE_URL; it may point to a web page rather than an API endpoint."
                )
                raise RuntimeError(msg)
            return Message(role="assistant", content=response)

        choices = self._get(response, "choices", [])
        choice = choices[0] if choices else None
        message = self._get(choice, "message")
        if message is None:
            return Message(role="assistant", content="")

        content = self._get(message, "content", "") or ""
        tool_calls = [
            ToolCall(
                id=self._get(tool_call, "id"),
                name=self._get(self._get(tool_call, "function"), "name"),
                arguments=self._coerce_tool_arguments(
                    self._get(self._get(tool_call, "function"), "arguments", {})
                ),
            )
            for tool_call in (self._get(message, "tool_calls") or [])
            if self._get(tool_call, "type") == "function"
        ]
        return Message(role="assistant", content=content, tool_calls=tool_calls)

    def _get(self, value: object, key: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(key, default)
        return getattr(value, key, default)

    def _coerce_tool_arguments(self, value: object) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, Mapping):
                return dict(parsed)
        return {}


def _looks_like_html(value: str) -> bool:
    lowered = value.lstrip().lower()
    return lowered.startswith("<!doctype html") or lowered.startswith("<html")
