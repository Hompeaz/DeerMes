from .anthropic import AnthropicProvider
from .base import ModelDescriptor, ModelProvider, ProviderResponse
from .catalog import (
    OPENAI_COMPATIBLE_PROVIDER_ALIASES,
    SUPPORTED_PROVIDER_NAMES,
    default_api_key_env_for_provider,
    default_base_url_for_provider,
    is_supported_provider_name,
    normalize_provider_name,
    provider_kind,
    provider_requires_base_url,
    provider_requires_model,
)
from .echo import EchoProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider


def build_provider(
    provider_name: str,
    model_name: str,
    base_url: str | None = None,
    timeout_sec: int | None = None,
    api_key_env: str | None = None,
) -> ModelProvider:
    normalized = normalize_provider_name(provider_name)
    if normalized == 'openai-compatible':
        return OpenAICompatibleProvider(
            model_name=model_name,
            provider_name=provider_name,
            base_url=base_url,
            timeout_sec=timeout_sec,
            api_key_env=api_key_env,
        )
    if normalized == 'anthropic':
        return AnthropicProvider(
            model_name=model_name,
            base_url=base_url,
            timeout_sec=timeout_sec,
            api_key_env=api_key_env,
        )
    if normalized == 'ollama':
        return OllamaProvider(model_name=model_name, base_url=base_url, timeout_sec=timeout_sec)
    return EchoProvider()


__all__ = [
    'ModelDescriptor',
    'ModelProvider',
    'ProviderResponse',
    'AnthropicProvider',
    'EchoProvider',
    'OllamaProvider',
    'OpenAICompatibleProvider',
    'SUPPORTED_PROVIDER_NAMES',
    'OPENAI_COMPATIBLE_PROVIDER_ALIASES',
    'default_api_key_env_for_provider',
    'default_base_url_for_provider',
    'is_supported_provider_name',
    'normalize_provider_name',
    'provider_kind',
    'provider_requires_base_url',
    'provider_requires_model',
    'build_provider',
]
