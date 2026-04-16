from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(slots=True)
class ProviderResponse:
    text: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ModelDescriptor:
    name: str
    loaded: bool = False
    metadata: dict[str, str] = field(default_factory=dict)


class ModelProvider(ABC):
    model_name: str = ''

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> ProviderResponse:
        raise NotImplementedError

    def list_models(self) -> list[ModelDescriptor]:
        model_name = getattr(self, 'model_name', '').strip()
        if not model_name:
            return []
        return [ModelDescriptor(name=model_name, loaded=True)]
