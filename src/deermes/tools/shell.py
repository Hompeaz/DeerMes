from __future__ import annotations

import shlex
import subprocess

from deermes.security import ToolInvocation
from deermes.tools.base import ArtifactRecord, Tool, ToolResult


class ShellTool(Tool):
    name = 'shell'
    description = 'Run a restricted shell command inside the current project.'

    def __init__(self, cwd: str, allowed_commands: list[str] | None = None) -> None:
        self.cwd = cwd
        self.allowed_commands = set(allowed_commands or ['*'])

    def describe_invocation(self, input_text: str) -> ToolInvocation:
        command = input_text.strip()
        argv = tuple(shlex.split(command)) if command else ()
        command_text = ' '.join(argv) if argv else '(empty command)'
        return ToolInvocation(
            tool_name=self.name,
            action='shell',
            summary=f'Run shell command: {command_text}',
            command=argv,
            target_display=command_text,
        )

    def invoke(self, input_text: str) -> ToolResult:
        command = input_text.strip()
        if not command:
            return ToolResult(tool_name=self.name, output_text='empty command', ok=False, error_type='EmptyCommand')

        argv = shlex.split(command)
        if not argv:
            return ToolResult(tool_name=self.name, output_text='empty command', ok=False, error_type='EmptyCommand')

        if argv[0] not in self.allowed_commands and '*' not in self.allowed_commands:
            return ToolResult(
                tool_name=self.name,
                output_text=f'command not allowed: {argv[0]}',
                ok=False,
                error_type='CommandNotAllowed',
            )

        completed = subprocess.run(argv, cwd=self.cwd, text=True, capture_output=True, check=False)
        output = (completed.stdout + completed.stderr).strip() or '(empty output)'
        artifact = ArtifactRecord(
            kind='command',
            tool_name=self.name,
            summary=' '.join(argv),
            verified=completed.returncode == 0,
            metadata={'returncode': str(completed.returncode)},
        )
        if completed.returncode != 0:
            return ToolResult(
                tool_name=self.name,
                output_text=f'exit code {completed.returncode}: {output}',
                ok=False,
                error_type='CommandFailed',
                artifacts=(artifact,),
            )
        return ToolResult(tool_name=self.name, output_text=output, artifacts=(artifact,))
