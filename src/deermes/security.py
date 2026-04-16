from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_PERMISSION_CONFIG = {
    'version': 1,
    'default_profile': 'workspace-write',
    'profiles': {
        'read-only': {
            'description': 'Read only inside the project root. No writes. No shell access.',
            'read_roots': ['{project_root}'],
            'write_roots': [],
            'allow_shell': False,
            'allowed_commands': [],
            'approval_required_for': [],
        },
        'workspace-write': {
            'description': 'Read and write inside the project root. Require approval for shell, writes, and reads outside configured roots.',
            'read_roots': ['{project_root}'],
            'write_roots': ['{project_root}'],
            'allow_shell': True,
            'allowed_commands': ['pwd', 'ls', 'find', 'cat', 'head', 'tail', 'wc', 'rg', 'git'],
            'approval_required_for': ['read_outside_roots', 'write', 'shell'],
        },
        'privileged': {
            'description': 'Read and write inside the project root and home directory. Allow any shell command without per-call approval.',
            'read_roots': ['{project_root}', '{home}'],
            'write_roots': ['{project_root}', '{home}'],
            'allow_shell': True,
            'allowed_commands': ['*'],
            'approval_required_for': [],
        },
    },
}


@dataclass(slots=True)
class ToolInvocation:
    tool_name: str
    action: str
    summary: str
    path: Path | None = None
    command: tuple[str, ...] = ()
    target_display: str = ''


@dataclass(slots=True)
class ApprovalRequest:
    profile_name: str
    tool_name: str
    action: str
    summary: str
    reason: str
    target_display: str = ''

    def render(self) -> str:
        details = [
            f'Approval required for profile `{self.profile_name}`.',
            f'Tool: `{self.tool_name}`',
            f'Action: `{self.action}`',
            f'Request: {self.summary}',
            f'Reason: {self.reason}',
        ]
        if self.target_display:
            details.append(f'Target: {self.target_display}')
        details.append('Type `/approve` or `/deny`.')
        return '\n'.join(details)


@dataclass(slots=True)
class PermissionDecision:
    allowed: bool
    requires_approval: bool = False
    reason: str = ''
    request: ApprovalRequest | None = None


@dataclass(slots=True)
class PermissionProfile:
    name: str
    description: str
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    allow_shell: bool
    allowed_commands: tuple[str, ...]
    approval_required_for: frozenset[str] = field(default_factory=frozenset)

    def command_allowed(self, command_name: str) -> bool:
        if '*' in self.allowed_commands:
            return True
        return command_name in self.allowed_commands


class PermissionManager:
    def __init__(
        self,
        project_root: Path,
        config_path: Path,
        default_profile_name: str,
        profiles: dict[str, PermissionProfile],
        active_profile_name: str,
    ) -> None:
        self.project_root = project_root
        self.config_path = config_path
        self.default_profile_name = default_profile_name
        self.profiles = profiles
        self.active_profile_name = active_profile_name
        self.active_profile = profiles[active_profile_name]

    @classmethod
    def load(cls, project_root: Path, requested_profile: str | None = None) -> 'PermissionManager':
        config_path = ensure_permissions_config(project_root)
        payload = json.loads(config_path.read_text(encoding='utf-8'))
        raw_profiles = payload.get('profiles', {})
        if not isinstance(raw_profiles, dict) or not raw_profiles:
            raise ValueError(f'Invalid permission config: {config_path}')

        default_profile_name = str(payload.get('default_profile', '')).strip() or 'workspace-write'
        active_profile_name = requested_profile or default_profile_name
        if active_profile_name not in raw_profiles:
            raise ValueError(f'Unknown permission profile: {active_profile_name}')

        profiles: dict[str, PermissionProfile] = {}
        for name, data in raw_profiles.items():
            if not isinstance(data, dict):
                continue
            profiles[name] = PermissionProfile(
                name=name,
                description=str(data.get('description', '')).strip() or f'Permission profile {name}.',
                read_roots=_resolve_roots(project_root, data.get('read_roots', [])),
                write_roots=_resolve_roots(project_root, data.get('write_roots', [])),
                allow_shell=bool(data.get('allow_shell', False)),
                allowed_commands=tuple(str(item).strip() for item in data.get('allowed_commands', []) if str(item).strip()),
                approval_required_for=frozenset(
                    str(item).strip() for item in data.get('approval_required_for', []) if str(item).strip()
                ),
            )

        if active_profile_name not in profiles:
            raise ValueError(f'Unknown permission profile: {active_profile_name}')

        return cls(
            project_root=project_root,
            config_path=config_path,
            default_profile_name=default_profile_name,
            profiles=profiles,
            active_profile_name=active_profile_name,
        )

    def authorize(self, invocation: ToolInvocation) -> PermissionDecision:
        if invocation.action == 'read':
            return self._authorize_path(invocation, roots=self.active_profile.read_roots, any_token='read', outside_token='read_outside_roots')
        if invocation.action == 'write':
            return self._authorize_path(invocation, roots=self.active_profile.write_roots, any_token='write', outside_token='write_outside_roots')
        if invocation.action == 'shell':
            return self._authorize_shell(invocation)
        return PermissionDecision(allowed=True)

    def describe_for_prompt(self) -> str:
        profile = self.active_profile
        command_text = ', '.join(profile.allowed_commands) if profile.allowed_commands else 'none'
        approval_text = ', '.join(sorted(profile.approval_required_for)) if profile.approval_required_for else 'none'
        read_text = '\n'.join(f'- {item}' for item in profile.read_roots) or '- none'
        write_text = '\n'.join(f'- {item}' for item in profile.write_roots) or '- none'
        return '\n'.join([
            f'Active permission profile: {profile.name}',
            f'Description: {profile.description}',
            'Allowed read roots:',
            read_text,
            'Allowed write roots:',
            write_text,
            f'Shell enabled: {profile.allow_shell}',
            f'Allowed shell commands: {command_text}',
            f'Approval-required actions: {approval_text}',
        ])

    def profile_summaries(self) -> list[str]:
        summaries: list[str] = []
        for name in sorted(self.profiles):
            profile = self.profiles[name]
            marker = ' (active)' if name == self.active_profile_name else ''
            summaries.append(f'- {name}{marker}: {profile.description}')
        return summaries

    def _authorize_path(
        self,
        invocation: ToolInvocation,
        roots: tuple[Path, ...],
        any_token: str,
        outside_token: str,
    ) -> PermissionDecision:
        path = invocation.path
        if path is None:
            return PermissionDecision(allowed=False, reason='No path target was provided for the permission check.')

        inside = any(_is_within(path, root) for root in roots)
        if inside:
            requires_approval = any_token in self.active_profile.approval_required_for
            request = None
            if requires_approval:
                request = ApprovalRequest(
                    profile_name=self.active_profile_name,
                    tool_name=invocation.tool_name,
                    action=invocation.action,
                    summary=invocation.summary,
                    reason=f'The active profile requires approval for `{invocation.action}` actions.',
                    target_display=invocation.target_display or str(path),
                )
            return PermissionDecision(allowed=True, requires_approval=requires_approval, request=request)

        if outside_token in self.active_profile.approval_required_for:
            request = ApprovalRequest(
                profile_name=self.active_profile_name,
                tool_name=invocation.tool_name,
                action=invocation.action,
                summary=invocation.summary,
                reason=f'The target is outside the configured {invocation.action} sandbox roots.',
                target_display=invocation.target_display or str(path),
            )
            return PermissionDecision(allowed=True, requires_approval=True, request=request)

        return PermissionDecision(
            allowed=False,
            reason=f'The target `{invocation.target_display or path}` is outside the allowed {invocation.action} roots for profile `{self.active_profile_name}`.',
        )

    def _authorize_shell(self, invocation: ToolInvocation) -> PermissionDecision:
        if not self.active_profile.allow_shell:
            return PermissionDecision(allowed=False, reason=f'Shell access is disabled for profile `{self.active_profile_name}`.')

        command_name = invocation.command[0] if invocation.command else ''
        if not command_name:
            return PermissionDecision(allowed=False, reason='No shell command was provided.')

        if not self.active_profile.command_allowed(command_name):
            return PermissionDecision(
                allowed=False,
                reason=f'Command `{command_name}` is not in the allowlist for profile `{self.active_profile_name}`.',
            )

        requires_approval = 'shell' in self.active_profile.approval_required_for
        request = None
        if requires_approval:
            request = ApprovalRequest(
                profile_name=self.active_profile_name,
                tool_name=invocation.tool_name,
                action='shell',
                summary=invocation.summary,
                reason='The active profile requires approval for shell commands.',
                target_display=invocation.target_display,
            )
        return PermissionDecision(allowed=True, requires_approval=requires_approval, request=request)


def ensure_permissions_config(project_root: Path) -> Path:
    config_path = project_root / 'deermes.permissions.json'
    if config_path.exists():
        return config_path
    config_path.write_text(json.dumps(DEFAULT_PERMISSION_CONFIG, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    return config_path


def _resolve_roots(project_root: Path, raw_values: object) -> tuple[Path, ...]:
    if not isinstance(raw_values, list):
        return ()
    roots: list[Path] = []
    mapping = {
        'project_root': str(project_root),
        'home': str(Path.home()),
    }
    for raw_value in raw_values:
        text = str(raw_value).strip()
        if not text:
            continue
        expanded = text.format(**mapping).strip()
        roots.append(Path(expanded).expanduser().resolve())
    return tuple(roots)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
