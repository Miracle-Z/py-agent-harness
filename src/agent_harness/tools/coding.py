from __future__ import annotations

from pathlib import Path

from agent_harness.tools.base import Tool
from agent_harness.tools.file_io import EditFileTool, ReadFileTool, WriteFileTool
from agent_harness.tools.git_tools import GitDiffTool
from agent_harness.tools.list_files import ListFilesTool
from agent_harness.tools.registry import ToolRegistry
from agent_harness.tools.search import GlobTool, SearchTextTool
from agent_harness.tools.shell import RunTestsTool, ShellTool


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
