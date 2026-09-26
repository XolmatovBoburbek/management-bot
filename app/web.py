"""HTTP-сервер Mini App: статика + JSON API. Авторизация — подпись initData от Telegram."""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from pathlib import Path

from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web

from app import logic
from app.excel_io import WorkbookError
from app.gsheets import GSheetError
from app.models import Member, Project
from app.service import AccessError, Service

log = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
INIT_DATA_MAX_AGE = 7 * 24 * 3600
SERVICE_KEY = web.AppKey("service", Service)
MEMBER_KEY = web.RequestKey("member", Member)


def _service(request: web.Request) -> Service:
    return request.app[SERVICE_KEY]


def _member(request: web.Request) -> Member:
    return request[MEMBER_KEY]


def authenticate(service: Service, init_data: str) -> Member | None:
    if not init_data:
        if service.config.dev_auth_username:
            return service.db.member_by_username(service.config.dev_auth_username)
        return None
    try:
        data = safe_parse_webapp_init_data(service.config.bot_token, init_data)
    except ValueError:
        return None
    if time.time() - data.auth_date.timestamp() > INIT_DATA_MAX_AGE or not data.user:
        return None
    return service.identify(data.user.id, data.user.username)


@web.middleware
async def api_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    service = _service(request)
    member = authenticate(service, request.headers.get("X-Init-Data", ""))
    if not member:
        return web.json_response({"error": "Нет доступа. Откройте приложение из бота; ваш ник должен быть в команде."},
                                 status=401)
    request[MEMBER_KEY] = member
    try:
        return await handler(request)
    except AccessError as exc:
        return web.json_response({"error": str(exc)}, status=403)
    except LookupError as exc:
        return web.json_response({"error": str(exc).strip("'")}, status=404)
    except (ValueError, WorkbookError, GSheetError, json.JSONDecodeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def _json(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("Ожидался JSON-объект")
    return data


def _project(request: web.Request) -> Project:
    project = _service(request).db.get_project(int(request.match_info["pid"]))
    if not project:
        raise LookupError("Проект не найден")
    return project


def _ok(data: object = None) -> web.Response:
    return web.json_response({"ok": True, **({"data": data} if data is not None else {})})


# ---------- чтение ----------
async def bootstrap(request: web.Request) -> web.Response:
    service = _service(request)
    me = _member(request)
    return web.json_response({
        "me": me.to_dict(),
        "today": service.today().isoformat(),
        "projects": [p.to_dict() for p in service.projects()],
        "members": [m.to_dict() for m in service.db.list_members(include_inactive=me.is_admin)],
        "settings": service.settings() if me.is_admin else {},
        "sheets_can_write": service.sheets.can_write,
        "sheets_service_email": service.sheets.service_email() if me.is_admin else None,
        "bot_username": service.bot_username,
    })


async def project_data(request: web.Request) -> web.Response:
    service = _service(request)
    project = _project(request)
    today = service.today()
    team = service.team()
    tasks = service.db.list_tasks(project.id)
    milestones = service.db.list_milestones(project.id)
    me = _member(request)
    my_ids = {t.id for t in team.tasks_of(me, tasks)}
    task_dicts = []
    for t in logic.sorted_tasks(tasks, today):
        d = t.to_dict(today)
        d["bucket"] = logic.bucket(t, today)
        d["assignee_ids"] = [m.id for m in team.assignees(t)]
        d["mine"] = t.id in my_ids
        d["can_edit"] = me.is_admin or t.id in my_ids
        task_dicts.append(d)
    return web.json_response({
        "project": project.to_dict(),
        "tasks": task_dicts,
        "stats": logic.stats(tasks, today),
        "blocks": logic.by_block(tasks, today),
        "workload": logic.workload(tasks, team, today),
        "attention": [t.id for t in logic.attention(tasks, today, limit=15)],
        "milestones": [m.to_dict(project.event_date) for m in milestones],
        "risks": [r.to_dict() for r in service.db.list_risks(project.id)],
        "audit": logic.audit(project, tasks, team, milestones, today) if me.is_admin else [],
    })


async def task_detail(request: web.Request) -> web.Response:
    service = _service(request)
    task = service.get_task(int(request.match_info["tid"]))
    today = service.today()
    data = task.to_dict(today)
    data["bucket"] = logic.bucket(task, today)
    data["can_edit"] = service.can_edit(_member(request), task)
    data["assignee_ids"] = [m.id for m in service.team().assignees(task)]
    data["events"] = service.db.task_events(task.id)
    data["calls"] = [c.to_dict() for c in service.db.list_calls(task.project_id, include_done=True)
                     if c.task_id == task.id]
    return web.json_response(data)


# ---------- задачи ----------
async def task_status(request: web.Request) -> web.Response:
    service = _service(request)
    task = service.get_task(int(request.match_info["tid"]))
    body = await _json(request)
    updated = await service.set_status(task, str(body.get("status", "")), _member(request))
    return _ok(updated.to_dict(service.today()))


async def task_comment(request: web.Request) -> web.Response:
    service = _service(request)
    task = service.get_task(int(request.match_info["tid"]))
    body = await _json(request)
    await service.add_comment(task, str(body.get("text", "")), _member(request))
    return _ok()


async def task_problem(request: web.Request) -> web.Response:
    service = _service(request)
    task = service.get_task(int(request.match_info["tid"]))
    body = await _json(request)
    updated = await service.report_problem(task, str(body.get("text", "")), _member(request))
    return _ok(updated.to_dict(service.today()))


async def task_resolve(request: web.Request) -> web.Response:
    service = _service(request)
    task = service.get_task(int(request.match_info["tid"]))
    updated = await service.resolve_problem(task, _member(request))
    return _ok(updated.to_dict(service.today()))


async def task_update(request: web.Request) -> web.Response:
    service = _service(request)
    task = service.get_task(int(request.match_info["tid"]))
    updated = await service.update_task(task, await _json(request), _member(request))
    return _ok(updated.to_dict(service.today()))


async def task_delete(request: web.Request) -> web.Response:
    service = _service(request)
    task = service.get_task(int(request.match_info["tid"]))
    await service.archive_task(task, _member(request))
    return _ok()


async def task_create(request: web.Request) -> web.Response:
    service = _service(request)
    task = await service.create_task(_project(request), await _json(request), _member(request))
    return _ok(task.to_dict(service.today()))


# ---------- проекты ----------
async def project_create(request: web.Request) -> web.Response:
    service = _service(request)
    body = await _json(request)
    event = date.fromisoformat(body["event_date"]) if body.get("event_date") else None
    project = service.create_project(_member(request), str(body.get("name", "")), event)
    return _ok(project.to_dict())


async def project_update(request: web.Request) -> web.Response:
    service = _service(request)
    body = await _json(request)
    fields = {}
    if "name" in body:
        fields["name"] = str(body["name"]).strip() or _project(request).name
    if "event_date" in body:
        fields["event_date"] = date.fromisoformat(body["event_date"]) if body["event_date"] else None
    if "archived" in body:
        fields["archived"] = bool(body["archived"])
    project = service.update_project(_member(request), _project(request), **fields)
    return _ok(project.to_dict())


async def upload(request: web.Request) -> web.Response:
    service = _service(request)
    reader = await request.multipart()
    field = await reader.next()
    while field is not None and getattr(field, "name", None) != "file":
        field = await reader.next()
    if field is None:
        raise ValueError("Файл не получен")
    data = await field.read(decode=False)
    filename = field.filename or "table.xlsx"
    result, parsed, audit = await service.import_file(bytes(data), filename, _member(request))
    return _ok({"project_id": result.project.id, "created": result.project_created, "tasks": len(parsed.tasks),
                "added": len(result.added), "updated": result.updated, "removed": len(result.removed),
                "warnings": parsed.warnings[:10], "audit": audit})


async def connect_sheet(request: web.Request) -> web.Response:
    service = _service(request)
    body = await _json(request)
    result, parsed, audit = await service.connect_sheet(str(body.get("url", "")).strip(), _member(request))
    return _ok({"project_id": result.project.id, "tasks": len(parsed.tasks), "added": len(result.added),
                "updated": result.updated, "audit": audit})


async def sync(request: web.Request) -> web.Response:
    service = _service(request)
    member = _member(request)
    if not member.is_admin:
        raise AccessError("Только для администраторов")
    result, parsed, _ = await service.sync_project(_project(request))
    return _ok({"tasks": len(parsed.tasks), "added": len(result.added), "updated": result.updated,
                "removed": len(result.removed), "status_changes": result.status_changes_from_sheet})


async def export(request: web.Request) -> web.Response:
    service = _service(request)
    member = _member(request)
    if not member.is_admin:
        raise AccessError("Только для администраторов")
    sent = await service.send_export(_project(request), member)
    return _ok({"sent": sent})


async def plan_get(request: web.Request) -> web.Response:
    service = _service(request)
    member = _member(request)
    if not member.is_admin:
        raise AccessError("Только для администраторов")
    project = _project(request)
    if not project.event_date:
        raise ValueError("Сначала укажите дату мероприятия")
    proposals = logic.propose_deadlines(service.db.list_tasks(project.id), service.team(), project.event_date,
                                        service.today())
    return web.json_response({"items": proposals})


async def plan_apply(request: web.Request) -> web.Response:
    service = _service(request)
    body = await _json(request)
    applied = await service.apply_plan(_project(request), list(body.get("items", [])), _member(request))
    return _ok({"applied": applied})


# ---------- обзвон ----------
async def calls_get(request: web.Request) -> web.Response:
    service = _service(request)
    project = _project(request)
    me = _member(request)
    items = service.call_items(project)
    if not me.is_admin:
        items = [i for i in items if i["kind"] == "scheduled" and i.get("caller") == me.name]
    planned = [c.to_dict() for c in service.db.list_calls(project.id)
               if me.is_admin or c.member_id == me.id]
    return web.json_response({"items": items, "planned": planned, "today": service.today().isoformat()})


async def calls_create(request: web.Request) -> web.Response:
    service = _service(request)
    call = await service.create_call(_project(request), await _json(request), _member(request))
    return _ok(call.to_dict())


async def calls_check(request: web.Request) -> web.Response:
    service = _service(request)
    body = await _json(request)
    service.check_call_item(_project(request), str(body.get("key", "")), _member(request),
                            str(body.get("note", "")), bool(body.get("checked", True)))
    return _ok()


async def call_done(request: web.Request) -> web.Response:
    service = _service(request)
    call = service.db.get_call(int(request.match_info["cid"]))
    if not call:
        raise LookupError("Звонок не найден")
    body = await _json(request)
    return _ok((await service.finish_call(call, _member(request), str(body.get("result", "")))).to_dict())


async def call_cancel(request: web.Request) -> web.Response:
    service = _service(request)
    call = service.db.get_call(int(request.match_info["cid"]))
    if not call:
        raise LookupError("Звонок не найден")
    return _ok(service.cancel_call(call, _member(request)).to_dict())


# ---------- команда и настройки ----------
async def member_save(request: web.Request) -> web.Response:
    service = _service(request)
    member = service.save_member(_member(request), await _json(request))
    return _ok(member.to_dict())


async def settings_save(request: web.Request) -> web.Response:
    service = _service(request)
    return _ok(service.update_settings(await _json(request), _member(request)))


# ---------- статика ----------
async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


def create_app(service: Service) -> web.Application:
    app = web.Application(middlewares=[api_middleware], client_max_size=25 * 1024 * 1024)
    app[SERVICE_KEY] = service
    r = app.router
    r.add_get("/", index)
    r.add_get("/health", health)
    r.add_static("/static/", WEBAPP_DIR, append_version=True)
    r.add_get("/api/bootstrap", bootstrap)
    r.add_post("/api/projects", project_create)
    r.add_get("/api/projects/{pid:\\d+}", project_data)
    r.add_patch("/api/projects/{pid:\\d+}", project_update)
    r.add_post("/api/projects/{pid:\\d+}/tasks", task_create)
    r.add_post("/api/projects/{pid:\\d+}/sync", sync)
    r.add_post("/api/projects/{pid:\\d+}/export", export)
    r.add_get("/api/projects/{pid:\\d+}/plan", plan_get)
    r.add_post("/api/projects/{pid:\\d+}/plan", plan_apply)
    r.add_get("/api/projects/{pid:\\d+}/calls", calls_get)
    r.add_post("/api/projects/{pid:\\d+}/calls", calls_create)
    r.add_post("/api/projects/{pid:\\d+}/calls/check", calls_check)
    r.add_post("/api/calls/{cid:\\d+}/done", call_done)
    r.add_post("/api/calls/{cid:\\d+}/cancel", call_cancel)
    r.add_post("/api/upload", upload)
    r.add_post("/api/sheet", connect_sheet)
    r.add_get("/api/tasks/{tid:\\d+}", task_detail)
    r.add_patch("/api/tasks/{tid:\\d+}", task_update)
    r.add_delete("/api/tasks/{tid:\\d+}", task_delete)
    r.add_post("/api/tasks/{tid:\\d+}/status", task_status)
    r.add_post("/api/tasks/{tid:\\d+}/comment", task_comment)
    r.add_post("/api/tasks/{tid:\\d+}/problem", task_problem)
    r.add_post("/api/tasks/{tid:\\d+}/resolve", task_resolve)
    r.add_post("/api/members", member_save)
    r.add_post("/api/settings", settings_save)
    return app
