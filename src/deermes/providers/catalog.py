from __future__ import annotations

from typing import Final


PROVIDER_DEFAULTS: Final[dict[str, dict[str, object]]] = {
    'echo': {
        'kind': 'echo',
        'base_url': None,
        'api_key_env': None,
        'requires_model': False,
        'requires_base_url': False,
    },
    'ollama': {
        'kind': 'ollama',
        'base_url': None,
        'api_key_env': None,
        'requires_model': False,
        'requires_base_url': False,
    },
    'anthropic': {
        'kind': 'anthropic',
        'base_url': 'https://api.anthropic.com',
        'api_key_env': 'ANTHROPIC_API_KEY',
        'requires_model': True,
        'requires_base_url': False,
    },
    'openai-compatible': {
        'kind': 'openai-compatible',
        'base_url': None,
        'api_key_env': None,
        'requires_model': True,
        'requires_base_url': False,
    },
    'custom-openai-compatible': {
        'kind': 'openai-compatible',
        'base_url': None,
        'api_key_env': None,
        'requires_model': True,
        'requires_base_url': True,
    },
    'openai-api': {
        'kind': 'openai-compatible',
        'base_url': 'https://api.openai.com/v1',
        'api_key_env': 'OPENAI_API_KEY',
        'requires_model': True,
        'requires_base_url': False,
    },
    'openrouter': {
        'kind': 'openai-compatible',
        'base_url': 'https://openrouter.ai/api/v1',
        'api_key_env': 'OPENROUTER_API_KEY',
        'requires_model': True,
        'requires_base_url': False,
    },
    'gemini': {
        'kind': 'openai-compatible',
        'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai',
        'api_key_env': 'GEMINI_API_KEY',
        'requires_model': True,
        'requires_base_url': False,
    },
    'groq': {
        'kind': 'openai-compatible',
        'base_url': 'https://api.groq.com/openai/v1',
        'api_key_env': 'GROQ_API_KEY',
        'requires_model': True,
        'requires_base_url': False,
    },
    'together': {
        'kind': 'openai-compatible',
        'base_url': 'https://api.together.xyz/v1',
        'api_key_env': 'TOGETHER_API_KEY',
        'requires_model': True,
        'requires_base_url': False,
    },
    'fireworks': {
        'kind': 'openai-compatible',
        'base_url': 'https://api.fireworks.ai/inference/v1',
        'api_key_env': 'FIREWORKS_API_KEY',
        'requires_model': True,
        'requires_base_url': False,
    },
    'deepseek': {
        'kind': 'openai-compatible',
        'base_url': 'https://api.deepseek.com/v1',
        'api_key_env': 'DEEPSEEK_API_KEY',
        'requires_model': True,
        'requires_base_url': False,
    },
    'xai': {
        'kind': 'openai-compatible',
        'base_url': 'https://api.x.ai/v1',
        'api_key_env': 'XAI_API_KEY',
        'requires_model': True,
        'requires_base_url': False,
    },
    'perplexity': {
        'kind': 'openai-compatible',
        'base_url': 'https://api.perplexity.ai',
        'api_key_env': 'PERPLEXITY_API_KEY',
        'requires_model': True,
        'requires_base_url': False,
    },
    'lmstudio': {
        'kind': 'openai-compatible',
        'base_url': 'http://127.0.0.1:1234/v1',
        'api_key_env': None,
        'requires_model': True,
        'requires_base_url': False,
    },
}

SUPPORTED_PROVIDER_NAMES = tuple(PROVIDER_DEFAULTS.keys())
OPENAI_COMPATIBLE_PROVIDER_ALIASES = {
    name
    for name, metadata in PROVIDER_DEFAULTS.items()
    if metadata.get('kind') == 'openai-compatible'
}


def is_supported_provider_name(name: str) -> bool:
    return name.strip() in PROVIDER_DEFAULTS


def provider_kind(name: str) -> str:
    value = name.strip()
    if not value:
        return 'echo'
    metadata = PROVIDER_DEFAULTS.get(value)
    if metadata is None:
        return value
    kind = str(metadata.get('kind') or value).strip()
    return kind or value


def normalize_provider_name(name: str) -> str:
    kind = provider_kind(name)
    return kind or 'echo'


def default_base_url_for_provider(name: str) -> str | None:
    metadata = PROVIDER_DEFAULTS.get(name.strip())
    if metadata is None:
        return None
    value = metadata.get('base_url')
    return str(value).strip() or None if value is not None else None


def default_api_key_env_for_provider(name: str) -> str | None:
    metadata = PROVIDER_DEFAULTS.get(name.strip())
    if metadata is None:
        return None
    value = metadata.get('api_key_env')
    return str(value).strip() or None if value is not None else None


def provider_requires_model(name: str) -> bool:
    metadata = PROVIDER_DEFAULTS.get(name.strip())
    if metadata is None:
        return False
    return bool(metadata.get('requires_model', False))


def provider_requires_base_url(name: str) -> bool:
    metadata = PROVIDER_DEFAULTS.get(name.strip())
    if metadata is None:
        return False
    return bool(metadata.get('requires_base_url', False))
