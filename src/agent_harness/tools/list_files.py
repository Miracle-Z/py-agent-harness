from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_harness.tools.base import ToolResult
from agent_harness.tools.workspace import resolve_workspace_path


@dataclass
class ListFilesTool:
    # 学习说明：这是 M1 用来验证 Tool Calling 闭环的最小工具。
    # 模型只看到 name、description 和 input_schema；真正的文件系统访问由 Harness 执行。
    root: Path = field(default_factory=Path.cwd)
    name: str = "list_files"
    description: str = "List files in a workspace-relative directory."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Workspace-relative directory to list. Defaults to the workspace root.",
                }
            },
            "additionalProperties": False,
        }
    )

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    async def run(self, path: str = ".") -> ToolResult:
        # 学习说明：工具必须自己做边界检查。LLM 只能提出动作请求，
        # Harness 决定这个请求是否安全、是否允许执行。
        target = resolve_workspace_path(self.root, path)

        if not target.exists():
            msg = f"Path does not exist: {path}"
            raise FileNotFoundError(msg)
        if not target.is_dir():
            msg = f"Path is not a directory: {path}"
            raise NotADirectoryError(msg)

        files = [entry.relative_to(self.root).as_posix() for entry in sorted(target.iterdir())]
        return ToolResult(output=files)
