from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
from typing import Any

from agent_harness.tools.base import ToolResult
from agent_harness.tools.workspace import relative_workspace_path, resolve_workspace_path, trim_text


@dataclass
class GitDiffTool:
    # 学习说明：git_diff 是 Coding Agent 的自检工具。
    # 修改文件后让模型看 diff，比只相信工具返回“写入成功”更可靠。
    root: Path = field(default_factory=Path.cwd)
    max_output_chars: int = 50_000
    name: str = "git_diff"
    description: str = "Show git diff for the workspace or a workspace-relative path."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Optional workspace-relative path to diff.",
                },
                "staged": {"type": "boolean", "description": "Show staged diff."},
                "stat": {"type": "boolean", "description": "Show diff stat instead of patch."},
            },
            "additionalProperties": False,
        }
    )

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    async def run(
        self,
        path: str | None = None,
        staged: bool = False,
        stat: bool = False,
    ) -> ToolResult:
        # 学习说明：这里手工拼 argv，不通过 shell，避免 diff path 被当作命令片段解释。
        args = ["git", "diff"]
        if staged:
            args.append("--staged")
        if stat:
            args.append("--stat")
        if path:
            target = resolve_workspace_path(self.root, path)
            args.extend(["--", relative_workspace_path(self.root, target)])

        process = subprocess.run(
            args,
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        stdout, stdout_truncated = trim_text(process.stdout, self.max_output_chars)
        stderr, stderr_truncated = trim_text(process.stderr, self.max_output_chars)
        return ToolResult(
            output={
                "command": " ".join(args),
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": stdout_truncated or stderr_truncated,
            }
        )
