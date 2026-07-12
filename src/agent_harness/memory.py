from __future__ import annotations

import builtins
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import unicodedata
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator

from agent_harness.tools.base import ToolResult


class MemoryType(StrEnum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


class MemoryRecord(BaseModel):
    """A durable, human-readable memory entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str
    name: str
    description: str
    type: MemoryType
    body: str

    @field_validator("name", "description", "body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "Memory fields must not be blank"
            raise ValueError(msg)
        return normalized

    @field_validator("name")
    @classmethod
    def _name_is_bounded(cls, value: str) -> str:
        if len(value) > 200:
            msg = "Memory name exceeds 200 characters"
            raise ValueError(msg)
        if "\n" in value or "\r" in value:
            msg = "Memory name must be a single line"
            raise ValueError(msg)
        return value

    @field_validator("description")
    @classmethod
    def _description_is_bounded(cls, value: str) -> str:
        if len(value) > 1_000:
            msg = "Memory description exceeds 1000 characters"
            raise ValueError(msg)
        if "\n" in value or "\r" in value:
            msg = "Memory description must be a single line"
            raise ValueError(msg)
        return value

    @field_validator("body")
    @classmethod
    def _body_is_bounded(cls, value: str) -> str:
        if len(value) > 100_000:
            msg = "Memory body exceeds 100000 characters"
            raise ValueError(msg)
        return value


class MemoryStore:
    """Markdown memory files plus a small, stable ``MEMORY.md`` index."""

    index_filename = "MEMORY.md"

    def __init__(self, directory: Path | str, *, max_body_chars: int = 100_000) -> None:
        self.directory = Path(directory).resolve()
        self.max_body_chars = max_body_chars

    @property
    def index_path(self) -> Path:
        return self.directory / self.index_filename

    def write(
        self,
        name: str,
        description: str,
        body: str,
        memory_type: MemoryType | str = MemoryType.PROJECT,
    ) -> MemoryRecord:
        if len(body) > self.max_body_chars:
            msg = f"Memory body exceeds {self.max_body_chars} characters"
            raise ValueError(msg)
        slug = self._available_slug(name)
        record = MemoryRecord(
            slug=slug,
            name=name,
            description=description,
            type=MemoryType(memory_type),
            body=body,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self._path(record.slug), _serialize_memory(record))
        self._rebuild_index()
        return record

    def get(self, name_or_slug: str) -> MemoryRecord:
        requested = name_or_slug.strip()
        if not requested:
            msg = "Memory name must not be blank"
            raise ValueError(msg)
        slug = _slugify(requested)
        path = self._path(slug)
        if path.exists():
            return self._read(path)
        for record in self.list():
            if record.name.casefold() == requested.casefold():
                return record
        msg = f"Memory not found: {name_or_slug}"
        raise FileNotFoundError(msg)

    def list(self) -> list[MemoryRecord]:
        if not self.directory.exists():
            return []
        records = [
            self._read(path)
            for path in self.directory.glob("*.md")
            if path.name.casefold() != self.index_filename.casefold()
        ]
        return sorted(records, key=lambda memory: (memory.name.casefold(), memory.slug))

    def search(
        self,
        query: str,
        *,
        max_items: int = 5,
    ) -> builtins.list[MemoryRecord]:
        if max_items < 0:
            msg = "max_items must be non-negative"
            raise ValueError(msg)
        records = self.list()
        normalized_query = query.strip().casefold()
        if not normalized_query:
            return records[:max_items]

        tokens = _search_terms(normalized_query)

        def score(record: MemoryRecord) -> tuple[int, str]:
            title = f"{record.name} {record.description}".casefold()
            complete = f"{title} {record.body}".casefold()
            value = 0
            if normalized_query in title:
                value += 20
            elif normalized_query in complete:
                value += 10
            value += sum(4 for token in tokens if token in title)
            value += sum(1 for token in tokens if token in complete)
            return value, record.name.casefold()

        ranked = sorted(records, key=lambda record: (-score(record)[0], score(record)[1]))
        return [record for record in ranked if score(record)[0] > 0][:max_items]

    def index_text(self) -> str:
        if self.index_path.is_symlink():
            msg = "Unsafe memory index path"
            raise PermissionError(msg)
        records = self.list()
        if not records:
            return ""
        canonical = _render_index(records)
        current = (
            self.index_path.read_text(encoding="utf-8")
            if self.index_path.exists()
            else ""
        )
        if current != canonical:
            self._atomic_write(self.index_path, canonical)
        return canonical.strip()

    def delete(self, name_or_slug: str) -> bool:
        try:
            record = self.get(name_or_slug)
        except FileNotFoundError:
            return False
        self._path(record.slug).unlink()
        self._rebuild_index()
        return True

    def _read(self, path: Path) -> MemoryRecord:
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory):
            msg = f"Unsafe memory path: {path.name}"
            raise PermissionError(msg)
        try:
            record = _parse_memory(path.read_text(encoding="utf-8"), slug=path.stem)
        except (OSError, ValueError, KeyError) as exc:
            msg = f"Invalid memory file: {path.name}: {exc}"
            raise ValueError(msg) from exc
        if len(record.body) > self.max_body_chars:
            msg = f"Invalid memory file: {path.name}: body is too large"
            raise ValueError(msg)
        return record

    def _path(self, slug: str) -> Path:
        if not re.fullmatch(r"[\w-]+", slug, flags=re.UNICODE):
            msg = f"Invalid memory slug: {slug!r}"
            raise ValueError(msg)
        path = self.directory / f"{slug}.md"
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory):
            msg = f"Unsafe memory path: {slug}"
            raise PermissionError(msg)
        return path

    def _rebuild_index(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.index_path, _render_index(self.list()))

    def _available_slug(self, name: str) -> str:
        slug = _slugify(name)
        path = self._path(slug)
        if not path.exists():
            return slug
        existing = self._read(path)
        if existing.name.casefold() == name.strip().casefold():
            return slug
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
        return f"{slug[:80]}-{digest}"

    def _atomic_write(self, path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    slug = re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE).strip("-_")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    if not slug:
        return f"memory-entry-{digest}"
    if slug.casefold() == "memory":
        return f"memory-entry-{digest}"
    if len(slug) > 80:
        return f"{slug[:80]}-{digest}"
    return slug


def _search_terms(value: str) -> list[str]:
    terms = re.findall(r"[a-z0-9_]+", value.casefold())
    for segment in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", value):
        if len(segment) == 1:
            terms.append(segment)
        else:
            terms.extend(segment[index : index + 2] for index in range(len(segment) - 1))
    return list(dict.fromkeys(terms))


def _render_index(records: list[MemoryRecord], *, max_items: int = 200) -> str:
    lines = ["# Memory Index", ""]
    for record in records[:max_items]:
        name = _escape_markdown(record.name)
        description = _escape_markdown(record.description)
        lines.append(
            f"- [{name}]({record.slug}.md) — {description} ({record.type.value})"
        )
    if len(records) > max_items:
        lines.append(f"- ... {len(records) - max_items} more memories; use memory_search")
    return "\n".join(lines).rstrip() + "\n"


def _escape_markdown(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "[]()":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _serialize_memory(record: MemoryRecord) -> str:
    return (
        "---\n"
        f"name: {json.dumps(record.name, ensure_ascii=False)}\n"
        f"description: {json.dumps(record.description, ensure_ascii=False)}\n"
        f"type: {record.type.value}\n"
        "---\n\n"
        f"{record.body.rstrip()}\n"
    )


def _parse_memory(content: str, *, slug: str) -> MemoryRecord:
    if not content.startswith("---\n"):
        msg = "missing frontmatter"
        raise ValueError(msg)
    parts = content.split("---\n", 2)
    if len(parts) != 3:
        msg = "unterminated frontmatter"
        raise ValueError(msg)
    metadata: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if value.startswith('"'):
            value = str(json.loads(value))
        metadata[key.strip()] = value
    return MemoryRecord(
        slug=slug,
        name=metadata["name"],
        description=metadata["description"],
        type=MemoryType(metadata["type"]),
        body=parts[2].strip(),
    )


def _memory_output(record: MemoryRecord) -> dict[str, str]:
    return record.model_dump(mode="json")


class MemoryWriteTool:
    name = "memory_write"
    description = (
        "Persist a durable user preference, feedback item, project fact, or reference "
        "that should survive future sessions."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string", "minLength": 1},
            "type": {
                "type": "string",
                "enum": [memory_type.value for memory_type in MemoryType],
            },
            "body": {"type": "string", "minLength": 1},
        },
        "required": ["name", "description", "type", "body"],
        "additionalProperties": False,
    }

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def run(self, name: str, description: str, type: str, body: str) -> ToolResult:
        return ToolResult(
            output=_memory_output(self.store.write(name, description, body, type))
        )


class MemoryReadTool:
    name = "memory_read"
    description = "Load one durable memory by its name or slug."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    }

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def run(self, name: str) -> ToolResult:
        return ToolResult(output=_memory_output(self.store.get(name)))


class MemorySearchTool:
    name = "memory_search"
    description = "Search durable memories and return up to five relevant full entries."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def run(self, query: str, max_items: int = 5) -> ToolResult:
        capped = min(max(max_items, 1), 5)
        matches = self.store.search(query, max_items=capped)
        return ToolResult(
            output={
                "count": len(matches),
                "memories": [_memory_output(record) for record in matches],
            }
        )


__all__ = [
    "MemoryReadTool",
    "MemoryRecord",
    "MemorySearchTool",
    "MemoryStore",
    "MemoryType",
    "MemoryWriteTool",
]
