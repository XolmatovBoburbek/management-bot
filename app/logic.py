"""Аналитика без побочных эффектов: сроки, нагрузка, обзвон, аудит таблицы, черновик дедлайнов."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.dates import plural
from app.models import (
    CANCELLED,
    CLOSED_STATUSES,
    DONE_STATUSES,
    OVERDUE,
    PRIORITY_RANK,
    PROGRESS,
    TODO,
    Call,
    Member,
    Milestone,
    Project,
    Task,
    norm_text,
)

SOON_DAYS = 3
WEEK_DAYS = 7
OVERLOAD_WEEK_TASKS = 6
MAX_DEADLINES_PER_DAY = 3

BUCKETS = ["overdue", "today", "tomorrow", "week", "later", "nodate", "done", "cancelled"]
BUCKET_TITLES = {
    "overdue": "Просрочено",
    "today": "Сегодня",
    "tomorrow": "Завтра",
    "week": "На этой неделе",
    "later": "Позже",
    "nodate": "Без срока",
    "done": "Выполнено",
    "cancelled": "Отменено",
}
INTERNAL_CONTRACTORS = {"iac agency", "iac media", "iac", "все команды"}

_SPLIT = re.compile(r"\s*(?:/|,|;|&|\+|\sи\s)\s*")


class Team:
    """Сопоставление имён из колонки «Ответственный» с участниками команды."""

    def __init__(self, members: list[Member]):
        self.members = [m for m in members if m.active]
        self.by_id = {m.id: m for m in self.members}
        self._keys: dict[str, Member] = {}
        self._helpers: dict[int, list[Member]] = defaultdict(list)
        for member in self.members:
            for key in member.match_keys():
                self._keys.setdefault(key, member)
            if member.assists_id and member.assists_id != member.id:
                self._helpers[member.assists_id].append(member)

    def match(self, responsible: str) -> tuple[list[Member], list[str]]:
        """Участники по колонке «Ответственный»; помощники получают задачи тех, кому помогают."""
        matched: list[Member] = []
        unknown: list[str] = []
        for part in _SPLIT.split(responsible or ""):
            key = norm_text(part)
            if not key:
                continue
            member = self._keys.get(key) or self._keys.get(key.split(" ")[0])
            if member:
                if member not in matched:
                    matched.append(member)
            else:
                unknown.append(part.strip())
        for member in list(matched):
            for helper in self._helpers.get(member.id, []):
                if helper not in matched:
                    matched.append(helper)
        return matched, unknown

    def assignees(self, task: Task) -> list[Member]:
        return self.match(task.responsible)[0]

    def tasks_of(self, member: Member, tasks: list[Task]) -> list[Task]:
        return [t for t in tasks if member in self.assignees(t)]


def bucket(task: Task, today: date) -> str:
    status = task.effective_status(today)
    if status in DONE_STATUSES:
        return "done"
    if status == CANCELLED:
        return "cancelled"
    if status == OVERDUE:
        return "overdue"
    days = task.days_left(today)
    if days is None:
        return "nodate"
    if days <= 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days <= WEEK_DAYS:
        return "week"
    return "later"


def sort_key(task: Task, today: date) -> tuple:
    return (
        BUCKETS.index(bucket(task, today)),
        0 if task.blocked else 1,
        task.deadline or date.max,
        PRIORITY_RANK.get(task.priority, 9),
        task.sort_order,
    )


def sorted_tasks(tasks: list[Task], today: date) -> list[Task]:
    return sorted(tasks, key=lambda t: sort_key(t, today))


def stats(tasks: list[Task], today: date) -> dict:
    counted = [t for t in tasks if t.status != CANCELLED]
    by_status = defaultdict(int)
    for t in counted:
        by_status[t.effective_status(today)] += 1
    done = sum(by_status[s] for s in DONE_STATUSES)
    open_tasks = [t for t in counted if t.status not in CLOSED_STATUSES]
    return {
        "total": len(counted),
        "done": done,
        "progress": by_status[PROGRESS],
        "todo": by_status[TODO],
        "overdue": by_status[OVERDUE],
        "nodate": sum(1 for t in open_tasks if not t.deadline),
        "critical_open": sum(1 for t in open_tasks if t.is_critical),
        "blocked": sum(1 for t in open_tasks if t.blocked),
        "due_today": sum(1 for t in open_tasks if t.deadline == today),
        "due_week": sum(1 for t in open_tasks if t.deadline and 0 <= (t.deadline - today).days <= WEEK_DAYS),
        "pct": round(100 * done / len(counted)) if counted else 0,
    }


def by_block(tasks: list[Task], today: date) -> list[dict]:
    groups: dict[str, list[Task]] = {}
    for t in tasks:
        if t.status == CANCELLED:
            continue
        groups.setdefault(t.block or "Без блока", []).append(t)
    rows = []
    for block, items in groups.items():
        done = sum(1 for t in items if t.status in DONE_STATUSES)
        rows.append({
            "block": block,
            "total": len(items),
            "done": done,
            "overdue": sum(1 for t in items if t.effective_status(today) == OVERDUE),
            "nodate": sum(1 for t in items if t.is_open() and not t.deadline),
            "pct": round(100 * done / len(items)) if items else 0,
        })
    return rows


def workload(tasks: list[Task], team: Team, today: date) -> list[dict]:
    rows: dict[int, dict] = {
        m.id: {"member_id": m.id, "name": m.name, "username": m.username, "role": m.role,
               "connected": m.telegram_id is not None, "total": 0, "open": 0, "done": 0, "overdue": 0,
               "critical_open": 0, "due_week": 0, "nodate": 0, "blocked": 0}
        for m in team.members
    }
    for t in tasks:
        if t.status == CANCELLED:
            continue
        for m in team.assignees(t):
            row = rows[m.id]
            row["total"] += 1
            if t.status in DONE_STATUSES:
                row["done"] += 1
                continue
            row["open"] += 1
            row["critical_open"] += int(t.is_critical)
            row["blocked"] += int(t.blocked)
            if t.effective_status(today) == OVERDUE:
                row["overdue"] += 1
            if not t.deadline:
                row["nodate"] += 1
            elif 0 <= (t.deadline - today).days <= WEEK_DAYS:
                row["due_week"] += 1
    # наблюдатель без задач — не часть рабочей нагрузки
    result = [r for r in rows.values() if r["total"] or not team.by_id[r["member_id"]].is_observer]
    open_counts = sorted(r["open"] for r in result if r["open"])
    median = open_counts[len(open_counts) // 2] if open_counts else 0
    for r in result:
        r["pct"] = round(100 * r["done"] / r["total"]) if r["total"] else 0
        r["overloaded"] = r["due_week"] >= OVERLOAD_WEEK_TASKS or (median >= 3 and r["open"] >= 1.6 * median)
    return sorted(result, key=lambda r: (-r["overdue"], -r["open"]))


def attention(tasks: list[Task], today: date, limit: int = 12) -> list[Task]:
    """Что требует внимания руководителя прямо сейчас."""
    items = []
    for t in tasks:
        if not t.is_open():
            continue
        b = bucket(t, today)
        if t.blocked or b in ("overdue", "today", "tomorrow") or (b == "week" and t.is_critical):
            items.append(t)
    return sorted_tasks(items, today)[:limit]


@dataclass
class PersonalFocus:
    overdue: list[Task]
    today: list[Task]
    soon: list[Task]
    in_progress: list[Task]
    nodate: list[Task]
    open_total: int
    done_total: int

    @property
    def has_urgent(self) -> bool:
        return bool(self.overdue or self.today or self.soon)


def personal_focus(member: Member, tasks: list[Task], team: Team, today: date) -> PersonalFocus:
    mine = [t for t in team.tasks_of(member, tasks) if t.status != CANCELLED]
    open_tasks = sorted_tasks([t for t in mine if t.is_open()], today)
    overdue = [t for t in open_tasks if bucket(t, today) == "overdue"]
    due_today = [t for t in open_tasks if bucket(t, today) == "today"]
    soon = [t for t in open_tasks if t.deadline and 1 <= (t.deadline - today).days <= SOON_DAYS]
    urgent_ids = {t.id for t in overdue + due_today + soon}
    return PersonalFocus(
        overdue=overdue,
        today=due_today,
        soon=soon,
        in_progress=[t for t in open_tasks if t.status == PROGRESS and t.id not in urgent_ids],
        nodate=[t for t in open_tasks if not t.deadline],
        open_total=len(open_tasks),
        done_total=sum(1 for t in mine if t.status in DONE_STATUSES),
    )


def check_candidates(member: Member, tasks: list[Task], team: Team, today: date, limit: int = 5) -> list[Task]:
    """Задачи, по которым утром спрашиваем «выполнено?»: срок сегодня или уже прошёл."""
    focus = personal_focus(member, tasks, team, today)
    return (focus.overdue + focus.today)[:limit]


def contractor_key(contractor: str) -> str:
    return "contractor:" + norm_text(contractor)[:80]


def is_external_contractor(contractor: str) -> bool:
    parts = [norm_text(p) for p in _SPLIT.split(contractor or "") if norm_text(p)]
    return any(p not in INTERNAL_CONTRACTORS for p in parts)


def call_list(tasks: list[Task], team: Team, calls: list[Call], checks: dict[str, dict], today: date,
              now: datetime | None = None, contractor_horizon: int = 5) -> list[dict]:
    """Список обзвона на день: кому из команды позвонить, каких подрядчиков дёрнуть, плановые звонки."""
    items: list[dict] = []
    open_tasks = [t for t in tasks if t.is_open()]

    per_member: dict[int, list[dict]] = defaultdict(list)
    for t in open_tasks:
        b = bucket(t, today)
        reason = None
        if t.blocked:
            reason = "нужна помощь: " + (t.blocked_reason or "блокер")
        elif b == "overdue":
            reason = "просрочено"
        elif b in ("today", "tomorrow") and t.status == TODO:
            reason = "срок " + ("сегодня" if b == "today" else "завтра") + ", работа не начата"
        elif b == "today":
            reason = "срок сегодня"
        if not reason:
            continue
        for m in team.assignees(t):
            per_member[m.id].append({"task_id": t.id, "title": t.title, "reason": reason,
                                     "deadline": t.deadline.isoformat() if t.deadline else None})
    for member_id, reasons in per_member.items():
        m = team.by_id[member_id]
        key = f"member:{m.id}"
        items.append({
            "key": key, "kind": "member", "title": m.name, "subtitle": m.role, "username": m.username,
            "phone": m.phone, "tasks": reasons, "priority": 0 if any("помощь" in r["reason"] for r in reasons) else 1,
            "check": checks.get(key),
        })

    per_contractor: dict[str, dict] = {}
    for t in open_tasks:
        if not t.contractor or not is_external_contractor(t.contractor):
            continue
        days = t.days_left(today)
        if days is None or days > contractor_horizon:
            continue
        key = contractor_key(t.contractor)
        entry = per_contractor.setdefault(key, {
            "key": key, "kind": "contractor", "title": t.contractor, "subtitle": "подрядчик / поставщик",
            "username": "", "phone": "", "tasks": [], "priority": 2, "check": checks.get(key),
            "owners": [],
        })
        entry["tasks"].append({"task_id": t.id, "title": t.title,
                               "reason": "просрочено" if days < 0 else f"срок {t.deadline.strftime('%d.%m')}",
                               "deadline": t.deadline.isoformat() if t.deadline else None})
        for m in team.assignees(t):
            if m.name not in entry["owners"]:
                entry["owners"].append(m.name)
    items.extend(per_contractor.values())

    for m in team.members:
        if m.telegram_id is None and team.tasks_of(m, open_tasks):
            key = f"connect:{m.id}"
            items.append({
                "key": key, "kind": "connect", "title": m.name, "subtitle": "не подключён к боту — попросить нажать Start",
                "username": m.username, "phone": m.phone, "tasks": [], "priority": 3, "check": checks.get(key),
            })

    for c in calls:
        if c.status != "planned" or c.due_at.date() > today:
            continue
        key = f"call:{c.id}"
        who = team.by_id.get(c.member_id) if c.member_id else None
        items.append({
            "key": key, "kind": "scheduled", "title": c.contact, "subtitle": c.note, "username": "",
            "phone": c.phone, "call_id": c.id, "due_at": c.due_at.isoformat(timespec="minutes"),
            "caller": who.name if who else "", "tasks": [], "priority": 1, "check": checks.get(key),
            "task_id": c.task_id,
        })
    return sorted(items, key=lambda i: (bool(i["check"]), i["priority"], i["title"]))


def audit(project: Project, tasks: list[Task], team: Team, milestones: list[Milestone], today: date) -> list[dict]:
    """Что не заполнено или противоречит в таблице — чтобы «сырая» таблица быстрее стала рабочей."""
    issues: list[dict] = []
    open_tasks = [t for t in tasks if t.is_open()]

    def add(level: str, title: str, items: list[str], hint: str = "") -> None:
        if items:
            issues.append({"level": level, "title": title, "count": len(items), "items": items[:30], "hint": hint})

    if not project.event_date:
        add("critical", "Не указана дата мероприятия", ["Дата мероприятия: «Уточнить»"],
            "Без даты бот не может предложить сроки и посчитать обратный отсчёт.")
    add("critical", "Критичные задачи без срока",
        [f"{t.title} — {t.responsible or 'нет ответственного'}" for t in open_tasks if t.is_critical and not t.deadline],
        "Заполните «Окончание работы» или примените черновик сроков.")
    add("warning", "Остальные задачи без срока",
        [f"{t.title} — {t.responsible or 'нет ответственного'}" for t in open_tasks
         if not t.is_critical and not t.deadline])
    add("critical", "Задачи без ответственного", [t.title for t in open_tasks if not t.responsible.strip()])
    unknown: dict[str, int] = defaultdict(int)
    for t in open_tasks:
        for name in team.match(t.responsible)[1]:
            unknown[name] += 1
    add("warning", "Ответственный не найден в команде",
        [f"{name} — {n} {plural(n, 'задача', 'задачи', 'задач')}" for name, n in sorted(unknown.items())],
        "Добавьте человека в «Команду» или исправьте имя в таблице.")
    add("warning", "Начало позже окончания",
        [t.title for t in tasks if t.start_date and t.deadline and t.start_date > t.deadline])
    if project.event_date:
        add("info", "Срок позже даты мероприятия",
            [f"{t.title} — {t.deadline.strftime('%d.%m')}" for t in open_tasks
             if t.deadline and t.deadline > project.event_date and not re.search(r"демонтаж|отч[её]т|post|контент|сверк|закрыва", norm_text(t.title + ' ' + t.block))],
            "Проверьте, что это действительно пост-ивент задачи.")
    load = workload(tasks, team, today)
    add("info", "Участники без задач",
        [r["name"] + (f" ({r['role']})" if r["role"] else "") for r in load if r["total"] == 0])
    add("warning", "Не подключены к боту (не получат напоминания)",
        [f"{m.name} @{m.username}" for m in team.members if m.telegram_id is None])
    add("warning", "Перегруз", [f"{r['name']}: открыто {r['open']}, на неделе {r['due_week']}"
                                for r in load if r["overloaded"]],
        "Подумайте о перераспределении задач.")
    add("info", "Вехи без даты", [m.title for m in milestones if not m.resolved_date(project.event_date)])
    return issues


# ---------- черновик сроков ----------
# (подстрока в названии задачи, смещение от дня мероприятия T в днях)
TITLE_RULES: list[tuple[str, int]] = [
    ("демонтаж", 1),
    ("передача контента", 3),
    ("сверка итогов", 7),
    ("закрывающий отчет", 7),
    ("отчет", 5),
    ("съемка мероприятия", 0),
    ("проверка перед открытием", 0),
    ("генеральн", -1),
    ("тест техники", -1),
    ("операционная проверка", -1),
    ("монтаж", -1),
    ("сборка и выдача", -1),
    ("call sheet", -2),
    ("брифинг", -2),
    ("контроль производства", -3),
    ("run of show", -6),
    ("скрипт", -5),
    ("cue", -4),
    ("доставка приглашений", -7),
    ("разрешени", -10),
    ("финальная передача в производство", -10),
    ("меню", -7),
    ("приглашени", -14),
    ("key visual", -18),
    ("финальный список гостей", -14),
    ("подтверждение шоурума", -21),
    ("подтверждение формата", -21),
    ("смета", -14),
]
BLOCK_RULES: list[tuple[str, int]] = [
    ("project management", -14),
    ("локация", -10),
    ("дизайн", -12),
    ("invitation", -10),
    ("персонал", -7),
    ("кейтеринг", -7),
    ("продакшн", -5),
    ("техническ", -5),
    ("тест-драйв", -5),
    ("монтаж", -1),
    ("медиа", 3),
    ("post-event", 3),
    ("смета", -10),
]
PRIORITY_DEFAULT = {"Критично": -10, "Высокий": -7}


def _offset_for(task: Task) -> tuple[int, str]:
    title = norm_text(task.title)
    for needle, offset in TITLE_RULES:
        if needle in title:
            return offset, f"по названию («{needle}»)"
    block = norm_text(task.block)
    for needle, offset in BLOCK_RULES:
        if needle in block:
            return offset, f"по блоку «{task.block}»"
    return PRIORITY_DEFAULT.get(task.priority, -5), "по приоритету"


def propose_deadlines(tasks: list[Task], team: Team, event_date: date, today: date) -> list[dict]:
    """Черновик дедлайнов для открытых задач без срока (обратное планирование от даты T).

    После расчёта сроки выравниваются по нагрузке: у одного человека не больше
    MAX_DEADLINES_PER_DAY дедлайнов в день — лишние сдвигаются на день раньше.
    """
    proposals: dict[int, dict] = {}
    for t in tasks:
        if not t.is_open() or t.deadline:
            continue
        offset, rule = _offset_for(t)
        deadline = event_date + timedelta(days=offset)
        note = ""
        if deadline < today:
            deadline, note = today, "сжато: расчётный срок уже прошёл"
        proposals[t.id] = {"task_id": t.id, "title": t.title, "block": t.block, "responsible": t.responsible,
                           "priority": t.priority, "offset": offset, "deadline": deadline, "rule": rule,
                           "note": note}

    by_member: dict[str, list[dict]] = defaultdict(list)
    for t in tasks:
        if t.id in proposals:
            owners = team.assignees(t)
            by_member[str(owners[0].id) if owners else "?" + t.responsible].append(proposals[t.id])
    fixed_per_day: dict[str, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    for t in tasks:
        if t.is_open() and t.deadline:
            owners = team.assignees(t)
            fixed_per_day[str(owners[0].id) if owners else "?" + t.responsible][t.deadline] += 1

    for owner, items in by_member.items():
        load = fixed_per_day[owner]
        # Идём от поздних сроков к ранним; в пределах дня первыми место занимают менее важные задачи,
        # поэтому при перегрузе раньше сдвигаются критичные — им нужен запас.
        for p in sorted(items, key=lambda p: (-p["deadline"].toordinal(), -PRIORITY_RANK.get(p["priority"], 9))):
            day = p["deadline"]
            while load[day] >= MAX_DEADLINES_PER_DAY and day > today and p["offset"] <= 0:
                day -= timedelta(days=1)
            if day != p["deadline"]:
                p["note"] = (p["note"] + "; " if p["note"] else "") + "сдвинуто раньше из-за нагрузки"
                p["deadline"] = day
            load[day] += 1

    result = sorted(proposals.values(), key=lambda p: (p["deadline"], PRIORITY_RANK.get(p["priority"], 9)))
    for p in result:
        p["deadline"] = p["deadline"].isoformat()
    return result
