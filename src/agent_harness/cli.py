from __future__ import annotations

import typer

app = typer.Typer(help="Run the Python Agent Harness.")


@app.callback(invoke_without_command=True)
def _main_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo("Hello from agent-harness!")


def main() -> None:
    app()
