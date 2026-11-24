"""Admin controls: global/per-user call permission and attachment size override.

Revision ID: 0008_admin_controls
Revises: 0007_manual_away
Create Date: 2026-08-16
"""

from alembic import op


revision = "0008_admin_controls"
down_revision = "0007_manual_away"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE messenger.profiles
          ADD COLUMN IF NOT EXISTS calls_enabled BOOLEAN NOT NULL DEFAULT TRUE
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger.admin_config (
          id                    BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
          calls_enabled         BOOLEAN NOT NULL DEFAULT TRUE,
          max_attachment_bytes  BIGINT CHECK (max_attachment_bytes IS NULL OR max_attachment_bytes > 0),
          updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          updated_by            TEXT
        )
        """
    )
    op.execute(
        """
        INSERT INTO messenger.admin_config (id) VALUES (TRUE)
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app') THEN
            GRANT SELECT, UPDATE ON messenger.admin_config TO messenger_app;
          END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messenger.admin_config")
    op.execute(
        """
        ALTER TABLE messenger.profiles
          DROP COLUMN IF EXISTS calls_enabled
        """
    )
