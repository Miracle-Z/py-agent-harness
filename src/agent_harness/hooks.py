from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
import inspect
from typing import Any

from agent_harness.messages.models import Message, ToolCall
from agent_harness.tools.base import ToolResult


class HookEvent(StrEnum):
    # 对应 learn-claude-code s04_hooks：这些枚举就是 AgentLoop 暴露给外部扩展的生命周期节点。
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    PRE_LLM_CALL = "pre_llm_call"
    POST_LLM_CALL = "post_llm_call"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    STOP = "stop"
    ERROR = "error"


@dataclass
class HookContext:
    # HookContext 是传给每个 hook callback 的统一上下文对象。
    # 不同事件会填充不同字段：工具事件有 tool_call/tool_result，异常事件有 error，通用补充信息放 metadata。
    event: HookEvent
    messages: list[Message]
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    error: BaseException | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookResult:
    # HookResult 表示 hook 对主流程的控制结果。
    # block=True 用于阻止工具执行；continue_with 用于 Stop 阶段强制追加消息继续运行。
    message: str | None = None
    block: bool = False
    continue_with: str | None = None

    @classmethod
    def block_tool(cls, message: str) -> HookResult:
        # 工具执行前的 hook 可以返回该结果，AgentLoop 会跳过真实工具调用并回填权限/拦截信息。
        return cls(message=message, block=True)

    @classmethod
    def force_continue(cls, message: str) -> HookResult:
        # Stop hook 可以返回该结果，AgentLoop 会把 message 当作新的用户消息继续一轮。
        return cls(continue_with=message)


HookReturn = HookResult | str | None
HookCallback = Callable[[HookContext], HookReturn | Awaitable[HookReturn]]


class HookManager:
    def __init__(self) -> None:
        # 为每个事件预置 callback 列表，后续 register 只需要 append。
        self._callbacks: dict[HookEvent, list[HookCallback]] = {
            event: [] for event in HookEvent
        }

    def register(self, event: HookEvent | str, callback: HookCallback) -> None:
        # 支持传 HookEvent，也支持直接传字符串；这里统一规范化成 HookEvent。
        self._callbacks[HookEvent(event)].append(callback)

    async def trigger(
        self,
        event: HookEvent | str,
        *,
        messages: list[Message],
        tool_call: ToolCall | None = None,
        tool_result: ToolResult | None = None,
        error: BaseException | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> HookResult | None:
        # 触发某个生命周期事件：先组装 HookContext，再按注册顺序执行 callback。
        hook_event = HookEvent(event)
        context = HookContext(
            event=hook_event,
            messages=messages,
            tool_call=tool_call,
            tool_result=tool_result,
            error=error,
            metadata=metadata or {},
        )
        for callback in list(self._callbacks[hook_event]):
            # callback 可以是普通函数，也可以是 async 函数；这里统一 await 到最终结果。
            result = callback(context)
            if inspect.isawaitable(result):
                result = await result

            # 第一个产生控制结果的 hook 会短路后续 callback。
            # 这让权限拒绝这类场景可以立即阻止工具继续执行。
            normalized = self._normalize_result(hook_event, result)
            if normalized is not None:
                return normalized
        return None

    def _normalize_result(
        self,
        event: HookEvent,
        result: HookReturn,
    ) -> HookResult | None:
        # None 表示 hook 只做观察，不干预主流程。
        if result is None:
            return None
        # HookResult 表示 callback 已经明确给出控制语义，直接返回。
        if isinstance(result, HookResult):
            return result
        # 字符串是简写：Stop 事件中表示“继续执行的补充提示”。
        if event == HookEvent.STOP:
            return HookResult.force_continue(result)
        # 其他事件中字符串默认表示阻止当前动作，并把字符串作为原因。
        return HookResult.block_tool(result)
