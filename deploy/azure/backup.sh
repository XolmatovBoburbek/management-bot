#!/usr/bin/env bash
# Ежедневный бэкап всех данных бота в ПРИВАТНЫЙ репозиторий GitHub: дамп базы (задачи, комментарии,
# страницы, пользователи, настройки) и загруженные Excel-файлы. История коммитов хранит каждый день.
# Ключ доступа — отдельный deploy key только для этого репозитория. Токен бота (.env) не копируется.
#
# Включить (один раз):  sudo bash deploy/azure/backup.sh --install git@github.com:ВЛАДЕЛЕЦ/iacpm-backup.git
# Бэкап прямо сейчас:   sudo systemctl start iacpm-backup
# Журнал:               journalctl -u iacpm-backup -n 30
# Выключить:            sudo bash deploy/azure/backup.sh --uninstall
set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
UNIT=iacpm-backup
REMOTE_FILE="$ROOT/.backup-remote"

if [[ "${1:-}" == "--install" ]]; then
  if [[ $EUID -ne 0 ]]; then echo "Запустите через sudo" >&2; exit 1; fi
  OWNER="$(stat -c %U "$ROOT")"
  HOME_DIR="$(getent passwd "$OWNER" | cut -d: -f6)"
  if [[ -n "${2:-}" ]]; then
    echo "$2" > "$REMOTE_FILE"
    chown "$OWNER" "$REMOTE_FILE"
  fi
  if [[ ! -s "$REMOTE_FILE" ]]; then
    echo "Укажите адрес приватного репозитория: sudo bash $0 --install git@github.com:ВЛАДЕЛЕЦ/iacpm-backup.git" >&2
    exit 1
  fi
  KEY="$HOME_DIR/.ssh/iacpm_backup"
  if [[ ! -f "$KEY" ]]; then
    sudo -u "$OWNER" mkdir -p "$HOME_DIR/.ssh"
    sudo -u "$OWNER" ssh-keygen -q -t ed25519 -N "" -C "iacpm-backup@$(hostname)" -f "$KEY"
  fi
  cat > "/etc/systemd/system/${UNIT}.service" <<EOF
[Unit]
Description=IAC PM bot: бэкап данных в GitHub
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=${OWNER}
SupplementaryGroups=docker
WorkingDirectory=${ROOT}
ExecStart=/usr/bin/env bash ${ROOT}/deploy/azure/backup.sh
EOF
  cat > "/etc/systemd/system/${UNIT}.timer" <<EOF
[Unit]
Description=IAC PM bot: бэкап каждый день в 03:00 по Ташкенту

[Timer]
OnCalendar=*-*-* 03:00:00 Asia/Tashkent
Persistent=true
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now "${UNIT}.timer"
  echo
  echo "Ежедневный бэкап включён (03:00 по Ташкенту), репозиторий: $(cat "$REMOTE_FILE")"
  echo
  echo "Последний шаг — дайте серверу доступ к репозиторию: GitHub → репозиторий бэкапов → Settings →"
  echo "Deploy keys → Add deploy key, вставьте строку ниже и отметьте «Allow write access»:"
  echo
  cat "$KEY.pub"
  echo
  echo "Потом проверьте: sudo systemctl start ${UNIT} && journalctl -u ${UNIT} -n 20 --no-pager"
  exit 0
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  if [[ $EUID -ne 0 ]]; then echo "Запустите через sudo" >&2; exit 1; fi
  systemctl disable --now "${UNIT}.timer" 2>/dev/null || true
  rm -f "/etc/systemd/system/${UNIT}.service" "/etc/systemd/system/${UNIT}.timer"
  systemctl daemon-reload
  echo "Ежедневный бэкап выключен. Ключ ~/.ssh/iacpm_backup и копия ~/iacpm-backup остались на месте."
  exit 0
fi

if [[ $EUID -eq 0 ]]; then
  echo "Не запускайте бэкап от root — используйте: sudo systemctl start ${UNIT}" >&2
  exit 1
fi

REMOTE="$(cat "$REMOTE_FILE" 2>/dev/null || true)"
if [[ -z "$REMOTE" ]]; then
  echo "Репозиторий для бэкапов не задан — выполните --install" >&2
  exit 1
fi
REPO="${IACPM_BACKUP_DIR:-$HOME/iacpm-backup}"
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/iacpm_backup -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$HOME/.ssh/iacpm_backup_known_hosts"

if ! git ls-remote "$REMOTE" >/dev/null 2>&1; then
  echo "Нет доступа к $REMOTE. Добавьте ключ ~/.ssh/iacpm_backup.pub в Deploy keys репозитория с правом записи:" >&2
  cat "$HOME/.ssh/iacpm_backup.pub" >&2
  exit 1
fi

if [[ ! -d "$REPO/.git" ]]; then
  git clone --quiet "$REMOTE" "$REPO"
fi
cd "$REPO"
git remote set-url origin "$REMOTE"
git config user.name "IAC PM backup"
git config user.email "backup@iacpm.local"
git fetch --quiet origin
if git rev-parse --verify --quiet origin/main >/dev/null; then
  git checkout --quiet -B main origin/main
else
  git symbolic-ref HEAD refs/heads/main
fi

# Снимаем данные во временную папку и подменяем только после успешной проверки.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
(cd "$ROOT" && docker compose exec -T bot python -m app.backup dump) > "$TMP/db.sql"
if ! grep -q "CREATE TABLE \"\?tasks" "$TMP/db.sql"; then
  echo "Дамп базы получился неполным — бэкап не записан" >&2
  exit 1
fi
mkdir -p "$TMP/uploads"
(cd "$ROOT" && docker compose cp bot:/data/uploads/. "$TMP/uploads/") >/dev/null 2>&1 || true

rm -rf data
mkdir -p data
mv "$TMP/db.sql" data/db.sql
mv "$TMP/uploads" data/uploads
cat > README.md <<'EOF'
# Бэкапы IAC PM

Каждый день сервер сохраняет сюда все данные бота; история коммитов — это бэкап на каждый день.

- `data/db.sql` — вся база: проекты, задачи, комментарии и история, обзвон, страницы, пользователи
  (пароли только в виде хешей), пространства, команда и настройки. Активные входы не сохраняются.
- `data/uploads/` — загруженные Excel-файлы (по ним выгружается Excel в исходном формате).

Токен бота и другие секреты сюда не попадают. **Репозиторий должен оставаться приватным.**

## Восстановление

На сервере: выберите день, достаньте дамп и загрузите его в бота.

```bash
cd ~/iacpm-backup && git log --oneline | head -20        # список бэкапов по дням
git show КОММИТ:data/db.sql > ~/restore.sql              # дамп нужного дня
cd ~/management-bot
docker compose stop bot
docker compose cp ~/restore.sql bot:/data/restore.sql
docker compose run --rm bot python -m app.backup restore /data/restore.sql --force
docker compose up -d bot
```

Excel-файлы того же дня: `git -C ~/iacpm-backup checkout КОММИТ -- data/uploads`, затем
`docker compose cp ~/iacpm-backup/data/uploads/. bot:/data/uploads/` и `git -C ~/iacpm-backup checkout main -- data`.

Прежняя база не удаляется — она остаётся рядом как `bot.sqlite3.before-restore-…`.
EOF

git add -A
if git diff --cached --quiet; then
  echo "Данные не изменились — новый коммит не нужен"
  exit 0
fi
git commit --quiet -m "Бэкап $(date +%F)"
git push --quiet origin HEAD:main
echo "Бэкап отправлен: $(git log -1 --format='%h %s')"
