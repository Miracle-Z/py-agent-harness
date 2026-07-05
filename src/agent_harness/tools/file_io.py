from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness.tools.base import ToolResult
from agent_harness.tools.workspace import (
    relative_workspace_path,
    resolve_workspace_path,
    trim_text,
)


@dataclass
class ReadFileTool:
    # 学习说明：每个工具对象同时包含“给模型看的描述/schema”和“本地执行逻辑”。
    # input_schema 越清楚，模型越容易生成正确参数。
    root: Path = field(default_factory=Path.cwd)
    max_chars: int = 50_000
    name: str = "read_file"
    description: str = "Read a UTF-8 text file from the workspace."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative file path to read.",
                },
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "1-based first line to return. Defaults to 1.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum number of lines to return.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        }
    )

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    async def run(self, path: str, start_line: int = 1, limit: int | None = None) -> ToolResult:
        # 学习说明：schema 约束是给模型看的提示，本地仍要做运行时校验。
        if start_line < 1:
            msg = "start_line must be >= 1"
            raise ValueError(msg)
        if limit is not None and limit < 1:
            msg = "limit must be >= 1"
            raise ValueError(msg)

        target = resolve_workspace_path(self.root, path)
        if not target.exists():
            msg = f"Path does not exist: {path}"
            raise FileNotFoundError(msg)
        if not target.is_file():
            msg = f"Path is not a file: {path}"
            raise IsADirectoryError(msg)

        text = target.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        total_lines = len(lines)
        # 学习说明：支持 start_line/limit 可以让模型按需读文件，避免一次塞入整个大文件。
        start_index = start_line - 1
        end_index = total_lines if limit is None else min(total_lines, start_index + limit)
        selected_lines = lines[start_index:end_index] if start_index < total_lines else []
        content, truncated_by_chars = trim_text("\n".join(selected_lines), self.max_chars)

        return ToolResult(
            output={
                "path": relative_workspace_path(self.root, target),
                "content": content,
                "start_line": start_line,
                "end_line": start_line + len(selected_lines) - 1 if selected_lines else None,
                "total_lines": total_lines,
                "truncated": end_index < total_lines or truncated_by_chars,
            }
        )


@dataclass
class WriteFileTool:
    # 学习说明：写工具是有副作用的工具。M2 先做 workspace 和大小限制，
    # M3 会继续加入审批、Hook 和更完整的权限策略。
    root: Path = field(default_factory=Path.cwd)
    max_bytes: int = 1_000_000
    name: str = "write_file"
    description: str = "Create or overwrite a UTF-8 text file in the workspace."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative file path to write.",
                },
                "content": {"type": "string", "description": "Complete file content."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }
    )

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    async def run(self, path: str, content: str) -> ToolResult:
        # 学习说明：写入前先解析路径，确保不能写到 workspace 之外。
        target = resolve_workspace_path(self.root, path)
        if target.exists() and target.is_dir():
            msg = f"Path is a directory: {path}"
            raise IsADirectoryError(msg)

        encoded = content.encode("utf-8")
        if len(encoded) > self.max_bytes:
            msg = f"Content exceeds max_bytes={self.max_bytes}"
            raise ValueError(msg)

        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(
            output={
                "path": relative_workspace_path(self.root, target),
                "bytes_written": len(encoded),
                "created": not existed,
            }
        )


@dataclass
class EditFileTool:
    # 学习说明：精确替换比“让模型重写整个文件”更可控，也更容易在 diff 中审核。
    root: Path = field(default_factory=Path.cwd)
    name: str = "edit_file"
    description: str = "Replace one exact text fragment in a workspace file."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative file path to edit.",
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact text to replace once.",
                },
                "new_text": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_text", "new_text"],
            "additionalProperties": False,
        }
    )

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    async def run(self, path: str, old_text: str, new_text: str) -> ToolResult:
        if not old_text:
            msg = "old_text must not be empty"
            raise ValueError(msg)

        target = resolve_workspace_path(self.root, path)
        if not target.exists():
            msg = f"Path does not exist: {path}"
            raise FileNotFoundError(msg)
        if not target.is_file():
            msg = f"Path is not a file: {path}"
            raise IsADirectoryError(msg)

        text = target.read_text(encoding="utf-8", errors="replace")
        occurrences = text.count(old_text)
        if occurrences == 0:
            msg = f"Text not found in {path}"
            raise ValueError(msg)

        # 学习说明：这里只替换第一次出现的位置，避免同一段文本多处出现时误改过多内容。
        target.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return ToolResult(
            output={
                "path": relative_workspace_path(self.root, target),
                "replacements": 1,
                "remaining_occurrences": occurrences - 1,
            }
        )
