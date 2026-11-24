from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException, status
from livekit.api import (
    AccessToken,
    ListRoomsRequest,
    LiveKitAPI,
    TokenVerifier,
    VideoGrants,
    WebhookReceiver,
)

from .auth import Identity
from .config import get_settings
from .redis_client import publish_chat, publish_signal
from .repository import (
    active_member_usernames,
    add_participant,
    calls_globally_enabled,
    close_session_by_room,
    create_or_get_active_session,
    end_session,
    find_active_session_by_room,
    get_active_session,
    is_member,
    list_active_participants,
    list_active_sessions,
    list_call_sessions_history,
    remove_participant,
    users_calls_allowed,
)

logger = logging.getLogger("messenger.rooms")


def mint_group_call_token(username: str, display_name: str, room_name: str) -> str:
    settings = get_settings()
    grants = VideoGrants(
        room_join=True,
        room=room_name,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
    )
    token = (
        AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(username)
        .with_name(display_name)
        .with_grants(grants)
        .with_ttl(timedelta(seconds=settings.group_call_token_ttl_seconds))
    )
    return token.to_jwt()


async def _broadcast(chat_id: UUID, event: dict) -> None:
    usernames = await active_member_usernames(chat_id)
    for username in usernames:
        await publish_signal(username, event)
    await publish_chat(str(chat_id), event)


async def _session_view(identity: Identity, session: dict, created: bool) -> dict:
    participants = await list_active_participants(session["id"])
    return {
        "session_id": session["id"],
        "chat_id": session["chat_id"],
        "room_name": session["room_name"],
        "livekit_url": get_settings().livekit_url,
        "access_token": mint_group_call_token(
            identity.username, identity.display_name, session["room_name"]
        ),
        "started_at": session["started_at"],
        "participants": participants,
    }


async def start_or_join_call(identity: Identity, chat_id: UUID) -> dict:
    if not await calls_globally_enabled():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Calls are disabled")
    permissions = await users_calls_allowed([identity.username])
    if not permissions.get(identity.username, True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Calls are disabled for this user")
    session, created = await create_or_get_active_session(chat_id, identity.username)
    await add_participant(session["id"], identity.username)
    view = await _session_view(identity, session, created)
    await _broadcast(chat_id, {
        "type": "call_session",
        "chat_id": str(chat_id),
        "session_id": str(session["id"]),
        "action": "started" if created else "joined",
        "by": identity.username,
        "participants": [
            {"username": p["username"], "display_name": p["display_name"]}
            for p in view["participants"]
        ],
    })
    return view


async def leave_call(identity: Identity, chat_id: UUID, session_id: UUID) -> dict:
    await remove_participant(session_id, identity.username)
    remaining_participants = await list_active_participants(session_id)
    ended = False
    if not remaining_participants:
        ended = bool(await end_session(session_id, "empty"))
    await _broadcast(chat_id, {
        "type": "call_session",
        "chat_id": str(chat_id),
        "session_id": str(session_id),
        "action": "left",
        "by": identity.username,
        "participants": [
            {"username": p["username"], "display_name": p["display_name"]}
            for p in remaining_participants
        ],
    })
    if ended:
        await _broadcast(chat_id, {
            "type": "call_session",
            "chat_id": str(chat_id),
            "session_id": str(session_id),
            "action": "ended",
            "by": identity.username,
            "participants": [],
        })
    return {"ok": True}


async def active_call(identity: Identity, chat_id: UUID) -> dict | None:
    if not await is_member(chat_id, identity.username):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    session = await get_active_session(chat_id)
    if not session:
        return None
    participants = await list_active_participants(session["id"])
    return {
        "session_id": session["id"],
        "chat_id": session["chat_id"],
        "room_name": session["room_name"],
        "started_at": session["started_at"],
        "participants": participants,
    }


async def call_history(identity: Identity, chat_id: UUID, before, limit: int) -> list[dict]:
    if not await is_member(chat_id, identity.username):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    return await list_call_sessions_history(chat_id, before, limit)


async def handle_livekit_webhook(raw_body: bytes, auth_header: str) -> None:
    settings = get_settings()
    verifier = TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret)
    receiver = WebhookReceiver(verifier)
    try:
        event = receiver.receive(raw_body.decode("utf-8"), auth_header)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid webhook signature") from exc

    if event.event == "participant_left" and event.room and event.participant:
        session = await find_active_session_by_room(event.room.name)
        if session:
            await remove_participant(session["id"], event.participant.identity)
            remaining_participants = await list_active_participants(session["id"])
            ended = False
            if not remaining_participants:
                ended = bool(await end_session(session["id"], "empty"))
            await _broadcast(session["chat_id"], {
                "type": "call_session",
                "chat_id": str(session["chat_id"]),
                "session_id": str(session["id"]),
                "action": "left",
                "by": event.participant.identity,
                "participants": [
                    {"username": p["username"], "display_name": p["display_name"]}
                    for p in remaining_participants
                ],
            })
            if ended:
                await _broadcast(session["chat_id"], {
                    "type": "call_session",
                    "chat_id": str(session["chat_id"]),
                    "session_id": str(session["id"]),
                    "action": "ended",
                    "by": event.participant.identity,
                    "participants": [],
                })
    elif event.event == "room_finished" and event.room:
        session = await close_session_by_room(event.room.name, "webhook")
        if session:
            await _broadcast(session["chat_id"], {
                "type": "call_session",
                "chat_id": str(session["chat_id"]),
                "session_id": str(session["id"]),
                "action": "ended",
                "by": None,
                "participants": [],
            })
    else:
        logger.debug("Unhandled LiveKit webhook event: %s", event.event)


async def reconcile_live_sessions() -> None:
    """Self-healing backstop for the webhook path: if livekit-server itself
    restarts/crashes mid-call, no participant_left/room_finished webhook is
    ever sent (there's no graceful shutdown to trigger it), and a session can
    be left looking "active" in Postgres forever. This cross-checks every
    session we think is active against LiveKit's own room list and closes
    any that no longer exist there."""
    sessions = await list_active_sessions()
    if not sessions:
        return
    settings = get_settings()
    lk = LiveKitAPI(
        url=settings.livekit_internal_url or "http://livekit:7880",
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        live_rooms = await lk.room.list_rooms(ListRoomsRequest())
        live_room_names = {room.name for room in live_rooms.rooms}
        for session in sessions:
            if session["room_name"] in live_room_names:
                continue
            closed = await end_session(session["id"], "webhook")
            if closed:
                await _broadcast(session["chat_id"], {
                    "type": "call_session",
                    "chat_id": str(session["chat_id"]),
                    "session_id": str(session["id"]),
                    "action": "ended",
                    "by": None,
                    "participants": [],
                })
    finally:
        await lk.aclose()


async def reconcile_live_sessions_loop() -> None:
    while True:
        try:
            await reconcile_live_sessions()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Group call reconciliation failed")
        await asyncio.sleep(get_settings().group_call_reconcile_interval_seconds)
