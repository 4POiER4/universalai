import asyncio
import hashlib
import os
import re
import struct
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile, status

from .auth import Identity
from .config import get_settings
from .database import pool, transaction
from .repository import audit, effective_max_attachment_bytes, require_membership


DANGEROUS_EXTENSIONS = {
    ".app", ".bat", ".cmd", ".com", ".cpl", ".dll", ".dmg", ".exe", ".hta",
    ".docm", ".htm", ".html", ".img", ".iso", ".jar", ".js", ".jse", ".lnk",
    ".msi", ".msp", ".php", ".pptm", ".ps1", ".py", ".reg", ".scr", ".sh",
    ".svg", ".vbe", ".vbs", ".wsf", ".xhtml", ".xlsm",
    ".appx", ".appxbundle", ".msix", ".msixbundle", ".chm", ".pif", ".url",
    ".website", ".vhd", ".vhdx", ".one", ".onepkg", ".dotm", ".xltm", ".xlam",
    ".ppam", ".potm", ".ppsm", ".sldm", ".iqy", ".slk", ".settingcontent-ms",
    ".library-ms", ".search-ms", ".scf", ".inf", ".ins", ".isp", ".msc", ".gadget",
    ".psm1", ".psd1", ".ws", ".wsc", ".wsh", ".vb", ".jnlp", ".application",
    ".xbap", ".mht", ".mhtml", ".xll", ".ade", ".adp", ".mde", ".mda", ".accde",
}
IMAGE_SIGNATURES = {
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/webp": (b"RIFF",),
}
IMAGE_EXTENSIONS = {
    "image/gif": {".gif"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
    "image/webp": {".webp"},
}
SAFE_NAME = re.compile(r"[\x00-\x1f\x7f]")


def safe_original_name(value: str | None) -> str:
    name = SAFE_NAME.sub("", Path((value or "attachment").replace("\\", "/")).name).strip(" .")
    return (name or "attachment")[:255]


def validate_file(name: str, media_type: str, prefix: bytes) -> bool:
    extension = Path(name).suffix.lower()
    if extension in DANGEROUS_EXTENSIONS:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "File type is not allowed")
    if media_type in IMAGE_SIGNATURES:
        if extension not in IMAGE_EXTENSIONS[media_type]:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Image extension mismatch")
        signatures = IMAGE_SIGNATURES[media_type]
        valid = any(prefix.startswith(item) for item in signatures)
        if media_type == "image/webp":
            valid = prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP"
        if not valid:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Image signature mismatch")
        return True
    if media_type == "application/pdf" and not prefix.startswith(b"%PDF-"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "PDF signature mismatch")
    return False


async def clamav_scan(path: Path) -> tuple[bool, str]:
    settings = get_settings()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.clamav_host, settings.clamav_port),
            timeout=settings.clamav_timeout_seconds,
        )
        writer.write(b"zINSTREAM\0")
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                writer.write(struct.pack(">I", len(chunk)) + chunk)
                await writer.drain()
        writer.write(struct.pack(">I", 0))
        await writer.drain()
        result = (await asyncio.wait_for(
            reader.readuntil(b"\0"), timeout=settings.clamav_timeout_seconds
        )).decode("utf-8", "replace")
        writer.close()
        await writer.wait_closed()
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Malware scanner unavailable") from exc
    if result.endswith("OK\0"):
        return True, ""
    return False, result.rstrip("\0")[:500]


async def store_upload(
    identity: Identity, chat_id: UUID, upload: UploadFile
) -> dict:
    settings = get_settings()
    root = Path(settings.attachment_root)
    quarantine = root / "quarantine"
    clean = root / "clean"
    quarantine.mkdir(parents=True, exist_ok=True)
    clean.mkdir(parents=True, exist_ok=True)
    storage_name = uuid4()
    temporary_path = quarantine / str(storage_name)
    original_name = safe_original_name(upload.filename)
    media_type = (upload.content_type or "application/octet-stream").lower().split(";")[0]
    digest = hashlib.sha256()
    size = 0
    prefix = b""
    async with pool().connection() as connection:
        await require_membership(connection, chat_id, identity.username)
    max_bytes = await effective_max_attachment_bytes()
    registered = False
    try:
        with temporary_path.open("xb") as destination:
            while chunk := await upload.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Attachment too large")
                if len(prefix) < 32:
                    prefix += chunk[: 32 - len(prefix)]
                digest.update(chunk)
                await asyncio.to_thread(destination.write, chunk)
        inline_safe = validate_file(original_name, media_type, prefix)
        async with transaction() as connection:
            cursor = await connection.execute(
                """
                INSERT INTO messenger.attachments
                  (chat_id, uploader_username, storage_name, original_name, media_type,
                   size_bytes, sha256, status, inline_safe)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'quarantine', %s)
                RETURNING *
                """,
                (chat_id, identity.username, storage_name, original_name, media_type,
                 size, digest.hexdigest(), inline_safe),
            )
            attachment = await cursor.fetchone()
            registered = True
        is_clean, reason = await clamav_scan(temporary_path)
        if is_clean:
            os.replace(temporary_path, clean / str(storage_name))
            final_status = "clean"
        else:
            temporary_path.unlink(missing_ok=True)
            final_status = "rejected"
        async with transaction() as connection:
            cursor = await connection.execute(
                """
                UPDATE messenger.attachments
                SET status=%s, rejection_reason=%s, scanned_at=NOW()
                WHERE id=%s RETURNING *
                """,
                (final_status, reason or None, attachment["id"]),
            )
            attachment = await cursor.fetchone()
            await audit(connection, identity, "attachment.upload", "attachment",
                        str(attachment["id"]), result="success" if is_clean else "failure")
        if not is_clean:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Attachment rejected")
        return {
            key: attachment[key]
            for key in (
                "id", "chat_id", "original_name", "media_type", "size_bytes",
                "sha256", "status", "inline_safe", "created_at", "scanned_at",
            )
        }
    except Exception:
        if not registered and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)
        raise


async def store_wallpaper_upload(identity: Identity, upload: UploadFile) -> dict:
    """One custom wallpaper per user — same quarantine/scan flow as chat
    attachments, but keyed by username instead of chat_id (this is a
    per-user client preference, not something shared into a conversation).
    A rejected upload leaves any existing wallpaper untouched."""
    settings = get_settings()
    root = Path(settings.attachment_root)
    quarantine = root / "quarantine"
    clean = root / "clean"
    quarantine.mkdir(parents=True, exist_ok=True)
    clean.mkdir(parents=True, exist_ok=True)
    storage_name = uuid4()
    temporary_path = quarantine / str(storage_name)
    original_name = safe_original_name(upload.filename)
    media_type = (upload.content_type or "application/octet-stream").lower().split(";")[0]
    digest = hashlib.sha256()
    size = 0
    prefix = b""
    max_bytes = await effective_max_attachment_bytes()
    try:
        with temporary_path.open("xb") as destination:
            while chunk := await upload.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Wallpaper too large")
                if len(prefix) < 32:
                    prefix += chunk[: 32 - len(prefix)]
                digest.update(chunk)
                await asyncio.to_thread(destination.write, chunk)
        if not validate_file(original_name, media_type, prefix):
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only images are allowed")
        is_clean, _reason = await clamav_scan(temporary_path)
        if not is_clean:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Wallpaper rejected")
        async with transaction() as connection:
            previous_cursor = await connection.execute(
                "SELECT storage_name FROM messenger.wallpapers WHERE username = %s", (identity.username,)
            )
            previous_row = await previous_cursor.fetchone()
            cursor = await connection.execute(
                """
                INSERT INTO messenger.wallpapers
                  (username, storage_name, original_name, media_type, size_bytes, sha256, status, scanned_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'clean', NOW())
                ON CONFLICT (username) DO UPDATE SET
                  storage_name = EXCLUDED.storage_name, original_name = EXCLUDED.original_name,
                  media_type = EXCLUDED.media_type, size_bytes = EXCLUDED.size_bytes,
                  sha256 = EXCLUDED.sha256, status = 'clean', rejection_reason = NULL,
                  created_at = NOW(), scanned_at = NOW()
                RETURNING username, original_name, media_type, size_bytes, created_at, scanned_at
                """,
                (identity.username, storage_name, original_name, media_type, size, digest.hexdigest()),
            )
            wallpaper = await cursor.fetchone()
            await audit(connection, identity, "wallpaper.upload", "wallpaper", identity.username, result="success")
        os.replace(temporary_path, clean / str(storage_name))
        if previous_row:
            (clean / str(previous_row["storage_name"])).unlink(missing_ok=True)
        return dict(wallpaper)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


async def get_wallpaper_download(identity: Identity) -> tuple[dict, Path]:
    async with pool().connection() as connection:
        cursor = await connection.execute(
            "SELECT * FROM messenger.wallpapers WHERE username = %s AND status = 'clean'",
            (identity.username,),
        )
        wallpaper = await cursor.fetchone()
    if not wallpaper:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wallpaper not found")
    path = Path(get_settings().attachment_root) / "clean" / str(wallpaper["storage_name"])
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wallpaper not found")
    return wallpaper, path


async def delete_wallpaper(identity: Identity) -> None:
    async with transaction() as connection:
        cursor = await connection.execute(
            "DELETE FROM messenger.wallpapers WHERE username = %s RETURNING storage_name",
            (identity.username,),
        )
        row = await cursor.fetchone()
        if row:
            await audit(connection, identity, "wallpaper.delete", "wallpaper", identity.username, result="success")
    if row:
        path = Path(get_settings().attachment_root) / "clean" / str(row["storage_name"])
        path.unlink(missing_ok=True)


async def get_download(identity: Identity, chat_id: UUID, attachment_id: UUID) -> tuple[dict, Path]:
    async with pool().connection() as connection:
        await require_membership(connection, chat_id, identity.username)
        cursor = await connection.execute(
            """
            SELECT a.* FROM messenger.attachments a
            JOIN messenger.messages m ON m.id=a.message_id AND m.deleted_at IS NULL
            WHERE a.id=%s AND a.chat_id=%s AND a.status='clean'
            """,
            (attachment_id, chat_id),
        )
        attachment = await cursor.fetchone()
    if not attachment:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    path = Path(get_settings().attachment_root) / "clean" / str(attachment["storage_name"])
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attachment not found")
    return attachment, path
