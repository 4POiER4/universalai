#!/bin/sh
set -eu

if [ -z "${MESSENGER_DB_PASSWORD:-}" ]; then
  echo "MESSENGER_DB_PASSWORD is required" >&2
  exit 1
fi

export PGPASSWORD="${POSTGRES_PASSWORD}"
until pg_isready -h postgres -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"; do
  sleep 1
done

psql \
  --host=postgres \
  --username="${POSTGRES_USER}" \
  --dbname="${POSTGRES_DB}" \
  --set=ON_ERROR_STOP=1 \
  --set=messenger_password="${MESSENGER_DB_PASSWORD}" <<'SQL'
SELECT format(
  'CREATE ROLE messenger_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
  :'messenger_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'messenger_app')
\gexec

SELECT format('ALTER ROLE messenger_app PASSWORD %L', :'messenger_password')
\gexec

GRANT CONNECT ON DATABASE universalai TO messenger_app;
SQL
