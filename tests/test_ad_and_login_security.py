import os
import unittest
from unittest.mock import patch

import ad_sync
import app as app_module
from auth import Identity
from auth_ldap import DirectoryUser, LdapUnavailable


class RecordingCursor:
    def __init__(self, returning=()):
        self.statements = []
        self._returning = list(returning)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, statement, params=()):
        self.statements.append((statement, params))

    def fetchall(self):
        return self._returning


class RecordingConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True


def directory_user(username, department="ОЦ", disabled=False):
    return DirectoryUser(
        username=username, dn=f"CN={username},DC=corp", display_name=f"{username} Иванович",
        department=department, title="Инженер", email="", phone="", office="", disabled=disabled,
    )


class LoginSecurityTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        app_module._login_attempts.clear()
        self.client = app_module.app.test_client()

    def _ldap_login(self, username, ip):
        return self.client.post(
            "/api/auth/login",
            json={"provider": "ldap", "username": username, "password": "wrong"},
            headers={"X-Forwarded-For": ip},
        )

    def test_failed_logins_from_one_colleague_do_not_lock_out_others(self):
        with patch.object(app_module, "authenticate_ldap", return_value=None):
            for _ in range(app_module.LOGIN_RATE_LIMIT):
                self._ldap_login("mallory", "10.0.0.66")
            self.assertEqual(self._ldap_login("mallory", "10.0.0.66").status_code, 429)
            self.assertEqual(self._ldap_login("alice", "10.0.0.10").status_code, 401)

    def test_one_account_is_limited_across_addresses(self):
        with patch.object(app_module, "authenticate_ldap", return_value=None):
            for index in range(app_module.LOGIN_RATE_LIMIT):
                self._ldap_login("alice", f"10.0.1.{index}")
            self.assertEqual(self._ldap_login("alice", "10.0.2.1").status_code, 429)

    def test_ad_login_cannot_take_over_local_account(self):
        identity = Identity("admin", "Захватчик", ("user",), "ldap")
        with (
            patch.object(app_module, "authenticate_ldap", return_value=identity),
            patch.object(app_module, "persist_external_identity", return_value=None),
        ):
            response = self._ldap_login("admin", "10.0.0.5")
        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as session:
            self.assertFalse(session.get("logged_in"))

    def test_default_local_passwords_are_rejected_in_any_environment(self):
        env = {"DATABASE_URL": "postgresql://x", "LOCAL_ADMIN_PASSWORD": "admin123"}
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError):
                app_module.validate_runtime_config()


class AdSyncTests(unittest.TestCase):
    def test_empty_directory_never_mass_disables(self):
        with self.assertRaises(LdapUnavailable):
            ad_sync.apply_directory([], set())

    def test_sync_upserts_people_and_disables_missing(self):
        cursor = RecordingCursor(returning=[{"username": "fired"}])
        connection = RecordingConnection(cursor)
        users = [directory_user("ivanov"), directory_user("petrov", disabled=True)]
        with patch.object(ad_sync, "get_db", return_value=connection):
            result = ad_sync.apply_directory(users, {"ivanov"})
        upserts = [params for sql, params in cursor.statements if "INSERT INTO users" in sql]
        self.assertEqual(len(upserts), 2)
        ivanov, petrov = upserts
        self.assertEqual(ivanov[0], "ivanov")
        self.assertEqual(ivanov[2], ["admin", "user"])
        self.assertTrue(ivanov[3])
        self.assertEqual(ivanov[5], "ОЦ")
        self.assertFalse(petrov[3])
        self.assertTrue(petrov[4])
        missing_sql, missing_params = cursor.statements[-1]
        self.assertIn("NOT (username = ANY(%s))", missing_sql)
        self.assertEqual(missing_params, (["ivanov", "petrov"],))
        self.assertEqual(result["removed"], ["fired"])
        self.assertTrue(connection.committed)


if __name__ == "__main__":
    unittest.main()
