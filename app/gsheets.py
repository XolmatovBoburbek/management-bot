"""Google Таблицы: скачивание как xlsx и (опционально) запись статусов обратно.

Чтение работает двумя способами:
* таблица открыта «Все, у кого есть ссылка — читатель» → скачиваем export?format=xlsx без ключей;
* задан GOOGLE_CREDENTIALS_FILE (сервисный аккаунт) → читаем и пишем через API.
Запись обратно (статусы, даты, комментарии) возможна только с сервисным аккаунтом,
которому выдан доступ «Редактор» к таблице.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from pathlib import Path

import aiohttp

from app.excel_io import DATE_FIELDS, comment_lines_to_append
from app.models import STATUS_LABELS, Task, norm_text

log = logging.getLogger(__name__)

_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]{20,})")


class GSheetError(RuntimeError):
    pass


def sheet_id_from_url(url: str) -> str | None:
    match = _ID_RE.search(url or "")
    return match.group(1) if match else None


def export_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx"


class GoogleSheets:
    def __init__(self, credentials_file: Path | None):
        self.credentials_file = credentials_file if credentials_file and credentials_file.exists() else None
        self._client = None

    @property
    def can_write(self) -> bool:
        return self.credentials_file is not None

    def service_email(self) -> str | None:
        if not self.credentials_file:
            return None
        import json

        try:
            return json.loads(self.credentials_file.read_text()).get("client_email")
        except (OSError, ValueError):
            return None

    def _gc(self):
        if self._client is None:
            import gspread

            self._client = gspread.service_account(filename=str(self.credentials_file))
        return self._client

    async def download(self, url: str) -> bytes:
        sheet_id = sheet_id_from_url(url)
        if not sheet_id:
            raise GSheetError("Это не похоже на ссылку на Google Таблицу (нужна ссылка вида docs.google.com/spreadsheets/d/…).")
        if self.credentials_file:
            try:
                return await asyncio.to_thread(self._download_api, sheet_id)
            except Exception as exc:  # noqa: BLE001 — пробуем публичную ссылку
                log.warning("Service account download failed, trying public link: %s", exc)
        return await self._download_public(sheet_id)

    def _download_api(self, sheet_id: str) -> bytes:
        from gspread.utils import ExportFormat

        return self._gc().open_by_key(sheet_id).export(format=ExportFormat.EXCEL)

    async def _download_public(self, sheet_id: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(export_url(sheet_id), allow_redirects=True) as resp:
                data = await resp.read()
                status = resp.status
                content_type = resp.headers.get("Content-Type", "")
        if status != 200 or "html" in content_type or not data.startswith(b"PK"):
            hint = "Откройте доступ «Все, у кого есть ссылка — Читатель»"
            if self.credentials_file:
                hint += f" или выдайте доступ сервисному аккаунту {self.service_email()}"
            raise GSheetError(f"Не удалось скачать таблицу (HTTP {status}). {hint}.")
        return data

    # ---------- запись обратно ----------
    async def write_task(self, url: str, layout: dict, task: Task, today: date,
                         comment_lines: list[str]) -> int | None:
        """Обновляет строку задачи в таблице. Возвращает номер строки (для новых задач — добавленной)."""
        if not self.can_write:
            return None
        sheet_id = sheet_id_from_url(url)
        if not sheet_id or not layout.get("columns"):
            return None
        return await asyncio.to_thread(self._write_task_sync, sheet_id, layout, task, today, comment_lines)

    def _write_task_sync(self, sheet_id: str, layout: dict, task: Task, today: date,
                         comment_lines: list[str]) -> int | None:
        from gspread.utils import rowcol_to_a1

        ws = self._gc().open_by_key(sheet_id).worksheet(layout["tasks_sheet"])
        cols = {k: int(v) for k, v in layout["columns"].items()}
        title_col = cols["title"]
        titles = ws.col_values(title_col)  # индекс 0 = строка 1
        row = task.row_index
        if not row or row > len(titles) or norm_text(titles[row - 1]) != norm_text(task.title):
            row = next((i + 1 for i, t in enumerate(titles) if norm_text(t) == norm_text(task.title)), None)
        is_new = row is None
        if is_new:
            row = len(titles) + 1

        values: dict[str, object] = {"status": STATUS_LABELS[task.effective_status(today)]}
        for field_name in DATE_FIELDS:
            value = getattr(task, field_name)
            values[field_name] = value.strftime("%d.%m.%Y") if value else ""
        if task.responsible:
            values["responsible"] = task.responsible
        if task.priority:
            values["priority"] = task.priority
        if is_new:
            numbers = [int(v) for v in ws.col_values(cols["no"])[1:] if str(v).isdigit()] if "no" in cols else []
            values.update({"no": str(max(numbers, default=0) + 1), "block": task.block, "title": task.title,
                           "description": task.description, "contractor": task.contractor})
        if "comment" in cols and comment_lines:
            current = task.sheet_comment if is_new else (ws.cell(row, cols["comment"]).value or "")
            extra = comment_lines_to_append(current, comment_lines)
            if extra:
                values["comment"] = "\n".join([current, *extra]).strip()

        updates = [{"range": rowcol_to_a1(row, cols[k]), "values": [[v]]} for k, v in values.items() if k in cols]
        ws.batch_update(updates, value_input_option="USER_ENTERED")
        return row
