"""Ventana caliente de memoria conversacional en Redis (ADR-03).

Decisión de estado (docs/decisiones-fase-1.md, D-05): el historial CANÓNICO
vive en Postgres (tabla Message); Redis es solo caché caliente con TTL,
reconstruible desde la DB. En Fase 1 el pipeline lee de Postgres; este servicio
queda operativo para cuando el volumen justifique la caché.
"""

import json

DEFAULT_TTL_SECONDS = 24 * 60 * 60  # ventana de 24h de WhatsApp


class RedisConversationMemory:
    def __init__(self, redis_client, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    @staticmethod
    def _key(conversation_id: int | str) -> str:
        return f"conv:{conversation_id}:history"

    async def append(self, conversation_id: int | str, role: str, content: str) -> None:
        key = self._key(conversation_id)
        await self._redis.rpush(key, json.dumps({"role": role, "content": content}))
        await self._redis.expire(key, self._ttl)

    async def recent(self, conversation_id: int | str, limit: int = 10) -> list[dict]:
        raw = await self._redis.lrange(self._key(conversation_id), -limit, -1)
        return [json.loads(item) for item in raw]

    async def clear(self, conversation_id: int | str) -> None:
        await self._redis.delete(self._key(conversation_id))
