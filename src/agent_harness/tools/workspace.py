from __future__ import annotations

from pathlib import Path

IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".memory",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".sessions",
        ".svn",
        ".task_outputs",
        ".tasks",
        ".transcripts",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)


def ensure_inside_root(root: Path, path: Path) -> Path:
    # 学习说明：所有文件工具都必须先过 workspace 边界检查。
    # resolve() 会处理 ../ 和符号链接；relative_to(root) 失败就代表逃出了工作区。
    root = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        msg = f"Path escapes tool root: {resolved}"
        raise PermissionError(msg) from exc
    return resolved


def resolve_workspace_path(root: Path, path: str | Path = ".") -> Path:
    # 学习说明：模型通常传相对路径；这里统一转成绝对路径并复用边界检查。
    root = root.resolve()
    raw_path = Path(path)
    target = raw_path if raw_path.is_absolute() else root / raw_path
    return ensure_inside_root(root, target)


def relative_workspace_path(root: Path, path: Path) -> str:
    # 学习说明：工具结果返回相对路径，避免把本机绝对路径暴露进模型上下文。
    return path.resolve().relative_to(root.resolve()).as_posix()


def trim_text(value: str, max_chars: int) -> tuple[str, bool]:
    # 学习说明：工具输出会进入上下文，过大的 stdout/diff/文件内容必须截断。
    # 返回 truncated 标记，让模型知道它看到的不是完整结果。
    if len(value) <= max_chars:
        return value, False
    return value[:max_chars], True
