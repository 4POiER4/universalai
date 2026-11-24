"""Admin panel: runtime retention/limits, per-user moderation flags, auditor role in DB.

Revision ID: 0009_admin_panel
Revises: 0008_admin_controls
Create Date: 2026-08-25
"""

from alembic import op


revision = "0009_admin_panel"
down_revision = "0008_admin_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Runtime-editable policy that used to live only in env vars. Defaults
    # match the pydantic Settings defaults so an existing deployment keeps
    # behaving identically until an admin changes something.
    # A 0-day retention means "keep forever" (the UI calls it «бессрочно»).
    op.execute(
        """
        ALTER TABLE messenger.admin_config
          ADD COLUMN IF NOT EXISTS auto_purge_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
          ADD COLUMN IF NOT EXISTS retention_messages_days  INTEGER NOT NULL DEFAULT 365
            CHECK (retention_messages_days >= 0 AND retention_messages_days <= 3650),
          ADD COLUMN IF NOT EXISTS retention_files_days     INTEGER NOT NULL DEFAULT 180
            CHECK (retention_files_days >= 0 AND retention_files_days <= 3650),
          ADD COLUMN IF NOT EXISTS retention_audit_days     INTEGER NOT NULL DEFAULT 730
            CHECK (retention_audit_days >= 0 AND retention_audit_days <= 3650),
          ADD COLUMN IF NOT EXISTS retention_backups_days   INTEGER NOT NULL DEFAULT 30
            CHECK (retention_backups_days >= 0 AND retention_backups_days <= 3650),
          ADD COLUMN IF NOT EXISTS rate_messages_per_minute INTEGER NOT NULL DEFAULT 60
            CHECK (rate_messages_per_minute BETWEEN 1 AND 1000),
          ADD COLUMN IF NOT EXISTS rate_logins_per_hour     INTEGER NOT NULL DEFAULT 10
            CHECK (rate_logins_per_hour BETWEEN 1 AND 1000),
          ADD COLUMN IF NOT EXISTS group_max_members        INTEGER NOT NULL DEFAULT 250
            CHECK (group_max_members BETWEEN 2 AND 5000),
          ADD COLUMN IF NOT EXISTS invites_per_day          INTEGER NOT NULL DEFAULT 25
            CHECK (invites_per_day BETWEEN 1 AND 1000)
        """
    )
    op.execute(
        """
        ALTER TABLE messenger.profiles
          ADD COLUMN IF NOT EXISTS muted   BOOLEAN NOT NULL DEFAULT FALSE,
          ADD COLUMN IF NOT EXISTS auditor BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app') THEN
            -- Column-scoped on purpose: the messenger may disable an account
            -- and invalidate its tokens, but must not be able to rewrite
            -- credentials, roles or identity fields on public.users.
            GRANT UPDATE (is_active, messenger_token_version, updated_at)
              ON public.users TO messenger_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app') THEN
            REVOKE UPDATE (is_active, messenger_token_version, updated_at)
              ON public.users FROM messenger_app;
          END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE messenger.profiles
          DROP COLUMN IF EXISTS auditor,
          DROP COLUMN IF EXISTS muted
        """
    )
    op.execute(
        """
        ALTER TABLE messenger.admin_config
          DROP COLUMN IF EXISTS invites_per_day,
          DROP COLUMN IF EXISTS group_max_members,
          DROP COLUMN IF EXISTS rate_logins_per_hour,
          DROP COLUMN IF EXISTS rate_messages_per_minute,
          DROP COLUMN IF EXISTS retention_backups_days,
          DROP COLUMN IF EXISTS retention_audit_days,
          DROP COLUMN IF EXISTS retention_files_days,
          DROP COLUMN IF EXISTS retention_messages_days,
          DROP COLUMN IF EXISTS auto_purge_enabled
        """
    )
