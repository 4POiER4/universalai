import time
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import app as app_module


class FakeCursor:
    def __init__(self, token_version=3, roles=("user",)):
        self.token_version = token_version
        self.roles = list(roles)
        self.statements = []

    def execute(self, statement, params=()):
        self.statements.append((statement, params))

    def fetchone(self):
        return {"messenger_token_version": self.token_version, "roles": self.roles}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeConnection:
    def __init__(self, roles=("user",)):
        self.cursor_instance = FakeCursor(roles=roles)
        self.committed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class MessengerTokenTests(unittest.TestCase):
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

    def test_broker_token_is_short_lived_and_audience_bound(self):
        connection = FakeConnection()
        with (
            patch.object(app_module, "MESSENGER_JWT_PRIVATE_KEY", self.private_pem),
            patch.object(app_module, "MESSENGER_JWT_TTL_SEC", 300),
            patch.object(app_module, "get_db", return_value=connection),
            app_module.app.test_request_context("/"),
        ):
            app_module.session["roles"] = ["user"]
            token, ttl = app_module._issue_messenger_token("alice", "Alice")

        claims = jwt.decode(
            token,
            self.public_pem,
            algorithms=["EdDSA"],
            audience="messenger",
            issuer="universalai",
        )
        self.assertEqual(ttl, 300)
        self.assertEqual(claims["sub"], "alice")
        self.assertEqual(claims["ver"], 3)
        self.assertLessEqual(claims["exp"] - int(time.time()), 300)
        self.assertNotIn("admin", claims["roles"])
        self.assertTrue(connection.committed)

    def test_admin_is_mapped_to_messenger_admin_roles(self):
        roles = app_module._messenger_roles("admin", {"admin", "user"})
        self.assertIn("chat_admin", roles)
        self.assertIn("system_admin", roles)
        self.assertIn("user", roles)

    def test_token_roles_come_from_database_not_login_cookie(self):
        # Admin rights revoked in AD after login: the cookie still says admin.
        connection = FakeConnection(roles=("user",))
        with (
            patch.object(app_module, "MESSENGER_JWT_PRIVATE_KEY", self.private_pem),
            patch.object(app_module, "get_db", return_value=connection),
            app_module.app.test_request_context("/"),
        ):
            app_module.session["roles"] = ["admin", "user"]
            token, _ = app_module._issue_messenger_token("bob", "Bob")
            refreshed = app_module.session["roles"]
        claims = jwt.decode(token, self.public_pem, algorithms=["EdDSA"], audience="messenger")
        self.assertNotIn("system_admin", claims["roles"])
        self.assertEqual(refreshed, ["user"])

    def test_messenger_page_has_strict_csp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="test")
        client = app_module.app.test_client()
        with client.session_transaction() as flask_session:
            flask_session["logged_in"] = True
            flask_session["username"] = "alice"
            flask_session["roles"] = ["user"]
        response = client.get("/messenger")
        policy = response.headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", policy)
        self.assertIn("wasm-unsafe-eval", policy)
        self.assertIn("unsafe-eval", policy)
        self.assertIn("style-src 'self'", policy)
        self.assertNotIn("unsafe-inline", policy)


if __name__ == "__main__":
    unittest.main()
