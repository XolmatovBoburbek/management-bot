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
#      если заняты другим проектом — поднимает только бота на свободном локальном порту (8080–8099)
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

free_port() {
  local p
  for p in $(seq 8080 8099); do
    if ! port_busy "$p"; then echo "$p"; return; fi
  done
  echo "Нет свободного порта в диапазоне 8080–8099" >&2
  return 1
}

# Повторный запуск: не путать свои же контейнеры с чужими сервисами.
if [[ -f docker-compose.override.yml ]]; then
  MODE=proxy
elif [[ -n "$($DOCKER compose ps -q caddy 2>/dev/null)" ]]; then
  MODE=caddy
elif port_busy 80 || port_busy 443; then
  MODE=proxy
else
  MODE=caddy
fi

if [[ "$MODE" == proxy ]]; then
  if [[ -f docker-compose.override.yml ]]; then
    LOCAL_PORT="$(grep -oE '127\.0\.0\.1:[0-9]+' docker-compose.override.yml | cut -d: -f2)"
  else
    LOCAL_PORT="$(free_port)"
    cat > docker-compose.override.yml <<EOF
services:
  bot:
    ports:
      - "127.0.0.1:${LOCAL_PORT}:8080"
  caddy:
    profiles: ["disabled"]
EOF
  fi
  warn "Порты 80/443 уже заняты другим проектом на этом сервере."
  warn "Поднимаю только бота на 127.0.0.1:${LOCAL_PORT} — подключите его к существующему веб-серверу."
  $DOCKER compose up -d --build bot
  cat <<EOF

Добавьте в конфиг nginx (например, /etc/nginx/sites-available/iacpm) и перезапустите nginx:

server {
    server_name ${DOMAIN};
    client_max_body_size 25m;
    location / {
        proxy_pass http://127.0.0.1:${LOCAL_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Real-IP \$remote_addr;
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
