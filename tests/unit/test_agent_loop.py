from __future__ import annotations

from collections.abc import Sequence

import pytest

from agent_harness.agent import AgentLoop
from agent_harness.messages import Message, ToolCall
from agent_harness.tools import ToolDefinition, ToolRegistry, ToolResult


class FakeLLM:
    def __init__(self, responses: list[Message]) -> None:
        self.responses = responses
        self.calls: list[list[Message]] = []
        self.tool_calls: list[list[ToolDefinition]] = []

    async def complete(self, messages: Sequence[Message], tools: Sequence[ToolDefinition] | None = None) -> Message:
        self.calls.append(list(messages))
        self.tool_calls.append(list(tools or []))
        return self.responses.pop(0)


class EchoTool:
    name = "echo"
    description = "Return the provided text."
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    def run(self, text: str) -> ToolResult:
        return ToolResult(output={"text": text})


@pytest.mark.asyncio
async def test_agent_loop_returns_assistant_message() -> None:
    llm = FakeLLM([Message(role="assistant", content="done")])
    loop = AgentLoop(llm=llm)

    response = await loop.run("hello")

    assert response.content == "done"
    assert llm.calls[0][0].content == "hello"


@pytest.mark.asyncio
async def test_agent_loop_executes_tool_calls() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    llm = FakeLLM(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call-1", name="echo", arguments={"text": "hello"})],
            ),
            Message(role="assistant", content="tool finished"),
        ]
    )
    loop = AgentLoop(llm=llm, tools=registry)

    response = await loop.run("use a tool")

    assert response.content == "tool finished"
    assert llm.tool_calls[0][0].name == "echo"
    assert llm.calls[1][-1].role == "tool"
    assert llm.calls[1][-1].content == '{"text": "hello"}'


@pytest.mark.asyncio
async def test_agent_loop_can_return_full_message_history() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    llm = FakeLLM(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call-1", name="echo", arguments={"text": "hello"})],
            ),
            Message(role="assistant", content="done"),
        ]
    )
    loop = AgentLoop(llm=llm, tools=registry)

    messages = await loop.run_with_history("use a tool")

    assert [message.role for message in messages] == ["user", "assistant", "tool", "assistant"]
    assert messages[-1].content == "done"
