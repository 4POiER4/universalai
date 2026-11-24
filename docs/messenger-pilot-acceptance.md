# Messenger pilot acceptance record

## Engineering evidence completed locally

- [x] Platform and Messenger unit suites pass (35 + 17 tests; messenger grew
  from 7 with the calls-migration test additions below).
- [x] Messenger service dependencies have no known published vulnerability —
  re-verified via `pip-audit` on 2026-08-16: `python-multipart==0.0.22` had 5
  published advisories (PYSEC-2026-3036/3037/3038/3039/3040), fixed by
  bumping to `0.0.32` in `services/messenger/requirements.txt`; audit is
  clean after the bump and the messenger test suite still passes on it.
- [x] Platform dependencies have no known published vulnerability
  (`pip-audit` on `requirements.txt`, 2026-08-16).
- [ ] Bandit medium/high gate does **not** currently pass cleanly: 6 B608
  ("possible SQL injection") findings in `repository.py`'s group-call
  session queries (`create_or_get_active_session`/`end_session`/
  `list_active_sessions`/`list_call_sessions_history`/
  `find_active_session_by_room`/`close_session_by_room`), all from
  interpolating the hardcoded `_SESSION_FIELDS` column-list constant into an
  f-string SQL template — bandit can't statically tell that constant apart
  from user input, and every actual user-supplied value in those queries is
  parameterized (`%s`), so this reads as a false positive, not a real
  injection. It predates this change (not introduced by the calls work) and
  contradicts the checkbox this line used to carry — corrected here rather
  than left claiming a clean pass. Confirm with your security owner and
  either add a reviewed `#nosec` with justification or restructure the
  query, then re-check this box.
- [ ] Messenger container high/critical CVE scan (`docker scout`) not run
  this pass — Docker Desktop's engine was not running locally; only the
  Python-level checks above (`pip-audit`, `bandit`, unit tests) were
  performed on this machine.
- [x] PostgreSQL schema and least-privilege grants verified.
- [x] Unauthorized REST request returns 401.
- [x] Cross-chat outsider access returns 404.
- [x] WebSocket rejects untrusted Origin and commands before authentication.
- [x] Stored XSS payload remains exact plain text and the frontend has no dynamic
  `innerHTML` sink.
- [x] Idempotent retry returns the same message.
- [x] Twenty concurrent sends receive unique ordered sequences.
- [x] Authenticated browser reaches a ready WebSocket connection.
- [x] Encrypted backup produced with checksum.
- [x] Backup restored into an isolated empty database; `users` and
  `messenger.messages` verified.
- [x] Flask, Messenger API, gateway, PostgreSQL and Redis healthy in local Compose.

## Voice/video: engineering evidence completed locally

Scope: 1:1 calls (`calls.py`) and group calls (`rooms.py`) now share one
media path — a self-hosted LiveKit SFU. The previous bespoke coturn-backed
mesh-WebRTC implementation for 1:1 calls has been retired: `calls.py` keeps
only the ringing/accept/decline/busy control plane (phone-call semantics),
and once a call is `accepted`, both sides mint a scoped LiveKit token
(`mint_direct_call_token`) and join a private room (`call-{call_id}`) the
same way group-call participants join `call_sessions` rooms — including
screen sharing, which is a LiveKit feature (`setScreenShareEnabled`) rather
than custom track-renegotiation code. This section does **not** by itself
satisfy the "Voice and video" gate in `messenger-post-mvp-security.md` — it
records what has been verified so far toward that gate.

- [x] Call-signaling authorization: a non-participant cannot inject a
  signal into another pair's `call_id` (IDOR check).
- [x] Self-call invite is rejected server-side.
- [x] Invite is rejected when the caller/callee do not share a resolvable
  direct chat (cannot ring an arbitrary user by guessing a `chat_id`).
- [x] Unknown/expired `call_id` returns `not_found` rather than leaking state.
- [x] `WebSocketCommand` strictly validates call payloads: missing `call_id`,
  unsupported `payload.type` (the protocol now only accepts
  invite/accept/decline/cancel/hangup — no client-driven SDP/ICE relay at
  all), `invite` without `to`, and spoofed extra fields (e.g. `sender_id`)
  are all rejected before reaching business logic.
- [x] The direct-call media token endpoint (`POST /calls/{call_id}/token`)
  404s for a call that doesn't exist or doesn't involve the caller, 409s if
  the call hasn't reached `accepted` yet, and — when it does mint — issues a
  token scoped to exactly that call's private room (verified by decoding the
  JWT and checking `video.room`/`video.roomJoin`, not just that a token was
  returned).
- [x] `node --check` passes on `static/messenger/js/client.js`.
- [x] Retired the now-unused coturn service, `MESSENGER_TURN_*` settings and
  the `/turn-credentials` endpoint (docker-compose, `.env.example`,
  `config.py`) — one fewer container and required secret to provision for a
  "boxed" deployment, and one fewer bespoke crypto path to audit.

## Voice/video: not yet verified — do not treat as gate evidence

- [ ] **Corporate NAT/firewall traversal is not configured.** LiveKit's own
  embedded TURN/TLS relay is not enabled in `livekit.yaml`, and nothing else
  provides one now that coturn is gone. On networks that block direct
  UDP — plausible for the stated "corporate calls" priority — call media may
  fail to establish even though ringing/signaling still works. This needs
  LiveKit's TURN configured (typically fronted on 443 for firewall
  friendliness) and validated from an actual restrictive network before
  corporate calls can be called reliably deployable.
- [ ] DTLS-SRTP configuration has not been independently reviewed (relies on
  LiveKit/browser defaults; the gate requires this be a reviewed decision).
- [ ] Not exercised against two real browsers/networks: 1:1 call setup,
  screen share, mic/camera toggling, and reconnect behavior on room drop —
  only reviewed by code inspection and unit-tested at the token/authorization
  layer above.
- [ ] Metadata-exposure and recording-consent tests: not applicable yet —
  call recording is not implemented. If corporate calls require recording
  for compliance, that is a separate feature with its own consent/retention
  design, not covered here.
- [ ] Local run of the full automated test suite is currently unreliable in
  a plain `venv` on this machine: `test_websocket_authenticates_before_accepting_commands`
  hangs (a pre-existing test unrelated to calls — likely a Starlette
  TestClient/anyio version mismatch on this Python install, since the
  project's own baseline records this suite passing). Verify test results
  against the project's pinned Docker dev environment, not an ad hoc venv.
- [ ] Pre-existing, unrelated finding: `test_frontend_has_no_dynamic_html_sink_or_mock_seed`
  currently fails because `client.js:3107,3113` set `.innerHTML` on the
  forward/delete selection-action labels. This predates the calls work in
  this section and should be tracked separately against the project's own
  "no dynamic `innerHTML` sink" invariant.
- [ ] `MESSENGER_GROUP_CALL_TOKEN_TTL_SECONDS`/`mint_group_call_token` now
  also name 1:1 call tokens — functionally fine (identical grant shape) but
  the "group" naming is a minor accuracy nit worth a rename later.

## External acceptance still required

- [ ] Production AD pilot group and named users approved by the directory owner.
- [ ] Production TLS certificate, firewall/VPN, host encryption, EDR, NTP and SIEM
  controls verified by infrastructure/security operations.
- [ ] Retention and acceptable-use policy approved by legal/data/security owners.
- [ ] Incident-response tabletop completed with named responders.
- [ ] Independent authenticated penetration test completed and findings closed.
- [ ] Exact clauses in the approved Belarus requirements document mapped and
  accepted by the responsible compliance authority.
- [ ] Formal production acceptance signed.

The second section cannot be truthfully completed by source code or a local
development environment. Production rollout remains blocked until all items are
signed by their organizational owners.
