from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from deermes.providers import (
    SUPPORTED_PROVIDER_NAMES,
    default_api_key_env_for_provider,
    default_base_url_for_provider,
)

CONTROL_CONFIG_ENV = 'DEERMES_CONFIG_PATH'
DEFAULT_CONTROL_CONFIG_PATH = Path.home() / '.config' / 'deermes' / 'config.json'
DEFAULT_PROVIDER_PROFILE_NAME = 'default'
INIT_PROVIDER_CHOICES = (
    'ollama',
    'anthropic',
    'openai-api',
    'openrouter',
    'gemini',
    'groq',
    'together',
    'fireworks',
    'deepseek',
    'xai',
    'perplexity',
    'lmstudio',
    'custom-openai-compatible',
    'echo',
)


@dataclass(slots=True)
class WorkspaceDefaults:
    project_root: str = '.'
    mode: str = 'single-agent'
    permission_profile: str | None = None
    session_name: str = 'default'
    history_limit: int = 8
    timeout_sec: int | None = None
    context_char_limit: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, object] | None) -> 'WorkspaceDefaults':
        data = payload or {}
        return cls(
            project_root=str(data.get('project_root', '.')).strip() or '.',
            mode=str(data.get('mode', 'single-agent')).strip() or 'single-agent',
            permission_profile=_optional_str(data.get('permission_profile')),
            session_name=str(data.get('session_name', 'default')).strip() or 'default',
            history_limit=max(1, int(data.get('history_limit', 8))),
            timeout_sec=_optional_int(data.get('timeout_sec')),
            context_char_limit=_optional_int(data.get('context_char_limit')),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            'project_root': self.project_root,
            'mode': self.mode,
            'permission_profile': self.permission_profile,
            'session_name': self.session_name,
            'history_limit': self.history_limit,
            'timeout_sec': self.timeout_sec,
            'context_char_limit': self.context_char_limit,
        }


@dataclass(slots=True)
class ProviderProfileConfig:
    name: str
    provider_name: str = 'echo'
    model_name: str = 'deermes-dev'
    base_url: str | None = None
    api_key_env: str | None = None

    @classmethod
    def from_payload(cls, name: str, payload: dict[str, object] | None) -> 'ProviderProfileConfig':
        data = payload or {}
        provider_name = str(data.get('provider_name', 'echo')).strip() or 'echo'
        return cls(
            name=name,
            provider_name=provider_name,
            model_name=str(data.get('model_name', _default_model_name(provider_name))).strip(),
            base_url=_optional_str(data.get('base_url')),
            api_key_env=_optional_str(data.get('api_key_env')),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            'provider_name': self.provider_name,
            'model_name': self.model_name,
            'base_url': self.base_url,
            'api_key_env': self.api_key_env,
        }

    def apply_provider_defaults(self) -> None:
        default_base_url = default_base_url_for_provider(self.provider_name)
        default_api_key_env = default_api_key_env_for_provider(self.provider_name)
        default_model_name = _default_model_name(self.provider_name)
        if default_base_url and not self.base_url:
            self.base_url = default_base_url
        if default_api_key_env and not self.api_key_env:
            self.api_key_env = default_api_key_env
        if not self.model_name:
            self.model_name = default_model_name


@dataclass(slots=True)
class ControlConfig:
    path: Path
    workspace: WorkspaceDefaults = field(default_factory=WorkspaceDefaults)
    active_provider_profile: str = DEFAULT_PROVIDER_PROFILE_NAME
    provider_profiles: dict[str, ProviderProfileConfig] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> 'ControlConfig':
        config_path = resolve_control_config_path(path)
        if not config_path.exists():
            return cls.create_default(config_path)
        try:
            payload = json.loads(config_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            return cls.create_default(config_path)
        if not isinstance(payload, dict):
            return cls.create_default(config_path)

        workspace = WorkspaceDefaults.from_payload(_coerce_dict(payload.get('workspace')))
        raw_profiles = _coerce_dict(payload.get('provider_profiles'))
        profiles: dict[str, ProviderProfileConfig] = {}
        for name, data in raw_profiles.items():
            if not isinstance(data, dict):
                continue
            profile = ProviderProfileConfig.from_payload(name, data)
            profile.apply_provider_defaults()
            profiles[name] = profile
        if not profiles:
            profiles[DEFAULT_PROVIDER_PROFILE_NAME] = default_provider_profile(DEFAULT_PROVIDER_PROFILE_NAME)

        active = str(payload.get('active_provider_profile', DEFAULT_PROVIDER_PROFILE_NAME)).strip() or DEFAULT_PROVIDER_PROFILE_NAME
        if active not in profiles:
            active = next(iter(profiles))

        return cls(
            path=config_path,
            workspace=workspace,
            active_provider_profile=active,
            provider_profiles=profiles,
        )

    @classmethod
    def create_default(cls, path: Path | None = None) -> 'ControlConfig':
        config_path = resolve_control_config_path(path)
        return cls(
            path=config_path,
            workspace=WorkspaceDefaults(),
            active_provider_profile=DEFAULT_PROVIDER_PROFILE_NAME,
            provider_profiles={DEFAULT_PROVIDER_PROFILE_NAME: default_provider_profile(DEFAULT_PROVIDER_PROFILE_NAME)},
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': 1,
            'workspace': self.workspace.to_payload(),
            'active_provider_profile': self.active_provider_profile,
            'provider_profiles': {
                name: profile.to_payload()
                for name, profile in sorted(self.provider_profiles.items())
            },
        }
        self.path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    def active_profile(self) -> ProviderProfileConfig:
        return self.ensure_provider_profile(self.active_provider_profile)

    def ensure_provider_profile(self, name: str | None = None) -> ProviderProfileConfig:
        profile_name = (name or self.active_provider_profile or DEFAULT_PROVIDER_PROFILE_NAME).strip() or DEFAULT_PROVIDER_PROFILE_NAME
        profile = self.provider_profiles.get(profile_name)
        if profile is None:
            profile = default_provider_profile(profile_name)
            self.provider_profiles[profile_name] = profile
        profile.apply_provider_defaults()
        return profile

    def profile_lines(self) -> list[str]:
        lines: list[str] = []
        for name in sorted(self.provider_profiles):
            profile = self.provider_profiles[name]
            marker = ' (active)' if name == self.active_provider_profile else ''
            target = profile.base_url or '(default)'
            lines.append(f'- {name}{marker}: {profile.provider_name}/{profile.model_name or "(unset)"} @ {target}')
        return lines

    def summary_lines(self) -> list[str]:
        profile = self.active_profile()
        api_key_env = profile.api_key_env or '(not required)'
        api_key_state = 'set' if profile.api_key_env and os.getenv(profile.api_key_env, '').strip() else 'unset'
        return [
            f'Control config: {self.path}',
            f'Workspace: {self.workspace.project_root}',
            f'Mode: {self.workspace.mode}',
            f'Permission profile: {self.workspace.permission_profile or "(project default)"}',
            f'Session: {self.workspace.session_name}',
            f'History limit: {self.workspace.history_limit}',
            f'Timeout: {self.workspace.timeout_sec if self.workspace.timeout_sec is not None else "(runtime default)"}',
            f'Context char limit: {self.workspace.context_char_limit if self.workspace.context_char_limit is not None else "(runtime default)"}',
            f'Active provider profile: {self.active_provider_profile}',
            f'Provider: {profile.provider_name}',
            f'Model: {profile.model_name or "(unset)"}',
            f'Base URL: {profile.base_url or "(provider default)"}',
            f'API key env: {api_key_env}',
            f'API key status: {api_key_state}',
        ]


def resolve_control_config_path(path: Path | None = None) -> Path:
    if path is not None:
        return path.expanduser().resolve()
    env_value = os.getenv(CONTROL_CONFIG_ENV, '').strip()
    if env_value:
        return Path(env_value).expanduser().resolve()
    return DEFAULT_CONTROL_CONFIG_PATH


def default_provider_profile(name: str, provider_name: str = 'echo') -> ProviderProfileConfig:
    profile = ProviderProfileConfig(
        name=name,
        provider_name=provider_name,
        model_name=_default_model_name(provider_name),
        base_url=default_base_url_for_provider(provider_name),
        api_key_env=default_api_key_env_for_provider(provider_name),
    )
    profile.apply_provider_defaults()
    return profile


def configured_provider_profile(
    name: str,
    provider_name: str,
    model_name: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
) -> ProviderProfileConfig:
    profile = ProviderProfileConfig(
        name=name,
        provider_name=provider_name.strip() or 'echo',
        model_name=(model_name if model_name is not None else _default_model_name(provider_name)).strip(),
        base_url=(base_url.strip() if base_url else None),
        api_key_env=(api_key_env.strip() if api_key_env else None),
    )
    profile.apply_provider_defaults()
    return profile


def _default_model_name(provider_name: str) -> str:
    value = provider_name.strip()
    if value == 'echo':
        return 'deermes-dev'
    if value == 'ollama':
        return 'deermes-dev'
    return ''


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value in {None, ''}:
        return None
    return int(value)


def _coerce_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return dict(value)
    return {}
