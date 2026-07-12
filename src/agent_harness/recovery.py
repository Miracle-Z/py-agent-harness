from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
import asyncio
import inspect
import random

from agent_harness.context import ContextManager
from agent_harness.llm.base import LLMClient
from agent_harness.messages.models import Message
from agent_harness.tools.base import ToolDefinition


class LLMErrorKind(StrEnum):
    # 对应 learn-claude-code s11_error_recovery：先把供应商异常归类，再决定是否可恢复。
    PROMPT_TOO_LONG = "prompt_too_long"
    RATE_LIMITED = "rate_limited"
    OVERLOADED = "overloaded"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RecoveryConfig:
    # 恢复策略的集中配置。AgentLoop 不关心这些细节，只把 LLM 调用交给 RecoveryManager。
    default_max_tokens: int = 8_000
    # 模型输出被 max_tokens 截断时，优先尝试把输出预算调大后重试。
    escalated_max_tokens: int = 64_000
    # 如果调大 max_tokens 后仍截断，最多追加 continuation prompt 续写几次。
    max_continuations: int = 3
    # 429/529 这类瞬态错误的最大重试次数。
    max_retries: int = 10
    # 指数退避的初始和最大等待时间。
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 32.0
    # 连续 overloaded 到达阈值后，允许切换到 fallback_model。
    max_consecutive_overloaded: int = 3
    fallback_model: str | None = None
    # reactive compact 时保留最近几条非 system 消息。
    compact_message_window: int = 5
    continuation_prompt: str = (
        "Output token limit hit. Resume directly; no apology, no recap. "
        "Pick up mid-thought."
    )


@dataclass
class RecoveryState:
    # RecoveryState 记录一次 AgentLoop 运行中的恢复进度，避免无限重试或无限 compact。
    has_escalated_max_tokens: bool = False
    continuation_count: int = 0
    has_attempted_reactive_compact: bool = False
    consecutive_overloaded: int = 0
    current_model: str | None = None


@dataclass(frozen=True)
class CompletionResult:
    # RecoveryManager 返回给 AgentLoop 的统一结果。
    # append_to_history=False 用于避免 AgentLoop 重复追加已经手动写入 messages 的中间消息。
    message: Message
    append_to_history: bool = True


# sleep 被注入成函数，测试里可以传入假的 sleep，避免真的等待指数退避时间。
SleepFunc = Callable[[float], None | Awaitable[None]]


class RecoveryManager:
    # RecoveryManager 是 AgentLoop 和 LLMClient 之间的一层恢复适配器。
    # 它只处理“模型调用”相关问题，不处理工具权限和工具执行错误。
    def __init__(
        self,
        config: RecoveryConfig | None = None,
        *,
        sleep: SleepFunc = asyncio.sleep,
        context_manager: ContextManager | None = None,
    ) -> None:
        self.config = config or RecoveryConfig()
        self._sleep = sleep
        self.context_manager = context_manager

    async def complete(
        self,
        llm: LLMClient,
        messages: list[Message],
        tools: Sequence[ToolDefinition],
        state: RecoveryState | None = None,
    ) -> CompletionResult:
        # 每次 AgentLoop run_with_history 会复用同一个 state，让连续轮次共享恢复状态。
        state = state or RecoveryState(current_model=getattr(llm, "model", None))
        if state.current_model is None:
            state.current_model = getattr(llm, "model", None)

        while True:
            try:
                # 普通路径：先执行带 retry 的 LLM 调用。
                message = await self._complete_with_retry(llm, messages, tools, state)
                # reactive retry is scoped to one failed API call, not the whole AgentLoop run.
                state.has_attempted_reactive_compact = False
            except Exception as exc:
                kind = classify_llm_error(exc)
                if kind == LLMErrorKind.PROMPT_TOO_LONG:
                    # 上下文太长时，先做一次 reactive compact：保留 system 和最近窗口消息。
                    if not state.has_attempted_reactive_compact:
                        if self.context_manager is not None:
                            try:
                                compacted = await self.context_manager.reactive(messages)
                            except Exception:
                                # transcript/summary failures must not disable the final cheap fallback.
                                messages[:] = reactive_compact(
                                    messages,
                                    window=self.config.compact_message_window,
                                )
                            else:
                                messages[:] = compacted.messages
                        else:
                            messages[:] = reactive_compact(
                                messages,
                                window=self.config.compact_message_window,
                            )
                        state.has_attempted_reactive_compact = True
                        continue
                    # 如果 compact 后仍然太长，说明当前输入或保留窗口本身仍超限，恢复层只能返回错误消息。
                    return CompletionResult(
                        Message(
                            role="assistant",
                            content="[Error] Context too large after reactive compact.",
                            stop_reason="error",
                        )
                    )
                # 未知错误不重试，转成 assistant 消息返回，避免把整个 AgentLoop 直接打崩。
                return CompletionResult(
                    Message(
                        role="assistant",
                        content=f"[Error] {type(exc).__name__}: {str(exc)[:500]}",
                        stop_reason="error",
                    )
                )

            # 正常完成或工具调用停止，不需要恢复，直接交回 AgentLoop。
            if message.stop_reason != "max_tokens" or message.tool_calls:
                return CompletionResult(message)

            # 第一次输出截断时，优先提高 max_tokens 后重试，不把 partial 输出塞进历史。
            if not state.has_escalated_max_tokens and self._set_max_tokens(
                llm,
                self.config.escalated_max_tokens,
            ):
                state.has_escalated_max_tokens = True
                continue

            # 如果已经提高过 max_tokens 仍截断，就保留 partial 输出并追加续写提示。
            messages.append(message)
            if state.continuation_count < self.config.max_continuations:
                messages.append(Message(role="user", content=self.config.continuation_prompt))
                state.continuation_count += 1
                continue
            # 续写次数耗尽后返回最后一次 partial，并告诉 AgentLoop 不要重复追加。
            return CompletionResult(message, append_to_history=False)

    async def _complete_with_retry(
        self,
        llm: LLMClient,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        state: RecoveryState,
    ) -> Message:
        # 只对 429 rate limit 和 529 overloaded 做重试；其它错误交给 complete 外层处理。
        for attempt in range(self.config.max_retries + 1):
            try:
                return await llm.complete(messages, tools=tools)
            except Exception as exc:
                kind = classify_llm_error(exc)
                if kind not in {LLMErrorKind.RATE_LIMITED, LLMErrorKind.OVERLOADED}:
                    raise
                if attempt >= self.config.max_retries:
                    raise

                if kind == LLMErrorKind.OVERLOADED:
                    # overloaded 连续出现时，可以按配置切到 fallback_model。
                    state.consecutive_overloaded += 1
                    self._maybe_switch_model(llm, state)
                else:
                    state.consecutive_overloaded = 0

                # 退避等待优先使用 Retry-After，否则用指数退避 + jitter。
                await self._wait(retry_delay(attempt, self.config, exc))

        msg = "unreachable retry loop exhausted"
        raise RuntimeError(msg)

    async def _wait(self, seconds: float) -> None:
        # 兼容同步 sleep fake 和 asyncio.sleep，方便测试和生产复用同一逻辑。
        result = self._sleep(seconds)
        if inspect.isawaitable(result):
            await result

    def _maybe_switch_model(self, llm: LLMClient, state: RecoveryState) -> None:
        # LLMClient 是 Protocol，这里只在运行时确实有 model 属性时才切换。
        if (
            state.consecutive_overloaded < self.config.max_consecutive_overloaded
            or not self.config.fallback_model
        ):
            return
        if hasattr(llm, "model"):
            setattr(llm, "model", self.config.fallback_model)
            state.current_model = self.config.fallback_model
        state.consecutive_overloaded = 0

    def _set_max_tokens(self, llm: LLMClient, value: int) -> bool:
        # 不是所有 LLMClient 都暴露 max_tokens；不支持时返回 False，让调用方走续写恢复。
        if not hasattr(llm, "max_tokens"):
            return False
        setattr(llm, "max_tokens", value)
        return True


def classify_llm_error(exc: BaseException) -> LLMErrorKind:
    # 当前先用异常类型名和消息文本做轻量分类。
    # 后续如果接入供应商 SDK 的结构化错误码，可以把这里替换成更精确的适配层。
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    combined = f"{name} {message}"
    if (
        ("prompt" in combined and "long" in combined)
        or "prompt_is_too_long" in combined
        or "context_length_exceeded" in combined
        or "max_context_window" in combined
    ):
        return LLMErrorKind.PROMPT_TOO_LONG
    if "ratelimit" in combined or "rate_limit" in combined or "429" in combined:
        return LLMErrorKind.RATE_LIMITED
    if "overloaded" in combined or "529" in combined:
        return LLMErrorKind.OVERLOADED
    return LLMErrorKind.UNKNOWN


def retry_delay(
    attempt: int,
    config: RecoveryConfig | None = None,
    exc: BaseException | None = None,
) -> float:
    # HTTP/SDK 如果给了 retry-after，优先尊重服务端建议。
    config = config or RecoveryConfig()
    retry_after = _retry_after_seconds(exc) if exc is not None else None
    if retry_after is not None:
        return retry_after
    # 否则用指数退避，并加一点随机抖动，避免多个 Agent 同时重试造成尖峰。
    base = min(config.base_delay_seconds * (2**attempt), config.max_delay_seconds)
    return base + random.uniform(0, base * 0.25)


def reactive_compact(messages: Sequence[Message], *, window: int = 5) -> list[Message]:
    # reactive compact 是失败后的兜底压缩。assistant tool_calls 与其连续 tool results
    # 组成不可拆分的协议组，窗口边界只能落在组与组之间。
    system_messages = [message for message in messages if message.role == "system"]
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

    selected: list[list[Message]] = []
    selected_size = 0
    for group in reversed(groups):
        if selected and selected_size >= window:
            break
        selected.append(group)
        selected_size += len(group)
    selected.reverse()
    tail = [message for group in selected for message in group]
    reminder = "[Reactive compact] Earlier conversation trimmed. Continue from the current task."
    if tail and tail[0].role == "user":
        first = tail[0]
        tail[0] = first.model_copy(
            update={"content": f"{reminder}\n\n[Recent user turn]\n{first.content}"}
        )
        prefix: list[Message] = []
    else:
        prefix = [Message(role="user", content=reminder)]
    return [
        *system_messages,
        *prefix,
        *tail,
    ]


def _retry_after_seconds(exc: BaseException | None) -> float | None:
    # 兼容两类常见形态：
    # 1. SDK 异常直接带 retry_after 属性；
    # 2. SDK 异常带 response.headers["Retry-After"]。
    if exc is None:
        return None
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            return None

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CompletionResult",
    "LLMErrorKind",
    "RecoveryConfig",
    "RecoveryManager",
    "RecoveryState",
    "classify_llm_error",
    "reactive_compact",
    "retry_delay",
]
