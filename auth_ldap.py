import os
import ssl
from dataclasses import dataclass

from auth import Identity, normalize_username


class LdapUnavailable(RuntimeError):
    pass


# Active Directory: userAccountControl bit for a disabled account, and the
# LDAP_MATCHING_RULE_IN_CHAIN rule that resolves nested group membership.
UAC_ACCOUNT_DISABLE = 0x2
MATCHING_RULE_IN_CHAIN = "1.2.840.113556.1.4.1941"

USER_ATTRIBUTES = [
    "sAMAccountName",
    "distinguishedName",
    "displayName",
    "cn",
    "department",
    "title",
    "mail",
    "telephoneNumber",
    "physicalDeliveryOfficeName",
    "userAccountControl",
]


@dataclass(frozen=True)
class LdapConfig:
    uri: str
    base_dn: str
    bind_dn: str
    bind_password: str
    user_filter: str
    sync_filter: str
    admin_groups: frozenset[str]
    timeout: float
    validate_tls: bool

    @classmethod
    def from_env(cls) -> "LdapConfig":
        return cls(
            uri=os.environ.get("LDAP_URI", "").strip(),
            base_dn=os.environ.get("LDAP_BASE_DN", "").strip(),
            bind_dn=os.environ.get("LDAP_BIND_DN", "").strip(),
            bind_password=os.environ.get("LDAP_BIND_PASSWORD", ""),
            user_filter=os.environ.get(
                "LDAP_USER_FILTER", "(&(objectClass=user)(sAMAccountName={username}))"
            ).strip(),
            sync_filter=os.environ.get(
                "LDAP_SYNC_FILTER", "(&(objectCategory=person)(objectClass=user)(sAMAccountName=*))"
            ).strip(),
            admin_groups=frozenset(
                item.strip()
                for item in os.environ.get("LDAP_ADMIN_GROUPS", "").split(",")
                if item.strip()
            ),
            timeout=float(os.environ.get("LDAP_TIMEOUT_SEC", "5")),
            validate_tls=os.environ.get("LDAP_TLS_VALIDATE", "true").lower() == "true",
        )

    @property
    def enabled(self) -> bool:
        return bool(self.uri and self.base_dn and self.bind_dn and self.bind_password)


@dataclass(frozen=True)
class DirectoryUser:
    username: str
    dn: str
    display_name: str
    department: str
    title: str
    email: str
    phone: str
    office: str
    disabled: bool


def _ldap3():
    try:
        import ldap3
        from ldap3.core.exceptions import LDAPException
        from ldap3.utils.conv import escape_filter_chars
    except ImportError as exc:
        raise LdapUnavailable("ldap3 is not installed") from exc
    return ldap3, LDAPException, escape_filter_chars


def _server(config: LdapConfig):
    ldap3, _, _ = _ldap3()
    use_ssl = config.uri.lower().startswith("ldaps://")
    tls = None
    if use_ssl:
        tls = ldap3.Tls(validate=ssl.CERT_REQUIRED if config.validate_tls else ssl.CERT_NONE)
    return ldap3.Server(
        config.uri,
        use_ssl=use_ssl,
        tls=tls,
        get_info=ldap3.NONE,
        connect_timeout=config.timeout,
    )


def _service_connection(config: LdapConfig, server):
    ldap3, _, _ = _ldap3()
    return ldap3.Connection(
        server,
        user=config.bind_dn,
        password=config.bind_password,
        auto_bind=True,
        receive_timeout=config.timeout,
    )


def _text(entry, attribute: str) -> str:
    if attribute not in entry:
        return ""
    value = entry[attribute].value
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _directory_user(entry) -> DirectoryUser | None:
    username = normalize_username(_text(entry, "sAMAccountName"))
    if not username:
        return None
    try:
        uac = int(_text(entry, "userAccountControl") or 0)
    except ValueError:
        uac = 0
    return DirectoryUser(
        username=username,
        dn=str(entry.entry_dn),
        display_name=_text(entry, "displayName") or _text(entry, "cn") or username,
        department=_text(entry, "department"),
        title=_text(entry, "title"),
        email=_text(entry, "mail"),
        phone=_text(entry, "telephoneNumber"),
        office=_text(entry, "physicalDeliveryOfficeName"),
        disabled=bool(uac & UAC_ACCOUNT_DISABLE),
    )


def _admin_group_dns(connection, config: LdapConfig) -> list[str]:
    """LDAP_ADMIN_GROUPS accepts full DNs or plain group names (CN)."""
    _, _, escape = _ldap3()
    result = []
    for group in config.admin_groups:
        if "=" in group:
            result.append(group)
            continue
        connection.search(
            config.base_dn,
            f"(&(objectClass=group)(cn={escape(group)}))",
            attributes=["distinguishedName"],
            size_limit=5,
        )
        result.extend(str(entry.entry_dn) for entry in connection.entries)
    return result


def _is_admin(connection, config: LdapConfig, user_dn: str, group_dns: list[str]) -> bool:
    _, _, escape = _ldap3()
    for group_dn in group_dns:
        connection.search(
            user_dn,
            f"(memberOf:{MATCHING_RULE_IN_CHAIN}:={escape(group_dn)})",
            search_scope="BASE",
            attributes=["distinguishedName"],
        )
        if connection.entries:
            return True
    return False


def authenticate_ldap(raw_username: str, password: str) -> Identity | None:
    config = LdapConfig.from_env()
    username = normalize_username(raw_username)
    if not config.enabled:
        raise LdapUnavailable("LDAP is not configured")
    if not username or not password:
        return None

    ldap3, LDAPException, escape = _ldap3()
    server = _server(config)
    try:
        with _service_connection(config, server) as search_connection:
            search_filter = config.user_filter.replace("{username}", escape(username))
            found = search_connection.search(
                config.base_dn,
                search_filter,
                attributes=USER_ATTRIBUTES,
                size_limit=2,
            )
            if not found or len(search_connection.entries) != 1:
                return None
            user = _directory_user(search_connection.entries[0])
            if user is None or user.disabled:
                return None
            admin = _is_admin(
                search_connection, config, user.dn, _admin_group_dns(search_connection, config)
            )

        with ldap3.Connection(
            server,
            user=user.dn,
            password=password,
            auto_bind=True,
            receive_timeout=config.timeout,
        ):
            pass
    except LDAPException:
        return None
    except OSError as exc:
        raise LdapUnavailable("LDAP connection failed") from exc

    return Identity(
        username=username,
        display_name=user.display_name,
        roles=("admin", "user") if admin else ("user",),
        auth_source="ldap",
        department=user.department,
        title=user.title,
        email=user.email,
        phone=user.phone,
        office=user.office,
    )


def fetch_directory(config: LdapConfig) -> tuple[list[DirectoryUser], set[str]]:
    """All AD user accounts plus the usernames of (nested) admin-group members."""
    _, LDAPException, escape = _ldap3()
    users: list[DirectoryUser] = []
    admins: set[str] = set()
    try:
        with _service_connection(config, _server(config)) as connection:
            for item in connection.extend.standard.paged_search(
                config.base_dn,
                config.sync_filter,
                attributes=USER_ATTRIBUTES,
                paged_size=500,
                generator=True,
            ):
                if item.get("type") != "searchResEntry":
                    continue
                attrs = item.get("attributes", {})
                entry = _PagedEntry(item.get("dn", ""), attrs)
                user = _directory_user(entry)
                if user:
                    users.append(user)
            for group_dn in _admin_group_dns(connection, config):
                for item in connection.extend.standard.paged_search(
                    config.base_dn,
                    f"(&(objectClass=user)(memberOf:{MATCHING_RULE_IN_CHAIN}:={escape(group_dn)}))",
                    attributes=["sAMAccountName"],
                    paged_size=500,
                    generator=True,
                ):
                    if item.get("type") != "searchResEntry":
                        continue
                    name = item.get("attributes", {}).get("sAMAccountName")
                    if isinstance(name, list):
                        name = name[0] if name else ""
                    if name:
                        admins.add(normalize_username(str(name)))
    except LDAPException as exc:
        raise LdapUnavailable(f"LDAP directory sync failed: {exc.__class__.__name__}") from exc
    except OSError as exc:
        raise LdapUnavailable("LDAP connection failed") from exc
    return users, admins


class _PagedEntry:
    """Adapts a paged_search dict to the attribute access _directory_user uses."""

    class _Value:
        def __init__(self, value):
            self.value = value

    def __init__(self, dn: str, attributes: dict):
        self.entry_dn = dn
        self._attributes = attributes

    def __contains__(self, name: str) -> bool:
        return name in self._attributes

    def __getitem__(self, name: str):
        return self._Value(self._attributes[name])
