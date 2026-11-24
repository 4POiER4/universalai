# UniversalAI

Корпоративный защищённый мессенджер. Сервис на Flask отвечает за вход (AD/локальные учётки/SSO), сессии, аватары и выдачу коротких JWT; сам мессенджер — отдельный сервис на FastAPI/WebSocket с PostgreSQL, Redis, LiveKit (звонки) и ClamAV (проверка вложений).

## Возможности

- личные и групповые чаты, ответы, пересылка, редактирование и удаление сообщений;
- вложения с антивирусной проверкой, стикеры, реакции, закреплённые сообщения, поиск;
- статусы присутствия, «печатает…», отметки о прочтении;
- аудио- и видеозвонки 1:1 и групповые через LiveKit;
- админ-панель мессенджера и журнал аудита;
- прямой вход через Active Directory, локальные роли admin/user, SSO, CSRF-защита;
- десктоп-обёртка на Electron (`desktop/`).

## Развёртывание и вход через портал

Патч портала, настройки HTTPS и инструкции для адреса по DNS или по IP с портом `8443` находятся в [руководстве по развёртыванию](deploy/portal-integration/DEPLOY.md). Перед запуском подставьте адреса своей инфраструктуры.

Чтобы собрать три установочных архива из текущего коммита, выполните из каталога репозитория:

```bash
python3 scripts/package_deployment.py
```

Архивы появятся в `dist/`. Скрипт использует файлы из Git и пригоден для повторной сборки с `--ref` для выбранного коммита.

## Быстрый запуск через Docker

Понадобится Docker Desktop.

1. Создайте конфигурацию:

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Вставьте разные случайные значения в `POSTGRES_PASSWORD`, `MESSENGER_DB_PASSWORD`, `AI_FLASK_SECRET`, `LOCAL_ADMIN_PASSWORD`, `LOCAL_TEST_PASSWORD` и `BACKUP_ENCRYPTION_PASSPHRASE`. Для паролей PostgreSQL используйте URL-безопасные символы, потому что они входят в URL подключения.

Создайте отдельную пару ключей Messenger и перенесите две выведенные строки в `.env`:

```powershell
python scripts/generate_messenger_keys.py
```

2. Для локальной проверки оставьте `APP_ENV=development`, `ALLOW_ANONYMOUS=false` и `SESSION_COOKIE_SECURE=false`. Если AD пока недоступен, используйте локальные логины `admin` и `test` с паролями из `.env`.

3. Запустите:

```powershell
docker compose up --build -d
```

Откройте `http://localhost:5000` — сразу откроется мессенджер (или окно входа).

Статус платформы: `/health`, готовность БД — `/ready`. Мессенджер проверяется контейнерными `/health/live` и `/health/ready`.

PostgreSQL с хоста: `127.0.0.1:5433`.

Остановка:

```powershell
docker compose down
```

`docker compose down -v` также удаляет данные PostgreSQL и вложения.

## Локальный запуск платформы без Docker

Понадобятся Python 3.12 и PostgreSQL.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Переменные из `.env` не загружаются Python автоматически. Перед запуском задайте их в PowerShell или используйте собственный безопасный загрузчик окружения:

```powershell
$env:DATABASE_URL = "postgresql://postgres:password@localhost:5433/universalai"
$env:ALLOW_ANONYMOUS = "false"
$env:AI_FLASK_SECRET = "replace-with-a-random-secret"
$env:LOCAL_ADMIN_PASSWORD = "replace-with-a-strong-password"
$env:LOCAL_TEST_PASSWORD = "replace-with-a-different-password"
python wsgi.py
```

Схема создаётся автоматически при старте. Для разработки также можно выполнить `python app.py`.

## Messenger

Пользователь входит через общую AD/local-сессию платформы; браузер получает короткий Messenger JWT, хранит его только в памяти и аутентифицирует WebSocket первым сообщением. До отдельной аттестации запрещена передача информации ограниченного распространения.

Архитектура, роли, retention и acceptance gates описаны в:

- `docs/messenger-security-model.md`;
- `docs/messenger-requirements-matrix.md`;
- `docs/messenger-operations-runbook.md`;
- `docs/messenger-post-mvp-security.md`.

Зашифрованная резервная копия:

```powershell
docker compose --profile operations run --rm backup
```

## Active Directory, production и SSO

Для прямого AD-входа задайте `LDAP_URI`, `LDAP_BASE_DN`, `LDAP_BIND_DN`, `LDAP_BIND_PASSWORD` и при необходимости `LDAP_ADMIN_GROUPS`. В production разрешён только `ldaps://` с `LDAP_TLS_VALIDATE=true`. Пароль пользователя используется только для LDAP bind и не сохраняется.

Для production обязательно задайте:

- `APP_ENV=production`;
- `ALLOW_ANONYMOUS=false`;
- постоянный случайный `AI_FLASK_SECRET`;
- отдельные `MESSENGER_DB_PASSWORD`, `MESSENGER_JWT_PRIVATE_KEY` и `MESSENGER_JWT_PUBLIC_KEY`;
- точный HTTPS origin в `MESSENGER_ALLOWED_ORIGINS`;
- случайный `AI_SSO_SHARED_SECRET`, если нужен обратносовместимый SSO-переход;
- `SESSION_COOKIE_SECURE=true` за HTTPS reverse proxy;
- `LDAP_ADMIN_GROUPS=UniversalAI Admins`;
- секреты через менеджер секретов, а не в репозитории.

Для общего входа с порталом включите `PORTAL_SSO_REQUIRED=true` **после** того,
как мессенджер получит отдельный HTTPS-адрес с сертификатом, которому доверяют
браузеры и настольные клиенты. В `.env` UniversalAI задайте:

```dotenv
PORTAL_SSO_REQUIRED=true
UNIVERSALAI_PUBLIC_URL=https://messenger.example.local
PORTAL_LOGIN_URL=https://portal.example.com/login
PORTAL_LOGOUT_URL=https://portal.example.com/logout
PORTAL_SSO_LOGOUT_CONTINUE_URL=https://ai.example.com/sso-logout
AI_SSO_SHARED_SECRET=<отдельный случайный секрет длиной не менее 32 символов>
SESSION_COOKIE_SECURE=true
```

`UNIVERSALAI_PUBLIC_URL` здесь — пример, не назначенный адрес. На портале
`UNIVERSALAI_PUBLIC_URL` должен совпадать с ним, а
`UNIVERSALAI_SSO_SHARED_SECRET` — с `AI_SSO_SHARED_SECRET` мессенджера.
Секрет БелнипиAI для этого не переиспользуйте. После настройки портала
открывайте плитку UniversalAI или прямой адрес мессенджера: браузер и
Electron-обёртка пройдут через `/sso-start` → вход на портале →
`/sso-login` → `/messenger`. В этом режиме прямой ввод AD-пароля и локальный
вход в UniversalAI отключены; выход из мессенджера завершает сессию портала.
При выходе с портала браузер проходит через logout мессенджера и БелнипиAI.
Запрос по IP/HTTP сначала перенаправляется на канонический HTTPS-адрес
мессенджера, иначе cookie одноразового SSO-запроса осталась бы на IP.
LDAP-настройки UniversalAI при этом нужны только для собственной синхронизации
справочника сотрудников и ролей; пароль пользователя мессенджер не получает.

Возврат `/sso-login` содержит одноразовый `state` и подпись:

```text
/sso-login?state=<nonce>&u=<login>&d=<display-name>&ts=<unix-time>&sig=<hex-hmac>
```

В режиме обязательного входа через портал `sig` — HMAC-SHA256 строки
`u|d|ts|state` с ключом `AI_SSO_SHARED_SECRET`.
Время допускается в пределах `AI_SSO_MAX_AGE_SEC`; `state` должен совпасть
с запросом данного браузера и не может быть использован второй раз.

## Основные настройки

- `DATABASE_URL`, `POSTGRES_PASSWORD` — база данных;
- `AVATAR_MAX_MB` — предел размера аватара (по умолчанию 5);
- `MESSENGER_*` — параметры мессенджера и звонков;
- `LOG_LEVEL` — уровень журналирования.

Полный шаблон находится в `.env.example`.

## Проверки

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s services/messenger/tests -v
python -m py_compile app.py wsgi.py auth.py auth_ldap.py db.py ad_sync.py
node --check static/messenger/js/client.js
.\scripts\security_gate.ps1
```

## Диагностика

- База не готова: проверьте `docker compose logs postgres` и соответствие пароля.
- В production приложение не стартует намеренно, если отсутствуют обязательные секреты или включён анонимный доступ.
