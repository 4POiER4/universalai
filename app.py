import os
import json
import time
import hmac
import hashlib
import tempfile
import uuid
import threading
import logging
import secrets
import urllib.parse
from collections import defaultdict, deque
from flask import Flask, request, send_from_directory, session, redirect, jsonify, render_template, make_response
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
import jwt
import redis
from auth import (
    Identity,
    authenticate_local,
    bootstrap_local_users,
    current_roles,
    has_role,
    load_active_roles,
    login_identity,
    logout_identity,
    normalize_username,
    persist_external_identity,
    record_login,
)
from auth_ldap import LdapConfig, LdapUnavailable, authenticate_ldap
from db import get_db

app = Flask(__name__, static_folder="static", template_folder="templates")
ROOT_DIR = app.root_path
AVATAR_DIR = os.path.join(ROOT_DIR, "data", "avatars")
AVATAR_MAX_BYTES = int(os.environ.get("AVATAR_MAX_MB", "5")) * 1024 * 1024
AVATAR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
ALLOW_ANONYMOUS = os.environ.get("ALLOW_ANONYMOUS", "false").strip().lower() == "true"
PORTAL_SSO_REQUIRED = os.environ.get("PORTAL_SSO_REQUIRED", "false").strip().lower() == "true"
AI_FLASK_SECRET = os.environ.get("AI_FLASK_SECRET", "").strip()
app.secret_key = AI_FLASK_SECRET or secrets.token_hex(32)
# The app is only reachable through the nginx gateway; without this every
# request would carry the gateway's address and share one login rate limit.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))
if TRUSTED_PROXY_HOPS > 0:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=TRUSTED_PROXY_HOPS, x_proto=TRUSTED_PROXY_HOPS)
app.config.update(
    SESSION_COOKIE_NAME="universalai_session" if PORTAL_SSO_REQUIRED else "belnipiai_session",
    SESSION_COOKIE_PATH='/',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get(
        "SESSION_COOKIE_SECURE",
        "true" if APP_ENV == "production" else "false",
    ).strip().lower() == "true",
    MAX_CONTENT_LENGTH=AVATAR_MAX_BYTES + 1024 * 1024,
)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("universalai")


@app.after_request
def _messenger_no_cache(response):
    path = request.path or ""
    if path in {"/sso-start", "/sso-login", "/sso-logout"}:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    if path.startswith("/static/messenger/stickers/") or path.startswith("/static/messenger/reactions/"):
        response.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return response
    if path == "/messenger" or path.startswith("/static/messenger/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


if not AI_FLASK_SECRET:
    logger.warning("AI_FLASK_SECRET is not set; sessions will reset after restart")

AI_SSO_SHARED_SECRET = os.environ.get("AI_SSO_SHARED_SECRET", "").strip()
AI_SSO_MAX_AGE_SEC = int(os.environ.get("AI_SSO_MAX_AGE_SEC", "300"))
UNIVERSALAI_PUBLIC_URL = os.environ.get("UNIVERSALAI_PUBLIC_URL", "").strip().rstrip("/")
PORTAL_LOGIN_URL = os.environ.get("PORTAL_LOGIN_URL", "https://portal.example.com/login").strip()
PORTAL_LOGOUT_URL = os.environ.get("PORTAL_LOGOUT_URL", "https://portal.example.com/logout").strip()
PORTAL_SSO_LOGOUT_CONTINUE_URL = os.environ.get("PORTAL_SSO_LOGOUT_CONTINUE_URL", "").strip()
LOGIN_RATE_LIMIT = int(os.environ.get("LOGIN_RATE_LIMIT", "10"))
LOGIN_RATE_WINDOW_SEC = int(os.environ.get("LOGIN_RATE_WINDOW_SEC", "300"))
MESSENGER_JWT_PRIVATE_KEY = os.environ.get("MESSENGER_JWT_PRIVATE_KEY", "").replace("\\n", "\n").strip()
MESSENGER_JWT_KEY_ID = os.environ.get("MESSENGER_JWT_KEY_ID", "local-1").strip()
MESSENGER_JWT_ISSUER = os.environ.get("MESSENGER_JWT_ISSUER", "universalai").strip()
MESSENGER_JWT_AUDIENCE = os.environ.get("MESSENGER_JWT_AUDIENCE", "messenger").strip()
MESSENGER_JWT_TTL_SEC = max(60, min(int(os.environ.get("MESSENGER_JWT_TTL_SEC", "300")), 900))
MESSENGER_LIVEKIT_URL = os.environ.get("MESSENGER_LIVEKIT_URL", "").strip()
MESSENGER_AUDITOR_USERS = {
    item.strip().lower()
    for item in os.environ.get("MESSENGER_AUDITOR_USERS", "").split(",")
    if item.strip()
}
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
_rate_redis = (
    redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    if REDIS_URL
    else None
)

_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_attempts_lock = threading.Lock()


def _current_user() -> str:
    username = _normalize_username(session.get("username") or "")
    if username:
        return username
    anonymous_id = session.get("anonymous_id")
    return f"anonymous:{anonymous_id}" if anonymous_id else ""


def _normalize_username(raw_username: str) -> str:
    return normalize_username(raw_username)


def _verify_sso_payload(username: str, display_name: str, ts_raw: str, sig: str, state: str = "") -> bool:
    if not AI_SSO_SHARED_SECRET:
        return False
    try:
        ts_val = int(ts_raw)
    except Exception:
        return False
    if abs(int(time.time()) - ts_val) > AI_SSO_MAX_AGE_SEC:
        return False
    payload = f"{username}|{display_name}|{ts_raw}"
    if state:
        payload += f"|{state}"
    expected = hmac.new(
        AI_SSO_SHARED_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return sig.isascii() and hmac.compare_digest(expected, sig)


PUBLIC_API_ENDPOINTS = {"api_me", "api_auth_login"}


@app.before_request
def require_canonical_sso_origin():
    if not PORTAL_SSO_REQUIRED or request.endpoint == "health":
        return None
    canonical_host = urllib.parse.urlsplit(UNIVERSALAI_PUBLIC_URL).netloc.lower()
    if request.host.lower() == canonical_host and request.scheme == "https":
        return None
    # The state cookie must be created on the same host that receives the
    # portal callback. Never forward signed callback parameters from an IP.
    if request.method in {"GET", "HEAD"} and request.path != "/sso-login" and not request.path.startswith("/api/"):
        return redirect(f"{UNIVERSALAI_PUBLIC_URL}/sso-start", code=302)
    return "Use the configured HTTPS messenger address", 403


@app.before_request
def require_api_auth():
    if not request.path.startswith("/api/"):
        return None
    if request.endpoint in PUBLIC_API_ENDPOINTS:
        if request.endpoint == "api_me" and ALLOW_ANONYMOUS and not session.get("anonymous_id"):
            session["anonymous_id"] = str(uuid.uuid4())
        return None

    if session.get("logged_in") and _current_user():
        pass
    elif ALLOW_ANONYMOUS:
        if not session.get("anonymous_id"):
            session["anonymous_id"] = str(uuid.uuid4())
    else:
        return jsonify({"error": "Требуется авторизация"}), 401

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return jsonify({"error": "Недействительный CSRF-токен"}), 403

    return None


def can_manage_platform() -> bool:
    return bool(session.get("logged_in") and has_role("admin"))


def _login_rate_limited(client_key: str) -> bool:
    now = time.monotonic()
    with _login_attempts_lock:
        attempts = _login_attempts[client_key]
        while attempts and now - attempts[0] > LOGIN_RATE_WINDOW_SEC:
            attempts.popleft()
        if len(attempts) >= LOGIN_RATE_LIMIT:
            return True
        attempts.append(now)
        return False


def _distributed_rate_limited(
    namespace: str,
    client_key: str,
    limit: int,
    window_seconds: int,
) -> bool:
    if _rate_redis is not None:
        try:
            key = f"universalai:rate:{namespace}:{client_key}"
            count = int(_rate_redis.incr(key))
            if count == 1:
                _rate_redis.expire(key, window_seconds)
            return count > limit
        except redis.RedisError:
            logger.warning("Redis rate limiter unavailable; using process-local fallback")
    return _login_rate_limited(f"{namespace}:{client_key}")


def _clear_rate_limit(namespace: str, client_key: str) -> None:
    if _rate_redis is not None:
        try:
            _rate_redis.delete(f"universalai:rate:{namespace}:{client_key}")
        except redis.RedisError:
            logger.warning("Redis rate limiter unavailable while clearing %s", namespace)
    with _login_attempts_lock:
        _login_attempts.pop(f"{namespace}:{client_key}", None)


# ── база данных ───────────────────────────────────────────────────────────────

def init_db():
    schema = os.path.join(ROOT_DIR, "schema.sql")
    if os.path.exists(schema):
        with get_db() as conn:
            with conn.cursor() as cur:
                with open(schema, "r", encoding="utf-8") as schema_file:
                    cur.execute(schema_file.read())
            conn.commit()
    _ensure_avatar_dir()
    bootstrap_local_users()


LOCAL_PASSWORD_VARS = ("LOCAL_ADMIN_PASSWORD", "LOCAL_TEST_PASSWORD", "LOCAL_FOORY_PASSWORD")
WEAK_PASSWORDS = {"admin", "admin123", "test", "test123", "foory", "foory123", "password", "12345678"}
MIN_LOCAL_PASSWORD_LEN = 12


def validate_runtime_config():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required")
    if APP_ENV == "production" and not AI_FLASK_SECRET:
        raise RuntimeError("AI_FLASK_SECRET is required in production")
    if APP_ENV == "production" and ALLOW_ANONYMOUS:
        raise RuntimeError("ALLOW_ANONYMOUS must be false in production")
    if PORTAL_SSO_REQUIRED:
        for name, value, path in (
            ("UNIVERSALAI_PUBLIC_URL", UNIVERSALAI_PUBLIC_URL, ""),
            ("PORTAL_LOGIN_URL", PORTAL_LOGIN_URL, "/login"),
            ("PORTAL_LOGOUT_URL", PORTAL_LOGOUT_URL, "/logout"),
        ):
            parsed = urllib.parse.urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or (path and parsed.path != path)
                    or (not path and parsed.path not in ("", "/"))):
                raise RuntimeError(f"{name} must be an HTTPS URL with the expected path")
        if len(AI_SSO_SHARED_SECRET) < 32:
            raise RuntimeError("AI_SSO_SHARED_SECRET must be at least 32 characters for portal SSO")
        if ALLOW_ANONYMOUS or (APP_ENV == "production" and not app.config["SESSION_COOKIE_SECURE"]):
            raise RuntimeError("Portal SSO requires non-anonymous access and secure production cookies")
        if PORTAL_SSO_LOGOUT_CONTINUE_URL:
            parsed = urllib.parse.urlsplit(PORTAL_SSO_LOGOUT_CONTINUE_URL)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                    or parsed.query or parsed.fragment):
                raise RuntimeError("PORTAL_SSO_LOGOUT_CONTINUE_URL must be a fixed HTTPS URL")
    ldap_config = LdapConfig.from_env()
    if APP_ENV == "production" and ldap_config.enabled:
        if not ldap_config.uri.lower().startswith("ldaps://"):
            raise RuntimeError("LDAP_URI must use ldaps:// in production")
        if not ldap_config.validate_tls:
            raise RuntimeError("LDAP_TLS_VALIDATE must be true in production")
    for name in LOCAL_PASSWORD_VARS:
        password = os.environ.get(name, "")
        if password and (password.lower() in WEAK_PASSWORDS or len(password) < MIN_LOCAL_PASSWORD_LEN):
            raise RuntimeError(
                f"{name} is too weak: use at least {MIN_LOCAL_PASSWORD_LEN} characters, not a default"
            )
    local_auth_enabled = any(os.environ.get(name) for name in LOCAL_PASSWORD_VARS)
    if (
        not ALLOW_ANONYMOUS
        and not AI_SSO_SHARED_SECRET
        and not ldap_config.enabled
        and not local_auth_enabled
    ):
        raise RuntimeError(
            "Configure LDAP, local bootstrap users, or SSO when anonymous access is disabled"
        )


# ── аватары ───────────────────────────────────────────────────────────────────

def _ensure_avatar_dir() -> None:
    os.makedirs(AVATAR_DIR, exist_ok=True)


def _avatar_public_url(username: str, updated_at) -> str | None:
    if not username or not updated_at:
        return None
    ts = int(updated_at.timestamp()) if hasattr(updated_at, "timestamp") else int(updated_at)
    return f"/api/avatars/{urllib.parse.quote(username)}?v={ts}"


def _avatar_file_path(username: str, ext: str) -> str:
    safe_ext = ext if ext in AVATAR_EXTENSIONS else ".jpg"
    return os.path.join(AVATAR_DIR, f"{username}{safe_ext}")


def _remove_avatar_files(username: str) -> None:
    for ext in AVATAR_EXTENSIONS:
        path = _avatar_file_path(username, ext)
        try:
            os.unlink(path)
        except OSError:
            pass


def _fetch_user_avatar(username: str) -> dict | None:
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT avatar_path, avatar_updated_at
                FROM users
                WHERE username=%s AND is_active=TRUE
                """,
                (username,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def _process_avatar_upload(username: str, tmp_path: str, ext: str) -> tuple[str, str]:
    from PIL import Image

    with Image.open(tmp_path) as image:
        image = image.convert("RGB")
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        out_ext = ".jpg"
        out_path = _avatar_file_path(username, out_ext)
        _remove_avatar_files(username)
        image.save(out_path, "JPEG", quality=86, optimize=True)
    return out_path, out_ext


def validate_image_signature(path: str) -> bool:
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


# ── HTTP ──────────────────────────────────────────────────────────────────────

@app.after_request
def add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' data: https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'",
    )
    return response


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({"error": "Размер запроса превышает допустимый лимит"}), 413


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if isinstance(error, HTTPException):
        return error
    logger.exception("Unhandled request error")
    if request.path.startswith("/api/"):
        return jsonify({"error": "Внутренняя ошибка сервера"}), 500
    return "Internal server error", 500


@app.route("/health")
def health():
    return {"status": "ok"}



@app.route("/ready")
def ready():
    checks = {"database": False}
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                checks["database"] = bool(cur.fetchone())
    except Exception:
        logger.exception("Database readiness check failed")
    status = 200 if all(checks.values()) else 503
    return {"status": "ready" if status == 200 else "not_ready", "checks": checks}, status


def _safe_next_url(value: str, fallback: str = "/messenger") -> str:
    value = (value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    return fallback


def _messenger_roles(username: str, platform_roles) -> list[str]:
    roles = {"user"}
    if "admin" in platform_roles:
        roles.update({"chat_admin", "system_admin"})
    if username in MESSENGER_AUDITOR_USERS:
        roles.add("security_auditor")
    return sorted(roles)


def _issue_messenger_token(username: str, display_name: str) -> tuple[str, int]:
    if not MESSENGER_JWT_PRIVATE_KEY:
        raise RuntimeError("Messenger token signing is not configured")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT messenger_token_version, roles
                FROM users
                WHERE username=%s AND is_active=TRUE
                """,
                (username,),
            )
            row = cur.fetchone()
            if not row:
                raise PermissionError("User is inactive")
            token_version = int(row["messenger_token_version"])
            # Roles come from the database, not the login cookie, so a change
            # in AD or the admin panel applies within one token lifetime.
            platform_roles = set(row.get("roles") or ["user"])
            session["roles"] = sorted(platform_roles & {"admin", "user"}) or ["user"]
            now = int(time.time())
            token_id = str(uuid.uuid4())
            claims = {
                "sub": username,
                "name": display_name or username,
                "roles": _messenger_roles(username, platform_roles),
                "iss": MESSENGER_JWT_ISSUER,
                "aud": MESSENGER_JWT_AUDIENCE,
                "iat": now,
                "nbf": now,
                "exp": now + MESSENGER_JWT_TTL_SEC,
                "jti": token_id,
                "ver": token_version,
            }
            token = jwt.encode(
                claims,
                MESSENGER_JWT_PRIVATE_KEY,
                algorithm="EdDSA",
                headers={"kid": MESSENGER_JWT_KEY_ID, "typ": "JWT"},
            )
            cur.execute(
                """
                INSERT INTO messenger.audit_events
                  (actor_username, action, target_type, target_id, result, metadata)
                VALUES (%s, 'token.issue', 'user', %s, 'success', %s)
                """,
                (
                    username,
                    username,
                    json.dumps({"jti": token_id, "aud": MESSENGER_JWT_AUDIENCE}),
                ),
            )
        conn.commit()
    return token, MESSENGER_JWT_TTL_SEC


def _revoke_messenger_tokens(username: str) -> None:
    if not username or not MESSENGER_JWT_PRIVATE_KEY:
        return
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET messenger_token_version = messenger_token_version + 1,
                    updated_at = NOW()
                WHERE username=%s
                RETURNING messenger_token_version
                """,
                (username,),
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    """
                    INSERT INTO messenger.audit_events
                      (actor_username, action, target_type, target_id, result, metadata)
                    VALUES (%s, 'token.revoke_all', 'user', %s, 'success', %s)
                    """,
                    (
                        username,
                        username,
                        json.dumps({"version": int(row["messenger_token_version"])}),
                    ),
                )
        conn.commit()


def _page_auth_redirect():
    if session.get("logged_in") and _current_user():
        return None
    if ALLOW_ANONYMOUS:
        return None
    return redirect(f"/login?next={request.path}")


@app.route("/")
def index():
    return redirect("/messenger")


@app.route("/messenger")
def messenger_page():
    if PORTAL_SSO_REQUIRED and not session.pop("sso_page_ready", False):
        return redirect("/sso-start")
    auth_redirect = _page_auth_redirect()
    if auth_redirect:
        return auth_redirect
    response = make_response(render_template("messenger.html"))
    # unsafe-eval: Emscripten embind (rlottie-wasm) uses new Function().
    # wasm-unsafe-eval: compile the rlottie .wasm binary.
    # connect-src needs the LiveKit URL too: its JS SDK opens its own
    # cross-origin WebSocket straight to the LiveKit server for group calls.
    connect_src = "'self'" + (f" {MESSENGER_LIVEKIT_URL}" if MESSENGER_LIVEKIT_URL else "")
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'wasm-unsafe-eval' 'unsafe-eval'; "
        "style-src 'self'; "
        "font-src 'self'; "
        "img-src 'self' data: blob:; "
        f"connect-src {connect_src}; "
        "worker-src 'self' blob:; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
        "form-action 'self'"
    )
    response.headers["Permissions-Policy"] = (
        "camera=(self), microphone=(self), display-capture=(self), geolocation=()"
    )
    # The <script>/<link> tags in this page carry cache-busting ?v= query
    # strings, but that only helps if the browser re-fetches THIS page to
    # see the new query string in the first place — without this header a
    # cached copy of the shell keeps pointing at old, equally-cached JS/CSS
    # forever, and a version bump silently does nothing for anyone who
    # already has the page cached.
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.route("/login")
def login_page():
    if PORTAL_SSO_REQUIRED:
        return redirect("/sso-start")
    if session.get("logged_in") and _current_user():
        return redirect(_safe_next_url(request.args.get("next")))
    return render_template("login.html", next_url=_safe_next_url(request.args.get("next")))


@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    if PORTAL_SSO_REQUIRED:
        return jsonify({"error": "Входите через корпоративный портал"}), 403
    client_key = request.remote_addr or "unknown"
    data = request.get_json(silent=True) or {}
    provider = str(data.get("provider") or "ldap").strip().lower()
    username = str(data.get("username") or "")
    account_key = _normalize_username(username) or "-"
    # Per-account limit keeps a password guesser from tripping the AD lockout
    # policy; the per-address limit is looser for shared workstations.
    if _distributed_rate_limited(
        "login-ip", client_key, LOGIN_RATE_LIMIT * 5, LOGIN_RATE_WINDOW_SEC
    ) or _distributed_rate_limited(
        "login-user", account_key, LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SEC
    ):
        return jsonify({"error": "Слишком много попыток. Повторите позже"}), 429

    password = str(data.get("password") or "")
    next_url = _safe_next_url(str(data.get("next") or ""))
    identity = None
    try:
        if provider == "local":
            identity = authenticate_local(username, password)
        elif provider == "ldap":
            identity = authenticate_ldap(username, password)
            if identity:
                identity = persist_external_identity(identity)
        else:
            return jsonify({"error": "Неизвестный способ входа"}), 400
    except LdapUnavailable:
        logger.warning("LDAP is unavailable for login from %s", client_key)
        return jsonify({"error": "Корпоративный вход временно недоступен"}), 503
    except Exception:
        logger.exception("Authentication backend failed")
        return jsonify({"error": "Не удалось выполнить вход"}), 500

    if not identity:
        logger.warning("Rejected %s login for %s", provider, _normalize_username(username))
        return jsonify({"error": "Неверный логин или пароль"}), 401

    if provider == "local":
        record_login(identity)
    login_identity(identity)
    session["csrf_token"] = secrets.token_urlsafe(32)
    _clear_rate_limit("login-ip", client_key)
    _clear_rate_limit("login-user", account_key)
    logger.info("Successful %s login for %s", provider, identity.username)
    return jsonify({"ok": True, "next": next_url})


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    username = _current_user()
    try:
        _revoke_messenger_tokens(username)
    except Exception:
        logger.exception("Could not revoke Messenger tokens for %s", username)
        return jsonify({"error": "Не удалось безопасно завершить сессию"}), 503
    logout_identity()
    return jsonify({"ok": True, "redirect_url": PORTAL_LOGOUT_URL if PORTAL_SSO_REQUIRED else "/login"})


@app.route("/api/auth/messenger-token", methods=["POST"])
def api_auth_messenger_token():
    username = _current_user()
    if not session.get("logged_in") or not username:
        return jsonify({"error": "Требуется авторизация"}), 401
    client_key = f"{request.remote_addr or 'unknown'}:{username}"
    if _distributed_rate_limited(
        "messenger-token",
        client_key,
        LOGIN_RATE_LIMIT,
        LOGIN_RATE_WINDOW_SEC,
    ):
        return jsonify({"error": "Слишком много запросов токена"}), 429
    try:
        token, expires_in = _issue_messenger_token(
            username,
            (session.get("display_name") or username).strip(),
        )
    except PermissionError:
        return jsonify({"error": "Учетная запись отключена"}), 403
    except RuntimeError:
        logger.error("Messenger token signing is not configured")
        return jsonify({"error": "Messenger временно недоступен"}), 503
    except Exception:
        logger.exception("Messenger token issuance failed for %s", username)
        return jsonify({"error": "Не удалось создать Messenger-сессию"}), 503
    return jsonify(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "ws_url": "/api/messenger/v1/ws",
        }
    )


def _external_base_url() -> str:
    host = (request.headers.get("X-Forwarded-Host") or request.host or "").strip()
    scheme = (request.headers.get("X-Forwarded-Proto") or request.scheme or "http").strip()
    if not host:
        return request.url_root.rstrip("/")
    return f"{scheme}://{host}".rstrip("/")




@app.route("/sso-login")
def sso_login():
    state = ""
    if PORTAL_SSO_REQUIRED:
        state = (request.args.get("state") or "").strip()
        stored = session.pop("portal_sso_state", None)
        started = session.pop("portal_sso_started", None)
        if (not state.isascii() or not stored or not hmac.compare_digest(state, stored)
                or not isinstance(started, int) or time.time() - started > AI_SSO_MAX_AGE_SEC * 2
                or time.time() < started):
            return "SSO state verification failed", 403
    raw_username = (request.args.get("u") or "").strip()
    username = _normalize_username(raw_username)
    display_name = (request.args.get("d") or "").strip()
    ts_raw = (request.args.get("ts") or "").strip()
    sig = (request.args.get("sig") or "").strip()
    if not username or not display_name or not ts_raw or not sig:
        return "SSO parameters are missing", 400
    if not _verify_sso_payload(raw_username, display_name, ts_raw, sig, state):
        return "SSO verification failed", 403
    identity = persist_external_identity(
        Identity(username, display_name, ("user",), "sso")
    )
    if identity is None:
        return "SSO login is not allowed for this account", 403
    login_identity(identity)
    session["csrf_token"] = secrets.token_urlsafe(32)
    if PORTAL_SSO_REQUIRED:
        session["sso_page_ready"] = True
    return redirect("/messenger")


@app.route("/sso-start")
def sso_start():
    if not PORTAL_SSO_REQUIRED:
        return redirect("/messenger")
    username = _current_user()
    if username:
        try:
            _revoke_messenger_tokens(username)
        except Exception:
            logger.exception("Could not revoke Messenger tokens during SSO refresh")
            return "Could not safely refresh session", 503
    session.clear()
    state = secrets.token_urlsafe(32)
    session["portal_sso_state"] = state
    session["portal_sso_started"] = int(time.time())
    callback = f"{UNIVERSALAI_PUBLIC_URL}/sso-login?{urllib.parse.urlencode({'state': state})}"
    destination = f"{PORTAL_LOGIN_URL}?{urllib.parse.urlencode({'return_url': callback})}"
    response = redirect(destination)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.route("/sso-logout")
def sso_logout():
    if not PORTAL_SSO_REQUIRED:
        return redirect(PORTAL_LOGIN_URL)
    username = _current_user()
    if username:
        try:
            _revoke_messenger_tokens(username)
        except Exception:
            logger.exception("Could not revoke Messenger tokens during SSO logout")
            return "Could not safely end session", 503
    session.clear()
    requested_next = (request.args.get("next") or "").strip()
    destination = (
        PORTAL_SSO_LOGOUT_CONTINUE_URL
        if requested_next and requested_next == PORTAL_SSO_LOGOUT_CONTINUE_URL
        else PORTAL_LOGIN_URL
    )
    response = redirect(destination)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/me")
def api_me():
    if not session.get("csrf_token"):
        session["csrf_token"] = secrets.token_urlsafe(32)
    username = _normalize_username(session.get("username") or "")
    anonymous = bool(session.get("anonymous_id"))
    display_name = (
        session.get("display_name")
        or username
        or ("Локальный пользователь" if anonymous else "")
    ).strip()
    avatar_url = None
    if username and session.get("logged_in"):
        avatar_row = _fetch_user_avatar(username)
        if avatar_row and avatar_row.get("avatar_path"):
            avatar_url = _avatar_public_url(username, avatar_row.get("avatar_updated_at"))
    return jsonify({
        "logged_in": bool(session.get("logged_in")),
        "anonymous": anonymous,
        "admin": can_manage_platform(),
        "roles": sorted(current_roles()) if session.get("logged_in") else [],
        "auth_source": session.get("auth_source", ""),
        "csrf_token": session["csrf_token"],
        "username": username,
        "display_name": display_name,
        "avatar_url": avatar_url,
    })


@app.route("/api/avatars/<username>")
def api_avatar_get(username):
    if not session.get("logged_in") or not _current_user():
        return jsonify({"error": "Требуется авторизация"}), 401
    normalized = _normalize_username(username)
    if not normalized:
        return jsonify({"error": "Пользователь не найден"}), 404
    row = _fetch_user_avatar(normalized)
    if not row or not row.get("avatar_path"):
        return jsonify({"error": "Аватар не найден"}), 404
    path = row["avatar_path"]
    if not os.path.isfile(path):
        return jsonify({"error": "Аватар не найден"}), 404
    response = send_from_directory(os.path.dirname(path), os.path.basename(path), conditional=True)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@app.route("/api/me/avatar", methods=["POST"])
def api_me_avatar_upload():
    username = _current_user()
    if not session.get("logged_in") or not username or username.startswith("anonymous:"):
        return jsonify({"error": "Требуется авторизация"}), 401
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "Файл не передан"}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in AVATAR_EXTENSIONS:
        return jsonify({"error": "Поддерживаются JPG, PNG, WEBP и GIF"}), 400
    tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        file.save(tmp_path)
        if os.path.getsize(tmp_path) > AVATAR_MAX_BYTES:
            return jsonify({"error": "Файл слишком большой (максимум 5 МБ)"}), 400
        if not validate_image_signature(tmp_path):
            return jsonify({"error": "Файл не является допустимым изображением"}), 400
        out_path, _out_ext = _process_avatar_upload(username, tmp_path, ext)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users
                    SET avatar_path=%s, avatar_updated_at=NOW(), updated_at=NOW()
                    WHERE username=%s
                    RETURNING avatar_updated_at
                    """,
                    (out_path, username),
                )
                row = cur.fetchone()
            conn.commit()
        avatar_url = _avatar_public_url(username, row["avatar_updated_at"])
        return jsonify({"ok": True, "avatar_url": avatar_url})
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.route("/api/me/avatar", methods=["DELETE"])
def api_me_avatar_delete():
    username = _current_user()
    if not session.get("logged_in") or not username or username.startswith("anonymous:"):
        return jsonify({"error": "Требуется авторизация"}), 401
    _remove_avatar_files(username)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET avatar_path=NULL, avatar_updated_at=NULL, updated_at=NOW()
                WHERE username=%s
                """,
                (username,),
            )
        conn.commit()
    return jsonify({"ok": True, "avatar_url": None})


@app.route("/back.png")
def bg_image():
    return send_from_directory(ROOT_DIR, "back.png")

@app.route("/ico.png")
def ico_image():
    return send_from_directory(ROOT_DIR, "ico.png")

@app.route("/name.png")
def name_image():
    return send_from_directory(ROOT_DIR, "name.png")



if __name__ == "__main__":
    validate_runtime_config()
    init_db()
    print("UniversalAI: http://localhost:5000")
    app.run(
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "5000")),
        debug=False,
        threaded=True,
    )
