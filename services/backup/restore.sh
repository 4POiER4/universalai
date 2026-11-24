#!/bin/sh
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"
: "${BACKUP_ENCRYPTION_PASSPHRASE:?BACKUP_ENCRYPTION_PASSPHRASE is required}"
: "${BACKUP_FILE:?BACKUP_FILE is required}"

umask 077
export GNUPGHOME=/tmp/gnupg
mkdir -p "${GNUPGHOME}"

if [ "${BACKUP_FILE}" = "latest" ]; then
  BACKUP_FILE=""
  for candidate in /backups/*.dump.gpg; do
    [ -f "${candidate}" ] || continue
    if [ -z "${BACKUP_FILE}" ] || [ "${candidate}" \> "${BACKUP_FILE}" ]; then
      BACKUP_FILE="${candidate}"
    fi
  done
fi
if [ -z "${BACKUP_FILE}" ] || [ ! -f "${BACKUP_FILE}" ]; then
  echo "Backup file was not found" >&2
  exit 1
fi

sha256sum -c "${BACKUP_FILE}.sha256"
gpg --batch --quiet --decrypt --pinentry-mode loopback --passphrase-fd 3 \
  "${BACKUP_FILE}" 3<<EOF |
${BACKUP_ENCRYPTION_PASSPHRASE}
EOF
  pg_restore --dbname="${DATABASE_URL}" --clean --if-exists --no-owner --no-privileges

attachment_file="${BACKUP_FILE%.dump.gpg}.attachments.tar.gpg"
if [ -f "${attachment_file}" ]; then
  : "${ATTACHMENT_RESTORE_ROOT:=/attachments}"
  mkdir -p "${ATTACHMENT_RESTORE_ROOT}"
  sha256sum -c "${attachment_file}.sha256"
  gpg --batch --quiet --decrypt --pinentry-mode loopback --passphrase-fd 3 \
    "${attachment_file}" 3<<EOF |
${BACKUP_ENCRYPTION_PASSPHRASE}
EOF
    tar -C "${ATTACHMENT_RESTORE_ROOT}" -xf -
fi

echo "Restore completed from ${BACKUP_FILE}"
