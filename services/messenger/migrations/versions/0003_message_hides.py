"""Per-user message hides (delete for me).

Revision ID: 0003_message_hides
Revises: 0002_rich_messenger
Create Date: 2026-08-04
"""

from alembic import op


revision = "0003_message_hides"
down_revision = "0002_rich_messenger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.message_hides (
          message_id UUID NOT NULL REFERENCES messenger.messages(id) ON DELETE CASCADE,
          username TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (message_id, username)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_message_hides_user
          ON messenger.message_hides (username, message_id)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON messenger.message_hides
              TO messenger_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messenger.message_hides")
