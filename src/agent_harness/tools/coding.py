from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from agent_harness.tools.base import Tool
from agent_harness.tools.file_io import EditFileTool, ReadFileTool, WriteFileTool
from agent_harness.tools.git_tools import GitDiffTool
from agent_harness.tools.list_files import ListFilesTool
from agent_harness.tools.registry import ToolRegistry
from agent_harness.tools.search import GlobTool, SearchTextTool
from agent_harness.tools.shell import RunTestsTool, ShellTool
from agent_harness.tools.workspace import ensure_inside_root

if TYPE_CHECKING:
    from agent_harness.context import ContextManager
    from agent_harness.memory import MemoryStore
    from agent_harness.tasks import TaskStore
    from agent_harness.todo import TodoManager


def create_coding_tools(root: Path | str | None = None) -> list[Tool]:
    # 学习说明：Coding Agent 的能力来自一组小而独立的工具。
    # 增加能力时优先新增工具并注册，而不是修改 AgentLoop。
    workspace_root = Path.cwd() if root is None else Path(root)
    return [
        ListFilesTool(root=workspace_root),
        ReadFileTool(root=workspace_root),
        WriteFileTool(root=workspace_root),
        EditFileTool(root=workspace_root),
        GlobTool(root=workspace_root),
        SearchTextTool(root=workspace_root),
        ShellTool(root=workspace_root),
        RunTestsTool(root=workspace_root),
        GitDiffTool(root=workspace_root),
    ]


def create_coding_tool_registry(root: Path | str | None = None) -> ToolRegistry:
    # 学习说明：Registry 是工具目录。LLM 看到的是 definitions()，本地执行走 execute()。
    registry = ToolRegistry()
    for tool in create_coding_tools(root):
        registry.register(tool)
    return registry


def register_m4_tools(
    registry: ToolRegistry,
    *,
    todo_manager: TodoManager,
    memory_store: MemoryStore,
    task_store: TaskStore,
    context_manager: ContextManager,
) -> ToolRegistry:
    """Add M4 stateful tools without changing the M2-only registry contract."""

    from agent_harness.context import CompactTool
    from agent_harness.memory import MemoryReadTool, MemorySearchTool, MemoryWriteTool
    from agent_harness.tasks import (
        ClaimTaskTool,
        CompleteTaskTool,
        CreateTaskTool,
        GetTaskTool,
        ListTasksTool,
    )
    from agent_harness.todo import TodoReadTool, TodoWriteTool

    tools: tuple[Tool, ...] = (
        TodoWriteTool(todo_manager),
        TodoReadTool(todo_manager),
        CompactTool(context_manager),
        MemoryWriteTool(memory_store),
        MemoryReadTool(memory_store),
        MemorySearchTool(memory_store),
        CreateTaskTool(task_store),
        ListTasksTool(task_store),
        GetTaskTool(task_store),
        ClaimTaskTool(task_store),
        CompleteTaskTool(task_store),
    )
    for tool in tools:
        registry.register(tool)
    return registry


def create_m4_tool_registry(
    root: Path | str | None = None,
    *,
    todo_manager: TodoManager | None = None,
    memory_store: MemoryStore | None = None,
    task_store: TaskStore | None = None,
    context_manager: ContextManager | None = None,
) -> ToolRegistry:
    """Create the full Coding Agent registry used by the M4 CLI runtime."""

    from agent_harness.context import ContextManager
    from agent_harness.memory import MemoryStore
    from agent_harness.tasks import TaskStore
    from agent_harness.todo import TodoManager

    workspace_root = (Path.cwd() if root is None else Path(root)).resolve()
    todo_manager = todo_manager or TodoManager()
    memory_store = memory_store or MemoryStore(
        ensure_inside_root(workspace_root, workspace_root / ".memory")
    )
    task_store = task_store or TaskStore(
        ensure_inside_root(workspace_root, workspace_root / ".tasks")
    )
    context_manager = context_manager or ContextManager(
        transcript_dir=ensure_inside_root(
            workspace_root,
            workspace_root / ".transcripts",
        ),
        tool_output_dir=ensure_inside_root(
            workspace_root,
            workspace_root / ".task_outputs" / "tool-results",
        ),
    )
    return register_m4_tools(
        create_coding_tool_registry(workspace_root),
        todo_manager=todo_manager,
        memory_store=memory_store,
        task_store=task_store,
        context_manager=context_manager,
    )
