import os
from dataclasses import dataclass

from flask import session
from werkzeug.security import check_password_hash, generate_password_hash

from db import get_db


VALID_ROLES = {"admin", "user"}


PROFILE_FIELDS = ("department", "title", "email", "phone", "office")


@dataclass(frozen=True)
class Identity:
    username: str
    display_name: str
    roles: tuple[str, ...]
    auth_source: str
    department: str = ""
    title: str = ""
    email: str = ""
    phone: str = ""
    office: str = ""


def normalize_username(raw_username: str) -> str:
    login = (raw_username or "").strip().lower()
    if "\\" in login:
        login = login.split("\\")[-1].strip()
    if "@" in login:
        login = login.split("@")[0].strip()
    return login


def _clean_roles(roles) -> tuple[str, ...]:
    result = sorted({str(role).strip().lower() for role in (roles or [])} & VALID_ROLES)
    return tuple(result or ["user"])


def bootstrap_local_users() -> None:
    """Create/update development bootstrap accounts when passwords are supplied."""
    seeds = (
        (
            os.environ.get("LOCAL_ADMIN_USERNAME", "admin"),
            os.environ.get("LOCAL_ADMIN_DISPLAY_NAME", "Администратор"),
            os.environ.get("LOCAL_ADMIN_PASSWORD", ""),
            ("admin", "user"),
        ),
        (
            os.environ.get("LOCAL_TEST_USERNAME", "test"),
            os.environ.get("LOCAL_TEST_DISPLAY_NAME", "Тестовый пользователь"),
            os.environ.get("LOCAL_TEST_PASSWORD", ""),
            ("user",),
        ),
        (
            os.environ.get("LOCAL_FOORY_USERNAME", "foory"),
            os.environ.get("LOCAL_FOORY_DISPLAY_NAME", "foory"),
            os.environ.get("LOCAL_FOORY_PASSWORD", ""),
            ("user",),
        ),
    )
    with get_db() as conn:
        with conn.cursor() as cur:
            for raw_username, display_name, password, roles in seeds:
                username = normalize_username(raw_username)
                if not username or not password:
                    continue
                password_hash = generate_password_hash(password, method="scrypt")
                cur.execute(
                    """
                    INSERT INTO users
                      (username, display_name, password_hash, auth_source, roles)
                    VALUES (%s, %s, %s, 'local', %s)
                    ON CONFLICT (username) DO UPDATE SET
                      display_name = EXCLUDED.display_name,
                      password_hash = EXCLUDED.password_hash,
                      roles = EXCLUDED.roles,
                      is_active = TRUE,
                      updated_at = NOW()
                    WHERE users.auth_source = 'local'
                    """,
                    (username, display_name.strip() or username, password_hash, list(roles)),
                )
        conn.commit()


def authenticate_local(raw_username: str, password: str) -> Identity | None:
    username = normalize_username(raw_username)
    if not username or not password:
        return None
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT username, display_name, password_hash, roles
                FROM users
                WHERE username=%s AND auth_source='local' AND is_active=TRUE
                """,
                (username,),
            )
            row = cur.fetchone()
    if not row or not row.get("password_hash"):
        return None
    if not check_password_hash(row["password_hash"], password):
        return None
    return Identity(
        username=row["username"],
        display_name=row.get("display_name") or row["username"],
        roles=_clean_roles(row.get("roles")),
        auth_source="local",
    )


def persist_external_identity(identity: Identity) -> Identity | None:
    """Upsert an LDAP/SSO identity. Returns None when the username belongs to a
    local account: an external login must never take over a local mailbox."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users
                  (username, display_name, auth_source, roles, last_login_at,
                   department, title, email, phone, office)
                VALUES (%s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s)
                ON CONFLICT (username) DO UPDATE SET
                  display_name = EXCLUDED.display_name,
                  roles = CASE WHEN EXCLUDED.auth_source = 'ldap' THEN EXCLUDED.roles ELSE users.roles END,
                  department = CASE WHEN EXCLUDED.auth_source = 'ldap' THEN EXCLUDED.department ELSE users.department END,
                  title = CASE WHEN EXCLUDED.auth_source = 'ldap' THEN EXCLUDED.title ELSE users.title END,
                  email = CASE WHEN EXCLUDED.auth_source = 'ldap' THEN EXCLUDED.email ELSE users.email END,
                  phone = CASE WHEN EXCLUDED.auth_source = 'ldap' THEN EXCLUDED.phone ELSE users.phone END,
                  office = CASE WHEN EXCLUDED.auth_source = 'ldap' THEN EXCLUDED.office ELSE users.office END,
                  last_login_at = NOW(),
                  updated_at = NOW()
                WHERE users.auth_source <> 'local'
                RETURNING username, display_name, auth_source, roles, is_active
                """,
                (
                    identity.username,
                    identity.display_name,
                    identity.auth_source,
                    list(identity.roles),
                    *(getattr(identity, field) for field in PROFILE_FIELDS),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    if not row or not row.get("is_active", True):
        return None
    return Identity(
        username=row["username"],
        display_name=row.get("display_name") or row["username"],
        roles=_clean_roles(row.get("roles")),
        auth_source=row.get("auth_source") or identity.auth_source,
    )


def load_active_roles(username: str) -> tuple[str, ...] | None:
    """Current roles from the database, or None if the account is gone or disabled."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT roles FROM users WHERE username=%s AND is_active=TRUE",
                (username,),
            )
            row = cur.fetchone()
    return _clean_roles(row["roles"]) if row else None


def record_login(identity: Identity) -> None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET last_login_at=NOW(), updated_at=NOW() WHERE username=%s",
                (identity.username,),
            )
        conn.commit()


def login_identity(identity: Identity) -> None:
    session.clear()
    session["logged_in"] = True
    session["username"] = identity.username
    session["display_name"] = identity.display_name
    session["roles"] = list(_clean_roles(identity.roles))
    session["auth_source"] = identity.auth_source


def logout_identity() -> None:
    session.clear()


def current_roles() -> set[str]:
    return set(_clean_roles(session.get("roles") or ["user"]))


def has_role(role: str) -> bool:
    return role in current_roles()

