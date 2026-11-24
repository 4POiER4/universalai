from datetime import datetime
from time import monotonic
from unicodedata import normalize
from uuid import UUID, uuid4
import re

from fastapi import HTTPException, status
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .auth import Identity
from .config import get_settings
from .database import pool, transaction
from .schemas import CreateChat, EditMessage, SendMessage

_STICKER_TOKEN = re.compile(r"\[\[sticker:([a-zA-Z0-9_-]{1,24})\]\]")


def _avatar_public_url(username: str, avatar_path: str | None, avatar_updated_at) -> str | None:
    if not username or not avatar_path or not avatar_updated_at:
        return None
    ts = int(avatar_updated_at.timestamp())
    return f"/api/avatars/{username}?v={ts}"


def _member_row(row: dict) -> dict:
    payload = dict(row)
    payload["avatar_url"] = _avatar_public_url(
        payload.get("username"),
        payload.get("avatar_path"),
        payload.get("avatar_updated_at"),
    )
    payload.pop("avatar_path", None)
    payload.pop("avatar_updated_at", None)
    return payload


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _directory_entry(row: dict) -> dict:
    return {
        "username": row["username"],
        "display_name": row["display_name"],
        "department": row.get("department") or "",
        "title": row.get("title") or "",
        "avatar_url": _avatar_public_url(row["username"], row.get("avatar_path"), row.get("avatar_updated_at")),
    }


_DIRECTORY_FIELDS = """
    username,
    COALESCE(NULLIF(display_name, ''), username) AS display_name,
    department, title, avatar_path, avatar_updated_at
"""


async def search_directory(identity: Identity, query: str, limit: int = 20) -> list[dict]:
    """Colleagues matched by name, login, department or job title."""
    q = query.strip()
    if len(q) < 1:
        return []
    pattern = _like_pattern(q)
    async with pool().connection() as connection:
        cursor = await connection.execute(
            f"""
            SELECT {_DIRECTORY_FIELDS}
            FROM public.users
            WHERE is_active = TRUE AND username <> %s
              AND (username ILIKE %s OR display_name ILIKE %s
                   OR department ILIKE %s OR title ILIKE %s)
            ORDER BY display_name
            LIMIT %s
            """,
            (identity.username, pattern, pattern, pattern, pattern, limit),
        )
        return [_directory_entry(dict(row)) for row in await cursor.fetchall()]


async def list_departments(identity: Identity) -> list[dict]:
    """The company directory: every active colleague grouped by department."""
    async with pool().connection() as connection:
        cursor = await connection.execute(
            f"""
            SELECT {_DIRECTORY_FIELDS}
            FROM public.users
            WHERE is_active = TRUE AND username <> %s
            ORDER BY (department = ''), department, display_name
            """,
            (identity.username,),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
    departments: list[dict] = []
    for row in rows:
        name = row.get("department") or ""
        if not departments or departments[-1]["name"] != name:
            departments.append({"name": name, "members": []})
        departments[-1]["members"].append(_directory_entry(row))
    return departments


def chat_preview_from_text(text: str) -> str:
    raw = (text or "").replace("\n", " ").strip()
    if not raw:
        return ""
    tokens = list(_STICKER_TOKEN.finditer(raw))
    if not tokens:
        return raw[:160]
    remainder = _STICKER_TOKEN.sub("", raw).strip()
    if not remainder:
        if len(tokens) == 1:
            return "Стикер"
        return f"Стикеры ({len(tokens)})"
    return remainder[:160]


async def audit(
    connection,
    identity: Identity,
    action: str,
    target_type: str,
    target_id: str,
    result: str = "success",
    metadata: dict | None = None,
) -> None:
    await connection.execute(
        """
        INSERT INTO messenger.audit_events
          (actor_username, action, target_type, target_id, result, metadata)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            identity.username,
            action,
            target_type,
            target_id,
            result,
            Jsonb(metadata or {}),
        ),
    )


async def sync_profile(identity: Identity) -> None:
    async with transaction() as connection:
        cursor = await connection.execute(
            "SELECT display_name FROM public.users WHERE username=%s",
            (identity.username,),
        )
        row = await cursor.fetchone()
        display_name = (
            (row.get("display_name") if row else None)
            or identity.display_name
            or identity.username
        ).strip()
        await connection.execute(
            """
            INSERT INTO messenger.profiles (username, display_name)
            VALUES (%s, %s)
            ON CONFLICT (username) DO UPDATE SET
              display_name = EXCLUDED.display_name,
              updated_at = NOW()
            """,
            (identity.username, display_name),
        )


async def get_manual_overrides(usernames: list[str]) -> dict[str, dict]:
    if not usernames:
        return {}
    async with pool().connection() as connection:
        cursor = await connection.execute(
            """
            SELECT username, dnd, manual_away, status_text, status_expires_at
            FROM messenger.profiles
            WHERE username = ANY(%s)
              AND (dnd IS TRUE OR manual_away IS TRUE OR status_text IS NOT NULL)
              AND (status_expires_at IS NULL OR status_expires_at > NOW())
            """,
            (usernames,),
        )
        rows = await cursor.fetchall()
    return {row["username"]: dict(row) for row in rows}


def require_admin(identity: Identity) -> None:
    if "system_admin" not in identity.roles:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")


async def set_manual_status(
    identity: Identity,
    dnd: bool,
    manual_away: bool,
    status_text: str,
    expires_at: datetime | None,
    target_username: str | None = None,
) -> dict:
    username = target_username or identity.username
    if target_username and target_username != identity.username:
        require_admin(identity)
    text = status_text.strip() or None
    async with transaction() as connection:
        cursor = await connection.execute(
            """
            UPDATE messenger.profiles
            SET dnd = %s, manual_away = %s, status_text = %s, status_expires_at = %s, updated_at = NOW()
            WHERE username = %s
            RETURNING username, display_name, dnd, manual_away, status_text, status_expires_at
            """,
            (dnd, manual_away, text, expires_at, username),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
        await audit(
            connection,
            identity,
            "presence.status_updated",
            "profile",
            username,
            metadata={
                "dnd": dnd,
                "manual_away": manual_away,
                "has_text": bool(text),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "by_admin": username != identity.username,
            },
        )
    return dict(row)


_ADMIN_CONFIG_FIELDS = (
    "calls_enabled, max_attachment_bytes, auto_purge_enabled, "
    "retention_messages_days, retention_files_days, retention_audit_days, retention_backups_days, "
    "rate_messages_per_minute, rate_logins_per_hour, group_max_members, invites_per_day, "
    "updated_at, updated_by"
)

_ADMIN_CONFIG_EDITABLE = (
    "calls_enabled", "max_attachment_bytes", "auto_purge_enabled",
    "retention_messages_days", "retention_files_days", "retention_audit_days",
    "retention_backups_days", "rate_messages_per_minute", "rate_logins_per_hour",
    "group_max_members", "invites_per_day",
)

# Hot paths (every message send, every group add) need these numbers, so the
# config row is cached briefly instead of being read per request. The TTL is
# short enough that an admin's change lands within seconds without needing
# any cross-worker invalidation channel.
_CONFIG_CACHE_TTL_SECONDS = 5.0
_config_cache: dict | None = None
_config_cache_at: float = 0.0


async def get_admin_config() -> dict:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            f"SELECT {_ADMIN_CONFIG_FIELDS} FROM messenger.admin_config WHERE id = TRUE"
        )
        row = await cursor.fetchone()
    return dict(row)


async def cached_admin_config() -> dict:
    global _config_cache, _config_cache_at
    now = monotonic()
    if _config_cache is None or now - _config_cache_at > _CONFIG_CACHE_TTL_SECONDS:
        _config_cache = await get_admin_config()
        _config_cache_at = now
    return _config_cache


def _invalidate_config_cache() -> None:
    global _config_cache
    _config_cache = None


async def update_admin_config(identity: Identity, changes: dict) -> dict:
    require_admin(identity)
    fields = {k: v for k, v in changes.items() if k in _ADMIN_CONFIG_EDITABLE}
    if not fields:
        return await get_admin_config()
    assignments = ", ".join(f"{name} = %s" for name in fields)
    async with transaction() as connection:
        cursor = await connection.execute(
            f"""
            UPDATE messenger.admin_config
            SET {assignments}, updated_at = NOW(), updated_by = %s
            WHERE id = TRUE
            RETURNING {_ADMIN_CONFIG_FIELDS}
            """,
            (*fields.values(), identity.username),
        )
        row = await cursor.fetchone()
        await audit(
            connection, identity, "admin.config_updated", "admin_config", "global",
            metadata=fields,
        )
    _invalidate_config_cache()
    return dict(row)


# Every platform account, including AD users who have not opened the
# messenger yet (they have no messenger.profiles row until then).
_ADMIN_PROFILE_FIELDS = """
    u.username,
    COALESCE(NULLIF(u.display_name, ''), p.display_name, u.username) AS display_name,
    COALESCE(p.calls_enabled, TRUE) AS calls_enabled,
    COALESCE(p.dnd, FALSE) AS dnd,
    COALESCE(p.manual_away, FALSE) AS manual_away,
    p.status_text, p.status_expires_at,
    COALESCE(p.muted, FALSE) AS muted,
    COALESCE(p.auditor, FALSE) AS auditor,
    u.is_active,
    'admin' = ANY(u.roles) AS is_admin,
    u.department, u.title
"""
_ADMIN_PROFILE_FROM = """
    FROM public.users u
    LEFT JOIN messenger.profiles p ON p.username = u.username
"""


async def list_profiles_for_admin() -> list[dict]:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            f"""
            SELECT {_ADMIN_PROFILE_FIELDS} {_ADMIN_PROFILE_FROM}
            -- retired password-less service accounts (the former AI bots)
            WHERE NOT (u.auth_source = 'local' AND u.password_hash IS NULL AND NOT u.is_active)
            ORDER BY display_name
            """
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_profile_for_admin(username: str) -> dict | None:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            f"SELECT {_ADMIN_PROFILE_FIELDS} {_ADMIN_PROFILE_FROM} WHERE u.username = %s",
            (username,),
        )
        row = await cursor.fetchone()
    return dict(row) if row else None


async def _set_profile_flag(
    identity: Identity, target_username: str, column: str, value: bool, action: str,
) -> dict:
    require_admin(identity)
    if column not in {"calls_enabled", "muted", "auditor"}:
        raise ValueError(f"Unsupported profile flag: {column}")
    async with transaction() as connection:
        cursor = await connection.execute(
            f"""
            INSERT INTO messenger.profiles (username, display_name, {column})
            SELECT username, COALESCE(NULLIF(display_name, ''), username), %s
            FROM public.users WHERE username = %s
            ON CONFLICT (username) DO UPDATE SET {column} = EXCLUDED.{column}, updated_at = NOW()
            RETURNING username, {column}
            """,
            (value, target_username),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Profile not found")
        await audit(
            connection, identity, action, "profile", target_username, metadata={column: value},
        )
    return dict(row)


async def set_user_calls_enabled(identity: Identity, target_username: str, enabled: bool) -> dict:
    return await _set_profile_flag(
        identity, target_username, "calls_enabled", enabled, "admin.user_calls_updated",
    )


async def set_user_muted(identity: Identity, target_username: str, muted: bool) -> dict:
    return await _set_profile_flag(
        identity, target_username, "muted", muted, "admin.user_mute_updated",
    )


async def set_user_auditor(identity: Identity, target_username: str, auditor: bool) -> dict:
    return await _set_profile_flag(
        identity, target_username, "auditor", auditor, "admin.user_auditor_updated",
    )


async def set_user_active(identity: Identity, target_username: str, active: bool) -> dict:
    """Disabling an account also bumps its token version: identity_is_current()
    checks both, so existing sessions stop working immediately rather than
    lingering until their JWTs expire."""
    require_admin(identity)
    if target_username == identity.username and not active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot disable your own account")
    async with transaction() as connection:
        cursor = await connection.execute(
            """
            UPDATE public.users
            SET is_active = %s,
                messenger_token_version = CASE
                  WHEN %s THEN messenger_token_version
                  ELSE messenger_token_version + 1
                END,
                updated_at = NOW()
            WHERE username = %s
            RETURNING username, is_active, messenger_token_version
            """,
            (active, active, target_username),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        await audit(
            connection, identity,
            "admin.user_enabled" if active else "admin.user_disabled",
            "user", target_username, metadata={"is_active": active},
        )
    return dict(row)


async def revoke_user_sessions(identity: Identity, target_username: str) -> dict:
    require_admin(identity)
    async with transaction() as connection:
        cursor = await connection.execute(
            """
            UPDATE public.users
            SET messenger_token_version = messenger_token_version + 1, updated_at = NOW()
            WHERE username = %s
            RETURNING username, messenger_token_version
            """,
            (target_username,),
        )
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        await audit(
            connection, identity, "admin.sessions_revoked", "user", target_username,
            metadata={"token_version": int(row["messenger_token_version"])},
        )
    return dict(row)


async def revoke_all_sessions(identity: Identity) -> int:
    require_admin(identity)
    async with transaction() as connection:
        cursor = await connection.execute(
            """
            UPDATE public.users
            SET messenger_token_version = messenger_token_version + 1, updated_at = NOW()
            WHERE is_active = TRUE
            RETURNING username
            """
        )
        rows = await cursor.fetchall()
        await audit(
            connection, identity, "admin.sessions_revoked_all", "user", "*",
            metadata={"affected": len(rows)},
        )
    return len(rows)


async def is_user_muted(username: str) -> bool:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT muted FROM messenger.profiles WHERE username = %s", (username,),
        )
        row = await cursor.fetchone()
    return bool(row and row["muted"])


async def is_db_auditor(username: str) -> bool:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT auditor FROM messenger.profiles WHERE username = %s", (username,),
        )
        row = await cursor.fetchone()
    return bool(row and row["auditor"])


async def users_calls_allowed(usernames: list[str]) -> dict[str, bool]:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT username, calls_enabled FROM messenger.profiles WHERE username = ANY(%s)",
            (usernames,),
        )
        rows = await cursor.fetchall()
    return {row["username"]: bool(row["calls_enabled"]) for row in rows}


async def calls_globally_enabled() -> bool:
    return bool((await cached_admin_config())["calls_enabled"])


async def effective_group_max_members() -> int:
    return int((await cached_admin_config())["group_max_members"])


async def effective_message_rate_limit() -> int:
    return int((await cached_admin_config())["rate_messages_per_minute"])


async def effective_invites_per_day() -> int:
    return int((await cached_admin_config())["invites_per_day"])


async def effective_max_attachment_bytes() -> int:
    configured = (await cached_admin_config())["max_attachment_bytes"]
    return int(configured) if configured else get_settings().attachment_max_bytes


async def clear_expired_statuses() -> list[str]:
    async with transaction() as connection:
        cursor = await connection.execute(
            """
            UPDATE messenger.profiles
            SET dnd = FALSE, manual_away = FALSE, status_text = NULL, status_expires_at = NULL, updated_at = NOW()
            WHERE status_expires_at IS NOT NULL AND status_expires_at <= NOW()
            RETURNING username
            """
        )
        rows = await cursor.fetchall()
    return [row["username"] for row in rows]


async def record_security_event(
    identity: Identity,
    action: str,
    target_id: str,
    metadata: dict | None = None,
) -> None:
    async with transaction() as connection:
        await audit(
            connection,
            identity,
            action,
            "connection",
            target_id,
            metadata=metadata,
        )


async def list_audit_events(
    identity: Identity,
    before_id: int | None,
    limit: int,
) -> list[dict]:
    # The auditor role may come from the JWT (env-provisioned, legacy) or from
    # messenger.profiles.auditor, which is what the admin panel manages — the
    # DB flag takes effect without waiting for the user's token to be reissued.
    if not ({"security_auditor", "system_admin"} & set(identity.roles)):
        if not await is_db_auditor(identity.username):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Audit role required")
    async with pool().connection() as connection:
        cursor = await connection.execute(
            """
            SELECT id, actor_username, action, target_type, target_id,
                   result, metadata, created_at
            FROM messenger.audit_events
            WHERE (%s::BIGINT IS NULL OR id < %s)
            ORDER BY id DESC
            LIMIT %s
            """,
            (before_id, before_id, limit),
        )
        return list(await cursor.fetchall())


async def is_member(chat_id: UUID, username: str) -> bool:
    async with pool().connection() as connection:
        row = await connection.execute(
            """
            SELECT 1
            FROM messenger.memberships
            WHERE chat_id = %s AND username = %s AND left_at IS NULL
            """,
            (chat_id, username),
        )
        return await row.fetchone() is not None


async def require_membership(connection, chat_id: UUID, username: str) -> dict:
    cursor = await connection.execute(
        """
        SELECT role
        FROM messenger.memberships
        WHERE chat_id = %s AND username = %s AND left_at IS NULL
        """,
        (chat_id, username),
    )
    membership = await cursor.fetchone()
    if not membership:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")
    return membership


async def require_group_manager(connection, chat_id: UUID, identity: Identity) -> None:
    cursor = await connection.execute(
        """
        SELECT EXISTS (
          SELECT 1
          FROM messenger.chats c
          WHERE c.id = %s AND c.kind = 'group' AND c.deleted_at IS NULL
        ) AS group_exists,
        EXISTS (
          SELECT 1
          FROM messenger.memberships m
          WHERE m.chat_id = %s AND m.username = %s
            AND m.role = 'owner' AND m.left_at IS NULL
        ) AS is_owner
        """,
        (chat_id, chat_id, identity.username),
    )
    row = await cursor.fetchone()
    if not row or not row["group_exists"] or (
        not row["is_owner"] and "chat_admin" not in identity.roles
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")


async def _members(connection, chat_id: UUID) -> list[dict]:
    cursor = await connection.execute(
        """
        SELECT m.username,
               COALESCE(NULLIF(u.display_name, ''), NULLIF(p.display_name, ''), m.username) AS display_name,
               m.role,
               u.avatar_path,
               u.avatar_updated_at
        FROM messenger.memberships m
        LEFT JOIN messenger.profiles p ON p.username = m.username
        LEFT JOIN public.users u ON u.username = m.username
        WHERE m.chat_id = %s AND m.left_at IS NULL
        ORDER BY (m.role = 'owner') DESC, display_name
        """,
        (chat_id,),
    )
    return [_member_row(dict(row)) for row in await cursor.fetchall()]


async def _members_by_chat(connection, chat_ids: list[UUID]) -> dict[UUID, list[dict]]:
    if not chat_ids:
        return {}
    cursor = await connection.execute(
        """
        SELECT m.chat_id, m.username,
               COALESCE(NULLIF(u.display_name, ''), NULLIF(p.display_name, ''), m.username) AS display_name,
               m.role,
               u.avatar_path,
               u.avatar_updated_at
        FROM messenger.memberships m
        LEFT JOIN messenger.profiles p ON p.username = m.username
        LEFT JOIN public.users u ON u.username = m.username
        WHERE m.chat_id = ANY(%s) AND m.left_at IS NULL
        ORDER BY m.chat_id, (m.role = 'owner') DESC, display_name
        """,
        (chat_ids,),
    )
    result: dict[UUID, list[dict]] = {}
    for row in await cursor.fetchall():
        row = dict(row)
        chat_id = row.pop("chat_id")
        result.setdefault(chat_id, []).append(_member_row(row))
    return result


def _direct_key(username_a: str, username_b: str) -> str:
    return "\x1f".join(sorted([username_a, username_b]))


async def direct_chat_for_pair(username_a: str, username_b: str) -> UUID | None:
    direct_key = _direct_key(username_a, username_b)
    async with pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT id FROM messenger.chats WHERE direct_key = %s AND deleted_at IS NULL",
            (direct_key,),
        )
        row = await cursor.fetchone()
        return row["id"] if row else None


_CALL_FIELDS = (
    "id, chat_id, caller_username, callee_username, status, "
    "started_at, answered_at, ended_at, had_video"
)


async def create_call_ringing(
    call_id: UUID, chat_id: UUID, caller: str, callee: str, had_video: bool,
) -> dict:
    async with transaction() as connection:
        cursor = await connection.execute(
            f"""
            INSERT INTO messenger.calls (id, chat_id, caller_username, callee_username, had_video)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            RETURNING {_CALL_FIELDS}
            """,
            (call_id, chat_id, caller, callee, had_video),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        cursor = await connection.execute(
            f"SELECT {_CALL_FIELDS} FROM messenger.calls WHERE id = %s",
            (call_id,),
        )
        return dict(await cursor.fetchone())


async def get_call(call_id: UUID) -> dict | None:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            f"SELECT {_CALL_FIELDS} FROM messenger.calls WHERE id = %s",
            (call_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def record_call_busy(
    call_id: UUID, chat_id: UUID, caller: str, callee: str, had_video: bool,
) -> None:
    async with transaction() as connection:
        await connection.execute(
            """
            INSERT INTO messenger.calls
              (id, chat_id, caller_username, callee_username, status, had_video, started_at, ended_at)
            VALUES (%s, %s, %s, %s, 'busy', %s, NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
            """,
            (call_id, chat_id, caller, callee, had_video),
        )


async def transition_call(call_id: UUID, expected: tuple[str, ...], new_status: str) -> dict | None:
    set_answered = new_status == "accepted"
    set_ended = new_status in {"declined", "missed", "cancelled", "ended", "failed"}
    async with transaction() as connection:
        cursor = await connection.execute(
            f"""
            UPDATE messenger.calls
            SET status = %s,
                answered_at = CASE WHEN %s THEN NOW() ELSE answered_at END,
                ended_at = CASE WHEN %s THEN NOW() ELSE ended_at END
            WHERE id = %s AND status = ANY(%s)
            RETURNING {_CALL_FIELDS}
            """,
            (new_status, set_answered, set_ended, call_id, list(expected)),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


_SESSION_FIELDS = "id, chat_id, room_name, started_by, started_at, ended_at, ended_reason"


async def active_member_usernames(chat_id: UUID) -> list[str]:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT username FROM messenger.memberships WHERE chat_id=%s AND left_at IS NULL",
            (chat_id,),
        )
        return [row["username"] for row in await cursor.fetchall()]


async def create_or_get_active_session(chat_id: UUID, started_by: str) -> tuple[dict, bool]:
    async with transaction() as connection:
        chat_cursor = await connection.execute(
            "SELECT kind FROM messenger.chats WHERE id=%s AND deleted_at IS NULL",
            (chat_id,),
        )
        chat = await chat_cursor.fetchone()
        if not chat or chat["kind"] != "group":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Group chat not found")
        await require_membership(connection, chat_id, started_by)
        session_id = uuid4()
        cursor = await connection.execute(
            f"""
            INSERT INTO messenger.call_sessions (id, chat_id, room_name, started_by)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (chat_id) WHERE ended_at IS NULL DO NOTHING
            RETURNING {_SESSION_FIELDS}
            """,
            (session_id, chat_id, str(session_id), started_by),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row), True
        cursor = await connection.execute(
            f"""
            SELECT {_SESSION_FIELDS} FROM messenger.call_sessions
            WHERE chat_id=%s AND ended_at IS NULL
            """,
            (chat_id,),
        )
        existing = await cursor.fetchone()
        return dict(existing), False


async def end_session(session_id: UUID, reason: str) -> dict | None:
    async with transaction() as connection:
        cursor = await connection.execute(
            f"""
            UPDATE messenger.call_sessions
            SET ended_at = NOW(), ended_reason = %s
            WHERE id = %s AND ended_at IS NULL
            RETURNING {_SESSION_FIELDS}
            """,
            (reason, session_id),
        )
        row = await cursor.fetchone()
        if row:
            await connection.execute(
                """
                UPDATE messenger.call_participants
                SET left_at = NOW()
                WHERE session_id=%s AND left_at IS NULL
                """,
                (session_id,),
            )
        return dict(row) if row else None


async def add_participant(session_id: UUID, username: str) -> dict:
    async with transaction() as connection:
        cursor = await connection.execute(
            """
            INSERT INTO messenger.call_participants (session_id, username)
            VALUES (%s, %s)
            ON CONFLICT (session_id, username) WHERE left_at IS NULL DO NOTHING
            RETURNING joined_at
            """,
            (session_id, username),
        )
        row = await cursor.fetchone()
        if row:
            return {"joined_at": row["joined_at"]}
        cursor = await connection.execute(
            """
            SELECT joined_at FROM messenger.call_participants
            WHERE session_id=%s AND username=%s AND left_at IS NULL
            """,
            (session_id, username),
        )
        existing = await cursor.fetchone()
        return {"joined_at": existing["joined_at"]}


async def remove_participant(session_id: UUID, username: str) -> dict | None:
    async with transaction() as connection:
        cursor = await connection.execute(
            """
            UPDATE messenger.call_participants
            SET left_at = NOW()
            WHERE session_id=%s AND username=%s AND left_at IS NULL
            RETURNING id
            """,
            (session_id, username),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def list_active_participants(session_id: UUID) -> list[dict]:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            """
            SELECT p.username,
                   COALESCE(NULLIF(u.display_name, ''), NULLIF(pr.display_name, ''), p.username) AS display_name,
                   p.joined_at
            FROM messenger.call_participants p
            LEFT JOIN messenger.profiles pr ON pr.username = p.username
            LEFT JOIN public.users u ON u.username = p.username
            WHERE p.session_id=%s AND p.left_at IS NULL
            ORDER BY p.joined_at
            """,
            (session_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def get_active_session(chat_id: UUID) -> dict | None:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            f"""
            SELECT {_SESSION_FIELDS} FROM messenger.call_sessions
            WHERE chat_id=%s AND ended_at IS NULL
            """,
            (chat_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def list_active_sessions() -> list[dict]:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            f"SELECT {_SESSION_FIELDS} FROM messenger.call_sessions WHERE ended_at IS NULL"
        )
        return [dict(row) for row in await cursor.fetchall()]


async def list_call_sessions_history(chat_id: UUID, before: datetime | None, limit: int) -> list[dict]:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            f"""
            SELECT {_SESSION_FIELDS} FROM messenger.call_sessions
            WHERE chat_id=%s AND (%s::TIMESTAMPTZ IS NULL OR started_at < %s)
            ORDER BY started_at DESC
            LIMIT %s
            """,
            (chat_id, before, before, limit),
        )
        sessions = [dict(row) for row in await cursor.fetchall()]
        for session in sessions:
            participants_cursor = await connection.execute(
                """
                SELECT p.username,
                       COALESCE(NULLIF(u.display_name, ''), NULLIF(pr.display_name, ''), p.username) AS display_name,
                       p.joined_at, p.left_at
                FROM messenger.call_participants p
                LEFT JOIN messenger.profiles pr ON pr.username = p.username
                LEFT JOIN public.users u ON u.username = p.username
                WHERE p.session_id=%s
                ORDER BY p.joined_at
                """,
                (session["id"],),
            )
            session["participants"] = [dict(row) for row in await participants_cursor.fetchall()]
        return sessions


async def find_active_session_by_room(room_name: str) -> dict | None:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            f"""
            SELECT {_SESSION_FIELDS} FROM messenger.call_sessions
            WHERE room_name=%s AND ended_at IS NULL
            """,
            (room_name,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def close_session_by_room(room_name: str, reason: str) -> dict | None:
    async with transaction() as connection:
        cursor = await connection.execute(
            f"""
            UPDATE messenger.call_sessions
            SET ended_at = NOW(), ended_reason = %s
            WHERE room_name = %s AND ended_at IS NULL
            RETURNING {_SESSION_FIELDS}
            """,
            (reason, room_name),
        )
        row = await cursor.fetchone()
        if row:
            await connection.execute(
                """
                UPDATE messenger.call_participants
                SET left_at = NOW()
                WHERE session_id=%s AND left_at IS NULL
                """,
                (row["id"],),
            )
        return dict(row) if row else None


def _chat_sort_key(chat: dict, identity: Identity) -> tuple:
    if chat["kind"] == "group":
        return (0, (chat.get("title") or "").casefold())
    peer = next(
        (member for member in chat.get("members", []) if member["username"] != identity.username),
        None,
    )
    label = (peer["display_name"] if peer else chat.get("title") or "").casefold()
    return (1, label)


async def list_chats(identity: Identity) -> list[dict]:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            """
            SELECT
              c.id, c.kind, c.title, c.last_sequence, c.last_message_preview,
              c.updated_at,
              GREATEST(c.last_sequence - COALESCE(r.last_read_sequence, 0), 0)
                AS unread_count
            FROM messenger.chats c
            JOIN messenger.memberships m
              ON m.chat_id = c.id AND m.username = %s AND m.left_at IS NULL
            LEFT JOIN messenger.read_cursors r
              ON r.chat_id = c.id AND r.username = %s
            WHERE c.deleted_at IS NULL
              -- an empty direct chat only shows up for whoever opened it
              AND (c.kind <> 'direct' OR c.last_sequence > 0 OR c.created_by = %s)
            ORDER BY c.updated_at DESC, c.id
            """,
            (identity.username, identity.username, identity.username),
        )
        chats = list(await cursor.fetchall())
        members = await _members_by_chat(connection, [chat["id"] for chat in chats])
        for chat in chats:
            chat["members"] = members.get(chat["id"], [])
        chats.sort(key=lambda chat: _chat_sort_key(chat, identity))
        return chats


async def create_chat(identity: Identity, payload: CreateChat) -> tuple[dict, bool]:
    requested = sorted(set(payload.members + [identity.username]))
    if payload.kind == "direct" and len(requested) != 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose another user")
    direct_key = _direct_key(requested[0], requested[1]) if payload.kind == "direct" else None
    async with transaction() as connection:
        cursor = await connection.execute(
            """
            SELECT username, display_name
            FROM public.users
            WHERE username = ANY(%s) AND is_active = TRUE
            """,
            (requested,),
        )
        active = {row["username"]: row for row in await cursor.fetchall()}
        missing = sorted(set(requested) - set(active))
        if missing:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown or inactive member")

        if direct_key:
            cursor = await connection.execute(
                """
                SELECT id, kind, title, last_sequence, last_message_preview, updated_at
                FROM messenger.chats
                WHERE direct_key = %s AND deleted_at IS NULL
                """,
                (direct_key,),
            )
            existing = await cursor.fetchone()
            if existing:
                existing["unread_count"] = 0
                existing["members"] = await _members(connection, existing["id"])
                return existing, False

        if direct_key:
            cursor = await connection.execute(
                """
                INSERT INTO messenger.chats (kind, title, direct_key, created_by)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (direct_key)
                  WHERE direct_key IS NOT NULL AND deleted_at IS NULL
                  DO NOTHING
                RETURNING id, kind, title, last_sequence, last_message_preview, updated_at
                """,
                (payload.kind, payload.title.strip(), direct_key, identity.username),
            )
            chat = await cursor.fetchone()
            if not chat:
                cursor = await connection.execute(
                    """
                    SELECT id, kind, title, last_sequence, last_message_preview, updated_at
                    FROM messenger.chats
                    WHERE direct_key = %s AND deleted_at IS NULL
                    """,
                    (direct_key,),
                )
                existing = await cursor.fetchone()
                existing["unread_count"] = 0
                existing["members"] = await _members(connection, existing["id"])
                return existing, False
        else:
            cursor = await connection.execute(
                """
                INSERT INTO messenger.chats (kind, title, direct_key, created_by)
                VALUES (%s, %s, %s, %s)
                RETURNING id, kind, title, last_sequence, last_message_preview, updated_at
                """,
                (payload.kind, payload.title.strip(), direct_key, identity.username),
            )
            chat = await cursor.fetchone()
        for username in requested:
            role = "owner" if username == identity.username else "member"
            await connection.execute(
                """
                INSERT INTO messenger.profiles (username, display_name)
                VALUES (%s, %s)
                ON CONFLICT (username) DO UPDATE SET
                  display_name = EXCLUDED.display_name,
                  updated_at = NOW()
                """,
                (username, active[username]["display_name"] or username),
            )
            await connection.execute(
                """
                INSERT INTO messenger.memberships (chat_id, username, role, added_by)
                VALUES (%s, %s, %s, %s)
                """,
                (chat["id"], username, role, identity.username),
            )
        await audit(connection, identity, "chat.create", "chat", str(chat["id"]))
        chat["unread_count"] = 0
        chat["members"] = await _members(connection, chat["id"])
        return chat, True


async def add_group_member(identity: Identity, chat_id: UUID, username: str) -> dict:
    if username == identity.username:
        # chat_admin may manage any group's members, but must not use that to
        # slip into a private group and read its history.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нельзя добавить в группу самого себя")
    async with transaction() as connection:
        await require_group_manager(connection, chat_id, identity)
        cursor = await connection.execute(
            """
            SELECT username, display_name
            FROM public.users
            WHERE username = %s AND is_active = TRUE
            """,
            (username,),
        )
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown or inactive user")
        cursor = await connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM messenger.memberships
            WHERE chat_id = %s AND left_at IS NULL
            """,
            (chat_id,),
        )
        if int((await cursor.fetchone())["count"]) >= await effective_group_max_members():
            raise HTTPException(status.HTTP_409_CONFLICT, "Group member limit reached")
        await connection.execute(
            """
            INSERT INTO messenger.profiles (username, display_name)
            VALUES (%s, %s)
            ON CONFLICT (username) DO UPDATE SET
              display_name = EXCLUDED.display_name, updated_at = NOW()
            """,
            (username, user["display_name"] or username),
        )
        await connection.execute(
            """
            INSERT INTO messenger.memberships (chat_id, username, role, added_by)
            VALUES (%s, %s, 'member', %s)
            ON CONFLICT (chat_id, username) DO UPDATE SET
              role = 'member', added_by = EXCLUDED.added_by,
              joined_at = NOW(), left_at = NULL
            """,
            (chat_id, username, identity.username),
        )
        await audit(
            connection,
            identity,
            "membership.add",
            "chat",
            str(chat_id),
            metadata={"username": username},
        )
        return {"chat_id": str(chat_id), "username": username, "role": "member"}


async def remove_group_member(identity: Identity, chat_id: UUID, username: str) -> dict:
    async with transaction() as connection:
        await require_group_manager(connection, chat_id, identity)
        cursor = await connection.execute(
            """
            UPDATE messenger.memberships
            SET left_at = NOW()
            WHERE chat_id = %s AND username = %s
              AND role = 'member' AND left_at IS NULL
            RETURNING username
            """,
            (chat_id, username),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
        await audit(
            connection,
            identity,
            "membership.remove",
            "chat",
            str(chat_id),
            metadata={"username": username},
        )
        return {"chat_id": str(chat_id), "username": username, "removed": True}


async def _require_group(connection, chat_id: UUID) -> None:
    cursor = await connection.execute(
        "SELECT 1 FROM messenger.chats WHERE id = %s AND kind = 'group' AND deleted_at IS NULL",
        (chat_id,),
    )
    if await cursor.fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")


async def leave_group(identity: Identity, chat_id: UUID) -> dict:
    """The caller leaves a group. An owner hands ownership to the longest-standing
    member; the last member to leave closes the group."""
    async with transaction() as connection:
        await _require_group(connection, chat_id)
        membership = await require_membership(connection, chat_id, identity.username)
        await connection.execute(
            "UPDATE messenger.memberships SET left_at = NOW() WHERE chat_id = %s AND username = %s",
            (chat_id, identity.username),
        )
        new_owner = None
        cursor = await connection.execute(
            """
            SELECT username, role FROM messenger.memberships
            WHERE chat_id = %s AND left_at IS NULL
            ORDER BY (role = 'owner') DESC, joined_at, username
            """,
            (chat_id,),
        )
        remaining = list(await cursor.fetchall())
        if not remaining:
            await connection.execute(
                "UPDATE messenger.chats SET deleted_at = NOW() WHERE id = %s", (chat_id,),
            )
        elif membership["role"] == "owner" and remaining[0]["role"] != "owner":
            new_owner = remaining[0]["username"]
            await connection.execute(
                "UPDATE messenger.memberships SET role = 'owner' WHERE chat_id = %s AND username = %s",
                (chat_id, new_owner),
            )
        await audit(
            connection, identity, "membership.leave", "chat", str(chat_id),
            metadata={"new_owner": new_owner} if new_owner else None,
        )
    return {
        "chat_id": str(chat_id), "username": identity.username, "removed": True,
        "new_owner": new_owner, "closed": not remaining,
    }


async def rename_group(identity: Identity, chat_id: UUID, title: str) -> dict:
    async with transaction() as connection:
        await require_group_manager(connection, chat_id, identity)
        await connection.execute(
            "UPDATE messenger.chats SET title = %s, updated_at = NOW() WHERE id = %s",
            (title, chat_id),
        )
        await audit(connection, identity, "chat.rename", "chat", str(chat_id), metadata={"title": title})
    return {"chat_id": str(chat_id), "title": title}


async def delete_group(identity: Identity, chat_id: UUID) -> None:
    async with transaction() as connection:
        await require_group_manager(connection, chat_id, identity)
        await connection.execute(
            "UPDATE messenger.chats SET deleted_at = NOW() WHERE id = %s", (chat_id,),
        )
        await audit(connection, identity, "chat.delete", "chat", str(chat_id))


async def list_messages(
    identity: Identity,
    chat_id: UUID,
    before_sequence: int | None,
    limit: int,
) -> list[dict]:
    async with pool().connection() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            """
            SELECT
              m.id, m.client_id, m.chat_id, m.sequence, m.sender_username,
              COALESCE(NULLIF(u.display_name, ''), NULLIF(p.display_name, ''), m.sender_username) AS sender_display_name,
              CASE WHEN m.deleted_at IS NULL THEN m.text ELSE '' END AS text,
              m.reply_to_id, m.forwarded_from_message_id, m.forwarded_from_chat_id,
              m.forwarded_sender_username, m.forwarded_at,
              m.created_at, m.edited_at, m.deleted_at
            FROM messenger.messages m
            LEFT JOIN messenger.profiles p ON p.username = m.sender_username
            LEFT JOIN public.users u ON u.username = m.sender_username
            WHERE m.chat_id = %s
              AND (%s::BIGINT IS NULL OR m.sequence < %s)
              AND NOT EXISTS (
                SELECT 1 FROM messenger.message_hides h
                WHERE h.message_id = m.id AND h.username = %s
              )
            ORDER BY m.sequence DESC
            LIMIT %s
            """,
            (chat_id, before_sequence, before_sequence, identity.username, limit),
        )
        rows = list(await cursor.fetchall())
        rows.reverse()
        await _enrich_messages(connection, rows, identity.username)
        return rows


async def _enrich_messages(connection, messages: list[dict], username: str) -> None:
    for message in messages:
        if message.get("reply_to_id"):
            cursor = await connection.execute(
                """
                SELECT id, sender_username,
                       CASE WHEN deleted_at IS NULL THEN LEFT(text, 240) ELSE '' END AS text,
                       deleted_at
                FROM messenger.messages WHERE id=%s AND chat_id=%s
                """,
                (message["reply_to_id"], message["chat_id"]),
            )
            message["reply_preview"] = await cursor.fetchone()
        else:
            message["reply_preview"] = None
        cursor = await connection.execute(
            """
            SELECT emoji, COUNT(*)::INTEGER AS count,
                   BOOL_OR(username=%s) AS reacted_by_me
            FROM messenger.reactions WHERE message_id=%s
            GROUP BY emoji ORDER BY MIN(created_at), emoji
            """,
            (username, message["id"]),
        )
        message["reactions"] = list(await cursor.fetchall())
        cursor = await connection.execute(
            """
            SELECT id, original_name, media_type, size_bytes, sha256, inline_safe
            FROM messenger.attachments
            WHERE message_id=%s AND status='clean'
              AND EXISTS (
                SELECT 1 FROM messenger.messages
                WHERE id=%s AND deleted_at IS NULL
              )
            ORDER BY created_at, id
            """,
            (message["id"], message["id"]),
        )
        message["attachments"] = list(await cursor.fetchall())
        cursor = await connection.execute(
            "SELECT 1 FROM messenger.pins WHERE chat_id=%s AND message_id=%s",
            (message["chat_id"], message["id"]),
        )
        message["pinned"] = await cursor.fetchone() is not None

    await _apply_read_status(connection, messages, username)


async def _apply_read_status(connection, messages: list[dict], username: str) -> None:
    if not messages:
        return
    chat_id = messages[0]["chat_id"]
    cursor = await connection.execute(
        """
        SELECT COALESCE(r.last_read_sequence, 0) AS last_read_sequence
        FROM messenger.memberships m
        LEFT JOIN messenger.read_cursors r
          ON r.chat_id = m.chat_id AND r.username = m.username
        WHERE m.chat_id = %s AND m.left_at IS NULL AND m.username != %s
        """,
        (chat_id, username),
    )
    others = [int(row["last_read_sequence"]) for row in await cursor.fetchall()]
    for message in messages:
        if message.get("sender_username") != username or message.get("deleted_at"):
            message["read"] = None
            continue
        if not others:
            message["read"] = False
            continue
        message["read"] = int(message["sequence"]) <= min(others)


async def list_member_read_cursors(identity: Identity, chat_id: UUID) -> dict:
    async with pool().connection() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            """
            SELECT
              m.username,
              COALESCE(r.last_read_sequence, 0) AS sequence,
              r.updated_at
            FROM messenger.memberships m
            LEFT JOIN messenger.read_cursors r
              ON r.chat_id = m.chat_id AND r.username = m.username
            WHERE m.chat_id = %s AND m.left_at IS NULL
            ORDER BY m.username
            """,
            (chat_id,),
        )
        return {"members": list(await cursor.fetchall())}


async def send_message(
    identity: Identity,
    chat_id: UUID,
    payload: SendMessage,
) -> tuple[dict, bool]:
    async with transaction() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            "SELECT muted FROM messenger.profiles WHERE username = %s", (identity.username,),
        )
        muted_row = await cursor.fetchone()
        if muted_row and muted_row["muted"]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Отправка сообщений отключена администратором",
            )
        cursor = await connection.execute(
            """
            SELECT
              m.id, m.client_id, m.chat_id, m.sequence, m.sender_username,
              COALESCE(NULLIF(u.display_name, ''), NULLIF(p.display_name, ''), m.sender_username) AS sender_display_name,
              m.text, m.reply_to_id, m.forwarded_from_message_id,
              m.forwarded_from_chat_id, m.forwarded_sender_username, m.forwarded_at,
              m.created_at, m.edited_at, m.deleted_at
            FROM messenger.messages m
            LEFT JOIN messenger.profiles p ON p.username = m.sender_username
            LEFT JOIN public.users u ON u.username = m.sender_username
            WHERE m.sender_username = %s AND m.client_id = %s
            """,
            (identity.username, payload.client_id),
        )
        existing = await cursor.fetchone()
        if existing:
            if existing["chat_id"] != chat_id:
                raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency key already used")
            await _enrich_messages(connection, [existing], identity.username)
            return existing, False

        if payload.reply_to_id:
            cursor = await connection.execute(
                "SELECT 1 FROM messenger.messages WHERE id = %s AND chat_id = %s",
                (payload.reply_to_id, chat_id),
            )
            if await cursor.fetchone() is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid reply target")

        forwarded = None
        if payload.forward_message_id:
            cursor = await connection.execute(
                """
                SELECT m.id, m.chat_id, m.sender_username,
                       CASE WHEN m.deleted_at IS NULL THEN m.text ELSE '' END AS text
                FROM messenger.messages m
                JOIN messenger.memberships mm ON mm.chat_id=m.chat_id
                  AND mm.username=%s AND mm.left_at IS NULL
                WHERE m.id=%s
                """,
                (identity.username, payload.forward_message_id),
            )
            forwarded = await cursor.fetchone()
            if not forwarded or not forwarded["text"]:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Forward source not found")
        message_text = payload.text or (forwarded["text"] if forwarded else "")

        if payload.attachment_ids:
            cursor = await connection.execute(
                """
                SELECT id FROM messenger.attachments
                WHERE id=ANY(%s) AND chat_id=%s AND uploader_username=%s
                  AND status='clean' AND message_id IS NULL
                FOR UPDATE
                """,
                (payload.attachment_ids, chat_id, identity.username),
            )
            available = {row["id"] for row in await cursor.fetchall()}
            if available != set(payload.attachment_ids):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid attachment")

        cursor = await connection.execute(
            """
            UPDATE messenger.chats
            SET last_sequence = last_sequence + 1,
                last_message_preview = LEFT(%s, 160),
                updated_at = NOW()
            WHERE id = %s AND deleted_at IS NULL
            RETURNING last_sequence
            """,
            (chat_preview_from_text(message_text), chat_id),
        )
        chat = await cursor.fetchone()
        if not chat:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found")

        try:
            cursor = await connection.execute(
                """
                INSERT INTO messenger.messages
                  (chat_id, sequence, sender_username, client_id, text, reply_to_id,
                   forwarded_from_message_id, forwarded_from_chat_id,
                   forwarded_sender_username, forwarded_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s::UUID IS NULL THEN NULL ELSE NOW() END)
                RETURNING
                  id, client_id, chat_id, sequence, sender_username, %s AS sender_display_name,
                  text, reply_to_id, forwarded_from_message_id, forwarded_from_chat_id,
                  forwarded_sender_username, forwarded_at, created_at, edited_at, deleted_at
                """,
                (
                    chat_id,
                    chat["last_sequence"],
                    identity.username,
                    payload.client_id,
                    message_text,
                    payload.reply_to_id,
                    forwarded["id"] if forwarded else None,
                    forwarded["chat_id"] if forwarded else None,
                    forwarded["sender_username"] if forwarded else None,
                    forwarded["id"] if forwarded else None,
                    identity.display_name,
                ),
            )
            message = await cursor.fetchone()
            if payload.attachment_ids:
                await connection.execute(
                    "UPDATE messenger.attachments SET message_id=%s WHERE id=ANY(%s)",
                    (message["id"], payload.attachment_ids),
                )
        except UniqueViolation as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, "Duplicate message") from exc
        await _enrich_messages(connection, [message], identity.username)
        return message, True


async def edit_message(
    identity: Identity,
    chat_id: UUID,
    message_id: UUID,
    payload: EditMessage,
) -> dict:
    async with transaction() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            """
            UPDATE messenger.messages
            SET text = %s, edited_at = NOW()
            WHERE id = %s AND chat_id = %s
              AND sender_username = %s AND deleted_at IS NULL
            RETURNING
              id, client_id, chat_id, sequence, sender_username, %s AS sender_display_name,
              text, reply_to_id, forwarded_from_message_id, forwarded_from_chat_id,
              forwarded_sender_username, forwarded_at, created_at, edited_at, deleted_at
            """,
            (payload.text, message_id, chat_id, identity.username, identity.display_name),
        )
        message = await cursor.fetchone()
        if not message:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
        await audit(connection, identity, "message.edit", "message", str(message_id))
        await _enrich_messages(connection, [message], identity.username)
        return message


async def delete_message(
    identity: Identity,
    chat_id: UUID,
    message_id: UUID,
    *,
    for_everyone: bool = False,
) -> dict:
    async with transaction() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            """
            SELECT
              id, client_id, chat_id, sequence, sender_username, %s AS sender_display_name,
              CASE WHEN deleted_at IS NULL THEN text ELSE '' END AS text,
              reply_to_id, forwarded_from_message_id, forwarded_from_chat_id,
              forwarded_sender_username, forwarded_at, created_at, edited_at, deleted_at
            FROM messenger.messages
            WHERE id = %s AND chat_id = %s
            """,
            (identity.display_name, message_id, chat_id),
        )
        existing = await cursor.fetchone()
        if not existing:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
        if existing.get("deleted_at"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")

        if for_everyone:
            if existing["sender_username"] != identity.username:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "Only the sender can delete the message for everyone",
                )
            cursor = await connection.execute(
                """
                UPDATE messenger.messages
                SET text = '', deleted_at = NOW()
                WHERE id = %s AND chat_id = %s
                  AND sender_username = %s AND deleted_at IS NULL
                RETURNING
                  id, client_id, chat_id, sequence, sender_username, %s AS sender_display_name,
                  text, reply_to_id, forwarded_from_message_id, forwarded_from_chat_id,
                  forwarded_sender_username, forwarded_at, created_at, edited_at, deleted_at
                """,
                (message_id, chat_id, identity.username, identity.display_name),
            )
            message = await cursor.fetchone()
            if not message:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
            await audit(connection, identity, "message.delete", "message", str(message_id))
            await _enrich_messages(connection, [message], identity.username)
            message["_for_everyone"] = True
            return message

        await connection.execute(
            """
            INSERT INTO messenger.message_hides (message_id, username)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (message_id, identity.username),
        )
        await audit(connection, identity, "message.hide", "message", str(message_id))
        await _enrich_messages(connection, [existing], identity.username)
        existing["_for_everyone"] = False
        existing["_hidden"] = True
        return existing

async def set_reaction(
    identity: Identity, chat_id: UUID, message_id: UUID, emoji: str, add: bool
) -> tuple[dict, bool]:
    # Strip the U+FE0F "emoji-style" variation selector and normalize form —
    # some clients include it and some don't for the exact same visible
    # emoji (e.g. heart), which otherwise makes GROUP BY below treat one
    # reaction as two, splitting its count instead of summing it.
    emoji = normalize("NFC", emoji.strip()).replace("️", "")
    if not emoji or len(emoji) > 32 or any(character.isspace() for character in emoji):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid reaction")
    async with transaction() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            "SELECT 1 FROM messenger.messages WHERE id=%s AND chat_id=%s AND deleted_at IS NULL",
            (message_id, chat_id),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
        if add:
            # Telegram-like free tier: one reaction per user per message.
            await connection.execute(
                """
                DELETE FROM messenger.reactions
                WHERE message_id=%s AND username=%s AND emoji<>%s
                """,
                (message_id, identity.username, emoji),
            )
            cursor = await connection.execute(
                """
                INSERT INTO messenger.reactions (message_id, username, emoji)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING 1
                """,
                (message_id, identity.username, emoji),
            )
        else:
            cursor = await connection.execute(
                """
                DELETE FROM messenger.reactions
                WHERE message_id=%s AND username=%s AND emoji=%s RETURNING 1
                """,
                (message_id, identity.username, emoji),
            )
        changed = await cursor.fetchone() is not None
        cursor = await connection.execute(
            """
            SELECT emoji, COUNT(*)::INTEGER AS count,
                   BOOL_OR(username=%s) AS reacted_by_me
            FROM messenger.reactions WHERE message_id=%s
            GROUP BY emoji ORDER BY MIN(created_at), emoji
            """,
            (identity.username, message_id),
        )
        return {
            "chat_id": str(chat_id),
            "message_id": str(message_id),
            "actor_username": identity.username,
            "emoji": emoji,
            "added": add,
            "reactions": list(await cursor.fetchall()),
        }, changed


async def set_pin(
    identity: Identity, chat_id: UUID, message_id: UUID, add: bool
) -> tuple[dict, bool]:
    async with transaction() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            "SELECT 1 FROM messenger.messages WHERE id=%s AND chat_id=%s AND deleted_at IS NULL",
            (message_id, chat_id),
        )
        if await cursor.fetchone() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
        if add:
            cursor = await connection.execute(
                """
                INSERT INTO messenger.pins (chat_id, message_id, pinned_by)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                RETURNING pinned_by, pinned_at
                """,
                (chat_id, message_id, identity.username),
            )
        else:
            cursor = await connection.execute(
                """
                DELETE FROM messenger.pins WHERE chat_id=%s AND message_id=%s
                RETURNING pinned_by, pinned_at
                """,
                (chat_id, message_id),
            )
        row = await cursor.fetchone()
        return {
            "chat_id": str(chat_id), "message_id": str(message_id),
            "pinned": add, "actor_username": identity.username,
        }, row is not None


async def list_pins(identity: Identity, chat_id: UUID) -> list[dict]:
    async with pool().connection() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            """
            SELECT p.message_id, p.pinned_by, p.pinned_at, m.sequence,
                   m.sender_username,
                   CASE WHEN m.deleted_at IS NULL THEN LEFT(m.text, 240) ELSE '' END AS text
            FROM messenger.pins p JOIN messenger.messages m ON m.id=p.message_id
            WHERE p.chat_id=%s ORDER BY p.pinned_at DESC
            """,
            (chat_id,),
        )
        return list(await cursor.fetchall())


async def search_messages(
    identity: Identity, chat_id: UUID, query: str, before: int | None, limit: int
) -> list[dict]:
    query = query.strip()
    if len(query) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Search query is too short")
    async with pool().connection() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            """
            SELECT m.id, m.client_id, m.chat_id, m.sequence, m.sender_username,
                   COALESCE(NULLIF(u.display_name, ''), NULLIF(p.display_name, ''), m.sender_username) AS sender_display_name,
                   m.text, m.reply_to_id, m.forwarded_from_message_id,
                   m.forwarded_from_chat_id, m.forwarded_sender_username, m.forwarded_at,
                   m.created_at, m.edited_at, m.deleted_at
            FROM messenger.messages m
            LEFT JOIN messenger.profiles p ON p.username=m.sender_username
            LEFT JOIN public.users u ON u.username = m.sender_username
            WHERE m.chat_id=%s AND m.deleted_at IS NULL
              AND (%s::BIGINT IS NULL OR m.sequence < %s)
              AND to_tsvector('simple', m.text) @@ plainto_tsquery('simple', %s)
              AND NOT EXISTS (
                SELECT 1 FROM messenger.message_hides h
                WHERE h.message_id = m.id AND h.username = %s
              )
            ORDER BY m.sequence DESC LIMIT %s
            """,
            (chat_id, before, before, query, identity.username, limit),
        )
        messages = list(await cursor.fetchall())
        await _enrich_messages(connection, messages, identity.username)
        return messages


async def update_read_cursor(identity: Identity, chat_id: UUID, sequence: int) -> dict:
    async with transaction() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            "SELECT last_sequence FROM messenger.chats WHERE id = %s",
            (chat_id,),
        )
        chat = await cursor.fetchone()
        if not chat or sequence > chat["last_sequence"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid read sequence")
        cursor = await connection.execute(
            """ 
            INSERT INTO messenger.read_cursors (chat_id, username, last_read_sequence)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id, username) DO UPDATE SET
              last_read_sequence = GREATEST(
                messenger.read_cursors.last_read_sequence,
                EXCLUDED.last_read_sequence
              ),
              updated_at = NOW()
            RETURNING last_read_sequence, updated_at
            """,
            (chat_id, identity.username, sequence),
        )
        saved = await cursor.fetchone()
        return {
            "chat_id": str(chat_id),
            "username": identity.username,
            "sequence": saved["last_read_sequence"],
            "updated_at": saved["updated_at"],
        }
