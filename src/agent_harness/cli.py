from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer

from agent_harness.agent import AgentLoop
from agent_harness.llm.anthropic import AnthropicClient
from agent_harness.messages import Message
from agent_harness.tools import create_coding_tool_registry

app = typer.Typer(help="Run the Python Agent Harness.")

DEFAULT_CODING_SYSTEM_PROMPT = (
    "You are a coding agent running inside {root}. "
    "Use the available tools to inspect, edit, and verify the workspace. "
    "Prefer small changes, explain important results clearly, and run tests after code changes."
)


@app.callback(invoke_without_command=True)
def _main_callback(ctx: typer.Context) -> None:
    # 学习说明：CLI 是项目对外启动入口。
    # 不带子命令时保留最小行为，用来验证 pyproject.toml 中的 console script 是否正确注册。
    if ctx.invoked_subcommand is None:
        typer.echo("Hello from agent-harness!")


@app.command()
def chat(
    prompt: str | None = typer.Argument(
        None,
        help="Prompt to run once. Omit it to start an interactive chat.",
    ),
    root: Path = typer.Option(
        Path("."),
        "--root",
        "-r",
        help="Workspace root exposed to coding tools.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        envvar="ANTHROPIC_MODEL",
        help="Anthropic model id. Falls back to ANTHROPIC_MODEL, then MODEL_ID.",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        envvar="ANTHROPIC_API_KEY",
        help="Anthropic API key. Defaults to ANTHROPIC_API_KEY.",
    ),
    max_tokens: int = typer.Option(
        4096,
        "--max-tokens",
        min=1,
        help="Maximum response tokens per model call.",
    ),
    max_tool_rounds: int = typer.Option(
        6,
        "--max-tool-rounds",
        min=0,
        help="Maximum tool-calling rounds before the loop stops.",
    ),
) -> None:
    """Run the coding AgentLoop with the registered coding tools."""
    resolved_root = root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise typer.BadParameter(f"root must be an existing directory: {root}")

    resolved_model = _resolve_model(model)
    if not resolved_model:
        raise typer.BadParameter("model is required. Pass --model or set ANTHROPIC_MODEL.")

    llm = AnthropicClient(model=resolved_model, max_tokens=max_tokens, api_key=api_key)
    loop = AgentLoop(
        llm=llm,
        tools=create_coding_tool_registry(resolved_root),
        max_tool_rounds=max_tool_rounds,
    )
    messages = _initial_messages(resolved_root)

    if prompt:
        messages.append(Message(role="user", content=prompt))
        response = _run_loop(loop, messages)
        typer.echo(response.content)
        return

    typer.echo(f"agent-harness chat: root={resolved_root}")
    typer.echo("Type q, quit, or exit to stop.")
    while True:
        try:
            user_input = typer.prompt("agent-harness")
        except (EOFError, KeyboardInterrupt):
            typer.echo()
            return

        if user_input.strip().lower() in {"q", "quit", "exit"}:
            return
        if not user_input.strip():
            continue

        messages.append(Message(role="user", content=user_input))
        response = _run_loop(loop, messages)
        typer.echo(response.content)


def _resolve_model(model: str | None) -> str | None:
    return model or os.environ.get("ANTHROPIC_MODEL") or os.environ.get("MODEL_ID")


def _initial_messages(root: Path) -> list[Message]:
    return [
        Message(
            role="system",
            content=DEFAULT_CODING_SYSTEM_PROMPT.format(root=root),
        )
    ]


def _run_loop(loop: AgentLoop, messages: list[Message]) -> Message:
    try:
        updated_messages = asyncio.run(loop.run_with_history(messages))
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    messages[:] = updated_messages
    return updated_messages[-1]


def main() -> None:
    app()
