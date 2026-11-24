#!/bin/sh
set -eu

alembic upgrade head
exec uvicorn messenger.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips="${MESSENGER_FORWARDED_ALLOW_IPS:-127.0.0.1}"
