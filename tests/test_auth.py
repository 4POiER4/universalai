import unittest
from unittest.mock import patch

import app as app_module
from auth import Identity, normalize_username
from auth_ldap import LdapConfig


class AuthTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        app_module._login_attempts.clear()
        self.client = app_module.app.test_client()

    def test_username_normalization_matches_ad_forms(self):
        self.assertEqual(normalize_username(r"DOMAIN\Ivanov"), "ivanov")
        self.assertEqual(normalize_username("Ivanov@example.local"), "ivanov")

    def test_local_login_establishes_admin_session(self):
        identity = Identity(
            "admin", "Администратор", ("admin", "user"), "local"
        )
        with (
            patch.object(app_module, "authenticate_local", return_value=identity),
            patch.object(app_module, "record_login"),
        ):
            response = self.client.post(
                "/api/auth/login",
                json={
                    "provider": "local",
                    "username": "admin",
                    "password": "secret",
                    "next": "/messenger",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["next"], "/messenger")
        with self.client.session_transaction() as session:
            self.assertTrue(session["logged_in"])
            self.assertIn("admin", session["roles"])
            self.assertEqual(session["auth_source"], "local")

    def test_login_rejects_unsafe_next_url(self):
        identity = Identity("test", "Test", ("user",), "local")
        with (
            patch.object(app_module, "authenticate_local", return_value=identity),
            patch.object(app_module, "record_login"),
        ):
            response = self.client.post(
                "/api/auth/login",
                json={
                    "provider": "local",
                    "username": "test",
                    "password": "secret",
                    "next": "//evil.example",
                },
            )
        self.assertEqual(response.get_json()["next"], "/messenger")

    def test_invalid_login_does_not_create_session(self):
        with patch.object(app_module, "authenticate_local", return_value=None):
            response = self.client.post(
                "/api/auth/login",
                json={"provider": "local", "username": "test", "password": "wrong"},
            )
        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as session:
            self.assertFalse(session.get("logged_in", False))

    def test_logout_requires_csrf_and_clears_session(self):
        with self.client.session_transaction() as session:
            session["logged_in"] = True
            session["username"] = "admin"
            session["roles"] = ["admin"]
            session["csrf_token"] = "csrf"
        forbidden = self.client.post("/api/auth/logout")
        self.assertEqual(forbidden.status_code, 403)
        with patch.object(app_module, "_revoke_messenger_tokens"):
            response = self.client.post(
                "/api/auth/logout", headers={"X-CSRF-Token": "csrf"}
            )
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as session:
            self.assertFalse(session.get("logged_in", False))

    def test_ldap_is_disabled_without_required_configuration(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(LdapConfig.from_env().enabled)

    def test_root_opens_messenger_and_messenger_requires_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/messenger"))
        with patch.object(app_module, "ALLOW_ANONYMOUS", False):
            response = self.client.get("/messenger")
            self.assertEqual(response.status_code, 302)
            self.assertIn("/login?next=/messenger", response.location)
        with self.client.session_transaction() as session:
            session["logged_in"] = True
            session["username"] = "test"
            session["display_name"] = "Test User"
            session["roles"] = ["user"]
        self.assertEqual(self.client.get("/messenger").status_code, 200)


if __name__ == "__main__":
    unittest.main()
