"""Interfaces del patrón Adapter (§1.1): el core depende de estas abstracciones,
nunca de un proveedor concreto. Cambiar YCloud/OpenAI/Odoo/ElevenLabs = escribir
otro adapter, sin tocar pipeline ni grafos."""

from abc import ABC, abstractmethod


class MessagingProvider(ABC):
    @abstractmethod
    async def send_text(self, to: str, text: str) -> str:
        """Envía un mensaje de texto. Devuelve el id externo del mensaje."""


class LLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        messages: list[dict],
        model: str,
        temperature: float = 0.4,
    ) -> str:
        """Genera una respuesta. `messages` = [{"role": "user"|"assistant", "content": str}]."""


class ERPProvider(ABC):
    """Contrato hacia Odoo (Fase 3). Definido ya para fijar el límite del sistema."""

    @abstractmethod
    async def get_partner_by_phone(self, phone: str) -> dict | None: ...

    @abstractmethod
    async def create_order(self, partner_id: int, lines: list[dict], idempotency_key: str) -> dict:
        """idempotency_key se propaga como client_order_ref: nunca crear dos veces."""


class VoiceProvider(ABC):
    """Contrato hacia ElevenLabs (Fase 4)."""

    @abstractmethod
    async def start_session(self, call_id: str) -> dict: ...
