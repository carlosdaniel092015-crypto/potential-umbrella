"""RedisConversationMemory con un stub async en memoria (sin Redis real)."""

from apps.core_api.services.redis_memory_service import RedisConversationMemory


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}

    async def rpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).append(value)

    async def expire(self, key: str, seconds: int) -> None:
        self.ttls[key] = seconds

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self.lists.get(key, [])
        stop = len(items) if stop == -1 else stop + 1
        return items[start if start >= 0 else max(0, len(items) + start) : stop]

    async def delete(self, key: str) -> None:
        self.lists.pop(key, None)


async def test_append_y_recent_conservan_orden_y_ttl():
    redis = FakeRedis()
    memory = RedisConversationMemory(redis, ttl_seconds=100)

    await memory.append(7, "user", "hola")
    await memory.append(7, "assistant", "¡hola!")

    history = await memory.recent(7, limit=10)
    assert history == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "¡hola!"},
    ]
    assert redis.ttls["conv:7:history"] == 100


async def test_recent_respeta_limite():
    redis = FakeRedis()
    memory = RedisConversationMemory(redis)
    for i in range(5):
        await memory.append(1, "user", f"m{i}")
    history = await memory.recent(1, limit=2)
    assert [h["content"] for h in history] == ["m3", "m4"]


async def test_clear_borra_la_conversacion():
    redis = FakeRedis()
    memory = RedisConversationMemory(redis)
    await memory.append(1, "user", "hola")
    await memory.clear(1)
    assert await memory.recent(1) == []
