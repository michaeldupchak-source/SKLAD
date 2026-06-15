#!/bin/bash
# =============================================================================
# setup.sh — первичная установка SKLAD на чистый Ubuntu-сервер
#
# Использование:
#   bash setup.sh --domain sklad.example.com --repo https://github.com/YOUR/SKLAD.git
#
# Флаги:
#   --domain  DOMAIN   домен или IP для nginx (обязательно)
#   --repo    URL      git-репозиторий (обязательно)
#   --user    NAME     системный пользователь (по умолчанию: deploy)
#   --dir     PATH     директория приложения (по умолчанию: /var/www/sklad)
#   --branch  NAME     ветка git (по умолчанию: main)
#   --ssl              запросить SSL-сертификат через certbot (нужен реальный домен)
#   --help             показать справку
# =============================================================================

set -euo pipefail

# ── Цвета ──────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

step()  { echo -e "\n${BLUE}${BOLD}[$((++STEP))/${TOTAL}]${NC} $*"; }
ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
warn()  { echo -e "  ${YELLOW}⚠${NC}  $*"; }
die()   { echo -e "\n${RED}✗ Ошибка:${NC} $*" >&2; exit 1; }

STEP=0
TOTAL=9

# ── Значения по умолчанию ──────────────────────────────────────────────────────
DOMAIN=""
REPO=""
APP_USER="deploy"
APP_DIR="/var/www/sklad"
BRANCH="main"
REQUEST_SSL=false

# ── Парсинг аргументов ─────────────────────────────────────────────────────────
usage() {
  echo "Использование: bash setup.sh --domain DOMAIN --repo REPO_URL [опции]"
  echo ""
  echo "Обязательные:"
  echo "  --domain  DOMAIN   домен или IP (например: sklad.example.com)"
  echo "  --repo    URL      git URL репозитория"
  echo ""
  echo "Опциональные:"
  echo "  --user    NAME     системный пользователь (по умолчанию: deploy)"
  echo "  --dir     PATH     путь к приложению (по умолчанию: /var/www/sklad)"
  echo "  --branch  NAME     git ветка (по умолчанию: main)"
  echo "  --ssl              запросить SSL через certbot"
  echo "  --help             эта справка"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain)  DOMAIN="$2";      shift 2 ;;
    --repo)    REPO="$2";        shift 2 ;;
    --user)    APP_USER="$2";    shift 2 ;;
    --dir)     APP_DIR="$2";     shift 2 ;;
    --branch)  BRANCH="$2";      shift 2 ;;
    --ssl)     REQUEST_SSL=true; shift   ;;
    --help)    usage; exit 0            ;;
    *)         die "Неизвестный аргумент: $1. Запусти с --help" ;;
  esac
done

if [[ -z "$DOMAIN" ]]; then
  read -p "Введите домен или IP-адрес для доступа к приложению: " DOMAIN
  [[ -z "$DOMAIN" ]] && die "Домен/IP не может быть пустым"
fi

if [[ -z "$REPO" ]]; then
  read -p "Введите URL git-репозитория: " REPO
  [[ -z "$REPO" ]] && die "URL репозитория не может быть пустым"
fi

read -p "Укажите внешний порт Nginx (по умолчанию 80): " NGINX_PORT
NGINX_PORT=${NGINX_PORT:-80}

read -p "Укажите внутренний порт Gunicorn (по умолчанию 5000): " APP_PORT
APP_PORT=${APP_PORT:-5000}

# ── Проверка root ──────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && die "Запусти скрипт от root: sudo bash setup.sh ..."

echo ""
echo -e "${BOLD}=== SKLAD — установка на сервер ===${NC}"
echo -e "  Домен:       ${YELLOW}$DOMAIN${NC}"
echo -e "  Репозиторий: ${YELLOW}$REPO${NC}"
echo -e "  Директория:  ${YELLOW}$APP_DIR${NC}"
echo -e "  Пользователь:${YELLOW}$APP_USER${NC}"
echo -e "  Ветка:       ${YELLOW}$BRANCH${NC}"
echo -e "  SSL:         ${YELLOW}$REQUEST_SSL${NC}"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
step "Установка системных зависимостей"
# ══════════════════════════════════════════════════════════════════════════════
apt-get update -qq
apt-get install -y -qq \
  python3 python3-pip python3-venv \
  git nginx \
  certbot python3-certbot-nginx \
  curl ufw
ok "Пакеты установлены"

# ══════════════════════════════════════════════════════════════════════════════
step "Создание системного пользователя: $APP_USER"
# ══════════════════════════════════════════════════════════════════════════════
if id "$APP_USER" &>/dev/null; then
  warn "Пользователь $APP_USER уже существует, пропускаем"
else
  useradd -m -s /bin/bash "$APP_USER"
  ok "Пользователь $APP_USER создан"
fi

# sudoers — только перезапуск нужного сервиса, без пароля
SUDOERS_FILE="/etc/sudoers.d/$APP_USER"
if [[ ! -f "$SUDOERS_FILE" ]]; then
  echo "$APP_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart sklad, /bin/systemctl status sklad" \
    > "$SUDOERS_FILE"
  chmod 440 "$SUDOERS_FILE"
  ok "sudoers настроен ($SUDOERS_FILE)"
else
  warn "sudoers уже настроен, пропускаем"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "Клонирование репозитория в $APP_DIR"
# ══════════════════════════════════════════════════════════════════════════════
if [[ -d "$APP_DIR/.git" ]]; then
  warn "Репозиторий уже существует. Делаем git pull..."
  cd "$APP_DIR"
  git pull origin "$BRANCH"
else
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --branch "$BRANCH" "$REPO" "$APP_DIR"
  ok "Репозиторий склонирован"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "Создание виртуального окружения и установка зависимостей"
# ══════════════════════════════════════════════════════════════════════════════
cd "$APP_DIR"

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
  ok "Виртуальное окружение создано"
else
  warn ".venv уже существует, пропускаем создание"
fi

source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet gunicorn
deactivate
ok "Зависимости установлены"

# ══════════════════════════════════════════════════════════════════════════════
step "Создание файла .env"
# ══════════════════════════════════════════════════════════════════════════════
ENV_FILE="$APP_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
  warn ".env уже существует, пропускаем (не перезаписываем SECRET_KEY)"
else
  SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
  cat > "$ENV_FILE" <<EOF
# Создан автоматически setup.sh $(date '+%Y-%m-%d %H:%M')
SECRET_KEY=$SECRET_KEY
FLASK_DEBUG=0
EOF
  ok ".env создан с новым SECRET_KEY"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "Права на файлы и директории"
# ══════════════════════════════════════════════════════════════════════════════
mkdir -p /var/log/sklad
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chown -R "$APP_USER:$APP_USER" /var/log/sklad
chmod +x "$APP_DIR/deploy.sh"
ok "Права выставлены"

# ══════════════════════════════════════════════════════════════════════════════
step "Настройка systemd сервиса"
# ══════════════════════════════════════════════════════════════════════════════
SERVICE_SRC="$APP_DIR/sklad.service"
SERVICE_DST="/etc/systemd/system/sklad.service"

[[ ! -f "$SERVICE_SRC" ]] && die "Файл $SERVICE_SRC не найден в репозитории"

# Подставляем пользователя и путь, если они нестандартные
sed \
  -e "s|User=www-data|User=$APP_USER|g" \
  -e "s|Group=www-data|Group=$APP_USER|g" \
  -e "s|/var/www/sklad|$APP_DIR|g" \
  -e "s|YOUR_APP_PORT|$APP_PORT|g" \
  "$SERVICE_SRC" > "$SERVICE_DST"

systemctl daemon-reload
systemctl enable sklad
systemctl restart sklad

sleep 2
if systemctl is-active --quiet sklad; then
  ok "Сервис sklad запущен и включён в автозапуск"
else
  warn "Сервис запустился с ошибкой. Проверь: journalctl -u sklad -n 30"
fi

# ══════════════════════════════════════════════════════════════════════════════
step "Настройка nginx"
# ══════════════════════════════════════════════════════════════════════════════
NGINX_SRC="$APP_DIR/sklad.nginx.conf"
NGINX_DST="/etc/nginx/sites-available/sklad"
NGINX_LINK="/etc/nginx/sites-enabled/sklad"

[[ ! -f "$NGINX_SRC" ]] && die "Файл $NGINX_SRC не найден в репозитории"

# Подставляем домен и путь
sed \
  -e "s|YOUR_DOMAIN|$DOMAIN|g" \
  -e "s|/var/www/sklad|$APP_DIR|g" \
  -e "s|YOUR_NGINX_PORT|$NGINX_PORT|g" \
  -e "s|YOUR_APP_PORT|$APP_PORT|g" \
  "$NGINX_SRC" > "$NGINX_DST"

# Активируем сайт
[[ -L "$NGINX_LINK" ]] && rm "$NGINX_LINK"
ln -s "$NGINX_DST" "$NGINX_LINK"

# Убираем дефолтный сайт nginx (если есть)
[[ -L "/etc/nginx/sites-enabled/default" ]] && rm /etc/nginx/sites-enabled/default

nginx -t -q && systemctl reload nginx
ok "nginx настроен для домена: $DOMAIN"

# ══════════════════════════════════════════════════════════════════════════════
step "SSL-сертификат (Let's Encrypt)"
# ══════════════════════════════════════════════════════════════════════════════
if [[ "$REQUEST_SSL" == true ]]; then
  # Проверяем что домен — не IP-адрес
  if [[ "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    warn "SSL не выдаётся для IP-адресов. Пропускаем certbot."
  else
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email
    ok "SSL-сертификат получен для $DOMAIN"
  fi
else
  warn "SSL пропущен (запусти с --ssl для получения сертификата)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# Итог
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}${BOLD}════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ✅  SKLAD успешно установлен!${NC}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════${NC}"
echo ""
echo -e "  Приложение: ${YELLOW}http://$DOMAIN${NC}"
echo -e "  Директория: ${YELLOW}$APP_DIR${NC}"
echo -e "  Логи:       ${YELLOW}journalctl -u sklad -f${NC}"
echo ""
echo -e "${BOLD}Следующий шаг — настройка GitHub Actions CI/CD:${NC}"
echo ""
echo -e "  1. Создай SSH-ключ для деплоя:"
echo -e "     ${YELLOW}ssh-keygen -t ed25519 -C 'github-actions' -f ~/.ssh/github_deploy${NC}"
echo -e "     ${YELLOW}cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys${NC}"
echo ""
echo -e "  2. Добавь секреты в GitHub → Settings → Secrets → Actions:"
echo -e "     ${YELLOW}VPS_HOST${NC}    = IP этого сервера"
echo -e "     ${YELLOW}VPS_USER${NC}    = $APP_USER"
echo -e "     ${YELLOW}VPS_SSH_KEY${NC} = содержимое ~/.ssh/github_deploy"
echo -e "     ${YELLOW}VPS_PORT${NC}    = 22"
echo ""
echo -e "  3. Готово — каждый ${YELLOW}git push main${NC} будет деплоить автоматически."
echo ""
