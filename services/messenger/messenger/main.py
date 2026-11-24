import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import FastAPI, File, HTTPException, Query, Request, Response, UploadFile, WebSocket, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import ValidationError

from .auth import CurrentIdentity, identity_is_current, websocket_identity
from .calls import finalize_orphaned_call, handle_call_signal, mint_direct_call_token
from .config import get_settings
from .database import close_pool, database_ready, open_pool
from .maintenance import retention_loop
from .attachments import delete_wallpaper, get_download, get_wallpaper_download, store_upload, store_wallpaper_upload
from .redis_client import (
    acquire_connection_slot,
    allow_rate,
    close_redis,
    count_connection_slots,
    open_redis,
    publish_chat,
    redis_ready,
    refresh_connection_slot,
    release_connection_slot,
    subscribe_chat,
    subscribe_presence,
    subscribe_signal,
)
from .presence import (
    close_connection,
    get_presence_map,
    heartbeat,
    manual_status_sweep_loop,
    open_connection,
    presence_sweep_loop,
    publish_effective,
)
from .rooms import (
    active_call,
    call_history,
    handle_livekit_webhook,
    leave_call,
    reconcile_live_sessions_loop,
    start_or_join_call,
)
from .repository import (
    add_group_member,
    create_chat,
    delete_group,
    delete_message,
    edit_message,
    effective_invites_per_day,
    effective_message_rate_limit,
    get_admin_config,
    get_profile_for_admin,
    is_member,
    leave_group,
    list_departments,
    list_audit_events,
    list_chats,
    list_member_read_cursors,
    list_messages,
    list_pins,
    list_profiles_for_admin,
    remove_group_member,
    rename_group,
    record_security_event,
    require_admin,
    revoke_all_sessions,
    revoke_user_sessions,
    search_directory,
    send_message,
    search_messages,
    set_manual_status,
    set_pin,
    set_reaction,
    set_user_active,
    set_user_auditor,
    set_user_calls_enabled,
    set_user_muted,
    sync_profile,
    update_admin_config,
    update_read_cursor,
)
from .schemas import (
    ActiveCallSessionView,
    AddMember,
    AdminConfigView,
    AdminUserView,
    CallSessionHistoryEntry,
    CallSessionView,
    ChatView,
    CreateChat,
    DirectCallToken,
    EditMessage,
    MessageView,
    PresenceView,
    ReadCursor,
    RenameChat,
    SendMessage,
    UpdateAdminConfig,
    UpdatePresenceStatus,
    UpdateUserCalls,
    UpdateUserFlag,
    WebSocketAuth,
    WebSocketCommand,
)


logger = logging.getLogger("messenger")


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings().validate_runtime()
    await open_pool()
    await open_redis()
    retention_task = asyncio.create_task(retention_loop())
    presence_sweep_task = asyncio.create_task(presence_sweep_loop())
    manual_sweep_task = asyncio.create_task(manual_status_sweep_loop())
    group_call_reconcile_task = asyncio.create_task(reconcile_live_sessions_loop())
    try:
        yield
    finally:
        tasks = (retention_task, presence_sweep_task, manual_sweep_task, group_call_reconcile_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_redis()
        await close_pool()


app = FastAPI(
    title="UniversalAI Messenger",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=list(get_settings().allowed_hosts),
)


@app.middleware("http")
async def request_security(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", "")
    try:
        request_id = str(UUID(request_id))
    except (ValueError, TypeError):
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response: Response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"error": "Invalid request", "details": jsonable_encoder(exc.errors())},
    )


@app.get("/health/live")
async def live():
    return {"ok": True}


@app.get("/health/ready")
async def ready():
    database_ok, redis_ok = await asyncio.gather(database_ready(), redis_ready())
    ok = database_ok and (redis_ok or not get_settings().ready_require_redis)
    return JSONResponse(
        {"ok": ok, "database": database_ok, "redis": redis_ok},
        status_code=200 if ok else 503,
    )


@app.get("/api/messenger/v1/chats", response_model=list[ChatView])
async def chats(identity: CurrentIdentity):
    await sync_profile(identity)
    return await list_chats(identity)


@app.get("/api/messenger/v1/directory/search")
async def directory_search(identity: CurrentIdentity, q: str = Query(min_length=1, max_length=64)):
    """Find someone to start a chat with, by username or display name."""
    if not await allow_rate(f"directory-search:{identity.username}", 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")
    return await search_directory(identity, q)


@app.get("/api/messenger/v1/directory/departments")
async def directory_departments(identity: CurrentIdentity):
    """The company directory: every active colleague grouped by department."""
    if not await allow_rate(f"directory-departments:{identity.username}", 30):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")
    return await list_departments(identity)


@app.get("/api/messenger/v1/presence", response_model=list[PresenceView])
async def presence_snapshot(
    identity: CurrentIdentity,
    users: str = Query(default="", max_length=4000),
):
    usernames = [item.strip().lower() for item in users.split(",") if item.strip()]
    if not usernames:
        return []
    if not await allow_rate(
        f"presence-read:{identity.username}",
        120,
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")
    statuses = await get_presence_map(usernames)
    return [
        PresenceView(username=username, **data)
        for username, data in statuses.items()
    ]


@app.get("/api/messenger/v1/presence/me", response_model=PresenceView)
async def my_presence(identity: CurrentIdentity):
    statuses = await get_presence_map([identity.username])
    data = statuses.get(identity.username, {"status": "offline", "status_text": None, "status_until": None})
    return PresenceView(username=identity.username, display_name=identity.display_name, **data)


@app.post("/api/messenger/v1/calls/{call_id}/token", response_model=DirectCallToken)
async def direct_call_token(call_id: UUID, identity: CurrentIdentity):
    if not await allow_rate(f"direct-call-token:{identity.username}", 30):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")
    return await mint_direct_call_token(identity, call_id)


@app.get("/api/messenger/v1/audit")
async def audit_events(
    identity: CurrentIdentity,
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
):
    return await list_audit_events(identity, before, limit)


@app.get("/api/messenger/v1/admin/config", response_model=AdminConfigView)
async def admin_get_config(identity: CurrentIdentity):
    require_admin(identity)
    return AdminConfigView(**await get_admin_config())


@app.put("/api/messenger/v1/admin/config", response_model=AdminConfigView)
async def admin_update_config(payload: UpdateAdminConfig, identity: CurrentIdentity):
    require_admin(identity)
    return AdminConfigView(**await update_admin_config(identity, payload.changes()))


async def _admin_user_view(username: str) -> AdminUserView:
    updated = await get_profile_for_admin(username)
    if not updated:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    sessions = (await count_connection_slots()).get(username, 0)
    return AdminUserView(**updated, sessions=sessions)


@app.get("/api/messenger/v1/admin/users", response_model=list[AdminUserView])
async def admin_list_users(identity: CurrentIdentity):
    require_admin(identity)
    sessions = await count_connection_slots()
    return [
        AdminUserView(**row, sessions=sessions.get(row["username"], 0))
        for row in await list_profiles_for_admin()
    ]


@app.put("/api/messenger/v1/admin/users/{username}/calls", response_model=AdminUserView)
async def admin_update_user_calls(username: str, payload: UpdateUserCalls, identity: CurrentIdentity):
    require_admin(identity)
    target = username.strip().lower()
    await set_user_calls_enabled(identity, target, payload.calls_enabled)
    return await _admin_user_view(target)


@app.put("/api/messenger/v1/admin/users/{username}/muted", response_model=AdminUserView)
async def admin_update_user_muted(username: str, payload: UpdateUserFlag, identity: CurrentIdentity):
    require_admin(identity)
    target = username.strip().lower()
    await set_user_muted(identity, target, payload.value)
    return await _admin_user_view(target)


@app.put("/api/messenger/v1/admin/users/{username}/auditor", response_model=AdminUserView)
async def admin_update_user_auditor(username: str, payload: UpdateUserFlag, identity: CurrentIdentity):
    require_admin(identity)
    target = username.strip().lower()
    await set_user_auditor(identity, target, payload.value)
    return await _admin_user_view(target)


@app.put("/api/messenger/v1/admin/users/{username}/active", response_model=AdminUserView)
async def admin_update_user_active(username: str, payload: UpdateUserFlag, identity: CurrentIdentity):
    require_admin(identity)
    target = username.strip().lower()
    await set_user_active(identity, target, payload.value)
    return await _admin_user_view(target)


@app.post("/api/messenger/v1/admin/users/{username}/revoke-sessions", response_model=AdminUserView)
async def admin_revoke_user_sessions(username: str, identity: CurrentIdentity):
    require_admin(identity)
    target = username.strip().lower()
    await revoke_user_sessions(identity, target)
    return await _admin_user_view(target)


@app.post("/api/messenger/v1/admin/revoke-all-sessions")
async def admin_revoke_all_sessions(identity: CurrentIdentity):
    require_admin(identity)
    return {"affected": await revoke_all_sessions(identity)}


@app.put("/api/messenger/v1/admin/users/{username}/status", response_model=PresenceView)
async def admin_update_user_status(username: str, payload: UpdatePresenceStatus, identity: CurrentIdentity):
    require_admin(identity)
    target = username.strip().lower()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=payload.expires_in_minutes)
        if payload.expires_in_minutes is not None
        else None
    )
    row = await set_manual_status(
        identity, payload.dnd, payload.manual_away, payload.status_text, expires_at,
        target_username=target,
    )
    await publish_effective(target, row["display_name"])
    statuses = await get_presence_map([target])
    data = statuses[target]
    return PresenceView(username=target, display_name=row["display_name"], **data)


@app.post("/api/messenger/v1/chats", response_model=ChatView)
async def new_chat(payload: CreateChat, identity: CurrentIdentity, response: Response):
    await sync_profile(identity)
    chat, created = await create_chat(identity, payload)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return chat


@app.post("/api/messenger/v1/chats/{chat_id}/members")
async def add_member(chat_id: UUID, payload: AddMember, identity: CurrentIdentity):
    if not await allow_rate(
        f"invite:{identity.username}", await effective_invites_per_day(), 24 * 60 * 60,
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Daily invite limit reached")
    membership = await add_group_member(identity, chat_id, payload.username)
    await publish_chat(
        str(chat_id),
        {"type": "membership.updated", "membership": membership},
    )
    return membership


@app.delete("/api/messenger/v1/chats/{chat_id}/members/{username}")
async def remove_member(chat_id: UUID, username: str, identity: CurrentIdentity):
    membership = await remove_group_member(identity, chat_id, username.strip().lower())
    await publish_chat(
        str(chat_id),
        {"type": "membership.updated", "membership": membership},
    )
    return membership


@app.post("/api/messenger/v1/chats/{chat_id}/leave")
async def leave_chat(chat_id: UUID, identity: CurrentIdentity):
    membership = await leave_group(identity, chat_id)
    await publish_chat(
        str(chat_id),
        {"type": "membership.updated", "membership": membership},
    )
    if membership["closed"]:
        await publish_chat(str(chat_id), {"type": "chat.deleted", "chat_id": str(chat_id)})
    return membership


@app.patch("/api/messenger/v1/chats/{chat_id}")
async def rename_chat(chat_id: UUID, payload: RenameChat, identity: CurrentIdentity):
    result = await rename_group(identity, chat_id, payload.title)
    await publish_chat(str(chat_id), {"type": "chat.updated", **result})
    return result


@app.delete("/api/messenger/v1/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: UUID, identity: CurrentIdentity):
    await delete_group(identity, chat_id)
    await publish_chat(str(chat_id), {"type": "chat.deleted", "chat_id": str(chat_id)})
    return Response(status_code=204)


@app.post("/api/messenger/v1/chats/{chat_id}/calls", response_model=CallSessionView)
async def start_group_call(chat_id: UUID, identity: CurrentIdentity):
    if not await allow_rate(f"group-call-start:{identity.username}", 20, 60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded")
    return await start_or_join_call(identity, chat_id)


@app.post("/api/messenger/v1/chats/{chat_id}/calls/{session_id}/leave")
async def leave_group_call(chat_id: UUID, session_id: UUID, identity: CurrentIdentity):
    return await leave_call(identity, chat_id, session_id)


@app.get(
    "/api/messenger/v1/chats/{chat_id}/calls/active",
    response_model=ActiveCallSessionView | None,
)
async def active_group_call(chat_id: UUID, identity: CurrentIdentity):
    return await active_call(identity, chat_id)


@app.get(
    "/api/messenger/v1/chats/{chat_id}/calls/history",
    response_model=list[CallSessionHistoryEntry],
)
async def group_call_history(
    chat_id: UUID,
    identity: CurrentIdentity,
    before: datetime | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    return await call_history(identity, chat_id, before, limit)


@app.post("/api/messenger/v1/livekit/webhook", include_in_schema=False)
async def livekit_webhook(request: Request):
    body = await request.body()
    await handle_livekit_webhook(body, request.headers.get("Authorization", ""))
    return {"ok": True}


@app.get(
    "/api/messenger/v1/chats/{chat_id}/messages",
    response_model=list[MessageView],
)
async def history(
    chat_id: UUID,
    identity: CurrentIdentity,
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=200, ge=1, le=500),
):
    limit = min(limit, get_settings().history_page_size)
    return await list_messages(identity, chat_id, before, limit)


@app.get(
    "/api/messenger/v1/chats/{chat_id}/messages/search",
    response_model=list[MessageView],
)
async def search(
    chat_id: UUID,
    identity: CurrentIdentity,
    q: str = Query(min_length=2, max_length=200),
    before: int | None = Query(default=None, ge=1),
    limit: int = Query(default=25, ge=1, le=50),
):
    return await search_messages(identity, chat_id, q, before, limit)


@app.post(
    "/api/messenger/v1/chats/{chat_id}/messages",
    response_model=MessageView,
)
async def create_message(
    chat_id: UUID,
    payload: SendMessage,
    identity: CurrentIdentity,
    response: Response,
):
    if not await allow_rate(
        f"message:{identity.username}",
        await effective_message_rate_limit(),
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Message rate exceeded")
    message, created = await send_message(identity, chat_id, payload)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    if created:
        await publish_chat(
            str(chat_id),
            {"type": "message.created", "message": jsonable_encoder(message)},
        )
    return message


@app.patch(
    "/api/messenger/v1/chats/{chat_id}/messages/{message_id}",
    response_model=MessageView,
)
async def update_message(
    chat_id: UUID,
    message_id: UUID,
    payload: EditMessage,
    identity: CurrentIdentity,
):
    message = await edit_message(identity, chat_id, message_id, payload)
    await publish_chat(
        str(chat_id),
        {"type": "message.updated", "message": jsonable_encoder(message)},
    )
    return message


@app.delete(
    "/api/messenger/v1/chats/{chat_id}/messages/{message_id}",
    response_model=MessageView,
)
async def remove_message(
    chat_id: UUID,
    message_id: UUID,
    identity: CurrentIdentity,
    for_everyone: bool = Query(default=False),
):
    message = await delete_message(identity, chat_id, message_id, for_everyone=for_everyone)
    if for_everyone:
        await publish_chat(
            str(chat_id),
            {"type": "message.deleted", "message": jsonable_encoder(message)},
        )
    return message


@app.put("/api/messenger/v1/chats/{chat_id}/messages/{message_id}/reactions/{emoji}")
async def add_reaction(chat_id: UUID, message_id: UUID, emoji: str, identity: CurrentIdentity):
    reaction, changed = await set_reaction(identity, chat_id, message_id, emoji, True)
    if changed:
        await publish_chat(str(chat_id), {"type": "reaction.updated", **reaction})
    return reaction


@app.delete("/api/messenger/v1/chats/{chat_id}/messages/{message_id}/reactions/{emoji}")
async def remove_reaction(chat_id: UUID, message_id: UUID, emoji: str, identity: CurrentIdentity):
    reaction, changed = await set_reaction(identity, chat_id, message_id, emoji, False)
    if changed:
        await publish_chat(str(chat_id), {"type": "reaction.updated", **reaction})
    return reaction


@app.get("/api/messenger/v1/chats/{chat_id}/pins")
async def pins(chat_id: UUID, identity: CurrentIdentity):
    return await list_pins(identity, chat_id)


@app.put("/api/messenger/v1/chats/{chat_id}/pins/{message_id}")
async def pin(chat_id: UUID, message_id: UUID, identity: CurrentIdentity):
    result, changed = await set_pin(identity, chat_id, message_id, True)
    if changed:
        await publish_chat(str(chat_id), {"type": "pin.updated", **result})
    return result


@app.delete("/api/messenger/v1/chats/{chat_id}/pins/{message_id}")
async def unpin(chat_id: UUID, message_id: UUID, identity: CurrentIdentity):
    result, changed = await set_pin(identity, chat_id, message_id, False)
    if changed:
        await publish_chat(str(chat_id), {"type": "pin.updated", **result})
    return result


@app.post("/api/messenger/v1/chats/{chat_id}/attachments", status_code=201)
async def upload_attachment(
    chat_id: UUID, identity: CurrentIdentity, file: UploadFile = File(...)
):
    return await store_upload(identity, chat_id, file)


@app.get("/api/messenger/v1/chats/{chat_id}/attachments/{attachment_id}")
async def download_attachment(
    chat_id: UUID,
    attachment_id: UUID,
    identity: CurrentIdentity,
    inline: bool = Query(default=False),
):
    attachment, path = await get_download(identity, chat_id, attachment_id)
    disposition = "inline" if inline and attachment["inline_safe"] else "attachment"
    return FileResponse(
        path,
        media_type=attachment["media_type"] if disposition == "inline" else "application/octet-stream",
        filename=attachment["original_name"],
        content_disposition_type=disposition,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@app.put("/api/messenger/v1/wallpapers/me", status_code=201)
async def upload_my_wallpaper(identity: CurrentIdentity, file: UploadFile = File(...)):
    return await store_wallpaper_upload(identity, file)


@app.get("/api/messenger/v1/wallpapers/me")
async def download_my_wallpaper(identity: CurrentIdentity):
    wallpaper, path = await get_wallpaper_download(identity)
    return FileResponse(
        path,
        media_type=wallpaper["media_type"],
        content_disposition_type="inline",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@app.delete("/api/messenger/v1/wallpapers/me", status_code=204)
async def remove_my_wallpaper(identity: CurrentIdentity):
    await delete_wallpaper(identity)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/messenger/v1/chats/{chat_id}/read-state")
async def read_state(chat_id: UUID, identity: CurrentIdentity):
    return await list_member_read_cursors(identity, chat_id)


@app.put("/api/messenger/v1/chats/{chat_id}/read")
async def mark_read(chat_id: UUID, payload: ReadCursor, identity: CurrentIdentity):
    receipt = await update_read_cursor(identity, chat_id, payload.sequence)
    encoded = jsonable_encoder(receipt)
    await publish_chat(str(chat_id), {"type": "receipt.updated", "receipt": encoded})
    return encoded


async def websocket_send(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    event: dict,
) -> None:
    async with send_lock:
        await websocket.send_json(event)


def _ends_subscription(event: dict, username: str) -> bool:
    if event.get("type") == "chat.deleted":
        return True
    membership = event.get("membership") or {}
    return (
        event.get("type") == "membership.updated"
        and membership.get("removed") is True
        and membership.get("username") == username
    )


async def forward_subscription(
    websocket: WebSocket,
    chat_id: UUID,
    send_lock: asyncio.Lock,
    username: str,
) -> None:
    async for event in subscribe_chat(str(chat_id)):
        await websocket_send(websocket, send_lock, event)
        # Once the member is out, stop relaying the chat to this socket;
        # the event itself is the last thing they receive.
        if _ends_subscription(event, username):
            return


async def forward_presence(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
) -> None:
    async for event in subscribe_presence():
        await websocket_send(websocket, send_lock, event)


async def forward_signal(
    websocket: WebSocket,
    send_lock: asyncio.Lock,
    username: str,
) -> None:
    async for event in subscribe_signal(username):
        await websocket_send(websocket, send_lock, event)


@app.websocket("/api/messenger/v1/ws")
async def websocket_endpoint(websocket: WebSocket):
    origin = (websocket.headers.get("origin") or "").rstrip("/")
    if origin not in get_settings().allowed_origins:
        await websocket.close(code=1008, reason="Origin not allowed")
        return

    await websocket.accept()
    send_lock = asyncio.Lock()
    slot: str | None = None
    conn_id: str | None = None
    subscriptions: dict[UUID, asyncio.Task] = {}
    presence_task: asyncio.Task | None = None
    signal_task: asyncio.Task | None = None
    call_state: dict[str, UUID | None] = {"hosting_call_id": None}
    identity = None
    try:
        raw_auth = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        auth_command = WebSocketAuth.model_validate(raw_auth)
        identity = await websocket_identity(websocket, auth_command.token)
        if identity is None:
            await websocket.close(code=1008, reason="Authentication failed")
            return
        slot = await acquire_connection_slot(
            identity.username,
            get_settings().rate_connections_per_user,
        )
        if slot is None:
            await websocket.close(code=1008, reason="Connection limit exceeded")
            return
        await sync_profile(identity)
        await record_security_event(identity, "connection.open", slot)
        conn_id = uuid.uuid4().hex
        presence_task = asyncio.create_task(forward_presence(websocket, send_lock))
        signal_task = asyncio.create_task(forward_signal(websocket, send_lock, identity.username))
        await open_connection(identity.username, identity.display_name, conn_id)
        await websocket_send(
            websocket,
            send_lock,
            {"type": "ready", "username": identity.username, "heartbeat_seconds": 25}
        )

        while True:
            raw_command = await asyncio.wait_for(websocket.receive_json(), timeout=35)
            command = WebSocketCommand.model_validate(raw_command)
            if command.type == "ping":
                if not await identity_is_current(identity):
                    await websocket.close(code=1008, reason="Access revoked")
                    return
                await refresh_connection_slot(slot)
                await heartbeat(identity.username, identity.display_name, conn_id, command.activity or "active")
                await websocket_send(websocket, send_lock, {"type": "pong"})
                continue
            if command.type == "call":
                if not await allow_rate(
                    f"call-signal:{identity.username}",
                    get_settings().call_signal_rate_limit,
                    get_settings().call_signal_rate_window_seconds,
                ):
                    await websocket_send(websocket, send_lock, {
                        "type": "call", "call_id": str(command.call_id), "from": None,
                        "payload": {"type": "error", "call_id": str(command.call_id), "code": "rate_limited"},
                    })
                    continue
                await handle_call_signal(websocket, send_lock, identity, command, call_state, websocket_send)
                continue
            chat_id = command.chat_id
            if command.type == "unsubscribe":
                task = subscriptions.pop(chat_id, None)
                if task:
                    task.cancel()
                continue
            if not await is_member(chat_id, identity.username):
                await websocket_send(
                    websocket,
                    send_lock,
                    {"type": "error", "code": "chat_not_found", "chat_id": str(chat_id)}
                )
                continue
            if command.type == "typing":
                await publish_chat(
                    str(chat_id),
                    {
                        "type": "typing.updated",
                        "chat_id": str(chat_id),
                        "username": identity.username,
                        "display_name": identity.display_name,
                        "active": command.active,
                    },
                )
                continue
            if chat_id not in subscriptions or subscriptions[chat_id].done():
                subscriptions[chat_id] = asyncio.create_task(
                    forward_subscription(websocket, chat_id, send_lock, identity.username)
                )
                await websocket_send(
                    websocket,
                    send_lock,
                    {"type": "subscribed", "chat_id": str(chat_id)}
                )
    except (TimeoutError, ValidationError, ValueError):
        with suppress(RuntimeError):
            await websocket.close(code=1008, reason="Invalid WebSocket command")
    except Exception as exc:
        if exc.__class__.__name__ != "WebSocketDisconnect":
            logger.warning("WebSocket ended: %s", exc.__class__.__name__)
    finally:
        if presence_task is not None:
            presence_task.cancel()
            await asyncio.gather(presence_task, return_exceptions=True)
        if signal_task is not None:
            signal_task.cancel()
            await asyncio.gather(signal_task, return_exceptions=True)
        for task in subscriptions.values():
            task.cancel()
        if subscriptions:
            await asyncio.gather(*subscriptions.values(), return_exceptions=True)
        if identity is not None and slot is not None:
            with suppress(Exception):
                await record_security_event(identity, "connection.close", slot)
            if conn_id is not None:
                with suppress(Exception):
                    await close_connection(identity.username, identity.display_name, conn_id)
            if call_state["hosting_call_id"] is not None:
                with suppress(Exception):
                    await finalize_orphaned_call(call_state["hosting_call_id"])
        await release_connection_slot(slot)
