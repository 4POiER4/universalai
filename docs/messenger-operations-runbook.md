# Messenger operations and pilot runbook

## Production prerequisites

1. Copy `deploy/nginx.production.conf.example` to
   `deploy/nginx.production.conf` and install the approved certificate as
   `deploy/tls/fullchain.pem` and `deploy/tls/privkey.pem`.
2. Generate an Ed25519 pair with `python scripts/generate_messenger_keys.py`.
   Store the private key only with the Flask platform and the public key with
   Messenger. Rotate `MESSENGER_JWT_KEY_ID` for every new pair.
3. Set unique random PostgreSQL, Messenger-role, Flask, local bootstrap and
   backup-encryption secrets. Do not use values from `.env.example`.
4. Set `MESSENGER_ALLOWED_ORIGINS` to the exact production HTTPS origin.
5. Restrict host ports 80/443 through the approved firewall/VPN. PostgreSQL,
   Redis, Flask and Messenger API ports must not be externally reachable.
6. Confirm host-volume and backup-media encryption, EDR/antivirus, approved NTP,
   centralized logs/SIEM and operating-system patching.

Start production with:

```powershell
docker compose -f docker-compose.yml -f docker-compose.production.yml up --build -d
```

## Monitoring

Poll `/health`, `/ready`, `/health/live` and `/health/ready` from the internal
monitoring system. Alert on:

- non-2xx readiness for two consecutive intervals;
- API 5xx or authentication failures above the approved baseline;
- repeated rate-limit events, reconnect storms or unusual chat fan-out;
- PostgreSQL storage, connection or replication pressure;
- Redis eviction/availability;
- certificate/key expiration and clock drift;
- backup absence or failed restore exercise.

Application/security logs may contain usernames, action metadata and request IDs,
but never message text, access tokens, passwords or LDAP credentials.

## Encrypted backup and restore drill

Create a backup:

```powershell
docker compose --profile operations run --rm backup
```

At least quarterly, restore into an isolated empty PostgreSQL instance. Never
restore over production for a drill. Supply `BACKUP_FILE` and override the
entrypoint:

```powershell
docker compose --profile operations run --rm `
  -e BACKUP_FILE=/backups/universalai-YYYYMMDDTHHMMSSZ.dump.gpg `
  --entrypoint /usr/local/bin/restore-messenger backup
```

Record checksum verification, start/end time, recovered row counts, service
smoke tests, operator and approver. A backup is not accepted until restoration
has been demonstrated.

## Key rotation and revocation

1. Generate a new Ed25519 pair and a new key ID.
2. Deploy the new public key to Messenger, then the matching private key to the
   platform during a controlled maintenance window.
3. Log out/revoke active Messenger sessions and verify old tokens fail.
4. Remove old key material after the maximum token TTL plus clock skew.

On suspected account compromise: disable the platform user, increment
`users.messenger_token_version`, terminate active connections, preserve audit
evidence and follow the incident-response process.

## Controlled pilot

- Use a dedicated small AD group and named support/security contacts.
- Public registration, bots, RAG, calls and external access remain
  disabled.
- Local attachments are allowed only for unrestricted MVP data after ClamAV
  quarantine/scan. Restricted information must still never be uploaded.
- Show and acknowledge the acceptable-use warning: restricted information must
  not be sent.
- Run authorization, XSS, reconnect, rate-limit, backup/restore and incident
  tabletop checks before inviting users.
- Review access and audit events after the first day and weekly during pilot.
- Stop the pilot on a high/critical finding, unexplained data access, lost
  committed message, failed restore or missing security telemetry.

Expansion requires documented remediation, an independent penetration test and
formal approval by the organization's information-security/compliance owner.
