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
    service.accounts.bootstrap()
    async with TestClient(TestServer(create_app(service))) as c:
        yield c


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def tg(client, user_id=102, username="s_maxhan", **kw) -> dict:
    resp = await client.post("/api/auth/telegram", json={"init_data": init_data(user_id, username, **kw)})
    assert resp.status == 200, await resp.text()
    client.session.cookie_jar.clear()  # проверяем именно заголовок, а не cookie
    return bearer((await resp.json())["token"])


async def login(client, login_name, password) -> aiohttp.ClientResponse:
    resp = await client.post("/api/auth/login", json={"login": login_name, "password": password})
    client.session.cookie_jar.clear()
    return resp


async def default_wid(client, headers) -> int:
    boot = await (await client.get("/api/bootstrap", headers=headers)).json()
    return boot["workspaces"][0]["id"]


async def upload(client, workbook, headers, wid=None):
    wid = wid or await default_wid(client, headers)
    form = aiohttp.FormData()
    form.add_field("file", workbook.read_bytes(), filename="erp.xlsx")
    return await client.post(f"/api/w/{wid}/upload", data=form, headers=headers)


async def make_user(client, admin, **data) -> dict:
    resp = await client.post("/api/admin/users", json=data, headers=admin)
    assert resp.status == 200, await resp.text()
    return (await resp.json())["data"]


async def new_workspace(client, admin, name="Клиент") -> int:
    resp = await client.post("/api/admin/workspaces", json={"name": name, "icon": "🤝"}, headers=admin)
    return (await resp.json())["data"]["id"]


# ---------- вход ----------
async def test_telegram_login_rejects_missing_forged_stale_and_strangers(client):
    assert (await client.get("/api/bootstrap")).status == 401
    for data in (init_data(102, "s_maxhan", token="999:other"), init_data(102, "s_maxhan", age=8 * 24 * 3600),
                 init_data(777, "stranger"), "garbage"):
        assert (await client.post("/api/auth/telegram", json={"init_data": data})).status == 401
    admin = await tg(client)
    body = await (await client.get("/api/bootstrap", headers=admin)).json()
    assert body["me"]["name"] == "Шахзод" and body["me"]["is_superadmin"] and body["me"]["session_kind"] == "telegram"
    assert body["settings"]["morning_time"] == "09:00"
    assert [(w["name"], w["role"]) for w in body["workspaces"]] == [("IAC Media", "admin")]


async def test_first_open_from_telegram_binds_member_by_username(client, service):
    headers = await tg(client, 4242, "asl_offf")
    body = await (await client.get("/api/bootstrap", headers=headers)).json()
    assert body["settings"] == {} and body["workspaces"][0]["role"] == "member"
    assert service.db.member_by_telegram(4242).name == "Асл"


async def test_password_login_temp_password_and_throttling(client):
    admin = await tg(client)
    wid = await new_workspace(client, admin)
    await make_user(client, admin, login="Client.One", name="Клиент", password="Temp-pass-1", roles={wid: "viewer"})
    assert (await login(client, "client.one", "wrong-pass")).status == 401
    resp = await login(client, "@Client.One", "Temp-pass-1")
    assert resp.status == 200 and (await resp.json())["must_change_password"]
    user = bearer((await resp.json())["token"])
    boot = await (await client.get("/api/bootstrap", headers=user)).json()
    assert boot["me"]["must_change_password"] and [w["id"] for w in boot["workspaces"]] == [wid]
    blocked = await client.get(f"/api/w/{wid}", headers=user)
    assert blocked.status == 403 and (await blocked.json())["must_change_password"]
    short = await client.post("/api/auth/password", json={"current": "Temp-pass-1", "new": "123"}, headers=user)
    assert short.status == 400
    ok = await client.post("/api/auth/password", json={"current": "Temp-pass-1", "new": "Client-own-pass"}, headers=user)
    assert ok.status == 200
    assert (await client.get(f"/api/w/{wid}", headers=user)).status == 200
    assert (await login(client, "client.one", "Temp-pass-1")).status == 401
    assert (await login(client, "client.one", "Client-own-pass")).status == 200
    for _ in range(5):
        await login(client, "client.one", "nope-nope")
    assert (await login(client, "client.one", "Client-own-pass")).status == 429


async def test_cookie_session_and_logout(client):
    admin = await tg(client)
    await make_user(client, admin, login="anna", name="Анна", password="Anna-pass-1")
    await login(client, "anna", "Anna-pass-1")
    await client.post("/api/auth/password", json={"current": "Anna-pass-1", "new": "Anna-pass-2"},
                      headers=bearer((await (await client.post("/api/auth/login", json={
                          "login": "anna", "password": "Anna-pass-1"})).json())["token"]))
    resp = await client.post("/api/auth/login", json={"login": "anna", "password": "Anna-pass-2"})
    assert resp.status == 200 and "pm_session" in resp.cookies and resp.cookies["pm_session"]["httponly"]
    assert (await client.get("/api/bootstrap")).status == 200  # cookie
    assert (await client.post("/api/auth/logout")).status == 200
    assert (await client.get("/api/bootstrap")).status == 401


async def test_superadmin_guardrails(client, service):
    admin = await tg(client)
    me = service.db.user_by_member(service.db.member_by_username("s_maxhan").id)
    resp = await client.patch(f"/api/admin/users/{me.id}", json={"is_superadmin": False}, headers=admin)
    assert resp.status == 400
    member = await tg(client, 106, "gflwwc")
    assert (await client.get("/api/admin", headers=member)).status == 403
    assert (await client.post("/api/admin/users", json={"login": "x", "name": "X"}, headers=member)).status == 403
    # без пароля и без Telegram войти невозможно
    resp = await client.post("/api/admin/users", json={"login": "ghost", "name": "Призрак"}, headers=admin)
    assert resp.status == 400
    # отключённый пользователь теряет сессию
    sarvar = service.db.user_by_member(service.db.member_by_username("gflwwc").id)
    await client.patch(f"/api/admin/users/{sarvar.id}", json={"active": False}, headers=admin)
    assert (await client.get("/api/bootstrap", headers=member)).status == 401
    resp = await client.post(f"/api/admin/users/{sarvar.id}/password", json={}, headers=admin)
    assert len((await resp.json())["data"]["password"]) >= 10


# ---------- пространства и права ----------
async def test_workspace_isolation(client, workbook, service):
    admin = await tg(client)
    resp = await upload(client, workbook, admin)
    pid = (await resp.json())["data"]["project_id"]
    default = await default_wid(client, admin)
    page = (await (await client.post(f"/api/w/{default}/pages", json={"title": "Регламент"}, headers=admin)).json())["data"]
    wid = await new_workspace(client, admin)
    await make_user(client, admin, login="client", name="Клиент", password="Client-pass-1", roles={wid: "admin"})
    service.db.update_user(service.db.user_by_login("client").id, must_change_password=False)
    user = bearer((await (await login(client, "client", "Client-pass-1")).json())["token"])
    boot = await (await client.get("/api/bootstrap", headers=user)).json()
    assert [w["id"] for w in boot["workspaces"]] == [wid]
    task_id = service.db.list_tasks(pid)[0].id
    for path in (f"/api/w/{default}", f"/api/projects/{pid}", f"/api/tasks/{task_id}", f"/api/pages/{page['id']}",
                 f"/api/w/{default}/search?q=key", f"/api/projects/{pid}/calls"):
        assert (await client.get(path, headers=user)).status == 404, path
    assert (await client.post(f"/api/tasks/{task_id}/status", json={"status": "done"}, headers=user)).status == 404
    assert (await upload(client, workbook, user, wid)).status == 400  # код проекта уже занят в другом пространстве
    mine = await (await client.get(f"/api/w/{wid}", headers=user)).json()
    assert mine["projects"] == [] and mine["role"] == "admin"


async def test_upload_project_and_task_actions(client, workbook, service):
    sarvar = await tg(client, 106, "gflwwc")
    assert (await upload(client, workbook, sarvar)).status == 403
    admin = await tg(client)
    resp = await upload(client, workbook, admin)
    assert resp.status == 200
    pid = (await resp.json())["data"]["project_id"]

    data = await (await client.get(f"/api/projects/{pid}", headers=sarvar)).json()
    mine = [t for t in data["tasks"] if t["mine"]]
    assert {t["title"] for t in mine} == {"Key Visual мероприятия", "Пресс-волл"}
    assert data["audit"] == [] and data["role"] == "member"
    kv = next(t for t in mine if t["title"].startswith("Key Visual"))
    other = next(t for t in data["tasks"] if not t["mine"])
    assert kv["can_edit"] and not other["can_edit"]

    assert (await client.post(f"/api/tasks/{other['id']}/status", json={"status": "done"}, headers=sarvar)).status == 403
    resp = await client.post(f"/api/tasks/{kv['id']}/status", json={"status": "done"}, headers=sarvar)
    assert resp.status == 200 and (await resp.json())["data"]["status"] == "done"
    resp = await client.post(f"/api/tasks/{kv['id']}/comment", json={"text": "Отправил клиенту"}, headers=sarvar)
    assert resp.status == 200
    detail = await (await client.get(f"/api/tasks/{kv['id']}", headers=sarvar)).json()
    assert [e["kind"] for e in detail["events"]][:2] == ["comment", "status"]
    assert detail["events"][0]["member_name"] == "Сарвар" and detail["project_name"]

    wid = await default_wid(client, sarvar)
    my = await (await client.get(f"/api/w/{wid}/my", headers=sarvar)).json()
    assert {t["title"] for t in my["tasks"]} == {"Key Visual мероприятия", "Пресс-волл"} and my["linked"]
    overview = await (await client.get(f"/api/w/{wid}", headers=sarvar)).json()
    assert overview["projects"][0]["my_open"] == 1 and overview["projects"][0]["stats"]["done"] == 2
    found = await (await client.get(f"/api/w/{wid}/search?q=пресс", headers=sarvar)).json()
    assert [t["title"] for t in found["tasks"]] == ["Пресс-волл"]

    admin_view = await (await client.get(f"/api/projects/{pid}", headers=admin)).json()
    assert admin_view["audit"] and admin_view["stats"]["done"] == 2
    xlsx = await client.get(f"/api/projects/{pid}/export.xlsx", headers=admin)
    assert xlsx.status == 200 and xlsx.headers["Content-Type"].startswith("application/vnd.openxml")
    assert (await client.get(f"/api/projects/{pid}/export.xlsx", headers=sarvar)).status == 403


async def test_admin_creates_project_task_and_plan(client, service):
    admin = await tg(client)
    wid = await default_wid(client, admin)
    resp = await client.post(f"/api/w/{wid}/projects", json={"name": "Презентация Tiguan", "event_date": "2026-10-20"},
                             headers=admin)
    project = (await resp.json())["data"]
    pid = project["id"]
    assert project["workspace_id"] == wid
    resp = await client.post(f"/api/projects/{pid}/tasks", json={"title": "Key Visual", "responsible": "Сарвар",
                                                                  "priority": "Критично"}, headers=admin)
    assert resp.status == 200
    task = (await resp.json())["data"]
    assert (await client.post(f"/api/projects/{pid}/tasks", json={"title": ""}, headers=admin)).status == 400
    plan = await (await client.get(f"/api/projects/{pid}/plan", headers=admin)).json()
    assert plan["items"][0]["deadline"] == "2026-10-02"
    resp = await client.post(f"/api/projects/{pid}/plan", json={"items": plan["items"]}, headers=admin)
    assert (await resp.json())["data"]["applied"] == 1
    resp = await client.patch(f"/api/tasks/{task['id']}", json={"deadline": "2026-10-01"}, headers=admin)
    assert (await resp.json())["data"]["deadline"] == "2026-10-01"
    sarvar = await tg(client, 106, "gflwwc")
    assert (await client.delete(f"/api/tasks/{task['id']}", headers=sarvar)).status == 403
    assert (await client.delete(f"/api/tasks/{task['id']}", headers=admin)).status == 200
    assert (await client.get(f"/api/tasks/{task['id']}", headers=admin)).status == 404
    # перенос проекта в другое пространство
    other = await new_workspace(client, admin, "Второе")
    resp = await client.patch(f"/api/projects/{pid}", json={"workspace_id": other}, headers=admin)
    assert (await resp.json())["data"]["workspace_id"] == other


async def test_members_settings_and_calls_api(client, service):
    admin = await tg(client)
    resp = await client.post("/api/members", json={"name": "Максуд", "username": "@maxud", "role": "Техника"},
                             headers=admin)
    assert (await resp.json())["data"]["username"] == "maxud"
    sarvar = await tg(client, 106, "gflwwc")
    assert (await client.post("/api/members", json={"name": "X"}, headers=sarvar)).status == 403
    assert (await client.post("/api/settings", json={"morning_time": "08:30"}, headers=sarvar)).status == 403
    assert (await client.post("/api/settings", json={"morning_time": "08:30"}, headers=admin)).status == 200
    project = service.db.create_project("P", "P", None)
    resp = await client.post(f"/api/projects/{project.id}/calls",
                             json={"contact": "Ведущий", "due_at": "2026-09-26T16:00"}, headers=sarvar)
    assert resp.status == 200
    calls = await (await client.get(f"/api/projects/{project.id}/calls", headers=sarvar)).json()
    assert len(calls["planned"]) == 1 and calls["items"][0]["kind"] == "scheduled"


async def test_observer_sees_everything_read_only(client, workbook, service):
    admin = await tg(client)
    await upload(client, workbook, admin)
    project = service.default_project()
    call = await service.create_call(project, {"contact": "DJ", "due_at": "2026-09-26T16:00"},
                                     service.db.member_by_username("s_maxhan"))
    watcher = await tg(client, 110, "ik7777777777")
    boot = await (await client.get("/api/bootstrap", headers=watcher)).json()
    assert boot["workspaces"][0]["role"] == "viewer" and boot["settings"] == {}
    data = await (await client.get(f"/api/projects/{project.id}", headers=watcher)).json()
    assert data["audit"] and not any(t["can_edit"] for t in data["tasks"])
    calls = await (await client.get(f"/api/projects/{project.id}/calls", headers=watcher)).json()
    assert [c["id"] for c in calls["planned"]] == [call.id] and any(i["kind"] != "scheduled" for i in calls["items"])
    task_id = data["tasks"][0]["id"]
    assert (await client.post(f"/api/tasks/{task_id}/status", json={"status": "done"}, headers=watcher)).status == 403
    assert (await client.post(f"/api/calls/{call.id}/done", json={}, headers=watcher)).status == 403
    assert (await client.post(f"/api/projects/{project.id}/calls/check", json={"key": f"call:{call.id}"},
                              headers=watcher)).status == 403
    assert (await client.post(f"/api/projects/{project.id}/calls", json={"contact": "X", "due_at": "2026-09-26T16:00"},
                              headers=watcher)).status == 403
    wid = await default_wid(client, watcher)
    assert (await client.post(f"/api/w/{wid}/pages", json={"title": "x"}, headers=watcher)).status == 403


async def test_account_without_team_link(client, workbook, service):
    """Аккаунт без связи с командой: админ пространства меняет задачи от своего имени."""
    admin = await tg(client)
    await upload(client, workbook, admin)
    wid = await default_wid(client, admin)
    await make_user(client, admin, login="manager", name="Менеджер Ольга", password="Olga-pass-1",
                    roles={wid: "admin"})
    service.db.update_user(service.db.user_by_login("manager").id, must_change_password=False)
    olga = bearer((await (await login(client, "manager", "Olga-pass-1")).json())["token"])
    task = service.db.list_tasks(service.default_project().id)[0]
    assert (await client.post(f"/api/tasks/{task.id}/status", json={"status": "progress"}, headers=olga)).status == 200
    detail = await (await client.get(f"/api/tasks/{task.id}", headers=olga)).json()
    assert detail["events"][0]["member_name"] == "Менеджер Ольга" and not detail["mine"]
    my = await (await client.get(f"/api/w/{wid}/my", headers=olga)).json()
    assert my == {"tasks": [], "linked": False}


# ---------- страницы ----------
async def test_pages_crud_sanitize_conflicts_and_trash(client, service):
    admin = await tg(client)
    wid = await default_wid(client, admin)
    sarvar = await tg(client, 106, "gflwwc")
    watcher = await tg(client, 110, "ik7777777777")
    resp = await client.post(f"/api/w/{wid}/pages", json={"title": "Бриф клиента", "icon": "📄"}, headers=sarvar)
    page = (await resp.json())["data"]
    content = [
        {"id": "a1", "type": "h1", "rich": [{"t": "Цели"}]},
        {"id": "a2", "type": "p", "rich": [{"t": "Жирный", "b": 1}, {"t": " ссылка", "href": "javascript:alert(1)"},
                                           {"t": " сайт", "href": "example.com", "onclick": "x"}]},
        {"id": "a3", "type": "todo", "rich": "Согласовать смету", "checked": True, "indent": 99},
        {"id": "a4", "type": "script", "rich": "<script>"},
        {"id": "a5", "type": "code", "text": "<b>raw</b>"},
        {"id": "a6", "type": "page", "page_id": 999999},
    ]
    resp = await client.put(f"/api/pages/{page['id']}", json={"content": content, "version": page["version"]},
                            headers=sarvar)
    saved = (await resp.json())["data"]
    blocks = saved["content"]
    assert [b["type"] for b in blocks] == ["h1", "p", "todo", "code"]
    assert blocks[1]["rich"] == [{"t": "Жирный", "b": 1}, {"t": " ссылка"}, {"t": " сайт", "href": "https://example.com"}]
    assert blocks[2]["indent"] == 8 and blocks[2]["checked"]
    stale = await client.put(f"/api/pages/{page['id']}", json={"title": "Старое", "version": page["version"]},
                             headers=admin)
    assert stale.status == 409 and (await stale.json())["page"]["version"] == saved["version"]
    assert (await client.put(f"/api/pages/{page['id']}", json={"title": "x"}, headers=watcher)).status == 403
    viewer_page = await (await client.get(f"/api/pages/{page['id']}", headers=watcher)).json()
    assert viewer_page["title"] == "Бриф клиента" and not viewer_page["can_edit"]

    child = (await (await client.post(f"/api/w/{wid}/pages", json={"title": "Смета", "parent_id": page["id"]},
                                      headers=sarvar)).json())["data"]
    resp = await client.post(f"/api/pages/{page['id']}/move", json={"parent_id": child["id"]}, headers=sarvar)
    assert resp.status == 400  # внутрь самой себя
    found = await (await client.get(f"/api/w/{wid}/search?q=смету", headers=watcher)).json()
    assert [p["title"] for p in found["pages"]] == ["Бриф клиента"]

    assert (await client.delete(f"/api/pages/{page['id']}", headers=sarvar)).status == 200
    tree = await (await client.get(f"/api/w/{wid}", headers=sarvar)).json()
    assert tree["pages"] == []
    trash = await (await client.get(f"/api/w/{wid}/trash", headers=sarvar)).json()
    assert [p["title"] for p in trash["pages"]] == ["Бриф клиента"]
    assert (await client.post(f"/api/pages/{page['id']}/purge", headers=sarvar)).status == 403
    assert (await client.post(f"/api/pages/{page['id']}/restore", headers=sarvar)).status == 200
    tree = await (await client.get(f"/api/w/{wid}", headers=sarvar)).json()
    assert {p["title"] for p in tree["pages"]} == {"Бриф клиента", "Смета"}
    await client.delete(f"/api/pages/{page['id']}", headers=sarvar)
    assert (await client.post(f"/api/pages/{page['id']}/purge", headers=admin)).status == 200
    assert service.db.get_page(child["id"]) is None


async def test_bootstrap_assigns_old_projects_once(service):
    project = service.db.create_project("Старый", "OLD", None)
    assert project.workspace_id is None
    created = service.accounts.bootstrap()
    assert created == len(service.db.list_members())
    assert service.db.get_project(project.id).workspace_id == service.db.default_workspace().id
    assert service.accounts.bootstrap() == 0
    users = {u.login: u for u in service.db.list_users()}
    assert users["s_maxhan"].is_superadmin and not users["s_maxhan"].has_password
    assert service.db.user_roles(users["ik7777777777"].id) == {service.db.default_workspace().id: "viewer"}


async def test_static_and_health(client):
    assert (await client.get("/health")).status == 200
    page = await client.get("/")
    html = await page.text()
    assert page.status == 200 and "telegram-web-app.js" in html and "{{v}}" not in html
    assert (await client.get("/static/app.js")).status == 200
    assert page.headers["X-Content-Type-Options"] == "nosniff"
