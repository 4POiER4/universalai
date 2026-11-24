"""Group calls: joinable-room sessions (LiveKit SFU).

Revision ID: 0006_group_calls
Revises: 0005_calls
Create Date: 2026-08-07
"""

from alembic import op


revision = "0006_group_calls"
down_revision = "0005_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.call_sessions (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          chat_id       UUID NOT NULL REFERENCES messenger.chats(id) ON DELETE CASCADE,
          room_name     TEXT NOT NULL UNIQUE,
          started_by    TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          ended_at      TIMESTAMPTZ,
          ended_reason  TEXT CHECK (ended_reason IN ('empty', 'left', 'webhook', 'admin')),
          created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messenger_call_sessions_active_chat
          ON messenger.call_sessions(chat_id)
          WHERE ended_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_call_sessions_chat_history
          ON messenger.call_sessions(chat_id, started_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.call_participants (
          id          BIGSERIAL PRIMARY KEY,
          session_id  UUID NOT NULL REFERENCES messenger.call_sessions(id) ON DELETE CASCADE,
          username    TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          left_at     TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_messenger_call_participants_active
          ON messenger.call_participants(session_id, username)
          WHERE left_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_call_participants_session
          ON messenger.call_participants(session_id, joined_at)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON messenger.call_sessions TO messenger_app;
            GRANT SELECT, INSERT, UPDATE, DELETE ON messenger.call_participants TO messenger_app;
            GRANT USAGE, SELECT ON SEQUENCE messenger.call_participants_id_seq TO messenger_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messenger.call_participants")
    op.execute("DROP TABLE IF EXISTS messenger.call_sessions")
