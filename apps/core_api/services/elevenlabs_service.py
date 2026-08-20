"""Adapter de ElevenLabs (voz) — Fase 4 del roadmap. Contrato en
providers.VoiceProvider; implementación pendiente."""

from apps.core_api.services.providers import VoiceProvider


class ElevenLabsProvider(VoiceProvider):
    def __init__(self, api_key: str) -> None:
        raise NotImplementedError("Integración de voz: Fase 4 del roadmap")

    async def start_session(self, call_id: str) -> dict:
        raise NotImplementedError
