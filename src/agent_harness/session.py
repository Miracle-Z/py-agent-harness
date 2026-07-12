from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_harness.messages.models import Message
from agent_harness.todo import TodoItem


_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class SessionError(RuntimeError):
    """Base class for durable session failures."""


class SessionNotFoundError(SessionError):
    pass


class SessionCorruptError(SessionError):
    pass


class SessionWorkspaceMismatchError(SessionError):
    pass


class SessionRecord(BaseModel):
    """Serializable state required to continue one conversation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    id: str
    workspace: str | None = None
    messages: list[Message] = Field(default_factory=list)
    todos: list[TodoItem] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("schema_version")
    @classmethod
    def _supported_schema(cls, value: int) -> int:
        if value != 1:
            msg = f"Unsupported session schema version: {value}"
            raise ValueError(msg)
        return value

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if not _SESSION_ID_RE.fullmatch(value):
            msg = f"Invalid session id: {value!r}"
            raise ValueError(msg)
        return value


class SessionStore:
    """Atomic JSON store for resumable AgentLoop histories."""

    def __init__(self, directory: Path | str, *, workspace: Path | str | None = None) -> None:
        self.directory = Path(directory).resolve()
        self.workspace = str(Path(workspace).resolve()) if workspace is not None else None

    def create(
        self,
        *,
        session_id: str | None = None,
        messages: Sequence[Message] = (),
        todos: Sequence[TodoItem | Mapping[str, object]] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionRecord:
        identifier = uuid4().hex if session_id is None else session_id
        path = self._path(identifier)
        if path.exists():
            msg = f"Session already exists: {identifier}"
            raise FileExistsError(msg)
        record = SessionRecord(
            id=identifier,
            workspace=self.workspace,
            messages=[message.model_copy(deep=True) for message in messages],
            todos=[
                item if isinstance(item, TodoItem) else TodoItem.model_validate(item)
                for item in todos
            ],
            metadata=dict(metadata or {}),
        )
        self._write(record, create_only=True)
        return record

    def load(self, session_id: str) -> SessionRecord:
        path = self._path(session_id)
        if not path.exists():
            raise SessionNotFoundError(f"Session not found: {session_id}")
        try:
            record = SessionRecord.model_validate_json(path.read_text(encoding="utf-8"))
            validate_message_protocol(record.messages)
        except (OSError, ValueError) as exc:
            msg = f"Session is corrupt: {session_id}: {exc}"
            raise SessionCorruptError(msg) from exc
        if record.id != session_id:
            msg = f"Session id mismatch: requested {session_id}, file contains {record.id}"
            raise SessionCorruptError(msg)
        if self.workspace and record.workspace and record.workspace != self.workspace:
            msg = (
                f"Session {session_id} belongs to workspace {record.workspace}, "
                f"not {self.workspace}"
            )
            raise SessionWorkspaceMismatchError(msg)
        return record

    def save(self, session: SessionRecord) -> SessionRecord:
        # Refuse to write a record whose path would be unsafe before creating dirs.
        self._path(session.id)
        if self.workspace and session.workspace and session.workspace != self.workspace:
            msg = (
                f"Session {session.id} belongs to workspace {session.workspace}, "
                f"not {self.workspace}"
            )
            raise SessionWorkspaceMismatchError(msg)
        validate_message_protocol(session.messages)
        updated = session.model_copy(update={"updated_at": datetime.now(timezone.utc)})
        self._write(updated)
        return updated

    def checkpoint(
        self,
        session_id: str,
        *,
        messages: Sequence[Message],
        todos: Sequence[TodoItem | Mapping[str, object]] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> SessionRecord:
        current = self.load(session_id)
        update: dict[str, object] = {
            "messages": [message.model_copy(deep=True) for message in messages],
            "todos": [
                item if isinstance(item, TodoItem) else TodoItem.model_validate(item)
                for item in todos
            ],
        }
        if metadata is not None:
            update["metadata"] = dict(metadata)
        return self.save(current.model_copy(update=update))

    def list(self) -> list[SessionRecord]:
        if not self.directory.exists():
            return []
        sessions = [self.load(path.stem) for path in self.directory.glob("*.json")]
        return sorted(sessions, key=lambda session: (session.updated_at, session.id), reverse=True)

    def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _path(self, session_id: str) -> Path:
        if not _SESSION_ID_RE.fullmatch(session_id):
            msg = f"Invalid session id: {session_id!r}"
            raise ValueError(msg)
        path = self.directory / f"{session_id}.json"
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory):
            msg = f"Unsafe session path: {session_id}"
            raise PermissionError(msg)
        return path

    def _write(self, session: SessionRecord, *, create_only: bool = False) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(session.id)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(session.model_dump_json(indent=2) + "\n", encoding="utf-8")
            if create_only:
                # Linking a fully written temp file fails atomically if another process won
                # the same named-session creation race.
                os.link(temporary, path)
            else:
                temporary.replace(path)
        except FileExistsError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            msg = f"Could not save session {session.id}: {exc}"
            raise SessionError(msg) from exc
        finally:
            temporary.unlink(missing_ok=True)


def validate_message_protocol(messages: Sequence[Message]) -> None:
    """Reject orphan, duplicate, or incomplete tool-call/result sequences."""

    pending: set[str] = set()
    for index, message in enumerate(messages):
        if pending:
            if message.role != "tool":
                missing = ", ".join(sorted(pending))
                msg = f"Missing tool results before message {index}: {missing}"
                raise ValueError(msg)
            tool_call_id = message.tool_call_id or ""
            if tool_call_id not in pending:
                msg = f"Unexpected or duplicate tool result at message {index}: {tool_call_id}"
                raise ValueError(msg)
            pending.remove(tool_call_id)
            continue

        if message.role == "tool":
            msg = f"Orphan tool result at message {index}: {message.tool_call_id or ''}"
            raise ValueError(msg)
        if message.role == "assistant" and message.tool_calls:
            ids = [tool_call.id for tool_call in message.tool_calls]
            if len(ids) != len(set(ids)):
                msg = f"Duplicate tool call id at message {index}"
                raise ValueError(msg)
            pending = set(ids)

    if pending:
        missing = ", ".join(sorted(pending))
        msg = f"Session ends with missing tool results: {missing}"
        raise ValueError(msg)


def repair_incomplete_tool_calls(messages: list[Message]) -> list[Message]:
    """Close only missing trailing/interrupted results before an error checkpoint.

    Existing orphan or duplicate results remain hard errors; silently reinterpreting them
    would hide session corruption.
    """

    repaired: list[Message] = []
    pending: dict[str, str] = {}

    def append_missing_results() -> None:
        for tool_call_id, tool_name in pending.items():
            repaired.append(
                Message(
                    role="tool",
                    name=tool_name,
                    tool_call_id=tool_call_id,
                    content=json.dumps(
                        {
                            "error": "InterruptedToolCall",
                            "message": "The prior turn ended before this tool call completed.",
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        pending.clear()

    for index, message in enumerate(messages):
        if pending and message.role != "tool":
            append_missing_results()
        if message.role == "tool":
            tool_call_id = message.tool_call_id or ""
            if tool_call_id not in pending:
                msg = f"Unexpected or duplicate tool result at message {index}: {tool_call_id}"
                raise ValueError(msg)
            pending.pop(tool_call_id)
            repaired.append(message)
            continue
        repaired.append(message)
        if message.role == "assistant" and message.tool_calls:
            ids = [tool_call.id for tool_call in message.tool_calls]
            if len(ids) != len(set(ids)):
                msg = f"Duplicate tool call id at message {index}"
                raise ValueError(msg)
            pending = {tool_call.id: tool_call.name for tool_call in message.tool_calls}

    if pending:
        append_missing_results()
    messages[:] = repaired
    return messages


__all__ = [
    "SessionCorruptError",
    "SessionError",
    "SessionNotFoundError",
    "SessionRecord",
    "SessionStore",
    "SessionWorkspaceMismatchError",
    "repair_incomplete_tool_calls",
    "validate_message_protocol",
]
