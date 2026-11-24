"""Manual presence override: Away (in addition to DND).

Revision ID: 0007_manual_away
Revises: 0006_group_calls
Create Date: 2026-08-16
"""

from alembic import op


revision = "0007_manual_away"
down_revision = "0006_group_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE messenger.profiles
          ADD COLUMN IF NOT EXISTS manual_away BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    op.execute(
        """
        ALTER TABLE messenger.profiles
          ADD CONSTRAINT chk_messenger_profiles_status_exclusive
          CHECK (NOT (dnd AND manual_away))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE messenger.profiles
          DROP CONSTRAINT IF EXISTS chk_messenger_profiles_status_exclusive
        """
    )
    op.execute(
        """
        ALTER TABLE messenger.profiles
          DROP COLUMN IF EXISTS manual_away
        """
    )
