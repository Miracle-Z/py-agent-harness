from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable
from typing import Any, Awaitable, Protocol


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    output: Any


class Tool(Protocol):
    # 学习说明：Tool 是 Harness 给模型暴露的“动作接口”。
    # name/description/input_schema 会发给 LLM，让模型知道有哪些工具、何时使用、参数长什么样。
    name: str
    description: str
    input_schema: dict[str, Any]

    @property
    def run(self) -> Callable[..., ToolResult | Awaitable[ToolResult]]:
        """Execute the tool and return a structured result."""
        ...
