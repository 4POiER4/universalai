CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ── Пользователи и платформа ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username       TEXT NOT NULL UNIQUE,
  display_name   TEXT NOT NULL DEFAULT '',
  avatar_path    TEXT,
  avatar_updated_at TIMESTAMPTZ,
  password_hash  TEXT,
  auth_source    TEXT NOT NULL DEFAULT 'local'
                   CHECK (auth_source IN ('local','ldap','sso')),
  roles          TEXT[] NOT NULL DEFAULT ARRAY['user']::TEXT[],
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  last_login_at  TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE users ADD COLUMN IF NOT EXISTS department  TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS title       TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS email       TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone       TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS office      TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN IF NOT EXISTS external_dn TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS synced_at   TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS ad_disabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_users_department ON users(department, display_name) WHERE is_active;
