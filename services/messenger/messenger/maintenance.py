import asyncio
import logging
import time
from pathlib import Path
from types import SimpleNamespace

from .config import get_settings
from .database import transaction
from .repository import get_admin_config


logger = logging.getLogger("messenger.maintenance")


async def apply_retention() -> None:
    settings = get_settings()
    config = await get_admin_config()
    if not config["auto_purge_enabled"]:
        logger.info("Retention skipped: nightly purge is disabled in admin config")
        return
    # 0 days means "keep forever" in the admin panel; the env value is only a
    # fallback for a deployment whose config row predates these columns.
    message_days = config["retention_messages_days"] or 0
    audit_days = config["retention_audit_days"] or 0
    async with transaction() as connection:
        messages = SimpleNamespace(rowcount=0)
        if message_days > 0:
            messages = await connection.execute(
                """
                UPDATE messenger.messages
                SET text = '', deleted_at = COALESCE(deleted_at, NOW())
                WHERE created_at < NOW() - (%s * INTERVAL '1 day')
                  AND text <> ''
                """,
                (message_days,),
            )
        audit_deleted = 0
        if audit_days > 0:
            try:
                # Nested transaction (SAVEPOINT): if this fails, only this
                # statement rolls back — the outer transaction stays usable for
                # the statements that follow. A bare try/except around a plain
                # execute() is not enough: Postgres aborts the whole transaction
                # on the first error, and every later statement on `connection`
                # would fail with InFailedSqlTransaction even though the Python
                # exception was already caught.
                async with connection.transaction():
                    audits = await connection.execute(
                        """
                        DELETE FROM messenger.audit_events
                        WHERE created_at < NOW() - (%s * INTERVAL '1 day')
                        """,
                        (audit_days,),
                    )
                    audit_deleted = audits.rowcount
            except Exception as exc:
                # Append-only grant may omit DELETE; operators purge with migration role.
                if exc.__class__.__name__ != "InsufficientPrivilege":
                    raise
                logger.warning("Audit retention skipped: messenger_app lacks DELETE on audit_events")
        cursor = await connection.execute(
            """
            DELETE FROM messenger.attachments
            WHERE message_id IS NULL
              AND created_at < NOW() - (%s * INTERVAL '1 hour')
            RETURNING storage_name
            """,
            (settings.attachment_orphan_hours,),
        )
        orphan_names = [str(row["storage_name"]) for row in await cursor.fetchall()]
        # Attachment retention is deliberately separate from (and usually
        # shorter than) message retention — media dominates storage, so the
        # admin panel lets it expire first while the text stays readable.
        file_days = config["retention_files_days"] or 0
        if file_days > 0:
            cursor = await connection.execute(
                """
                DELETE FROM messenger.attachments
                WHERE created_at < NOW() - (%s * INTERVAL '1 day')
                RETURNING storage_name
                """,
                (file_days,),
            )
            orphan_names.extend(str(row["storage_name"]) for row in await cursor.fetchall())
        cursor = await connection.execute("SELECT storage_name FROM messenger.attachments")
        known_names = {str(row["storage_name"]) for row in await cursor.fetchall()}
        stale_calls = await connection.execute(
            """
            UPDATE messenger.calls
            SET status = 'missed', ended_at = COALESCE(ended_at, started_at)
            WHERE status = 'ringing' AND started_at < NOW() - INTERVAL '1 hour'
            """
        )
        # Last-resort net for group-call sessions: the LiveKit webhook is the
        # normal cleanup path, this only catches a dropped webhook delivery
        # (crashed participant whose "left" event never arrived) or a session
        # abandoned well past any realistic call length.
        stale_sessions = await connection.execute(
            """
            UPDATE messenger.call_sessions
            SET ended_at = NOW(), ended_reason = 'empty'
            WHERE ended_at IS NULL
              AND (
                started_at < NOW() - INTERVAL '12 hours'
                OR (
                  started_at < NOW() - INTERVAL '10 minutes'
                  AND NOT EXISTS (
                    SELECT 1 FROM messenger.call_participants cp
                    WHERE cp.session_id = call_sessions.id AND cp.left_at IS NULL
                  )
                )
              )
            RETURNING id
            """
        )
        stale_session_ids = [row["id"] for row in await stale_sessions.fetchall()]
        if stale_session_ids:
            await connection.execute(
                """
                UPDATE messenger.call_participants
                SET left_at = NOW()
                WHERE session_id = ANY(%s) AND left_at IS NULL
                """,
                (stale_session_ids,),
            )
    root = Path(settings.attachment_root)
    for storage_name in orphan_names:
        for directory in ("clean", "quarantine"):
            (root / directory / storage_name).unlink(missing_ok=True)
    cutoff = time.time() - settings.attachment_orphan_hours * 3600
    filesystem_orphans = 0
    for directory in ("clean", "quarantine"):
        path = root / directory
        if not path.is_dir():
            continue
        for candidate in path.iterdir():
            if (
                candidate.is_file()
                and candidate.name not in known_names
                and candidate.stat().st_mtime < cutoff
            ):
                candidate.unlink(missing_ok=True)
                filesystem_orphans += 1
    logger.info(
        "Retention applied: message_tombstones=%s audit_deleted=%s attachment_orphans=%s "
        "stale_calls=%s stale_sessions=%s",
        messages.rowcount,
        audit_deleted,
        len(orphan_names) + filesystem_orphans,
        stale_calls.rowcount,
        len(stale_session_ids),
    )


async def retention_loop() -> None:
    while True:
        try:
            await apply_retention()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Retention job failed")
        await asyncio.sleep(24 * 60 * 60)
