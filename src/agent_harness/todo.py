from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from agent_harness.tools.base import ToolResult


class TodoStatus(StrEnum):
    """The three states exposed by the session-local planning tool."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TodoItem(BaseModel):
    """One lightweight step in the current session plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str
    status: TodoStatus = TodoStatus.PENDING

    @field_validator("content")
    @classmethod
    def _content_must_not_be_blank(cls, value: str) -> str:
        content = value.strip()
        if not content:
            msg = "Todo content must not be blank"
            raise ValueError(msg)
        if "\n" in content or "\r" in content:
            msg = "Todo content must be a single line"
            raise ValueError(msg)
        if len(content) > 1_000:
            msg = "Todo content exceeds 1000 characters"
            raise ValueError(msg)
        return content


class TodoManager:
    """Owns an in-memory plan for one AgentLoop/Session.

    ``replace`` deliberately replaces the entire list.  This mirrors TodoWrite's
    snapshot semantics and keeps partial updates from leaving an ambiguous plan.
    """

    def __init__(self, items: Sequence[TodoItem | Mapping[str, object]] = ()) -> None:
        self._items: tuple[TodoItem, ...] = ()
        if items:
            self.replace(items)

    @property
    def items(self) -> tuple[TodoItem, ...]:
        return self._items

    def replace(
        self,
        items: Sequence[TodoItem | Mapping[str, object]],
    ) -> tuple[TodoItem, ...]:
        # Validate the complete replacement before mutating current state.
        if len(items) > 100:
            msg = "A session Todo list cannot contain more than 100 items"
            raise ValueError(msg)
        validated = tuple(
            item if isinstance(item, TodoItem) else TodoItem.model_validate(item)
            for item in items
        )
        self._items = validated
        return self._items

    def clear(self) -> None:
        self._items = ()

    def as_dicts(self) -> list[dict[str, str]]:
        return [item.model_dump(mode="json") for item in self._items]


class TodoWriteTool:
    name = "todo_write"
    description = (
        "Replace the complete task list for the current session. Use it before "
        "multi-step work and update statuses as progress changes."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "minLength": 1},
                        "status": {
                            "type": "string",
                            "enum": [status.value for status in TodoStatus],
                        },
                    },
                    "required": ["content", "status"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["todos"],
        "additionalProperties": False,
    }

    def __init__(self, manager: TodoManager) -> None:
        self.manager = manager

    def run(self, todos: list[dict[str, object]]) -> ToolResult:
        items = self.manager.replace(todos)
        return ToolResult(
            output={
                "updated": len(items),
                "todos": self.manager.as_dicts(),
            }
        )


class TodoReadTool:
    name = "todo_read"
    description = "Return the current session task list."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, manager: TodoManager) -> None:
        self.manager = manager

    def run(self) -> ToolResult:
        return ToolResult(
            output={
                "count": len(self.manager.items),
                "todos": self.manager.as_dicts(),
            }
        )


__all__ = [
    "TodoItem",
    "TodoManager",
    "TodoReadTool",
    "TodoStatus",
    "TodoWriteTool",
]
