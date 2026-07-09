"""工具执行权限检查。

CLI 和测试会直接导入 ``agent_harness.permissions``，所以这个文件目前同时承担
公共 API 导出和权限系统具体实现两个职责。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import builtins

from agent_harness.hooks import HookContext, HookEvent, HookResult
from agent_harness.tools.workspace import resolve_workspace_path


class PermissionBehavior(StrEnum):
    """权限策略评估可能产生的结果。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class PermissionRequest:
    """判断工具调用是否允许执行所需的标准化信息。"""

    tool_name: str
    arguments: dict[str, object]
    root: Path


@dataclass(frozen=True)
class PermissionDecision:
    """权限策略决策，以及可选的用户可读原因。"""

    behavior: PermissionBehavior
    reason: str | None = None

    @classmethod
    def allow(cls) -> PermissionDecision:
        """创建一个无需审批即可执行的决策。"""

        return cls(PermissionBehavior.ALLOW)

    @classmethod
    def deny(cls, reason: str) -> PermissionDecision:
        """创建一个阻止执行的决策。"""

        return cls(PermissionBehavior.DENY, reason)

    @classmethod
    def ask(cls, reason: str) -> PermissionDecision:
        """创建一个执行前需要审批器确认的决策。"""

        return cls(PermissionBehavior.ASK, reason)


class Approver:
    """审批器接口，用于确认 ASK 类型的权限决策。"""

    def approve(self, request: PermissionRequest, reason: str) -> bool:
        raise NotImplementedError


@dataclass(frozen=True)
class AlwaysAllowApprover(Approver):
    """自动允许所有审批请求，常用于宽松模式和测试。"""

    def approve(self, request: PermissionRequest, reason: str) -> bool:
        return True


@dataclass(frozen=True)
class DenyByDefaultApprover(Approver):
    """默认拒绝审批请求，用于无法交互或禁用交互审批的场景。"""

    def approve(self, request: PermissionRequest, reason: str) -> bool:
        return False


@dataclass(frozen=True)
class InteractiveApprover(Approver):
    """在允许敏感工具调用前，向本地用户发起确认。"""

    input_func: Callable[[str], str] = builtins.input
    output_func: Callable[[str], None] = builtins.print

    def approve(self, request: PermissionRequest, reason: str) -> bool:
        self.output_func(f"\n需要权限确认：{reason}")
        self.output_func(f"工具调用：{request.tool_name}({_preview_arguments(request.arguments)})")
        answer = self.input_func("是否允许执行？[y/n] ").strip().lower()
        return answer in {"y", "yes"}


@dataclass(frozen=True)
class PermissionPolicy:
    """将工具调用分类为允许、拒绝或需要审批的静态规则。"""

    root: Path
    # 这些命令片段会在用户审批前直接拒绝，主要覆盖破坏性操作或超出
    # 编码 agent 预期范围的操作。
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
    # 会修改工作区或执行命令的工具，必须经过审批器确认后才能运行。
    always_ask_tools: frozenset[str] = field(
        default_factory=lambda: frozenset({"write_file", "edit_file", "shell"})
    )

    def __post_init__(self) -> None:
        """将策略根目录保存为绝对路径，保证路径检查稳定。"""

        object.__setattr__(self, "root", self.root.resolve())

    def evaluate(self, request: PermissionRequest) -> PermissionDecision:
        """只评估权限规则，不执行交互审批。"""

        # 路径检查优先执行，避免工具即使在其他规则下可执行，也能越过工作区边界。
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
        """拒绝解析后位于工作区根目录之外的路径参数。"""

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
    """将权限策略接入 agent hook 系统。"""

    root: Path
    approver: Approver = field(default_factory=DenyByDefaultApprover)
    policy: PermissionPolicy | None = None

    def __post_init__(self) -> None:
        """调用方未提供策略时创建默认策略。"""

        self.root = self.root.resolve()
        if self.policy is None:
            self.policy = PermissionPolicy(root=self.root)

    def check(self, tool_name: str, arguments: dict[str, object] | None) -> PermissionDecision:
        """返回最终权限决策，必要时会执行审批流程。"""

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
        """工具执行前的 hook 入口，用于阻止被拒绝的工具调用。"""

        if context.event != HookEvent.PRE_TOOL_USE or context.tool_call is None:
            return None

        decision = self.check(context.tool_call.name, context.tool_call.arguments)
        if decision.behavior == PermissionBehavior.ALLOW:
            return None
        return HookResult.block_tool(decision.reason or "权限被拒绝")


def _command_argument(arguments: dict[str, object]) -> str | None:
    """提取 shell 类工具使用的命令字符串。"""

    value = arguments.get("command")
    if value is None:
        return None
    return str(value)


def _preview_arguments(arguments: dict[str, object], *, max_chars: int = 500) -> str:
    """为交互审批提示创建简短的参数预览。"""

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
