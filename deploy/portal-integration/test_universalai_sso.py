import hashlib
import hmac
import os
import sys
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import app as portal


class UniversalAiSsoTests(unittest.TestCase):
    def setUp(self):
        portal.app.config.update(TESTING=True)
        self.client = portal.app.test_client()
        self.settings = patch.multiple(
            portal,
            UNIVERSALAI_PUBLIC_URL="https://messenger.example.local",
            UNIVERSALAI_CALLBACK_URL="https://messenger.example.local/sso-login",
            UNIVERSALAI_SSO_SHARED_SECRET="messenger-secret-" + "x" * 48,
            AI_SSO_SHARED_SECRET="ai-secret",
        )
        self.settings.start()
        access = patch.object(portal, "can_access_portal_path", return_value=True)
        self.access = access.start()
        self.addCleanup(access.stop)

    def tearDown(self):
        self.settings.stop()

    def test_portal_signs_only_exact_messenger_callback(self):
        with self.client.session_transaction() as current:
            current["logged_in"] = True
            current["username"] = "DOMAIN\\alice"
            current["display_name"] = "Alice"
        callback = "https://messenger.example.local/sso-login?state=" + "A" * 32
        response = self.client.get("/login", query_string={"return_url": callback})
        self.assertEqual(response.status_code, 302)
        query = parse_qs(urlsplit(response.location).query)
        self.assertEqual(query["u"], ["alice"])
        payload = f"alice|Alice|{query['ts'][0]}|{query['state'][0]}"
        self.assertEqual(query["sig"], [hmac.new(portal.UNIVERSALAI_SSO_SHARED_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()])
        for unsafe in ("https://evil.example/sso-login?state=" + "A" * 32,
                       callback + "&next=https://evil.example", callback.replace("https://", "http://")):
            self.assertEqual(self.client.get("/login", query_string={"return_url": unsafe}).status_code, 400)

    def test_portal_tile_refreshes_messenger_and_logout_chains(self):
        with self.client.session_transaction() as current:
            current["logged_in"] = True
            current["username"] = "alice"
        self.assertEqual(self.client.get("/universalai").location, "https://messenger.example.local/sso-start")
        response = self.client.get("/logout")
        self.assertEqual(urlsplit(response.location).path, "/sso-logout")
        self.assertEqual(parse_qs(urlsplit(response.location).query)["next"], [portal.AI_ASSISTANT_PUBLIC_URL.rstrip("/") + "/sso-logout"])

    def test_direct_sso_respects_resource_permissions(self):
        with self.client.session_transaction() as current:
            current["logged_in"] = True
            current["username"] = "alice"
        self.access.return_value = False
        callback = "https://messenger.example.local/sso-login?state=" + "A" * 32
        response = self.client.get("/login", query_string={"return_url": callback})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(response.location)
        self.access.assert_called_once_with("alice", "/universalai")

    def test_ad_login_redirects_to_messenger_with_signed_identity(self):
        callback = "https://messenger.example.local/sso-login?state=" + "B" * 32
        with (
            patch.object(portal, "check_ldap_auth", return_value=(True, None)),
            patch.object(portal, "get_db_connection", return_value=MagicMock()),
            patch.object(portal, "add_user_to_default_group"),
        ):
            response = self.client.post("/login", json={
                "username": "alice", "password": "test-password", "return_url": callback,
            })
        self.assertEqual(response.status_code, 200)
        redirect_url = response.get_json()["redirect_url"]
        self.assertEqual(urlsplit(redirect_url).netloc, "messenger.example.local")
        self.assertEqual(parse_qs(urlsplit(redirect_url).query)["state"], ["B" * 32])

    def test_incomplete_messenger_configuration_is_not_activated(self):
        with patch.object(portal, "UNIVERSALAI_PUBLIC_URL", "http://messenger.example.local"):
            self.assertFalse(portal._universalai_sso_ready())
        with patch.object(portal, "UNIVERSALAI_SSO_SHARED_SECRET", "short"):
            self.assertFalse(portal._universalai_sso_ready())

    def test_callback_must_match_the_configured_ip_and_port(self):
        chat_origin = "https://192.0.2.125:8443"
        with patch.multiple(portal, UNIVERSALAI_PUBLIC_URL=chat_origin,
                            UNIVERSALAI_CALLBACK_URL=f"{chat_origin}/sso-login"):
            with self.client.session_transaction() as current:
                current["logged_in"] = True
                current["username"] = "alice"
            callback = f"{chat_origin}/sso-login?state=" + "A" * 32
            accepted = self.client.get("/login", query_string={"return_url": callback})
            self.assertEqual(accepted.status_code, 302)
            self.assertEqual(urlsplit(accepted.location).netloc, "192.0.2.125:8443")
            wrong_port = self.client.get("/login", query_string={"return_url": callback.replace(":8443", ":9443")})
            self.assertEqual(wrong_port.status_code, 400)


if __name__ == "__main__":
    unittest.main()
