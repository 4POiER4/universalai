"""Prepare UniversalAI production settings and activate its portal settings."""

import argparse
import ipaddress
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


MESSENGER_ENV = Path(__file__).resolve().parents[1] / '.env.production'
SETTING = re.compile(r'^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$')


def read_settings(lines):
    values = {}
    for line in lines:
        if match := SETTING.fullmatch(line):
            raw = match[2].strip()
            values[match[1]] = raw.strip('"\'')
    return values


def update_settings(path, lines, updates):
    retained = []
    for line in lines:
        match = SETTING.fullmatch(line)
        if not match or match[1] not in updates:
            retained.append(line)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write('\n'.join([*retained, *(f'{key}={value}' for key, value in updates.items())]) + '\n')
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def origin(authority):
    host, separator, port = authority.lower().partition(':')
    label = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
    message = 'Укажите DNS-имя или IPv4, при необходимости с портом, без схемы и пути.'
    if len(host) > 253 or not re.fullmatch(rf'{label}(?:\.{label})+', host):
        raise argparse.ArgumentTypeError(message)
    if host.replace('.', '').isdigit():
        try:
            ipaddress.IPv4Address(host)
        except ipaddress.AddressValueError:
            raise argparse.ArgumentTypeError(message) from None
    if separator and (not re.fullmatch(r'[1-9][0-9]{0,4}', port) or int(port) > 65535):
        raise argparse.ArgumentTypeError('Укажите порт в диапазоне 1–65535.')
    return f'https://{host}:{port}' if separator and port != '443' else f'https://{host}'


def prepare(args):
    if not 1024 <= args.gateway_port <= 65535:
        raise SystemExit('Укажите порт Docker-шлюза в диапазоне 1024–65535.')
    lines = MESSENGER_ENV.read_text(encoding='utf-8').splitlines() if MESSENGER_ENV.is_file() else []
    current = read_settings(lines)
    if current and current.get('PORTAL_SSO_REQUIRED') != 'true':
        raise SystemExit('Существующий production-конфиг UniversalAI использует другой режим входа.')

    names = (
        'POSTGRES_PASSWORD', 'MESSENGER_DB_PASSWORD', 'AI_FLASK_SECRET',
        'AI_SSO_SHARED_SECRET', 'MESSENGER_LIVEKIT_API_KEY',
        'MESSENGER_LIVEKIT_API_SECRET', 'BACKUP_ENCRYPTION_PASSPHRASE',
    )
    updates = {key: secrets.token_hex(32) for key in names if not current.get(key)}
    jwt_names = ('MESSENGER_JWT_PRIVATE_KEY', 'MESSENGER_JWT_PUBLIC_KEY')
    if not all(current.get(key) for key in jwt_names):
        if any(current.get(key) for key in jwt_names):
            raise SystemExit('В production-конфиге должен присутствовать полный набор ключей JWT.')
        private = subprocess.check_output(['openssl', 'genpkey', '-algorithm', 'ED25519'])
        public = subprocess.check_output(['openssl', 'pkey', '-pubout'], input=private)
        updates.update({key: pem.decode('ascii').strip().replace('\n', '\\n')
                        for key, pem in zip(jwt_names, (private, public))})

    public = urlsplit(args.host)
    updates.update({
        'APP_ENV': 'production',
        'PORTAL_SSO_REQUIRED': 'true',
        'SESSION_COOKIE_SECURE': 'true',
        'UNIVERSALAI_PUBLIC_URL': args.host,
        'UNIVERSALAI_GATEWAY_PORT': args.gateway_port,
        'PORTAL_LOGIN_URL': f'{args.portal_host}/login',
        'PORTAL_LOGOUT_URL': f'{args.portal_host}/logout',
        'MESSENGER_ALLOWED_ORIGINS': args.host,
        'MESSENGER_ALLOWED_HOSTS': f'localhost,127.0.0.1,messenger-api,{public.hostname}',
        'MESSENGER_LIVEKIT_URL': f'wss://{public.netloc}/livekit',
        'MESSENGER_LIVEKIT_EXTERNAL_IP': args.livekit_ip,
    })
    update_settings(MESSENGER_ENV, lines, updates)
    print(f'Production-конфиг UniversalAI подготовлен: {MESSENGER_ENV}')


def messenger_portal_settings():
    if not MESSENGER_ENV.is_file():
        raise SystemExit('Сначала выполните prepare для UniversalAI.')
    messenger = read_settings(MESSENGER_ENV.read_text(encoding='utf-8').splitlines())
    url = messenger.get('UNIVERSALAI_PUBLIC_URL', '')
    return {
        'UNIVERSALAI_PUBLIC_URL': url,
        'UNIVERSALAI_CALLBACK_URL': f'{url}/sso-login',
        'UNIVERSALAI_SSO_SHARED_SECRET': messenger.get('AI_SSO_SHARED_SECRET', ''),
    }


def validate_portal_settings(values):
    url = values.get('UNIVERSALAI_PUBLIC_URL', '')
    callback = values.get('UNIVERSALAI_CALLBACK_URL', '')
    secret = values.get('UNIVERSALAI_SSO_SHARED_SECRET', '')
    try:
        canonical = origin(url.removeprefix('https://'))
    except argparse.ArgumentTypeError:
        canonical = None
    if (url != canonical
            or callback != f'{url}/sso-login'
            or not re.fullmatch(r'[A-Za-z0-9_-]{32,}', secret)):
        raise SystemExit('Проверьте HTTPS-адрес, адрес возврата и секрет SSO UniversalAI.')
    return {
        'UNIVERSALAI_PUBLIC_URL': url,
        'UNIVERSALAI_CALLBACK_URL': callback,
        'UNIVERSALAI_SSO_SHARED_SECRET': secret,
    }


def export_sso(args):
    settings = validate_portal_settings(messenger_portal_settings())
    try:
        with args.output.open('x', encoding='utf-8') as output:
            output.writelines(f'{key}={value}\n' for key, value in settings.items())
    except FileExistsError:
        raise SystemExit(f'Файл уже существует: {args.output}. Для экспорта укажите новый путь.') from None
    print(f'Настройки SSO для переноса на портал записаны: {args.output}')


def activate(args):
    source = (read_settings(args.sso_env.read_text(encoding='utf-8').splitlines())
              if args.sso_env else messenger_portal_settings())
    updates = validate_portal_settings(source)

    portal_env = args.portal_env.resolve(strict=True)
    lines = portal_env.read_text(encoding='utf-8').splitlines()
    portal = read_settings(lines)
    if not portal.get('LDAP_BIND_DN') or not portal.get('LDAP_BIND_PASSWORD'):
        raise SystemExit('Укажите действующий .env.production портала с настройками AD.')
    if updates['UNIVERSALAI_SSO_SHARED_SECRET'] == portal.get('AI_SSO_SHARED_SECRET'):
        raise SystemExit('Для UniversalAI требуется отдельный секрет SSO.')
    if any(portal.get(key) != value for key, value in updates.items()):
        with tempfile.NamedTemporaryFile(prefix=f'{portal_env.name}.before-universalai-',
                                         dir=portal_env.parent, delete=False) as backup:
            with portal_env.open('rb') as source:
                shutil.copyfileobj(source, backup)
        print(f'Резервная копия конфигурации портала: {backup.name}')
        update_settings(portal_env, lines, updates)
    print('Настройки SSO UniversalAI записаны в production-конфигурацию портала.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--host', type=origin, default='universalai.example.com')
    prep.add_argument('--portal-host', type=origin, default='portal.example.com')
    prep.add_argument('--livekit-ip', type=ipaddress.IPv4Address, required=True)
    prep.add_argument('--gateway-port', type=int, default=8085)
    prep.set_defaults(run=prepare)
    export = commands.add_parser('export-sso')
    export.add_argument('output', type=Path)
    export.set_defaults(run=export_sso)
    setup = commands.add_parser('activate')
    setup.add_argument('portal_env', type=Path)
    setup.add_argument('--sso-env', type=Path)
    setup.set_defaults(run=activate)
    args = parser.parse_args()
    os.umask(0o077)
    args.run(args)


if __name__ == '__main__':
    main()
