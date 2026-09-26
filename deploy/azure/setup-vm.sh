#!/usr/bin/env bash
# Установка бота на Azure VM (Ubuntu/Debian), рядом с уже работающими ботами.
#
#   git clone https://github.com/XolmatovBoburbek/management-bot.git
#   cd management-bot
#   bash deploy/azure/setup-vm.sh
#
# Что делает:
#   1. ставит Docker (если его нет);
#   2. создаёт .env (спрашивает токен бота и домен);
#   3. если порты 80/443 свободны — поднимает бота + Caddy с автоматическим HTTPS;
#      если заняты (например, nginx другого бота) — поднимает только бота на 127.0.0.1:8080
#      и печатает готовый блок для nginx.
set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

if ! command -v docker >/dev/null 2>&1; then
  say "Устанавливаю Docker"
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
fi
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then DOCKER="sudo docker"; fi

if [[ ! -f .env ]]; then
  say "Настройка .env"
  read -r -p "Токен бота от @BotFather: " BOT_TOKEN
  echo
  echo "Нужен домен с HTTPS для Mini App. Бесплатный вариант в Azure:"
  echo "  Портал → ваша VM → Public IP → Configuration → DNS name label (например, iacpm)"
  echo "  получится iacpm.<регион>.cloudapp.azure.com"
  read -r -p "Домен (без https://): " DOMAIN
  cat > .env <<EOF
BOT_TOKEN=${BOT_TOKEN}
WEBAPP_URL=https://${DOMAIN}
DOMAIN=${DOMAIN}
TIMEZONE=Asia/Tashkent
PORT=8080
EOF
  chmod 600 .env
  if [[ -f secrets/google.json ]]; then
    echo "GOOGLE_CREDENTIALS_FILE=/secrets/google.json" >> .env
  fi
else
  say ".env уже есть — использую его"
fi

DOMAIN="$(grep -E '^DOMAIN=' .env | cut -d= -f2- || true)"

port_busy() { sudo ss -ltnH "( sport = :$1 )" 2>/dev/null | grep -q .; }

if port_busy 80 || port_busy 443; then
  warn "Порты 80/443 уже заняты другим сервисом (скорее всего, веб-сервер другого бота)."
  warn "Поднимаю только бота на 127.0.0.1:8080 — подключите его к существующему nginx."
  cat > docker-compose.override.yml <<'EOF'
services:
  bot:
    ports:
      - "127.0.0.1:8080:8080"
  caddy:
    profiles: ["disabled"]
EOF
  $DOCKER compose up -d --build bot
  cat <<EOF

Добавьте в конфиг nginx (например, /etc/nginx/sites-available/iacpm) и перезапустите nginx:

server {
    server_name ${DOMAIN};
    client_max_body_size 25m;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}

Сертификат: sudo certbot --nginx -d ${DOMAIN}
EOF
else
  say "Запускаю бота и Caddy (HTTPS выпустится автоматически)"
  warn "Убедитесь, что в Azure → VM → Networking открыты входящие порты 80 и 443."
  $DOCKER compose up -d --build
fi

say "Готово. Логи: $DOCKER compose logs -f bot"
echo "Проверка: curl -s https://${DOMAIN}/health"
echo "Данные бота (база, загруженные таблицы) лежат в docker-томе bot-data и переживают перезапуски."
