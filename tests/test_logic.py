from datetime import date, timedelta

from app import logic
from app.models import DONE, PROGRESS, TODO, Member, Project, Task

TODAY = date(2026, 9, 26)


def members():
    return [
        Member(id=1, name="Азмиддин", username="gulyamov_330", is_admin=True, is_pm=True, telegram_id=1),
        Member(id=2, name="Сарвар", username="gflwwc", aliases="Sarvar", telegram_id=2),
        Member(id=3, name="Сардор", username="msnuzz"),
        Member(id=4, name="Мухаммаджон", username="raxmanov7777", aliases="Мухаммаджон Рахманов", telegram_id=4),
    ]


def task(i, title="T", responsible="Сарвар", deadline=None, status=TODO, priority="Высокий", **kw):
    return Task(id=i, project_id=1, title=title, responsible=responsible, deadline=deadline, status=status,
                priority=priority, **kw)


def test_team_matching():
    team = logic.Team(members())
    matched, unknown = team.match("Сарвар / Сардор, Максуд")
    assert [m.name for m in matched] == ["Сарвар", "Сардор"] and unknown == ["Максуд"]
    assert [m.name for m in team.match("sarvar")[0]] == ["Сарвар"]
    assert [m.name for m in team.match("@msnuzz")[0]] == ["Сардор"]
    assert [m.name for m in team.match("Мухаммаджон Рахманов")[0]] == ["Мухаммаджон"]
    assert [m.name for m in team.match("Сарвар и Сардор")[0]] == ["Сарвар", "Сардор"]


def test_buckets_and_effective_status():
    t = task(1, deadline=TODAY - timedelta(days=1))
    assert t.effective_status(TODAY) == "overdue" and logic.bucket(t, TODAY) == "overdue"
    assert logic.bucket(task(2, deadline=TODAY), TODAY) == "today"
    assert logic.bucket(task(3, deadline=TODAY + timedelta(days=1)), TODAY) == "tomorrow"
    assert logic.bucket(task(4, deadline=TODAY + timedelta(days=6)), TODAY) == "week"
    assert logic.bucket(task(5), TODAY) == "nodate"
    assert logic.bucket(task(6, deadline=TODAY - timedelta(days=3), status=DONE), TODAY) == "done"


def test_stats_and_workload():
    team = logic.Team(members())
    tasks = [
        task(1, deadline=TODAY - timedelta(days=1)),
        task(2, responsible="Мухаммаджон", status=DONE),
        task(3, responsible="Сардор", deadline=TODAY, blocked=True, blocked_reason="нет ответа"),
        task(4, responsible="Сарвар / Сардор", priority="Критично"),
    ]
    s = logic.stats(tasks, TODAY)
    assert (s["total"], s["done"], s["overdue"], s["blocked"], s["nodate"], s["pct"]) == (4, 1, 1, 1, 1, 25)
    load = {r["name"]: r for r in logic.workload(tasks, team, TODAY)}
    assert load["Сарвар"]["open"] == 2 and load["Сарвар"]["overdue"] == 1
    assert load["Сардор"]["blocked"] == 1 and load["Мухаммаджон"]["pct"] == 100


def test_personal_focus_and_checks():
    team = logic.Team(members())
    sarvar = team.by_id[2]
    tasks = [
        task(1, deadline=TODAY - timedelta(days=2)),
        task(2, deadline=TODAY),
        task(3, deadline=TODAY + timedelta(days=2)),
        task(4, deadline=TODAY + timedelta(days=10), status=PROGRESS),
        task(5),
        task(6, responsible="Сардор", deadline=TODAY),
    ]
    focus = logic.personal_focus(sarvar, tasks, team, TODAY)
    assert [t.id for t in focus.overdue] == [1] and [t.id for t in focus.today] == [2]
    assert [t.id for t in focus.soon] == [3] and [t.id for t in focus.in_progress] == [4]
    assert [t.id for t in focus.nodate] == [5] and focus.open_total == 5
    assert [t.id for t in logic.check_candidates(sarvar, tasks, team, TODAY)] == [1, 2]


def test_call_list():
    team = logic.Team(members())
    tasks = [
        task(1, deadline=TODAY - timedelta(days=1), contractor="IAC Agency"),
        task(2, responsible="Сардор", deadline=TODAY + timedelta(days=1), contractor="DJ Mayskiy / фриланс"),
        task(3, responsible="Мухаммаджон", deadline=TODAY + timedelta(days=3), status=PROGRESS,
             contractor="Модельное агентство"),
        task(4, responsible="Мухаммаджон", deadline=TODAY + timedelta(days=30), contractor="Далёкий подрядчик"),
    ]
    items = logic.call_list(tasks, team, [], {}, TODAY)
    keys = {i["key"]: i for i in items}
    assert keys["member:2"]["tasks"][0]["reason"] == "просрочено"
    assert "работа не начата" in keys["member:3"]["tasks"][0]["reason"]
    assert "contractor:dj mayskiy / фриланс" in keys and "contractor:модельное агентство" in keys
    assert "contractor:iac agency" not in keys  # внутренние исполнители не в обзвоне подрядчиков
    assert not any("далёкий" in k for k in keys)
    assert "connect:3" in keys  # Сардор не нажал Start, а задачи есть
    checked = logic.call_list(tasks, team, [], {"member:2": {"member_name": "Азмиддин"}}, TODAY)
    assert checked[-1]["key"] == "member:2"  # отмеченные уходят вниз


def test_audit_finds_gaps():
    team = logic.Team(members())
    project = Project(id=1, code="X", name="X")
    tasks = [task(1, priority="Критично"), task(2, responsible="Максуд", deadline=TODAY),
             task(3, responsible="", deadline=TODAY), task(4, start_date=TODAY, deadline=TODAY - timedelta(days=1))]
    titles = {i["title"]: i for i in logic.audit(project, tasks, team, [], TODAY)}
    assert "Не указана дата мероприятия" in titles
    assert titles["Критичные задачи без срока"]["count"] == 1
    assert titles["Ответственный не найден в команде"]["items"] == ["Максуд — 1 задача"]
    assert "Задачи без ответственного" in titles and "Начало позже окончания" in titles
    assert sorted(titles["Участники без задач"]["items"]) == ["Азмиддин", "Мухаммаджон", "Сардор"]


def test_propose_deadlines_respects_rules_and_load():
    team = logic.Team(members())
    event = TODAY + timedelta(days=30)
    tasks = [
        task(1, title="Key Visual мероприятия", priority="Критично"),
        task(2, title="Демонтаж и сдача площадки"),
        task(3, title="Уже со сроком", deadline=TODAY + timedelta(days=3)),
        task(4, title="Готово", status=DONE),
    ] + [task(10 + i, title=f"Монтаж зоны {i}", priority="Критично" if i == 0 else "Средний") for i in range(5)]
    proposals = {p["task_id"]: p for p in logic.propose_deadlines(tasks, team, event, TODAY)}
    assert set(proposals) == {1, 2, 10, 11, 12, 13, 14}
    assert proposals[1]["deadline"] == (event - timedelta(days=18)).isoformat()
    assert proposals[2]["deadline"] == (event + timedelta(days=1)).isoformat()
    per_day = {}
    for p in proposals.values():
        per_day[p["deadline"]] = per_day.get(p["deadline"], 0) + 1
    assert max(per_day.values()) <= logic.MAX_DEADLINES_PER_DAY
    # при перегрузе раньше сдвигается критичная задача
    assert proposals[10]["deadline"] < (event - timedelta(days=1)).isoformat()


def test_propose_deadlines_never_in_past():
    team = logic.Team(members())
    proposals = logic.propose_deadlines([task(1, title="Key Visual")], team, TODAY + timedelta(days=5), TODAY)
    assert proposals[0]["deadline"] == TODAY.isoformat() and "сжато" in proposals[0]["note"]


def test_helper_gets_tasks_of_member_they_assist():
    babur = Member(id=5, name="Бабур", username="rrkaier", assists_id=2)
    team = logic.Team(members() + [babur])
    assert [m.name for m in team.match("Сарвар")[0]] == ["Сарвар", "Бабур"]
    assert [m.name for m in team.match("Сарвар / Сардор")[0]] == ["Сарвар", "Сардор", "Бабур"]
    assert [m.name for m in team.match("Сардор")[0]] == ["Сардор"]
    tasks = [task(1, responsible="Сарвар"), task(2, responsible="Сардор")]
    assert [t.id for t in team.tasks_of(babur, tasks)] == [1]
    inactive = logic.Team(members() + [Member(id=5, name="Бабур", assists_id=2, active=False)])
    assert [m.name for m in inactive.match("Сарвар")[0]] == ["Сарвар"]
