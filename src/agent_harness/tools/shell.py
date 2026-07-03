from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shlex
import subprocess
from typing import Any

from agent_harness.tools.base import ToolResult
from agent_harness.tools.workspace import relative_workspace_path, resolve_workspace_path, trim_text

SHELL_CONTROL_SNIPPETS: tuple[str, ...] = ("&&", "||", "|", ";", ">", "<", "`", "$(", "\n")
# 学习说明：M2 的 shell 工具是“受限制命令执行”，不是完整 Bash。
# 禁止管道、重定向、命令拼接和明显危险命令，后续 M3 再接入人工审批。
DANGEROUS_EXECUTABLES: frozenset[str] = frozenset(
    {"dd", "mkfs", "reboot", "rm", "shutdown", "sudo"}
)
DANGEROUS_SNIPPETS: tuple[str, ...] = (
    "chmod 777",
    "chown ",
    "git clean",
    "git reset --hard",
    "rm -rf",
)


def run_restricted_command(
    *,
    root: Path,
    command: str,
    cwd: str = ".",
    timeout_seconds: int = 120,
    max_timeout_seconds: int = 120,
    max_output_chars: int = 50_000,
) -> dict[str, Any]:
    # 学习说明：ShellTool 和 RunTestsTool 共用这段执行逻辑，避免安全边界写两份。
    if not command.strip():
        msg = "command must not be empty"
        raise ValueError(msg)
    _validate_command(command)

    working_dir = resolve_workspace_path(root, cwd)
    if not working_dir.is_dir():
        msg = f"cwd is not a directory: {cwd}"
        raise NotADirectoryError(msg)

    timeout = min(max(1, timeout_seconds), max_timeout_seconds)
    # 学习说明：不用 shell=True，而是 shlex.split 后以 argv 运行，降低注入风险。
    argv = shlex.split(command)
    try:
        process = subprocess.run(
            argv,
            cwd=working_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _coerce_timeout_output(exc.stdout)
        stderr = _coerce_timeout_output(exc.stderr)
        stdout, stdout_truncated = trim_text(stdout, max_output_chars)
        stderr, stderr_truncated = trim_text(stderr, max_output_chars)
        return {
            "command": command,
            "cwd": relative_workspace_path(root, working_dir),
            "exit_code": None,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": True,
            "timeout_seconds": timeout,
            "truncated": stdout_truncated or stderr_truncated,
        }
    except OSError as exc:
        return {
            "command": command,
            "cwd": relative_workspace_path(root, working_dir),
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "timed_out": False,
            "timeout_seconds": timeout,
            "truncated": False,
        }

    stdout, stdout_truncated = trim_text(process.stdout, max_output_chars)
    stderr, stderr_truncated = trim_text(process.stderr, max_output_chars)
    # 学习说明：返回结构化 stdout/stderr/exit_code，模型能区分命令失败和无输出。
    return {
        "command": command,
        "cwd": relative_workspace_path(root, working_dir),
        "exit_code": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": False,
        "timeout_seconds": timeout,
        "truncated": stdout_truncated or stderr_truncated,
    }


def _validate_command(command: str) -> None:
    # 学习说明：这里是硬拦截。它不是完整权限系统，只覆盖 M2 阶段最常见风险。
    if any(snippet in command for snippet in SHELL_CONTROL_SNIPPETS):
        msg = "Shell control operators are not allowed in restricted commands"
        raise PermissionError(msg)

    lowered = command.lower()
    if any(snippet in lowered for snippet in DANGEROUS_SNIPPETS):
        msg = f"Dangerous command blocked: {command}"
        raise PermissionError(msg)

    argv = shlex.split(command)
    if not argv:
        msg = "command must not be empty"
        raise ValueError(msg)
    executable = Path(argv[0]).name.lower()
    if executable in DANGEROUS_EXECUTABLES:
        msg = f"Executable is blocked: {executable}"
        raise PermissionError(msg)


def _coerce_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


@dataclass
class ShellTool:
    # 学习说明：shell 给 Agent 执行通用命令的能力，但必须受 cwd、timeout、输出大小约束。
    root: Path = field(default_factory=Path.cwd)
    max_timeout_seconds: int = 120
    max_output_chars: int = 50_000
    name: str = "shell"
    description: str = "Run a restricted command in the workspace without shell operators."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command and arguments, for example 'uv run pytest'.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Workspace-relative working directory. Defaults to '.'.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Timeout capped by the harness. Defaults to 120.",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        }
    )

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    async def run(self, command: str, cwd: str = ".", timeout_seconds: int = 120) -> ToolResult:
        return ToolResult(
            output=run_restricted_command(
                root=self.root,
                command=command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_timeout_seconds=self.max_timeout_seconds,
                max_output_chars=self.max_output_chars,
            )
        )


@dataclass
class RunTestsTool:
    # 学习说明：把“运行测试”做成专用工具，可以让模型优先选择验证动作，
    # 同时保留覆盖 command 的能力，方便不同项目使用不同测试命令。
    root: Path = field(default_factory=Path.cwd)
    default_command: str = "uv run pytest"
    max_timeout_seconds: int = 300
    max_output_chars: int = 50_000
    name: str = "run_tests"
    description: str = "Run the project test command in the workspace."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Test command. Defaults to 'uv run pytest'.",
                },
                "cwd": {
                    "type": "string",
                    "description": "Workspace-relative working directory. Defaults to '.'.",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Timeout capped by the harness. Defaults to 300.",
                },
            },
            "additionalProperties": False,
        }
    )

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    async def run(
        self,
        command: str | None = None,
        cwd: str = ".",
        timeout_seconds: int = 300,
    ) -> ToolResult:
        return ToolResult(
            output=run_restricted_command(
                root=self.root,
                command=command or self.default_command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                max_timeout_seconds=self.max_timeout_seconds,
                max_output_chars=self.max_output_chars,
            )
        )
