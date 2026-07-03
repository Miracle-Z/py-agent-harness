from __future__ import annotations

from collections.abc import Sequence

from typer.testing import CliRunner

from agent_harness import cli
from agent_harness.messages import Message
from agent_harness.tools import ToolDefinition


class FakeAnthropicClient:
    def __init__(
        self,
        model: str,
        max_tokens: int = 1024,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.api_key = api_key

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition] | None = None,
    ) -> Message:
        return Message(role="assistant", content=f"fake response: {messages[-1].content}")


def test_cli_default_command_prints_health_check() -> None:
    runner = CliRunner()

    result = runner.invoke(cli.app)

    assert result.exit_code == 0
    assert "Hello from agent-harness!" in result.output


def test_chat_command_runs_agent_loop(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "AnthropicClient", FakeAnthropicClient)

    result = runner.invoke(cli.app, ["chat", "--model", "fake-model", "hello"])

    assert result.exit_code == 0
    assert "fake response: hello" in result.output


def test_resolve_model_falls_back_to_model_id(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.setenv("MODEL_ID", "fallback-model")

    assert cli._resolve_model(None) == "fallback-model"
