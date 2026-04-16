from __future__ import annotations

import json
import os
import urllib.request

from deermes.providers.base import ModelDescriptor, ModelProvider, ProviderResponse
from deermes.providers.catalog import default_api_key_env_for_provider, default_base_url_for_provider


class OpenAICompatibleProvider(ModelProvider):
    def __init__(
        self,
        model_name: str,
        provider_name: str = 'openai-compatible',
        base_url: str | None = None,
        api_key: str | None = None,
        api_key_env: str | None = None,
        timeout_sec: int | None = None,
    ) -> None:
        self.provider_name = provider_name.strip() or 'openai-compatible'
        self.model_name = model_name.strip()
        self.base_url = self._resolve_base_url(base_url)
        self.api_key_env = (api_key_env or default_api_key_env_for_provider(self.provider_name) or '').strip() or None
        self.api_key = api_key or (os.getenv(self.api_key_env, '') if self.api_key_env else '')
        self.timeout_sec = max(30, int(timeout_sec or os.getenv('DEERMES_OPENAI_TIMEOUT_SEC', '300')))

    def complete(self, system_prompt: str, user_prompt: str) -> ProviderResponse:
        if not self.base_url:
            raise RuntimeError(f'No base URL is configured for provider {self.provider_name!r}.')
        if not self.model_name:
            raise RuntimeError(f'No model is configured for provider {self.provider_name!r}.')

        payload = {
            'model': self.model_name,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
        }
        body = self._request_json('/chat/completions', payload=payload, method='POST')
        choice = (body.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        text = _extract_content_text(message.get('content'))
        if not text:
            text = _extract_content_text(choice.get('delta', {}).get('content'))
        return ProviderResponse(
            text=text,
            metadata={
                'provider': self.provider_name,
                'base_url': self.base_url,
                'api_key_env': self.api_key_env or '',
                'timeout_sec': str(self.timeout_sec),
            },
        )

    def list_models(self) -> list[ModelDescriptor]:
        if not self.base_url:
            return super().list_models()
        try:
            body = self._request_json('/models', method='GET')
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
                'owned_by': str(item.get('owned_by', '')),
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
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    def _resolve_base_url(self, explicit_base_url: str | None) -> str:
        value = (explicit_base_url or '').strip()
        if value:
            return value.rstrip('/')
        provider_default = default_base_url_for_provider(self.provider_name)
        if provider_default:
            return provider_default.rstrip('/')
        env_default = os.getenv('DEERMES_OPENAI_BASE_URL', '').strip()
        return env_default.rstrip('/')


def _extract_content_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                text = str(item.get('text') or item.get('content') or '').strip()
            else:
                text = ''
            if text:
                parts.append(text)
        return '\n'.join(parts).strip()
    if isinstance(content, dict):
        text = content.get('text') or content.get('content') or ''
        return str(text).strip()
    return ''
