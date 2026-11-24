# Messenger security model

## Scope and data classification

The first production-capable release processes:

- employee identity data obtained from the platform account;
- private and group plain-text messages;
- locally stored, malware-scanned attachments for unrestricted MVP data only;
- chat membership, delivery and read-state metadata;
- security audit events.

It must not process restricted, state-secret, commercial-secret, payment, medical,
biometric or other specially regulated information. Attachments are allowed only
for unrestricted MVP data and are not approval to upload classified or regulated
content. Voice/video, external federation, public registration, bots and RAG are
disabled until their own threat model and acceptance gate are approved.

End-to-end encryption is outside the MVP. Confidentiality is provided by TLS/WSS,
encrypted host volumes and backups, least-privilege access, authorization on every
operation, and audited administration. This decision must be reviewed before the
data classification changes.

## Trust boundaries

1. The browser and all browser-provided identifiers are untrusted.
2. The Flask platform authenticates AD/local users and is the identity authority.
3. The platform issues short-lived, Messenger-audience tokens. The Messenger
   service receives only the public verification key, never the Flask session key.
4. The Messenger API authorizes every REST and WebSocket operation from the
   verified subject and current membership. Sender IDs, roles and timestamps sent
   by a client are ignored.
5. PostgreSQL is the durable source of truth. Redis contains only ephemeral
   presence, rate-limit and fan-out state.
6. Only the reverse proxy is externally reachable. Databases and application
   container ports stay on internal Docker networks.

## Roles and permissions

| Action | user | chat_admin | security_auditor | system_admin |
| --- | --- | --- | --- | --- |
| Read/write own chats | yes | yes | no | no by default |
| Create direct/group chat | yes | yes | no | no by default |
| Manage a group they own | yes | yes | no | no by default |
| Moderate any group | no | yes, audited | no | no |
| Read security audit metadata | no | no | yes | yes |
| Read message content without membership | no | no | no | no |
| Disable users/revoke sessions | no | no | no | yes, audited |
| Change service configuration | no | no | no | yes, audited |

Administrative roles do not implicitly grant access to message content.
Exceptional access requires a separately approved, time-bounded procedure and is
not implemented in the MVP.

## Retention defaults

- Messages and tombstones: 365 days, configurable by an authorized operator.
- Security audit metadata: 365 days minimum.
- Revoked token identifiers: token expiration plus clock-skew allowance.
- Presence and rate-limit state: minutes, never backed up.
- Application logs: 30 days; message text, token values and credentials forbidden.
- Encrypted backups: 30 days with a quarterly restore exercise.

Legal and information-security owners must approve production values. Deletion
jobs must preserve required audit metadata while removing message content after
the approved retention period.

## Threats and required controls

| Threat | Required controls | Verification |
| --- | --- | --- |
| Session theft | secure cookies, short-lived audience JWT, token only in memory, logout revocation | expired/revoked token tests |
| IDOR/BOLA | membership check on every chat/message operation | cross-user negative tests |
| WebSocket impersonation | first-frame authentication, origin allowlist, strict event schemas | malformed/origin/replay tests |
| Stored/DOM XSS | plain-text messages, `textContent`, CSP without unsafe inline script | XSS payload corpus |
| Message duplication/loss | idempotency key, server sequence, DB commit before fan-out, reconnect sync | retry/restart tests |
| Brute force/spam | Redis limits by user, IP and chat; size and connection limits | rate-limit tests and alerts |
| Log/database disclosure | no content in security logs, least-privilege DB role, encrypted storage/backups | secret/content scan |
| Privileged misuse | separated roles, append-only audit, no implicit content access | role matrix tests |
| Dependency compromise | pinned dependencies, SBOM, SAST and container/dependency scan | CI security gate |
| Service outage | health probes, bounded queues, DB-based resync, backup restore drill | failure and restore tests |
| Bot/RAG data exfiltration | feature disabled until scoped service identities and per-chat ACL filtering exist | route/UI absence in MVP |

## Security audit events

Audit successful and failed authentication, token issuance/revocation, connection
establishment, chat creation, membership/role changes, moderation, account
disablement and administrative configuration changes. Record actor, action,
target identifier, result, timestamp, request/correlation ID and source address.
Never record message text, passwords, access tokens or LDAP bind credentials.

Audit records are append-only for the application database role. Export to the
organization's protected logging/SIEM system is an operational requirement.

## Acceptance gates

Pilot is blocked until:

- all REST/WebSocket authorization matrix tests pass;
- no open critical/high security findings remain;
- TLS, origin policy, CSP and rate limits are enabled in the pilot environment;
- secret, dependency, container and SAST scans pass;
- encrypted backup and restore are demonstrated;
- incident response, access administration, retention and acceptable-use
  procedures have named owners;
- users are explicitly informed that restricted information is prohibited;
- the Belarus requirements matrix has evidence or an approved compensating
  control for every applicable requirement.

Production expansion additionally requires an independent penetration test and
the organization's formal information-security/compliance approval. Those are
external controls and cannot be completed by source-code changes alone.
