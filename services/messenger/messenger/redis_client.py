import json
from collections.abc import AsyncIterator

from redis.asyncio import Redis

from .config import get_settings


_redis: Redis | None = None


async def open_redis() -> None:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(
            get_settings().redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=5,
        )
    await _redis.ping()


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def client() -> Redis:
    if _redis is None:
        raise RuntimeError("Redis is not initialized")
    return _redis


async def redis_ready() -> bool:
    try:
        return bool(await client().ping())
    except Exception:
        return False


async def allow_rate(key: str, limit: int, window_seconds: int = 60) -> bool:
    redis = client()
    namespaced = f"messenger:rate:{key}"
    count = await redis.incr(namespaced)
    if count == 1:
        await redis.expire(namespaced, window_seconds)
    return count <= limit


async def acquire_connection_slot(username: str, limit: int, ttl: int = 90) -> str | None:
    redis = client()
    sequence = await redis.incr(f"messenger:connection-seq:{username}")
    slot = f"messenger:connection:{username}:{sequence}"
    await redis.set(slot, "1", ex=ttl)
    keys = [key async for key in redis.scan_iter(match=f"messenger:connection:{username}:*")]
    if len(keys) > limit:
        await redis.delete(slot)
        return None
    return slot


async def refresh_connection_slot(slot: str, ttl: int = 90) -> None:
    await client().expire(slot, ttl)


async def count_connection_slots() -> dict[str, int]:
    """Live WebSocket sessions per user, for the admin panel. Slots carry a
    TTL and are refreshed by heartbeats, so an abandoned tab drops out on its
    own without needing an explicit disconnect."""
    redis = client()
    counts: dict[str, int] = {}
    async for key in redis.scan_iter(match="messenger:connection:*"):
        parts = key.split(":")
        if len(parts) < 4:
            continue
        username = ":".join(parts[2:-1])
        counts[username] = counts.get(username, 0) + 1
    return counts


async def release_connection_slot(slot: str | None) -> None:
    if slot:
        await client().delete(slot)


async def publish_chat(chat_id: str, event: dict) -> None:
    await client().publish(f"messenger:chat:{chat_id}", json.dumps(event, ensure_ascii=False))


async def publish_presence(event: dict) -> None:
    await client().publish("messenger:presence", json.dumps(event, ensure_ascii=False))


async def subscribe_presence():
    pubsub = client().pubsub()
    await pubsub.subscribe("messenger:presence")
    try:
        async for item in pubsub.listen():
            if item["type"] != "message":
                continue
            yield json.loads(item["data"])
    finally:
        await pubsub.unsubscribe("messenger:presence")
        await pubsub.aclose()


async def subscribe_chat(chat_id: str) -> AsyncIterator[dict]:
    pubsub = client().pubsub()
    await pubsub.subscribe(f"messenger:chat:{chat_id}")
    try:
        async for item in pubsub.listen():
            if item["type"] != "message":
                continue
            yield json.loads(item["data"])
    finally:
        await pubsub.unsubscribe(f"messenger:chat:{chat_id}")
        await pubsub.aclose()


async def publish_signal(username: str, event: dict) -> None:
    await client().publish(f"messenger:signal:{username}", json.dumps(event, ensure_ascii=False))


async def subscribe_signal(username: str) -> AsyncIterator[dict]:
    pubsub = client().pubsub()
    channel = f"messenger:signal:{username}"
    await pubsub.subscribe(channel)
    try:
        async for item in pubsub.listen():
            if item["type"] != "message":
                continue
            yield json.loads(item["data"])
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


async def call_state_set(call_id: str, fields: dict, ttl: int) -> None:
    key = f"messenger:call:{call_id}"
    redis = client()
    await redis.hset(key, mapping=fields)
    await redis.expire(key, ttl)


async def call_state_get(call_id: str) -> dict | None:
    data = await client().hgetall(f"messenger:call:{call_id}")
    return data or None


async def call_state_extend(call_id: str, ttl: int) -> None:
    await client().expire(f"messenger:call:{call_id}", ttl)


async def call_state_delete(call_id: str) -> None:
    await client().delete(f"messenger:call:{call_id}")


async def call_active_set(username: str, call_id: str, ttl: int) -> None:
    await client().set(f"messenger:call:active:{username}", call_id, ex=ttl)


async def call_active_get(username: str) -> str | None:
    return await client().get(f"messenger:call:active:{username}")


async def call_active_clear(username: str, call_id: str) -> None:
    redis = client()
    key = f"messenger:call:active:{username}"
    current = await redis.get(key)
    if current == call_id:
        await redis.delete(key)
