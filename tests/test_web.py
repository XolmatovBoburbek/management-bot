import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import aiohttp
import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.web import create_app
from tests.conftest import TOKEN


def init_data(user_id: int, username: str, token: str = TOKEN, age: int = 0) -> str:
    data = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": user_id, "first_name": "Test", "username": username}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


@pytest.fixture
async def client(service):
    async with TestClient(TestServer(create_app(service))) as c:
        yield c


def auth(user_id=102, username="s_maxhan", **kw):
    return {"X-Init-Data": init_data(user_id, username, **kw)}


async def upload(client, workbook, headers):
    form = aiohttp.FormData()
    form.add_field("file", workbook.read_bytes(), filename="erp.xlsx")
    return await client.post("/api/upload", data=form, headers=headers)


async def test_auth_rejects_missing_forged_and_stale(client):
    assert (await client.get("/api/bootstrap")).status == 401
    assert (await client.get("/api/bootstrap", headers=auth(token="999:other"))).status == 401
    assert (await client.get("/api/bootstrap", headers=auth(age=8 * 24 * 3600))).status == 401
    assert (await client.get("/api/bootstrap", headers=auth(777, "stranger"))).status == 401
    resp = await client.get("/api/bootstrap", headers=auth())
    assert resp.status == 200
    body = await resp.json()
    assert body["me"]["name"] == "Шахзод" and body["me"]["is_admin"] and body["settings"]["morning_time"] == "09:00"


async def test_first_open_binds_member_by_username(client, service):
    resp = await client.get("/api/bootstrap", headers=auth(4242, "asl_offf"))
    assert resp.status == 200 and (await resp.json())["settings"] == {}
    assert service.db.member_by_telegram(4242).name == "Асл"


async def test_upload_project_and_task_actions(client, workbook, service):
    assert (await upload(client, workbook, auth(106, "gflwwc"))).status == 403
    resp = await upload(client, workbook, auth())
    assert resp.status == 200
    pid = (await resp.json())["data"]["project_id"]

    data = await (await client.get(f"/api/projects/{pid}", headers=auth(106, "gflwwc"))).json()
    mine = [t for t in data["tasks"] if t["mine"]]
    assert {t["title"] for t in mine} == {"Key Visual мероприятия", "Пресс-волл"}
    assert data["audit"] == []  # аудит только для админов
    kv = next(t for t in mine if t["title"].startswith("Key Visual"))
    other = next(t for t in data["tasks"] if not t["mine"])
    assert kv["can_edit"] and not other["can_edit"]

    resp = await client.post(f"/api/tasks/{other['id']}/status", json={"status": "done"}, headers=auth(106, "gflwwc"))
    assert resp.status == 403
    resp = await client.post(f"/api/tasks/{kv['id']}/status", json={"status": "done"}, headers=auth(106, "gflwwc"))
    assert resp.status == 200 and (await resp.json())["data"]["status"] == "done"
    resp = await client.post(f"/api/tasks/{kv['id']}/comment", json={"text": "Отправил клиенту"},
                             headers=auth(106, "gflwwc"))
    assert resp.status == 200
    detail = await (await client.get(f"/api/tasks/{kv['id']}", headers=auth(106, "gflwwc"))).json()
    assert [e["kind"] for e in detail["events"]][:2] == ["comment", "status"]

    admin = await (await client.get(f"/api/projects/{pid}", headers=auth())).json()
    assert admin["audit"] and admin["stats"]["done"] == 2


async def test_admin_creates_project_task_and_plan(client, service):
    resp = await client.post("/api/projects", json={"name": "Презентация Tiguan", "event_date": "2026-10-20"},
                             headers=auth())
    project = (await resp.json())["data"]
    pid = project["id"]
    resp = await client.post(f"/api/projects/{pid}/tasks", json={"title": "Key Visual", "responsible": "Сарвар",
                                                                  "priority": "Критично"}, headers=auth())
    assert resp.status == 200
    task = (await resp.json())["data"]
    assert (await client.post(f"/api/projects/{pid}/tasks", json={"title": ""}, headers=auth())).status == 400
    plan = await (await client.get(f"/api/projects/{pid}/plan", headers=auth())).json()
    assert plan["items"][0]["deadline"] == "2026-10-02"
    resp = await client.post(f"/api/projects/{pid}/plan", json={"items": plan["items"]}, headers=auth())
    assert (await resp.json())["data"]["applied"] == 1
    resp = await client.patch(f"/api/tasks/{task['id']}", json={"deadline": "2026-10-01"}, headers=auth())
    assert (await resp.json())["data"]["deadline"] == "2026-10-01"
    assert (await client.delete(f"/api/tasks/{task['id']}", headers=auth(106, "gflwwc"))).status == 403
    assert (await client.delete(f"/api/tasks/{task['id']}", headers=auth())).status == 200
    assert (await client.get(f"/api/tasks/{task['id']}", headers=auth())).status == 404


async def test_members_and_calls_api(client, service):
    resp = await client.post("/api/members", json={"name": "Максуд", "username": "@maxud", "role": "Техника"},
                             headers=auth())
    assert (await resp.json())["data"]["username"] == "maxud"
    assert (await client.post("/api/members", json={"name": "X"}, headers=auth(106, "gflwwc"))).status == 403
    project = service.db.create_project("P", "P", None)
    resp = await client.post(f"/api/projects/{project.id}/calls",
                             json={"contact": "Ведущий", "due_at": "2026-09-26T16:00"}, headers=auth(106, "gflwwc"))
    assert resp.status == 200
    calls = await (await client.get(f"/api/projects/{project.id}/calls", headers=auth(106, "gflwwc"))).json()
    assert len(calls["planned"]) == 1 and calls["items"][0]["kind"] == "scheduled"


async def test_observer_sees_everything_read_only(client, workbook, service):
    await upload(client, workbook, auth())
    project = service.default_project()
    call = await service.create_call(project, {"contact": "DJ", "due_at": "2026-09-26T16:00"},
                                     service.db.member_by_username("s_maxhan"))
    watcher = auth(110, "ik7777777777")
    boot = await (await client.get("/api/bootstrap", headers=watcher)).json()
    assert boot["me"]["is_observer"] and not boot["me"]["is_admin"] and boot["settings"] == {}
    data = await (await client.get(f"/api/projects/{project.id}", headers=watcher)).json()
    assert data["audit"] and not any(t["can_edit"] for t in data["tasks"])
    calls = await (await client.get(f"/api/projects/{project.id}/calls", headers=watcher)).json()
    assert [c["id"] for c in calls["planned"]] == [call.id] and any(i["kind"] != "scheduled" for i in calls["items"])
    task_id = data["tasks"][0]["id"]
    assert (await client.post(f"/api/tasks/{task_id}/status", json={"status": "done"}, headers=watcher)).status == 403
    assert (await client.post(f"/api/calls/{call.id}/done", json={}, headers=watcher)).status == 403
    assert (await client.post(f"/api/projects/{project.id}/calls/check", json={"key": f"call:{call.id}"},
                              headers=watcher)).status == 403


async def test_static_and_health(client):
    assert (await client.get("/health")).status == 200
    page = await client.get("/")
    assert page.status == 200 and "telegram-web-app.js" in await page.text()
    assert (await client.get("/static/app.js")).status == 200
