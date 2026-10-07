from tests.test_web import client, default_wid, new_workspace, tg, upload  # noqa: F401  (client — фикстура)


async def project_with_tasks(client, workbook, admin) -> tuple[int, dict]:
    resp = await upload(client, workbook, admin)
    pid = (await resp.json())["data"]["project_id"]
    data = await (await client.get(f"/api/projects/{pid}", headers=admin)).json()
    return pid, {t["title"]: t for t in data["tasks"]}


async def test_custom_columns_move_cards_and_sync_status(client, workbook):
    admin = await tg(client)
    pid, tasks = await project_with_tasks(client, workbook, admin)
    data = await (await client.get(f"/api/projects/{pid}", headers=admin)).json()
    assert data["stages"] == []

    # включаем колонки по умолчанию, добавляем свою и ставим её второй
    resp = await client.post(f"/api/projects/{pid}/stages", json={"preset": "default"}, headers=admin)
    stages = (await resp.json())["data"]
    assert [(s["title"], s["status"]) for s in stages] == [("Нужно сделать", "todo"), ("В работе", "progress"),
                                                           ("Готово", "done")]
    resp = await client.post(f"/api/projects/{pid}/stages", json={"title": "Съёмка", "color": "purple"}, headers=admin)
    shoot = (await resp.json())["data"][-1]
    assert shoot["title"] == "Съёмка" and shoot["status"] == "" and shoot["color"] == "purple"
    resp = await client.post(f"/api/stages/{shoot['id']}/move", json={"index": 1}, headers=admin)
    assert [s["title"] for s in (await resp.json())["data"]] == ["Нужно сделать", "Съёмка", "В работе", "Готово"]
    done = next(s for s in stages if s["status"] == "done")

    # карточку в «Готово» — задача выполнена; обратно в «Съёмку» — снова в работе
    kv = tasks["Key Visual мероприятия"]
    moved = await client.post(f"/api/tasks/{kv['id']}/stage", json={"stage_id": done["id"]}, headers=admin)
    assert (await moved.json())["data"]["status"] in ("done", "done_late")
    back = await client.post(f"/api/tasks/{kv['id']}/stage", json={"stage_id": shoot["id"]}, headers=admin)
    body = (await back.json())["data"]
    assert body["status"] == "progress" and body["stage_id"] == shoot["id"]

    # закрыли задачу в «Съёмке» (например, из бота) — она уезжает из колонки к «Готово»
    closed = await client.post(f"/api/tasks/{kv['id']}/status", json={"status": "done"}, headers=admin)
    assert (await closed.json())["data"]["stage_id"] is None
    detail = await (await client.get(f"/api/tasks/{kv['id']}", headers=admin)).json()
    assert [s["title"] for s in detail["stages"]][1] == "Съёмка"
    assert any(e["kind"] == "stage" for e in detail["events"])

    # новая карточка прямо в колонке берёт статус колонки
    created = await client.post(f"/api/projects/{pid}/tasks", json={"title": "Монтаж ролика", "stage_id": done["id"]},
                                headers=admin)
    assert (await created.json())["data"]["status"] == "done"

    # переименовать и удалить колонку; задачи не пропадают
    resp = await client.patch(f"/api/stages/{shoot['id']}", json={"title": "Съёмка и монтаж", "status": "progress"},
                              headers=admin)
    assert (await resp.json())["data"] == {**shoot, "title": "Съёмка и монтаж", "status": "progress", "sort_order": 2}
    assert (await client.patch(f"/api/stages/{shoot['id']}", json={"title": " "}, headers=admin)).status == 400
    assert (await client.patch(f"/api/stages/{shoot['id']}", json={"status": "weird"}, headers=admin)).status == 400
    assert (await client.delete(f"/api/stages/{shoot['id']}", headers=admin)).status == 200
    data = await (await client.get(f"/api/projects/{pid}", headers=admin)).json()
    assert len(data["stages"]) == 3 and len(data["tasks"]) == len(tasks) + 1


async def test_column_rights_and_isolation(client, workbook):
    admin = await tg(client)
    pid, tasks = await project_with_tasks(client, workbook, admin)
    stages = (await (await client.post(f"/api/projects/{pid}/stages", json={"preset": "default"}, headers=admin))
              .json())["data"]
    progress = next(s for s in stages if s["status"] == "progress")

    # участник двигает свою карточку, но не настраивает колонки и не трогает чужие задачи
    sarvar = await tg(client, 106, "gflwwc")
    own = tasks["Key Visual мероприятия"]
    resp = await client.post(f"/api/tasks/{own['id']}/stage", json={"stage_id": progress["id"]}, headers=sarvar)
    assert resp.status == 200
    other = tasks["Команда хостес"]
    assert (await client.post(f"/api/tasks/{other['id']}/stage", json={"stage_id": progress["id"]},
                              headers=sarvar)).status == 403
    assert (await client.post(f"/api/projects/{pid}/stages", json={"title": "Моя"}, headers=sarvar)).status == 403
    assert (await client.patch(f"/api/stages/{progress['id']}", json={"title": "x"}, headers=sarvar)).status == 403

    # колонка из другого проекта не подходит; чужое пространство колонок не видит
    wid2 = await new_workspace(client, admin)
    resp = await client.post(f"/api/w/{wid2}/projects", json={"name": "Другой"}, headers=admin)
    pid2 = (await resp.json())["data"]["id"]
    alien = (await (await client.post(f"/api/projects/{pid2}/stages", json={"title": "Чужая"}, headers=admin))
             .json())["data"][0]
    assert (await client.post(f"/api/tasks/{own['id']}/stage", json={"stage_id": alien["id"]},
                              headers=admin)).status == 404
    assert (await client.patch(f"/api/stages/{alien['id']}", json={"title": "x"}, headers=sarvar)).status == 404


async def test_task_board_block_on_page_stays_in_workspace(client, workbook):
    admin = await tg(client)
    wid = await default_wid(client, admin)
    pid, _ = await project_with_tasks(client, workbook, admin)
    wid2 = await new_workspace(client, admin)
    pid2 = (await (await client.post(f"/api/w/{wid2}/projects", json={"name": "Чужой"}, headers=admin)).json())["data"]["id"]
    content = [
        {"id": "b1", "type": "tasks", "project_id": pid, "view": "table"},
        {"id": "b2", "type": "tasks", "project_id": pid2},
        {"id": "b3", "type": "tasks", "project_id": "x"},
        {"id": "b4", "type": "tasks", "project_id": pid, "view": "gallery"},
    ]
    resp = await client.post(f"/api/w/{wid}/pages", json={"title": "План", "content": content}, headers=admin)
    page = (await resp.json())["data"]
    assert page["content"] == [{"id": "b1", "type": "tasks", "project_id": pid, "view": "table"},
                               {"id": "b4", "type": "tasks", "project_id": pid, "view": "board"}]
