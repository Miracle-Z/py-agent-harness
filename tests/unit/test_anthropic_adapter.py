from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_harness.llm.anthropic import AnthropicClient
from agent_harness.messages import Message, ToolCall
from agent_harness.tools import ToolDefinition


class FakeMessagesAPI:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


class FakeAnthropicAPI:
    def __init__(self, response: object) -> None:
        self.messages = FakeMessagesAPI(response)


@pytest.mark.asyncio
async def test_anthropic_client_sends_tools_and_parses_tool_use() -> None:
    api = FakeAnthropicAPI(
        SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="I will inspect the workspace."),
                SimpleNamespace(type="tool_use", id="toolu_1", name="list_files", input={"path": "."}),
            ]
        )
    )
    client = AnthropicClient(model="fake-model", client=api)
    tool = ToolDefinition(
        name="list_files",
        description="List files.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
    )

    response = await client.complete([Message(role="user", content="List files")], tools=[tool])

    assert api.messages.calls[0]["tools"] == [
        {
            "name": "list_files",
            "description": "List files.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
        }
    ]
    assert response.content == "I will inspect the workspace."
    assert response.tool_calls == [ToolCall(id="toolu_1", name="list_files", arguments={"path": "."})]


@pytest.mark.asyncio
async def test_anthropic_client_serializes_tool_results() -> None:
    api = FakeAnthropicAPI(SimpleNamespace(content=[SimpleNamespace(type="text", text="done")]))
    client = AnthropicClient(model="fake-model", client=api)

    await client.complete(
        [
            Message(role="user", content="List files"),
            Message(
                role="assistant",
                content="I will inspect the workspace.",
                tool_calls=[ToolCall(id="toolu_1", name="list_files", arguments={"path": "."})],
            ),
            Message(role="tool", name="list_files", tool_call_id="toolu_1", content='["README.md"]'),
        ]
    )

    assert api.messages.calls[0]["messages"] == [
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I will inspect the workspace."},
                {"type": "tool_use", "id": "toolu_1", "name": "list_files", "input": {"path": "."}},
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": '["README.md"]',
                }
            ],
        },
    ]
