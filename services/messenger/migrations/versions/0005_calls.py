"""1:1 voice/video calls: history table for signaling-relay state transitions.

Revision ID: 0005_calls
Revises: 0004_manual_presence_status
Create Date: 2026-08-07
"""

from alembic import op


revision = "0005_calls"
down_revision = "0004_manual_presence_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.calls (
          id               UUID PRIMARY KEY,
          chat_id          UUID NOT NULL REFERENCES messenger.chats(id) ON DELETE CASCADE,
          caller_username  TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          callee_username  TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          status           TEXT NOT NULL DEFAULT 'ringing'
            CHECK (status IN ('ringing','accepted','declined','missed','cancelled','ended','busy','failed')),
          had_video        BOOLEAN NOT NULL DEFAULT FALSE,
          started_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          answered_at      TIMESTAMPTZ,
          ended_at         TIMESTAMPTZ,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          CHECK (caller_username <> callee_username)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_calls_chat
          ON messenger.calls(chat_id, started_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_calls_caller
          ON messenger.calls(caller_username, started_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_calls_callee
          ON messenger.calls(callee_username, started_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_calls_stale_ringing
          ON messenger.calls(started_at)
          WHERE status = 'ringing'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON messenger.calls TO messenger_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messenger.calls")
