from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from agent_harness.hooks import HookEvent, HookManager
from agent_harness.llm.base import LLMClient
from agent_harness.messages.models import Message
from agent_harness.recovery import CompletionResult, RecoveryManager, RecoveryState
from agent_harness.tools.base import ToolDefinition, ToolResult
from agent_harness.tools.registry import ToolRegistry


@dataclass
class AgentLoop:
    # 学习说明：AgentLoop 是 M1 的核心。
    # 它不“决定”是否调用工具；模型决定。Loop 只负责把工具描述交给模型、执行模型请求、再把结果放回上下文。
    llm: LLMClient
    tools: ToolRegistry = field(default_factory=ToolRegistry)
    max_tool_rounds: int = 3
    # 对应 learn-claude-code s04_hooks：把权限、追踪、收尾等扩展逻辑挂到 hook 上，而不是写死在循环里。
    hooks: HookManager = field(default_factory=HookManager)
    # 对应 learn-claude-code s11_error_recovery：LLM 调用由恢复层包裹，支持重试、截断恢复和上下文超限处理。
    recovery: RecoveryManager | None = field(default_factory=RecoveryManager)

    async def run(self, prompt_or_messages: str | Sequence[Message]) -> Message:
        messages = await self.run_with_history(prompt_or_messages)
        return messages[-1]

    async def run_with_history(self, prompt_or_messages: str | Sequence[Message]) -> list[Message]:
        messages = self._normalize_messages(prompt_or_messages)
        tool_definitions = self.tools.definitions()
        if messages and messages[-1].role == "user":
            # 对应 s04_hooks：UserPromptSubmit，在用户输入进入 LLM 前触发。
            await self.hooks.trigger(
                HookEvent.USER_PROMPT_SUBMIT,
                messages=messages,
                metadata={"prompt": messages[-1].content},
            )
        recovery_state = RecoveryState(current_model=getattr(self.llm, "model", None))

        for tool_round in range(self.max_tool_rounds + 1):
            # 学习说明：每一轮都把完整上下文和当前可用工具定义交给模型。
            # 工具定义只是“菜单”，模型只能请求工具；真正执行仍发生在本地 Harness。
            # 对应 s04_hooks：PreLLMCall 是本项目扩展出的 LLM 调用前 hook，Tracing 也挂在这里。
            await self.hooks.trigger(
                HookEvent.PRE_LLM_CALL,
                messages=messages,
                metadata={"tool_round": tool_round},
            )
            # 对应 s11_error_recovery：这里不再裸调 llm.complete，而是通过 RecoveryManager 处理 429/529、max_tokens 和 context too long。
            completion = await self._complete_assistant_message(
                messages,
                tool_definitions,
                recovery_state,
            )
            assistant_message = completion.message
            if completion.append_to_history:
                messages.append(assistant_message)
            # 对应 s04_hooks：PostLLMCall 是本项目扩展出的 LLM 调用后 hook，用于记录 stop_reason、工具调用数等观测信息。
            await self.hooks.trigger(
                HookEvent.POST_LLM_CALL,
                messages=messages,
                metadata={
                    "tool_round": tool_round,
                    "stop_reason": assistant_message.stop_reason,
                    "tool_calls": len(assistant_message.tool_calls),
                },
            )

            # 学习说明：没有 tool_calls 说明模型认为任务已完成，AgentLoop 直接返回最终回答。
            if not assistant_message.tool_calls:
                # 对应 s04_hooks：Stop hook 在循环退出前触发；如果返回 continue_with，可强制追加消息继续跑。
                stop_result = await self.hooks.trigger(
                    HookEvent.STOP,
                    messages=messages,
                    metadata={"tool_round": tool_round},
                )
                if stop_result and stop_result.continue_with:
                    messages.append(Message(role="user", content=stop_result.continue_with))
                    continue
                return messages

            if tool_round >= self.max_tool_rounds:
                break

            for tool_call in assistant_message.tool_calls:
                # 学习说明：ToolRegistry 根据模型给出的工具名做分发。
                # 这里不关心具体工具是读文件、搜索还是跑命令，Loop 因此保持稳定。
                # 对应 s03_permission + s04_hooks：权限检查注册为 PreToolUse hook，命中 deny/ask 拒绝时不执行工具。
                #将判断逻辑直接写在循环里
                pre_tool_result = await self.hooks.trigger(
                    HookEvent.PRE_TOOL_USE,
                    messages=messages,
                    tool_call=tool_call,
                    metadata={"tool_round": tool_round},
                )
                if pre_tool_result and pre_tool_result.block:
                    result = ToolResult(
                        output={
                            "error": "PermissionDenied",
                            "message": pre_tool_result.message or "Tool call blocked by hook.",
                        }
                    )
                    messages.append(self._tool_message(tool_call.id, tool_call.name, result))
                    continue

                try:
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                except Exception as exc:
                    # 对应 s11_error_recovery：工具异常不让 AgentLoop 崩溃，而是结构化回填给模型继续恢复。
                    # 对应 s04_hooks：Error hook 让 tracing/logging 能观察失败。
                    await self.hooks.trigger(
                        HookEvent.ERROR,
                        messages=messages,
                        tool_call=tool_call,
                        error=exc,
                        metadata={"stage": "tool", "tool_round": tool_round},
                    )
                    result = ToolResult(
                        output={
                            "error": type(exc).__name__,
                            "message": str(exc),
                        }
                    )

                # 对应 s04_hooks：PostToolUse 在工具执行后触发，Tracing 和后处理逻辑挂在这里。
                await self.hooks.trigger(
                    HookEvent.POST_TOOL_USE,
                    messages=messages,
                    tool_call=tool_call,
                    tool_result=result,
                    metadata={"tool_round": tool_round},
                )
                messages.append(
                    self._tool_message(tool_call.id, tool_call.name, result)
                )

        msg = f"Tool calling exceeded max_tool_rounds={self.max_tool_rounds}"
        raise RuntimeError(msg)

    def _normalize_messages(self, prompt_or_messages: str | Sequence[Message]) -> list[Message]:
        if isinstance(prompt_or_messages, str):
            return [Message(role="user", content=prompt_or_messages)]
        return list(prompt_or_messages)

    async def _complete_assistant_message(
        self,
        messages: list[Message],
        tool_definitions: Sequence[ToolDefinition],
        recovery_state: RecoveryState,
    ) -> CompletionResult:
        if self.recovery is None:
            return CompletionResult(
                await self.llm.complete(messages, tools=tool_definitions)
            )
        # 对应 s11_error_recovery：恢复层内部负责瞬态错误重试、输出截断升级和 reactive compact。
        return await self.recovery.complete(
            self.llm,
            messages,
            tool_definitions,
            recovery_state,
        )

    def _tool_message(self, tool_call_id: str, tool_name: str, result: ToolResult) -> Message:
        return Message(
            role="tool",
            name=tool_name,
            tool_call_id=tool_call_id,
            # 学习说明：工具结果必须回填到消息历史里，模型下一轮才能基于观察继续推理。
            content=self._stringify_tool_output(result.output),
        )

    def _stringify_tool_output(self, output: object) -> str:
        try:
            return json.dumps(output, ensure_ascii=False)
        except TypeError:
            return str(output)
