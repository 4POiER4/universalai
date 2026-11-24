"""Create the isolated Messenger schema.

Revision ID: 0001_secure_messenger
Revises:
Create Date: 2026-08-04
"""

from alembic import op


revision = "0001_secure_messenger"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS messenger")
    op.execute(
        """
        ALTER TABLE public.users
        ADD COLUMN IF NOT EXISTS messenger_token_version INTEGER NOT NULL DEFAULT 0
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.profiles (
          username      TEXT PRIMARY KEY REFERENCES public.users(username) ON UPDATE CASCADE,
          display_name  TEXT NOT NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.chats (
          id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          kind                  TEXT NOT NULL CHECK (kind IN ('direct', 'group')),
          title                 TEXT NOT NULL DEFAULT '',
          direct_key            TEXT,
          created_by            TEXT NOT NULL REFERENCES public.users(username),
          last_sequence         BIGINT NOT NULL DEFAULT 0 CHECK (last_sequence >= 0),
          last_message_preview  TEXT NOT NULL DEFAULT '',
          created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          deleted_at            TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_messenger_direct_chat
          ON messenger.chats(direct_key)
          WHERE direct_key IS NOT NULL AND deleted_at IS NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_chats_updated
          ON messenger.chats(updated_at DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.memberships (
          chat_id     UUID NOT NULL REFERENCES messenger.chats(id) ON DELETE CASCADE,
          username    TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          role        TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
          added_by    TEXT NOT NULL REFERENCES public.users(username),
          joined_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          left_at     TIMESTAMPTZ,
          PRIMARY KEY (chat_id, username)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_memberships_user
          ON messenger.memberships(username, chat_id)
          WHERE left_at IS NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.messages (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          chat_id          UUID NOT NULL REFERENCES messenger.chats(id) ON DELETE CASCADE,
          sequence         BIGINT NOT NULL CHECK (sequence > 0),
          sender_username  TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          client_id        UUID NOT NULL,
          text             TEXT NOT NULL CHECK (char_length(text) <= 32000),
          reply_to_id      UUID REFERENCES messenger.messages(id),
          created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          edited_at        TIMESTAMPTZ,
          deleted_at       TIMESTAMPTZ,
          UNIQUE (chat_id, sequence),
          UNIQUE (sender_username, client_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_messages_history
          ON messenger.messages(chat_id, sequence DESC)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.read_cursors (
          chat_id             UUID NOT NULL REFERENCES messenger.chats(id) ON DELETE CASCADE,
          username            TEXT NOT NULL REFERENCES messenger.profiles(username) ON UPDATE CASCADE,
          last_read_sequence  BIGINT NOT NULL DEFAULT 0 CHECK (last_read_sequence >= 0),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (chat_id, username)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.audit_events (
          id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
          actor_username  TEXT NOT NULL,
          action          TEXT NOT NULL,
          target_type     TEXT NOT NULL,
          target_id       TEXT NOT NULL,
          result          TEXT NOT NULL CHECK (result IN ('success', 'failure')),
          metadata        JSONB NOT NULL DEFAULT '{}'::JSONB,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messenger_audit_created
          ON messenger.audit_events(created_at DESC)
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app') THEN
            GRANT USAGE ON SCHEMA messenger TO messenger_app;
            GRANT SELECT ON public.users TO messenger_app;
            GRANT SELECT, INSERT, UPDATE, DELETE
              ON messenger.profiles, messenger.chats, messenger.memberships,
                 messenger.messages, messenger.read_cursors
              TO messenger_app;
            GRANT SELECT, INSERT ON messenger.audit_events TO messenger_app;
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA messenger TO messenger_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS messenger CASCADE")
    op.execute(
        "ALTER TABLE public.users DROP COLUMN IF EXISTS messenger_token_version"
    )
