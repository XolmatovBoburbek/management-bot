#!/usr/bin/env bash
# Автообновление бота: раз в 2 минуты проверяет GitHub и, если появились новые коммиты,
# забирает их и пересобирает контейнер. Другие проекты на сервере не затрагиваются.
#
# Включить (один раз):   sudo bash deploy/azure/auto-update.sh --install
# Выключить:             sudo bash deploy/azure/auto-update.sh --uninstall
# Обновить прямо сейчас: sudo systemctl start iacpm-update
# Журнал:                journalctl -u iacpm-update -n 50
set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
UNIT=iacpm-update

if [[ "${1:-}" == "--install" ]]; then
  if [[ $EUID -ne 0 ]]; then echo "Запустите через sudo" >&2; exit 1; fi
  OWNER="$(stat -c %U "$ROOT")"
  cat > "/etc/systemd/system/${UNIT}.service" <<EOF
[Unit]
Description=IAC PM bot: обновление из GitHub
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=${OWNER}
SupplementaryGroups=docker
WorkingDirectory=${ROOT}
ExecStart=/usr/bin/env bash ${ROOT}/deploy/azure/auto-update.sh
EOF
  cat > "/etc/systemd/system/${UNIT}.timer" <<EOF
[Unit]
Description=IAC PM bot: проверка обновлений каждые 2 минуты

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now "${UNIT}.timer"
  systemctl start "${UNIT}.service" || true
  journalctl -u "${UNIT}" -n 5 --no-pager || true
  echo
  echo "Автообновление включено: бот сам подтянет изменения из GitHub в течение 2 минут."
  exit 0
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  if [[ $EUID -ne 0 ]]; then echo "Запустите через sudo" >&2; exit 1; fi
  systemctl disable --now "${UNIT}.timer" 2>/dev/null || true
  rm -f "/etc/systemd/system/${UNIT}.service" "/etc/systemd/system/${UNIT}.timer"
  systemctl daemon-reload
  echo "Автообновление выключено."
  exit 0
fi

if [[ $EUID -eq 0 ]]; then
  echo "Не запускайте обновление от root — используйте: sudo systemctl start ${UNIT}" >&2
  exit 1
fi

git fetch --quiet
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse '@{u}')" ]]; then
  if ! git merge --ff-only --quiet '@{u}'; then
    echo "Не получилось забрать обновления: на сервере есть свои изменения в файлах репозитория." >&2
    echo "Посмотрите: cd ${ROOT} && git status" >&2
    exit 1
  fi
fi

HEAD="$(git rev-parse HEAD)"
[[ "$HEAD" == "$(cat .deployed-commit 2>/dev/null || true)" ]] && exit 0
# сломанную сборку не повторяем каждые 2 минуты — ждём следующего коммита
[[ "$HEAD" == "$(cat .deploy-failed 2>/dev/null || true)" ]] && exit 0

echo "Обновляю бота до ${HEAD:0:7}: $(git log -1 --format=%s)"
if docker compose up -d --build; then
  echo "$HEAD" > .deployed-commit
  rm -f .deploy-failed
  echo "Готово: бот работает на версии ${HEAD:0:7}"
else
  echo "$HEAD" > .deploy-failed
  echo "Сборка ${HEAD:0:7} не удалась, бот работает на прежней версии." >&2
  echo "Повторить: rm ${ROOT}/.deploy-failed && sudo systemctl start ${UNIT}" >&2
  exit 1
fi
