from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from agent_harness.agent import AgentLoop
from agent_harness.hooks import HookEvent, HookManager
from agent_harness.messages import Message, ToolCall
from agent_harness.observability import InMemoryTracer
from agent_harness.permissions import (
    AlwaysAllowApprover,
    DenyByDefaultApprover,
    InteractiveApprover,
    PermissionBehavior,
    PermissionManager,
    PermissionRequest,
)
from agent_harness.recovery import RecoveryConfig, RecoveryManager
from agent_harness.tools import ToolDefinition, ToolRegistry, ToolResult


class FakeLLM:
    def __init__(self, responses: list[Message]) -> None:
        self.responses = responses
        self.calls: list[list[Message]] = []
        self.max_tokens = 8_000
        self.model = "primary"

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> Message:
        self.calls.append(list(messages))
        return self.responses.pop(0)


class FlakyLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__([Message(role="assistant", content="recovered")])
        self.failures_remaining = 2

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> Message:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("429 rate limit")
        return await super().complete(messages, tools)


class EchoTool:
    name = "echo"
    description = "Return text."
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
        "additionalProperties": False,
    }

    def run(self, text: str) -> ToolResult:
        return ToolResult(output=text)


def test_permission_manager_denies_before_asking_on_hard_deny(tmp_path: Path) -> None:
    manager = PermissionManager(root=tmp_path, approver=AlwaysAllowApprover())

    decision = manager.check("shell", {"command": "sudo reboot"})

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.reason and "被禁止的模式" in decision.reason


def test_permission_manager_asks_for_mutating_tools(tmp_path: Path) -> None:
    manager = PermissionManager(root=tmp_path, approver=DenyByDefaultApprover())

    decision = manager.check("write_file", {"path": "example.txt", "content": "x"})

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.reason == "write_file 需要权限确认"


def test_interactive_approver_uses_chinese_prompt(tmp_path: Path) -> None:
    outputs: list[str] = []
    prompts: list[str] = []
    approver = InteractiveApprover(
        input_func=lambda prompt: prompts.append(prompt) or "y",
        output_func=outputs.append,
    )

    approved = approver.approve(
        PermissionRequest(
            tool_name="write_file",
            arguments={"path": "example.txt", "content": "x"},
            root=tmp_path,
        ),
        "write_file 需要权限确认",
    )

    assert approved is True
    assert outputs[0] == "\n需要权限确认：write_file 需要权限确认"
    assert outputs[1].startswith("工具调用：write_file(")
    assert prompts == ["是否允许执行？[y/N] "]


@pytest.mark.asyncio
async def test_pre_tool_hook_blocks_tool_execution() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    hooks = HookManager()
    called = False

    async def block_echo(context):
        nonlocal called
        called = True
        return "blocked by test hook"

    hooks.register(HookEvent.PRE_TOOL_USE, block_echo)
    llm = FakeLLM(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call-1", name="echo", arguments={"text": "hello"})],
            ),
            Message(role="assistant", content="done"),
        ]
    )

    messages = await AgentLoop(llm=llm, tools=registry, hooks=hooks).run_with_history("go")

    assert called is True
    assert messages[2].role == "tool"
    assert "PermissionDenied" in messages[2].content
    assert llm.calls[1][-1].content == (
        '{"error": "PermissionDenied", "message": "blocked by test hook"}'
    )


@pytest.mark.asyncio
async def test_tracer_records_llm_and_tool_events() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    hooks = HookManager()
    tracer = InMemoryTracer()
    tracer.install(hooks)
    llm = FakeLLM(
        [
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call-1", name="echo", arguments={"text": "hello"})],
            ),
            Message(role="assistant", content="done"),
        ]
    )

    await AgentLoop(llm=llm, tools=registry, hooks=hooks).run("go")

    event_names = [event.name for event in tracer.events]
    assert "pre_llm_call" in event_names
    assert "post_llm_call" in event_names
    assert "pre_tool_use" in event_names
    assert "post_tool_use" in event_names
    assert "stop" in event_names


@pytest.mark.asyncio
async def test_recovery_retries_transient_llm_errors() -> None:
    sleeps: list[float] = []

    def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    llm = FlakyLLM()
    recovery = RecoveryManager(
        RecoveryConfig(max_retries=3, base_delay_seconds=0, max_delay_seconds=0),
        sleep=record_sleep,
    )

    response = await AgentLoop(llm=llm, recovery=recovery).run("hello")

    assert response.content == "recovered"
    assert len(sleeps) == 2


@pytest.mark.asyncio
async def test_recovery_escalates_max_tokens_without_appending_truncated_output() -> None:
    llm = FakeLLM(
        [
            Message(role="assistant", content="partial", stop_reason="max_tokens"),
            Message(role="assistant", content="complete"),
        ]
    )

    messages = await AgentLoop(llm=llm).run_with_history("hello")

    assert llm.max_tokens == 64_000
    assert [message.content for message in messages] == ["hello", "complete"]
