"""Per-user custom chat wallpaper uploads (ClamAV-scanned, one per user).

Revision ID: 0010_wallpapers
Revises: 0009_admin_panel
Create Date: 2026-08-26
"""

from alembic import op


revision = "0010_wallpapers"
down_revision = "0009_admin_panel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.wallpapers (
          username TEXT PRIMARY KEY REFERENCES messenger.profiles(username) ON DELETE CASCADE ON UPDATE CASCADE,
          storage_name UUID NOT NULL UNIQUE,
          original_name TEXT NOT NULL,
          media_type TEXT NOT NULL,
          size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
          sha256 TEXT NOT NULL CHECK (char_length(sha256) = 64),
          status TEXT NOT NULL CHECK (status IN ('quarantine', 'clean', 'rejected')),
          rejection_reason TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          scanned_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON messenger.wallpapers TO messenger_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messenger.wallpapers")
