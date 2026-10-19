from datetime import date, datetime

from app.dates import fmt_days_left, parse_date, plural

EVENT = date(2026, 10, 8)
TODAY = date(2026, 9, 26)


def test_parse_common_formats():
    assert parse_date(datetime(2026, 10, 1, 12, 0)) == date(2026, 10, 1)
    assert parse_date("01.10.2026") == date(2026, 10, 1)
    assert parse_date("1/10/26") == date(2026, 10, 1)
    assert parse_date("2026-10-01") == date(2026, 10, 1)
    assert parse_date("3 октября", today=TODAY) == date(2026, 10, 3)
    assert parse_date("03.10", today=TODAY) == date(2026, 10, 3)


def test_short_date_in_january_means_next_year():
    assert parse_date("05.01", today=date(2026, 11, 20)) == date(2027, 1, 5)


def test_relative_to_event():
    assert parse_date("T", EVENT) == EVENT
    assert parse_date("T-1", EVENT) == date(2026, 10, 7)
    assert parse_date("T+3", EVENT) == date(2026, 10, 11)
    assert parse_date("Т-2", EVENT) == date(2026, 10, 6)  # кириллическая «Т»
    assert parse_date("T-1") is None  # без даты мероприятия считать не от чего


def test_placeholders_are_empty():
    for value in (None, "", "Уточнить", "T-?", "скоро", "31.02.2026"):
        assert parse_date(value, EVENT) is None


def test_days_left_wording():
    assert fmt_days_left(-2) == "просрочено на 2 дня"
    assert fmt_days_left(0) == "срок сегодня"
    assert fmt_days_left(5) == "через 5 дней"
    assert plural(21, "день", "дня", "дней") == "день"
    assert plural(12, "день", "дня", "дней") == "дней"
