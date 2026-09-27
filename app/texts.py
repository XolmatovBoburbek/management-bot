"""Тексты сообщений бота (HTML parse mode)."""
from __future__ import annotations

import re
from datetime import date
from html import escape

from app.dates import fmt_date, fmt_days_left, fmt_short, plural
from app.logic import PersonalFocus, Team, stats
from app.models import (
    PRIORITY_EMOJI,
    STATUS_EMOJI,
    STATUS_LABELS,
    Member,
    Project,
    Task,
)

MAX_LINES = 8


def e(value: object) -> str:
    return escape(str(value or ""), quote=False)


def countdown(project: Project, today: date) -> str:
    if not project.event_date:
        return "⚠️ дата мероприятия не указана"
    days = (project.event_date - today).days
    if days > 0:
        return f"до мероприятия {days} {plural(days, 'день', 'дня', 'дней')} ({fmt_date(project.event_date)})"
    if days == 0:
        return "🎉 мероприятие сегодня!"
    return f"мероприятие прошло {fmt_date(project.event_date)}"


def task_line(task: Task, today: date, team: Team | None = None, show_owner: bool = False) -> str:
    prio = PRIORITY_EMOJI.get(task.priority, "▫️")
    parts = [f"{prio} <b>{e(task.title)}</b>"]
    if task.deadline:
        parts.append(f"— {fmt_short(task.deadline)} ({fmt_days_left(task.days_left(today))})")
    if show_owner:
        owners = team.assignees(task) if team else []
        parts.append("· " + (", ".join(m.mention for m in owners) if owners else e(task.responsible or "без ответственного")))
    if task.blocked:
        parts.append("🆘")
    return " ".join(parts)


def task_card(task: Task, project: Project, today: date, team: Team) -> str:
    status = task.effective_status(today)
    owners = team.assignees(task)
    lines = [
        f"{PRIORITY_EMOJI.get(task.priority, '▫️')} <b>{e(task.title)}</b>",
        f"<i>{e(task.block)}</i>" if task.block else "",
        "",
        e(task.description) if task.description else "",
        "",
        f"Статус: {STATUS_EMOJI[status]} {STATUS_LABELS[status]}",
        f"Срок: {fmt_date(task.deadline, with_weekday=True)}"
        + (f" · {fmt_days_left(task.days_left(today))}" if task.deadline and task.is_open() else ""),
        f"Ответственный: {', '.join(m.mention for m in owners) if owners else e(task.responsible or '—')}",
    ]
    if task.contractor:
        lines.append(f"Подрядчик: {e(task.contractor)}")
    if task.proof:
        lines.append(f"Результат: {e(task.proof)}")
    if task.blocked:
        lines.append(f"🆘 Блокер: {e(task.blocked_reason)}")
    lines.append(f"\n<i>{e(project.name)}</i>")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _section(title: str, tasks: list[Task], today: date, limit: int = MAX_LINES) -> list[str]:
    if not tasks:
        return []
    lines = [f"\n{title} ({len(tasks)}):"]
    lines += [f"• {task_line(t, today)}" for t in tasks[:limit]]
    if len(tasks) > limit:
        lines.append(f"  …и ещё {len(tasks) - limit}")
    return lines


def personal_digest(member: Member, project: Project, focus: PersonalFocus, today: date, weekly: bool,
                    greeting: bool = True) -> str:
    head = f"☀️ Доброе утро, {e(member.name)}!" if greeting else "📋 <b>Ваши задачи</b>"
    lines = [head, f"<b>{e(project.name)}</b> — {countdown(project, today)}"]
    lines += _section("🔴 Просрочено", focus.overdue, today)
    lines += _section("🟠 Срок сегодня", focus.today, today)
    lines += _section("🟡 Ближайшие дни", focus.soon, today)
    lines += _section("🔵 В работе", focus.in_progress, today, limit=5)
    if weekly and focus.nodate:
        lines += _section("⚪️ Без срока — уточните дедлайн у PM", focus.nodate, today, limit=5)
    elif focus.nodate:
        lines.append(f"\n⚪️ Без срока: {len(focus.nodate)}")
    if not focus.open_total:
        lines.append("\nОткрытых задач нет 🎉")
    else:
        lines.append(f"\nОткрыто: {focus.open_total} · выполнено: {focus.done_total}")
    return "\n".join(lines)


def check_question(task: Task, today: date) -> str:
    days = task.days_left(today)
    if days is not None and days < 0:
        head = f"⏰ Задача просрочена ({fmt_days_left(days)})."
    else:
        head = "⏰ Сегодня срок по задаче."
    return f"{head}\n\n{task_line(task, today)}\n\n<b>Выполнили?</b>"


def evening_question(task: Task, today: date) -> str:
    return f"🌆 Завтра срок:\n\n{task_line(task, today)}\n\n<b>Успеваете?</b>"


def pm_digest(project: Project, tasks: list[Task], team: Team, today: date, *, attention: list[Task],
              done_yesterday: list[dict], calls_count: int, audit: list[dict]) -> str:
    s = stats(tasks, today)
    lines = [
        f"📊 <b>Сводка: {e(project.name)}</b>",
        countdown(project, today),
        f"Готово {s['done']}/{s['total']} ({s['pct']}%) · в работе {s['progress']} · "
        f"просрочено {s['overdue']} · без срока {s['nodate']}",
    ]
    blocked = [t for t in attention if t.blocked]
    rest = [t for t in attention if not t.blocked]
    if blocked:
        lines.append("\n🆘 <b>Нужна помощь:</b>")
        lines += [f"• {task_line(t, today, team, show_owner=True)}\n  <i>{e(t.blocked_reason)}</i>" for t in blocked]
    if rest:
        lines.append("\n🔥 <b>Требует внимания:</b>")
        lines += [f"• {task_line(t, today, team, show_owner=True)}" for t in rest[:MAX_LINES]]
    if done_yesterday:
        lines.append(f"\n✅ <b>Закрыто за сутки ({len(done_yesterday)}):</b>")
        lines += [f"• {e(d['task_title'])} — {e(d['member_name'] or '')}" for d in done_yesterday[:MAX_LINES]]
    if calls_count:
        lines.append(f"\n📞 Обзвон на сегодня: {calls_count} {plural(calls_count, 'контакт', 'контакта', 'контактов')}")
    important = [i for i in audit if i["level"] in ("critical", "warning")]
    if important:
        lines.append("\n🧹 <b>Дозаполнить в таблице:</b>")
        lines += [f"• {e(i['title'])}: {i['count']}" for i in important[:5]]
    return "\n".join(lines)


def group_digest(project: Project, tasks: list[Task], team: Team, today: date, load: list[dict]) -> str:
    s = stats(tasks, today)
    lines = [
        f"📊 <b>{e(project.name)}</b> — {countdown(project, today)}",
        f"Готово {s['done']}/{s['total']} ({s['pct']}%) · просрочено {s['overdue']} · сегодня срок {s['due_today']}",
    ]
    hot = [r for r in load if r["overdue"] or r["blocked"]]
    if hot:
        lines.append("\n🔴 Горит:")
        for r in hot:
            mention = f"@{r['username']}" if r["username"] else r["name"]
            bits = []
            if r["overdue"]:
                bits.append(f"просрочено {r['overdue']}")
            if r["blocked"]:
                bits.append(f"нужна помощь {r['blocked']}")
            lines.append(f"• {e(mention)} — {', '.join(bits)}")
    due = [t for t in tasks if t.is_open() and t.deadline == today]
    if due:
        lines.append("\n📅 Срок сегодня:")
        lines += [f"• {task_line(t, today, team, show_owner=True)}" for t in due[:MAX_LINES]]
    return "\n".join(lines)


def import_report(result, parsed, audit: list[dict]) -> str:
    project = result.project
    lines = [
        f"📥 <b>{e(project.name)}</b>",
        "Новый проект создан." if result.project_created else "Проект обновлён.",
        f"Задач: {len(parsed.tasks)} (новых {len(result.added)}, обновлено {result.updated}"
        + (f", удалено из таблицы {len(result.removed)}" if result.removed else "") + ")",
        f"Вех: {len(parsed.milestones)} · рисков: {len(parsed.risks)}",
    ]
    if result.status_changes_from_sheet and not result.project_created:
        lines.append(f"Статусов изменено из таблицы: {result.status_changes_from_sheet}")
    if parsed.warnings:
        lines.append("\n⚠️ " + "\n⚠️ ".join(e(w) for w in parsed.warnings[:5]))
    if audit:
        lines.append("\n🧹 <b>Что стоит дозаполнить:</b>")
        for issue in audit[:6]:
            lines.append(f"• {e(issue['title'])}: {issue['count']}")
    return "\n".join(lines)


HELP = """<b>Что умеет бот</b>

📋 /my — мои задачи и сроки
📊 /summary — сводка по проекту
📞 /calls — список обзвона на сегодня
➕ /add — быстро добавить задачу (админы):
<code>/add @username 30.09 Название задачи</code>

Админам:
📎 пришлите файл .xlsx — бот загрузит задачи
🔗 /sheet ссылка — подключить Google Таблицу (синхронизация каждые 10 минут)
🔄 /sync — синхронизировать сейчас
📥 /export — выгрузить актуальный Excel
👥 /bind_group — (в группе) присылать сюда сводки

Каждое утро бот пишет каждому в личку его задачи на сегодня и спрашивает «выполнено?» по горящим срокам, вечером — «успеваете?» по завтрашним. Кнопка «Открыть» внизу — личный кабинет с задачами и сроками."""
