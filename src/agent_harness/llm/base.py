from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from agent_harness.messages.models import Message
from agent_harness.tools.base import ToolDefinition


class LLMClient(Protocol):
    # 学习说明：LLMClient 是模型适配层的最小契约。
    # AgentLoop 只知道 complete(messages, tools)，不需要知道背后是 Anthropic、OpenAI 还是本地模型。
    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition] | None = None) -> Message:
        """Return the assistant's next message for the given conversation and tool set."""
        ...
