# Деплой SKLAD на VPS с автообновлением через GitHub Actions

> Каждый `git push main` → GitHub Actions → SSH на VPS → `git pull` + перезапуск → готово (~30 сек)

---

## Быстрый старт (автоматическая установка)

Для установки на чистый Ubuntu-сервер достаточно одной команды:

```bash
# Скачать и запустить setup.sh
curl -sSL https://raw.githubusercontent.com/YOUR_USERNAME/SKLAD/main/setup.sh \
  | sudo bash -s -- \
    --domain sklad.example.com \
    --repo https://github.com/YOUR_USERNAME/SKLAD.git

# С SSL-сертификатом (нужен реальный домен, направленный на сервер)
curl -sSL https://raw.githubusercontent.com/YOUR_USERNAME/SKLAD/main/setup.sh \
  | sudo bash -s -- \
    --domain sklad.example.com \
    --repo https://github.com/YOUR_USERNAME/SKLAD.git \
    --ssl
```

Скрипт сделает всё сам: установит зависимости, создаст пользователя, развернёт приложение,
настроит systemd и nginx. В конце покажет инструкцию по настройке GitHub Actions.

> Ручные шаги (Шаги 1-5 ниже) нужны только если хочешь настроить что-то по-своему.

---

## Структура на VPS

```
/var/www/sklad/          ← код приложения
├── .env                 ← SECRET_KEY и настройки (НЕ в git)
├── .venv/               ← виртуальное окружение Python
├── app.py
├── warehouse.db         ← БД (создаётся при первом запуске)
└── deploy.sh            ← скрипт деплоя

/etc/systemd/system/sklad.service     ← автозапуск приложения
/etc/nginx/sites-available/sklad      ← nginx конфиг для домена
/var/log/sklad/                       ← логи gunicorn
```

---

## Шаг 1 — Подготовка VPS

Подключись к VPS:
```bash
ssh root@YOUR_VPS_IP
```

### 1.1 Установи зависимости

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx
pip3 install gunicorn
```

### 1.2 Создай пользователя для приложения (не запускай от root)

```bash
useradd -m -s /bin/bash deploy
# Дай право перезапускать только сервис sklad без пароля
echo "deploy ALL=(ALL) NOPASSWD: /bin/systemctl restart sklad, /bin/systemctl status sklad" \
  >> /etc/sudoers.d/deploy
```

### 1.3 Создай директорию и склонируй репозиторий

```bash
mkdir -p /var/www/sklad
cd /var/www/sklad
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git .
```

### 1.4 Создай виртуальное окружение и установи зависимости

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install gunicorn  # если не в requirements
```

### 1.5 Создай .env файл

```bash
nano /var/www/sklad/.env
```

Содержимое:
```
SECRET_KEY=<сгенерируй командой: python3 -c "import secrets; print(secrets.token_hex(32))">
FLASK_DEBUG=0
```

### 1.6 Создай папку для логов

```bash
mkdir -p /var/log/sklad
chown www-data:www-data /var/log/sklad
```

### 1.7 Выдай права

```bash
chown -R www-data:www-data /var/www/sklad
chmod +x /var/www/sklad/deploy.sh
```

---

## Шаг 2 — Systemd сервис

Скопируй файл сервиса (он уже есть в репозитории):

```bash
cp /var/www/sklad/sklad.service /etc/systemd/system/sklad.service
systemctl daemon-reload
systemctl enable sklad
systemctl start sklad
systemctl status sklad   # должно быть: Active: active (running)
```

---

## Шаг 3 — Nginx

> ⚠️ У тебя уже работает nginx с сайтом-заглушкой. Этот конфиг — ОТДЕЛЬНЫЙ файл.
> Он НЕ затронет существующий сайт. Просто добавляется новый server-блок.

### 3.1 Замени домен в конфиге

```bash
# Отредактируй файл и замени YOUR_DOMAIN на реальный домен
nano /var/www/sklad/sklad.nginx.conf
```

### 3.2 Подключи конфиг

```bash
# Копируем в sites-available
cp /var/www/sklad/sklad.nginx.conf /etc/nginx/sites-available/sklad

# Активируем (symlink в sites-enabled)
ln -s /etc/nginx/sites-available/sklad /etc/nginx/sites-enabled/sklad

# Проверяем, что nginx всё принял (твой существующий сайт не должен упасть)
nginx -t

# Перезагружаем nginx (не restart — reload сохраняет существующие соединения)
systemctl reload nginx
```

### 3.3 Проверь, что оба сайта работают

```bash
# Новый SKLAD
curl -I http://YOUR_DOMAIN

# Существующий сайт-заглушка (должен отвечать как раньше)
curl -I http://EXISTING_SITE_DOMAIN
```

### 3.4 Получи SSL-сертификат (Let's Encrypt)

```bash
certbot --nginx -d YOUR_DOMAIN
```

Certbot сам обновит nginx конфиг и добавит HTTPS. После этого раскомментируй HTTPS-блок
в `/etc/nginx/sites-available/sklad` и раскомментируй редирект HTTP→HTTPS.

---

## Шаг 4 — GitHub Actions (CI/CD)

### 4.1 Создай SSH-ключ специально для деплоя

На VPS:
```bash
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_deploy
# Нажми Enter дважды (без passphrase)

# Добавь публичный ключ в authorized_keys
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Скопируй приватный ключ — он нужен для GitHub Secrets
cat ~/.ssh/github_deploy
```

### 4.2 Добавь секреты в GitHub

Перейди в репозиторий → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Значение |
|--------|----------|
| `VPS_HOST` | IP-адрес твоего VPS |
| `VPS_USER` | `deploy` (или `root`, если не создавал отдельного пользователя) |
| `VPS_SSH_KEY` | Содержимое `~/.ssh/github_deploy` (весь текст включая `-----BEGIN...`) |
| `VPS_PORT` | `22` (или другой, если поменял SSH-порт) |

### 4.3 Файл workflow уже в репозитории

`.github/workflows/deploy.yml` уже создан. Он запускается автоматически при каждом `push` в `main`.

### 4.4 Проверь первый деплой

```bash
git add .
git commit -m "feat: add deployment config"
git push origin main
```

Зайди в GitHub → **Actions** — увидишь запущенный workflow. Зелёная галочка = всё работает.

---

## Шаг 5 — Проверка

```bash
# Логи приложения
journalctl -u sklad -f

# Логи gunicorn
tail -f /var/log/sklad/error.log

# Статус nginx
systemctl status nginx

# Статус приложения
systemctl status sklad
```

---

## Что происходит при каждом деплое

```
git push main
    │
    └─▶ GitHub Actions запускается
            │
            └─▶ SSH на VPS → /var/www/sklad
                    │
                    ├─▶ git pull origin main
                    ├─▶ pip install -r requirements.txt
                    └─▶ sudo systemctl restart sklad
                                │
                                └─▶ SKLAD обновлён ✅
```

**База данных** (`warehouse.db`) при деплое НЕ трогается — она не в git и живёт отдельно на VPS.

---

## Частые команды на VPS

```bash
# Перезапустить вручную
sudo systemctl restart sklad

# Посмотреть логи в реальном времени
journalctl -u sklad -f

# Откатиться на предыдущий коммит
cd /var/www/sklad
git log --oneline -5     # найди нужный хэш
git checkout <hash>
sudo systemctl restart sklad
```

---

## Файлы, которые были добавлены в репозиторий

| Файл | Описание |
|------|----------|
| `.github/workflows/deploy.yml` | GitHub Actions — триггер деплоя |
| `deploy.sh` | Скрипт деплоя (выполняется на VPS) |
| `sklad.service` | Systemd unit-файл (копировать в `/etc/systemd/system/`) |
| `sklad.nginx.conf` | Nginx конфиг (копировать в `/etc/nginx/sites-available/`) |
| `DEPLOY.md` | Этот файл |
