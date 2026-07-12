from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agent_harness import cli
from agent_harness.agent import AgentLoop
from agent_harness.context import ContextConfig, ContextManager, LLMContextSummarizer
from agent_harness.hooks import HookEvent, HookManager
from agent_harness.memory import MemoryStore, MemoryType
from agent_harness.messages import Message, ToolCall
from agent_harness.prompts import PromptContext, SystemPromptBuilder, replace_system_message
from agent_harness.permissions import DenyByDefaultApprover, PermissionBehavior, PermissionManager
from agent_harness.recovery import RecoveryManager
from agent_harness.session import (
    SessionCorruptError,
    SessionRecord,
    SessionStore,
    repair_incomplete_tool_calls,
)
from agent_harness.tasks import TaskRecord, TaskStatus, TaskStore
from agent_harness.todo import TodoManager, TodoStatus, TodoWriteTool
from agent_harness.tools import ToolDefinition, ToolRegistry, create_m4_tool_registry


def test_todo_write_replaces_the_full_list_atomically() -> None:
    manager = TodoManager([{"content": "inspect", "status": "pending"}])
    tool = TodoWriteTool(manager)

    result = tool.run(
        [
            {"content": "implement", "status": "in_progress"},
            {"content": "verify", "status": "pending"},
        ]
    )

    assert result.output["updated"] == 2
    assert [item.content for item in manager.items] == ["implement", "verify"]
    assert manager.items[0].status == TodoStatus.IN_PROGRESS

    with pytest.raises(ValidationError):
        manager.replace([{"content": "", "status": "completed"}])
    assert [item.content for item in manager.items] == ["implement", "verify"]


def test_session_round_trip_preserves_protocol_messages_and_todos(tmp_path: Path) -> None:
    messages = [
        Message(role="system", content="system"),
        Message(role="user", content="inspect"),
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call-1", name="read_file", arguments={"path": "a.py"})],
            stop_reason="tool_use",
        ),
        Message(
            role="tool",
            name="read_file",
            tool_call_id="call-1",
            content="print('ok')",
        ),
    ]
    store = SessionStore(tmp_path / ".sessions", workspace=tmp_path)
    created = store.create(
        session_id="demo",
        messages=messages,
        todos=[{"content": "inspect", "status": "completed"}],
    )

    restored = SessionStore(tmp_path / ".sessions", workspace=tmp_path).load("demo")

    assert restored.id == created.id
    assert [message.model_dump() for message in restored.messages] == [
        message.model_dump() for message in messages
    ]
    assert restored.todos[0].status == TodoStatus.COMPLETED

    (tmp_path / ".sessions" / "broken.json").write_text("not json", encoding="utf-8")
    with pytest.raises(SessionCorruptError):
        store.load("broken")


def test_memory_store_writes_index_and_survives_a_fresh_instance(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / ".memory")
    store.write(
        "Testing preference",
        "Never call a real external service from tests",
        "Use a deterministic fake at the HTTP boundary.",
        MemoryType.USER,
    )
    # The same safe slug is an update, not a duplicate index row.
    store.write(
        "Testing preference",
        "Tests use deterministic HTTP fakes",
        "Never call a real external service from tests.",
        MemoryType.FEEDBACK,
    )

    fresh = MemoryStore(tmp_path / ".memory")
    matches = fresh.search("external service")

    assert len(fresh.list()) == 1
    assert matches[0].type == MemoryType.FEEDBACK
    assert "Testing preference" in fresh.index_text()
    assert fresh.index_text().count("testing-preference.md") == 1


def test_memory_store_reserves_index_name_avoids_collisions_and_matches_chinese(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / ".memory")
    reserved = store.write("MEMORY", "Reserved-looking name", "still a record")
    first = store.write("x" * 100 + "a", "first long name", "first")
    second = store.write("x" * 100 + "b", "second long name", "second")
    constraint = store.write(
        "测试约束",
        "测试不得访问真实外部服务",
        "所有 HTTP 边界都使用确定性的 fake。",
        MemoryType.PROJECT,
    )

    assert reserved.slug != "memory"
    assert store.get("MEMORY").body == "still a record"
    assert first.slug != second.slug
    assert constraint in store.search("请帮我实现一项测试")


def test_task_store_enforces_dependencies_state_and_persistence(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / ".tasks")
    schema = store.create("schema")
    api = store.create("api", blocked_by=[schema.id])

    with pytest.raises(ValueError, match="blocked by"):
        store.claim(api.id)
    with pytest.raises(ValueError, match="cannot complete"):
        store.complete(schema.id)

    claimed_schema = store.claim(schema.id, owner="main")
    completed_schema, unblocked = store.complete(schema.id)

    assert claimed_schema.status == TaskStatus.IN_PROGRESS
    assert completed_schema.status == TaskStatus.COMPLETED
    assert [task.id for task in unblocked] == [api.id]

    fresh = TaskStore(tmp_path / ".tasks")
    assert fresh.can_start(api.id) is True
    assert fresh.get(schema.id).status == TaskStatus.COMPLETED


def test_task_store_rejects_a_cycle_without_partial_write(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / ".tasks")
    store.create("first", blocked_by=["task_b"], task_id="task_a")

    with pytest.raises(ValueError, match="cycle"):
        store.create("second", blocked_by=["task_a"], task_id="task_b")

    assert store.exists("task_b") is False


def test_task_store_rejects_malformed_dependencies_id_mismatch_and_public_save_cycle(
    tmp_path: Path,
) -> None:
    store = TaskStore(tmp_path / ".tasks")
    with pytest.raises(ValueError, match="blockedBy must be an array"):
        store.create("bad", blocked_by="abc")  # type: ignore[arg-type]

    task = store.create("valid", task_id="task_x")
    mismatched = task.model_copy(update={"id": "task_y"})
    (tmp_path / ".tasks" / "task_x.json").write_text(
        mismatched.model_dump_json(by_alias=True),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="id mismatch"):
        store.get("task_x")

    with pytest.raises(ValueError, match="depend on itself"):
        store.save(
            TaskRecord(
                id="task_cycle",
                subject="cycle",
                blockedBy=("task_cycle",),
            )
        )


def test_runtime_prompt_uses_actual_state_and_replaces_system_message(tmp_path: Path) -> None:
    builder = SystemPromptBuilder()
    context = PromptContext(
        workspace=str(tmp_path),
        enabled_tools=("todo_write", "read_file", "memory_write"),
        memory_index="- [testing](testing.md) — test rule",
    )

    first = builder.build(context)
    second = builder.build(context)
    messages = [Message(role="system", content="old"), Message(role="user", content="hello")]
    replace_system_message(messages, first)
    replace_system_message(messages, second)

    assert first is second
    assert "read_file" in first
    assert "Memory Index" not in first
    assert "testing.md" in first
    assert [message.role for message in messages].count("system") == 1


@pytest.mark.asyncio
async def test_context_pipeline_persists_large_results_and_keeps_tool_pairs(
    tmp_path: Path,
) -> None:
    manager = ContextManager(
        transcript_dir=tmp_path / ".transcripts",
        tool_output_dir=tmp_path / ".outputs",
        config=ContextConfig(
            max_messages=7,
            keep_head_messages=1,
            keep_recent_tool_results=1,
            micro_compact_min_chars=20,
            tool_result_batch_budget_chars=60,
            persist_tool_result_min_chars=20,
            tool_result_preview_chars=10,
            auto_compact_threshold_chars=100_000,
        ),
    )
    messages = [Message(role="system", content="system"), Message(role="user", content="goal")]
    for index in range(4):
        call_id = f"call-{index}"
        messages.extend(
            [
                Message(
                    role="assistant",
                    content="",
                    tool_calls=[ToolCall(id=call_id, name="read_file", arguments={})],
                ),
                Message(
                    role="tool",
                    name="read_file",
                    tool_call_id=call_id,
                    content=("x" * 80 if index >= 2 else "old result " * 8),
                ),
            ]
        )

    result = await manager.prepare(messages)

    assert result.stages == ("tool_result_budget", "snip", "micro")
    assert list((tmp_path / ".outputs").glob("*.txt"))
    assert result.transcript_path and result.transcript_path.exists()
    _assert_no_orphan_tool_messages(result.messages)
    # Caller-owned messages are not mutated.
    assert messages[-1].content == "x" * 80


@pytest.mark.asyncio
async def test_context_summary_saves_transcript_and_preserves_system(tmp_path: Path) -> None:
    calls: list[tuple[list[Message], str | None]] = []

    async def summarize(messages: Sequence[Message], focus: str | None) -> str:
        calls.append((list(messages), focus))
        return "goal, decisions, changed files, and remaining tests"

    manager = ContextManager(
        transcript_dir=tmp_path / ".transcripts",
        tool_output_dir=tmp_path / ".outputs",
        config=ContextConfig(auto_compact_threshold_chars=20),
        summarizer=summarize,
    )
    messages = [
        Message(role="system", content="system"),
        Message(role="user", content="a goal that is longer than the threshold"),
    ]

    result = await manager.prepare(messages)

    assert len(calls) == 1
    assert result.stages == ("summary",)
    assert result.messages[0] == messages[0]
    assert "changed files" in result.messages[-1].content
    assert result.transcript_path and result.transcript_path.exists()


@pytest.mark.asyncio
async def test_llm_context_summarizer_calls_model_without_tools() -> None:
    class SummaryLLM:
        def __init__(self) -> None:
            self.tools: Sequence[ToolDefinition] | None = None

        async def complete(
            self,
            messages: Sequence[Message],
            tools: Sequence[ToolDefinition] | None = None,
        ) -> Message:
            self.tools = tools
            assert "current goal" in messages[0].content
            return Message(role="assistant", content="durable summary")

    llm = SummaryLLM()
    summary = await LLMContextSummarizer(llm)([Message(role="user", content="goal")])

    assert summary == "durable summary"
    assert llm.tools == []


class PromptTooLongOnce:
    def __init__(self) -> None:
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> Message:
        self.calls.append([message.model_copy(deep=True) for message in messages])
        if len(self.calls) == 1:
            raise RuntimeError("prompt_is_too_long")
        return Message(role="assistant", content="recovered")


@pytest.mark.asyncio
async def test_recovery_reuses_context_manager_for_protocol_safe_reactive_compact(
    tmp_path: Path,
) -> None:
    manager = ContextManager(
        transcript_dir=tmp_path / ".transcripts",
        tool_output_dir=tmp_path / ".outputs",
        config=ContextConfig(auto_compact_threshold_chars=100_000),
    )
    llm = PromptTooLongOnce()
    loop = AgentLoop(llm=llm, context_manager=manager)
    history = [Message(role="user", content=f"turn {index}") for index in range(8)]

    response = await loop.run(history)

    assert response.content == "recovered"
    assert "[Reactive compact]" in llm.calls[1][0].content
    assert list((tmp_path / ".transcripts").glob("*.jsonl"))


@pytest.mark.asyncio
async def test_max_tokens_tool_call_is_returned_for_execution_without_continuation() -> None:
    class TruncatedToolLLM:
        async def complete(
            self,
            messages: Sequence[Message],
            tools: Sequence[ToolDefinition] | None = None,
        ) -> Message:
            return Message(
                role="assistant",
                content="partial",
                stop_reason="max_tokens",
                tool_calls=[ToolCall(id="call-1", name="todo_read", arguments={})],
            )

    messages = [Message(role="user", content="work")]
    result = await RecoveryManager().complete(TruncatedToolLLM(), messages, [])

    assert result.message.tool_calls[0].id == "call-1"
    assert messages == [Message(role="user", content="work")]


def test_m4_registry_extends_but_does_not_replace_coding_tools(tmp_path: Path) -> None:
    names = {tool.name for tool in create_m4_tool_registry(tmp_path).list()}

    assert {"read_file", "write_file", "todo_write", "compact"} <= names
    assert {"create_task", "list_tasks", "get_task", "claim_task", "complete_task"} <= names
    assert {"memory_write", "memory_read", "memory_search"} <= names


def test_durable_m4_mutations_use_existing_permission_approval(tmp_path: Path) -> None:
    manager = PermissionManager(root=tmp_path, approver=DenyByDefaultApprover())

    decision = manager.check(
        "memory_write",
        {"name": "preference", "description": "test", "type": "user", "body": "x"},
    )

    assert decision.behavior == PermissionBehavior.DENY


class RecordingCLIClient:
    calls: list[list[Message]] = []

    def __init__(
        self,
        model: str,
        max_tokens: int = 1024,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> Message:
        self.calls.append([message.model_copy(deep=True) for message in messages])
        return Message(role="assistant", content=f"answer {len(self.calls)}")


def test_cli_can_resume_an_explicit_session(monkeypatch, tmp_path: Path) -> None:
    RecordingCLIClient.calls = []
    monkeypatch.setattr(cli, "AnthropicClient", RecordingCLIClient)
    runner = CliRunner()
    common = [
        "chat",
        "--root",
        str(tmp_path),
        "--model",
        "fake-model",
        "--session",
        "demo",
    ]

    first = runner.invoke(cli.app, [*common, "first"])
    second = runner.invoke(cli.app, [*common, "second"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    second_contents = [message.content for message in RecordingCLIClient.calls[1]]
    assert "first" in second_contents
    assert "answer 1" in second_contents
    assert "second" in second_contents
    assert (tmp_path / ".sessions" / "demo.json").exists()


def test_cli_inherits_options_before_subcommand(monkeypatch, tmp_path: Path) -> None:
    RecordingCLIClient.calls = []
    monkeypatch.setattr(cli, "AnthropicClient", RecordingCLIClient)
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "--root",
            str(tmp_path),
            "--model",
            "fake-model",
            "--session",
            "before-chat",
            "chat",
            "hello",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".sessions" / "before-chat.json").exists()


def test_explicit_child_option_overrides_conflicting_parent_option(
    monkeypatch,
    tmp_path: Path,
) -> None:
    RecordingCLIClient.calls = []
    monkeypatch.setattr(cli, "AnthropicClient", RecordingCLIClient)
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "--root",
            str(tmp_path),
            "--model",
            "fake-model",
            "--session",
            "parent-session",
            "chat",
            "--session",
            "child-session",
            "hello",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".sessions" / "child-session.json").exists()
    assert not (tmp_path / ".sessions" / "parent-session.json").exists()


class FailingAfterTodoClient:
    def __init__(
        self,
        model: str,
        max_tokens: int = 1024,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> Message:
        self.calls += 1
        if self.calls == 1:
            return Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="todo-1",
                        name="todo_write",
                        arguments={
                            "todos": [{"content": "persist me", "status": "in_progress"}]
                        },
                    )
                ],
            )
        return Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="todo-2", name="todo_read", arguments={})],
        )


def test_failed_turn_still_checkpoints_todo_and_protocol_history(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cli, "AnthropicClient", FailingAfterTodoClient)
    runner = CliRunner()

    result = runner.invoke(
        cli.app,
        [
            "chat",
            "--root",
            str(tmp_path),
            "--model",
            "fake-model",
            "--session",
            "failed-turn",
            "--max-tool-rounds",
            "1",
            "work",
        ],
    )

    assert result.exit_code == 1
    restored = SessionStore(tmp_path / ".sessions", workspace=tmp_path).load("failed-turn")
    assert restored.todos[0].content == "persist me"
    assert restored.messages[-1].role == "tool"


def test_session_rejects_empty_id_and_protocol_corruption(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / ".sessions", workspace=tmp_path)
    with pytest.raises(ValueError, match="Invalid session id"):
        store.create(session_id="")

    corrupt = SessionRecord(
        id="orphan",
        workspace=str(tmp_path.resolve()),
        messages=[Message(role="tool", tool_call_id="missing", content="bad")],
    )
    (tmp_path / ".sessions").mkdir()
    (tmp_path / ".sessions" / "orphan.json").write_text(
        corrupt.model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(SessionCorruptError, match="Orphan tool result"):
        store.load("orphan")

    incomplete = [
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="call-x", name="read_file", arguments={})],
        )
    ]
    repair_incomplete_tool_calls(incomplete)
    assert incomplete[-1].role == "tool"
    assert incomplete[-1].tool_call_id == "call-x"


@pytest.mark.asyncio
async def test_tool_result_is_recorded_before_a_failing_post_tool_hook() -> None:
    manager = TodoManager()
    registry = ToolRegistry()
    registry.register(TodoWriteTool(manager))
    hooks = HookManager()

    def fail_after_tool(context) -> None:
        raise RuntimeError("tracer failed")

    hooks.register(HookEvent.POST_TOOL_USE, fail_after_tool)

    class TodoLLM:
        async def complete(
            self,
            messages: Sequence[Message],
            tools: Sequence[ToolDefinition] | None = None,
        ) -> Message:
            return Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="todo-post-hook",
                        name="todo_write",
                        arguments={
                            "todos": [{"content": "done already", "status": "completed"}]
                        },
                    )
                ],
            )

    history = [Message(role="user", content="plan")]
    with pytest.raises(RuntimeError, match="tracer failed"):
        await AgentLoop(llm=TodoLLM(), tools=registry, hooks=hooks).run_with_history(history)

    assert history[-1].role == "tool"
    assert "done already" in history[-1].content


def _assert_no_orphan_tool_messages(messages: Sequence[Message]) -> None:
    expected: set[str] = set()
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            expected = {tool_call.id for tool_call in message.tool_calls}
            continue
        if message.role == "tool":
            assert message.tool_call_id in expected
            expected.discard(message.tool_call_id or "")
            continue
        assert not expected
    assert not expected
