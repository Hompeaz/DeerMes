from __future__ import annotations

from deermes.providers.base import ModelProvider, ProviderResponse


class EchoProvider(ModelProvider):
    def complete(self, system_prompt: str, user_prompt: str) -> ProviderResponse:
        text = "\n".join([
            "EchoProvider fallback response.",
            "This run used deterministic local scaffolding instead of a real LLM.",
            "",
            "System prompt excerpt:",
            system_prompt[:200],
            "",
            "User prompt:",
            user_prompt,
        ])
        return ProviderResponse(text=text, metadata={"provider": "echo"})
