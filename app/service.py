"""Сценарии приложения: всё, что меняет данные и рассылает уведомления.

И бот, и кабинет, и планировщик вызывают только этот слой — поэтому правила
(кто что может менять, кого уведомлять, что писать обратно в таблицу) живут в одном месте.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Protocol

import yaml
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app import keyboards, logic, texts
from app.accounts import Accounts
from app.config import Config
from app.dates import parse_date
from app.db import Database, ImportResult
from app.excel_io import ParsedWorkbook, build_workbook, export_workbook, parse_workbook
from app.gsheets import GoogleSheets
from app.models import (
    CANCELLED,
    DONE,
    DONE_LATE,
    DONE_STATUSES,
    PRIORITIES,
    PROGRESS,
    STATUS_LABELS,
    TODO,
    AccessError,
    Call,
    Member,
    Project,
    Task,
    norm_text,
    normalize_priority,
)
from app.pages import Pages

log = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "morning_time": "09:00",
    "pm_time": "09:30",
    "evening_time": "18:00",
    "standup_time": "",
    "sync_minutes": "10",
    "group_chat_id": "",
    "weekly_day": "0",
    "checks_per_day": "5",
}
NUMERIC_SETTINGS = {"sync_minutes": (0, 1440), "checks_per_day": (1, 20), "weekly_day": (0, 6)}
EDITABLE_TASK_FIELDS = {"title", "block", "description", "priority", "responsible", "start_date", "deadline",
                        "contractor", "status"}
KEEP_UPLOADS = 5


class Notifier(Protocol):
    async def send(self, chat_id: int, text: str, keyboard: InlineKeyboardMarkup | None = None) -> bool: ...

    async def send_document(self, chat_id: int, data: bytes, filename: str, caption: str = "") -> bool: ...


class Service:
    def __init__(self, db: Database, config: Config, notifier: Notifier, sheets: GoogleSheets | None = None,
                 clock=None):
        self.db = db
        self.config = config
        self.notifier = notifier
        self.sheets = sheets or GoogleSheets(None)
        self._clock = clock
        if clock:
            db.clock = clock
        self.bot_username = ""
        self._write_queue: asyncio.Queue | None = None
        self.accounts = Accounts(db, self.now)
        self.pages = Pages(db)

    # ---------- время и настройки ----------
    def now(self) -> datetime:
        return self._clock() if self._clock else datetime.now(self.config.tz)

    def today(self) -> date:
        return self.now().date()

    def settings(self) -> dict[str, str]:
        return {**DEFAULT_SETTINGS, **self.db.all_settings()}

    def update_settings(self, values: dict[str, str], actor: Member) -> dict[str, str]:
        self._require_admin(actor)
        for key, value in values.items():
            if key not in DEFAULT_SETTINGS:
                continue
            value = str(value or "").strip()
            if key.endswith("_time") and value and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", value):
                raise ValueError(f"Время «{value}» — нужен формат ЧЧ:ММ")
            if key in NUMERIC_SETTINGS:
                low, high = NUMERIC_SETTINGS[key]
                if not value.isdigit() or not low <= int(value) <= high:
                    raise ValueError(f"Значение «{value}» должно быть числом от {low} до {high}")
            self.db.set_setting(key, value)
        return self.settings()

    def team(self) -> logic.Team:
        return logic.Team(self.db.list_members())

    @property
    def webapp_url(self) -> str:
        return self.config.webapp_url

    # ---------- команда ----------
    def seed_team(self, path: Path) -> int:
        """Добавляет из team.yaml тех, кого ещё нет в базе (по нику или имени).

        Уже существующих участников не меняет: их правят в кабинете → «Команда».
        """
        if not path.exists():
            return 0
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        existing = self.db.list_members(include_inactive=True)
        known = {m.username.lower() for m in existing if m.username} | {norm_text(m.name) for m in existing}
        created = []
        for item in data.get("members", []):
            username = str(item.get("username", "")).lstrip("@").strip().lower()
            if username in known or norm_text(str(item["name"])) in known:
                continue
            created.append((item, self.db.upsert_member(
                name=str(item["name"]), username=str(item.get("username", "")), role=str(item.get("role", "")),
                aliases=str(item.get("aliases", "")), phone=str(item.get("phone", "")),
                is_admin=bool(item.get("admin")), is_pm=bool(item.get("pm")),
                is_observer=bool(item.get("observer")),
            )))
        for item, member in created:
            target = self.db.member_by_username(str(item.get("assists", "")))
            if target and target.id != member.id:
                self.db.set_assists(member.id, target.id)
        return len(created)

    def identify(self, telegram_id: int, username: str | None) -> Member | None:
        member = self.db.member_by_telegram(telegram_id)
        if member:
            return member
        member = self.db.member_by_username(username or "")
        if member:
            self.db.bind_telegram(member.id, telegram_id)
            return self.db.get_member(member.id)
        return None

    def save_member(self, actor: Member, data: dict) -> Member:
        self._require_admin(actor)
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Укажите имя")
        member_id = data.get("id")
        if member_id and int(member_id) == actor.id and not data.get("is_admin", True):
            raise ValueError("Нельзя снять права администратора с самого себя")
        assists_id = int(data["assists_id"]) if data.get("assists_id") else None
        if assists_id is not None:
            if member_id and assists_id == int(member_id):
                raise ValueError("Участник не может выполнять задачи сам за себя")
            if not self.db.get_member(assists_id):
                raise ValueError("Участник, за которого выполняются задачи, не найден")
        return self.db.upsert_member(
            member_id=int(member_id) if member_id else None, name=name,
            username=str(data.get("username", "")), role=str(data.get("role", "")),
            aliases=str(data.get("aliases", "")), phone=str(data.get("phone", "")),
            is_admin=bool(data.get("is_admin")), is_pm=bool(data.get("is_pm")),
            active=bool(data.get("active", True)), assists_id=assists_id,
            is_observer=bool(data.get("is_observer")),
        )

    def _require_admin(self, actor: Member | None) -> None:
        if not actor or not actor.is_admin:
            raise AccessError("Это действие доступно только администраторам")

    def can_edit(self, actor: Member | None, task: Task) -> bool:
        if not actor:
            return False
        return actor.is_admin or actor.id in {m.id for m in self.team().assignees(task)}

    def _event(self, task_id: int, kind: str, text: str, actor: Member) -> None:
        self.db.add_event(task_id, kind, text, actor.id, actor.name)

    def require_task_access(self, actor: Member | None, task: Task) -> None:
        if not self.can_edit(actor, task):
            raise AccessError("Это не ваша задача — менять её может ответственный или администратор")

    # ---------- проекты ----------
    def projects(self, workspace_id: int | None = None) -> list[Project]:
        return self.db.list_projects(workspace_id=workspace_id)

    def default_project(self) -> Project | None:
        projects = self.projects()
        return projects[0] if projects else None

    def create_project(self, actor: Member, name: str, event_date: date | None,
                       workspace_id: int | None = None) -> Project:
        self._require_admin(actor)
        name = name.strip()
        if not name:
            raise ValueError("Укажите название проекта")
        code = re.sub(r"[^\w]+", "_", name, flags=re.UNICODE).strip("_").upper()[:40] or "PROJECT"
        return self.db.create_project(name, code, event_date, workspace_id)

    def update_project(self, actor: Member, project: Project, **fields) -> Project:
        self._require_admin(actor)
        allowed = {k: v for k, v in fields.items() if k in {"name", "event_date", "archived"}}
        return self.db.update_project(project.id, **allowed)

    async def import_file(self, data: bytes, filename: str, actor: Member | None, *, source_type: str = "upload",
                          source_url: str | None = None, project_id: int | None = None,
                          workspace_id: int | None = None) -> tuple[ImportResult, ParsedWorkbook, list[dict]]:
        if actor is not None:
            self._require_admin(actor)
        parsed = parse_workbook(data, fallback_name=Path(filename).stem)
        if workspace_id is not None and project_id is None:
            existing = self.db.project_by_code(parsed.code)
            if existing and existing.workspace_id != workspace_id:
                other = self.db.get_workspace(existing.workspace_id) if existing.workspace_id else None
                raise ValueError(f"Проект с кодом {parsed.code} уже есть в пространстве "
                                 f"«{other.name if other else '—'}». Загрузите таблицу там или поменяйте код проекта "
                                 "на листе «Информация о проекте».")
        uploads = self.config.uploads_dir
        uploads.mkdir(parents=True, exist_ok=True)
        safe_code = re.sub(r"[^\w-]+", "_", parsed.code)[:40]
        path = uploads / f"{safe_code}_{self.now().strftime('%Y%m%d-%H%M%S')}.xlsx"
        path.write_bytes(data)
        result = self.db.apply_import(parsed, file_path=str(path), source_type=source_type,
                                      source_url=source_url, project_id=project_id, workspace_id=workspace_id)
        self._cleanup_uploads(safe_code)
        project = result.project
        audit = logic.audit(project, self.db.list_tasks(project.id), self.team(),
                            self.db.list_milestones(project.id), self.today())
        await self._notify_assignments(result)
        return result, parsed, audit

    def _cleanup_uploads(self, safe_code: str) -> None:
        files = sorted(self.config.uploads_dir.glob(f"{safe_code}_*.xlsx"))
        for old in files[:-KEEP_UPLOADS]:
            old.unlink(missing_ok=True)

    async def connect_sheet(self, url: str, actor: Member,
                            workspace_id: int | None = None) -> tuple[ImportResult, ParsedWorkbook, list[dict]]:
        self._require_admin(actor)
        data = await self.sheets.download(url)
        return await self.import_file(data, "google_sheet.xlsx", actor, source_type="gsheet", source_url=url,
                                      workspace_id=workspace_id)

    async def sync_project(self, project: Project) -> tuple[ImportResult, ParsedWorkbook, list[dict]]:
        if project.source_type != "gsheet" or not project.source_url:
            raise ValueError("Проект не связан с Google Таблицей — подключите её командой /sheet ссылка")
        try:
            data = await self.sheets.download(project.source_url)
            return await self.import_file(data, "google_sheet.xlsx", None, source_type="gsheet",
                                          source_url=project.source_url, project_id=project.id)
        except Exception as exc:
            self.db.update_project(project.id, last_sync_error=str(exc)[:500])
            raise

    def export(self, project: Project) -> tuple[bytes, str]:
        today = self.today()
        tasks = self.db.list_tasks(project.id)
        comments = self.db.comment_lines(project.id)
        if project.file_path and Path(project.file_path).exists() and project.layout.get("columns"):
            data = export_workbook(project.file_path, project.layout, tasks, today, comments)
        else:
            data = build_workbook(project.name, project.code, project.event_date, tasks, today, comments)
        return data, f"{project.code}_{today.strftime('%d-%m-%Y')}.xlsx"

    async def send_export(self, project: Project, actor: Member) -> bool:
        if not actor.telegram_id:
            raise ValueError("Сначала нажмите /start в личке с ботом")
        data, filename = self.export(project)
        caption = f"📥 {project.name}\nАктуальные статусы на {self.today().strftime('%d.%m.%Y')}"
        return await self.notifier.send_document(actor.telegram_id, data, filename, caption)

    # ---------- задачи ----------
    def get_task(self, task_id: int) -> Task:
        task = self.db.get_task(task_id)
        if not task or task.archived:
            raise LookupError("Задача не найдена")
        return task

    async def set_status(self, task: Task, status: str, actor: Member) -> Task:
        self.require_task_access(actor, task)
        if status not in STATUS_LABELS:
            raise ValueError("Неизвестный статус")
        today = self.today()
        fields: dict = {"status": status}
        if status in DONE_STATUSES:
            status = DONE_LATE if task.deadline and today > task.deadline else DONE
            fields = {"status": status, "fact_date": today, "blocked": False, "blocked_reason": "", "eta": None}
        elif task.status in DONE_STATUSES:
            fields["fact_date"] = None
        if status == CANCELLED:
            fields.update(blocked=False, blocked_reason="")
        updated = self.db.update_task(task.id, **fields)
        self._event(task.id, "status", STATUS_LABELS[status], actor)
        if status in DONE_STATUSES and task.status not in DONE_STATUSES:
            late = " (с просрочкой)" if status == DONE_LATE else ""
            await self._to_group(f"✅ {texts.e(actor.mention)} закрыл(а) «{texts.e(task.title)}»{late}")
        self._queue_write(updated)
        return updated

    async def add_comment(self, task: Task, text: str, actor: Member) -> None:
        self.require_task_access(actor, task)
        text = text.strip()
        if not text:
            raise ValueError("Пустой комментарий")
        self._event(task.id, "comment", text[:2000], actor)
        self._queue_write(task)

    async def report_problem(self, task: Task, text: str, actor: Member) -> Task:
        self.require_task_access(actor, task)
        text = text.strip() or "нужна помощь"
        updated = self.db.update_task(task.id, blocked=True, blocked_reason=text[:500])
        self._event(task.id, "problem", text[:2000], actor)
        today = self.today()
        message = (f"🆘 <b>{texts.e(actor.name)}</b> просит помощи\n\n"
                   f"{texts.task_line(updated, today)}\n\n💬 {texts.e(text)}")
        buttons = [keyboards.app_button(self.webapp_url, "Открыть задачу", f"?task={task.id}")]
        if actor.username:
            buttons.append(InlineKeyboardButton(text=f"Написать {actor.name}", url=f"https://t.me/{actor.username}"))
        markup = InlineKeyboardMarkup(inline_keyboard=[[b for b in buttons if b]]) if any(buttons) else None
        for pm in self._pms():
            if pm.id != actor.id and pm.telegram_id:
                await self.notifier.send(pm.telegram_id, message, markup)
        await self._to_group(f"🆘 {texts.e(actor.mention)}: нужна помощь по «{texts.e(task.title)}» — {texts.e(text)}")
        self._queue_write(updated)
        return updated

    async def resolve_problem(self, task: Task, actor: Member) -> Task:
        self.require_task_access(actor, task)
        self._event(task.id, "resolved", "блокер снят", actor)
        return self.db.update_task(task.id, blocked=False, blocked_reason="")

    async def confirm_on_track(self, task: Task, actor: Member) -> None:
        self.require_task_access(actor, task)
        self._event(task.id, "checkin", "успевает к сроку", actor)

    async def set_eta(self, task: Task, days: int, actor: Member) -> Task:
        self.require_task_access(actor, task)
        eta = self.today() + timedelta(days=days)
        if task.status == TODO:
            self.db.update_task(task.id, status=PROGRESS)
        self._event(task.id, "eta", f"обещает закрыть к {eta.strftime('%d.%m')}", actor)
        return self.db.update_task(task.id, eta=eta)

    def _clean_fields(self, data: dict) -> dict:
        fields: dict = {}
        for key, value in data.items():
            if key not in EDITABLE_TASK_FIELDS:
                continue
            if key in {"start_date", "deadline"}:
                fields[key] = parse_date(value, today=self.today()) if value else None
                if value and fields[key] is None:
                    raise ValueError(f"Не понял дату «{value}»")
            elif key == "priority":
                fields[key] = normalize_priority(value) if value else ""
            elif key == "status":
                if value not in STATUS_LABELS:
                    raise ValueError("Неизвестный статус")
                fields[key] = value
            else:
                fields[key] = str(value or "").strip()
        if "title" in fields and not fields["title"]:
            raise ValueError("Название задачи не может быть пустым")
        return fields

    async def create_task(self, project: Project, data: dict, actor: Member) -> Task:
        self._require_admin(actor)
        fields = self._clean_fields(data)
        if not fields.get("title"):
            raise ValueError("Укажите название задачи")
        status = fields.pop("status", TODO)
        task = self.db.create_task(project.id, status=status, **fields)
        self._event(task.id, "created", "задача создана в боте", actor)
        await self._notify_new_task(task, actor)
        self._queue_write(task)
        return task

    async def update_task(self, task: Task, data: dict, actor: Member) -> Task:
        self._require_admin(actor)
        fields = self._clean_fields(data)
        status = fields.pop("status", None)
        changes = [k for k, v in fields.items() if getattr(task, k) != v]
        if not changes and not status:
            return task
        updated = self.db.update_task(task.id, **{k: fields[k] for k in changes})
        if changes:
            self._event(task.id, "edit", "изменено: " + ", ".join(changes), actor)
        if status and status != task.status:
            updated = await self.set_status(updated, status, actor)
        team = self.team()
        if "responsible" in changes:
            before = {m.id for m in team.match(task.responsible)[0]}
            for m in team.assignees(updated):
                if m.id not in before and m.telegram_id and m.id != actor.id:
                    await self.notifier.send(
                        m.telegram_id, "📌 <b>Вам назначена задача</b>\n\n" + texts.task_card(
                            updated, self._project(updated), self.today(), team),
                        keyboards.task_actions(updated, self.webapp_url))
        if "deadline" in changes and "responsible" not in changes:
            for m in team.assignees(updated):
                if m.telegram_id and m.id != actor.id:
                    await self.notifier.send(
                        m.telegram_id,
                        f"📅 Срок изменён: <b>{texts.e(updated.title)}</b>\n"
                        f"Новый срок: {texts.fmt_date(updated.deadline, with_weekday=True)}")
        self._queue_write(updated)
        return updated

    async def archive_task(self, task: Task, actor: Member) -> None:
        self._require_admin(actor)
        self.db.update_task(task.id, archived=True)
        self._event(task.id, "archived", "задача удалена", actor)

    def parse_quick_add(self, text: str) -> dict:
        """`/add @user 30.09 ! Название` → поля задачи. Порядок частей произвольный."""
        team = self.team()
        data: dict = {"responsible": "", "deadline": None, "priority": ""}
        words = text.split()
        rest: list[str] = []
        for word in words:
            if word.startswith("@") and not data["responsible"]:
                member = self.db.member_by_username(word)
                data["responsible"] = member.name if member else word.lstrip("@")
            elif data["deadline"] is None and re.fullmatch(r"\d{1,2}[./]\d{1,2}([./]\d{2,4})?", word):
                data["deadline"] = word
            elif word in {"!", "!!"} and not data["priority"]:
                data["priority"] = PRIORITIES[0] if word == "!!" else PRIORITIES[1]
            else:
                rest.append(word)
        if not data["responsible"] and rest:
            matched, _ = team.match(rest[0])
            if matched:
                data["responsible"] = matched[0].name
                rest = rest[1:]
        data["title"] = " ".join(rest).strip()
        return {k: v for k, v in data.items() if v}

    async def apply_plan(self, project: Project, items: list[dict], actor: Member) -> int:
        self._require_admin(actor)
        applied = 0
        for item in items:
            task = self.db.get_task(int(item["task_id"]))
            if not task or task.project_id != project.id or task.deadline or not task.is_open():
                continue
            deadline = date.fromisoformat(item["deadline"])
            updated = self.db.update_task(task.id, deadline=deadline)
            self._event(task.id, "edit", f"срок из черновика плана: {deadline.strftime('%d.%m')}", actor)
            self._queue_write(updated)
            applied += 1
        return applied

    def _project(self, task: Task) -> Project:
        return self.db.get_project(task.project_id)  # type: ignore[return-value]

    # ---------- обзвон ----------
    def call_items(self, project: Project) -> list[dict]:
        today = self.today()
        return logic.call_list(self.db.list_tasks(project.id), self.team(), self.db.list_calls(project.id),
                               self.db.call_checks(project.id, today), today, self.now())

    def check_call_item(self, project: Project, key: str, actor: Member, note: str = "", checked: bool = True) -> None:
        if actor.is_observer and not actor.is_admin:
            raise AccessError("Наблюдатель только просматривает обзвон")
        self.db.set_call_check(project.id, self.today(), key, actor.id, note.strip()[:500], checked, actor.name)
        if checked and key.startswith("call:"):
            call = self.db.get_call(int(key.split(":", 1)[1]))
            if call and call.status == "planned":
                self.db.update_call(call.id, status="done", result=note.strip()[:500], done_at=self.now())

    async def create_call(self, project: Project, data: dict, actor: Member) -> Call:
        contact = str(data.get("contact", "")).strip()
        if not contact:
            raise ValueError("Кому звонить?")
        due_raw = str(data.get("due_at", "")).strip()
        try:
            due_at = datetime.fromisoformat(due_raw)
        except ValueError as exc:
            raise ValueError("Укажите дату и время звонка") from exc
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=self.config.tz)
        member_id = int(data["member_id"]) if data.get("member_id") else actor.id
        if member_id != actor.id:
            self._require_admin(actor)
        task_id = int(data["task_id"]) if data.get("task_id") else None
        call = self.db.create_call(project.id, contact=contact, due_at=due_at, member_id=member_id, task_id=task_id,
                                   phone=str(data.get("phone", "")).strip(), note=str(data.get("note", "")).strip(),
                                   created_by=actor.id)
        if task_id:
            self._event(task_id, "call", f"запланирован звонок: {contact} {due_at.strftime('%d.%m %H:%M')}", actor)
        return call

    async def finish_call(self, call: Call, actor: Member, result: str = "") -> Call:
        if call.member_id != actor.id and call.created_by != actor.id:
            self._require_admin(actor)
        call = self.db.update_call(call.id, status="done", result=result.strip()[:500], done_at=self.now())
        if call.task_id:
            self._event(call.task_id, "call", f"созвонились: {call.contact}" + (f" — {result}" if result else ""), actor)
        return call

    async def snooze_call(self, call: Call, until: datetime) -> Call:
        return self.db.update_call(call.id, due_at=until, notified_at=None)

    def cancel_call(self, call: Call, actor: Member) -> Call:
        if call.member_id != actor.id and call.created_by != actor.id:
            self._require_admin(actor)
        return self.db.update_call(call.id, status="cancelled")

    # ---------- уведомления ----------
    def _pms(self) -> list[Member]:
        members = self.db.list_members()
        pms = [m for m in members if m.is_pm]
        return pms or [m for m in members if m.is_admin]

    def _digest_recipients(self) -> list[Member]:
        """Утреннюю сводку получают PM и наблюдатели."""
        return self._pms() + [m for m in self.db.list_members() if m.is_observer and not m.is_pm]

    async def _to_group(self, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
        chat_id = self.settings().get("group_chat_id")
        if chat_id:
            await self.notifier.send(int(chat_id), text, keyboard)

    async def _notify_new_task(self, task: Task, actor: Member) -> None:
        team = self.team()
        for m in team.assignees(task):
            if m.telegram_id and m.id != actor.id:
                await self.notifier.send(
                    m.telegram_id,
                    "📌 <b>Новая задача</b>\n\n" + texts.task_card(task, self._project(task), self.today(), team),
                    keyboards.task_actions(task, self.webapp_url))

    async def _notify_assignments(self, result: ImportResult) -> None:
        team = self.team()
        project = result.project
        added = set(result.added)
        new_by_member: dict[int, list[Task]] = defaultdict(list)
        for task in self.db.list_tasks(project.id):
            if not task.is_open():
                continue
            if task.id in added:
                before: set[int] = set()
            elif task.id in result.previous_responsible:
                before = {m.id for m in team.match(result.previous_responsible[task.id])[0]}
            else:
                continue
            for m in team.assignees(task):
                if m.id not in before:
                    new_by_member[m.id].append(task)
        today = self.today()
        for member_id, items in new_by_member.items():
            member = team.by_id[member_id]
            if not member.telegram_id:
                continue
            if len(items) <= 2:
                for task in items:
                    await self.notifier.send(
                        member.telegram_id,
                        "📌 <b>Вам назначена задача</b>\n\n" + texts.task_card(task, project, today, team),
                        keyboards.task_actions(task, self.webapp_url))
                continue
            top = logic.sorted_tasks(items, today)[:8]
            text = (f"📌 <b>Вам назначено {len(items)} задач</b> в проекте «{texts.e(project.name)}»\n\n"
                    + "\n".join(f"• {texts.task_line(t, today)}" for t in top)
                    + (f"\n…и ещё {len(items) - len(top)}" if len(items) > len(top) else "")
                    + "\n\nВсе задачи и сроки — в личном кабинете.")
            await self.notifier.send(member.telegram_id, text, keyboards.app_only(self.webapp_url, query="?tab=my"))

    # ---------- запись в Google Таблицу ----------
    def _queue_write(self, task: Task) -> None:
        if not self.sheets.can_write or self._write_queue is None:
            return
        project = self._project(task)
        if project.source_type == "gsheet" and project.source_url:
            self._write_queue.put_nowait(task.id)

    async def run_sheet_writer(self) -> None:
        """Фоновая очередь записи в Google Таблицу: по одной строке, чтобы не упереться в лимиты API."""
        self._write_queue = asyncio.Queue()
        while True:
            task_id = await self._write_queue.get()
            try:
                task = self.db.get_task(task_id)
                if not task:
                    continue
                project = self._project(task)
                comments = self.db.comment_lines(project.id).get(task.id, [])
                row = await self.sheets.write_task(project.source_url or "", project.layout, task, self.today(),
                                                   comments)
                if row and row != task.row_index:
                    self.db.conn.execute("UPDATE tasks SET row_index = ? WHERE id = ?", (row, task.id))
                    self.db.conn.commit()
            except Exception:  # noqa: BLE001 — ошибка записи не должна ронять бота
                log.exception("Failed to write task %s to Google Sheets", task_id)
            finally:
                self._write_queue.task_done()

    # ---------- плановые рассылки (вызывает планировщик) ----------
    async def send_morning(self, project: Project) -> None:
        today = self.today()
        settings = self.settings()
        weekly = str(today.weekday()) == settings.get("weekly_day", "0")
        limit = int(settings.get("checks_per_day") or 5)
        team = self.team()
        tasks = self.db.list_tasks(project.id)
        for member in team.members:
            if not member.telegram_id:
                continue
            focus = logic.personal_focus(member, tasks, team, today)
            if focus.open_total == 0:
                continue
            if (focus.has_urgent or focus.in_progress or weekly) and self.db.mark_sent(
                    f"morning:{project.id}:{today}:{member.id}"):
                await self.notifier.send(member.telegram_id,
                                         texts.personal_digest(member, project, focus, today, weekly),
                                         keyboards.app_only(self.webapp_url, "📋 Мои задачи", "?tab=my"))
            for task in logic.check_candidates(member, tasks, team, today, limit):
                if self.db.mark_sent(f"check:{today}:{task.id}:{member.id}"):
                    await self.notifier.send(member.telegram_id, texts.check_question(task, today),
                                             keyboards.done_check(task))

    async def send_evening(self, project: Project) -> None:
        today = self.today()
        team = self.team()
        tomorrow = today + timedelta(days=1)
        for task in self.db.list_tasks(project.id):
            if not task.is_open() or task.deadline != tomorrow:
                continue
            for member in team.assignees(task):
                if member.telegram_id and self.db.mark_sent(f"evening:{today}:{task.id}:{member.id}"):
                    await self.notifier.send(member.telegram_id, texts.evening_question(task, today),
                                             keyboards.evening_check(task))

    async def send_pm_digest(self, project: Project) -> None:
        today = self.today()
        team = self.team()
        tasks = self.db.list_tasks(project.id)
        since = datetime.combine(today - timedelta(days=1), self._morning_time(), self.config.tz)
        since_utc = since.astimezone(timezone.utc).isoformat(timespec="seconds")
        done = [e for e in self.db.events_since(project.id, since_utc, ("status",))
                if e["text"] in (STATUS_LABELS[DONE], STATUS_LABELS[DONE_LATE])]
        audit = logic.audit(project, tasks, team, self.db.list_milestones(project.id), today)
        calls = [i for i in self.call_items(project) if not i["check"]]
        text = texts.pm_digest(project, tasks, team, today, attention=logic.attention(tasks, today),
                               done_yesterday=done, calls_count=len(calls), audit=audit)
        for pm in self._digest_recipients():
            if pm.telegram_id and self.db.mark_sent(f"pm:{project.id}:{today}:{pm.id}"):
                await self.notifier.send(pm.telegram_id, text, keyboards.pm_actions(self.webapp_url))
        if self.settings().get("group_chat_id") and self.db.mark_sent(f"group:{project.id}:{today}"):
            text = texts.group_digest(project, tasks, team, today, logic.workload(tasks, team, today))
            await self._to_group(text, self._bot_link_keyboard())

    async def send_standup(self, project: Project) -> None:
        today = self.today()
        if not self.db.mark_sent(f"standup:{project.id}:{today}"):
            return
        text = (f"🗣 Через 15 минут статус-созвон по проекту «{texts.e(project.name)}».\n"
                "Подготовьте: что закрыли, что в работе, где нужна помощь.")
        if self.settings().get("group_chat_id"):
            await self._to_group(text)
            return
        for m in self.team().members:
            if m.telegram_id:
                await self.notifier.send(m.telegram_id, text)

    async def send_due_calls(self) -> None:
        now = self.now()
        team = self.team()
        for call in self.db.due_calls(now):
            self.db.update_call(call.id, notified_at=now)
            member = team.by_id.get(call.member_id) if call.member_id else None
            if not member or not member.telegram_id:
                continue
            lines = [f"📞 <b>Пора позвонить:</b> {texts.e(call.contact)}"]
            if call.phone:
                lines.append(f"Телефон: {texts.e(call.phone)}")
            if call.task_id:
                task = self.db.get_task(call.task_id)
                if task:
                    lines.append(f"По задаче: {texts.e(task.title)}")
            if call.note:
                lines.append(f"💬 {texts.e(call.note)}")
            await self.notifier.send(member.telegram_id, "\n".join(lines), keyboards.call_actions(call.id))

    def _morning_time(self) -> time:
        return parse_hhmm(self.settings()["morning_time"]) or time(9, 0)

    def _bot_link_keyboard(self) -> InlineKeyboardMarkup | None:
        if not self.bot_username:
            return None
        return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="📋 Мои задачи в боте", url=f"https://t.me/{self.bot_username}?start=my")]])


def parse_hhmm(value: str) -> time | None:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", value or "")
    if not match:
        return None
    return time(int(match.group(1)), int(match.group(2)))
