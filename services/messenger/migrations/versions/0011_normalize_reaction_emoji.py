"""Normalize stored reaction emoji so visually-identical reactions (with or
without the U+FE0F variation selector) group into one count instead of two.

Revision ID: 0011_normalize_reaction_emoji
Revises: 0010_wallpapers
Create Date: 2026-08-26
"""

from alembic import op


revision = "0011_normalize_reaction_emoji"
down_revision = "0010_wallpapers"
branch_labels = None
depends_on = None

# U+FE0F VARIATION SELECTOR-16 ("emoji-style" marker) — chr(65039) rather
# than an embedded literal so this file has no invisible/combining
# characters in its source.
_FE0F_EXPR = "chr(65039)"


def upgrade() -> None:
    op.execute(
        f"""
        -- A user could have both an FE0F and non-FE0F row for the same
        -- message pre-fix (from different reaction sources); collapse those
        -- to the earliest one before the emoji text itself is normalized,
        -- since the table's primary key is (message_id, username, emoji).
        WITH ranked AS (
          SELECT ctid, ROW_NUMBER() OVER (
            PARTITION BY message_id, username, replace(emoji, {_FE0F_EXPR}, '')
            ORDER BY created_at
          ) AS rn
          FROM messenger.reactions
        )
        DELETE FROM messenger.reactions
        WHERE ctid IN (SELECT ctid FROM ranked WHERE rn > 1)
        """
    )
    op.execute(
        f"""
        UPDATE messenger.reactions
        SET emoji = replace(emoji, {_FE0F_EXPR}, '')
        WHERE emoji LIKE '%' || {_FE0F_EXPR} || '%'
        """
    )


def downgrade() -> None:
    pass
