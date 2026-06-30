from __future__ import annotations

import builtins
from copy import deepcopy
import inspect
from typing import Any

from agent_harness.tools.base import Tool, ToolDefinition, ToolResult

EMPTY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        # 学习说明：Registry 是 Tool 的目录。AgentLoop 不直接依赖某个具体工具，
        # 只通过名字查找和执行，后续才能方便增加 read_file、shell、grep 等工具。
        if not tool.name:
            msg = "Tool name must not be empty"
            raise ValueError(msg)
        if tool.name in self._tools:
            msg = f"Tool already registered: {tool.name}"
            raise ValueError(msg)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            msg = f"Unknown tool: {name}"
            raise KeyError(msg) from exc

    def list(self) -> builtins.list[Tool]:
        return builtins.list(self._tools.values())

    def definitions(self) -> builtins.list[ToolDefinition]:
        return [
            ToolDefinition(
                name=tool.name,
                description=tool.description,
                input_schema=deepcopy(getattr(tool, "input_schema", EMPTY_INPUT_SCHEMA)),
            )
            for tool in self._tools.values()
        ]

    async def execute(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        tool = self.get(name)
        result = tool.run(**(arguments or {}))
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, ToolResult):
            return result
        return ToolResult(output=result)
