"""Remove AI bots: drop access grants, disable bot accounts, hide their chats.

Bot accounts were the only local users seeded without a password hash.

Revision ID: 0013_remove_ai_bots
Revises: 0012_bot_access
Create Date: 2026-10-01
"""

from alembic import op


revision = "0013_remove_ai_bots"
down_revision = "0012_bot_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE messenger.chats c
        SET deleted_at = NOW()
        WHERE c.kind = 'direct' AND c.deleted_at IS NULL
          AND EXISTS (
            SELECT 1
            FROM messenger.memberships m
            JOIN public.users u ON u.username = m.username
            WHERE m.chat_id = c.id
              AND u.auth_source = 'local' AND u.password_hash IS NULL
          )
        """
    )
    op.execute(
        """
        UPDATE public.users
        SET is_active = FALSE, updated_at = NOW()
        WHERE auth_source = 'local' AND password_hash IS NULL AND is_active = TRUE
        """
    )
    op.execute("DROP TABLE IF EXISTS messenger.bot_access")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.bot_access (
          bot_username  TEXT NOT NULL,
          username      TEXT NOT NULL,
          granted_by    TEXT NOT NULL,
          granted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (bot_username, username)
        )
        """
    )
