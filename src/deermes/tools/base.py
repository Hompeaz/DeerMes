from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from deermes.security import ApprovalRequest, PermissionManager, ToolInvocation


ApprovalCallback = Callable[[ApprovalRequest], bool]


@dataclass(slots=True)
class ArtifactRecord:
    kind: str
    tool_name: str
    path: str = ''
    summary: str = ''
    verified: bool = False
    metadata: dict[str, str] = field(default_factory=dict)

    def as_text(self) -> str:
        status = 'verified' if self.verified else 'unverified'
        target = self.path or self.summary or '(no target)'
        detail = f' [{status}]' if self.kind.startswith('file_') else ''
        return f'{self.kind}{detail}: {target}'

    def to_payload(self) -> dict[str, object]:
        return {
            'kind': self.kind,
            'tool_name': self.tool_name,
            'path': self.path,
            'summary': self.summary,
            'verified': self.verified,
            'metadata': dict(self.metadata),
        }


@dataclass(slots=True)
class ToolResult:
    tool_name: str
    output_text: str
    ok: bool = True
    error_type: str = ''
    artifacts: tuple[ArtifactRecord, ...] = field(default_factory=tuple)

    def as_observation(self, limit: int = 2000) -> str:
        text = self.output_text.strip() or '(empty output)'
        clipped = text[:limit]
        if self.ok:
            return f'{self.tool_name}: {clipped}'
        error_type = self.error_type or 'ToolError'
        return f'tool_error[{self.tool_name}][{error_type}]: {clipped}'


class Tool(ABC):
    name: str
    description: str

    @abstractmethod
    def describe_invocation(self, input_text: str) -> ToolInvocation:
        raise NotImplementedError

    @abstractmethod
    def invoke(self, input_text: str) -> ToolResult:
        raise NotImplementedError


class ToolRegistry:
    def __init__(
        self,
        permission_manager: PermissionManager | None = None,
        approval_callback: ApprovalCallback | None = None,
    ) -> None:
        self._tools: dict[str, Tool] = {}
        self.permission_manager = permission_manager
        self.approval_callback = approval_callback

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def has(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def subset(self, names: tuple[str, ...]) -> 'ToolRegistry':
        registry = ToolRegistry(permission_manager=self.permission_manager, approval_callback=self.approval_callback)
        for name in names:
            if name in self._tools:
                registry.register(self._tools[name])
        return registry

    def describe(self) -> str:
        lines = []
        for name in self.names():
            tool = self._tools[name]
            lines.append(f'- {tool.name}: {tool.description}')
        return '\n'.join(lines)

    def invoke(self, tool_name: str, input_text: str) -> ToolResult:
        if tool_name not in self._tools:
            return ToolResult(
                tool_name=tool_name,
                output_text=f'unknown tool: {tool_name}',
                ok=False,
                error_type='UnknownTool',
            )

        tool = self._tools[tool_name]
        try:
            invocation = tool.describe_invocation(input_text)
        except Exception as exc:
            return ToolResult(
                tool_name=tool_name,
                output_text=str(exc) or repr(exc),
                ok=False,
                error_type=type(exc).__name__,
            )

        decision = self.permission_manager.authorize(invocation) if self.permission_manager else None
        if decision and not decision.allowed:
            return ToolResult(
                tool_name=tool_name,
                output_text=decision.reason or 'permission denied',
                ok=False,
                error_type='PermissionDenied',
            )
        if decision and decision.requires_approval:
            if self.approval_callback is None:
                return ToolResult(
                    tool_name=tool_name,
                    output_text=decision.reason or 'approval required but no approval handler is available',
                    ok=False,
                    error_type='ApprovalRequired',
                )
            approved = self.approval_callback(decision.request) if decision.request else False
            if not approved:
                return ToolResult(
                    tool_name=tool_name,
                    output_text='The user denied approval for this tool invocation.',
                    ok=False,
                    error_type='ApprovalDenied',
                )

        try:
            return tool.invoke(input_text)
        except Exception as exc:
            return ToolResult(
                tool_name=tool_name,
                output_text=str(exc) or repr(exc),
                ok=False,
                error_type=type(exc).__name__,
            )
