from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import builtins

from agent_harness.hooks import HookContext, HookEvent, HookResult
from agent_harness.tools.workspace import resolve_workspace_path


class PermissionBehavior(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class PermissionRequest:
    tool_name: str
    arguments: dict[str, object]
    root: Path


@dataclass(frozen=True)
class PermissionDecision:
    behavior: PermissionBehavior
    reason: str | None = None

    @classmethod
    def allow(cls) -> PermissionDecision:
        return cls(PermissionBehavior.ALLOW)

    @classmethod
    def deny(cls, reason: str) -> PermissionDecision:
        return cls(PermissionBehavior.DENY, reason)

    @classmethod
    def ask(cls, reason: str) -> PermissionDecision:
        return cls(PermissionBehavior.ASK, reason)


class Approver:
    def approve(self, request: PermissionRequest, reason: str) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class AlwaysAllowApprover(Approver):
    def approve(self, request: PermissionRequest, reason: str) -> bool:
        return True


@dataclass(frozen=True)
class DenyByDefaultApprover(Approver):
    def approve(self, request: PermissionRequest, reason: str) -> bool:
        return False


@dataclass(frozen=True)
class InteractiveApprover(Approver):
    input_func: Callable[[str], str] = builtins.input
    output_func: Callable[[str], None] = builtins.print

    def approve(self, request: PermissionRequest, reason: str) -> bool:
        self.output_func(f"\n需要权限确认：{reason}")
        self.output_func(f"工具调用：{request.tool_name}({_preview_arguments(request.arguments)})")
        answer = self.input_func("是否允许执行？[y/n] ").strip().lower()
        return answer in {"y", "yes"}


@dataclass(frozen=True)
class PermissionPolicy:
    root: Path
    hard_deny_snippets: tuple[str, ...] = (
        "rm -rf /",
        "sudo",
        "shutdown",
        "reboot",
        "mkfs",
        "dd if=",
        "git reset --hard",
        "git clean",
    )
    always_ask_tools: frozenset[str] = field(
        default_factory=lambda: frozenset({"write_file", "edit_file", "shell"})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.resolve())

    def evaluate(self, request: PermissionRequest) -> PermissionDecision:
        path_decision = self._check_workspace_paths(request)
        if path_decision.behavior != PermissionBehavior.ALLOW:
            return path_decision

        command = _command_argument(request.arguments)
        if command:
            lowered = command.lower()
            for snippet in self.hard_deny_snippets:
                if snippet in lowered:
                    return PermissionDecision.deny(
                        f"命令包含被禁止的模式：{snippet}"
                    )

        if request.tool_name in self.always_ask_tools:
            return PermissionDecision.ask(f"{request.tool_name} 需要权限确认")

        if request.tool_name == "run_tests" and command:
            return PermissionDecision.ask("自定义测试命令需要权限确认")

        return PermissionDecision.allow()

    def _check_workspace_paths(self, request: PermissionRequest) -> PermissionDecision:
        for argument_name in ("path", "cwd"):
            value = request.arguments.get(argument_name)
            if value is None:
                continue
            try:
                resolve_workspace_path(request.root, str(value))
            except PermissionError:
                return PermissionDecision.deny(
                    f"{argument_name} 越过工作区边界：{value}"
                )
        return PermissionDecision.allow()


@dataclass
class PermissionManager:
    root: Path
    approver: Approver = field(default_factory=DenyByDefaultApprover)
    policy: PermissionPolicy | None = None

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        if self.policy is None:
            self.policy = PermissionPolicy(root=self.root)

    def check(self, tool_name: str, arguments: dict[str, object] | None) -> PermissionDecision:
        request = PermissionRequest(
            tool_name=tool_name,
            arguments=arguments or {},
            root=self.root,
        )
        assert self.policy is not None
        decision = self.policy.evaluate(request)
        if decision.behavior != PermissionBehavior.ASK:
            return decision

        if self.approver.approve(request, decision.reason or "需要权限确认"):
            return PermissionDecision.allow()
        return PermissionDecision.deny(decision.reason or "用户拒绝执行")

    async def pre_tool_use_hook(self, context: HookContext) -> HookResult | None:
        if context.event != HookEvent.PRE_TOOL_USE or context.tool_call is None:
            return None

        decision = self.check(context.tool_call.name, context.tool_call.arguments)
        if decision.behavior == PermissionBehavior.ALLOW:
            return None
        return HookResult.block_tool(decision.reason or "权限被拒绝")


def _command_argument(arguments: dict[str, object]) -> str | None:
    value = arguments.get("command")
    if value is None:
        return None
    return str(value)


def _preview_arguments(arguments: dict[str, object], *, max_chars: int = 500) -> str:
    preview = repr(arguments)
    if len(preview) <= max_chars:
        return preview
    return preview[:max_chars] + "..."


__all__ = [
    "AlwaysAllowApprover",
    "Approver",
    "DenyByDefaultApprover",
    "InteractiveApprover",
    "PermissionBehavior",
    "PermissionDecision",
    "PermissionManager",
    "PermissionPolicy",
    "PermissionRequest",
]
