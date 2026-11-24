import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect


SERVICE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = SERVICE_ROOT.parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

from messenger.auth import decode_token  # noqa: E402
from messenger.auth import Identity  # noqa: E402
from messenger.calls import handle_call_signal, mint_direct_call_token  # noqa: E402
from messenger.config import get_settings  # noqa: E402
from messenger.main import app  # noqa: E402
from messenger import presence  # noqa: E402
from messenger.presence import merge_status  # noqa: E402
from messenger.repository import (  # noqa: E402
    _ADMIN_CONFIG_EDITABLE,
    require_admin,
    require_membership,
    revoke_all_sessions,
    revoke_user_sessions,
    set_user_active,
    set_user_auditor,
    set_user_muted,
    update_admin_config,
)
from messenger.rooms import start_or_join_call  # noqa: E402
from messenger.attachments import safe_original_name, validate_file  # noqa: E402
from messenger.schemas import (  # noqa: E402
    SendMessage,
    UpdateAdminConfig,
    UpdatePresenceStatus,
    WebSocketCommand,
)


class FakeRedis:
    """Minimal in-memory stand-in covering exactly the redis-py methods
    presence.py uses, so the sticky-away hysteresis can be exercised without
    a live Redis."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    async def hdel(self, key, *fields):
        for field in fields:
            self.hashes.get(key, {}).pop(field, None)

    async def expire(self, _key, _seconds):
        return True

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, ex=None):  # noqa: ARG002 - ex accepted for API parity
        self.kv[key] = str(value)

    async def delete(self, key):
        self.kv.pop(key, None)

    async def incr(self, key):
        self.kv[key] = str(int(self.kv.get(key, 0)) + 1)
        return int(self.kv[key])


class EmptyCursor:
    async def fetchone(self):
        return None


class EmptyConnection:
    async def execute(self, *_args, **_kwargs):
        return EmptyCursor()


class MessengerSecurityTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        key = Ed25519PrivateKey.generate()
        cls.private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        cls.public_pem = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()

    def tearDown(self):
        get_settings.cache_clear()

    def token(self, **overrides):
        now = int(time.time())
        claims = {
            "sub": "alice",
            "name": "Alice",
            "roles": ["user"],
            "iss": "universalai",
            "aud": "messenger",
            "iat": now,
            "exp": now + 300,
            "jti": str(uuid4()),
            "ver": 2,
        }
        claims.update(overrides)
        return jwt.encode(claims, self.private_pem, algorithm="EdDSA")

    def test_token_is_audience_bound(self):
        with patch.dict(
            os.environ,
            {"MESSENGER_JWT_PUBLIC_KEY": self.public_pem},
            clear=False,
        ):
            get_settings.cache_clear()
            identity = decode_token(self.token())
            self.assertEqual(identity.username, "alice")
            with self.assertRaises(jwt.InvalidTokenError):
                decode_token(self.token(aud="another-service"))

    def test_strict_event_schema_rejects_spoofed_fields(self):
        with self.assertRaises(ValidationError):
            WebSocketCommand.model_validate(
                {"type": "subscribe", "chat_id": str(uuid4()), "sender_id": "admin"}
            )

    def test_message_rejects_empty_and_oversized_content(self):
        with self.assertRaises(ValidationError):
            SendMessage(text=" ", client_id=uuid4())
        with self.assertRaises(ValidationError):
            SendMessage(text="x" * 8001, client_id=uuid4())
        self.assertEqual(
            SendMessage(text="", client_id=uuid4(), forward_message_id=uuid4()).text,
            "",
        )

    def test_attachment_names_cannot_traverse_storage(self):
        self.assertEqual(safe_original_name("../../windows/system32/cmd.exe"), "cmd.exe")
        self.assertEqual(safe_original_name("..\\..\\evil.png"), "evil.png")
        with self.assertRaises(HTTPException) as raised:
            validate_file("payload.exe", "application/octet-stream", b"MZ")
        self.assertEqual(raised.exception.status_code, 415)

    def test_inline_images_require_matching_signature(self):
        self.assertTrue(validate_file("safe.png", "image/png", b"\x89PNG\r\n\x1a\nrest"))
        with self.assertRaises(HTTPException):
            validate_file("fake.png", "image/png", b"<script>")

    def test_typing_event_is_strict_and_ephemeral(self):
        chat_id = uuid4()
        command = WebSocketCommand(type="typing", chat_id=chat_id, active=True)
        self.assertEqual(command.chat_id, chat_id)
        with self.assertRaises(ValidationError):
            WebSocketCommand(type="typing", chat_id=chat_id)

    async def test_missing_membership_is_hidden_as_not_found(self):
        with self.assertRaises(HTTPException) as raised:
            await require_membership(EmptyConnection(), uuid4(), "alice")
        self.assertEqual(raised.exception.status_code, 404)

    def test_frontend_has_no_dynamic_html_sink_or_mock_seed(self):
        client_source = (
            WORKSPACE_ROOT / "static" / "messenger" / "js" / "client.js"
        ).read_text(encoding="utf-8")
        template = (
            WORKSPACE_ROOT / "templates" / "messenger.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", client_source)
        self.assertNotIn("data.js", template)
        self.assertNotIn("attachBtn", template)
        self.assertNotIn("hCall", template)

    def test_websocket_rejects_untrusted_origin(self):
        client = TestClient(app)
        with self.assertRaises(WebSocketDisconnect) as raised:
            with client.websocket_connect(
                "/api/messenger/v1/ws",
                headers={"origin": "https://evil.example", "host": "localhost"},
            ):
                pass
        self.assertEqual(raised.exception.code, 1008)

    def test_websocket_authenticates_before_accepting_commands(self):
        identity = Identity("alice", "Alice", ("user",), 1, "token-id")
        client = TestClient(app)
        with (
            patch("messenger.main.websocket_identity", new=AsyncMock(return_value=identity)),
            patch("messenger.main.acquire_connection_slot", new=AsyncMock(return_value="slot")),
            patch("messenger.main.sync_profile", new=AsyncMock()),
            patch("messenger.main.record_security_event", new=AsyncMock()),
            patch("messenger.main.release_connection_slot", new=AsyncMock()),
            patch("messenger.main.open_connection", new=AsyncMock()),
            patch("messenger.main.close_connection", new=AsyncMock()),
            patch("messenger.main.forward_presence", new=AsyncMock()),
            patch("messenger.main.forward_signal", new=AsyncMock()),
        ):
            with client.websocket_connect(
                "/api/messenger/v1/ws",
                headers={"origin": "http://localhost:5000", "host": "localhost"},
            ) as websocket:
                websocket.send_json({"type": "auth", "token": "opaque-token"})
                self.assertEqual(websocket.receive_json()["type"], "ready")
                websocket.send_json(
                    {
                        "type": "subscribe",
                        "chat_id": str(uuid4()),
                        "sender_id": "admin",
                    }
                )
                with self.assertRaises(WebSocketDisconnect) as raised:
                    websocket.receive_json()
                self.assertEqual(raised.exception.code, 1008)

    def test_call_schema_rejects_missing_or_unsupported_fields(self):
        chat_id = uuid4()
        call_id = uuid4()
        with self.assertRaises(ValidationError):
            WebSocketCommand(type="call", payload={"type": "offer", "sdp": {}})
        with self.assertRaises(ValidationError):
            WebSocketCommand(type="call", call_id=call_id, payload={"type": "teleport"})
        with self.assertRaises(ValidationError):
            WebSocketCommand(
                type="call", call_id=call_id, payload={"type": "invite", "chat_id": str(chat_id)},
            )
        with self.assertRaises(ValidationError):
            WebSocketCommand(
                type="call", call_id=call_id, payload={"type": "offer", "sdp": {}}, sender_id="admin",
            )
        command = WebSocketCommand(
            type="call", call_id=call_id, to="Bob", payload={"type": "invite", "media": {"video": True}},
        )
        self.assertEqual(command.to, "bob")

    async def test_call_signal_rejects_self_invite(self):
        identity = Identity("alice", "Alice", ("user",), 1, "jti")
        command = WebSocketCommand(
            type="call", call_id=uuid4(), to="alice",
            payload={"type": "invite", "media": {"video": False}},
        )
        sent = []

        async def fake_send(_ws, _lock, message):
            sent.append(message)

        await handle_call_signal(None, None, identity, command, {}, fake_send)
        self.assertEqual(sent[-1]["payload"]["code"], "self_call")

    async def test_call_signal_rejects_invite_outside_shared_direct_chat(self):
        identity = Identity("alice", "Alice", ("user",), 1, "jti")
        command = WebSocketCommand(
            type="call", call_id=uuid4(), to="bob",
            payload={"type": "invite", "media": {"video": False}, "chat_id": str(uuid4())},
        )
        sent = []

        async def fake_send(_ws, _lock, message):
            sent.append(message)

        with patch("messenger.calls.direct_chat_for_pair", new=AsyncMock(return_value=None)):
            await handle_call_signal(None, None, identity, command, {}, fake_send)
        self.assertEqual(sent[-1]["payload"]["code"], "not_direct_contact")

    async def test_call_signal_rejects_signal_from_non_participant(self):
        """An attacker who guesses/observes another pair's call_id must not be
        able to inject SDP/ICE into it or learn its state (IDOR on calls)."""
        identity = Identity("mallory", "Mallory", ("user",), 1, "jti")
        call_id = uuid4()
        command = WebSocketCommand(type="call", call_id=call_id, payload={"type": "hangup"})
        sent = []

        async def fake_send(_ws, _lock, message):
            sent.append(message)

        with patch(
            "messenger.calls.call_state_get",
            new=AsyncMock(return_value={
                "caller": "alice", "callee": "bob", "chat_id": str(uuid4()), "status": "accepted",
            }),
        ):
            await handle_call_signal(None, None, identity, command, {}, fake_send)
        self.assertEqual(sent[-1]["payload"]["code"], "not_participant")

    async def test_call_signal_reports_not_found_for_unknown_call(self):
        identity = Identity("alice", "Alice", ("user",), 1, "jti")
        command = WebSocketCommand(type="call", call_id=uuid4(), payload={"type": "hangup"})
        sent = []

        async def fake_send(_ws, _lock, message):
            sent.append(message)

        with patch("messenger.calls.call_state_get", new=AsyncMock(return_value=None)):
            await handle_call_signal(None, None, identity, command, {}, fake_send)
        self.assertEqual(sent[-1]["payload"]["code"], "not_found")

    async def test_direct_call_token_requires_call_to_exist_and_be_accepted(self):
        identity = Identity("alice", "Alice", ("user",), 1, "jti")

        with patch("messenger.calls.get_call", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as raised:
                await mint_direct_call_token(identity, uuid4())
            self.assertEqual(raised.exception.status_code, 404)

        # A call that exists but doesn't involve this user must 404, not 403 —
        # same not-found-not-forbidden pattern used for chat membership, so an
        # attacker can't distinguish "not yours" from "doesn't exist".
        with patch(
            "messenger.calls.get_call",
            new=AsyncMock(return_value={
                "caller_username": "bob", "callee_username": "carol", "status": "accepted",
            }),
        ):
            with self.assertRaises(HTTPException) as raised:
                await mint_direct_call_token(identity, uuid4())
            self.assertEqual(raised.exception.status_code, 404)

        # Still ringing (not yet accepted by both sides): no media token yet.
        with patch(
            "messenger.calls.get_call",
            new=AsyncMock(return_value={
                "caller_username": "alice", "callee_username": "bob", "status": "ringing",
            }),
        ):
            with self.assertRaises(HTTPException) as raised:
                await mint_direct_call_token(identity, uuid4())
            self.assertEqual(raised.exception.status_code, 409)

    async def test_direct_call_token_is_scoped_to_a_private_room_for_this_call(self):
        identity = Identity("alice", "Alice", ("user",), 1, "jti")
        call_id = uuid4()
        with patch.dict(
            os.environ,
            {
                "MESSENGER_LIVEKIT_URL": "wss://livekit.internal",
                "MESSENGER_LIVEKIT_API_KEY": "test-key",
                "MESSENGER_LIVEKIT_API_SECRET": "test-secret-value-long-enough",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            with (
                patch(
                    "messenger.calls.get_call",
                    new=AsyncMock(return_value={
                        "caller_username": "alice", "callee_username": "bob", "status": "accepted",
                    }),
                ),
                patch("messenger.calls.calls_globally_enabled", new=AsyncMock(return_value=True)),
                patch(
                    "messenger.calls.users_calls_allowed",
                    new=AsyncMock(return_value={"alice": True, "bob": True}),
                ),
            ):
                result = await mint_direct_call_token(identity, call_id)
        get_settings.cache_clear()
        self.assertEqual(result["room_name"], f"call-{call_id}")
        self.assertEqual(result["livekit_url"], "wss://livekit.internal")
        claims = jwt.decode(
            result["access_token"], "test-secret-value-long-enough", algorithms=["HS256"],
        )
        self.assertEqual(claims["sub"], "alice")
        self.assertEqual(claims["video"]["room"], f"call-{call_id}")
        self.assertTrue(claims["video"]["roomJoin"])

    def test_update_presence_status_rejects_dnd_and_manual_away_together(self):
        UpdatePresenceStatus(dnd=True, manual_away=False)
        UpdatePresenceStatus(dnd=False, manual_away=True)
        with self.assertRaises(ValidationError):
            UpdatePresenceStatus(dnd=True, manual_away=True)

    def test_merge_status_no_override_passes_derived_through(self):
        status, text, until, manual = merge_status("online", None)
        self.assertEqual(status, "online")
        self.assertIsNone(text)
        self.assertIsNone(until)
        self.assertIsNone(manual)

    def test_merge_status_dnd_overrides_derived_status(self):
        # dnd forces "dnd" even while the user is actively typing (derived
        # would say "online") — that's the point of a manual override.
        status, _text, _until, manual = merge_status(
            "online", {"dnd": True, "manual_away": False, "status_text": None, "status_expires_at": None},
        )
        self.assertEqual(status, "dnd")
        self.assertEqual(manual, "dnd")

    def test_merge_status_manual_away_sticks_regardless_of_activity(self):
        # This is the whole point of manual away vs. derived away: the user
        # is actively at the keyboard (derived="online") but chose to show
        # as away, and it must not silently flip back on the next keypress.
        status, _text, _until, manual = merge_status(
            "online", {"dnd": False, "manual_away": True, "status_text": None, "status_expires_at": None},
        )
        self.assertEqual(status, "away")
        self.assertEqual(manual, "away")

    def test_merge_status_dnd_takes_priority_over_manual_away(self):
        # Both should never be true at once (schema + DB constraint both
        # reject it) but merge_status must still resolve deterministically
        # if it ever happens.
        status, _text, _until, manual = merge_status(
            "offline", {"dnd": True, "manual_away": True, "status_text": None, "status_expires_at": None},
        )
        self.assertEqual(status, "dnd")
        self.assertEqual(manual, "dnd")

    def test_merge_status_text_only_keeps_derived_status(self):
        # A status message with neither dnd nor manual_away set must not
        # force any particular availability — only DND/away are exclusive.
        status, text, _until, manual = merge_status(
            "away", {"dnd": False, "manual_away": False, "status_text": "на встрече", "status_expires_at": None},
        )
        self.assertEqual(status, "away")
        self.assertEqual(text, "на встрече")
        self.assertIsNone(manual)


class StickyAwayTests(unittest.IsolatedAsyncioTestCase):
    """Auto-away must stick once triggered: a single stray heartbeat back to
    "active" should not instantly clear it, only sustained activity should."""

    async def asyncSetUp(self):
        self.redis = FakeRedis()
        patchers = [
            patch("messenger.presence.client", MagicMock(return_value=self.redis)),
            patch("messenger.presence.publish_presence", new=AsyncMock()),
            patch("messenger.presence.get_manual_overrides", new=AsyncMock(return_value={})),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_idle_heartbeat_sets_sticky_away(self):
        await presence.heartbeat("alice", "Alice", "c1", "active")
        self.assertEqual(await presence.compute_status("alice"), "online")
        await presence.heartbeat("alice", "Alice", "c1", "idle")
        self.assertEqual(await presence.compute_status("alice"), "away")

    async def test_single_active_heartbeat_does_not_clear_sticky_away(self):
        await presence.heartbeat("alice", "Alice", "c1", "idle")
        self.assertEqual(await presence.compute_status("alice"), "away")
        await presence.heartbeat("alice", "Alice", "c1", "active")
        self.assertEqual(
            await presence.compute_status("alice"), "away",
            "one stray active heartbeat must not instantly clear sticky away",
        )

    async def test_two_consecutive_active_heartbeats_clear_sticky_away(self):
        await presence.heartbeat("alice", "Alice", "c1", "idle")
        await presence.heartbeat("alice", "Alice", "c1", "active")
        await presence.heartbeat("alice", "Alice", "c1", "active")
        self.assertEqual(await presence.compute_status("alice"), "online")

    async def test_interrupted_return_resets_confirmation_progress(self):
        await presence.heartbeat("alice", "Alice", "c1", "idle")
        await presence.heartbeat("alice", "Alice", "c1", "active")  # 1st confirmation
        await presence.heartbeat("alice", "Alice", "c1", "idle")    # interrupted — resets
        await presence.heartbeat("alice", "Alice", "c1", "active")  # would be 2nd if not reset
        self.assertEqual(
            await presence.compute_status("alice"), "away",
            "an idle heartbeat in between must reset the return-confirmation count",
        )

    async def test_going_offline_clears_sticky_away(self):
        await presence.heartbeat("alice", "Alice", "c1", "idle")
        self.assertEqual(await presence.compute_status("alice"), "away")
        await presence.close_connection("alice", "Alice", "c1")
        self.assertEqual(await presence.compute_status("alice"), "offline")
        # Reconnecting fresh must not inherit the stale sticky-away flag.
        await presence.open_connection("alice", "Alice", "c2")
        self.assertEqual(await presence.compute_status("alice"), "online")

    async def test_other_users_unaffected(self):
        await presence.heartbeat("alice", "Alice", "c1", "idle")
        await presence.heartbeat("bob", "Bob", "c1", "active")
        self.assertEqual(await presence.compute_status("alice"), "away")
        self.assertEqual(await presence.compute_status("bob"), "online")


class AdminControlsTests(unittest.IsolatedAsyncioTestCase):
    def test_require_admin_rejects_non_admin(self):
        with self.assertRaises(HTTPException) as raised:
            require_admin(Identity("alice", "Alice", ("user",), 1, "jti"))
        self.assertEqual(raised.exception.status_code, 403)

    def test_require_admin_allows_system_admin(self):
        require_admin(Identity("alice", "Alice", ("user", "system_admin"), 1, "jti"))

    def test_update_admin_config_rejects_out_of_range_attachment_size(self):
        UpdateAdminConfig(calls_enabled=True, max_attachment_bytes=None)
        UpdateAdminConfig(calls_enabled=True, max_attachment_bytes=1024)
        with self.assertRaises(ValidationError):
            UpdateAdminConfig(calls_enabled=True, max_attachment_bytes=100)  # below 1024 floor
        with self.assertRaises(ValidationError):
            UpdateAdminConfig(calls_enabled=True, max_attachment_bytes=600 * 1024 * 1024)  # above 500MB cap

    async def test_call_signal_rejects_invite_when_calls_globally_disabled(self):
        identity = Identity("alice", "Alice", ("user",), 1, "jti")
        command = WebSocketCommand(
            type="call", call_id=uuid4(), to="bob",
            payload={"type": "invite", "media": {"video": False}},
        )
        sent = []

        async def fake_send(_ws, _lock, message):
            sent.append(message)

        with (
            patch("messenger.calls.direct_chat_for_pair", new=AsyncMock(return_value=uuid4())),
            patch("messenger.calls.calls_globally_enabled", new=AsyncMock(return_value=False)),
        ):
            await handle_call_signal(None, None, identity, command, {}, fake_send)
        self.assertEqual(sent[-1]["payload"]["code"], "calls_disabled")

    async def test_call_signal_rejects_invite_when_a_participant_has_calls_disabled(self):
        identity = Identity("alice", "Alice", ("user",), 1, "jti")
        command = WebSocketCommand(
            type="call", call_id=uuid4(), to="bob",
            payload={"type": "invite", "media": {"video": False}},
        )
        sent = []

        async def fake_send(_ws, _lock, message):
            sent.append(message)

        with (
            patch("messenger.calls.direct_chat_for_pair", new=AsyncMock(return_value=uuid4())),
            patch("messenger.calls.calls_globally_enabled", new=AsyncMock(return_value=True)),
            patch(
                "messenger.calls.users_calls_allowed",
                new=AsyncMock(return_value={"alice": True, "bob": False}),
            ),
        ):
            await handle_call_signal(None, None, identity, command, {}, fake_send)
        self.assertEqual(sent[-1]["payload"]["code"], "calls_disabled")

    async def test_direct_call_token_rechecks_permission_at_join_time(self):
        # A call can be accepted before an admin flips calls off mid-ring —
        # the token mint must re-check, not just trust the earlier invite.
        identity = Identity("alice", "Alice", ("user",), 1, "jti")
        call_id = uuid4()
        with (
            patch(
                "messenger.calls.get_call",
                new=AsyncMock(return_value={
                    "caller_username": "alice", "callee_username": "bob", "status": "accepted",
                }),
            ),
            patch("messenger.calls.calls_globally_enabled", new=AsyncMock(return_value=False)),
        ):
            with self.assertRaises(HTTPException) as raised:
                await mint_direct_call_token(identity, call_id)
        self.assertEqual(raised.exception.status_code, 403)

    async def test_start_or_join_group_call_rejects_when_globally_disabled(self):
        identity = Identity("alice", "Alice", ("user",), 1, "jti")
        with patch("messenger.rooms.calls_globally_enabled", new=AsyncMock(return_value=False)):
            with self.assertRaises(HTTPException) as raised:
                await start_or_join_call(identity, uuid4())
        self.assertEqual(raised.exception.status_code, 403)

    async def test_start_or_join_group_call_rejects_when_user_calls_disabled(self):
        identity = Identity("alice", "Alice", ("user",), 1, "jti")
        with (
            patch("messenger.rooms.calls_globally_enabled", new=AsyncMock(return_value=True)),
            patch(
                "messenger.rooms.users_calls_allowed",
                new=AsyncMock(return_value={"alice": False}),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await start_or_join_call(identity, uuid4())
        self.assertEqual(raised.exception.status_code, 403)

    def test_admin_config_patch_only_sends_supplied_fields(self):
        # The panel saves one section at a time, so an omitted field must not
        # be written back as a default and silently clobber another section.
        changes = UpdateAdminConfig(rate_messages_per_minute=120).changes()
        self.assertEqual(changes, {"rate_messages_per_minute": 120})

    def test_admin_config_can_clear_attachment_limit_explicitly(self):
        # "Use the server default" is a real value (NULL), distinct from
        # "field not supplied" — otherwise the limit could never be unset.
        self.assertEqual(
            UpdateAdminConfig(max_attachment_bytes_set=True).changes(),
            {"max_attachment_bytes": None},
        )
        self.assertEqual(
            UpdateAdminConfig(max_attachment_bytes=5 * 1024 * 1024).changes(),
            {"max_attachment_bytes": 5 * 1024 * 1024},
        )

    def test_admin_config_rejects_out_of_range_runtime_limits(self):
        with self.assertRaises(ValidationError):
            UpdateAdminConfig(rate_messages_per_minute=0)
        with self.assertRaises(ValidationError):
            UpdateAdminConfig(group_max_members=99999)
        with self.assertRaises(ValidationError):
            UpdateAdminConfig(retention_messages_days=-1)
        # 0 days is valid and means "keep forever".
        self.assertEqual(
            UpdateAdminConfig(retention_messages_days=0).changes(),
            {"retention_messages_days": 0},
        )

    def test_admin_config_editable_allowlist_excludes_identity_columns(self):
        # update_admin_config builds its SQL SET clause from dict keys, so the
        # allowlist is what stops a crafted payload from writing bookkeeping
        # columns (or anything else) via the config endpoint.
        allowlisted = set(_ADMIN_CONFIG_EDITABLE)
        for forbidden in ("id", "updated_at", "updated_by"):
            self.assertNotIn(forbidden, allowlisted)
        # Everything the panel edits must be present, or a section silently
        # stops saving.
        for expected in ("calls_enabled", "retention_messages_days", "group_max_members"):
            self.assertIn(expected, allowlisted)

    async def test_set_user_active_refuses_self_lockout(self):
        identity = Identity("admin", "Admin", ("user", "system_admin"), 1, "jti")
        with self.assertRaises(HTTPException) as raised:
            await set_user_active(identity, "admin", False)
        self.assertEqual(raised.exception.status_code, 400)

    async def test_user_admin_actions_require_admin_role(self):
        plain = Identity("alice", "Alice", ("user",), 1, "jti")
        for coro in (
            set_user_active(plain, "bob", False),
            set_user_muted(plain, "bob", True),
            set_user_auditor(plain, "bob", True),
            revoke_user_sessions(plain, "bob"),
            revoke_all_sessions(plain),
            update_admin_config(plain, {"calls_enabled": False}),
        ):
            with self.assertRaises(HTTPException) as raised:
                await coro
            self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
