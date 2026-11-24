import hashlib
import hmac
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import app as messenger
from auth import Identity


class PortalSsoTests(unittest.TestCase):
    def setUp(self):
        messenger.app.config.update(TESTING=True, SECRET_KEY="test-cookie-secret")
        self.client = messenger.app.test_client()
        self.base_url = "https://messenger.example.local"
        self.settings = patch.multiple(
            messenger,
            PORTAL_SSO_REQUIRED=True,
            UNIVERSALAI_PUBLIC_URL="https://messenger.example.local",
            PORTAL_LOGIN_URL="https://portal.example.local/login",
            PORTAL_LOGOUT_URL="https://portal.example.local/logout",
            PORTAL_SSO_LOGOUT_CONTINUE_URL="https://ai.example.local/sso-logout",
            AI_SSO_SHARED_SECRET="messenger-secret-" + "x" * 48,
        )
        self.settings.start()

    def tearDown(self):
        self.settings.stop()

    def _start(self):
        response = self.client.get("/sso-start", base_url=self.base_url)
        self.assertEqual(response.status_code, 302)
        portal_query = parse_qs(urlsplit(response.location).query)
        callback = portal_query["return_url"][0]
        return parse_qs(urlsplit(callback).query)["state"][0]

    def _callback(self, state, username):
        display = username.title()
        timestamp = str(int(time.time()))
        payload = f"{username}|{display}|{timestamp}|{state}"
        sig = hmac.new(
            messenger.AI_SSO_SHARED_SECRET.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return self.client.get("/sso-login", base_url=self.base_url, query_string={
            "state": state, "u": username, "d": display, "ts": timestamp, "sig": sig,
        })

    def test_direct_page_starts_portal_login(self):
        response = self.client.get("/messenger", base_url=self.base_url)
        self.assertEqual(response.location, "/sso-start")
        state = self._start()
        self.assertGreaterEqual(len(state), 20)

    def test_docker_health_check_stays_local_with_sso(self):
        response = self.client.get("/health", base_url="http://localhost:5000")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})

    def test_https_ip_and_port_are_preserved_in_sso(self):
        self.base_url = "https://192.0.2.125:8443"
        with patch.object(messenger, "UNIVERSALAI_PUBLIC_URL", self.base_url):
            response = self.client.get("/sso-start", base_url=self.base_url)
            self.assertEqual(response.status_code, 302)
            callback = parse_qs(urlsplit(response.location).query)["return_url"][0]
            self.assertEqual(urlsplit(callback).netloc, "192.0.2.125:8443")
            state = parse_qs(urlsplit(callback).query)["state"][0]
            with patch.object(messenger, "persist_external_identity", side_effect=lambda identity: identity):
                self.assertEqual(self._callback(state, "alice").location, "/messenger")

    def test_ip_entry_moves_to_canonical_https_before_creating_state(self):
        response = self.client.get("/messenger", base_url="http://192.0.2.125:5000")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "https://messenger.example.local/sso-start")
        self.assertEqual(self.client.get("/api/me", base_url="http://192.0.2.125:5000").status_code, 403)

    def test_callback_requires_matching_one_time_state(self):
        state = self._start()
        with patch.object(messenger, "persist_external_identity", return_value=Identity("alice", "Alice", ("user",), "sso")):
            self.assertEqual(self._callback("wrong", "alice").status_code, 403)
            self.assertEqual(self._callback(state, "alice").status_code, 403)
            state = self._start()
            self.assertEqual(self._callback(state, "alice").status_code, 302)
            self.assertEqual(self._callback(state, "alice").status_code, 403)
        self.assertEqual(self.client.get("/messenger", base_url=self.base_url).status_code, 200)
        self.assertEqual(self.client.get("/messenger", base_url=self.base_url).location, "/sso-start")

    def test_reentry_replaces_old_identity_and_revokes_tokens(self):
        with patch.object(messenger, "persist_external_identity", side_effect=lambda identity: identity), patch.object(messenger, "_revoke_messenger_tokens") as revoke:
            self.assertEqual(self._callback(self._start(), "alice").status_code, 302)
            self.client.get("/messenger", base_url=self.base_url)
            state = self._start()
            revoke.assert_called_with("alice")
            self.assertEqual(self._callback(state, "bob").status_code, 302)
        with self.client.session_transaction(base_url=self.base_url) as current:
            self.assertEqual(current["username"], "bob")

    def test_local_login_is_disabled_and_logout_is_allowlisted(self):
        self.assertEqual(self.client.post("/api/auth/login", base_url=self.base_url, json={}).status_code, 403)
        with self.client.session_transaction(base_url=self.base_url) as current:
            current["username"] = "alice"
            current["logged_in"] = True
        with patch.object(messenger, "_revoke_messenger_tokens"):
            response = self.client.get("/sso-logout", base_url=self.base_url, query_string={"next": "https://evil.example/"})
        self.assertEqual(response.location, "https://portal.example.local/login")
        with self.client.session_transaction(base_url=self.base_url) as current:
            self.assertNotIn("username", current)
        response = self.client.get("/sso-logout", base_url=self.base_url, query_string={"next": "https://ai.example.local/sso-logout"})
        self.assertEqual(response.location, "https://ai.example.local/sso-logout")

    def test_required_sso_rejects_insecure_configuration(self):
        with patch.object(messenger, "DATABASE_URL", "postgresql://test"), patch.object(messenger, "UNIVERSALAI_PUBLIC_URL", "http://messenger.example.local"):
            with self.assertRaisesRegex(RuntimeError, "UNIVERSALAI_PUBLIC_URL"):
                messenger.validate_runtime_config()
        with patch.object(messenger, "DATABASE_URL", "postgresql://test"), patch.object(messenger, "AI_SSO_SHARED_SECRET", "short"):
            with self.assertRaisesRegex(RuntimeError, "AI_SSO_SHARED_SECRET"):
                messenger.validate_runtime_config()


if __name__ == "__main__":
    unittest.main()
