import logging
import os
import threading

from auth_ldap import DirectoryUser, LdapConfig, LdapUnavailable, fetch_directory
from db import get_db

logger = logging.getLogger("universalai.ad_sync")

SYNC_INTERVAL_SEC = max(60, int(float(os.environ.get("LDAP_SYNC_INTERVAL_MIN", "15")) * 60))

_UPSERT = """
INSERT INTO users
  (username, display_name, auth_source, roles, is_active, ad_disabled,
   department, title, email, phone, office, external_dn, synced_at)
VALUES (%s, %s, 'ldap', %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
ON CONFLICT (username) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  auth_source  = 'ldap',
  roles        = EXCLUDED.roles,
  -- AD can switch an account off, and back on only if AD switched it off;
  -- a manual block from the messenger admin panel survives every sync.
  is_active    = (NOT EXCLUDED.ad_disabled) AND (users.is_active OR users.ad_disabled),
  messenger_token_version = users.messenger_token_version
    + CASE WHEN users.is_active AND EXCLUDED.ad_disabled THEN 1 ELSE 0 END,
  ad_disabled  = EXCLUDED.ad_disabled,
  department   = EXCLUDED.department,
  title        = EXCLUDED.title,
  email        = EXCLUDED.email,
  phone        = EXCLUDED.phone,
  office       = EXCLUDED.office,
  external_dn  = EXCLUDED.external_dn,
  synced_at    = NOW(),
  updated_at   = NOW()
WHERE users.auth_source <> 'local'
"""

_DISABLE_MISSING = """
UPDATE users
SET is_active = FALSE,
    ad_disabled = TRUE,
    messenger_token_version = messenger_token_version + CASE WHEN is_active THEN 1 ELSE 0 END,
    updated_at = NOW()
WHERE auth_source = 'ldap' AND NOT (username = ANY(%s)) AND NOT ad_disabled
RETURNING username
"""


def apply_directory(users: list[DirectoryUser], admins: set[str]) -> dict:
    if not users:
        # An empty result means a broken filter or base DN, not a company with
        # no employees — never mass-disable on it.
        raise LdapUnavailable("AD returned no users; sync skipped")
    seen = []
    with get_db() as conn:
        with conn.cursor() as cur:
            for user in users:
                roles = ["admin", "user"] if user.username in admins else ["user"]
                cur.execute(
                    _UPSERT,
                    (
                        user.username, user.display_name, roles,
                        not user.disabled, user.disabled,
                        user.department, user.title, user.email, user.phone, user.office,
                        user.dn,
                    ),
                )
                seen.append(user.username)
            cur.execute(_DISABLE_MISSING, (seen,))
            removed = [row["username"] for row in cur.fetchall()]
        conn.commit()
    return {
        "total": len(users),
        "disabled": sum(1 for user in users if user.disabled),
        "removed": removed,
    }


def sync_once(config: LdapConfig | None = None) -> dict:
    config = config or LdapConfig.from_env()
    users, admins = fetch_directory(config)
    return apply_directory(users, admins)


def _loop(config: LdapConfig) -> None:
    stop = threading.Event()
    while True:
        try:
            result = sync_once(config)
            logger.info(
                "AD sync: %s accounts, %s disabled in AD, %s no longer in AD",
                result["total"], result["disabled"], len(result["removed"]),
            )
        except LdapUnavailable as exc:
            logger.warning("%s", exc)
        except Exception:
            logger.exception("AD sync failed")
        stop.wait(SYNC_INTERVAL_SEC)


def start_background_sync() -> bool:
    config = LdapConfig.from_env()
    if not config.enabled:
        logger.info("AD sync is off: LDAP is not configured")
        return False
    threading.Thread(target=_loop, args=(config,), name="ad-sync", daemon=True).start()
    return True
