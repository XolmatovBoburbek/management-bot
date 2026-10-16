"""Разбор дат из «сырых» таблиц: datetime, 25.10.2026, 25 октября, T-3 и т.п."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.models import norm_text

MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "мая": 5, "май": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}
RU_MONTHS_GEN = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
RU_WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

_RELATIVE = re.compile(r"^t\s*([+\-−–]\s*\d+)?$")
_NUMERIC = re.compile(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_WORDS = re.compile(r"^(\d{1,2})\s+([а-я]+)\.?(?:\s+(\d{4}))?")


def _guess_year(month: int, day: int, today: date) -> int:
    candidate = date(today.year, month, day)
    # «05.01» в конце года скорее означает следующий год.
    if (today - candidate).days > 180:
        return today.year + 1
    return today.year


def parse_date(value: object, event_date: date | None = None, today: date | None = None) -> date | None:
    """Возвращает дату или None, если значение пустое/нераспознанное («Уточнить»)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 30000 < value < 80000:
        return date(1899, 12, 30) + timedelta(days=int(value))
    text = norm_text(value)
    if not text:
        return None
    today = today or date.today()

    rel = _RELATIVE.match(text.replace("т", "t"))
    if rel:
        if not event_date:
            return None
        shift = rel.group(1)
        if not shift:
            return event_date
        shift = shift.replace(" ", "").replace("−", "-").replace("–", "-")
        return event_date + timedelta(days=int(shift))

    try:
        iso = _ISO.match(text)
        if iso:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        num = _NUMERIC.match(text)
        if num:
            day, month = int(num.group(1)), int(num.group(2))
            year_raw = num.group(3)
            if year_raw:
                year = int(year_raw)
                if year < 100:
                    year += 2000
            else:
                year = _guess_year(month, day, today)
            return date(year, month, day)
        words = _WORDS.match(text)
        if words:
            month = MONTHS.get(words.group(2)[:3])
            if month:
                day = int(words.group(1))
                year = int(words.group(3)) if words.group(3) else _guess_year(month, day, today)
                return date(year, month, day)
    except ValueError:
        return None
    return None


def fmt_date(value: date | None, with_weekday: bool = False) -> str:
    if not value:
        return "без срока"
    text = f"{value.day} {RU_MONTHS_GEN[value.month]}"
    if with_weekday:
        text += f" ({RU_WEEKDAYS[value.weekday()]})"
    return text


def fmt_short(value: date | None) -> str:
    return value.strftime("%d.%m") if value else "—"


def plural(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(n) % 100
    if 11 <= n_abs <= 14:
        return many
    last = n_abs % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def fmt_days_left(days: int | None) -> str:
    if days is None:
        return "без срока"
    if days < 0:
        n = -days
        return f"просрочено на {n} {plural(n, 'день', 'дня', 'дней')}"
    if days == 0:
        return "срок сегодня"
    if days == 1:
        return "срок завтра"
    return f"через {days} {plural(days, 'день', 'дня', 'дней')}"
