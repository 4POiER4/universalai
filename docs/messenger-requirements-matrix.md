# Messenger security requirements matrix

This matrix is the engineering evidence index for the Belarus security
requirements. The information-security owner must map each row to the exact
clause in the approved source document before production acceptance.

| Control area | MVP implementation/evidence | Owner | Gate |
| --- | --- | --- | --- |
| Data classification | `docs/messenger-security-model.md`; restricted information prohibited | Information security | policy approved |
| Identification/authentication | AD/local login in platform; short-lived Messenger JWT | Platform engineering | auth tests pass |
| Authorization | server-side chat membership and role checks on REST and WebSocket | Messenger engineering | IDOR suite passes |
| Session protection | Secure/HttpOnly/SameSite platform cookie; audience token in memory; revocation | Platform engineering | replay/logout tests pass |
| Transport protection | HTTPS/WSS at reverse proxy, TLS 1.2+, HSTS in production | Infrastructure | TLS scan passes |
| Stored-data protection | encrypted host volume and encrypted backups; least-privilege DB role | Infrastructure/DBA | configuration reviewed |
| Input/content safety | strict schemas, plain-text messages, size limits, CSP | Messenger engineering | XSS/malformed suite passes |
| Event registration | append-only security audit metadata; no message content in logs | Security operations | SIEM ingestion verified |
| Availability | health probes, resource limits, PostgreSQL resync, Redis degradation rules | Infrastructure | failure tests pass |
| Backup/recovery | encrypted scheduled backups and documented restore drill | DBA | restore demonstrated |
| Time synchronization | host/container timestamps synchronized through approved NTP | Infrastructure | drift alert verified |
| Malware protection | local attachments with ClamAV quarantine/scan before download; executable types denied | Information security + Messenger engineering | upload/download authz and scan suite passes |
| Network protection | only reverse proxy published; DB/Redis on internal network; firewall/VPN policy | Infrastructure | exposure scan passes |
| Vulnerability management | pinned dependencies, SBOM, SAST, dependency/container/secret scans | DevSecOps | no high/critical finding |
| Administrative access | separated admin/auditor roles; privileged actions audited | System owner | role review complete |
| Incident response | runbook, contacts, evidence preservation and revocation procedure | Security operations | tabletop completed |
| Retention/deletion | configured retention, deletion job and protected audit metadata | Data owner/DBA | legal approval and test |
| User awareness | acceptable-use notice prohibits restricted information | System owner | pilot users acknowledged |
| Independent assessment | penetration test and required conformity/attestation activities | Compliance | formal acceptance |

## Evidence package

For every pilot/release, retain:

1. reviewed threat model and role matrix;
2. CI test and security-scan reports with immutable build identifier;
3. deployed configuration export with secrets redacted;
4. TLS/network exposure report;
5. access review and key-rotation record;
6. backup/restore report;
7. incident-response exercise report;
8. vulnerability exceptions and compensating-control approvals;
9. penetration-test closure report;
10. signed production acceptance.

Code completion alone does not prove compliance. Network controls, host
encryption, antivirus/EDR, SIEM, NTP, backup media protection, personnel
procedures, certification and formal acceptance must be supplied by the
organization responsible for the production environment.
