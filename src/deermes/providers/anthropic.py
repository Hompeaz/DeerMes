from __future__ import annotations

import json
import os
import urllib.request

from deermes.providers.base import ModelDescriptor, ModelProvider, ProviderResponse
from deermes.providers.catalog import default_api_key_env_for_provider, default_base_url_for_provider


class AnthropicProvider(ModelProvider):
    def __init__(
        self,
        model_name: str,
        base_url: str | None = None,
        api_key: str | None = None,
        api_key_env: str | None = None,
        timeout_sec: int | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.provider_name = 'anthropic'
        self.model_name = model_name.strip()
        self.base_url = (base_url or default_base_url_for_provider('anthropic') or 'https://api.anthropic.com').rstrip('/')
        self.api_key_env = (api_key_env or default_api_key_env_for_provider('anthropic') or 'ANTHROPIC_API_KEY').strip()
        self.api_key = api_key or os.getenv(self.api_key_env, '')
        self.timeout_sec = max(30, int(timeout_sec or os.getenv('DEERMES_ANTHROPIC_TIMEOUT_SEC', '300')))
        self.max_tokens = max(256, int(max_tokens or os.getenv('DEERMES_ANTHROPIC_MAX_TOKENS', '4096')))

    def complete(self, system_prompt: str, user_prompt: str) -> ProviderResponse:
        if not self.model_name:
            raise RuntimeError('No model is configured for provider anthropic.')
        if not self.api_key:
            raise RuntimeError('API key environment variable %r is not set.' % self.api_key_env)
        payload = {
            'model': self.model_name,
            'system': system_prompt,
            'max_tokens': self.max_tokens,
            'messages': [
                {
                    'role': 'user',
                    'content': user_prompt,
                }
            ],
        }
        body = self._request_json('/v1/messages', payload=payload, method='POST')
        content = body.get('content') or []
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get('type') or '').strip() != 'text':
                continue
            text = str(item.get('text') or '').strip()
            if text:
                parts.append(text)
        return ProviderResponse(
            text='\n'.join(parts).strip(),
            metadata={
                'provider': 'anthropic',
                'base_url': self.base_url,
                'api_key_env': self.api_key_env,
                'timeout_sec': str(self.timeout_sec),
                'stop_reason': str(body.get('stop_reason', '')),
            },
        )

    def list_models(self) -> list[ModelDescriptor]:
        if not self.api_key:
            return super().list_models()
        try:
            body = self._request_json('/v1/models', method='GET')
        except Exception:
            return super().list_models()
        models: list[ModelDescriptor] = []
        seen: set[str] = set()
        for item in body.get('data', []):
            name = str(item.get('id') or item.get('name') or '').strip()
            if not name or name in seen:
                continue
            seen.add(name)
            metadata = {
                'display_name': str(item.get('display_name', '')),
                'created_at': str(item.get('created_at', '')),
            }
            models.append(ModelDescriptor(name=name, metadata=metadata))
        if not models:
            return super().list_models()
        return models

    def _request_json(self, path: str, payload: dict | None = None, method: str = 'POST') -> dict:
        request = urllib.request.Request(
            url=f'{self.base_url}{path}',
            data=json.dumps(payload).encode('utf-8') if payload is not None else None,
            headers=self._headers(),
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
            return json.load(response)

    def _headers(self) -> dict[str, str]:
        return {
            'Content-Type': 'application/json',
            'x-api-key': self.api_key,
            'anthropic-version': '2023-06-01',
        }
