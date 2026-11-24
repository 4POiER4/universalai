"""Hide the empty direct chats that used to be created between every pair of users.

Direct chats are now created on demand from the company directory, so an
unused pair no longer needs a row; chats with any message history stay.

Revision ID: 0014_hide_empty_dms
Revises: 0013_remove_ai_bots
Create Date: 2026-10-02
"""

from alembic import op


revision = "0014_hide_empty_dms"
down_revision = "0013_remove_ai_bots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE messenger.chats c
        SET deleted_at = NOW()
        WHERE c.kind = 'direct' AND c.deleted_at IS NULL AND c.last_sequence = 0
          AND NOT EXISTS (SELECT 1 FROM messenger.messages m WHERE m.chat_id = c.id)
          AND NOT EXISTS (SELECT 1 FROM messenger.call_sessions s WHERE s.chat_id = c.id)
          AND NOT EXISTS (SELECT 1 FROM messenger.calls k WHERE k.chat_id = c.id)
        """
    )


def downgrade() -> None:
    pass
