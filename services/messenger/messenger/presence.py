from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal

from .config import get_settings
from .redis_client import client, publish_presence
from .repository import clear_expired_statuses, get_manual_overrides

logger = logging.getLogger("messenger.presence")

DerivedStatus = Literal["online", "away", "offline"]
EffectiveStatus = Literal["online", "away", "offline", "dnd"]

CONN_KEY = "messenger:presence:{username}:conns"
STATUS_KEY = "messenger:presence:{username}:status"
STATUS_CACHE_TTL = 3 * 24 * 60 * 60

# Auto-away hysteresis: once idle-derived "away" fires, it sticks — a single
# stray mouse pixel must not instantly flip it back to online. Returning
# requires activity to be reported as "active" on RETURN_CONFIRM_THRESHOLD
# consecutive connection-lifecycle events (heartbeats fire every 25s
# client-side, so this is roughly one missed + one confirmed heartbeat).
STICKY_AWAY_KEY = "messenger:presence:{username}:sticky_away"
RETURN_CONFIRM_KEY = "messenger:presence:{username}:return_confirm"
STICKY_AWAY_TTL = 24 * 60 * 60
RETURN_CONFIRM_THRESHOLD = 2


def _now() -> int:
    return int(time.time())


def _conn_ttl() -> int:
    return get_settings().presence_conn_ttl_seconds


async def _live_entries(username: str) -> dict[str, str]:
    """Pure read: never mutates Redis. Physical cleanup happens in the sweep loop."""
    raw = await client().hgetall(CONN_KEY.format(username=username))
    now = _now()
    live: dict[str, str] = {}
    for conn_id, value in raw.items():
        expiry_str, _, activity = value.partition(":")
        if expiry_str.isdigit() and int(expiry_str) > now:
            live[conn_id] = activity
    return live


async def compute_status(username: str) -> DerivedStatus:
    """Pure read: reflects live connections plus whatever sticky-away state
    _advance_sticky_state last settled on. Never mutates — safe to call from
    read-only paths like get_presence_map (looking up someone else's status
    must not perturb their own return-from-away hysteresis)."""
    entries = await _live_entries(username)
    if not entries:
        return "offline"
    if await client().get(STICKY_AWAY_KEY.format(username=username)):
        return "away"
    return "online" if "active" in entries.values() else "away"


async def _advance_sticky_state(username: str) -> None:
    """Mutating hysteresis step. Call exactly once per own connection
    lifecycle event (open/heartbeat/close/sweep) — never from a read-only
    presence lookup of another user, or the return-confirmation counter
    would advance every time a colleague merely loads their contact list
    instead of tracking this user's actual elapsed heartbeats."""
    redis = client()
    sticky_key = STICKY_AWAY_KEY.format(username=username)
    confirm_key = RETURN_CONFIRM_KEY.format(username=username)
    entries = await _live_entries(username)
    if not entries:
        await redis.delete(sticky_key)
        await redis.delete(confirm_key)
        return
    if "active" not in entries.values():
        await redis.delete(confirm_key)
        await redis.set(sticky_key, "1", ex=STICKY_AWAY_TTL)
        return
    if not await redis.get(sticky_key):
        await redis.delete(confirm_key)
        return
    confirmations = await redis.incr(confirm_key)
    await redis.expire(confirm_key, _conn_ttl())
    if confirmations >= RETURN_CONFIRM_THRESHOLD:
        await redis.delete(sticky_key)
        await redis.delete(confirm_key)


async def _write_connection(username: str, conn_id: str, activity: str) -> None:
    redis = client()
    key = CONN_KEY.format(username=username)
    value = f"{_now() + _conn_ttl()}:{activity}"
    await redis.hset(key, conn_id, value)
    await redis.expire(key, _conn_ttl() + 60)


ManualStatus = Literal["dnd", "away"] | None


def merge_status(
    derived: DerivedStatus, override: dict | None
) -> tuple[EffectiveStatus, str | None, object, ManualStatus]:
    if not override:
        return derived, None, None, None
    manual: ManualStatus = None
    status: EffectiveStatus = derived
    if override.get("dnd"):
        status, manual = "dnd", "dnd"
    elif override.get("manual_away"):
        # Manual away sticks regardless of activity — unlike the derived
        # "away", it does not flip back to online just because the user
        # moved the mouse. Only clearing it (or expiry) changes it back.
        status, manual = "away", "away"
    return status, override.get("status_text"), override.get("status_expires_at"), manual


async def publish_effective(username: str, display_name: str) -> None:
    """Unconditional publish. Used by manual-status paths (PUT /presence/me,
    the expiry sweep) which must never be swallowed by the derived-status
    dedup cache — see merge_status/_broadcast_if_derived_changed split."""
    derived = await compute_status(username)
    overrides = await get_manual_overrides([username])
    status, text, until, manual = merge_status(derived, overrides.get(username))
    await publish_presence(
        {
            "type": "presence.updated",
            "username": username,
            "display_name": display_name,
            "status": status,
            "status_text": text,
            "status_until": until.isoformat() if until else None,
            "manual_status": manual,
        }
    )


async def _broadcast_if_derived_changed(username: str, display_name: str) -> DerivedStatus:
    """Connection-driven path only (open/heartbeat/close/sweep). Gated by a
    dedup cache of the *derived* status so routine heartbeats don't spam
    broadcasts. Never use this for manual-status changes.

    This is also the single call site for _advance_sticky_state: every
    caller here is a genuine connection-lifecycle event for *this* user, so
    advancing the return-from-away hysteresis here (and nowhere else) keeps
    it tied to this user's own heartbeats rather than to however often
    someone happens to look up their presence."""
    await _advance_sticky_state(username)
    status = await compute_status(username)
    redis = client()
    cache_key = STATUS_KEY.format(username=username)
    previous = await redis.get(cache_key)
    if previous == status:
        return status
    await redis.set(cache_key, status, ex=STATUS_CACHE_TTL)
    await publish_effective(username, display_name)
    return status


async def open_connection(username: str, display_name: str, conn_id: str) -> DerivedStatus:
    await _write_connection(username, conn_id, "active")
    return await _broadcast_if_derived_changed(username, display_name)


async def heartbeat(
    username: str, display_name: str, conn_id: str, activity: str
) -> DerivedStatus:
    await _write_connection(username, conn_id, activity)
    return await _broadcast_if_derived_changed(username, display_name)


async def close_connection(username: str, display_name: str, conn_id: str) -> DerivedStatus:
    await client().hdel(CONN_KEY.format(username=username), conn_id)
    return await _broadcast_if_derived_changed(username, display_name)


async def get_presence_map(usernames: list[str]) -> dict[str, dict]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in usernames:
        username = str(raw or "").strip().lower()
        if not username or username in seen:
            continue
        seen.add(username)
        unique.append(username)
    derived = {username: await compute_status(username) for username in unique}
    overrides = await get_manual_overrides(unique)
    result: dict[str, dict] = {}
    for username in unique:
        status, text, until, manual = merge_status(derived[username], overrides.get(username))
        result[username] = {
            "status": status, "status_text": text, "status_until": until, "manual_status": manual,
        }
    return result


async def sweep_stale_connections() -> None:
    redis = client()
    now = _now()
    async for key in redis.scan_iter(match="messenger:presence:*:conns"):
        username = key.removeprefix("messenger:presence:").removesuffix(":conns")
        raw = await redis.hgetall(key)
        stale = [
            conn_id
            for conn_id, value in raw.items()
            if not value.partition(":")[0].isdigit() or int(value.partition(":")[0]) <= now
        ]
        if stale:
            await redis.hdel(key, *stale)
        await _broadcast_if_derived_changed(username, username)


async def presence_sweep_loop() -> None:
    while True:
        try:
            await sweep_stale_connections()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Presence connection sweep failed")
        await asyncio.sleep(get_settings().presence_sweep_interval_seconds)


async def manual_status_sweep_loop() -> None:
    while True:
        try:
            for username in await clear_expired_statuses():
                await publish_effective(username, username)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Manual status sweep failed")
        await asyncio.sleep(get_settings().presence_manual_sweep_interval_seconds)
