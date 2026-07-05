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


class FakeOpenAIClient(FakeAnthropicClient):
    def __init__(
        self,
        model: str,
        max_tokens: int = 1024,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(model=model, max_tokens=max_tokens, api_key=api_key)
        self.base_url = base_url


def test_cli_default_command_enters_chat_loop(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "AnthropicClient", FakeAnthropicClient)

    result = runner.invoke(cli.app, ["--model", "fake-model"], input="q\n")

    assert result.exit_code == 0
    assert "agent-harness chat: 工作目录=" in result.output
    assert "输入 q、quit 或 exit 退出。" in result.output


def test_chat_command_runs_agent_loop(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "AnthropicClient", FakeAnthropicClient)

    result = runner.invoke(cli.app, ["chat", "--model", "fake-model", "hello"])

    assert result.exit_code == 0
    assert "fake response: hello" in result.output


def test_chat_command_can_use_openai_provider(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(cli, "OpenAIClient", FakeOpenAIClient)

    result = runner.invoke(cli.app, ["chat", "--provider", "openai", "--model", "fake-model", "hello"])

    assert result.exit_code == 0
    assert "fake response: hello" in result.output


def test_resolve_model_falls_back_to_model_id(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    settings = cli.CLISettings(openai_model=None, model_id="fallback-model")

    assert cli._resolve_model("openai", None, settings) == "fallback-model"


def test_resolve_openai_settings() -> None:
    settings = cli.CLISettings(openai_model="openai-model", openai_api_key="openai-key")

    assert cli._resolve_model("openai", None, settings) == "openai-model"
    assert cli._resolve_api_key("openai", None, settings) == "openai-key"
