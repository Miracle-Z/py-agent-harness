from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import glob
import os
from pathlib import Path
import re
from typing import Any

from agent_harness.tools.base import ToolResult
from agent_harness.tools.workspace import (
    IGNORED_DIR_NAMES,
    ensure_inside_root,
    relative_workspace_path,
    resolve_workspace_path,
)


@dataclass
class GlobTool:
    # 学习说明：glob 解决“先找文件再决定读哪个”的问题，是 Coding Agent 的观察工具。
    root: Path = field(default_factory=Path.cwd)
    ignored_dirs: frozenset[str] = IGNORED_DIR_NAMES
    name: str = "glob"
    description: str = "Find workspace files or directories matching a glob pattern."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Workspace-relative glob pattern, such as '**/*.py'.",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum matches to return. Defaults to 200.",
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        }
    )

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    async def run(self, pattern: str, max_results: int = 200) -> ToolResult:
        if max_results < 1:
            msg = "max_results must be >= 1"
            raise ValueError(msg)
        self._validate_pattern(pattern)

        matches: list[str] = []
        truncated = False
        # 学习说明：搜索类工具默认跳过缓存、虚拟环境和 .git，减少噪音和上下文浪费。
        for match in glob.iglob(pattern, root_dir=self.root, recursive=True, include_hidden=True):
            if self._has_ignored_part(match):
                continue
            try:
                target = resolve_workspace_path(self.root, match)
            except PermissionError:
                continue
            if len(matches) >= max_results:
                truncated = True
                break
            matches.append(relative_workspace_path(self.root, target))

        return ToolResult(
            output={
                "pattern": pattern,
                "matches": sorted(matches),
                "count": len(matches),
                "truncated": truncated,
            }
        )

    def _validate_pattern(self, pattern: str) -> None:
        # 学习说明：glob pattern 也要限制在 workspace 内，不能用绝对路径或 .. 绕出去。
        path = Path(pattern)
        if path.is_absolute() or ".." in path.parts:
            msg = f"Glob pattern must stay inside workspace: {pattern}"
            raise PermissionError(msg)

    def _has_ignored_part(self, path: str) -> bool:
        return any(part in self.ignored_dirs for part in Path(path).parts)


@dataclass
class SearchTextTool:
    # 学习说明：search_text 是 grep/ripgrep 的教学版封装。
    # 返回 path + line_number + line，方便模型定位后再调用 read_file 或 edit_file。
    root: Path = field(default_factory=Path.cwd)
    ignored_dirs: frozenset[str] = IGNORED_DIR_NAMES
    max_line_chars: int = 500
    name: str = "search_text"
    description: str = "Search workspace text files for a literal string or regex."
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text or regex pattern to find."},
                "path": {
                    "type": "string",
                    "description": "Workspace-relative file or directory to search. Defaults to '.'.",
                },
                "regex": {"type": "boolean", "description": "Treat query as a regular expression."},
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Use case-sensitive matching. Defaults to true.",
                },
                "include_glob": {
                    "type": "string",
                    "description": "Optional glob filter for relative file paths, such as '*.py'.",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Maximum matches to return. Defaults to 100.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        }
    )

    def __post_init__(self) -> None:
        self.root = self.root.resolve()

    async def run(
        self,
        query: str,
        path: str = ".",
        regex: bool = False,
        case_sensitive: bool = True,
        include_glob: str | None = None,
        max_results: int = 100,
    ) -> ToolResult:
        if not query:
            msg = "query must not be empty"
            raise ValueError(msg)
        if max_results < 1:
            msg = "max_results must be >= 1"
            raise ValueError(msg)

        target = resolve_workspace_path(self.root, path)
        # 学习说明：literal 和 regex 共用同一个 matcher，工具接口保持简单。
        matcher = self._build_matcher(query, regex=regex, case_sensitive=case_sensitive)
        results: list[dict[str, Any]] = []
        truncated = False

        for file_path in self._iter_text_files(target):
            relative_path = relative_workspace_path(self.root, file_path)
            if include_glob and not fnmatch.fnmatch(relative_path, include_glob):
                continue

            for line_number, line in enumerate(
                file_path.read_text(encoding="utf-8", errors="replace").splitlines(),
                start=1,
            ):
                if not matcher(line):
                    continue
                if len(results) >= max_results:
                    truncated = True
                    break
                results.append(
                    {
                        "path": relative_path,
                        "line_number": line_number,
                        "line": self._trim_line(line),
                    }
                )
            if truncated:
                break

        return ToolResult(
            output={
                "query": query,
                "matches": results,
                "count": len(results),
                "truncated": truncated,
            }
        )

    def _build_matcher(
        self,
        query: str,
        *,
        regex: bool,
        case_sensitive: bool,
    ) -> Any:
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            compiled = re.compile(query, flags)
            return lambda line: compiled.search(line) is not None

        needle = query if case_sensitive else query.casefold()
        return lambda line: needle in (line if case_sensitive else line.casefold())

    def _iter_text_files(self, target: Path) -> list[Path]:
        # 学习说明：搜索目录时逐层 walk，并在进入子目录前过滤掉不该读的目录。
        if target.is_file():
            return [target] if self._is_probably_text(target) else []
        if not target.is_dir():
            msg = f"Path is not a file or directory: {target}"
            raise FileNotFoundError(msg)

        files: list[Path] = []
        for current, dirnames, filenames in os.walk(target):
            current_path = Path(current)
            dirnames[:] = sorted(
                dirname
                for dirname in dirnames
                if dirname not in self.ignored_dirs and self._is_safe_child(current_path / dirname)
            )
            for filename in sorted(filenames):
                file_path = current_path / filename
                if self._is_safe_child(file_path) and self._is_probably_text(file_path):
                    files.append(file_path)
        return files

    def _is_safe_child(self, path: Path) -> bool:
        try:
            ensure_inside_root(self.root, path)
        except PermissionError:
            return False
        return True

    def _is_probably_text(self, path: Path) -> bool:
        # 学习说明：只读文件头判断是否像文本文件，避免把二进制内容塞进模型上下文。
        try:
            with path.open("rb") as handle:
                return b"\0" not in handle.read(1024)
        except OSError:
            return False

    def _trim_line(self, line: str) -> str:
        if len(line) <= self.max_line_chars:
            return line
        return f"{line[: self.max_line_chars]}..."
