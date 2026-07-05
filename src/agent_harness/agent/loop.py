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
        messages = await self.run_with_history(prompt_or_messages)
        return messages[-1]

    async def run_with_history(self, prompt_or_messages: str | Sequence[Message]) -> list[Message]:
        messages = self._normalize_messages(prompt_or_messages)
        tool_definitions = self.tools.definitions()

        for tool_round in range(self.max_tool_rounds + 1):
            # 学习说明：每一轮都把完整上下文和当前可用工具定义交给模型。
            # 工具定义只是“菜单”，模型只能请求工具；真正执行仍发生在本地 Harness。
            assistant_message = await self.llm.complete(messages, tools=tool_definitions)
            messages.append(assistant_message)

            # 学习说明：没有 tool_calls 说明模型认为任务已完成，AgentLoop 直接返回最终回答。
            if not assistant_message.tool_calls:
                return messages

            if tool_round >= self.max_tool_rounds:
                break

            for tool_call in assistant_message.tool_calls:
                # 学习说明：ToolRegistry 根据模型给出的工具名做分发。
                # 这里不关心具体工具是读文件、搜索还是跑命令，Loop 因此保持稳定。
                result = await self.tools.execute(tool_call.name, tool_call.arguments)
                messages.append(
                    Message(
                        role="tool",
                        name=tool_call.name,
                        tool_call_id=tool_call.id,
                        # 学习说明：工具结果必须回填到消息历史里，模型下一轮才能基于观察继续推理。
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
