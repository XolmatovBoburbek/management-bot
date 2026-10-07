"""HTTP-сервер кабинета: статика + JSON API.

Вход — по логину и паролю (сессия в cookie или заголовке Authorization), внутри Telegram —
автоматически по подписи initData, если Telegram связан с аккаунтом. Всё рабочее разложено по
пространствам: человек видит только те, куда его добавил администратор, и права у него —
по роли в пространстве (администратор / участник / наблюдатель).
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlparse

from aiogram.utils.web_app import safe_parse_webapp_init_data
from aiohttp import web

from app import logic
from app.accounts import SESSION_DAYS, AuthError, ThrottledError
from app.excel_io import WorkbookError
from app.gsheets import GSheetError
from app.models import ADMIN, ROLE_LABELS, VIEWER, AccessError, Member, Project, Task, User, Workspace, norm_text
from app.pages import ConflictError
from app.service import Service

log = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
INIT_DATA_MAX_AGE = 7 * 24 * 3600
SESSION_COOKIE = "pm_session"
SERVICE_KEY = web.AppKey("service", Service)
VERSION_KEY = web.AppKey("static_version", str)
USER_KEY = web.RequestKey("user", User)
TOKEN_KEY = web.RequestKey("token", str)
SESSION_KIND_KEY = web.RequestKey("session_kind", str)
PUBLIC_API = {"/api/auth/login", "/api/auth/telegram"}
ALLOWED_WITH_TEMP_PASSWORD = {"/api/bootstrap", "/api/auth/password", "/api/auth/logout"}


def _service(request: web.Request) -> Service:
    return request.app[SERVICE_KEY]


def _user(request: web.Request) -> User:
    return request[USER_KEY]


def _token(request: web.Request) -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(SESSION_COOKIE, "")


def _client_address(request: web.Request) -> str:
    """Адрес для ограничения попыток входа. За nginx берём X-Real-IP / X-Forwarded-For."""
    remote = request.remote or ""
    try:
        behind_proxy = ipaddress.ip_address(remote).is_loopback or ipaddress.ip_address(remote).is_private
    except ValueError:
        behind_proxy = False
    if behind_proxy:
        forwarded = request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0]
        return forwarded.strip()
    return remote


def _error(message: str, status: int, **extra) -> web.Response:
    return web.json_response({"error": message, **extra}, status=status)


@web.middleware
async def api_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/"):
        return await handler(request)
    service = _service(request)
    try:
        if request.path not in PUBLIC_API:
            token = _token(request)
            found = service.accounts.session(token)
            if not found:
                return _error("Войдите в кабинет", 401, auth=True)
            user, session = found
            if (user.must_change_password and session["kind"] == "password"
                    and request.path not in ALLOWED_WITH_TEMP_PASSWORD):
                return _error("Сначала смените временный пароль", 403, must_change_password=True)
            request[USER_KEY] = user
            request[TOKEN_KEY] = token
            request[SESSION_KIND_KEY] = session["kind"]
        return await handler(request)
    except AuthError as exc:
        return _error(str(exc), 401, auth=True)
    except ThrottledError as exc:
        return _error(str(exc), 429)
    except AccessError as exc:
        return _error(str(exc), 403)
    except ConflictError as exc:
        return _error(str(exc), 409, page=exc.page.to_dict())
    except LookupError as exc:
        return _error(str(exc).strip("'"), 404)
    except (ValueError, WorkbookError, GSheetError, json.JSONDecodeError) as exc:
        return _error(str(exc), 400)


async def _json(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("Ожидался JSON-объект")
    return data


def _ok(data: object = None) -> web.Response:
    return web.json_response({"ok": True, **({"data": data} if data is not None else {})})


def _int(request: web.Request, key: str) -> int:
    return int(request.match_info[key])


# ---------- права ----------
def _role(request: web.Request, workspace_id: int | None) -> str | None:
    return _service(request).accounts.role(_user(request), workspace_id)


def _actor(request: web.Request, role: str | None) -> Member:
    return _service(request).accounts.actor(_user(request), role)


def _global_actor(request: web.Request) -> Member:
    """Команда и напоминания общие для всех пространств — их меняет только главный администратор."""
    return _actor(request, ADMIN if _user(request).is_superadmin else VIEWER)


def _require_admin_role(role: str | None) -> None:
    if role != ADMIN:
        raise AccessError("Это действие доступно администратору пространства")


def _workspace(request: web.Request) -> tuple[Workspace, str]:
    workspace = _service(request).db.get_workspace(_int(request, "wid"))
    role = _role(request, workspace.id) if workspace else None
    if not workspace or not role:
        raise LookupError("Пространство не найдено")
    return workspace, role


def _project(request: web.Request) -> tuple[Project, str]:
    project = _service(request).db.get_project(_int(request, "pid"))
    role = _role(request, project.workspace_id) if project else None
    if not project or not role:
        raise LookupError("Проект не найден")
    return project, role


def _task(request: web.Request) -> tuple[Task, Project, str]:
    service = _service(request)
    task = service.get_task(_int(request, "tid"))
    project = service.db.get_project(task.project_id)
    role = _role(request, project.workspace_id) if project else None
    if not project or not role:
        raise LookupError("Задача не найдена")
    return task, project, role


def _page(request: web.Request):
    page = _service(request).pages.get(_int(request, "page_id"))
    role = _role(request, page.workspace_id)
    if not role:
        raise LookupError("Страница не найдена")
    return page, role


def _stage(request: web.Request):
    service = _service(request)
    stage = service.db.get_stage(_int(request, "sid"))
    project = service.db.get_project(stage.project_id) if stage else None
    role = _role(request, project.workspace_id) if project else None
    if not stage or not role:
        raise LookupError("Колонка не найдена")
    return stage, project, role


def _call(request: web.Request):
    service = _service(request)
    call = service.db.get_call(_int(request, "cid"))
    project = service.db.get_project(call.project_id) if call else None
    role = _role(request, project.workspace_id) if project else None
    if not call or not role:
        raise LookupError("Звонок не найден")
    return call, project, role


def _secure(request: web.Request) -> bool:
    webapp = urlparse(_service(request).config.webapp_url)
    return (request.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https"
            or (webapp.scheme == "https" and request.host == webapp.netloc))


def _with_session(request: web.Request, user: User, token: str) -> web.Response:
    response = web.json_response({"ok": True, "token": token, "must_change_password": user.must_change_password})
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="Lax",
                        secure=_secure(request), path="/")
    return response


# ---------- вход ----------
async def auth_login(request: web.Request) -> web.Response:
    body = await _json(request)
    user, token = _service(request).accounts.login(str(body.get("login", "")), str(body.get("password", "")),
                                                   _client_address(request))
    return _with_session(request, user, token)


async def auth_telegram(request: web.Request) -> web.Response:
    service = _service(request)
    body = await _json(request)
    try:
        data = safe_parse_webapp_init_data(service.config.bot_token, str(body.get("init_data", "")))
    except ValueError:
        raise AuthError("Не удалось проверить вход из Telegram") from None
    if time.time() - data.auth_date.timestamp() > INIT_DATA_MAX_AGE or not data.user:
        raise AuthError("Сессия Telegram устарела — откройте кабинет из бота заново")
    member = service.identify(data.user.id, data.user.username)
    user, token = service.accounts.telegram_login(member)
    return _with_session(request, user, token)


async def auth_logout(request: web.Request) -> web.Response:
    _service(request).accounts.logout(request[TOKEN_KEY])
    response = _ok()
    response.del_cookie(SESSION_COOKIE, path="/")
    return response


async def auth_password(request: web.Request) -> web.Response:
    body = await _json(request)
    user = _service(request).accounts.change_password(_user(request), str(body.get("current", "")),
                                                      str(body.get("new", "")), request[TOKEN_KEY])
    return _ok(user.to_dict())


# ---------- общее ----------
def _me(request: web.Request) -> dict:
    service = _service(request)
    user = _user(request)
    member = service.db.get_member(user.member_id) if user.member_id else None
    return {**user.to_dict(), "session_kind": request[SESSION_KIND_KEY],
            "member": member.to_dict() if member else None}


async def bootstrap(request: web.Request) -> web.Response:
    service = _service(request)
    user = _user(request)
    workspaces = [{**w.to_dict(), "role": role} for w, role in service.accounts.workspaces(user)]
    is_admin_somewhere = user.is_superadmin or any(w["role"] == ADMIN for w in workspaces)
    return web.json_response({
        "me": _me(request),
        "today": service.today().isoformat(),
        "workspaces": workspaces,
        "members": [m.to_dict() for m in service.db.list_members(include_inactive=user.is_superadmin)],
        "settings": service.settings() if user.is_superadmin else {},
        "sheets_can_write": service.sheets.can_write,
        "sheets_service_email": service.sheets.service_email() if is_admin_somewhere else None,
        "bot_username": service.bot_username,
        "role_labels": ROLE_LABELS,
    })


def _task_dict(task: Task, today: date, team: logic.Team, my_ids: set[int], actor: Member) -> dict:
    data = task.to_dict(today)
    data["bucket"] = logic.bucket(task, today)
    data["assignee_ids"] = [m.id for m in team.assignees(task)]
    data["mine"] = task.id in my_ids
    data["can_edit"] = actor.is_admin or task.id in my_ids
    return data


async def workspace_data(request: web.Request) -> web.Response:
    service = _service(request)
    workspace, role = _workspace(request)
    actor = _actor(request, role)
    today = service.today()
    team = service.team()
    projects = []
    for project in service.projects(workspace.id):
        tasks = service.db.list_tasks(project.id)
        mine = [t for t in team.tasks_of(actor, tasks) if t.is_open()]
        projects.append({**project.to_dict(), "stats": logic.stats(tasks, today),
                         "my_open": len(mine),
                         "my_urgent": sum(1 for t in mine if logic.bucket(t, today) in ("overdue", "today"))})
    return web.json_response({
        "workspace": workspace.to_dict(),
        "role": role,
        "projects": projects,
        "archived_projects": [p.to_dict() for p in service.db.list_projects(include_archived=True,
                                                                             workspace_id=workspace.id)
                              if p.archived] if role == ADMIN else [],
        "pages": [p.to_dict(with_content=False) for p in service.pages.tree(workspace.id)],
    })


async def workspace_my(request: web.Request) -> web.Response:
    """Мои задачи во всех проектах пространства."""
    service = _service(request)
    workspace, role = _workspace(request)
    actor = _actor(request, role)
    today = service.today()
    team = service.team()
    items = []
    for project in service.projects(workspace.id):
        tasks = service.db.list_tasks(project.id)
        mine = team.tasks_of(actor, tasks)
        my_ids = {t.id for t in mine}
        for task in logic.sorted_tasks(mine, today):
            items.append({**_task_dict(task, today, team, my_ids, actor), "project_name": project.name})
    return web.json_response({"tasks": items, "linked": actor.id > 0})


async def workspace_search(request: web.Request) -> web.Response:
    service = _service(request)
    workspace, _ = _workspace(request)
    query = request.query.get("q", "").strip()
    if len(query) < 2:
        return web.json_response({"pages": [], "tasks": [], "projects": []})
    needle = norm_text(query)
    tasks, projects = [], []
    for project in service.projects(workspace.id):
        if needle in norm_text(project.name):
            projects.append(project.to_dict())
        for task in service.db.list_tasks(project.id):
            haystack = norm_text(f"{task.title} {task.description} {task.responsible} {task.contractor} "
                                       f"{task.block}")
            if needle in haystack:
                tasks.append({"id": task.id, "title": task.title, "project_id": project.id,
                              "project_name": project.name, "status": task.effective_status(service.today())})
    return web.json_response({
        "pages": [p.to_dict(with_content=False) for p in service.pages.search(workspace.id, query)],
        "tasks": tasks[:30],
        "projects": projects[:10],
    })


# ---------- проекты ----------
async def project_data(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    actor = _actor(request, role)
    today = service.today()
    team = service.team()
    tasks = service.db.list_tasks(project.id)
    milestones = service.db.list_milestones(project.id)
    my_ids = {t.id for t in team.tasks_of(actor, tasks)}
    comments = service.db.comment_counts(project.id)
    task_dicts = []
    for task in logic.sorted_tasks(tasks, today):
        data = _task_dict(task, today, team, my_ids, actor)
        data["comments"] = comments.get(task.id, 0)
        task_dicts.append(data)
    return web.json_response({
        "project": project.to_dict(),
        "role": role,
        "tasks": task_dicts,
        "stats": logic.stats(tasks, today),
        "blocks": logic.by_block(tasks, today),
        "workload": logic.workload(tasks, team, today),
        "attention": [t.id for t in logic.attention(tasks, today, limit=15)],
        "milestones": [m.to_dict(project.event_date) for m in milestones],
        "risks": [r.to_dict() for r in service.db.list_risks(project.id)],
        "audit": logic.audit(project, tasks, team, milestones, today) if actor.sees_all else [],
        "stages": [s.to_dict() for s in service.stages(project)],
    })


async def project_create(request: web.Request) -> web.Response:
    service = _service(request)
    workspace, role = _workspace(request)
    body = await _json(request)
    event = date.fromisoformat(body["event_date"]) if body.get("event_date") else None
    project = service.create_project(_actor(request, role), str(body.get("name", "")), event, workspace.id)
    return _ok(project.to_dict())


async def project_update(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    body = await _json(request)
    fields = {}
    if "name" in body:
        fields["name"] = str(body["name"]).strip() or project.name
    if "event_date" in body:
        fields["event_date"] = date.fromisoformat(body["event_date"]) if body["event_date"] else None
    if "archived" in body:
        fields["archived"] = bool(body["archived"])
    updated = service.update_project(_actor(request, role), project, **fields) if fields else project
    if body.get("workspace_id") and int(body["workspace_id"]) != project.workspace_id:
        target = service.db.get_workspace(int(body["workspace_id"]))
        if not target or _role(request, target.id) != ADMIN or role != ADMIN:
            raise AccessError("Переносить проект можно между пространствами, где вы администратор")
        updated = service.db.update_project(project.id, workspace_id=target.id)
    return _ok(updated.to_dict())


async def upload(request: web.Request) -> web.Response:
    service = _service(request)
    workspace, role = _workspace(request)
    reader = await request.multipart()
    field = await reader.next()
    while field is not None and getattr(field, "name", None) != "file":
        field = await reader.next()
    if field is None:
        raise ValueError("Файл не получен")
    data = await field.read(decode=False)
    filename = field.filename or "table.xlsx"
    result, parsed, audit = await service.import_file(bytes(data), filename, _actor(request, role),
                                                      workspace_id=workspace.id)
    return _ok({"project_id": result.project.id, "created": result.project_created, "tasks": len(parsed.tasks),
                "added": len(result.added), "updated": result.updated, "removed": len(result.removed),
                "warnings": parsed.warnings[:10], "audit": audit})


async def connect_sheet(request: web.Request) -> web.Response:
    service = _service(request)
    workspace, role = _workspace(request)
    body = await _json(request)
    result, parsed, audit = await service.connect_sheet(str(body.get("url", "")).strip(), _actor(request, role),
                                                        workspace.id)
    return _ok({"project_id": result.project.id, "tasks": len(parsed.tasks), "added": len(result.added),
                "updated": result.updated, "audit": audit})


async def sync(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    _require_admin_role(role)
    result, parsed, _ = await service.sync_project(project)
    return _ok({"tasks": len(parsed.tasks), "added": len(result.added), "updated": result.updated,
                "removed": len(result.removed), "status_changes": result.status_changes_from_sheet})


async def export_to_chat(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    _require_admin_role(role)
    sent = await service.send_export(project, _actor(request, role))
    return _ok({"sent": sent})


async def export_download(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    _require_admin_role(role)
    data, filename = service.export(project)
    return web.Response(body=data, headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "no-store",
    })


async def plan_get(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    _require_admin_role(role)
    if not project.event_date:
        raise ValueError("Сначала укажите дату мероприятия")
    proposals = logic.propose_deadlines(service.db.list_tasks(project.id), service.team(), project.event_date,
                                        service.today())
    return web.json_response({"items": proposals})


async def plan_apply(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    body = await _json(request)
    applied = await service.apply_plan(project, list(body.get("items", [])), _actor(request, role))
    return _ok({"applied": applied})


# ---------- задачи ----------
async def task_detail(request: web.Request) -> web.Response:
    service = _service(request)
    task, project, role = _task(request)
    actor = _actor(request, role)
    today = service.today()
    team = service.team()
    my_ids = {t.id for t in team.tasks_of(actor, [task])}
    data = _task_dict(task, today, team, my_ids, actor)
    data["workspace_id"] = project.workspace_id
    data["project_name"] = project.name
    data["stages"] = [s.to_dict() for s in service.stages(project)]
    data["events"] = service.db.task_events(task.id)
    data["calls"] = [c.to_dict() for c in service.db.list_calls(task.project_id, include_done=True)
                     if c.task_id == task.id]
    return web.json_response(data)


async def task_create(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    task = await service.create_task(project, await _json(request), _actor(request, role))
    return _ok(task.to_dict(service.today()))


async def task_status(request: web.Request) -> web.Response:
    service = _service(request)
    task, _, role = _task(request)
    body = await _json(request)
    updated = await service.set_status(task, str(body.get("status", "")), _actor(request, role))
    return _ok(updated.to_dict(service.today()))


async def task_comment(request: web.Request) -> web.Response:
    service = _service(request)
    task, _, role = _task(request)
    body = await _json(request)
    await service.add_comment(task, str(body.get("text", "")), _actor(request, role))
    return _ok()


async def task_problem(request: web.Request) -> web.Response:
    service = _service(request)
    task, _, role = _task(request)
    body = await _json(request)
    updated = await service.report_problem(task, str(body.get("text", "")), _actor(request, role))
    return _ok(updated.to_dict(service.today()))


async def task_resolve(request: web.Request) -> web.Response:
    service = _service(request)
    task, _, role = _task(request)
    updated = await service.resolve_problem(task, _actor(request, role))
    return _ok(updated.to_dict(service.today()))


async def task_update(request: web.Request) -> web.Response:
    service = _service(request)
    task, _, role = _task(request)
    updated = await service.update_task(task, await _json(request), _actor(request, role))
    return _ok(updated.to_dict(service.today()))


async def task_delete(request: web.Request) -> web.Response:
    service = _service(request)
    task, _, role = _task(request)
    await service.archive_task(task, _actor(request, role))
    return _ok()


async def task_stage(request: web.Request) -> web.Response:
    service = _service(request)
    task, _, role = _task(request)
    body = await _json(request)
    updated = await service.move_to_stage(task, body.get("stage_id"), _actor(request, role))
    return _ok(updated.to_dict(service.today()))


# ---------- свои колонки доски ----------
async def stage_create(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    stages = service.create_stage(project, await _json(request), _actor(request, role))
    return _ok([s.to_dict() for s in stages])


async def stage_update(request: web.Request) -> web.Response:
    service = _service(request)
    stage, _, role = _stage(request)
    return _ok(service.update_stage(stage, await _json(request), _actor(request, role)).to_dict())


async def stage_move(request: web.Request) -> web.Response:
    service = _service(request)
    stage, _, role = _stage(request)
    body = await _json(request)
    stages = service.move_stage(stage, body.get("index", 0), _actor(request, role))
    return _ok([s.to_dict() for s in stages])


async def stage_delete(request: web.Request) -> web.Response:
    service = _service(request)
    stage, _, role = _stage(request)
    service.delete_stage(stage, _actor(request, role))
    return _ok()


# ---------- обзвон ----------
async def calls_get(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    me = _actor(request, role)
    items = service.call_items(project)
    if not me.sees_all:
        items = [i for i in items if i["kind"] == "scheduled" and i.get("caller") == me.name]
    planned = [c.to_dict() for c in service.db.list_calls(project.id) if me.sees_all or c.member_id == me.id]
    return web.json_response({"items": items, "planned": planned, "today": service.today().isoformat()})


async def calls_create(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    if role == VIEWER:
        raise AccessError("Наблюдатель только просматривает обзвон")
    call = await service.create_call(project, await _json(request), _actor(request, role))
    return _ok(call.to_dict())


async def calls_check(request: web.Request) -> web.Response:
    service = _service(request)
    project, role = _project(request)
    body = await _json(request)
    service.check_call_item(project, str(body.get("key", "")), _actor(request, role),
                            str(body.get("note", "")), bool(body.get("checked", True)))
    return _ok()


async def call_done(request: web.Request) -> web.Response:
    service = _service(request)
    call, _, role = _call(request)
    body = await _json(request)
    return _ok((await service.finish_call(call, _actor(request, role), str(body.get("result", "")))).to_dict())


async def call_cancel(request: web.Request) -> web.Response:
    service = _service(request)
    call, _, role = _call(request)
    return _ok(service.cancel_call(call, _actor(request, role)).to_dict())


# ---------- страницы ----------
def _author(request: web.Request) -> str:
    return _user(request).name


async def page_create(request: web.Request) -> web.Response:
    service = _service(request)
    workspace, role = _workspace(request)
    page = service.pages.create(workspace.id, role, _author(request), await _json(request))
    return _ok(page.to_dict())


async def page_get(request: web.Request) -> web.Response:
    page, role = _page(request)
    return web.json_response({**page.to_dict(), "can_edit": role != VIEWER and not page.archived})


async def page_save(request: web.Request) -> web.Response:
    service = _service(request)
    page, role = _page(request)
    updated = service.pages.save(page, role, _author(request), await _json(request))
    return _ok(updated.to_dict())


async def page_move(request: web.Request) -> web.Response:
    service = _service(request)
    page, role = _page(request)
    body = await _json(request)
    moved = service.pages.move(page, role, _author(request), body.get("parent_id"), body.get("index"))
    return _ok(moved.to_dict(with_content=False))


async def page_delete(request: web.Request) -> web.Response:
    service = _service(request)
    page, role = _page(request)
    service.pages.archive(page, role)
    return _ok()


async def page_restore(request: web.Request) -> web.Response:
    service = _service(request)
    page, role = _page(request)
    return _ok(service.pages.restore(page, role, _author(request)).to_dict(with_content=False))


async def page_purge(request: web.Request) -> web.Response:
    service = _service(request)
    page, role = _page(request)
    service.pages.purge(page, role)
    return _ok()


async def workspace_trash(request: web.Request) -> web.Response:
    service = _service(request)
    workspace, _ = _workspace(request)
    return web.json_response({"pages": [p.to_dict(with_content=False) for p in service.pages.trash(workspace.id)]})


# ---------- команда, напоминания, доступ (главный администратор) ----------
async def member_save(request: web.Request) -> web.Response:
    service = _service(request)
    member = service.save_member(_global_actor(request), await _json(request))
    return _ok(member.to_dict())


async def settings_save(request: web.Request) -> web.Response:
    service = _service(request)
    return _ok(service.update_settings(await _json(request), _global_actor(request)))


def _require_superadmin(request: web.Request) -> None:
    if not _user(request).is_superadmin:
        raise AccessError("Управлять пользователями и пространствами может только главный администратор")


def _admin_payload(service: Service) -> dict:
    users = []
    for user in service.db.list_users():
        users.append({**user.to_dict(), "roles": service.db.user_roles(user.id)})
    return {
        "users": users,
        "workspaces": [w.to_dict() for w in service.db.list_workspaces()],
        "members": [m.to_dict() for m in service.db.list_members(include_inactive=True)],
    }


async def admin_overview(request: web.Request) -> web.Response:
    _require_superadmin(request)
    return web.json_response(_admin_payload(_service(request)))


async def admin_user_create(request: web.Request) -> web.Response:
    service = _service(request)
    user = service.accounts.create_user(_user(request), await _json(request))
    return _ok({**user.to_dict(), "roles": service.db.user_roles(user.id)})


async def admin_user_update(request: web.Request) -> web.Response:
    service = _service(request)
    user = service.accounts.update_user(_user(request), _int(request, "uid"), await _json(request))
    return _ok({**user.to_dict(), "roles": service.db.user_roles(user.id)})


async def admin_user_password(request: web.Request) -> web.Response:
    service = _service(request)
    body = await _json(request)
    user, password = service.accounts.reset_password(_user(request), _int(request, "uid"),
                                                     str(body.get("password") or ""))
    return _ok({"user": user.to_dict(), "password": password})


async def admin_workspace_create(request: web.Request) -> web.Response:
    service = _service(request)
    body = await _json(request)
    workspace = service.accounts.create_workspace(_user(request), str(body.get("name", "")), str(body.get("icon", "")))
    return _ok(workspace.to_dict())


async def admin_workspace_update(request: web.Request) -> web.Response:
    service = _service(request)
    body = await _json(request)
    workspace = service.accounts.update_workspace(_user(request), _int(request, "wid"), str(body.get("name", "")),
                                                  str(body.get("icon", "")))
    return _ok(workspace.to_dict())


# ---------- статика ----------
async def index(request: web.Request) -> web.Response:
    html = (WEBAPP_DIR / "index.html").read_text(encoding="utf-8").replace("{{v}}", request.app[VERSION_KEY])
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


@web.middleware
async def security_headers(request: web.Request, handler):
    response = await handler(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def _static_version() -> str:
    digest = hashlib.sha1()
    for path in sorted(WEBAPP_DIR.glob("*")):
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def create_app(service: Service) -> web.Application:
    app = web.Application(middlewares=[security_headers, api_middleware], client_max_size=25 * 1024 * 1024)
    app[SERVICE_KEY] = service
    app[VERSION_KEY] = _static_version()
    r = app.router
    r.add_get("/", index)
    r.add_get("/health", health)
    r.add_static("/static/", WEBAPP_DIR)

    r.add_post("/api/auth/login", auth_login)
    r.add_post("/api/auth/telegram", auth_telegram)
    r.add_post("/api/auth/logout", auth_logout)
    r.add_post("/api/auth/password", auth_password)
    r.add_get("/api/bootstrap", bootstrap)

    r.add_get("/api/w/{wid:\\d+}", workspace_data)
    r.add_get("/api/w/{wid:\\d+}/my", workspace_my)
    r.add_get("/api/w/{wid:\\d+}/search", workspace_search)
    r.add_get("/api/w/{wid:\\d+}/trash", workspace_trash)
    r.add_post("/api/w/{wid:\\d+}/projects", project_create)
    r.add_post("/api/w/{wid:\\d+}/upload", upload)
    r.add_post("/api/w/{wid:\\d+}/sheet", connect_sheet)
    r.add_post("/api/w/{wid:\\d+}/pages", page_create)

    r.add_get("/api/projects/{pid:\\d+}", project_data)
    r.add_patch("/api/projects/{pid:\\d+}", project_update)
    r.add_post("/api/projects/{pid:\\d+}/tasks", task_create)
    r.add_post("/api/projects/{pid:\\d+}/sync", sync)
    r.add_post("/api/projects/{pid:\\d+}/export", export_to_chat)
    r.add_get("/api/projects/{pid:\\d+}/export.xlsx", export_download)
    r.add_get("/api/projects/{pid:\\d+}/plan", plan_get)
    r.add_post("/api/projects/{pid:\\d+}/plan", plan_apply)
    r.add_get("/api/projects/{pid:\\d+}/calls", calls_get)
    r.add_post("/api/projects/{pid:\\d+}/calls", calls_create)
    r.add_post("/api/projects/{pid:\\d+}/calls/check", calls_check)
    r.add_post("/api/calls/{cid:\\d+}/done", call_done)
    r.add_post("/api/calls/{cid:\\d+}/cancel", call_cancel)

    r.add_get("/api/tasks/{tid:\\d+}", task_detail)
    r.add_patch("/api/tasks/{tid:\\d+}", task_update)
    r.add_delete("/api/tasks/{tid:\\d+}", task_delete)
    r.add_post("/api/tasks/{tid:\\d+}/status", task_status)
    r.add_post("/api/tasks/{tid:\\d+}/comment", task_comment)
    r.add_post("/api/tasks/{tid:\\d+}/problem", task_problem)
    r.add_post("/api/tasks/{tid:\\d+}/resolve", task_resolve)
    r.add_post("/api/tasks/{tid:\\d+}/stage", task_stage)
    r.add_post("/api/projects/{pid:\\d+}/stages", stage_create)
    r.add_patch("/api/stages/{sid:\\d+}", stage_update)
    r.add_post("/api/stages/{sid:\\d+}/move", stage_move)
    r.add_delete("/api/stages/{sid:\\d+}", stage_delete)

    r.add_get("/api/pages/{page_id:\\d+}", page_get)
    r.add_put("/api/pages/{page_id:\\d+}", page_save)
    r.add_delete("/api/pages/{page_id:\\d+}", page_delete)
    r.add_post("/api/pages/{page_id:\\d+}/move", page_move)
    r.add_post("/api/pages/{page_id:\\d+}/restore", page_restore)
    r.add_post("/api/pages/{page_id:\\d+}/purge", page_purge)

    r.add_post("/api/members", member_save)
    r.add_post("/api/settings", settings_save)
    r.add_get("/api/admin", admin_overview)
    r.add_post("/api/admin/users", admin_user_create)
    r.add_patch("/api/admin/users/{uid:\\d+}", admin_user_update)
    r.add_post("/api/admin/users/{uid:\\d+}/password", admin_user_password)
    r.add_post("/api/admin/workspaces", admin_workspace_create)
    r.add_patch("/api/admin/workspaces/{wid:\\d+}", admin_workspace_update)
    return app
