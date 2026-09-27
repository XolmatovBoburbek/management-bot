"""Точка входа: бот (long polling) + веб-сервер Mini App + планировщик в одном процессе."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

from app.bot import TelegramNotifier, build_router, setup_bot_profile
from app.config import load_config
from app.db import Database
from app.gsheets import GoogleSheets
from app.scheduler import Scheduler
from app.service import Service
from app.web import create_app

log = logging.getLogger("management-bot")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    if not config.bot_token:
        raise SystemExit("BOT_TOKEN не задан. Скопируйте .env.example в .env и впишите токен от @BotFather.")
    if not config.webapp_url.startswith("https://"):
        log.warning("WEBAPP_URL не задан или не https — кнопка Mini App не появится (бот работает и без неё).")

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    db = Database(config.db_path, config.tz)
    service = Service(db, config, TelegramNotifier(bot), GoogleSheets(config.google_credentials_file))
    seeded = service.seed_team(config.team_file)
    if seeded:
        log.info("Из %s добавлено участников: %s", config.team_file, seeded)

    me = await bot.get_me()
    service.bot_username = me.username or ""
    log.info("Бот @%s запущен", service.bot_username)
    await setup_bot_profile(bot, config.webapp_url)

    runner = web.AppRunner(create_app(service))
    await runner.setup()
    await web.TCPSite(runner, config.host, config.port).start()
    log.info("Mini App: http://%s:%s (публичный адрес: %s)", config.host, config.port, config.webapp_url or "—")

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_router(service))
    background = [
        asyncio.create_task(Scheduler(service).run(), name="scheduler"),
        asyncio.create_task(service.run_sheet_writer(), name="sheet-writer"),
    ]
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        for task in background:
            task.cancel()
        await runner.cleanup()
        await bot.session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
