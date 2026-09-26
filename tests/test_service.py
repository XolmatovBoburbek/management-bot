from datetime import date, datetime, timedelta

import pytest

from app.models import DONE, DONE_LATE, PROGRESS
from app.scheduler import Scheduler
from app.service import AccessError
from tests.conftest import TZ, make_workbook, member

SARVAR_TG, PM_TG, HEAD_TG, MUH_TG, SARDOR_TG = 106, 102, 101, 103, 107


async def load(service, workbook):
    result, parsed, audit = await service.import_file(workbook.read_bytes(), "erp.xlsx", member(service, "s_maxhan"))
    return result.project


def by_title(service, project):
    return {t.title: t for t in service.db.list_tasks(project.id)}


async def test_import_requires_admin_and_notifies_assignees(service, workbook, notifier):
    with pytest.raises(AccessError):
        await service.import_file(workbook.read_bytes(), "erp.xlsx", member(service, "gflwwc"))
    project = await load(service, workbook)
    assert project.name == "Тестовое открытие"
    sarvar_msgs = notifier.to(SARVAR_TG)
    assert len(sarvar_msgs) == 2 and all("Вам назначена задача" in m for m in sarvar_msgs)
    assert any("Пресс-волл" in m for m in notifier.to(SARDOR_TG))
    # повторный импорт того же файла — без повторных уведомлений
    notifier.messages.clear()
    await load(service, workbook)
    assert notifier.messages == []


async def test_status_flow_and_access(service, workbook, notifier, clock):
    project = await load(service, workbook)
    tasks = by_title(service, project)
    sarvar = member(service, "gflwwc")
    muh = member(service, "raxmanov7777")
    kv = tasks["Key Visual мероприятия"]
    with pytest.raises(AccessError):
        await service.set_status(kv, DONE, muh)  # чужая задача
    service.db.set_setting("group_chat_id", "-100500")
    updated = await service.set_status(kv, DONE, sarvar)
    assert updated.status == DONE and updated.fact_date == date(2026, 9, 26)
    assert any("закрыл(а) «Key Visual мероприятия»" in m for m in notifier.to(-100500))
    # после срока — «выполнена с просрочкой»
    press = tasks["Пресс-волл"]
    clock.value = datetime(2026, 9, 30, 12, 0, tzinfo=TZ)
    assert (await service.set_status(press, DONE, sarvar)).status == DONE_LATE
    # возврат в работу снимает дату факта
    back = await service.set_status(service.get_task(press.id), PROGRESS, sarvar)
    assert back.status == PROGRESS and back.fact_date is None


async def test_problem_escalates_to_pms(service, workbook, notifier):
    project = await load(service, workbook)
    kv = by_title(service, project)["Key Visual мероприятия"]
    notifier.messages.clear()
    updated = await service.report_problem(kv, "Клиент не утвердил цвета", member(service, "gflwwc"))
    assert updated.blocked and updated.blocked_reason == "Клиент не утвердил цвета"
    for pm in (PM_TG, HEAD_TG):
        assert any("просит помощи" in m and "Клиент не утвердил цвета" in m for m in notifier.to(pm))
    assert notifier.to(SARVAR_TG) == []
    resolved = await service.resolve_problem(updated, member(service, "s_maxhan"))
    assert not resolved.blocked


async def test_morning_digest_and_done_checks_are_sent_once(service, workbook, notifier, clock):
    project = await load(service, workbook)
    clock.value = datetime(2026, 10, 1, 9, 5, tzinfo=TZ)  # срок KV сегодня, пресс-волл просрочен
    notifier.messages.clear()
    await service.send_morning(project)
    sarvar = notifier.to(SARVAR_TG)
    assert "Доброе утро, Сарвар" in sarvar[0]
    questions = [m for m in sarvar if "Выполнили?" in m]
    assert len(questions) == 2
    assert any("Пресс-волл" in q and "просрочена" in q for q in questions)
    keyboard = [kb for cid, text, kb in notifier.messages if cid == SARVAR_TG and "Выполнили?" in text][0]
    assert [b.callback_data for b in keyboard.inline_keyboard[0]][0].startswith("t:done:")
    # у Азмиддина только задача без срока — в будний день дайджест не шлём
    assert notifier.to(HEAD_TG) == []
    count = len(notifier.messages)
    await service.send_morning(project)
    assert len(notifier.messages) == count


async def test_weekly_digest_reminds_about_tasks_without_deadline(service, workbook, notifier, clock):
    project = await load(service, workbook)
    clock.value = datetime(2026, 9, 28, 9, 5, tzinfo=TZ)  # понедельник
    notifier.messages.clear()
    await service.send_morning(project)
    head = notifier.to(HEAD_TG)
    assert head and "Без срока" in head[0] and "Подтверждение формата" in head[0]


async def test_evening_check_for_tomorrow(service, workbook, notifier, clock):
    project = await load(service, workbook)
    clock.value = datetime(2026, 9, 27, 18, 0, tzinfo=TZ)
    notifier.messages.clear()
    await service.send_evening(project)
    for tg in (SARVAR_TG, SARDOR_TG):  # пресс-волл на двоих, срок 28.09
        msgs = notifier.to(tg)
        assert len(msgs) == 1 and "Завтра срок" in msgs[0] and "Пресс-волл" in msgs[0]


async def test_eta_and_on_track_answers(service, workbook):
    project = await load(service, workbook)
    tasks = by_title(service, project)
    sarvar = member(service, "gflwwc")
    updated = await service.set_eta(tasks["Пресс-волл"], 1, sarvar)
    assert updated.eta == date(2026, 9, 27) and updated.status == PROGRESS
    await service.confirm_on_track(tasks["Key Visual мероприятия"], sarvar)
    kinds = [e["kind"] for e in service.db.task_events(tasks["Key Visual мероприятия"].id)]
    assert "checkin" in kinds


async def test_pm_and_group_digest(service, workbook, notifier, clock):
    project = await load(service, workbook)
    service.db.set_setting("group_chat_id", "-100500")
    clock.value = datetime(2026, 9, 29, 9, 30, tzinfo=TZ)
    notifier.messages.clear()
    await service.send_pm_digest(project)
    pm = notifier.to(PM_TG)[0]
    assert "Сводка" in pm and "Пресс-волл" in pm and "Дозаполнить в таблице" in pm
    group = notifier.to(-100500)[0]
    assert "@gflwwc" in group and "просрочено" in group
    count = len(notifier.messages)
    await service.send_pm_digest(project)
    assert len(notifier.messages) == count


async def test_manual_task_create_and_quick_add(service, workbook, notifier):
    project = await load(service, workbook)
    head = member(service, "gulyamov_330")
    data = service.parse_quick_add("@gflwwc 30.09 !! Макет флагов")
    assert data == {"responsible": "Сарвар", "deadline": "30.09", "priority": "Критично", "title": "Макет флагов"}
    assert service.parse_quick_add("Сардор Позвонить ведущему")["responsible"] == "Сардор"
    notifier.messages.clear()
    task = await service.create_task(project, data, head)
    assert task.deadline == date(2026, 9, 30) and task.source == "manual" and task.priority == "Критично"
    assert any("Новая задача" in m and "Макет флагов" in m for m in notifier.to(SARVAR_TG))
    with pytest.raises(AccessError):
        await service.create_task(project, {"title": "x"}, member(service, "gflwwc"))
    with pytest.raises(ValueError):
        await service.create_task(project, {"title": "x", "deadline": "когда-нибудь"}, head)


async def test_admin_edit_notifies_new_assignee(service, workbook, notifier):
    project = await load(service, workbook)
    kv = by_title(service, project)["Key Visual мероприятия"]
    notifier.messages.clear()
    updated = await service.update_task(kv, {"responsible": "Мухаммаджон", "deadline": "2026-10-05"},
                                        member(service, "s_maxhan"))
    assert updated.responsible == "Мухаммаджон" and updated.deadline == date(2026, 10, 5)
    assert any("Вам назначена задача" in m for m in notifier.to(MUH_TG))


async def test_plan_and_export(service, workbook, notifier):
    project = await load(service, workbook)
    pm = member(service, "s_maxhan")
    items = [{"task_id": t.id, "deadline": "2026-10-02"} for t in service.db.list_tasks(project.id) if not t.deadline]
    assert await service.apply_plan(project, items, pm) == 2  # открытые без срока (выполненная не трогается)
    await service.send_export(project, pm)
    chat_id, filename, data = notifier.documents[0]
    assert chat_id == PM_TG and filename.startswith("TEST_EVENT_") and data[:2] == b"PK"


async def test_calls(service, workbook, notifier, clock):
    project = await load(service, workbook)
    pm = member(service, "s_maxhan")
    call = await service.create_call(project, {"contact": "DJ Mayskiy", "phone": "+998901234567",
                                               "due_at": "2026-09-26T15:00", "note": "Райдер"}, pm)
    with pytest.raises(AccessError):
        await service.create_call(project, {"contact": "x", "due_at": "2026-09-26T15:00", "member_id": pm.id},
                                  member(service, "gflwwc"))
    notifier.messages.clear()
    await service.send_due_calls()
    assert notifier.messages == []
    clock.value = datetime(2026, 9, 26, 15, 1, tzinfo=TZ)
    await service.send_due_calls()
    await service.send_due_calls()
    assert len(notifier.to(PM_TG)) == 1 and "DJ Mayskiy" in notifier.to(PM_TG)[0]
    await service.snooze_call(call, clock.value + timedelta(hours=1))
    clock.value += timedelta(hours=1, minutes=1)
    await service.send_due_calls()
    assert len(notifier.to(PM_TG)) == 2
    items = service.call_items(project)
    assert any(i["key"] == f"call:{call.id}" for i in items)
    service.check_call_item(project, f"call:{call.id}", pm, "договорились")
    assert service.db.get_call(call.id).status == "done"


async def test_identify_binds_by_username(service):
    assert service.identify(999, "someone_else") is None
    asl = service.identify(555, "Asl_Offf")
    assert asl.name == "Асл" and service.db.member_by_telegram(555).id == asl.id


async def test_scheduler_windows(service, workbook, notifier, clock):
    project = await load(service, workbook)
    scheduler = Scheduler(service)
    clock.value = datetime(2026, 10, 1, 8, 59, tzinfo=TZ)
    notifier.messages.clear()
    await scheduler.tick()
    assert notifier.messages == []
    clock.value = datetime(2026, 10, 1, 9, 0, tzinfo=TZ)
    await scheduler.tick()
    assert any("Доброе утро" in m for m in notifier.to(SARVAR_TG))
    clock.value = datetime(2026, 10, 1, 9, 31, tzinfo=TZ)
    await scheduler.tick()
    assert any("Сводка" in m for m in notifier.to(PM_TG))
    # через 3 часа после времени утренней рассылки окно закрыто
    notifier.messages.clear()
    service.db.conn.execute("DELETE FROM notification_log")
    clock.value = datetime(2026, 10, 1, 12, 30, tzinfo=TZ)
    await scheduler.tick()
    assert not any("Доброе утро" in m for m in notifier.to(SARVAR_TG))
    # после мероприятия + 14 дней напоминания прекращаются
    service.db.update_project(project.id, event_date=date(2026, 9, 1))
    clock.value = datetime(2026, 10, 2, 9, 0, tzinfo=TZ)
    notifier.messages.clear()
    await scheduler.tick()
    assert notifier.messages == []


async def test_settings_validation(service):
    pm = member(service, "s_maxhan")
    assert service.update_settings({"morning_time": "08:30"}, pm)["morning_time"] == "08:30"
    with pytest.raises(ValueError):
        service.update_settings({"evening_time": "25:00"}, pm)
    with pytest.raises(AccessError):
        service.update_settings({"morning_time": "08:30"}, member(service, "gflwwc"))


async def test_pm_digest_lists_tasks_closed_since_yesterday_morning(service, workbook, notifier, clock):
    project = await load(service, workbook)
    kv = by_title(service, project)["Key Visual мероприятия"]
    # закрыли вчера в 23:30 по Ташкенту (18:30 UTC) — должно попасть в сегодняшнюю сводку
    clock.value = datetime(2026, 9, 26, 23, 30, tzinfo=TZ)
    await service.set_status(kv, DONE, member(service, "gflwwc"))
    clock.value = datetime(2026, 9, 27, 9, 30, tzinfo=TZ)
    notifier.messages.clear()
    await service.send_pm_digest(project)
    digest = notifier.to(PM_TG)[0]
    assert "Закрыто за сутки (1)" in digest and "Key Visual мероприятия — Сарвар" in digest


async def test_numeric_settings_and_member_validation(service):
    pm = member(service, "s_maxhan")
    with pytest.raises(ValueError):
        service.update_settings({"checks_per_day": "много"}, pm)
    with pytest.raises(ValueError):
        service.update_settings({"weekly_day": "9"}, pm)
    assert service.update_settings({"sync_minutes": "0"}, pm)["sync_minutes"] == "0"
    sarvar = member(service, "gflwwc")
    with pytest.raises(ValueError):
        service.save_member(pm, {"id": sarvar.id, "name": "Сарвар", "username": "msnuzz"})
    with pytest.raises(ValueError):
        service.save_member(pm, {"id": pm.id, "name": "Шахзод", "username": "s_maxhan", "is_admin": False})


async def test_comment_lines_use_local_date(service, workbook, clock):
    project = await load(service, workbook)
    kv = by_title(service, project)["Key Visual мероприятия"]
    service.db.tz = TZ
    clock.value = datetime(2026, 9, 27, 1, 0, tzinfo=TZ)  # в UTC это ещё 26.09
    await service.add_comment(kv, "Ночная правка", member(service, "gflwwc"))
    assert service.db.comment_lines(project.id)[kv.id] == ["[27.09 Сарвар] Ночная правка"]


async def test_helper_gets_same_tasks_and_reminders(service, tmp_path, notifier, clock):
    babur, shahzod = member(service, "rrkaier"), member(service, "s_maxhan")
    assert babur.assists_id == shahzod.id  # из config/team.yaml
    service.db.bind_telegram(babur.id, 108)
    rows = [(1, "ТЕХНИЧЕСКАЯ ЧАСТЬ", "Звуковой пакет", "", "Критично", "Шахзод", "задача не начата", None,
             date(2026, 9, 26), "Технический подрядчик", "", "")]
    wb = make_workbook(tmp_path / "s.xlsx", tasks=rows)
    await service.import_file(wb.read_bytes(), "s.xlsx", shahzod)
    assert any("Звуковой пакет" in m for m in notifier.to(108))
    notifier.messages.clear()
    await service.import_file(wb.read_bytes(), "s.xlsx", shahzod)
    assert notifier.messages == []  # повторная синхронизация не присылает помощнику то же самое
    project = service.default_project()
    clock.value = datetime(2026, 9, 26, 9, 5, tzinfo=TZ)
    await service.send_morning(project)
    assert any("Выполнили?" in m and "Звуковой пакет" in m for m in notifier.to(108))
    assert any("Выполнили?" in m and "Звуковой пакет" in m for m in notifier.to(PM_TG))


async def test_assists_validation(service):
    pm = member(service, "s_maxhan")
    sarvar = member(service, "gflwwc")
    with pytest.raises(ValueError):
        service.save_member(pm, {"id": sarvar.id, "name": "Сарвар", "username": "gflwwc", "assists_id": sarvar.id})
    with pytest.raises(ValueError):
        service.save_member(pm, {"id": sarvar.id, "name": "Сарвар", "username": "gflwwc", "assists_id": 9999})
    saved = service.save_member(pm, {"id": sarvar.id, "name": "Сарвар", "username": "gflwwc",
                                     "assists_id": str(pm.id), "active": True})
    assert saved.assists_id == pm.id
    cleared = service.save_member(pm, {"id": sarvar.id, "name": "Сарвар", "username": "gflwwc", "assists_id": "",
                                       "active": True})
    assert cleared.assists_id is None


def test_existing_database_gets_assists_column(tmp_path):
    import sqlite3

    from app.db import Database

    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE members (id INTEGER PRIMARY KEY, name TEXT NOT NULL, username TEXT UNIQUE, "
                 "role TEXT DEFAULT '', aliases TEXT DEFAULT '', phone TEXT DEFAULT '', is_admin INTEGER DEFAULT 0, "
                 "is_pm INTEGER DEFAULT 0, telegram_id INTEGER UNIQUE, active INTEGER DEFAULT 1, created_at TEXT)")
    conn.execute("INSERT INTO members(name, username, telegram_id) VALUES('Шахзод', 's_maxhan', 102)")
    conn.commit()
    conn.close()
    db = Database(path)
    old = db.member_by_username("s_maxhan")
    assert old.assists_id is None and old.telegram_id == 102
    helper = db.upsert_member(name="Бабур", username="rrkaier", assists_id=old.id)
    assert db.get_member(helper.id).assists_id == old.id
