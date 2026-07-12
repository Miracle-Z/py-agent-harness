from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from agent_harness.llm.base import LLMClient
from agent_harness.messages.models import Message
from agent_harness.tools.base import ToolResult


SummaryFunction = Callable[..., str | Awaitable[str]]


@dataclass(frozen=True)
class ContextConfig:
    max_messages: int = 50
    keep_head_messages: int = 3
    keep_recent_tool_results: int = 3
    micro_compact_min_chars: int = 120
    tool_result_batch_budget_chars: int = 200_000
    persist_tool_result_min_chars: int = 30_000
    tool_result_preview_chars: int = 2_000
    auto_compact_threshold_chars: int = 50_000
    summary_max_chars: int = 8_000
    reactive_keep_messages: int = 5

    def __post_init__(self) -> None:
        positive_fields = (
            "max_messages",
            "keep_recent_tool_results",
            "micro_compact_min_chars",
            "tool_result_batch_budget_chars",
            "persist_tool_result_min_chars",
            "tool_result_preview_chars",
            "auto_compact_threshold_chars",
            "summary_max_chars",
            "reactive_keep_messages",
        )
        for name in positive_fields:
            if getattr(self, name) < 1:
                msg = f"{name} must be positive"
                raise ValueError(msg)
        if self.keep_head_messages < 0:
            msg = "keep_head_messages must not be negative"
            raise ValueError(msg)


@dataclass(frozen=True)
class CompactionResult:
    messages: list[Message]
    stages: tuple[str, ...] = ()
    transcript_path: Path | None = None

    @property
    def changed(self) -> bool:
        return bool(self.stages)


class ContextManager:
    """Cheap-first context compaction with protocol-safe message grouping."""

    def __init__(
        self,
        *,
        transcript_dir: Path | str,
        tool_output_dir: Path | str,
        config: ContextConfig | None = None,
        summarizer: SummaryFunction | None = None,
    ) -> None:
        self.transcript_dir = Path(transcript_dir).resolve()
        self.tool_output_dir = Path(tool_output_dir).resolve()
        self.config = config or ContextConfig()
        self.summarizer: SummaryFunction = summarizer or self._default_summary
        self._requested_focus: str | None = None
        self._compaction_requested = False

    def request_compaction(self, focus: str | None = None) -> None:
        self._compaction_requested = True
        self._requested_focus = focus.strip() if focus and focus.strip() else None

    async def prepare(self, messages: Sequence[Message]) -> CompactionResult:
        """Run budget -> snip -> micro -> summary before an LLM call."""

        original = [message.model_copy(deep=True) for message in messages]
        working = [message.model_copy(deep=True) for message in messages]
        stages: list[str] = []

        working, budget_changed = self._tool_result_budget(working)
        if budget_changed:
            stages.append("tool_result_budget")

        working, snip_changed = self._snip(working)
        if snip_changed:
            stages.append("snip")

        working, micro_changed = self._micro_compact(working)
        if micro_changed:
            stages.append("micro")

        transcript: Path | None = None
        if budget_changed or snip_changed or micro_changed:
            # Cheap transforms are still lossy. Preserve the pre-transform history before
            # a resumable Session checkpoints the compacted active context.
            transcript = self.write_transcript(original)

        force = self._compaction_requested
        focus = self._requested_focus
        if not force and estimate_context_chars(working) <= self.config.auto_compact_threshold_chars:
            return CompactionResult(working, tuple(stages), transcript)

        if transcript is None:
            transcript = self.write_transcript(original)
        try:
            summary = await self._summarize(working, focus)
        except Exception:
            # A failed manual request remains pending so a later turn can retry.
            if force:
                self._compaction_requested = True
                self._requested_focus = focus
            raise
        if not summary.strip():
            msg = "Context summarizer returned an empty summary"
            raise ValueError(msg)

        self._compaction_requested = False
        self._requested_focus = None
        system_messages = [
            message.model_copy(deep=True) for message in working if message.role == "system"
        ]
        compacted = [
            *system_messages,
            Message(role="user", content=f"[Compacted context]\n\n{summary.strip()}"),
        ]
        stages.append("summary")
        return CompactionResult(compacted, tuple(stages), transcript)

    async def reactive(self, messages: Sequence[Message]) -> CompactionResult:
        """Emergency prefix summary that preserves a protocol-safe recent tail."""

        original = [message.model_copy(deep=True) for message in messages]
        transcript = self.write_transcript(original)
        system_messages = [message for message in original if message.role == "system"]
        groups = _atomic_non_system_groups(original)

        tail: list[list[Message]] = []
        tail_size = 0
        for group in reversed(groups):
            if tail and tail_size >= self.config.reactive_keep_messages:
                break
            tail.append(group)
            tail_size += len(group)
        tail.reverse()
        tail_ids = {id(message) for group in tail for message in group}
        prefix = [
            message
            for group in groups
            for message in group
            if id(message) not in tail_ids
        ]
        summary_source = prefix or [
            Message(role="user", content="Earlier context was too large for the model.")
        ]
        summary = await self._summarize(summary_source, "recover from context overflow")
        tail_messages = [
            message.model_copy(deep=True) for group in tail for message in group
        ]
        summary_content = f"[Reactive compact]\n\n{summary.strip()}"
        if tail_messages and tail_messages[0].role == "user":
            first = tail_messages[0]
            tail_messages[0] = first.model_copy(
                update={"content": f"{summary_content}\n\n[Recent user turn]\n{first.content}"}
            )
            summary_message: list[Message] = []
        else:
            summary_message = [Message(role="user", content=summary_content)]
        compacted = [
            *[message.model_copy(deep=True) for message in system_messages],
            *summary_message,
            *tail_messages,
        ]
        return CompactionResult(compacted, ("reactive",), transcript)

    def write_transcript(self, messages: Sequence[Message]) -> Path:
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = self.transcript_dir / f"transcript-{timestamp}-{uuid4().hex[:8]}.jsonl"
        temporary = path.with_name(f".{path.name}.tmp")
        content = "".join(
            json.dumps(message.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for message in messages
        )
        try:
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def _tool_result_budget(
        self,
        messages: list[Message],
    ) -> tuple[list[Message], bool]:
        trailing_indices: list[int] = []
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role != "tool":
                break
            trailing_indices.append(index)
        trailing_indices.reverse()
        total = sum(len(messages[index].content) for index in trailing_indices)
        if total <= self.config.tool_result_batch_budget_chars:
            return messages, False

        changed = False
        ranked = sorted(trailing_indices, key=lambda index: len(messages[index].content), reverse=True)
        for index in ranked:
            if total <= self.config.tool_result_batch_budget_chars:
                break
            message = messages[index]
            if len(message.content) <= self.config.persist_tool_result_min_chars:
                continue
            reference = self._persist_tool_output(message)
            preview = message.content[: self.config.tool_result_preview_chars]
            replacement = (
                f"[Large tool result persisted at {reference}]\n"
                f"Preview:\n{preview}"
            )
            total -= len(message.content) - len(replacement)
            messages[index] = message.model_copy(update={"content": replacement})
            changed = True
        return messages, changed

    def _persist_tool_output(self, message: Message) -> str:
        self.tool_output_dir.mkdir(parents=True, exist_ok=True)
        raw_id = message.tool_call_id or "unknown"
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_id).strip("-.") or "unknown"
        digest = hashlib.sha256(message.content.encode("utf-8")).hexdigest()[:12]
        path = self.tool_output_dir / f"{safe_id[:80]}-{digest}.txt"
        if not path.exists():
            temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
            try:
                temporary.write_text(message.content, encoding="utf-8")
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return str(path)

    def _snip(self, messages: list[Message]) -> tuple[list[Message], bool]:
        if len(messages) <= self.config.max_messages:
            return messages, False

        systems = [message for message in messages if message.role == "system"]
        groups = _atomic_non_system_groups(messages)
        budget = max(self.config.max_messages - len(systems) - 1, 1)

        head: list[list[Message]] = []
        head_size = 0
        for group in groups:
            if head and head_size >= self.config.keep_head_messages:
                break
            head.append(group)
            head_size += len(group)

        selected_ids = {id(message) for group in head for message in group}
        tail_budget = max(budget - head_size, 1)
        tail: list[list[Message]] = []
        tail_size = 0
        for group in reversed(groups):
            if any(id(message) in selected_ids for message in group):
                break
            if tail and tail_size + len(group) > tail_budget:
                break
            tail.append(group)
            tail_size += len(group)
        tail.reverse()

        kept = head_size + tail_size
        non_system_count = sum(len(group) for group in groups)
        dropped = non_system_count - kept
        if dropped <= 0:
            return messages, False
        placeholder = Message(
            role="user",
            content=f"[Snipped {dropped} messages from the conversation middle.]",
        )
        compacted = [
            *[message.model_copy(deep=True) for message in systems],
            *[message for group in head for message in group],
            placeholder,
            *[message for group in tail for message in group],
        ]
        return compacted, True

    def _micro_compact(
        self,
        messages: list[Message],
    ) -> tuple[list[Message], bool]:
        indices = [index for index, message in enumerate(messages) if message.role == "tool"]
        old_indices = indices[: -self.config.keep_recent_tool_results]
        changed = False
        for index in old_indices:
            message = messages[index]
            if len(message.content) <= self.config.micro_compact_min_chars:
                continue
            messages[index] = message.model_copy(
                update={
                    "content": "[Earlier tool result compacted. Re-run the tool if needed.]"
                }
            )
            changed = True
        return messages, changed

    async def _summarize(self, messages: Sequence[Message], focus: str | None) -> str:
        try:
            parameter_count = len(inspect.signature(self.summarizer).parameters)
        except (TypeError, ValueError):
            parameter_count = 2
        if parameter_count == 1:
            result = self.summarizer(messages)
        else:
            result = self.summarizer(messages, focus)
        if inspect.isawaitable(result):
            result = await result
        return str(result)[: self.config.summary_max_chars]

    def _default_summary(self, messages: Sequence[Message], focus: str | None) -> str:
        non_system = [message for message in messages if message.role != "system"]
        user_messages = [
            message.content
            for message in non_system
            if message.role == "user" and not message.content.lstrip().startswith("<reminder>")
        ]
        lines = [
            "Continue the same coding task using this compacted state.",
            f"Current goal: {(user_messages[-1] if user_messages else 'Continue prior work')[:2000]}",
        ]
        if focus:
            lines.append(f"Compaction focus: {focus[:1000]}")

        tool_names = [
            tool_call.name
            for message in non_system
            if message.role == "assistant"
            for tool_call in message.tool_calls
        ]
        if tool_names:
            lines.append("Tools used: " + ", ".join(dict.fromkeys(tool_names)))

        lines.append("Recent state:")
        for message in non_system[-12:]:
            if message.role == "tool":
                detail = message.content[:300]
                lines.append(f"- tool {message.name or ''}: {detail}")
            else:
                detail = message.content[:700]
                if detail:
                    lines.append(f"- {message.role}: {detail}")
        lines.append(
            "Preserve user constraints, verified decisions, changed files, failures, and remaining work."
        )
        return "\n".join(lines)


class LLMContextSummarizer:
    """Use the configured model for the expensive fourth compaction layer."""

    def __init__(self, llm: LLMClient, *, max_input_chars: int = 80_000) -> None:
        self.llm = llm
        self.max_input_chars = max_input_chars

    async def __call__(self, messages: Sequence[Message], focus: str | None = None) -> str:
        transcript = "\n".join(
            json.dumps(message.model_dump(mode="json"), ensure_ascii=False)
            for message in messages
        )
        if len(transcript) > self.max_input_chars:
            head_size = self.max_input_chars // 4
            tail_size = self.max_input_chars - head_size
            transcript = (
                transcript[:head_size]
                + "\n[... middle omitted before summarization ...]\n"
                + transcript[-tail_size:]
            )
        focus_line = f"\nCompaction focus: {focus}" if focus else ""
        prompt = (
            "Summarize this coding-agent conversation so work can continue safely.\n"
            "Preserve the current goal, user constraints, verified findings and decisions, "
            "files read or changed, failed attempts, Todo/Task progress, and remaining work. "
            "Be compact but concrete; do not invent facts."
            f"{focus_line}\n\nConversation JSONL:\n{transcript}"
        )
        response = await self.llm.complete([Message(role="user", content=prompt)], tools=[])
        if not response.content.strip():
            msg = "LLM context summarizer returned an empty response"
            raise ValueError(msg)
        return response.content.strip()


class CompactTool:
    name = "compact"
    description = "Summarize earlier conversation before the next model call to free context."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"focus": {"type": "string"}},
        "additionalProperties": False,
    }

    def __init__(self, manager: ContextManager) -> None:
        self.manager = manager

    def run(self, focus: str | None = None) -> ToolResult:
        self.manager.request_compaction(focus)
        return ToolResult(output={"requested": True, "focus": focus})


def estimate_context_chars(messages: Sequence[Message]) -> int:
    return sum(
        len(message.content)
        + sum(
            len(tool_call.name)
            + len(json.dumps(tool_call.arguments, ensure_ascii=False, default=str))
            for tool_call in message.tool_calls
        )
        for message in messages
    )


def _atomic_non_system_groups(messages: Sequence[Message]) -> list[list[Message]]:
    non_system = [message for message in messages if message.role != "system"]
    groups: list[list[Message]] = []
    index = 0
    while index < len(non_system):
        message = non_system[index]
        if message.role == "assistant" and message.tool_calls:
            expected_ids = {tool_call.id for tool_call in message.tool_calls}
            group = [message]
            index += 1
            while index < len(non_system):
                candidate = non_system[index]
                if candidate.role != "tool" or candidate.tool_call_id not in expected_ids:
                    break
                group.append(candidate)
                index += 1
            groups.append(group)
            continue
        groups.append([message])
        index += 1
    return groups


__all__ = [
    "CompactTool",
    "CompactionResult",
    "ContextConfig",
    "ContextManager",
    "LLMContextSummarizer",
    "SummaryFunction",
    "estimate_context_chars",
]
