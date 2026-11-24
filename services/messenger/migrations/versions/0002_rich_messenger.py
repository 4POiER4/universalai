"""Add rich messaging metadata and local attachments.

Revision ID: 0002_rich_messenger
Revises: 0001_secure_messenger
Create Date: 2026-08-04
"""

from alembic import op


revision = "0002_rich_messenger"
down_revision = "0001_secure_messenger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE messenger.messages
          ADD COLUMN IF NOT EXISTS forwarded_from_message_id UUID,
          ADD COLUMN IF NOT EXISTS forwarded_from_chat_id UUID,
          ADD COLUMN IF NOT EXISTS forwarded_sender_username TEXT,
          ADD COLUMN IF NOT EXISTS forwarded_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.reactions (
          message_id UUID NOT NULL REFERENCES messenger.messages(id) ON DELETE CASCADE,
          username TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          emoji TEXT NOT NULL CHECK (char_length(emoji) BETWEEN 1 AND 32),
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (message_id, username, emoji)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.attachments (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          chat_id UUID NOT NULL REFERENCES messenger.chats(id) ON DELETE CASCADE,
          message_id UUID REFERENCES messenger.messages(id) ON DELETE CASCADE,
          uploader_username TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          storage_name UUID NOT NULL UNIQUE,
          original_name TEXT NOT NULL,
          media_type TEXT NOT NULL,
          size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
          sha256 TEXT NOT NULL CHECK (char_length(sha256) = 64),
          status TEXT NOT NULL CHECK (status IN ('quarantine', 'clean', 'rejected')),
          inline_safe BOOLEAN NOT NULL DEFAULT FALSE,
          rejection_reason TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          scanned_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_messenger_attachments_message "
        "ON messenger.attachments(message_id) WHERE message_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_messenger_attachments_orphans "
        "ON messenger.attachments(created_at) WHERE message_id IS NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.pins (
          chat_id UUID NOT NULL REFERENCES messenger.chats(id) ON DELETE CASCADE,
          message_id UUID NOT NULL REFERENCES messenger.messages(id) ON DELETE CASCADE,
          pinned_by TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          pinned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (chat_id, message_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_messenger_messages_search "
        "ON messenger.messages USING GIN (to_tsvector('simple', text))"
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON messenger.reactions, messenger.attachments, messenger.pins
              TO messenger_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messenger.pins")
    op.execute("DROP TABLE IF EXISTS messenger.attachments")
    op.execute("DROP TABLE IF EXISTS messenger.reactions")
    op.execute(
        """
        ALTER TABLE messenger.messages
          DROP COLUMN IF EXISTS forwarded_at,
          DROP COLUMN IF EXISTS forwarded_sender_username,
          DROP COLUMN IF EXISTS forwarded_from_chat_id,
          DROP COLUMN IF EXISTS forwarded_from_message_id
        """
    )
