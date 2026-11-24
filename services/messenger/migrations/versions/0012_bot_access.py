"""AI bot access grants: which humans may see/start a chat with which bot.

Revision ID: 0012_bot_access
Revises: 0011_normalize_reaction_emoji
Create Date: 2026-09-11
"""

from alembic import op


revision = "0012_bot_access"
down_revision = "0011_normalize_reaction_emoji"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_bot_access_username ON messenger.bot_access(username)"
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app') THEN
            GRANT SELECT, INSERT, DELETE ON messenger.bot_access TO messenger_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messenger.bot_access")
