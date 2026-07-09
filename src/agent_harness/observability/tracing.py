from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from agent_harness.hooks import HookContext, HookEvent, HookManager


@dataclass(frozen=True)
class TraceEvent:
    name: str
    metadata: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None


class InMemoryTracer:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self._starts: dict[str, float] = {}

    def install(self, hooks: HookManager) -> None:
        hooks.register(HookEvent.PRE_LLM_CALL, self._record)
        hooks.register(HookEvent.POST_LLM_CALL, self._record)
        hooks.register(HookEvent.PRE_TOOL_USE, self._record)
        hooks.register(HookEvent.POST_TOOL_USE, self._record)
        hooks.register(HookEvent.STOP, self._record)
        hooks.register(HookEvent.ERROR, self._record)

    def drain(self) -> list[TraceEvent]:
        events = list(self.events)
        self.events.clear()
        return events

    def _record(self, context: HookContext) -> None:
        if context.event == HookEvent.PRE_LLM_CALL:
            key = self._llm_key(context)
            self._starts[key] = time.perf_counter()
            self.events.append(
                TraceEvent(
                    name="pre_llm_call",
                    metadata={"message_count": len(context.messages), **context.metadata},
                )
            )
            return

        if context.event == HookEvent.POST_LLM_CALL:
            key = self._llm_key(context)
            started_at = self._starts.pop(key, None)
            self.events.append(
                TraceEvent(
                    name="post_llm_call",
                    metadata={"message_count": len(context.messages), **context.metadata},
                    duration_ms=_duration_ms(started_at),
                )
            )
            return

        if context.event == HookEvent.PRE_TOOL_USE and context.tool_call:
            self._starts[context.tool_call.id] = time.perf_counter()
            self.events.append(
                TraceEvent(
                    name="pre_tool_use",
                    metadata={
                        "tool_name": context.tool_call.name,
                        "tool_call_id": context.tool_call.id,
                        **context.metadata,
                    },
                )
            )
            return

        if context.event == HookEvent.POST_TOOL_USE and context.tool_call:
            started_at = self._starts.pop(context.tool_call.id, None)
            self.events.append(
                TraceEvent(
                    name="post_tool_use",
                    metadata={
                        "tool_name": context.tool_call.name,
                        "tool_call_id": context.tool_call.id,
                        **context.metadata,
                    },
                    duration_ms=_duration_ms(started_at),
                )
            )
            return

        metadata: dict[str, Any] = dict(context.metadata)
        if context.error is not None:
            metadata["error_type"] = type(context.error).__name__
            metadata["error"] = str(context.error)
        self.events.append(TraceEvent(name=context.event.value, metadata=metadata))

    def _llm_key(self, context: HookContext) -> str:
        return f"llm:{context.metadata.get('tool_round', 0)}"


def _duration_ms(started_at: float | None) -> float | None:
    if started_at is None:
        return None
    return (time.perf_counter() - started_at) * 1000
