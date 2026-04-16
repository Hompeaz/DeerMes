from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from deermes.config import (
    INIT_PROVIDER_CHOICES,
    ControlConfig,
    configured_provider_profile,
    resolve_control_config_path,
)
from deermes.providers import (
    SUPPORTED_PROVIDER_NAMES,
    build_provider,
    default_api_key_env_for_provider,
    default_base_url_for_provider,
    is_supported_provider_name,
    provider_requires_base_url,
    provider_requires_model,
)
from deermes.runtime import build_deerflow_runtime, build_runtime
from deermes.security import PermissionManager


@dataclass(slots=True)
class ResolvedRuntimeConfig:
    project_root: Path
    mode: str
    provider_profile_name: str
    provider_name: str
    model_name: str
    base_url: str | None
    api_key_env: str | None
    permission_profile: str | None
    request_timeout_sec: int | None
    session_context_char_limit: int | None
    session_name: str
    history_limit: int


def add_runtime_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--project-root', default=None, help='Project root to inspect.')
    parser.add_argument('--provider-profile', default=None, help='Provider profile name from the DeerMes control config.')
    parser.add_argument('--provider', default=None, choices=SUPPORTED_PROVIDER_NAMES)
    parser.add_argument('--model', default=None)
    parser.add_argument('--base-url', default=None, help='Optional provider base URL override.')
    parser.add_argument('--mode', default=None, choices=['single-agent', 'deerflow'])
    parser.add_argument('--permission-profile', default=None, help='Permission profile name from deermes.permissions.json.')
    parser.add_argument('--timeout-sec', type=int, default=None, help='Optional provider request timeout override in seconds.')
    parser.add_argument('--context-char-limit', type=int, default=None, help='Optional chat session context size override in characters.')
    parser.add_argument('--api-key-env', default=None, help='Environment variable to read the provider API key from.')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='deermes', description='Run and configure the DeerMes agent.')
    subparsers = parser.add_subparsers(dest='command', required=True)

    run_parser = subparsers.add_parser('run', help='Run an agent task.')
    run_parser.add_argument('goal', help='The task or user request to execute.')
    add_runtime_options(run_parser)

    chat_parser = subparsers.add_parser('chat', aliases=['tui'], help='Open the interactive terminal chat UI.')
    add_runtime_options(chat_parser)
    chat_parser.add_argument('--session', default=None, help='Session name to persist and reload.')
    chat_parser.add_argument('--history-limit', type=int, default=None, help='How many recent chat messages to reuse as context.')

    init_parser = subparsers.add_parser('init', help='Create or update the DeerMes control config.')
    init_parser.add_argument('--config-path', default=None, help='Optional override for the DeerMes control config path.')
    init_parser.add_argument('--profile-name', default=None, help='Provider profile name to create or update.')
    init_parser.add_argument('--provider', default=None, choices=INIT_PROVIDER_CHOICES)
    init_parser.add_argument('--model', default=None)
    init_parser.add_argument('--base-url', default=None)
    init_parser.add_argument('--api-key-env', default=None)
    init_parser.add_argument('--project-root', default=None)
    init_parser.add_argument('--mode', default=None, choices=['single-agent', 'deerflow'])
    init_parser.add_argument('--permission-profile', default=None)
    init_parser.add_argument('--session', default=None)
    init_parser.add_argument('--history-limit', type=int, default=None)
    init_parser.add_argument('--timeout-sec', type=int, default=None)
    init_parser.add_argument('--context-char-limit', type=int, default=None)
    init_parser.add_argument('--non-interactive', action='store_true', help='Use flags and current defaults without prompting.')

    doctor_parser = subparsers.add_parser('doctor', help='Show the active DeerMes control configuration.')
    doctor_parser.add_argument('--config-path', default=None, help='Optional override for the DeerMes control config path.')
    doctor_parser.add_argument('--json', action='store_true', help='Print the resolved control config as JSON.')

    models_parser = subparsers.add_parser('models', help='List models for the selected provider.')
    models_parser.add_argument('--config-path', default=None, help='Optional override for the DeerMes control config path.')
    add_runtime_options(models_parser)

    config_parser = subparsers.add_parser('config', help='Inspect or modify the DeerMes control config.')
    config_parser.add_argument('--config-path', default=None, help='Optional override for the DeerMes control config path.')
    config_subparsers = config_parser.add_subparsers(dest='config_command', required=True)

    config_subparsers.add_parser('show', help='Show the active control configuration.')
    config_subparsers.add_parser('profiles', help='List configured provider profiles.')
    set_parser = config_subparsers.add_parser('set', help='Set a single control config value.')
    set_parser.add_argument(
        'key',
        choices=[
            'project-root',
            'mode',
            'permission-profile',
            'session',
            'history-limit',
            'timeout-sec',
            'context-char-limit',
            'provider-profile',
            'provider',
            'model',
            'base-url',
            'api-key-env',
        ],
    )
    set_parser.add_argument('value')
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == 'init':
        handle_init(args)
        return
    if args.command == 'doctor':
        handle_doctor(args)
        return
    if args.command == 'models':
        handle_models(args)
        return
    if args.command == 'config':
        handle_config(args)
        return

    resolved = resolve_runtime_config(args, allow_modelless=True)

    if args.command in {'chat', 'tui'}:
        from deermes.tui import run_chat_ui

        run_chat_ui(
            project_root=resolved.project_root,
            mode=resolved.mode,
            provider_name=resolved.provider_name,
            model_name=resolved.model_name,
            base_url=resolved.base_url,
            api_key_env=resolved.api_key_env,
            session_name=resolved.session_name,
            history_limit=resolved.history_limit,
            permission_profile=resolved.permission_profile,
            request_timeout_sec=resolved.request_timeout_sec,
            session_context_char_limit=resolved.session_context_char_limit,
        )
        return

    builder = build_runtime if resolved.mode == 'single-agent' else build_deerflow_runtime
    runtime = builder(
        resolved.project_root,
        provider_name=resolved.provider_name,
        model_name=resolved.model_name,
        base_url=resolved.base_url,
        permission_profile=resolved.permission_profile,
        api_key_env=resolved.api_key_env,
        request_timeout_sec=resolved.request_timeout_sec,
        session_context_char_limit=resolved.session_context_char_limit,
    )
    print(runtime.run(args.goal))


def resolve_runtime_config(args: argparse.Namespace, allow_modelless: bool = False) -> ResolvedRuntimeConfig:
    control = load_control_config(getattr(args, 'config_path', None))
    provider_profile_name = (getattr(args, 'provider_profile', None) or control.active_provider_profile).strip() or control.active_provider_profile
    provider_profile = control.ensure_provider_profile(provider_profile_name)

    project_root = Path(getattr(args, 'project_root', None) or control.workspace.project_root or '.').expanduser().resolve()
    mode = getattr(args, 'mode', None) or control.workspace.mode or 'single-agent'
    explicit_provider_name = getattr(args, 'provider', None)
    provider_name = explicit_provider_name or provider_profile.provider_name or 'echo'
    provider_switched = bool(explicit_provider_name and explicit_provider_name != provider_profile.provider_name)

    model_name = getattr(args, 'model', None)
    if model_name is None:
        model_name = '' if provider_switched else provider_profile.model_name
    model_name = model_name.strip() if model_name is not None else ''

    base_url = getattr(args, 'base_url', None)
    if base_url is None:
        base_url = default_base_url_for_provider(provider_name) if provider_switched else (provider_profile.base_url or default_base_url_for_provider(provider_name))
    base_url = base_url.strip() if isinstance(base_url, str) and base_url.strip() else None

    api_key_env = getattr(args, 'api_key_env', None)
    if api_key_env is None:
        api_key_env = default_api_key_env_for_provider(provider_name) if provider_switched else (provider_profile.api_key_env or default_api_key_env_for_provider(provider_name))
    api_key_env = api_key_env.strip() if isinstance(api_key_env, str) and api_key_env.strip() else None

    permission_profile = getattr(args, 'permission_profile', None)
    if permission_profile is None:
        permission_profile = control.workspace.permission_profile

    request_timeout_sec = getattr(args, 'timeout_sec', None)
    if request_timeout_sec is None:
        request_timeout_sec = control.workspace.timeout_sec

    session_context_char_limit = getattr(args, 'context_char_limit', None)
    if session_context_char_limit is None:
        session_context_char_limit = control.workspace.context_char_limit

    session_name = getattr(args, 'session', None) or control.workspace.session_name or 'default'
    history_limit = getattr(args, 'history_limit', None)
    if history_limit is None:
        history_limit = control.workspace.history_limit

    if provider_requires_model(provider_name) and not model_name and not allow_modelless:
        raise ValueError(f'Provider {provider_name!r} requires an explicit model. Run `deermes init` or pass --model.')
    if provider_requires_base_url(provider_name) and not base_url:
        raise ValueError(f'Provider {provider_name!r} requires --base-url or a configured provider profile.')

    return ResolvedRuntimeConfig(
        project_root=project_root,
        mode=mode,
        provider_profile_name=provider_profile_name,
        provider_name=provider_name,
        model_name=model_name,
        base_url=base_url,
        api_key_env=api_key_env,
        permission_profile=permission_profile,
        request_timeout_sec=request_timeout_sec,
        session_context_char_limit=session_context_char_limit,
        session_name=session_name,
        history_limit=max(1, int(history_limit)),
    )


def handle_init(args: argparse.Namespace) -> None:
    config = load_control_config(args.config_path)
    interactive = not args.non_interactive

    current_profile = config.active_profile()
    current_provider = args.provider or current_profile.provider_name or 'ollama'
    if current_provider not in INIT_PROVIDER_CHOICES:
        current_provider = 'custom-openai-compatible' if current_provider == 'openai-compatible' else 'ollama'

    if interactive:
        print(f'Config path: {config.path}')
        print('OpenAI OAuth is not implemented yet in DeerMes. Use API-key providers, Ollama, or a custom OpenAI-compatible endpoint today.')

    provider_name = _resolve_init_provider(args.provider, current_provider, interactive)
    profile_name = _resolve_value(
        provided=args.profile_name,
        prompt='Provider profile name',
        default=config.active_provider_profile or provider_name.replace('-', '_'),
        interactive=interactive,
    )
    project_root_text = _resolve_value(
        provided=args.project_root,
        prompt='Workspace project root',
        default=config.workspace.project_root or str(Path.cwd()),
        interactive=interactive,
    )
    project_root = Path(project_root_text).expanduser().resolve()

    mode = _resolve_choice(
        provided=args.mode,
        prompt='Execution mode',
        choices=('single-agent', 'deerflow'),
        default=config.workspace.mode or 'deerflow',
        interactive=interactive,
    )

    permission_default = args.permission_profile or config.workspace.permission_profile or _detect_default_permission_profile(project_root)
    permission_profile = _resolve_value(
        provided=args.permission_profile,
        prompt='Permission profile',
        default=permission_default,
        interactive=interactive,
        note=_permission_note(project_root) if interactive else None,
    )

    session_name = _resolve_value(
        provided=args.session,
        prompt='Default session name',
        default=config.workspace.session_name or 'default',
        interactive=interactive,
    )
    history_limit = int(_resolve_value(
        provided=str(args.history_limit) if args.history_limit is not None else None,
        prompt='History limit',
        default=str(config.workspace.history_limit or 8),
        interactive=interactive,
    ))
    timeout_sec = int(_resolve_value(
        provided=str(args.timeout_sec) if args.timeout_sec is not None else None,
        prompt='Provider timeout (seconds)',
        default=str(config.workspace.timeout_sec or 600),
        interactive=interactive,
    ))
    context_char_limit = int(_resolve_value(
        provided=str(args.context_char_limit) if args.context_char_limit is not None else None,
        prompt='Session context char limit',
        default=str(config.workspace.context_char_limit or 3500),
        interactive=interactive,
    ))

    base_url_default = args.base_url or current_profile.base_url or default_base_url_for_provider(provider_name) or ''
    api_key_env_default = args.api_key_env or current_profile.api_key_env or default_api_key_env_for_provider(provider_name) or ''
    model_default = args.model if args.model is not None else current_profile.model_name
    if not model_default:
        model_default = ''

    if provider_name == 'ollama' and interactive:
        _print_ollama_models(base_url_default)
        model_prompt = 'Model name (leave blank to auto-resolve from Ollama)'
    elif provider_name == 'echo':
        model_default = 'deermes-dev'
        model_prompt = 'Model name'
    else:
        model_prompt = 'Model name'

    model_name = _resolve_value(
        provided=args.model,
        prompt=model_prompt,
        default=model_default,
        interactive=interactive,
        allow_empty=(provider_name == 'ollama'),
    )
    base_url = _resolve_value(
        provided=args.base_url,
        prompt='Base URL',
        default=base_url_default,
        interactive=interactive,
        allow_empty=not provider_requires_base_url(provider_name),
    )
    api_key_env = _resolve_value(
        provided=args.api_key_env,
        prompt='API key environment variable',
        default=api_key_env_default,
        interactive=interactive,
        allow_empty=provider_name in {'echo', 'ollama', 'lmstudio', 'openai-compatible', 'custom-openai-compatible'},
    )

    if provider_requires_model(provider_name) and not model_name:
        raise ValueError(f'Provider {provider_name!r} requires a model during init.')
    if provider_requires_base_url(provider_name) and not base_url:
        raise ValueError(f'Provider {provider_name!r} requires a base URL during init.')

    profile = configured_provider_profile(
        name=profile_name,
        provider_name=provider_name,
        model_name=model_name,
        base_url=base_url,
        api_key_env=api_key_env,
    )
    config.provider_profiles[profile_name] = profile
    config.active_provider_profile = profile_name
    config.workspace.project_root = str(project_root)
    config.workspace.mode = mode
    config.workspace.permission_profile = permission_profile or None
    config.workspace.session_name = session_name or 'default'
    config.workspace.history_limit = max(1, history_limit)
    config.workspace.timeout_sec = max(30, timeout_sec)
    config.workspace.context_char_limit = max(500, context_char_limit)
    config.save()

    print('DeerMes control config updated.')
    for line in config.summary_lines():
        print(line)


def handle_doctor(args: argparse.Namespace) -> None:
    config = load_control_config(args.config_path)
    if args.json:
        payload = {
            'path': str(config.path),
            'workspace': config.workspace.to_payload(),
            'active_provider_profile': config.active_provider_profile,
            'provider_profiles': {
                name: profile.to_payload()
                for name, profile in sorted(config.provider_profiles.items())
            },
        }
        print(json.dumps(payload, indent=2))
        return
    for line in config.summary_lines():
        print(line)
    print('Provider profiles:')
    for line in config.profile_lines():
        print(line)


def handle_models(args: argparse.Namespace) -> None:
    resolved = resolve_runtime_config(args, allow_modelless=True)
    provider = build_provider(
        provider_name=resolved.provider_name,
        model_name=resolved.model_name,
        base_url=resolved.base_url,
        api_key_env=resolved.api_key_env,
        timeout_sec=resolved.request_timeout_sec,
    )
    models = provider.list_models()
    print(f'Provider: {resolved.provider_name}')
    if resolved.base_url:
        print(f'Base URL: {resolved.base_url}')
    if not models:
        print('No models reported by the provider.')
        return
    for item in models:
        details = []
        if item.metadata.get('parameter_size'):
            details.append(item.metadata['parameter_size'])
        if item.metadata.get('quantization_level'):
            details.append(item.metadata['quantization_level'])
        suffix = f" ({', '.join(details)})" if details else ''
        marker = ' [loaded]' if item.loaded else ''
        print(f'- {item.name}{marker}{suffix}')


def handle_config(args: argparse.Namespace) -> None:
    config = load_control_config(args.config_path)
    if args.config_command == 'show':
        for line in config.summary_lines():
            print(line)
        return
    if args.config_command == 'profiles':
        for line in config.profile_lines():
            print(line)
        return
    if args.config_command == 'set':
        apply_config_set(config, args.key, args.value)
        config.save()
        print(f'Updated {args.key}.')
        for line in config.summary_lines():
            print(line)
        return


def apply_config_set(config: ControlConfig, key: str, value: str) -> None:
    if key == 'project-root':
        config.workspace.project_root = str(Path(value).expanduser().resolve())
        return
    if key == 'mode':
        if value not in {'single-agent', 'deerflow'}:
            raise ValueError('Mode must be `single-agent` or `deerflow`.')
        config.workspace.mode = value
        return
    if key == 'permission-profile':
        config.workspace.permission_profile = value.strip() or None
        return
    if key == 'session':
        config.workspace.session_name = value.strip() or 'default'
        return
    if key == 'history-limit':
        config.workspace.history_limit = max(1, int(value))
        return
    if key == 'timeout-sec':
        config.workspace.timeout_sec = max(30, int(value))
        return
    if key == 'context-char-limit':
        config.workspace.context_char_limit = max(500, int(value))
        return
    if key == 'provider-profile':
        profile_name = value.strip() or 'default'
        config.active_provider_profile = profile_name
        config.ensure_provider_profile(profile_name)
        return

    profile = config.active_profile()
    if key == 'provider':
        if not is_supported_provider_name(value):
            raise ValueError(f'Unsupported provider: {value}')
        updated = configured_provider_profile(
            name=profile.name,
            provider_name=value,
            model_name=profile.model_name,
            base_url=profile.base_url,
            api_key_env=profile.api_key_env,
        )
        config.provider_profiles[profile.name] = updated
        return
    if key == 'model':
        profile.model_name = value.strip()
        profile.apply_provider_defaults()
        return
    if key == 'base-url':
        profile.base_url = value.strip() or None
        profile.apply_provider_defaults()
        return
    if key == 'api-key-env':
        profile.api_key_env = value.strip() or None
        profile.apply_provider_defaults()
        return
    raise ValueError(f'Unsupported config key: {key}')


def load_control_config(config_path: str | None) -> ControlConfig:
    path = resolve_control_config_path(Path(config_path).expanduser()) if config_path else resolve_control_config_path()
    return ControlConfig.load(path)


def _resolve_init_provider(provided: str | None, current: str, interactive: bool) -> str:
    if provided:
        return provided
    if not interactive:
        return current
    print('Provider choices:')
    for index, name in enumerate(INIT_PROVIDER_CHOICES, start=1):
        print(f'  {index}. {name}')
    while True:
        raw = input(f'Provider [{current}]: ').strip()
        if not raw:
            return current
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(INIT_PROVIDER_CHOICES):
                return INIT_PROVIDER_CHOICES[index - 1]
        if raw in INIT_PROVIDER_CHOICES:
            return raw
        print('Choose one of the listed provider names or numbers.')


def _resolve_choice(provided: str | None, prompt: str, choices: tuple[str, ...], default: str, interactive: bool) -> str:
    if provided:
        return provided
    if not interactive:
        return default
    note = f"Choices: {', '.join(choices)}"
    return _resolve_value(None, prompt, default, interactive=True, validator=lambda value: value in choices, note=note)


def _resolve_value(
    provided: str | None,
    prompt: str,
    default: str,
    interactive: bool,
    allow_empty: bool = False,
    validator=None,
    note: str | None = None,
) -> str:
    if provided is not None:
        return provided.strip()
    if not interactive:
        return default.strip()
    if note:
        print(note)
    while True:
        raw = input(f'{prompt} [{default}]: ').strip()
        if not raw:
            raw = default.strip()
        if raw or allow_empty:
            if validator is None or validator(raw):
                return raw
        print('Please provide a valid value.')


def _detect_default_permission_profile(project_root: Path) -> str | None:
    try:
        manager = PermissionManager.load(project_root)
    except Exception:
        return None
    return manager.active_profile_name


def _permission_note(project_root: Path) -> str | None:
    try:
        manager = PermissionManager.load(project_root)
    except Exception:
        return None
    lines = ['Available permission profiles:']
    lines.extend(manager.profile_summaries())
    return '\n'.join(lines)


def _print_ollama_models(base_url: str | None) -> None:
    try:
        provider = build_provider('ollama', model_name='deermes-dev', base_url=base_url)
        models = provider.list_models()
    except Exception as exc:
        print(f'Unable to load Ollama models: {exc}')
        return
    if not models:
        print('No Ollama models were reported by the configured endpoint.')
        return
    print('Ollama models:')
    for item in models:
        marker = ' [loaded]' if item.loaded else ''
        print(f'- {item.name}{marker}')


if __name__ == '__main__':
    main()
