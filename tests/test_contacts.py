"""Кто запускал бота: список для администратора, добавление в команду и связывание с аккаунтом."""
from pathlib import Path

from tests.test_bot import env, feed, message  # noqa: F401  (env — фикстура)
from tests.test_web import client, init_data, make_user, tg  # noqa: F401  (client — фикстура)


async def contacts(client, admin) -> dict:
    data = await (await client.get("/api/admin", headers=admin)).json()
    return {c["telegram_id"]: c for c in data["contacts"]}


async def test_bot_remembers_everyone_who_writes_in_private(env, service, notifier):
    await feed(env, message=message(5555, "ilhom_new", "/start"))   # не из команды
    await feed(env, message=message(4242, "asl_offf", "/start"))    # из команды — бот сам свяжет по нику
    await feed(env, message=message(6666, "group_guy", "/summary", chat_type="supergroup", chat_id=-100777))
    found = {c["telegram_id"]: c for c in service.bot_contacts()}
    assert found[5555]["username"] == "ilhom_new" and found[5555]["member_id"] is None
    assert found[4242]["member_name"] == "Асл"
    assert 6666 not in found  # в группе бота не «запускали»
    # руководителю — подсказка, где связать человека
    note = next(m for m in notifier.to(102) if "не из команды" in m)
    assert "Запустили бота" in note


async def test_admin_links_starter_to_team_and_cabinet(client, service):
    admin = await tg(client)
    # человек нажал Start / открыл кабинет из Telegram, но его нет в команде — входа нет, но он в списке
    resp = await client.post("/api/auth/telegram", json={"init_data": init_data(5555, "ilhom_new")})
    assert resp.status == 401
    starter = (await contacts(client, admin))[5555]
    assert starter["username"] == "ilhom_new" and starter["member_id"] is None and starter["full_name"] == "Test"

    # только главный администратор
    sarvar = await tg(client, 106, "gflwwc")
    assert (await client.post("/api/admin/contacts/5555/member", json={"name": "Ильхом"}, headers=sarvar)).status == 403

    # «Связать… → Новый участник»: в команде появляется Ильхом с ником и Telegram
    resp = await client.post("/api/admin/contacts/5555/member", json={"name": "Ильхом"}, headers=admin)
    member = (await resp.json())["data"]
    assert member["name"] == "Ильхом" and member["username"] == "ilhom_new" and member["connected"]
    # повторное нажатие не плодит участников
    again = (await (await client.post("/api/admin/contacts/5555/member", json={}, headers=admin)).json())["data"]
    assert again["id"] == member["id"]

    # аккаунт кабинета, связанный с ним, открывается из Telegram без пароля
    wid = (await (await client.get("/api/bootstrap", headers=admin)).json())["workspaces"][0]["id"]
    await make_user(client, admin, login="ilhom_new", name="Ильхом", member_id=member["id"], roles={str(wid): "member"})
    assert (await client.post("/api/auth/telegram", json={"init_data": init_data(5555, "ilhom_new")})).status == 200
    row = (await contacts(client, admin))[5555]
    assert row["member_name"] == "Ильхом" and row["user_login"] == "ilhom_new"

    # отвязать и связать с уже существующим участником
    assert (await client.post("/api/admin/contacts/5555/link", json={"member_id": None}, headers=admin)).status == 200
    assert (await contacts(client, admin))[5555]["member_id"] is None
    elshod = service.db.upsert_member(name="Элшод")
    resp = await client.post("/api/admin/contacts/5555/link", json={"member_id": elshod.id}, headers=admin)
    assert (await resp.json())["data"]["connected"]
    assert (await client.post("/api/admin/contacts/9999/link", json={"member_id": elshod.id}, headers=admin)).status == 404


def test_team_yaml_adds_new_spellings_to_existing_member(service, tmp_path: Path):
    service.db.upsert_member(name="Олимхон", username="khodjaevo")
    team_file = tmp_path / "team.yaml"
    team_file.write_text("members:\n  - name: Олимхон\n    username: khodjaevo\n    aliases: Олимжон, Olimxon\n",
                         encoding="utf-8")
    assert service.seed_team(team_file) == 0
    team = service.team()
    assert [m.name for m in team.match("Олимжон")[0]] == ["Олимхон"]
    service.seed_team(team_file)  # повторный запуск не дублирует варианты
    assert service.db.member_by_username("khodjaevo").aliases == "Олимжон, Olimxon"


async def test_admin_attaches_table_names_to_member(client, service, workbook):
    from tests.test_web import upload
    admin = await tg(client)
    await upload(client, workbook, admin)
    project = service.default_project()
    task = service.db.list_tasks(project.id)[0]
    service.db.update_task(task.id, responsible="Владимер")
    names = {u["name"]: u for u in (await (await client.get("/api/team/unmatched", headers=admin)).json())["data"]}
    assert names["Владимер"]["tasks"] == 1 and names["Владимер"]["projects"] == [project.name]

    vova = service.db.upsert_member(name="Владимир", username="vova")
    sarvar = await tg(client, 106, "gflwwc")
    assert (await client.get("/api/team/unmatched", headers=sarvar)).status == 403
    resp = await client.post(f"/api/members/{vova.id}/alias", json={"name": "Владимер"}, headers=admin)
    assert "Владимер" in (await resp.json())["data"]["aliases"]
    assert [m.name for m in service.team().assignees(service.get_task(task.id))] == ["Владимир"]
    names = {u["name"] for u in (await (await client.get("/api/team/unmatched", headers=admin)).json())["data"]}
    assert "Владимер" not in names
