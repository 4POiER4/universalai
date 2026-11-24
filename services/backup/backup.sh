#!/bin/sh
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_ENCRYPTION_PASSPHRASE:?BACKUP_ENCRYPTION_PASSPHRASE is required}"

umask 077
export GNUPGHOME=/tmp/gnupg
mkdir -p "${GNUPGHOME}"
mkdir -p /backups
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="/backups/universalai-${stamp}.dump.gpg"
attachment_target="/backups/universalai-${stamp}.attachments.tar.gpg"

pg_dump --dbname="${DATABASE_URL}" --format=custom --no-owner --no-privileges |
  gpg --batch --yes --symmetric --cipher-algo AES256 \
    --pinentry-mode loopback --passphrase-fd 3 \
    --output "${target}" 3<<EOF
${BACKUP_ENCRYPTION_PASSPHRASE}
EOF

sha256sum "${target}" > "${target}.sha256"
if [ -d /attachments ]; then
  tar -C /attachments -cf - . |
    gpg --batch --yes --symmetric --cipher-algo AES256 \
      --pinentry-mode loopback --passphrase-fd 3 \
      --output "${attachment_target}" 3<<EOF
${BACKUP_ENCRYPTION_PASSPHRASE}
EOF
  sha256sum "${attachment_target}" > "${attachment_target}.sha256"
fi
find /backups -type f -mtime "+${BACKUP_RETENTION_DAYS:-30}" -delete
echo "Encrypted backup created: ${target}"
