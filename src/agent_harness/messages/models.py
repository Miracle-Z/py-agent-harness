from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    # 学习说明：ToolCall 表示“模型希望 Harness 执行的动作”。
    # arguments 是模型根据工具 input_schema 生成的参数，真正执行前仍应由工具自己校验。
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    # 学习说明：Message 是 Agent Loop 的统一上下文格式。
    # user/system/assistant 是对话消息；tool 是本地工具执行结果，会由模型适配层转成供应商协议。
    # Java 写法对照：
    # class Message {
    #     Role role;
    #     String content;
    #     String name = null;
    #     String toolCallId = null;
    #     List<ToolCall> toolCalls = new ArrayList<>();
    # }
    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str | None = None
