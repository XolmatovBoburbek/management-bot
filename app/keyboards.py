from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from app.models import DONE_STATUSES, PROGRESS, Task


def _rows(*rows: list[InlineKeyboardButton] | None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


def app_button(webapp_url: str, text: str = "📋 Открыть кабинет", query: str = "") -> InlineKeyboardButton | None:
    if not webapp_url.startswith("https://"):
        return None
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=f"{webapp_url}/{query}"))


def app_only(webapp_url: str, text: str = "📋 Открыть кабинет", query: str = "") -> InlineKeyboardMarkup | None:
    button = app_button(webapp_url, text, query)
    return _rows([button]) if button else None


def task_actions(task: Task, webapp_url: str = "", in_private: bool = True) -> InlineKeyboardMarkup:
    first = []
    if task.status not in DONE_STATUSES:
        if task.status != PROGRESS:
            first.append(InlineKeyboardButton(text="▶️ В работу", callback_data=f"t:start:{task.id}"))
        first.append(InlineKeyboardButton(text="✅ Выполнено", callback_data=f"t:done:{task.id}"))
    second = [
        InlineKeyboardButton(text="🆘 Проблема", callback_data=f"t:prob:{task.id}"),
        InlineKeyboardButton(text="💬 Комментарий", callback_data=f"t:cmt:{task.id}"),
    ]
    button = app_button(webapp_url, "Подробнее", f"?task={task.id}") if in_private else None
    return _rows(first, second, [button] if button else None)


def done_check(task: Task) -> InlineKeyboardMarkup:
    """Утренний вопрос «Выполнили?» по задаче со сроком сегодня/просроченной."""
    return _rows(
        [InlineKeyboardButton(text="✅ Да, выполнено", callback_data=f"t:done:{task.id}")],
        [
            InlineKeyboardButton(text="⏳ Ещё в работе", callback_data=f"t:wip:{task.id}"),
            InlineKeyboardButton(text="🆘 Нужна помощь", callback_data=f"t:prob:{task.id}"),
        ],
    )


def evening_check(task: Task) -> InlineKeyboardMarkup:
    return _rows(
        [
            InlineKeyboardButton(text="👍 Успеваю", callback_data=f"t:ok:{task.id}"),
            InlineKeyboardButton(text="✅ Уже готово", callback_data=f"t:done:{task.id}"),
        ],
        [InlineKeyboardButton(text="🆘 Нужна помощь", callback_data=f"t:prob:{task.id}")],
    )


def eta_choice(task: Task) -> InlineKeyboardMarkup:
    return _rows(
        [
            InlineKeyboardButton(text="Сегодня", callback_data=f"t:eta0:{task.id}"),
            InlineKeyboardButton(text="Завтра", callback_data=f"t:eta1:{task.id}"),
            InlineKeyboardButton(text="Через 2–3 дня", callback_data=f"t:eta3:{task.id}"),
        ],
        [InlineKeyboardButton(text="🆘 Нужна помощь", callback_data=f"t:prob:{task.id}")],
    )


def call_actions(call_id: int) -> InlineKeyboardMarkup:
    return _rows(
        [InlineKeyboardButton(text="✅ Созвонился", callback_data=f"c:done:{call_id}")],
        [
            InlineKeyboardButton(text="⏰ Через час", callback_data=f"c:h1:{call_id}"),
            InlineKeyboardButton(text="📅 Завтра", callback_data=f"c:tm:{call_id}"),
        ],
    )


def pm_actions(webapp_url: str) -> InlineKeyboardMarkup | None:
    buttons = [
        app_button(webapp_url, "📊 Кабинет", "?tab=home"),
        app_button(webapp_url, "📞 Обзвон", "?tab=calls"),
    ]
    buttons = [b for b in buttons if b]
    return _rows(buttons) if buttons else None
