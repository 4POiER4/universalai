"""Manual presence override: DND + free-text status.

Revision ID: 0004_manual_presence_status
Revises: 0003_message_hides
Create Date: 2026-08-07
"""

from alembic import op


revision = "0004_manual_presence_status"
down_revision = "0003_message_hides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE messenger.profiles
          ADD COLUMN IF NOT EXISTS dnd BOOLEAN NOT NULL DEFAULT FALSE,
          ADD COLUMN IF NOT EXISTS status_text TEXT CHECK (char_length(status_text) <= 60),
          ADD COLUMN IF NOT EXISTS status_expires_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_profiles_status_expiry
          ON messenger.profiles(status_expires_at)
          WHERE status_expires_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS messenger.idx_messenger_profiles_status_expiry")
    op.execute(
        """
        ALTER TABLE messenger.profiles
          DROP COLUMN IF EXISTS status_expires_at,
          DROP COLUMN IF EXISTS status_text,
          DROP COLUMN IF EXISTS dnd
        """
    )
