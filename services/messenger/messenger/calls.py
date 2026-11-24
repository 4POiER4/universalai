from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status

from .auth import Identity
from .config import get_settings
from .redis_client import (
    call_active_clear,
    call_active_get,
    call_active_set,
    call_state_delete,
    call_state_get,
    call_state_set,
    publish_signal,
)
from .repository import (
    calls_globally_enabled,
    create_call_ringing,
    direct_chat_for_pair,
    get_call,
    record_call_busy,
    transition_call,
    users_calls_allowed,
)
from .rooms import mint_group_call_token


async def mint_direct_call_token(identity: Identity, call_id: UUID) -> dict:
    """1:1 calls share the same LiveKit SFU as group calls: the ringing/accept
    control plane below stays custom (phone-call semantics — busy, missed,
    ring timeout), but once a call is accepted, media goes over a private
    LiveKit room instead of a raw mesh RTCPeerConnection. That gets 1:1 calls
    screen sharing, TURN traversal and future recording for free from the
    same infrastructure group calls already use."""
    call = await get_call(call_id)
    if call is None or identity.username not in (call["caller_username"], call["callee_username"]):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    if call["status"] != "accepted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Call is not active")
    # Re-check permission at media-join time too, not just at invite time —
    # an admin may have disabled calls (globally or for this user) while
    # this call was ringing.
    if not await calls_globally_enabled():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Calls are disabled")
    permissions = await users_calls_allowed([call["caller_username"], call["callee_username"]])
    if not all(permissions.get(user, True) for user in (call["caller_username"], call["callee_username"])):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Calls are disabled for a participant")
    room_name = f"call-{call_id}"
    return {
        "room_name": room_name,
        "livekit_url": get_settings().livekit_url,
        "access_token": mint_group_call_token(identity.username, identity.display_name, room_name),
    }


def _err(call_id: UUID, code: str) -> dict:
    return {
        "type": "call", "call_id": str(call_id), "from": None,
        "payload": {"type": "error", "call_id": str(call_id), "code": code},
    }


def _event(call_id: UUID, payload_type: str, frm: str | None) -> dict:
    return {
        "type": "call", "call_id": str(call_id), "from": frm,
        "payload": {"type": payload_type, "call_id": str(call_id)},
    }


async def _teardown(call_id: UUID, caller: str, callee: str) -> None:
    await call_state_delete(str(call_id))
    await call_active_clear(caller, str(call_id))
    await call_active_clear(callee, str(call_id))


async def handle_call_signal(
    websocket,
    send_lock,
    identity: Identity,
    command,
    call_state: dict,
    websocket_send,
) -> None:
    payload = command.payload
    ptype = payload["type"]
    call_id = command.call_id
    settings = get_settings()

    if ptype == "invite":
        to = command.to
        if to == identity.username:
            await websocket_send(websocket, send_lock, _err(call_id, "self_call"))
            return
        resolved_chat_id = await direct_chat_for_pair(identity.username, to)
        requested_chat_id = payload.get("chat_id")
        if resolved_chat_id is None or (
            requested_chat_id and str(resolved_chat_id) != str(requested_chat_id)
        ):
            await websocket_send(websocket, send_lock, _err(call_id, "not_direct_contact"))
            return
        if not await calls_globally_enabled():
            await websocket_send(websocket, send_lock, _err(call_id, "calls_disabled"))
            return
        permissions = await users_calls_allowed([identity.username, to])
        if not permissions.get(identity.username, True) or not permissions.get(to, True):
            await websocket_send(websocket, send_lock, _err(call_id, "calls_disabled"))
            return
        if await call_active_get(identity.username):
            await websocket_send(websocket, send_lock, _err(call_id, "already_in_call"))
            return
        has_video = bool((payload.get("media") or {}).get("video"))
        if await call_active_get(to):
            await record_call_busy(call_id, resolved_chat_id, identity.username, to, has_video)
            await websocket_send(websocket, send_lock, _event(call_id, "busy", identity.username))
            return
        await create_call_ringing(call_id, resolved_chat_id, identity.username, to, has_video)
        ttl = settings.call_ring_timeout_seconds + 15
        await call_state_set(str(call_id), {
            "caller": identity.username, "callee": to,
            "chat_id": str(resolved_chat_id), "status": "ringing",
        }, ttl)
        await call_active_set(identity.username, str(call_id), ttl)
        await call_active_set(to, str(call_id), ttl)
        call_state["hosting_call_id"] = call_id
        await publish_signal(to, {
            "type": "call", "call_id": str(call_id), "from": identity.username,
            "payload": {
                "type": "invite", "call_id": str(call_id), "from": identity.username,
                "from_display_name": identity.display_name,
                "chat_id": str(resolved_chat_id), "media": payload.get("media"),
            },
        })
        return

    state = await call_state_get(str(call_id))
    if not state:
        await websocket_send(websocket, send_lock, _err(call_id, "not_found"))
        return
    caller, callee = state["caller"], state["callee"]
    if identity.username not in (caller, callee):
        await websocket_send(websocket, send_lock, _err(call_id, "not_participant"))
        return

    if ptype == "accept":
        if identity.username != callee:
            await websocket_send(websocket, send_lock, _err(call_id, "not_participant"))
            return
        row = await transition_call(call_id, ("ringing",), "accepted")
        if row is None:
            await websocket_send(websocket, send_lock, {
                "type": "call", "call_id": str(call_id), "from": None,
                "payload": {"type": "accept-rejected", "call_id": str(call_id), "reason": "already-answered"},
            })
            return
        await call_state_set(str(call_id), {"status": "accepted"}, settings.call_max_duration_seconds)
        await call_active_set(caller, str(call_id), settings.call_max_duration_seconds)
        await call_active_set(callee, str(call_id), settings.call_max_duration_seconds)
        call_state["hosting_call_id"] = call_id
        event = _event(call_id, "accepted", identity.username)
        await publish_signal(caller, event)
        await publish_signal(callee, event)
        return

    if ptype in {"decline", "cancel"}:
        reason = str(payload.get("reason") or "user").strip()
        new_status = "missed" if reason == "timeout" else ("declined" if ptype == "decline" else "cancelled")
        row = await transition_call(call_id, ("ringing",), new_status)
        if row is None:
            return
        await _teardown(call_id, caller, callee)
        event = {
            "type": "call", "call_id": str(call_id), "from": identity.username,
            "payload": {"type": new_status, "call_id": str(call_id), "reason": reason},
        }
        await publish_signal(caller, event)
        await publish_signal(callee, event)
        call_state["hosting_call_id"] = None
        return

    if ptype == "hangup":
        row = await transition_call(call_id, ("accepted",), "ended")
        if row is None:
            return
        await _teardown(call_id, caller, callee)
        event = _event(call_id, "ended", identity.username)
        await publish_signal(caller, event)
        await publish_signal(callee, event)
        call_state["hosting_call_id"] = None
        return


async def finalize_orphaned_call(call_id: UUID) -> None:
    """Best-effort cleanup when the connection hosting this call's WebRTC
    side disconnects without an explicit hangup/cancel (crash, network drop)."""
    row = await transition_call(call_id, ("ringing",), "missed")
    if row is None:
        row = await transition_call(call_id, ("accepted",), "ended")
    if row is None:
        return
    caller, callee = row["caller_username"], row["callee_username"]
    await _teardown(call_id, caller, callee)
    event = {
        "type": "call", "call_id": str(call_id), "from": None,
        "payload": {"type": row["status"], "call_id": str(call_id), "reason": "peer_disconnected"},
    }
    await publish_signal(caller, event)
    await publish_signal(callee, event)
