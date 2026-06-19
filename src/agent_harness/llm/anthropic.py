from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent_harness.messages.models import Message, ToolCall
from agent_harness.tools.base import ToolDefinition


class AnthropicClient:
    # 学习说明：这个类把项目内部的 Message/ToolDefinition 转成 Anthropic API 的协议。
    # 关键转换是：assistant 的 ToolCall -> Anthropic tool_use；本地 ToolResult -> Anthropic tool_result。
    def __init__(
        self,
        model: str,
        max_tokens: int = 1024,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=api_key)

        self.model = model
        self.max_tokens = max_tokens
        self._client: Any = client

    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition] | None = None) -> Message:
        system_prompt = "\n\n".join(message.content for message in messages if message.role == "system")

        kwargs: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self._to_anthropic_messages(messages),
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if tools:
            kwargs["tools"] = [self._to_anthropic_tool(tool) for tool in tools]

        response = await self._client.messages.create(**kwargs)
        return self._from_anthropic_response(response)

    def _to_anthropic_messages(self, messages: Sequence[Message]) -> list[dict[str, object]]:
        anthropic_messages: list[dict[str, object]] = []
        pending_tool_results: list[dict[str, object]] = []

        for message in messages:
            if message.role == "system":
                continue

            if message.role == "tool":
                pending_tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id or "",
                        "content": message.content,
                    }
                )
                continue

            if pending_tool_results:
                anthropic_messages.append({"role": "user", "content": pending_tool_results})
                pending_tool_results = []

            if message.role == "assistant":
                anthropic_messages.append(self._to_anthropic_assistant_message(message))
            else:
                anthropic_messages.append({"role": "user", "content": message.content})

        if pending_tool_results:
            anthropic_messages.append({"role": "user", "content": pending_tool_results})

        return anthropic_messages

    def _to_anthropic_assistant_message(self, message: Message) -> dict[str, object]:
        if not message.tool_calls:
            return {"role": "assistant", "content": message.content}

        content_blocks: list[dict[str, object]] = []
        if message.content:
            content_blocks.append({"type": "text", "text": message.content})

        for tool_call in message.tool_calls:
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "input": tool_call.arguments,
                }
            )

        return {"role": "assistant", "content": content_blocks}

    def _to_anthropic_tool(self, tool: ToolDefinition) -> dict[str, object]:
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.input_schema,
        }

    def _from_anthropic_response(self, response: object) -> Message:
        content = getattr(response, "content", [])
        text_parts = [
            block.text
            for block in content
            if getattr(block, "type", None) == "text" and getattr(block, "text", None)
        ]
        tool_calls = [
            ToolCall(
                id=getattr(block, "id"),
                name=getattr(block, "name"),
                arguments=self._coerce_tool_arguments(getattr(block, "input", {})),
            )
            for block in content
            if getattr(block, "type", None) == "tool_use"
        ]
        return Message(role="assistant", content="\n".join(text_parts), tool_calls=tool_calls)

    def _coerce_tool_arguments(self, value: object) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        return {}
