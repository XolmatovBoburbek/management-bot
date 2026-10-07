from datetime import date

import openpyxl
import pytest

from app.db import Database
from app.excel_io import WorkbookError, build_workbook, export_workbook, parse_workbook
from app.models import DONE, PROGRESS, TODO
from tests.conftest import DEFAULT_TASKS, make_workbook

TODAY = date(2026, 9, 26)


def test_parse_real_layout(workbook):
    parsed = parse_workbook(workbook)
    assert parsed.code == "TEST_EVENT"
    assert parsed.name == "Тестовое открытие"
    assert parsed.event_date is None
    assert [t.title for t in parsed.tasks][:2] == ["Подтверждение формата, даты и бюджета", "Key Visual мероприятия"]
    kv = parsed.tasks[1]
    assert kv.status == PROGRESS and kv.deadline == date(2026, 10, 1) and kv.priority == "Критично"
    assert parsed.tasks[2].deadline == date(2026, 9, 28)
    assert parsed.tasks[0].deadline is None  # «Уточнить»
    assert parsed.tasks[3].status == DONE
    assert parsed.tasks[4].status == TODO
    assert parsed.layout["columns"]["status"] == 7
    assert "comment" in parsed.layout["columns"]
    assert len(parsed.milestones) == 2 and parsed.milestones[1].date_raw == "T-1"
    assert len(parsed.risks) == 2 and parsed.risks[0].owner == "Азмиддин"
    assert parsed.warnings == []


def test_columns_found_by_header_in_any_order(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Задача", "Дедлайн", "Ответственный", "Статус"])
    ws.append(["Сделать макет", "30.09.2026", "Сарвар", "в работе"])
    ws.append([None, None, None, None])
    ws.append(["Позвонить ведущему", "завтра?", "Сардор", ""])
    path = tmp_path / "simple.xlsx"
    wb.save(path)
    parsed = parse_workbook(path, fallback_name="Мой проект")
    assert parsed.code == "МОЙ_ПРОЕКТ"
    assert [t.title for t in parsed.tasks] == ["Сделать макет", "Позвонить ведущему"]
    assert parsed.tasks[0].deadline == date(2026, 9, 30) and parsed.tasks[0].status == PROGRESS
    assert any("завтра?" in w for w in parsed.warnings)


def test_not_a_task_table(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.append(["просто", "данные"])
    path = tmp_path / "x.xlsx"
    wb.save(path)
    with pytest.raises(WorkbookError):
        parse_workbook(path)
    with pytest.raises(WorkbookError):
        parse_workbook(b"not an excel file")


def test_event_date_resolves_relative_milestones(tmp_path):
    parsed = parse_workbook(make_workbook(tmp_path / "e.xlsx", event_date="08.10.2026"))
    assert parsed.event_date == date(2026, 10, 8)


def _import(db, path, **kw):
    return db.apply_import(parse_workbook(path), file_path=str(path), source_type="upload", **kw)


def test_reimport_keeps_bot_changes_but_takes_sheet_changes(tmp_path):
    db = Database(":memory:")
    first = make_workbook(tmp_path / "a.xlsx")
    result = _import(db, first)
    assert result.project_created and len(result.added) == 5
    tasks = {t.title: t for t in db.list_tasks(result.project.id)}

    # В боте: хостес → в работу, пресс-волл → выполнено, KV → новый срок
    db.update_task(tasks["Пресс-волл"].id, status=DONE, fact_date=TODAY)
    db.update_task(tasks["Key Visual мероприятия"].id, deadline=date(2026, 10, 3))
    db.update_task(tasks["Подтверждение формата, даты и бюджета"].id, deadline=date(2026, 9, 29))

    # В таблице: хостес вернули в работу, остальное без изменений
    changed = [list(t) for t in DEFAULT_TASKS]
    changed[3][6] = "в процессе работы"
    second = make_workbook(tmp_path / "b.xlsx", tasks=[tuple(t) for t in changed])
    result2 = _import(db, second)
    assert not result2.project_created and result2.added == [] and result2.updated == 5
    after = {t.title: t for t in db.list_tasks(result.project.id)}
    assert after["Команда хостес"].status == PROGRESS  # изменилось в таблице → из таблицы
    assert after["Пресс-волл"].status == DONE  # в таблице не менялось → правка бота остаётся
    assert after["Key Visual мероприятия"].deadline == date(2026, 10, 3)
    assert after["Подтверждение формата, даты и бюджета"].deadline == date(2026, 9, 29)


def test_reimport_archives_removed_and_matches_renamed(tmp_path):
    db = Database(":memory:")
    result = _import(db, make_workbook(tmp_path / "a.xlsx"))
    manual = db.create_task(result.project.id, title="Задача из бота", responsible="Асл")
    rows = [list(t) for t in DEFAULT_TASKS if t[0] != 5]
    rows[1][2] = "Key Visual (финал)"  # переименовали строку №2
    _import(db, make_workbook(tmp_path / "b.xlsx", tasks=[tuple(r) for r in rows]))
    titles = {t.title for t in db.list_tasks(result.project.id)}
    assert "Звук для ведущего" not in titles  # строку удалили из таблицы
    assert "Key Visual (финал)" in titles and "Key Visual мероприятия" not in titles
    assert "Задача из бота" in titles  # ручные задачи не трогаем
    kv = next(t for t in db.list_tasks(result.project.id) if t.title == "Key Visual (финал)")
    assert kv.status == PROGRESS and manual.id not in {kv.id}


def test_export_writes_statuses_and_appends_manual_tasks(tmp_path):
    db = Database(":memory:")
    path = make_workbook(tmp_path / "a.xlsx")
    result = _import(db, path)
    project = result.project
    tasks = {t.title: t for t in db.list_tasks(project.id)}
    db.update_task(tasks["Пресс-волл"].id, status=DONE, fact_date=TODAY)
    db.add_event(tasks["Пресс-волл"].id, "comment", "Макет утверждён")
    db.create_task(project.id, title="Новая задача", responsible="Асл", deadline=date(2026, 10, 2), priority="Высокий")
    comments = db.comment_lines(project.id)
    data = export_workbook(path, project.layout, db.list_tasks(project.id), TODAY, comments)

    wb = openpyxl.load_workbook(__import__("io").BytesIO(data))
    ws = wb[project.layout["tasks_sheet"]]
    assert ws.cell(4, 7).value == "выполнена"
    assert ws.cell(4, 11).value.date() == TODAY
    assert "Макет утверждён" in ws.cell(4, 14).value
    assert ws.cell(7, 3).value == "Новая задача" and ws.cell(7, 1).value == 6
    assert ws.cell(7, 6).value == "Асл"
    assert ws["V2"].value.startswith("=COUNTIF")  # формулы на месте

    # Повторная выгрузка после повторного импорта не дублирует комментарии
    reimport = tmp_path / "c.xlsx"
    reimport.write_bytes(data)
    _import(db, reimport)
    data2 = export_workbook(reimport, db.get_project(project.id).layout, db.list_tasks(project.id), TODAY,
                            db.comment_lines(project.id))
    ws2 = openpyxl.load_workbook(__import__("io").BytesIO(data2))[project.layout["tasks_sheet"]]
    assert ws2.cell(4, 14).value.count("Макет утверждён") == 1
    assert ws2.cell(8, 3).value is None  # ручная задача не задвоилась


def test_built_workbook_can_be_imported_back(tmp_path):
    db = Database(":memory:")
    project = db.create_project("Ручной проект", "MANUAL", date(2026, 10, 8))
    db.create_task(project.id, title="Первая", responsible="Сарвар", deadline=date(2026, 10, 1), priority="Критично")
    db.create_task(project.id, title="Вторая", responsible="Асл")
    data = build_workbook(project.name, project.code, project.event_date, db.list_tasks(project.id), TODAY)
    parsed = parse_workbook(data)
    assert parsed.code == "MANUAL" and parsed.event_date == date(2026, 10, 8)
    assert [(t.title, t.responsible, t.deadline) for t in parsed.tasks] == [
        ("Первая", "Сарвар", date(2026, 10, 1)), ("Вторая", "Асл", None)]
