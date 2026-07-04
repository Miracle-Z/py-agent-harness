from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
import typer

from agent_harness.agent import AgentLoop
from agent_harness.llm.anthropic import AnthropicClient
from agent_harness.llm.base import LLMClient
from agent_harness.llm.openai import OpenAIClient
from agent_harness.messages import Message
from agent_harness.tools import create_coding_tool_registry

Provider = Literal["anthropic", "openai"]

app = typer.Typer(help="运行 Python Agent Harness。")

DEFAULT_CODING_SYSTEM_PROMPT = (
    "You are a coding agent running inside {root}. "
    "Use the available tools to inspect, edit, and verify the workspace. "
    "Prefer small changes, explain important results clearly, and run tests after code changes."
)


class CLISettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str | None = None
    anthropic_model: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    openai_base_url: str | None = None
    model_id: str | None = None


@app.callback(invoke_without_command=True)
def _main_callback(
    ctx: typer.Context,
    root: Path = typer.Option(
        Path("."),
        "--root",
        "-r",
        help="暴露给代码工具的工作目录根路径。",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="模型 ID；默认依次读取 ANTHROPIC_MODEL、OPENAI_MODEL、MODEL_ID。",
    ),
    provider: Provider = typer.Option(
        "anthropic",
        "--provider",
        "-p",
        help="模型供应商，可选 anthropic 或 openai。",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="模型供应商 API Key；默认读取 ANTHROPIC_API_KEY 或 OPENAI_API_KEY。",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="OpenAI 兼容接口地址；provider=openai 时默认读取 OPENAI_BASE_URL。",
    ),
    max_tokens: int = typer.Option(
        4096,
        "--max-tokens",
        min=1,
        help="每次模型调用允许的最大输出 token 数。",
    ),
    max_tool_rounds: int = typer.Option(
        6,
        "--max-tool-rounds",
        min=0,
        help="AgentLoop 停止前允许的最大工具调用轮数。",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="打印供应商配置和消息摘要，不暴露 API Key 明文。",
    ),
) -> None:
    # 学习说明：CLI 是项目对外启动入口。
    # 不带子命令时直接进入交互模式，贴近 learn-claude-code 的教学脚本体验。
    if ctx.invoked_subcommand is None:
        _chat(
            prompt=None,
            root=root,
            model=model,
            provider=provider,
            api_key=api_key,
            base_url=base_url,
            max_tokens=max_tokens,
            max_tool_rounds=max_tool_rounds,
            debug=debug,
        )


@app.command()
def chat(
    prompt: str | None = typer.Argument(
        None,
        help="一次性执行的提示词；不传则进入交互模式。",
    ),
    root: Path = typer.Option(
        Path("."),
        "--root",
        "-r",
        help="暴露给代码工具的工作目录根路径。",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="模型 ID；默认依次读取 ANTHROPIC_MODEL、OPENAI_MODEL、MODEL_ID。",
    ),
    provider: Provider = typer.Option(
        "anthropic",
        "--provider",
        "-p",
        help="模型供应商，可选 anthropic 或 openai。",
    ),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        help="模型供应商 API Key；默认读取 ANTHROPIC_API_KEY 或 OPENAI_API_KEY。",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="OpenAI 兼容接口地址；provider=openai 时默认读取 OPENAI_BASE_URL。",
    ),
    max_tokens: int = typer.Option(
        4096,
        "--max-tokens",
        min=1,
        help="每次模型调用允许的最大输出 token 数。",
    ),
    max_tool_rounds: int = typer.Option(
        6,
        "--max-tool-rounds",
        min=0,
        help="AgentLoop 停止前允许的最大工具调用轮数。",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="打印供应商配置和消息摘要，不暴露 API Key 明文。",
    ),
) -> None:
    """使用已注册的代码工具运行 Coding AgentLoop。"""
    _chat(
        prompt=prompt,
        root=root,
        model=model,
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        max_tokens=max_tokens,
        max_tool_rounds=max_tool_rounds,
        debug=debug,
    )


def _chat(
    *,
    prompt: str | None,
    root: Path,
    model: str | None,
    provider: Provider,
    api_key: str | None,
    base_url: str | None,
    max_tokens: int,
    max_tool_rounds: int,
    debug: bool,
) -> None:
    resolved_root = root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise typer.BadParameter(f"root 必须是已存在的目录：{root}")

    settings = _load_settings()
    resolved_model = _resolve_model(provider, model, settings)
    if not resolved_model:
        raise typer.BadParameter(
            "必须指定模型。请传入 --model，或设置 ANTHROPIC_MODEL、OPENAI_MODEL、MODEL_ID。"
        )

    llm = _create_llm_client(
        provider=provider,
        model=resolved_model,
        api_key=api_key,
        base_url=base_url,
        max_tokens=max_tokens,
        settings=settings,
    )
    if debug:
        _print_debug_config(
            provider=provider,
            model=resolved_model,
            api_key=_resolve_api_key(provider, api_key, settings),
            base_url=base_url,
            settings=settings,
        )
    loop = AgentLoop(
        llm=llm,
        tools=create_coding_tool_registry(resolved_root),
        max_tool_rounds=max_tool_rounds,
    )
    messages = _initial_messages(resolved_root)

    # 单次模式
    if prompt:
        messages.append(Message(role="user", content=prompt))
        response = _run_loop(loop, messages, debug=debug)
        typer.echo(response.content)
        return
    # 对话模式
    typer.echo(f"agent-harness chat: 工作目录={resolved_root}")
    typer.echo("输入 q、quit 或 exit 退出。")
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
        response = _run_loop(loop, messages, debug=debug)
        typer.echo(response.content)


def _load_settings() -> CLISettings:
    return CLISettings()


def _resolve_model(
    provider: Provider,
    model: str | None,
    settings: CLISettings | None = None,
) -> str | None:
    settings = settings or _load_settings()
    if _non_empty(model):
        return model
    provider_model = settings.openai_model if provider == "openai" else settings.anthropic_model
    return _non_empty(provider_model) or _non_empty(settings.model_id)


def _resolve_api_key(
    provider: Provider,
    api_key: str | None,
    settings: CLISettings | None = None,
) -> str | None:
    settings = settings or _load_settings()
    if _non_empty(api_key):
        return api_key
    if provider == "openai":
        return _non_empty(settings.openai_api_key)
    return _non_empty(settings.anthropic_api_key)


def _create_llm_client(
    *,
    provider: Provider,
    model: str,
    api_key: str | None,
    base_url: str | None,
    max_tokens: int,
    settings: CLISettings | None = None,
) -> LLMClient:
    settings = settings or _load_settings()
    resolved_api_key = _resolve_api_key(provider, api_key, settings)
    if provider == "openai":
        return OpenAIClient(
            model=model,
            max_tokens=max_tokens,
            api_key=resolved_api_key,
            base_url=_non_empty(base_url) or _non_empty(settings.openai_base_url),
        )
    return AnthropicClient(model=model, max_tokens=max_tokens, api_key=resolved_api_key)


def _print_debug_config(
    *,
    provider: Provider,
    model: str,
    api_key: str | None,
    base_url: str | None,
    settings: CLISettings,
) -> None:
    resolved_base_url = _non_empty(base_url) or (
        _non_empty(settings.openai_base_url) if provider == "openai" else None
    )
    typer.echo(
        "调试："
        f"供应商={provider} "
        f"模型={model} "
        f"APIKey已配置={bool(_non_empty(api_key))} "
        f"接口地址={resolved_base_url or '默认地址'}"
    )


def _non_empty(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _initial_messages(root: Path) -> list[Message]:
    return [
        Message(
            role="system",
            content=DEFAULT_CODING_SYSTEM_PROMPT.format(root=root),
        )
    ]


def _run_loop(loop: AgentLoop, messages: list[Message], *, debug: bool = False) -> Message:
    previous_len = len(messages)
    try:
        updated_messages = asyncio.run(loop.run_with_history(messages))
    except Exception as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=1) from exc

    messages[:] = updated_messages
    if debug:
        _print_debug_messages(updated_messages[previous_len:])
    response = updated_messages[-1]
    if response.role == "assistant" and not response.content and not response.tool_calls:
        typer.echo(
            "警告：模型返回了空的 assistant 消息，且没有工具调用。",
            err=True,
        )
    return response


def _print_debug_messages(new_messages: list[Message]) -> None:
    for message in new_messages:
        summary = (
            f"调试：消息角色={message.role} "
            f"内容字符数={len(message.content)} "
            f"工具调用数={len(message.tool_calls)}"
        )
        if message.name:
            summary += f" 工具名={message.name}"
        typer.echo(summary)


def main() -> None:
    app()
