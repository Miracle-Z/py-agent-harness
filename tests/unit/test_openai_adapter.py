from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_harness.llm.openai import OpenAIClient
from agent_harness.messages import Message, ToolCall
from agent_harness.tools import ToolDefinition


class FakeCompletionsAPI:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.response


class FakeOpenAIAPI:
    def __init__(self, response: object) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletionsAPI(response))


@pytest.mark.asyncio
async def test_openai_client_sends_tools_and_parses_tool_calls() -> None:
    api = FakeOpenAIAPI(
        SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="I will inspect the workspace.",
                        tool_calls=[
                            SimpleNamespace(
                                id="call_1",
                                type="function",
                                function=SimpleNamespace(
                                    name="list_files",
                                    arguments='{"path": "."}',
                                ),
                            )
                        ],
                    )
                )
            ]
        )
    )
    client = OpenAIClient(model="fake-model", client=api)
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

    assert api.chat.completions.calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
        }
    ]
    assert response.content == "I will inspect the workspace."
    assert response.tool_calls == [ToolCall(id="call_1", name="list_files", arguments={"path": "."})]


@pytest.mark.asyncio
async def test_openai_client_parses_dict_response() -> None:
    api = FakeOpenAIAPI(
        {
            "choices": [
                {
                    "message": {
                        "content": "你好！",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "list_files", "arguments": '{"path": "."}'},
                            }
                        ],
                    }
                }
            ]
        }
    )
    client = OpenAIClient(model="fake-model", client=api)

    response = await client.complete([Message(role="user", content="你好")])

    assert response.content == "你好！"
    assert response.tool_calls == [ToolCall(id="call_1", name="list_files", arguments={"path": "."})]


@pytest.mark.asyncio
async def test_openai_client_accepts_plain_string_response() -> None:
    api = FakeOpenAIAPI("ok")
    client = OpenAIClient(model="fake-model", client=api)

    response = await client.complete([Message(role="user", content="hello")])

    assert response.content == "ok"


@pytest.mark.asyncio
async def test_openai_client_rejects_html_response() -> None:
    api = FakeOpenAIAPI("<!doctype html><html><title>New API</title></html>")
    client = OpenAIClient(model="fake-model", client=api)

    with pytest.raises(RuntimeError, match="returned HTML instead of JSON"):
        await client.complete([Message(role="user", content="hello")])


@pytest.mark.asyncio
async def test_openai_client_serializes_tool_results() -> None:
    api = FakeOpenAIAPI(
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))])
    )
    client = OpenAIClient(model="fake-model", client=api)

    await client.complete(
        [
            Message(role="user", content="List files"),
            Message(
                role="assistant",
                content="I will inspect the workspace.",
                tool_calls=[ToolCall(id="call_1", name="list_files", arguments={"path": "."})],
            ),
            Message(role="tool", name="list_files", tool_call_id="call_1", content='["README.md"]'),
        ]
    )

    assert api.chat.completions.calls[0]["messages"] == [
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": "I will inspect the workspace.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "list_files", "arguments": '{"path": "."}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '["README.md"]'},
    ]
