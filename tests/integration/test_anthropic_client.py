from __future__ import annotations

import os

import pytest

from agent_harness.llm.anthropic import AnthropicClient
from agent_harness.messages import Message


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY") or not os.environ.get("ANTHROPIC_MODEL"),
    reason="ANTHROPIC_API_KEY and ANTHROPIC_MODEL are required for integration tests",
)
@pytest.mark.asyncio
async def test_anthropic_client_smoke() -> None:
    client = AnthropicClient(model=os.environ["ANTHROPIC_MODEL"], max_tokens=16)

    response = await client.complete([Message(role="user", content="Reply with exactly: ok")])

    assert response.role == "assistant"
    assert response.content
