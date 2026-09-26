"""Доменные модели и словари статусов/приоритетов.

Подписи статусов совпадают с выпадающим списком в ERP-таблице, чтобы при
выгрузке обратно в Excel/Google Sheets значения оставались валидными.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

TODO = "todo"
PROGRESS = "progress"
DONE = "done"
DONE_LATE = "done_late"
OVERDUE = "overdue"
CANCELLED = "cancelled"

STATUS_LABELS = {
    TODO: "задача не начата",
    PROGRESS: "в процессе работы",
    DONE: "выполнена",
    DONE_LATE: "выполнена с просрочкой",
    OVERDUE: "просрочена",
    CANCELLED: "отменена",
}
STATUS_EMOJI = {
    TODO: "⚪️",
    PROGRESS: "🔵",
    DONE: "✅",
    DONE_LATE: "☑️",
    OVERDUE: "🔴",
    CANCELLED: "⚫️",
}
OPEN_STATUSES = {TODO, PROGRESS, OVERDUE}
DONE_STATUSES = {DONE, DONE_LATE}
CLOSED_STATUSES = DONE_STATUSES | {CANCELLED}

PRIORITY_CRITICAL = "Критично"
PRIORITIES = [PRIORITY_CRITICAL, "Высокий", "Средний", "Низкий"]
PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}
PRIORITY_EMOJI = {"Критично": "🟥", "Высокий": "🟧", "Средний": "🟨", "Низкий": "🟩"}


def norm_text(value: object) -> str:
    """Нормализация для сравнения: нижний регистр, ё→е, схлопнутые пробелы."""
    if value is None:
        return ""
    text = str(value).replace("ё", "е").replace("Ё", "Е").lower()
    return re.sub(r"\s+", " ", text).strip()


def normalize_status(raw: object) -> str:
    text = norm_text(raw)
    if not text or "не начат" in text:
        return TODO
    if "отмен" in text:
        return CANCELLED
    if "с просрочк" in text:
        return DONE_LATE
    if "просроч" in text:
        return OVERDUE
    if "процесс" in text or "в работе" in text or text in {"in progress", "progress"}:
        return PROGRESS
    if text.startswith("выполн") or text in {"готово", "сделано", "done"}:
        return DONE
    return TODO


def normalize_priority(raw: object) -> str:
    text = norm_text(raw)
    if not text:
        return ""
    if text.startswith("крит"):
        return "Критично"
    if text.startswith("выс"):
        return "Высокий"
    if text.startswith("сред"):
        return "Средний"
    if text.startswith("низ"):
        return "Низкий"
    return str(raw).strip()


def to_iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def from_iso(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


@dataclass
class Member:
    id: int
    name: str
    username: str = ""
    role: str = ""
    aliases: str = ""
    phone: str = ""
    is_admin: bool = False
    is_pm: bool = False
    telegram_id: int | None = None
    active: bool = True
    assists_id: int | None = None  # помогает этому участнику: получает те же задачи и напоминания

    @property
    def mention(self) -> str:
        return f"@{self.username}" if self.username else self.name

    def match_keys(self) -> set[str]:
        keys = {norm_text(self.name)}
        first = norm_text(self.name).split(" ")[0]
        if first:
            keys.add(first)
        for alias in re.split(r"[,;]", self.aliases or ""):
            if norm_text(alias):
                keys.add(norm_text(alias))
        if self.username:
            keys.add(norm_text(self.username))
            keys.add("@" + norm_text(self.username))
        keys.discard("")
        return keys

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "username": self.username,
            "role": self.role,
            "aliases": self.aliases,
            "phone": self.phone,
            "is_admin": self.is_admin,
            "is_pm": self.is_pm,
            "connected": self.telegram_id is not None,
            "active": self.active,
            "assists_id": self.assists_id,
        }


@dataclass
class Project:
    id: int
    code: str
    name: str
    event_date: date | None = None
    info: list = field(default_factory=list)
    source_type: str = "upload"
    source_url: str | None = None
    file_path: str | None = None
    layout: dict = field(default_factory=dict)
    sheet_values: dict = field(default_factory=dict)
    last_sync_at: str | None = None
    last_sync_error: str | None = None
    archived: bool = False
    updated_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "event_date": to_iso(self.event_date),
            "info": self.info,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "last_sync_at": self.last_sync_at,
            "last_sync_error": self.last_sync_error,
            "archived": self.archived,
        }


@dataclass
class Task:
    id: int
    project_id: int
    title: str
    no: str = ""
    block: str = ""
    description: str = ""
    priority: str = ""
    responsible: str = ""
    status: str = TODO
    start_date: date | None = None
    deadline: date | None = None
    fact_date: date | None = None
    contractor: str = ""
    proof: str = ""
    sheet_comment: str = ""
    blocked: bool = False
    blocked_reason: str = ""
    eta: date | None = None
    row_index: int | None = None
    sort_order: int = 0
    source: str = "sheet"
    sheet_values: dict = field(default_factory=dict)
    archived: bool = False
    updated_at: str | None = None

    @property
    def is_critical(self) -> bool:
        return self.priority == PRIORITY_CRITICAL

    def effective_status(self, today: date) -> str:
        """Статус с учётом дедлайна: открытая задача после срока — просрочена."""
        if self.status in (TODO, PROGRESS) and self.deadline and self.deadline < today:
            return OVERDUE
        return self.status

    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def days_left(self, today: date) -> int | None:
        if not self.deadline:
            return None
        return (self.deadline - today).days

    def to_dict(self, today: date) -> dict:
        eff = self.effective_status(today)
        return {
            "id": self.id,
            "project_id": self.project_id,
            "no": self.no,
            "block": self.block,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "responsible": self.responsible,
            "status": eff,
            "status_label": STATUS_LABELS[eff],
            "raw_status": self.status,
            "start_date": to_iso(self.start_date),
            "deadline": to_iso(self.deadline),
            "fact_date": to_iso(self.fact_date),
            "days_left": self.days_left(today),
            "contractor": self.contractor,
            "proof": self.proof,
            "sheet_comment": self.sheet_comment,
            "blocked": self.blocked,
            "blocked_reason": self.blocked_reason,
            "eta": to_iso(self.eta),
            "source": self.source,
        }


@dataclass
class Milestone:
    id: int
    project_id: int
    title: str
    date_raw: str = ""
    criteria: str = ""
    status: str = ""
    sort_order: int = 0

    def resolved_date(self, event_date: date | None) -> date | None:
        from app.dates import parse_date

        return parse_date(self.date_raw, event_date)

    def to_dict(self, event_date: date | None) -> dict:
        resolved = self.resolved_date(event_date)
        return {
            "id": self.id,
            "title": self.title,
            "date_raw": self.date_raw,
            "date": to_iso(resolved),
            "criteria": self.criteria,
            "status": self.status,
        }


@dataclass
class Risk:
    id: int
    project_id: int
    title: str
    no: str = ""
    probability: str = ""
    impact: str = ""
    scenario: str = ""
    mitigation: str = ""
    owner: str = ""
    status: str = ""

    @property
    def is_open(self) -> bool:
        text = norm_text(self.status)
        return not any(word in text for word in ("закрыт", "снят", "решен", "неактуал"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "no": self.no,
            "title": self.title,
            "probability": self.probability,
            "impact": self.impact,
            "scenario": self.scenario,
            "mitigation": self.mitigation,
            "owner": self.owner,
            "status": self.status,
            "is_open": self.is_open,
        }


@dataclass
class Call:
    id: int
    project_id: int
    member_id: int | None
    contact: str
    due_at: datetime
    task_id: int | None = None
    phone: str = ""
    note: str = ""
    status: str = "planned"
    result: str = ""
    notified_at: str | None = None
    created_by: int | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "member_id": self.member_id,
            "task_id": self.task_id,
            "contact": self.contact,
            "phone": self.phone,
            "note": self.note,
            "due_at": self.due_at.isoformat(timespec="minutes"),
            "status": self.status,
            "result": self.result,
        }


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
