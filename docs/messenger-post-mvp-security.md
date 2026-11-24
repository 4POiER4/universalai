# Messenger post-MVP security gates

No feature below may reuse the text-message acceptance result. Each requires its
own threat model, tests and security approval.

## Attachments

The local attachment implementation is enabled only for the unrestricted-data
MVP. It does not authorize restricted, regulated, classified, medical, payment,
biometric or commercial-secret content; broader use still requires this gate.

- Store objects outside the application filesystem under opaque IDs.
- Quarantine before availability; verify size, extension, MIME and magic bytes.
- Apply antivirus and, where required, content disarm/reconstruction and DLP.
- Use short-lived, audience-bound signed download URLs after membership checks.
- Set `Content-Disposition: attachment`, safe names and `nosniff`.
- Encrypt storage/backups, define retention and audit upload/download/delete.
- Gate: traversal, polyglot, archive-bomb, malware and unauthorized-download
  suites pass; quarantine cannot be bypassed.

## Bots and RAG

- Give each bot a disabled-by-default service identity with narrow scopes.
- A chat owner explicitly adds/removes a bot; users see when content is sent to it.
- Enforce chat membership and document ACLs on every retrieval, not only at index
  time. Keep tenants/security domains in separate indexes where required.
- Treat all messages/documents as untrusted prompt input; tools use allowlists,
  schemas, timeouts, quotas and human confirmation for consequential actions.
- Never place platform, database or infrastructure credentials in model context.
- Audit bot reads, retrieval sources, tool calls and writes without logging
  unrelated message content.
- Gate: prompt-injection, cross-chat retrieval, confused-deputy, data-exfiltration
  and tool-abuse tests pass.

## Channels and search

- Separate publisher/moderator/subscriber permissions.
- Search results apply membership and retention filters before returning snippets.
- Index deletion follows message tombstones and legal retention.
- Gate: cross-chat search and stale-index deletion tests pass.

## Push notifications

- Minimize lock-screen content by default.
- Device registrations are per-user, revocable and never trusted as identity.
- Push payloads contain opaque references rather than message text where possible.
- Gate: revoked/lost device receives no new notification content.

## Voice and video

Both 1:1 and group calls share one media path: a self-hosted LiveKit SFU.
The custom coturn-backed mesh WebRTC path for 1:1 calls has been retired —
there is now a single call-media implementation to review instead of two.

- Media transport (DTLS-SRTP, ICE) is LiveKit's reviewed implementation, not
  bespoke code; this repo's own surface is limited to token minting and the
  ringing/accept/decline/busy control plane.
- Access tokens are minted server-side only, scoped to a single call's
  private room (`call-{call_id}` for 1:1), short-lived, and only issued to
  the call's caller/callee after the call reaches `accepted` status.
- Corporate NAT/firewall traversal: the current LiveKit deployment does not
  enable an embedded TURN/TLS relay (`turn:` block in `livekit.yaml`) or
  point at an external TURN server. On networks that block direct UDP —
  common on restrictive corporate networks, which is the stated priority
  use case — call media may fail to establish even though signaling and
  ringing work. This must be configured (LiveKit's own TURN, typically on
  443/TLS for firewall-friendliness) before treating corporate calls as
  reliably deployable, and validated from inside a real restrictive network,
  not just localhost.
- Define recording consent, retention and access separately if/when
  recording is implemented; it is not implemented today.
- Gate: signaling authorization, token-scope/expiry, LiveKit TURN reachability
  from a restrictive network, metadata exposure and recording consent (once
  applicable) tests pass.

## External federation or desktop packaging

- Federation requires explicit trust domains, server identity, abuse handling,
  data-location review and protocol-level authorization.
- Desktop clients require signed builds/updates, secure key storage, hardened
  webviews, disabled arbitrary navigation and device revocation.
- Gate: supply-chain signing, update rollback and compromised-device scenarios
  are independently assessed.
