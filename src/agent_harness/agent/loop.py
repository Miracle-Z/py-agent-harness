from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from agent_harness.llm.base import LLMClient
from agent_harness.messages.models import Message
from agent_harness.tools.registry import ToolRegistry


@dataclass
class AgentLoop:
    # 学习说明：AgentLoop 是 M1 的核心。
    # 它不“决定”是否调用工具；模型决定。Loop 只负责把工具描述交给模型、执行模型请求、再把结果放回上下文。
    llm: LLMClient
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    max_tool_rounds: int = 3

    async def run(self, prompt_or_messages: str | Sequence[Message]) -> Message:
        messages = self._normalize_messages(prompt_or_messages)
        tool_definitions = self.tools.definitions()

        for tool_round in range(self.max_tool_rounds + 1):
            assistant_message = await self.llm.complete(messages, tools=tool_definitions)
            messages.append(assistant_message)

            if not assistant_message.tool_calls:
                return assistant_message

            if tool_round >= self.max_tool_rounds:
                break

            for tool_call in assistant_message.tool_calls:
                result = await self.tools.execute(tool_call.name, tool_call.arguments)
                messages.append(
                    Message(
                        role="tool",
                        name=tool_call.name,
                        tool_call_id=tool_call.id,
                        content=self._stringify_tool_output(result.output),
                    )
                )

        msg = f"Tool calling exceeded max_tool_rounds={self.max_tool_rounds}"
        raise RuntimeError(msg)

    def _normalize_messages(self, prompt_or_messages: str | Sequence[Message]) -> list[Message]:
        if isinstance(prompt_or_messages, str):
            return [Message(role="user", content=prompt_or_messages)]
        return list(prompt_or_messages)

    def _stringify_tool_output(self, output: object) -> str:
        try:
            return json.dumps(output, ensure_ascii=False)
        except TypeError:
            return str(output)
