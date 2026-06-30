from __future__ import annotations

import pytest

from agent_harness.tools import ToolRegistry, ToolResult


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
        return ToolResult(output=text)


@pytest.mark.asyncio
async def test_registry_executes_registered_tool() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    result = await registry.execute("echo", {"text": "hello"})

    assert result == ToolResult(output="hello")


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(EchoTool())


def test_registry_exports_tool_definitions() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())

    definitions = registry.definitions()

    assert definitions[0].name == "echo"
    assert definitions[0].input_schema["required"] == ["text"]
