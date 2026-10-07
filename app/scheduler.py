"""Планировщик: раз в минуту проверяет, не пора ли что-то отправить.

Каждая рассылка помечается в notification_log, поэтому перезапуск бота не приводит
ни к дублям, ни к пропускам: если бот лежал в 9:00 и поднялся в 10:30, утренние
сообщения всё равно уйдут (окно — 3 часа после назначенного времени).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta

from app import texts
from app.service import Service, parse_hhmm

log = logging.getLogger(__name__)

SEND_WINDOW = timedelta(hours=3)
QUIET_AFTER_EVENT_DAYS = 14


def in_window(now: datetime, at: time | None, window: timedelta = SEND_WINDOW) -> bool:
    if at is None:
        return False
    start = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return start <= now < start + window


class Scheduler:
    def __init__(self, service: Service):
        self.service = service

    async def run(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 — планировщик не должен падать из-за одной ошибки
                log.exception("Scheduler tick failed")
            now = self.service.now()
            await asyncio.sleep(max(5, 60 - now.second))

    async def tick(self) -> None:
        service = self.service
        now = service.now()
        today = now.date()
        settings = service.settings()
        await service.send_due_calls()

        for project in service.projects():
            await self._maybe_sync(project, now, settings)
            project = service.db.get_project(project.id) or project
            if project.event_date and today > project.event_date + timedelta(days=QUIET_AFTER_EVENT_DAYS):
                continue
            if in_window(now, parse_hhmm(settings["morning_time"])):
                await service.send_morning(project)
            if in_window(now, parse_hhmm(settings["pm_time"])):
                await service.send_pm_digest(project)
            if in_window(now, parse_hhmm(settings["evening_time"])):
                await service.send_evening(project)
            standup = parse_hhmm(settings.get("standup_time", ""))
            if standup:
                remind_at = (datetime.combine(today, standup) - timedelta(minutes=15)).time()
                if in_window(now, remind_at, timedelta(minutes=15)):
                    await service.send_standup(project)

    async def _maybe_sync(self, project, now: datetime, settings: dict) -> None:
        if project.source_type != "gsheet" or not project.source_url:
            return
        minutes = int(settings.get("sync_minutes") or 0)
        if minutes <= 0:
            return
        if project.last_sync_at:
            last = datetime.fromisoformat(project.last_sync_at)
            if now - last < timedelta(minutes=minutes):
                return
        try:
            await self.service.sync_project(project)
        except Exception as exc:  # noqa: BLE001
            log.warning("Sync of project %s failed: %s", project.id, exc)
            # чтобы не долбить Google каждую минуту при ошибке
            self.service.db.update_project(project.id, last_sync_at=now.isoformat(timespec="seconds"))
            if self.service.db.mark_sent(f"syncerr:{project.id}:{now.date()}"):
                for pm in self.service._pms():
                    if pm.telegram_id:
                        await self.service.notifier.send(
                            pm.telegram_id,
                            f"⚠️ Не удалось синхронизировать «{texts.e(project.name)}» с Google Таблицей:\n"
                            f"{texts.e(exc)}")
