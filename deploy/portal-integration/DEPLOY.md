# UniversalAI на портале

Адреса `192.0.2.*`, домены `*.example.com` и имена SSH-пользователей в этом руководстве служат примерами. Перед запуском подставьте адреса, сертификаты и учётные записи своего развёртывания. Артефакты можно собрать из клона командой `python3 scripts/package_deployment.py`.

| Сервер | Что устанавливается | Как подключиться |
| --- | --- | --- |
| `192.0.2.125` | Контейнеры UniversalAI и HTTPS для чата | `ssh chatadmin@192.0.2.125` |
| `192.0.2.81` | Патч работающего портала и настройки SSO | `ssh portaladmin@192.0.2.81` |

Для чата используется предполагаемый адрес `https://universalai.example.com`. Добавьте внутреннюю DNS-запись на `192.0.2.125` и сертификат на это имя, которому доверяют рабочие компьютеры. Nginx на сервере чата принимает HTTPS на `443`, шлюз контейнеров слушает `127.0.0.1:8085`. Для запуска нужен Docker Compose v2.24.4 или новее. Рабочим компьютерам нужен доступ по HTTPS к обоим сайтам.

Пользователи открывают плитку **UniversalAI** в разделе **Сервисы**. Портал проверяет учётную запись в AD и передаёт чату подтверждённый логин и имя. Действующие LDAP-настройки остаются в серверном `.env.production` портала. При наличии сессии портал сразу открывает чат. Пользователи появляются в справочнике чата после первого входа.

## Доступ по IP и порту без новой DNS-записи

Для уже установленного чата подготовлен адрес **`https://192.0.2.125:8443`**. Порт `8443` принимает клиентский HTTPS на хосте, Nginx передаёт запросы в существующий шлюз на `127.0.0.1:8085`. Для входа через портал и работы браузерного чата нужен доверенный HTTPS. [Требования к защищённому контексту браузера](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts)

### Обновите установленный чат

На ноутбуке в PowerShell из папки с обновлением выполните:

```powershell
scp.exe '.\universalai-port-update.tar.gz' 'chatadmin@192.0.2.125:~/'
ssh.exe chatadmin@192.0.2.125
```

На `192.0.2.125` обновите файлы чата и адрес SSO. Команда `prepare` сохранит сгенерированные ранее секреты и настройки базы.

```bash
cd "$HOME/universalai-release/UniverslaAI"
sudo ss -ltn '( sport = :8443 )'
tar -xzf "$HOME/universalai-port-update.tar.gz"
chmod 0644 deploy/nginx.production.conf.example deploy/nginx.proxy-listener.conf
python3 deploy/portal-configure.py prepare --host 192.0.2.125:8443 --livekit-ip 192.0.2.125
ua=(docker compose -p universalai-production --env-file .env.production \
  -f docker-compose.yml -f docker-compose.production.yml -f docker-compose.portal.yml)
"${ua[@]}" up -d --build --wait --wait-timeout 300 app messenger-api
"${ua[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 60 gateway
curl -fsS http://127.0.0.1:8085/health
```

### Подключите HTTPS на `8443`

Для доступа по IP поставьте доверенный сертификат с `IP Address:192.0.2.125` в SAN. Поместите полную цепочку в `/etc/nginx/ssl/universalai-ip/server.crt`, а ключ — в `/etc/nginx/ssl/universalai-ip/server.key` с правами `600`. Если требуется выпуск через корпоративный УЦ, запрос на сертификат можно создать на сервере. Команда `test` защищает уже существующий закрытый ключ.

```bash
sudo sh -eu <<'SH'
dir=/etc/nginx/ssl/universalai-ip
install -d -m 750 "$dir"
test ! -e "$dir/server.key"
umask 077
openssl req -new -newkey rsa:3072 -noenc -subj '/CN=192.0.2.125' \
  -addext 'subjectAltName=IP:192.0.2.125' \
  -keyout "$dir/server.key" -out "$dir/server.csr"
SH
```

Если на ноутбуке уже работает доверенный `https://ai.example.com`, можно использовать его имя и сертификат на отдельном порту **`https://ai.example.com:8443`**. В этом случае выполните `prepare --host ai.example.com:8443 --livekit-ip 192.0.2.125` с обновлённым скриптом и пересоздайте указанные выше контейнеры. В `deploy/nginx.port-site.conf.example` замените `server_name` на `ai.example.com` и укажите пути действующего сертификата и ключа. Сайт чата использует отдельную сессию `universalai_session`.

Выбранную конфигурацию подключите отдельным сайтом Nginx.

```bash
sudo install -m 644 deploy/nginx.port-site.conf.example /etc/nginx/sites-available/universalai-port.conf
sudo ln -s /etc/nginx/sites-available/universalai-port.conf /etc/nginx/sites-enabled/universalai-port.conf
sudo nginx -t && sudo systemctl reload nginx
```

На ноутбуке проверьте выбранный адрес. Для варианта с действующим именем замените IP в URL на `ai.example.com`.

```powershell
curl.exe -fsS https://192.0.2.125:8443/health
```

### Обновите настройки SSO на портале

После ответа `{"status":"ok"}` экспортируйте параметры для нового адреса на `192.0.2.125`.

```bash
cd "$HOME/universalai-release/UniverslaAI"
python3 deploy/portal-configure.py export-sso "$HOME/.universalai-portal-sso-8443.env"
```

На ноутбуке повторите перенос из шага 4 с обновлённым `universalai-portal-update.tar.gz`. В первой команде `scp.exe` для выгрузки секрета укажите источник `chatadmin@192.0.2.125:.universalai-portal-sso-8443.env`. Имя локального файла `$sso` и итоговый путь `~/.universalai-portal-sso.env` на `192.0.2.81` сохраните из инструкции. Затем распакуйте обновлённый архив на `0.81` и выполните шаг 5. Адрес обратного перехода включает `:8443`, действующий секрет БелнипиAI сохраняется.

## 1. Скопируйте пакет чата на `192.0.2.125`

С компьютера, с которого подключаетесь по SSH, скопируйте архив и откройте сеанс.

```bash
scp universalai-portal-release.tar.gz chatadmin@192.0.2.125:~/
ssh chatadmin@192.0.2.125
```

Распакуйте архив в сеансе на сервере чата.

```bash
umask 077
mkdir -p "$HOME/universalai-release"
tar -xzf "$HOME/universalai-portal-release.tar.gz" -C "$HOME/universalai-release"
cd "$HOME/universalai-release/UniverslaAI"
chmod 0644 deploy/nginx.production.conf.example deploy/nginx.proxy-listener.conf
```

Шлюз Nginx читает эти два файла от имени непривилегированного пользователя контейнера. После распаковки с `umask 077` файлы могут получить права `600`, поэтому откройте чтение для обоих файлов конфигурации. [Образ Nginx Unprivileged](https://github.com/nginx/docker-nginx-unprivileged#nginx-unprivileged-docker-image)

## 2. Запустите UniversalAI

Перед запуском проверьте, свободны ли `8085`, `7881/tcp` и `7882/udp`.

```bash
sudo ss -lntup '( sport = :8085 or sport = :7881 or sport = :7882 )'
python3 deploy/portal-configure.py prepare --livekit-ip 192.0.2.125

messenger_compose=(docker compose -p universalai-production --env-file .env.production \
  -f docker-compose.yml -f docker-compose.production.yml -f docker-compose.portal.yml)
"${messenger_compose[@]}" config --quiet
"${messenger_compose[@]}" up -d --build --wait --wait-timeout 300
"${messenger_compose[@]}" ps
curl -fsS http://127.0.0.1:8085/health
```

Команда `prepare` создаёт секреты непосредственно на сервере в файле с правами `600`. Повторный запуск сохраняет сгенерированные секреты. При занятом `8085` укажите свободный порт параметром `--gateway-port` и задайте тот же порт в `proxy_pass` конфигурации Nginx. Для звонков клиентам нужен доступ к `192.0.2.125` по `7881/tcp` и `7882/udp`.

## 3. Подключите HTTPS на `192.0.2.125`

Сначала подготовьте PEM-сертификат с цепочкой и соответствующий закрытый ключ. Для `universalai.example.com` подойдёт сертификат с этим именем в SAN или доверенный сертификат `*.example.com`. В конфигурации `deploy/nginx.portal-site.conf.example` укажите фактические пути в `ssl_certificate` и `ssl_certificate_key`. По умолчанию используются `/etc/nginx/ssl/universalai.example.com.crt` и `/etc/nginx/ssl/universalai.example.com.key`. Если файлов ещё нет, `nginx -t` завершится ошибкой, а Nginx продолжит обслуживать запросы по текущей конфигурации. [Документация Nginx](https://nginx.org/en/docs/http/configuring_https_servers.html)

После установки сертификата и ключа включите сайт. Следующие команды подходят для Debian и Ubuntu с каталогом `sites-enabled`. Если сайт уже добавлен, измените пути в `/etc/nginx/sites-available/universalai.conf` и начните с `nginx -t`.

```bash
sudo install -m 644 deploy/nginx.portal-site.conf.example /etc/nginx/sites-available/universalai.conf
sudo ln -s /etc/nginx/sites-available/universalai.conf /etc/nginx/sites-enabled/universalai.conf
sudo nginx -t && sudo systemctl reload nginx
curl -fsS --resolve universalai.example.com:443:192.0.2.125 \
  https://universalai.example.com/health
```

Ответ проверки должен содержать `{"status":"ok"}`. Если назначили другое имя, задайте его параметром `--host` для `prepare` и замените имя в конфигурации Nginx и проверках. По умолчанию чат отправляет пользователей на `https://portal.example.com`. Если рабочий адрес портала другой, задайте его DNS-имя параметром `--portal-host` при `prepare`.

### Проверка с рабочего компьютера Windows

На ноутбуке в PowerShell можно проверить HTTPS до регистрации имени. `--resolve` действует только на этот запрос и сохраняет проверку сертификата для `universalai.example.com`. [Документация curl](https://curl.se/docs/manpage.html#--resolve)

```powershell
curl.exe -fsS --resolve universalai.example.com:443:192.0.2.125 https://universalai.example.com/health
```

Для сотрудников добавьте в корпоративном DNS запись `A` для `universalai.example.com` с адресом `192.0.2.125`. Если корпоративный DNS работает на Windows Server, в существующей зоне `example.com` создайте узел `universalai`. [Настройка записи A в Windows Server](https://learn.microsoft.com/windows-server/networking/dns/manage-resource-records#create-a-host-record)

После регистрации имени проверьте DNS и HTTPS с рабочего ноутбука.

```powershell
Resolve-DnsName universalai.example.com -Type A -DnsOnly
curl.exe -fsS https://universalai.example.com/health
```

DNS должен вернуть `192.0.2.125`, HTTPS — `{"status":"ok"}`. Ошибка `6` означает сбой разрешения имени, ошибка `60` указывает на проверку сертификата. Для шага 5 дождитесь успешной проверки с рабочего компьютера.

### Можно ли использовать сертификат БелнипиAI

`ai.example.com` и `universalai.example.com` могут обслуживаться на одном IP и порту `443` с разными `server_name` в Nginx. Если БелнипиAI уже работает по HTTPS на `192.0.2.125`, проверьте, покрывает ли его сертификат имя чата. [Настройка нескольких HTTPS-сайтов в Nginx](https://nginx.org/en/docs/http/configuring_https_servers.html#name_based_https_servers)

На `192.0.2.125` выполните:

```bash
openssl s_client -connect 192.0.2.125:443 -servername ai.example.com </dev/null 2>/dev/null |
  openssl x509 -noout -subject -issuer -dates -ext subjectAltName \
    -checkhost universalai.example.com
```

На рабочем ноутбуке проверьте доверие Windows к сертификату действующего сайта. Строка с HTTP-статусом подтверждает, что TLS-проверка прошла.

```powershell
curl.exe -sS --resolve ai.example.com:443:192.0.2.125 -o NUL -w "HTTP %{http_code}\n" https://ai.example.com/
```

Если сертификат покрывает имя чата и Windows ему доверяет, укажите в `/etc/nginx/sites-available/universalai.conf` те же пути `ssl_certificate` и `ssl_certificate_key`, что в конфигурации `ai.example.com`. Оставьте `server_name universalai.example.com` и прокси на `127.0.0.1:8085`. Выполните `sudo nginx -t && sudo systemctl reload nginx` и повторите запрос `/health` с ноутбука. Если сертификат покрывает только `ai.example.com`, получите сертификат, в SAN которого есть имя чата или `*.example.com`, и установите полную цепочку, которой доверяют рабочие компьютеры.

### Временная проверка, пока выпускается рабочий сертификат

Если конфигурация сайта уже добавлена и указывает на пути по умолчанию, создайте отдельный самоподписанный сертификат для проверки `/health`. Для `curl` скопируйте публичный сертификат во временный файл, доступный текущему пользователю. Это позволяет оставить права на каталог Nginx в их текущем виде. [Документация curl](https://curl.se/docs/sslcerts.html)

```bash
sudo sh -eu <<'SH'
dir=/etc/nginx/ssl/universalai-test
install -d -m 755 "$dir"
if [ ! -e "$dir/server.crt" ] && [ ! -e "$dir/server.key" ]; then
  umask 077
  openssl req -x509 -newkey rsa:3072 -noenc -days 14 \
    -subj '/CN=universalai.example.com' \
    -addext 'subjectAltName=DNS:universalai.example.com' \
    -addext 'basicConstraints=critical,CA:FALSE' \
    -keyout "$dir/server.key" -out "$dir/server.crt"
  chmod 644 "$dir/server.crt"
fi
test -s "$dir/server.crt"
test -s "$dir/server.key"
sed -i \
  -e "s|/etc/nginx/ssl/universalai\.energoprom\.by\.crt|$dir/server.crt|" \
  -e "s|/etc/nginx/ssl/universalai\.energoprom\.by\.key|$dir/server.key|" \
  /etc/nginx/sites-available/universalai.conf
nginx -t
systemctl reload nginx
SH
```

Если `nginx -t` уже прошёл и Nginx перечитал конфигурацию, достаточно следующей проверки.

```bash
(
  set -e
  cert=$(mktemp "$HOME/universalai-test.XXXXXX.crt")
  trap 'rm -f -- "$cert"' EXIT
  sudo cat /etc/nginx/ssl/universalai-test/server.crt > "$cert"
  curl -fsS --cacert "$cert" \
    --resolve universalai.example.com:443:192.0.2.125 \
    https://universalai.example.com/health
)
```

Для доступа сотрудников укажите пути рабочего сертификата и ключа в `/etc/nginx/sites-available/universalai.conf`, проверьте конфигурацию и перечитайте её. Затем проверьте HTTPS с рабочего компьютера и перейдите к включению SSO.

### Если HTTPS возвращает `502 Bad Gateway`

На `192.0.2.125` сравните адрес `proxy_pass` в Nginx с портом шлюза, который опубликовал Docker. Для порта по умолчанию локальный `/health` должен вернуть HTTP `200` и `{"status":"ok"}`.

```bash
cd "$HOME/universalai-release/UniverslaAI"
ua=(docker compose -p universalai-production --env-file .env.production \
  -f docker-compose.yml -f docker-compose.production.yml -f docker-compose.portal.yml)
sudo grep -n 'proxy_pass' /etc/nginx/sites-available/universalai.conf
"${ua[@]}" ps -a
"${ua[@]}" port gateway 8443
curl -sS -i --max-time 5 http://127.0.0.1:8085/health
```

- Если опубликован другой порт, проверьте `/health` на нём и укажите этот порт в `proxy_pass`. Затем выполните `sudo nginx -t && sudo systemctl reload nginx`.
- Если `gateway` перезапускается с `Permission denied` при чтении `/etc/nginx/nginx.conf` или `/etc/nginx/universalai-listener.conf`, выполните `chmod 0644 deploy/nginx.production.conf.example deploy/nginx.proxy-listener.conf`, затем `"${ua[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 60 gateway`.
- Если `gateway` не запущен, выполните `"${ua[@]}" up -d --wait --wait-timeout 300`. При ошибке проверьте `"${ua[@]}" ps -a` и `"${ua[@]}" logs --tail=40 gateway app messenger-api clamav`.
- Если локальный запрос тоже вернул `502`, проверьте логи `gateway` и `app`. Если локальный запрос вернул `200`, а HTTPS по-прежнему возвращает `502`, проверьте запись запроса `/health` в журнале Nginx на хосте.

## 4. Перенесите настройки SSO и патч на `192.0.2.81`

После проверки HTTPS экспортируйте настройки на сервере чата `192.0.2.125`.

```bash
cd "$HOME/universalai-release/UniverslaAI"
python3 deploy/portal-configure.py export-sso "$HOME/.universalai-portal-sso.env"
```

Файл содержит HTTPS-адрес чата, адрес возврата и общий секрет SSO. Права на файл `600`. С компьютера, на котором лежат архивы, перенесите файлы на портал и откройте SSH-сеанс.

### Windows, PowerShell

Откройте PowerShell в папке с `universalai-portal-update.tar.gz`. Для переноса используются `ssh.exe` и `scp.exe` из клиента OpenSSH для Windows. Временный каталог на портале доступен учётной записи `portaladmin`. [OpenSSH для Windows](https://learn.microsoft.com/windows-server/administration/openssh/openssh-overview)

```powershell
if (-not (Test-Path -LiteralPath '.\universalai-portal-update.tar.gz' -PathType Leaf)) {
    throw 'Откройте PowerShell в папке с архивом universalai-portal-update.tar.gz'
}
ssh.exe portaladmin@192.0.2.81 'install -d -m 700 .universalai-incoming'
if ($LASTEXITCODE -ne 0) { throw 'Проверьте SSH-доступ к порталу 192.0.2.81' }

$transfer = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ('universalai-' + [guid]::NewGuid().ToString('N')))
$sso = Join-Path $transfer.FullName '.universalai-portal-sso.env'
try {
    scp.exe 'chatadmin@192.0.2.125:.universalai-portal-sso.env' $sso
    if ($LASTEXITCODE -ne 0) { throw 'Проверьте экспорт SSO и SSH-доступ к 192.0.2.125' }
    scp.exe '.\universalai-portal-update.tar.gz' $sso 'portaladmin@192.0.2.81:.universalai-incoming/'
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка загрузки файлов на портал 192.0.2.81' }
} finally {
    if (Test-Path -LiteralPath $sso) { Remove-Item -LiteralPath $sso }
    Remove-Item -LiteralPath $transfer.FullName
}
ssh.exe portaladmin@192.0.2.81
```

В открывшемся SSH-сеансе на `192.0.2.81` переместите полученные файлы в домашний каталог.

```bash
chmod 600 "$HOME/.universalai-incoming/.universalai-portal-sso.env" &&
mv -- "$HOME/.universalai-incoming/universalai-portal-update.tar.gz" \
  "$HOME/.universalai-incoming/.universalai-portal-sso.env" "$HOME/" &&
rmdir -- "$HOME/.universalai-incoming"
ls -l "$HOME/universalai-portal-update.tar.gz" "$HOME/.universalai-portal-sso.env"
```

### Linux и macOS, Bash

```bash
transfer_dir=$(mktemp -d)
scp -p chatadmin@192.0.2.125:.universalai-portal-sso.env "$transfer_dir/" &&
scp -p universalai-portal-update.tar.gz "$transfer_dir/.universalai-portal-sso.env" \
  portaladmin@192.0.2.81:~/ &&
rm -- "$transfer_dir/.universalai-portal-sso.env" && rmdir "$transfer_dir"
ssh portaladmin@192.0.2.81
```

## 5. Включите плитку и единый вход на `192.0.2.81`

На сервере портала используйте действующий каталог портала с его базой и `.env.production`. Если путь неизвестен, проверьте рабочий каталог контейнера этой командой.

```bash
docker ps --filter publish=5004 \
  --format '{{.Names}} {{.Label "com.docker.compose.project.working_dir"}}'
```

```bash
umask 077
portal_update_dir="$HOME/universalai-portal-update"
mkdir -p "$portal_update_dir"
tar -xzf "$HOME/universalai-portal-update.tar.gz" -C "$portal_update_dir"
portal_dir="$HOME/PythonProject1111"
cd "$portal_dir"
git status --short
git apply --check "$portal_update_dir/portal-sso.patch" && git apply "$portal_update_dir/portal-sso.patch"
```

Если проверка патча завершилась ошибкой, проверьте `git apply --reverse --check "$portal_update_dir/portal-sso.patch"`. Успех означает, что патч уже применён. Если обе проверки завершились ошибкой, сопоставьте патч с версией и локальными изменениями портала перед обновлением.

```bash
portal_container=$(docker compose --env-file .env.production ps -q web)
portal_image=$(docker inspect --format '{{.Image}}' "$portal_container")
portal_tag=$(docker inspect --format '{{.Config.Image}}' "$portal_container")
docker image tag "$portal_image" portal-before-universalai:saved
python3 "$portal_update_dir/portal-configure.py" activate "$portal_dir/.env.production" \
  --sso-env "$HOME/.universalai-portal-sso.env"
bash deploy/update-docker-production.sh
curl -fsS http://127.0.0.1:5004/healthz
```

`activate` сохраняет резервную копию действующего `.env.production` и записывает только `UNIVERSALAI_PUBLIC_URL`, `UNIVERSALAI_CALLBACK_URL` и `UNIVERSALAI_SSO_SHARED_SECRET`. Действующие LDAP-настройки и параметры входа в БелнипиAI (`AI_ASSISTANT_*` и `AI_SSO_SHARED_SECRET`) сохраняются. Скрипт портала сохраняет копию SQLite перед пересозданием `web`. При необходимости отката восстановите выведенную копию `.env.production`, верните тег прежнего образа и пересоздайте сервис из этого образа.

```bash
install -m 600 /путь/к/.env.production.before-universalai-КОПИЯ .env.production
docker image tag portal-before-universalai:saved "$portal_tag"
docker compose --env-file .env.production up -d --no-build --force-recreate web
```

С рабочего компьютера откройте портал в приватном окне, войдите через AD и проверьте плитку **БелнипиAI**. Затем проверьте плитку **UniversalAI** и прямой вход через `https://universalai.example.com`. Чат должен открыться под вашим именем. Проверьте выход и обмен сообщениями между двумя разрешёнными учётными записями. После проверки удалите файл переноса `.universalai-portal-sso.env` из домашних каталогов на обоих серверах.
