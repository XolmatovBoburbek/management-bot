"""Локальный запуск кабинета без Telegram — для проверки интерфейса в браузере.

    python scripts/dev_server.py path/to/table.xlsx

Аккаунтам команды задаётся пароль DEV_PASSWORD (по умолчанию devpass123), вход — по Telegram-нику.
Уведомления не отправляются, а печатаются в консоль. Только для локальной разработки!
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from aiohttp import web

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.accounts import hash_password  # noqa: E402
from app.config import load_config  # noqa: E402
from app.db import Database  # noqa: E402
from app.service import Service  # noqa: E402
from app.web import create_app  # noqa: E402


class ConsoleNotifier:
    async def send(self, chat_id, text, keyboard=None):
        print(f"--- to {chat_id} ---\n{text}\n")
        return True

    async def send_document(self, chat_id, data, filename, caption=""):
        print(f"--- document {filename} ({len(data)} bytes) to {chat_id} ---")
        return True


async def main() -> None:
    config = load_config()
    db = Database(config.data_dir / "dev.sqlite3", config.tz)
    service = Service(db, config, ConsoleNotifier())
    service.seed_team(config.team_file)
    service.accounts.bootstrap()
    password = os.environ.get("DEV_PASSWORD", "devpass123")
    for user in db.list_users():
        if not user.has_password:
            db.update_user(user.id, password_hash=hash_password(password))
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        await service.import_file(path.read_bytes(), path.name, None)
    runner = web.AppRunner(create_app(service))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", config.port).start()
    logins = ", ".join(u.login for u in db.list_users())
    print(f"Кабинет: http://127.0.0.1:{config.port}/  логины: {logins}; пароль: {password}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
