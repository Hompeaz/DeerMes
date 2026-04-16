from __future__ import annotations

from pathlib import Path

from deermes.config import ToolSpec
from deermes.security import PermissionManager
from deermes.tools.base import ApprovalCallback, Tool, ToolRegistry
from deermes.tools.filesystem import (
    CreateFileTool,
    FindFilesTool,
    PatchFileTool,
    ReadFileTool,
    WriteFileAtomicTool,
    WriteNoteTool,
)
from deermes.tools.shell import ShellTool


class ToolFactory:
    def __init__(
        self,
        project_root: Path,
        permission_manager: PermissionManager | None = None,
        approval_callback: ApprovalCallback | None = None,
    ) -> None:
        self.project_root = project_root
        self.permission_manager = permission_manager
        self.approval_callback = approval_callback

    def build_registry(self, specs: tuple[ToolSpec, ...]) -> ToolRegistry:
        registry = ToolRegistry(permission_manager=self.permission_manager, approval_callback=self.approval_callback)
        for spec in specs:
            if not spec.enabled:
                continue
            registry.register(self.create(spec))
        return registry

    def create(self, spec: ToolSpec) -> Tool:
        options = dict(spec.options)

        if spec.name == 'find_files':
            return FindFilesTool(self.project_root, limit=int(options.get('limit', 200)))
        if spec.name == 'read_file':
            return ReadFileTool(self.project_root)
        if spec.name == 'write_note':
            return WriteNoteTool(self.project_root)
        if spec.name == 'create_file':
            return CreateFileTool(self.project_root)
        if spec.name == 'write_file_atomic':
            return WriteFileAtomicTool(self.project_root)
        if spec.name == 'patch_file':
            return PatchFileTool(self.project_root)
        if spec.name == 'shell':
            commands = options.get('allowed_commands')
            command_list = list(commands) if isinstance(commands, list) else None
            return ShellTool(str(self.project_root), allowed_commands=command_list)

        raise ValueError(f'unknown tool spec: {spec.name}')
