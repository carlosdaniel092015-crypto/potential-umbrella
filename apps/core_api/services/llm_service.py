"""Adapter LLM (§1.1). El proveedor concreto se elige por configuración:
`fake` corre sin credenciales (dev/tests) y `openai` usa el SDK oficial."""

import logging
from typing import Any, cast

from apps.core_api.config import Settings
from apps.core_api.services.providers import LLMProvider
from apps.core_api.services.resilience import CircuitBreaker

logger = logging.getLogger(__name__)


class OpenAILLMProvider(LLMProvider):
    def __init__(self, api_key: str) -> None:
        # Import diferido: el SDK solo se carga si este provider se usa.
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._breaker = CircuitBreaker()

    async def complete(
        self,
        system_prompt: str,
        messages: list[dict],
        model: str,
        temperature: float = 0.4,
    ) -> str:
        async def _call() -> str:
            # cast: el contrato interno usa list[dict] genérico (independiente
            # del proveedor); el SDK pide sus propios TypedDict por rol.
            payload = cast(
                "list[Any]", [{"role": "system", "content": system_prompt}, *messages]
            )
            response = await self._client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=payload,
            )
            return response.choices[0].message.content or ""

        return await self._breaker.call(_call)


class FakeLLMProvider(LLMProvider):
    """Determinista y sin red. Guarda las llamadas para aserciones en tests."""

    def __init__(self, canned_reply: str | None = None) -> None:
        self.calls: list[dict] = []
        self._canned = canned_reply

    async def complete(
        self,
        system_prompt: str,
        messages: list[dict],
        model: str,
        temperature: float = 0.4,
    ) -> str:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": messages,
                "model": model,
                "temperature": temperature,
            }
        )
        if self._canned is not None:
            return self._canned
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"), ""
        )
        return f"[respuesta simulada] Recibí: {last_user[:80]}"


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("LLM_PROVIDER=openai requiere OPENAI_API_KEY")
        return OpenAILLMProvider(api_key=settings.openai_api_key)
    return FakeLLMProvider()
