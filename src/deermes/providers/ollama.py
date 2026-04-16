from __future__ import annotations

import json
import os
import urllib.request

from deermes.providers.base import ModelDescriptor, ModelProvider, ProviderResponse


OLLAMA_PLACEHOLDER_MODEL = 'deermes-dev'
OLLAMA_DEFAULT_MODEL_CANDIDATES = (
    'gemma4:31b-it-bf16',
    'gemma4:26b-a4b-it-q4_K_M',
    'gemma4:31b',
)


class OllamaProvider(ModelProvider):
    def __init__(self, model_name: str, base_url: str | None = None, timeout_sec: int | None = None) -> None:
        self.base_url = (base_url or os.getenv('OLLAMA_HOST') or os.getenv('DEERMES_OLLAMA_BASE_URL') or 'http://127.0.0.1:11434').rstrip('/')
        if not self.base_url.startswith('http://') and not self.base_url.startswith('https://'):
            self.base_url = 'http://' + self.base_url
        self.timeout_sec = max(30, int(timeout_sec or os.getenv('DEERMES_OLLAMA_TIMEOUT_SEC', '600')))
        self.model_name = self._resolve_model_name(model_name)

    def complete(self, system_prompt: str, user_prompt: str) -> ProviderResponse:
        chat_payload = {
            'model': self.model_name,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'stream': False,
            'think': False,
        }
        body = self._post('/api/chat', chat_payload)
        message = body.get('message', {})
        text = (message.get('content') or '').strip()

        if not text:
            generate_payload = {
                'model': self.model_name,
                'system': system_prompt,
                'prompt': user_prompt,
                'stream': False,
            }
            generate_body = self._post('/api/generate', generate_payload)
            text = (generate_body.get('response') or '').strip()
            body = generate_body

        if not text:
            text = (message.get('thinking') or body.get('thinking') or '').strip()

        return ProviderResponse(
            text=text,
            metadata={
                'provider': 'ollama',
                'base_url': self.base_url,
                'done_reason': str(body.get('done_reason', '')),
                'timeout_sec': str(self.timeout_sec),
            },
        )

    def list_models(self) -> list[ModelDescriptor]:
        tags_body = self._request_json('/api/tags', method='GET')
        loaded_body = self._request_json('/api/ps', method='GET')
        loaded_names = {
            str(item.get('name') or item.get('model') or ''): item
            for item in loaded_body.get('models', [])
            if str(item.get('name') or item.get('model') or '').strip()
        }

        models: list[ModelDescriptor] = []
        seen: set[str] = set()
        for item in tags_body.get('models', []):
            name = str(item.get('name') or item.get('model') or '').strip()
            if not name or name in seen:
                continue
            seen.add(name)
            details = item.get('details') or {}
            metadata = {
                'parameter_size': str(details.get('parameter_size', '')),
                'quantization_level': str(details.get('quantization_level', '')),
                'modified_at': str(item.get('modified_at', '')),
            }
            models.append(ModelDescriptor(name=name, loaded=name in loaded_names, metadata=metadata))

        for name, item in loaded_names.items():
            if name in seen:
                continue
            metadata = {
                'size_vram': str(item.get('size_vram', '')),
                'expires_at': str(item.get('expires_at', '')),
            }
            models.append(ModelDescriptor(name=name, loaded=True, metadata=metadata))
        return models

    def _post(self, path: str, payload: dict) -> dict:
        return self._request_json(path, payload=payload, method='POST')

    def _request_json(self, path: str, payload: dict | None = None, method: str = 'POST') -> dict:
        request = urllib.request.Request(
            url=f'{self.base_url}{path}',
            data=json.dumps(payload).encode('utf-8') if payload is not None else None,
            headers={'Content-Type': 'application/json'},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
            return json.load(response)

    def _resolve_model_name(self, requested_model_name: str) -> str:
        requested = requested_model_name.strip()
        available_names = [item.name for item in self.list_models()]

        if requested and requested != OLLAMA_PLACEHOLDER_MODEL:
            if available_names and requested not in available_names:
                raise ValueError(
                    f'Ollama model {requested!r} is not available at {self.base_url}. '
                    f'Available models: {", ".join(available_names)}'
                )
            return requested

        configured_default = os.getenv('DEERMES_OLLAMA_DEFAULT_MODEL', '').strip()
        if configured_default and configured_default in available_names:
            return configured_default
        for candidate in OLLAMA_DEFAULT_MODEL_CANDIDATES:
            if candidate in available_names:
                return candidate
        if available_names:
            return available_names[0]
        if requested:
            return requested
        raise RuntimeError(f'No Ollama models are available at {self.base_url}.')
