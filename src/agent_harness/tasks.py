from __future__ import annotations

import builtins
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_harness.tools.base import ToolResult


_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TaskRecord(BaseModel):
    """One durable node in the long-running task dependency graph."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)

    id: str
    subject: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    owner: str | None = None
    blocked_by: tuple[str, ...] = Field(default_factory=tuple, alias="blockedBy")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _TASK_ID_RE.fullmatch(value):
            msg = f"Invalid task id: {value!r}"
            raise ValueError(msg)
        return value

    @field_validator("subject")
    @classmethod
    def _subject_not_blank(cls, value: str) -> str:
        subject = value.strip()
        if not subject:
            msg = "Task subject must not be blank"
            raise ValueError(msg)
        if "\n" in subject or "\r" in subject:
            msg = "Task subject must be a single line"
            raise ValueError(msg)
        if len(subject) > 500:
            msg = "Task subject exceeds 500 characters"
            raise ValueError(msg)
        return subject

    @field_validator("blocked_by")
    @classmethod
    def _unique_dependencies(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            msg = "Task dependencies must be unique"
            raise ValueError(msg)
        for task_id in value:
            if not _TASK_ID_RE.fullmatch(task_id):
                msg = f"Invalid dependency task id: {task_id!r}"
                raise ValueError(msg)
        return value


class TaskStore:
    """File-backed task graph with atomic record updates."""

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory).resolve()

    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[str] | tuple[str, ...] | None = None,
        *,
        task_id: str | None = None,
    ) -> TaskRecord:
        identifier = task_id or f"task_{uuid4().hex[:12]}"
        dependencies = _normalize_dependencies(blocked_by)
        if identifier in set(dependencies):
            msg = "A task cannot depend on itself"
            raise ValueError(msg)
        if self.exists(identifier):
            msg = f"Task already exists: {identifier}"
            raise FileExistsError(msg)
        record = TaskRecord(
            id=identifier,
            subject=subject,
            description=description,
            blockedBy=dependencies,
        )
        self.save(record)
        return record

    def save(self, task: TaskRecord) -> None:
        if task.id in task.blocked_by:
            msg = "A task cannot depend on itself"
            raise ValueError(msg)
        path = self._path(task.id)
        if path.exists():
            current = self.get(task.id)
            allowed = {
                (TaskStatus.PENDING, TaskStatus.PENDING),
                (TaskStatus.PENDING, TaskStatus.IN_PROGRESS),
                (TaskStatus.IN_PROGRESS, TaskStatus.IN_PROGRESS),
                (TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED),
                (TaskStatus.COMPLETED, TaskStatus.COMPLETED),
            }
            if (current.status, task.status) not in allowed:
                msg = (
                    f"Invalid task transition: {current.status.value} -> "
                    f"{task.status.value}"
                )
                raise ValueError(msg)
        elif task.status != TaskStatus.PENDING:
            msg = f"New task must be pending, not {task.status.value}"
            raise ValueError(msg)
        self._ensure_acyclic(extra=task)
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = task.model_dump_json(by_alias=True, indent=2)
        self._atomic_write(path, payload + "\n")

    def get(self, task_id: str) -> TaskRecord:
        path = self._path(task_id)
        if not path.exists():
            msg = f"Task not found: {task_id}"
            raise FileNotFoundError(msg)
        try:
            task = TaskRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            msg = f"Invalid task file: {path.name}: {exc}"
            raise ValueError(msg) from exc
        if task.id != task_id:
            msg = f"Task id mismatch: requested {task_id}, file contains {task.id}"
            raise ValueError(msg)
        return task

    def list(self) -> list[TaskRecord]:
        if not self.directory.exists():
            return []
        records = [self.get(path.stem) for path in self.directory.glob("*.json")]
        return sorted(records, key=lambda task: (task.created_at, task.id))

    def exists(self, task_id: str) -> bool:
        return self._path(task_id).exists()

    def blocked_dependencies(self, task_id: str) -> builtins.list[str]:
        task = self.get(task_id)
        blocked: builtins.list[str] = []
        for dependency_id in task.blocked_by:
            try:
                dependency = self.get(dependency_id)
            except FileNotFoundError:
                blocked.append(dependency_id)
                continue
            if dependency.status != TaskStatus.COMPLETED:
                blocked.append(dependency_id)
        return blocked

    def can_start(self, task_id: str) -> bool:
        return not self.blocked_dependencies(task_id)

    def claim(self, task_id: str, owner: str = "agent") -> TaskRecord:
        task = self.get(task_id)
        if task.status != TaskStatus.PENDING:
            msg = f"Task {task_id} is {task.status.value}, cannot claim"
            raise ValueError(msg)
        blocked = self.blocked_dependencies(task_id)
        if blocked:
            msg = f"Task {task_id} is blocked by: {', '.join(blocked)}"
            raise ValueError(msg)
        normalized_owner = owner.strip()
        if not normalized_owner:
            msg = "Task owner must not be blank"
            raise ValueError(msg)
        if "\n" in normalized_owner or "\r" in normalized_owner:
            msg = "Task owner must be a single line"
            raise ValueError(msg)
        claimed = task.model_copy(
            update={
                "owner": normalized_owner,
                "status": TaskStatus.IN_PROGRESS,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self.save(claimed)
        return claimed

    def complete(
        self,
        task_id: str,
    ) -> tuple[TaskRecord, builtins.list[TaskRecord]]:
        task = self.get(task_id)
        if task.status != TaskStatus.IN_PROGRESS:
            msg = f"Task {task_id} is {task.status.value}, cannot complete"
            raise ValueError(msg)

        dependents = [
            candidate
            for candidate in self.list()
            if candidate.status == TaskStatus.PENDING and task_id in candidate.blocked_by
        ]
        completed = task.model_copy(
            update={
                "status": TaskStatus.COMPLETED,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        self.save(completed)
        unblocked = [candidate for candidate in dependents if self.can_start(candidate.id)]
        return completed, unblocked

    def blocks(self, task_id: str) -> builtins.list[str]:
        return [task.id for task in self.list() if task_id in task.blocked_by]

    def _path(self, task_id: str) -> Path:
        if not _TASK_ID_RE.fullmatch(task_id):
            msg = f"Invalid task id: {task_id!r}"
            raise ValueError(msg)
        path = self.directory / f"{task_id}.json"
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory):
            msg = f"Unsafe task path: {task_id}"
            raise PermissionError(msg)
        return path

    def _ensure_acyclic(self, *, extra: TaskRecord | None = None) -> None:
        records = {task.id: task for task in self.list()}
        if extra is not None:
            records[extra.id] = extra

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visited or task_id not in records:
                return
            if task_id in visiting:
                msg = f"Task dependency cycle detected at {task_id}"
                raise ValueError(msg)
            visiting.add(task_id)
            for dependency in records[task_id].blocked_by:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in records:
            visit(task_id)

    def _atomic_write(self, path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _normalize_dependencies(
    blocked_by: list[str] | tuple[str, ...] | None,
) -> tuple[str, ...]:
    if blocked_by is None:
        return ()
    if not isinstance(blocked_by, (list, tuple)) or not all(
        isinstance(task_id, str) for task_id in blocked_by
    ):
        msg = "blockedBy must be an array of task ID strings"
        raise ValueError(msg)
    return tuple(blocked_by)


def _task_output(store: TaskStore, task: TaskRecord) -> dict[str, object]:
    output = task.model_dump(mode="json", by_alias=True)
    output["blocks"] = store.blocks(task.id)
    output["blocked"] = bool(store.blocked_dependencies(task.id))
    return output


class CreateTaskTool:
    name = "create_task"
    description = "Create a persistent task, optionally blocked by other task IDs."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "blockedBy": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["subject"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    def run(
        self,
        subject: str,
        description: str = "",
        blockedBy: list[str] | None = None,
    ) -> ToolResult:
        task = self.store.create(subject, description, blockedBy)
        return ToolResult(output=_task_output(self.store, task))


class ListTasksTool:
    name = "list_tasks"
    description = "List persistent tasks with dependency and status summaries."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    def run(self) -> ToolResult:
        tasks = [_task_output(self.store, task) for task in self.store.list()]
        return ToolResult(output={"count": len(tasks), "tasks": tasks})


class GetTaskTool:
    name = "get_task"
    description = "Get one persistent task and its full dependency details."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    def run(self, task_id: str) -> ToolResult:
        return ToolResult(output=_task_output(self.store, self.store.get(task_id)))


class ClaimTaskTool:
    name = "claim_task"
    description = "Claim an unblocked pending task and mark it in progress."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "owner": {"type": "string", "default": "agent"},
        },
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    def run(self, task_id: str, owner: str = "agent") -> ToolResult:
        return ToolResult(output=_task_output(self.store, self.store.claim(task_id, owner)))


class CompleteTaskTool:
    name = "complete_task"
    description = "Complete an in-progress task and report newly unblocked tasks."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
        "additionalProperties": False,
    }

    def __init__(self, store: TaskStore) -> None:
        self.store = store

    def run(self, task_id: str) -> ToolResult:
        task, unblocked = self.store.complete(task_id)
        output = _task_output(self.store, task)
        output["unblocked"] = [
            {"id": candidate.id, "subject": candidate.subject} for candidate in unblocked
        ]
        return ToolResult(output=output)


TASK_TOOL_TYPES = (
    CreateTaskTool,
    ListTasksTool,
    GetTaskTool,
    ClaimTaskTool,
    CompleteTaskTool,
)


__all__ = [
    "ClaimTaskTool",
    "CompleteTaskTool",
    "CreateTaskTool",
    "GetTaskTool",
    "ListTasksTool",
    "TASK_TOOL_TYPES",
    "TaskRecord",
    "TaskStatus",
    "TaskStore",
]
