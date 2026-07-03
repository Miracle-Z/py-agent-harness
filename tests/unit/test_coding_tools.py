from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from agent_harness.tools import (
    EditFileTool,
    GitDiffTool,
    GlobTool,
    ReadFileTool,
    RunTestsTool,
    SearchTextTool,
    ShellTool,
    WriteFileTool,
    create_coding_tool_registry,
)


@pytest.mark.asyncio
async def test_file_tools_read_write_and_edit_within_workspace(tmp_path: Path) -> None:
    write_result = await WriteFileTool(root=tmp_path).run(
        "pkg/example.py",
        "one\ntwo\ntwo\n",
    )

    assert write_result.output["created"] is True
    assert write_result.output["path"] == "pkg/example.py"

    read_result = await ReadFileTool(root=tmp_path).run(
        "pkg/example.py",
        start_line=2,
        limit=1,
    )
    assert read_result.output["content"] == "two"
    assert read_result.output["truncated"] is True

    edit_result = await EditFileTool(root=tmp_path).run("pkg/example.py", "two", "TWO")
    assert edit_result.output["remaining_occurrences"] == 1
    assert (tmp_path / "pkg/example.py").read_text() == "one\nTWO\ntwo\n"


@pytest.mark.asyncio
async def test_file_tools_reject_paths_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="escapes tool root"):
        await WriteFileTool(root=tmp_path).run("../outside.txt", "nope")


@pytest.mark.asyncio
async def test_search_tools_find_files_and_text(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("def handler():\n    return 'ok'\n")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv/ignored.py").write_text("def ignored():\n    pass\n")

    glob_result = await GlobTool(root=tmp_path).run("**/*.py")
    assert glob_result.output["matches"] == ["src/app.py"]

    search_result = await SearchTextTool(root=tmp_path).run(
        "handler",
        include_glob="*.py",
    )
    assert search_result.output["matches"] == [
        {"path": "src/app.py", "line_number": 1, "line": "def handler():"}
    ]


@pytest.mark.asyncio
async def test_shell_tool_runs_simple_commands_and_blocks_shell_operators(tmp_path: Path) -> None:
    result = await ShellTool(root=tmp_path).run("pwd")

    assert result.output["exit_code"] == 0
    assert Path(result.output["stdout"].strip()).resolve() == tmp_path.resolve()

    with pytest.raises(PermissionError, match="Shell control operators"):
        await ShellTool(root=tmp_path).run("echo ok && echo nope")

    with pytest.raises(PermissionError, match="Dangerous command blocked"):
        await ShellTool(root=tmp_path).run("rm -rf .")


@pytest.mark.asyncio
async def test_run_tests_tool_uses_default_command(tmp_path: Path) -> None:
    result = await RunTestsTool(root=tmp_path, default_command="pwd").run()

    assert result.output["command"] == "pwd"
    assert result.output["exit_code"] == 0


@pytest.mark.asyncio
async def test_git_diff_tool_returns_workspace_diff(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "example.txt").write_text("hello\n")
    subprocess.run(["git", "add", "example.txt"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "example.txt").write_text("hello world\n")

    result = await GitDiffTool(root=tmp_path).run(path="example.txt")

    assert result.output["exit_code"] == 0
    assert "-hello" in result.output["stdout"]
    assert "+hello world" in result.output["stdout"]


def test_coding_tool_registry_contains_m2_tools(tmp_path: Path) -> None:
    registry = create_coding_tool_registry(tmp_path)

    assert {tool.name for tool in registry.list()} == {
        "edit_file",
        "git_diff",
        "glob",
        "list_files",
        "read_file",
        "run_tests",
        "search_text",
        "shell",
        "write_file",
    }
