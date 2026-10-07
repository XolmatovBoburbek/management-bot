"""SQLite-хранилище. Одна таблица задач на все проекты, простые синхронные вызовы:
нагрузка — команда из десятка человек, блокировка event loop на микросекунды не страшна.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone, tzinfo
from pathlib import Path

from app.excel_io import ParsedWorkbook
from app.models import (
    Call,
    Member,
    Milestone,
    Page,
    Project,
    Risk,
    Stage,
    Task,
    User,
    Workspace,
    dumps,
    from_iso,
    norm_text,
    to_iso,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT UNIQUE,
    role TEXT DEFAULT '',
    aliases TEXT DEFAULT '',
    phone TEXT DEFAULT '',
    is_admin INTEGER DEFAULT 0,
    is_pm INTEGER DEFAULT 0,
    telegram_id INTEGER UNIQUE,
    active INTEGER DEFAULT 1,
    assists_id INTEGER,
    is_observer INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    event_date TEXT,
    info_json TEXT DEFAULT '[]',
    source_type TEXT DEFAULT 'manual',
    source_url TEXT,
    file_path TEXT,
    layout_json TEXT DEFAULT '{}',
    sheet_values_json TEXT DEFAULT '{}',
    last_sync_at TEXT,
    last_sync_error TEXT,
    archived INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    workspace_id INTEGER
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    no TEXT DEFAULT '',
    block TEXT DEFAULT '',
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    priority TEXT DEFAULT '',
    responsible TEXT DEFAULT '',
    status TEXT DEFAULT 'todo',
    start_date TEXT,
    deadline TEXT,
    fact_date TEXT,
    contractor TEXT DEFAULT '',
    proof TEXT DEFAULT '',
    sheet_comment TEXT DEFAULT '',
    blocked INTEGER DEFAULT 0,
    blocked_reason TEXT DEFAULT '',
    eta TEXT,
    row_index INTEGER,
    sort_order INTEGER DEFAULT 0,
    source TEXT DEFAULT 'sheet',
    sheet_values_json TEXT DEFAULT '{}',
    archived INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    stage_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, archived);
CREATE TABLE IF NOT EXISTS stages (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    color TEXT DEFAULT 'gray',
    status TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_stages_project ON stages(project_id, sort_order);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    member_id INTEGER,
    kind TEXT NOT NULL,
    text TEXT DEFAULT '',
    created_at TEXT,
    actor_name TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events(task_id);
CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    sort_order INTEGER DEFAULT 0,
    title TEXT NOT NULL,
    date_raw TEXT DEFAULT '',
    criteria TEXT DEFAULT '',
    status TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS risks (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    no TEXT DEFAULT '',
    title TEXT NOT NULL,
    probability TEXT DEFAULT '',
    impact TEXT DEFAULT '',
    scenario TEXT DEFAULT '',
    mitigation TEXT DEFAULT '',
    owner TEXT DEFAULT '',
    status TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    task_id INTEGER,
    member_id INTEGER,
    contact TEXT NOT NULL,
    phone TEXT DEFAULT '',
    note TEXT DEFAULT '',
    due_at TEXT NOT NULL,
    status TEXT DEFAULT 'planned',
    result TEXT DEFAULT '',
    notified_at TEXT,
    created_by INTEGER,
    created_at TEXT,
    done_at TEXT
);
CREATE TABLE IF NOT EXISTS call_checks (
    project_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    key TEXT NOT NULL,
    member_id INTEGER,
    note TEXT DEFAULT '',
    created_at TEXT,
    actor_name TEXT DEFAULT '',
    PRIMARY KEY (project_id, day, key)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS notification_log (
    key TEXT PRIMARY KEY,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    icon TEXT DEFAULT '',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    login TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    password_hash TEXT DEFAULT '',
    must_change_password INTEGER DEFAULT 0,
    is_superadmin INTEGER DEFAULT 0,
    member_id INTEGER UNIQUE,
    active INTEGER DEFAULT 1,
    created_at TEXT,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS workspace_users (
    workspace_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    kind TEXT DEFAULT 'password',
    created_at TEXT,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    workspace_id INTEGER NOT NULL,
    parent_id INTEGER,
    title TEXT DEFAULT '',
    icon TEXT DEFAULT '',
    content_json TEXT DEFAULT '[]',
    text_index TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    version INTEGER DEFAULT 1,
    archived INTEGER DEFAULT 0,
    archived_at TEXT,
    created_by TEXT DEFAULT '',
    updated_by TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pages_workspace ON pages(workspace_id, archived);
"""

# Поля, которые можно менять и в таблице, и в боте. При повторном импорте
# значение из таблицы применяется только если оно изменилось в самой таблице
# (трёхсторонний merge), иначе сохраняется правка, сделанная в боте.
MERGED_FIELDS = ("status", "start_date", "deadline", "fact_date", "responsible", "priority")
DATE_COLUMNS = {"start_date", "deadline", "fact_date", "eta"}


def _utc_iso(value: datetime) -> str:
    """Все служебные отметки времени — в UTC, чтобы их можно было сравнивать строками."""
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _task_from_row(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        no=row["no"] or "",
        block=row["block"] or "",
        description=row["description"] or "",
        priority=row["priority"] or "",
        responsible=row["responsible"] or "",
        status=row["status"] or "todo",
        start_date=from_iso(row["start_date"]),
        deadline=from_iso(row["deadline"]),
        fact_date=from_iso(row["fact_date"]),
        contractor=row["contractor"] or "",
        proof=row["proof"] or "",
        sheet_comment=row["sheet_comment"] or "",
        blocked=bool(row["blocked"]),
        blocked_reason=row["blocked_reason"] or "",
        eta=from_iso(row["eta"]),
        row_index=row["row_index"],
        sort_order=row["sort_order"] or 0,
        source=row["source"] or "sheet",
        sheet_values=json.loads(row["sheet_values_json"] or "{}"),
        archived=bool(row["archived"]),
        updated_at=row["updated_at"],
        stage_id=row["stage_id"],
    )


def _stage_from_row(row: sqlite3.Row) -> Stage:
    return Stage(id=row["id"], project_id=row["project_id"], title=row["title"], color=row["color"] or "gray",
                 status=row["status"] or "", sort_order=row["sort_order"] or 0)


def _member_from_row(row: sqlite3.Row) -> Member:
    return Member(
        id=row["id"],
        name=row["name"],
        username=row["username"] or "",
        role=row["role"] or "",
        aliases=row["aliases"] or "",
        phone=row["phone"] or "",
        is_admin=bool(row["is_admin"]),
        is_pm=bool(row["is_pm"]),
        telegram_id=row["telegram_id"],
        active=bool(row["active"]),
        assists_id=row["assists_id"],
        is_observer=bool(row["is_observer"]),
    )


def _project_from_row(row: sqlite3.Row) -> Project:
    return Project(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        event_date=from_iso(row["event_date"]),
        info=json.loads(row["info_json"] or "[]"),
        source_type=row["source_type"] or "manual",
        source_url=row["source_url"],
        file_path=row["file_path"],
        layout=json.loads(row["layout_json"] or "{}"),
        sheet_values=json.loads(row["sheet_values_json"] or "{}"),
        last_sync_at=row["last_sync_at"],
        last_sync_error=row["last_sync_error"],
        archived=bool(row["archived"]),
        updated_at=row["updated_at"],
        workspace_id=row["workspace_id"],
    )


def _user_from_row(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        login=row["login"],
        name=row["name"],
        password_hash=row["password_hash"] or "",
        must_change_password=bool(row["must_change_password"]),
        is_superadmin=bool(row["is_superadmin"]),
        member_id=row["member_id"],
        active=bool(row["active"]),
        last_login_at=row["last_login_at"],
    )


def _page_from_row(row: sqlite3.Row) -> Page:
    keys = row.keys()
    return Page(
        id=row["id"],
        workspace_id=row["workspace_id"],
        parent_id=row["parent_id"],
        title=row["title"] or "",
        icon=row["icon"] or "",
        content=json.loads(row["content_json"] or "[]") if "content_json" in keys else [],
        sort_order=row["sort_order"] or 0,
        version=row["version"] or 1,
        archived=bool(row["archived"]),
        created_by=row["created_by"] or "",
        updated_by=row["updated_by"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _call_from_row(row: sqlite3.Row) -> Call:
    return Call(
        id=row["id"],
        project_id=row["project_id"],
        member_id=row["member_id"],
        contact=row["contact"],
        due_at=datetime.fromisoformat(row["due_at"]),
        task_id=row["task_id"],
        phone=row["phone"] or "",
        note=row["note"] or "",
        status=row["status"],
        result=row["result"] or "",
        notified_at=row["notified_at"],
        created_by=row["created_by"],
    )


def _db_value(field_name: str, value: object) -> object:
    if field_name in DATE_COLUMNS:
        return to_iso(value) if isinstance(value, date) else value
    return value


class ImportResult:
    def __init__(self, project: Project, created: bool):
        self.project = project
        self.project_created = created
        self.added: list[int] = []
        self.updated: int = 0
        self.removed: list[str] = []
        self.status_changes_from_sheet: int = 0
        # task_id -> ответственный до импорта (для уведомления о новых назначениях)
        self.previous_responsible: dict[int, str] = {}


class Database:
    def __init__(self, path: str | Path, tz: tzinfo | None = None):
        self.path = str(path)
        self.tz = tz
        self.clock = None  # подменяется в тестах (Service передаёт свои часы)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Колонки, добавленные после первого запуска, — для уже существующих баз."""
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(members)")}
        if "assists_id" not in columns:
            self.conn.execute("ALTER TABLE members ADD COLUMN assists_id INTEGER")
        if "is_observer" not in columns:
            self.conn.execute("ALTER TABLE members ADD COLUMN is_observer INTEGER DEFAULT 0")
        for table, column, ddl in (
            ("projects", "workspace_id", "INTEGER"),
            ("task_events", "actor_name", "TEXT DEFAULT ''"),
            ("call_checks", "actor_name", "TEXT DEFAULT ''"),
            ("tasks", "stage_id", "INTEGER"),
        ):
            if column not in {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_workspace ON projects(workspace_id)")

    def close(self) -> None:
        self.conn.close()

    def _now_iso(self) -> str:
        return _utc_iso(self.clock() if self.clock else datetime.now(timezone.utc))

    # ---------- настройки / журнал уведомлений ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str | None) -> None:
        if value is None:
            self.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        self.conn.commit()

    def all_settings(self) -> dict[str, str]:
        return {r["key"]: r["value"] for r in self.conn.execute("SELECT key, value FROM settings")}

    def mark_sent(self, key: str) -> bool:
        """True если уведомление с таким ключом ещё не отправлялось (и помечает его)."""
        cur = self.conn.execute("INSERT OR IGNORE INTO notification_log(key, sent_at) VALUES(?, ?)", (key, self._now_iso()))
        self.conn.commit()
        return cur.rowcount == 1

    def was_sent(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM notification_log WHERE key = ?", (key,)).fetchone() is not None

    # ---------- участники ----------
    def list_members(self, include_inactive: bool = False) -> list[Member]:
        sql = "SELECT * FROM members" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY id"
        return [_member_from_row(r) for r in self.conn.execute(sql)]

    def get_member(self, member_id: int) -> Member | None:
        row = self.conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
        return _member_from_row(row) if row else None

    def member_by_telegram(self, telegram_id: int) -> Member | None:
        row = self.conn.execute("SELECT * FROM members WHERE telegram_id = ? AND active = 1", (telegram_id,)).fetchone()
        return _member_from_row(row) if row else None

    def member_by_username(self, username: str) -> Member | None:
        uname = (username or "").lstrip("@").lower()
        if not uname:
            return None
        row = self.conn.execute("SELECT * FROM members WHERE lower(username) = ? AND active = 1", (uname,)).fetchone()
        return _member_from_row(row) if row else None

    def upsert_member(self, *, name: str, username: str = "", role: str = "", aliases: str = "", phone: str = "",
                      is_admin: bool = False, is_pm: bool = False, member_id: int | None = None,
                      active: bool = True, assists_id: int | None = None, is_observer: bool = False) -> Member:
        uname = username.lstrip("@").strip().lower() or None
        if uname:
            taken = self.conn.execute("SELECT id FROM members WHERE lower(username) = ?", (uname,)).fetchone()
            if taken and member_id is not None and taken["id"] != member_id:
                raise ValueError(f"Ник @{uname} уже есть у другого участника")
        if member_id is None and uname:
            existing = self.conn.execute("SELECT id FROM members WHERE lower(username) = ?", (uname,)).fetchone()
            member_id = existing["id"] if existing else None
        if member_id is None:
            cur = self.conn.execute(
                "INSERT INTO members(name, username, role, aliases, phone, is_admin, is_pm, active, assists_id, "
                "is_observer, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (name, uname, role, aliases, phone, int(is_admin), int(is_pm), int(active), assists_id,
                 int(is_observer), self._now_iso()),
            )
            member_id = cur.lastrowid
        else:
            self.conn.execute(
                "UPDATE members SET name=?, username=?, role=?, aliases=?, phone=?, is_admin=?, is_pm=?, active=?, "
                "assists_id=?, is_observer=? WHERE id=?",
                (name, uname, role, aliases, phone, int(is_admin), int(is_pm), int(active), assists_id,
                 int(is_observer), member_id),
            )
        self.conn.commit()
        return self.get_member(member_id)  # type: ignore[return-value]

    def set_assists(self, member_id: int, assists_id: int | None) -> None:
        self.conn.execute("UPDATE members SET assists_id = ? WHERE id = ?", (assists_id, member_id))
        self.conn.commit()

    def bind_telegram(self, member_id: int, telegram_id: int) -> None:
        self.conn.execute("UPDATE members SET telegram_id = NULL WHERE telegram_id = ? AND id != ?",
                          (telegram_id, member_id))
        self.conn.execute("UPDATE members SET telegram_id = ? WHERE id = ?", (telegram_id, member_id))
        self.conn.commit()

    # ---------- проекты ----------
    def list_projects(self, include_archived: bool = False, workspace_id: int | None = None) -> list[Project]:
        where, args = [], []
        if not include_archived:
            where.append("archived = 0")
        if workspace_id is not None:
            where.append("workspace_id = ?")
            args.append(workspace_id)
        sql = "SELECT * FROM projects" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY updated_at DESC"
        return [_project_from_row(r) for r in self.conn.execute(sql, args)]

    def get_project(self, project_id: int) -> Project | None:
        row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return _project_from_row(row) if row else None

    def project_by_code(self, code: str) -> Project | None:
        row = self.conn.execute("SELECT * FROM projects WHERE code = ?", (code,)).fetchone()
        return _project_from_row(row) if row else None

    def create_project(self, name: str, code: str, event_date: date | None = None,
                       workspace_id: int | None = None) -> Project:
        now = self._now_iso()
        base, n = code, 1
        while self.project_by_code(code):
            n += 1
            code = f"{base}_{n}"
        if workspace_id is None:
            default = self.default_workspace()
            workspace_id = default.id if default else None
        cur = self.conn.execute(
            "INSERT INTO projects(code, name, event_date, workspace_id, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (code, name, to_iso(event_date), workspace_id, now, now),
        )
        self.conn.commit()
        return self.get_project(cur.lastrowid)  # type: ignore[return-value]

    def update_project(self, project_id: int, **fields: object) -> Project:
        allowed = {"name", "event_date", "source_type", "source_url", "file_path", "last_sync_at",
                   "last_sync_error", "archived", "info", "layout", "sheet_values", "workspace_id"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown project field {key}")
            column = {"info": "info_json", "layout": "layout_json", "sheet_values": "sheet_values_json"}.get(key, key)
            if key in {"info", "layout", "sheet_values"}:
                value = dumps(value)
            elif key == "event_date":
                value = to_iso(value) if isinstance(value, date) else value
            elif key == "archived":
                value = int(bool(value))
            sets.append(f"{column} = ?")
            values.append(value)
        sets.append("updated_at = ?")
        values.append(self._now_iso())
        self.conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", (*values, project_id))
        self.conn.commit()
        return self.get_project(project_id)  # type: ignore[return-value]

    # ---------- задачи ----------
    def list_tasks(self, project_id: int, include_archived: bool = False) -> list[Task]:
        sql = "SELECT * FROM tasks WHERE project_id = ?" + ("" if include_archived else " AND archived = 0")
        sql += " ORDER BY sort_order, id"
        return [_task_from_row(r) for r in self.conn.execute(sql, (project_id,))]

    def get_task(self, task_id: int) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _task_from_row(row) if row else None

    def create_task(self, project_id: int, *, title: str, block: str = "", description: str = "",
                    priority: str = "", responsible: str = "", start_date: date | None = None,
                    deadline: date | None = None, contractor: str = "", status: str = "todo",
                    stage_id: int | None = None) -> Task:
        now = self._now_iso()
        max_order = self.conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM tasks WHERE project_id = ?",
                                      (project_id,)).fetchone()["m"]
        cur = self.conn.execute(
            "INSERT INTO tasks(project_id, title, block, description, priority, responsible, status, start_date, "
            "deadline, contractor, sort_order, source, created_at, updated_at, stage_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, title, block, description, priority, responsible, status, to_iso(start_date),
             to_iso(deadline), contractor, max_order + 1, "manual", now, now, stage_id),
        )
        self.conn.commit()
        return self.get_task(cur.lastrowid)  # type: ignore[return-value]

    def update_task(self, task_id: int, **fields: object) -> Task:
        allowed = {"title", "block", "description", "priority", "responsible", "status", "start_date", "deadline",
                   "fact_date", "contractor", "proof", "blocked", "blocked_reason", "eta", "archived", "stage_id"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown task field {key}")
            if key in {"blocked", "archived"}:
                value = int(bool(value))
            sets.append(f"{key} = ?")
            values.append(_db_value(key, value))
        if not sets:
            return self.get_task(task_id)  # type: ignore[return-value]
        sets.append("updated_at = ?")
        values.append(self._now_iso())
        self.conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", (*values, task_id))
        self.conn.commit()
        return self.get_task(task_id)  # type: ignore[return-value]

    # ---------- колонки доски ----------
    def list_stages(self, project_id: int) -> list[Stage]:
        rows = self.conn.execute("SELECT * FROM stages WHERE project_id = ? ORDER BY sort_order, id", (project_id,))
        return [_stage_from_row(r) for r in rows]

    def get_stage(self, stage_id: int) -> Stage | None:
        row = self.conn.execute("SELECT * FROM stages WHERE id = ?", (stage_id,)).fetchone()
        return _stage_from_row(row) if row else None

    def create_stage(self, project_id: int, title: str, color: str = "gray", status: str = "") -> Stage:
        max_order = self.conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM stages WHERE project_id = ?",
                                      (project_id,)).fetchone()["m"]
        cur = self.conn.execute(
            "INSERT INTO stages(project_id, title, color, status, sort_order, created_at) VALUES(?,?,?,?,?,?)",
            (project_id, title, color, status, max_order + 1, self._now_iso()))
        self.conn.commit()
        return self.get_stage(cur.lastrowid)  # type: ignore[return-value]

    def update_stage(self, stage_id: int, **fields: object) -> Stage:
        allowed = {"title", "color", "status"}
        sets = [f"{key} = ?" for key in fields if key in allowed]
        values = [value for key, value in fields.items() if key in allowed]
        if sets:
            self.conn.execute(f"UPDATE stages SET {', '.join(sets)} WHERE id = ?", (*values, stage_id))
            self.conn.commit()
        return self.get_stage(stage_id)  # type: ignore[return-value]

    def reorder_stages(self, ids: list[int]) -> None:
        for order, stage_id in enumerate(ids, start=1):
            self.conn.execute("UPDATE stages SET sort_order = ? WHERE id = ?", (order, stage_id))
        self.conn.commit()

    def delete_stage(self, stage_id: int) -> None:
        self.conn.execute("UPDATE tasks SET stage_id = NULL WHERE stage_id = ?", (stage_id,))
        self.conn.execute("DELETE FROM stages WHERE id = ?", (stage_id,))
        self.conn.commit()

    def add_event(self, task_id: int, kind: str, text: str = "", member_id: int | None = None,
                  actor_name: str = "") -> None:
        # Отрицательный id — аккаунт веб-кабинета без связи с участником команды: имя пишем текстом.
        self.conn.execute(
            "INSERT INTO task_events(task_id, member_id, kind, text, created_at, actor_name) VALUES(?,?,?,?,?,?)",
            (task_id, member_id if member_id and member_id > 0 else None, kind, text, self._now_iso(), actor_name),
        )
        self.conn.commit()

    def task_events(self, task_id: int, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT e.*, COALESCE(m.name, NULLIF(e.actor_name, '')) AS member_name FROM task_events e "
            "LEFT JOIN members m ON m.id = e.member_id "
            "WHERE e.task_id = ? ORDER BY e.id DESC LIMIT ?",
            (task_id, limit),
        )
        return [dict(r) for r in rows]

    def comment_counts(self, project_id: int) -> dict[int, int]:
        rows = self.conn.execute(
            "SELECT e.task_id, COUNT(*) AS n FROM task_events e JOIN tasks t ON t.id = e.task_id "
            "WHERE t.project_id = ? AND e.kind IN ('comment', 'problem') GROUP BY e.task_id", (project_id,))
        return {r["task_id"]: r["n"] for r in rows}

    def events_since(self, project_id: int, since_iso: str, kinds: tuple[str, ...]) -> list[dict]:
        marks = ",".join("?" * len(kinds))
        rows = self.conn.execute(
            f"SELECT e.*, t.title AS task_title, COALESCE(m.name, NULLIF(e.actor_name, '')) AS member_name "
            f"FROM task_events e "
            f"JOIN tasks t ON t.id = e.task_id LEFT JOIN members m ON m.id = e.member_id "
            f"WHERE t.project_id = ? AND e.created_at >= ? AND e.kind IN ({marks}) ORDER BY e.id",
            (project_id, since_iso, *kinds),
        )
        return [dict(r) for r in rows]

    def comment_lines(self, project_id: int) -> dict[int, list[str]]:
        """Комментарии из бота в формате строки для колонки «Комментарии исполнителя»."""
        rows = self.conn.execute(
            "SELECT e.task_id, e.text, e.created_at, e.kind, COALESCE(m.name, NULLIF(e.actor_name, '')) AS member_name "
            "FROM task_events e "
            "JOIN tasks t ON t.id = e.task_id LEFT JOIN members m ON m.id = e.member_id "
            "WHERE t.project_id = ? AND e.kind IN ('comment', 'problem') ORDER BY e.id",
            (project_id,),
        )
        result: dict[int, list[str]] = {}
        for r in rows:
            stamp = datetime.fromisoformat(r["created_at"]).astimezone(self.tz).strftime("%d.%m")
            prefix = "⚠️ " if r["kind"] == "problem" else ""
            result.setdefault(r["task_id"], []).append(f"[{stamp} {r['member_name'] or 'бот'}] {prefix}{r['text']}")
        return result

    # ---------- импорт ----------
    def apply_import(self, parsed: ParsedWorkbook, *, file_path: str | None, source_type: str,
                     source_url: str | None = None, project_id: int | None = None,
                     workspace_id: int | None = None) -> ImportResult:
        project = self.get_project(project_id) if project_id else self.project_by_code(parsed.code)
        created = project is None
        if project is None:
            project = self.create_project(parsed.name, parsed.code, parsed.event_date, workspace_id)
        result = ImportResult(project, created)

        # Дата мероприятия: тот же merge, что и для задач.
        sheet_values = dict(project.sheet_values)
        incoming_event = to_iso(parsed.event_date)
        event_date = to_iso(project.event_date)
        if created or incoming_event != sheet_values.get("event_date"):
            event_date = incoming_event or event_date
        sheet_values["event_date"] = incoming_event

        self.update_project(
            project.id, name=parsed.name, event_date=event_date, info=parsed.info, layout=parsed.layout,
            sheet_values=sheet_values, file_path=file_path, source_type=source_type,
            source_url=source_url if source_url is not None else project.source_url,
            last_sync_at=self._now_iso(), last_sync_error=None, archived=False,
        )

        existing = self.list_tasks(project.id, include_archived=True)
        by_title: dict[str, Task] = {}
        for task in existing:
            by_title.setdefault(norm_text(task.title), task)
        by_no = {task.no: task for task in existing if task.no and task.source == "sheet"}
        incoming_titles = {norm_text(p.title) for p in parsed.tasks}
        matched: set[int] = set()
        now = self._now_iso()

        for order, item in enumerate(parsed.tasks, start=1):
            task = by_title.get(norm_text(item.title))
            if task is None and item.no in by_no:
                candidate = by_no[item.no]
                # Строку переименовали в таблице — номер тот же, старого названия больше нет.
                if norm_text(candidate.title) not in incoming_titles:
                    task = candidate
            if task is not None and task.id in matched:
                task = None  # дубликат названия в таблице — заводим отдельной задачей
            incoming = {
                "status": item.status,
                "start_date": to_iso(item.start_date),
                "deadline": to_iso(item.deadline),
                "fact_date": to_iso(item.fact_date),
                "responsible": item.responsible,
                "priority": item.priority,
            }
            if task is None:
                cur = self.conn.execute(
                    "INSERT INTO tasks(project_id, no, block, title, description, priority, responsible, status, "
                    "start_date, deadline, fact_date, contractor, proof, sheet_comment, row_index, sort_order, "
                    "source, sheet_values_json, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project.id, item.no, item.block, item.title, item.description, item.priority, item.responsible,
                     item.status, incoming["start_date"], incoming["deadline"], incoming["fact_date"],
                     item.contractor, item.proof, item.comment, item.row_index, order, "sheet",
                     dumps(incoming), now, now),
                )
                result.added.append(cur.lastrowid)
                matched.add(cur.lastrowid)
                continue

            matched.add(task.id)
            result.previous_responsible[task.id] = task.responsible
            last_seen = task.sheet_values
            current = {f: _db_value(f, getattr(task, f)) for f in MERGED_FIELDS}
            merged = dict(current)
            for f in MERGED_FIELDS:
                if task.source != "sheet" or incoming[f] != last_seen.get(f):
                    # Новое значение в таблице (или задача впервые пришла из таблицы) — берём его,
                    # но пустая ячейка не затирает то, что заполнили в боте.
                    if incoming[f] or task.source == "sheet":
                        merged[f] = incoming[f]
            if merged["status"] != current["status"]:
                result.status_changes_from_sheet += 1
            self.conn.execute(
                "UPDATE tasks SET no=?, block=?, title=?, description=?, contractor=?, proof=?, sheet_comment=?, "
                "row_index=?, sort_order=?, source='sheet', archived=0, sheet_values_json=?, status=?, "
                "start_date=?, deadline=?, fact_date=?, responsible=?, priority=?, updated_at=? WHERE id=?",
                (item.no, item.block, item.title, item.description, item.contractor, item.proof, item.comment,
                 item.row_index, order, dumps(incoming), merged["status"], merged["start_date"],
                 merged["deadline"], merged["fact_date"], merged["responsible"], merged["priority"], now, task.id),
            )
            result.updated += 1

        for task in existing:
            if task.id not in matched and task.source == "sheet" and not task.archived:
                self.conn.execute("UPDATE tasks SET archived = 1, updated_at = ? WHERE id = ?", (now, task.id))
                result.removed.append(task.title)

        # Вехи и риски ведутся только в таблице — просто перезаписываем.
        if parsed.milestones or parsed.layout.get("milestones_sheet"):
            self.conn.execute("DELETE FROM milestones WHERE project_id = ?", (project.id,))
            for i, m in enumerate(parsed.milestones):
                self.conn.execute(
                    "INSERT INTO milestones(project_id, sort_order, title, date_raw, criteria, status) "
                    "VALUES(?,?,?,?,?,?)",
                    (project.id, i, m.title, m.date_raw, m.criteria, m.status),
                )
        if parsed.risks or parsed.layout.get("risks_sheet"):
            self.conn.execute("DELETE FROM risks WHERE project_id = ?", (project.id,))
            for r in parsed.risks:
                self.conn.execute(
                    "INSERT INTO risks(project_id, no, title, probability, impact, scenario, mitigation, owner, "
                    "status) VALUES(?,?,?,?,?,?,?,?,?)",
                    (project.id, r.no, r.title, r.probability, r.impact, r.scenario, r.mitigation, r.owner,
                     r.status),
                )
        self.conn.commit()
        result.project = self.get_project(project.id)  # type: ignore[assignment]
        return result

    # ---------- вехи и риски ----------
    def list_milestones(self, project_id: int) -> list[Milestone]:
        rows = self.conn.execute("SELECT * FROM milestones WHERE project_id = ? ORDER BY sort_order", (project_id,))
        return [Milestone(id=r["id"], project_id=r["project_id"], title=r["title"], date_raw=r["date_raw"] or "",
                          criteria=r["criteria"] or "", status=r["status"] or "", sort_order=r["sort_order"])
                for r in rows]

    def list_risks(self, project_id: int) -> list[Risk]:
        rows = self.conn.execute("SELECT * FROM risks WHERE project_id = ? ORDER BY id", (project_id,))
        return [Risk(id=r["id"], project_id=r["project_id"], title=r["title"], no=r["no"] or "",
                     probability=r["probability"] or "", impact=r["impact"] or "", scenario=r["scenario"] or "",
                     mitigation=r["mitigation"] or "", owner=r["owner"] or "", status=r["status"] or "")
                for r in rows]

    # ---------- обзвон ----------
    def create_call(self, project_id: int, *, contact: str, due_at: datetime, member_id: int | None,
                    task_id: int | None = None, phone: str = "", note: str = "",
                    created_by: int | None = None) -> Call:
        cur = self.conn.execute(
            "INSERT INTO calls(project_id, task_id, member_id, contact, phone, note, due_at, created_by, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (project_id, task_id, member_id, contact, phone, note, due_at.isoformat(timespec="minutes"),
             created_by, self._now_iso()),
        )
        self.conn.commit()
        return self.get_call(cur.lastrowid)  # type: ignore[return-value]

    def get_call(self, call_id: int) -> Call | None:
        row = self.conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
        return _call_from_row(row) if row else None

    def list_calls(self, project_id: int, include_done: bool = False) -> list[Call]:
        sql = "SELECT * FROM calls WHERE project_id = ?" + ("" if include_done else " AND status = 'planned'")
        return [_call_from_row(r) for r in self.conn.execute(sql + " ORDER BY due_at", (project_id,))]

    def due_calls(self, now: datetime) -> list[Call]:
        rows = self.conn.execute("SELECT * FROM calls WHERE status = 'planned' AND notified_at IS NULL")
        return [c for c in map(_call_from_row, rows) if c.due_at <= now]

    def update_call(self, call_id: int, **fields: object) -> Call:
        allowed = {"status", "result", "notified_at", "due_at", "done_at"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown call field {key}")
            if isinstance(value, datetime):
                value = value.isoformat(timespec="minutes")
            sets.append(f"{key} = ?")
            values.append(value)
        self.conn.execute(f"UPDATE calls SET {', '.join(sets)} WHERE id = ?", (*values, call_id))
        self.conn.commit()
        return self.get_call(call_id)  # type: ignore[return-value]

    def call_checks(self, project_id: int, day: date) -> dict[str, dict]:
        rows = self.conn.execute(
            "SELECT c.*, COALESCE(m.name, NULLIF(c.actor_name, '')) AS member_name FROM call_checks c "
            "LEFT JOIN members m ON m.id = c.member_id "
            "WHERE c.project_id = ? AND c.day = ?",
            (project_id, day.isoformat()),
        )
        return {r["key"]: dict(r) for r in rows}

    def set_call_check(self, project_id: int, day: date, key: str, member_id: int | None, note: str,
                       checked: bool, actor_name: str = "") -> None:
        if checked:
            self.conn.execute(
                "INSERT INTO call_checks(project_id, day, key, member_id, note, created_at, actor_name) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(project_id, day, key) DO UPDATE SET note = excluded.note, "
                "member_id = excluded.member_id, actor_name = excluded.actor_name",
                (project_id, day.isoformat(), key, member_id if member_id and member_id > 0 else None, note,
                 self._now_iso(), actor_name),
            )
        else:
            self.conn.execute("DELETE FROM call_checks WHERE project_id = ? AND day = ? AND key = ?",
                              (project_id, day.isoformat(), key))
        self.conn.commit()

    # ---------- пространства ----------
    def create_workspace(self, name: str, icon: str = "") -> Workspace:
        cur = self.conn.execute("INSERT INTO workspaces(name, icon, created_at) VALUES(?,?,?)",
                                (name, icon, self._now_iso()))
        self.conn.commit()
        return self.get_workspace(cur.lastrowid)  # type: ignore[return-value]

    def get_workspace(self, workspace_id: int) -> Workspace | None:
        row = self.conn.execute("SELECT * FROM workspaces WHERE id = ?", (workspace_id,)).fetchone()
        return Workspace(id=row["id"], name=row["name"], icon=row["icon"] or "") if row else None

    def list_workspaces(self) -> list[Workspace]:
        rows = self.conn.execute("SELECT * FROM workspaces ORDER BY id")
        return [Workspace(id=r["id"], name=r["name"], icon=r["icon"] or "") for r in rows]

    def default_workspace(self) -> Workspace | None:
        """Сюда попадают проекты, загруженные через бота, и проекты из версий до пространств."""
        row = self.conn.execute("SELECT id FROM workspaces ORDER BY id LIMIT 1").fetchone()
        return self.get_workspace(row["id"]) if row else None

    def update_workspace(self, workspace_id: int, *, name: str, icon: str) -> Workspace:
        self.conn.execute("UPDATE workspaces SET name = ?, icon = ? WHERE id = ?", (name, icon, workspace_id))
        self.conn.commit()
        return self.get_workspace(workspace_id)  # type: ignore[return-value]

    def assign_orphan_projects(self, workspace_id: int) -> int:
        cur = self.conn.execute("UPDATE projects SET workspace_id = ? WHERE workspace_id IS NULL", (workspace_id,))
        self.conn.commit()
        return cur.rowcount

    # ---------- аккаунты ----------
    def create_user(self, *, login: str, name: str, password_hash: str = "", must_change_password: bool = False,
                    is_superadmin: bool = False, member_id: int | None = None) -> User:
        cur = self.conn.execute(
            "INSERT INTO users(login, name, password_hash, must_change_password, is_superadmin, member_id, active, "
            "created_at) VALUES(?,?,?,?,?,?,1,?)",
            (login, name, password_hash, int(must_change_password), int(is_superadmin), member_id, self._now_iso()),
        )
        self.conn.commit()
        return self.get_user(cur.lastrowid)  # type: ignore[return-value]

    def get_user(self, user_id: int) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_from_row(row) if row else None

    def user_by_login(self, login: str) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE login = ?", (login.strip().lower(),)).fetchone()
        return _user_from_row(row) if row else None

    def user_by_member(self, member_id: int) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE member_id = ?", (member_id,)).fetchone()
        return _user_from_row(row) if row else None

    def list_users(self) -> list[User]:
        return [_user_from_row(r) for r in self.conn.execute("SELECT * FROM users ORDER BY id")]

    def count_users(self) -> int:
        return self.conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

    def update_user(self, user_id: int, **fields: object) -> User:
        allowed = {"login", "name", "password_hash", "must_change_password", "is_superadmin", "member_id", "active",
                   "last_login_at"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown user field {key}")
            if key in {"must_change_password", "is_superadmin", "active"}:
                value = int(bool(value))
            sets.append(f"{key} = ?")
            values.append(value)
        if sets:
            self.conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", (*values, user_id))
            self.conn.commit()
        return self.get_user(user_id)  # type: ignore[return-value]

    def set_role(self, workspace_id: int, user_id: int, role: str | None) -> None:
        if role is None:
            self.conn.execute("DELETE FROM workspace_users WHERE workspace_id = ? AND user_id = ?",
                              (workspace_id, user_id))
        else:
            self.conn.execute(
                "INSERT INTO workspace_users(workspace_id, user_id, role) VALUES(?,?,?) "
                "ON CONFLICT(workspace_id, user_id) DO UPDATE SET role = excluded.role",
                (workspace_id, user_id, role),
            )
        self.conn.commit()

    def user_roles(self, user_id: int) -> dict[int, str]:
        rows = self.conn.execute("SELECT workspace_id, role FROM workspace_users WHERE user_id = ?", (user_id,))
        return {r["workspace_id"]: r["role"] for r in rows}

    def workspace_roles(self, workspace_id: int) -> dict[int, str]:
        rows = self.conn.execute("SELECT user_id, role FROM workspace_users WHERE workspace_id = ?", (workspace_id,))
        return {r["user_id"]: r["role"] for r in rows}

    # ---------- сессии ----------
    def create_session(self, token_hash: str, user_id: int, kind: str, expires_at: datetime) -> None:
        self.conn.execute(
            "INSERT INTO sessions(token_hash, user_id, kind, created_at, expires_at) VALUES(?,?,?,?,?)",
            (token_hash, user_id, kind, self._now_iso(), _utc_iso(expires_at)),
        )
        self.conn.commit()

    def get_session(self, token_hash: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM sessions WHERE token_hash = ? AND expires_at > ?",
                                (token_hash, self._now_iso())).fetchone()
        return dict(row) if row else None

    def extend_session(self, token_hash: str, expires_at: datetime) -> None:
        self.conn.execute("UPDATE sessions SET expires_at = ? WHERE token_hash = ?", (_utc_iso(expires_at), token_hash))
        self.conn.commit()

    def delete_session(self, token_hash: str) -> None:
        self.conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        self.conn.commit()

    def delete_user_sessions(self, user_id: int, kind: str | None = None, keep: str | None = None) -> None:
        sql, args = "DELETE FROM sessions WHERE user_id = ?", [user_id]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if keep:
            sql += " AND token_hash != ?"
            args.append(keep)
        self.conn.execute(sql, args)
        self.conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (self._now_iso(),))
        self.conn.commit()

    # ---------- страницы ----------
    _PAGE_META = ("id, workspace_id, parent_id, title, icon, sort_order, version, archived, created_by, updated_by, "
                  "created_at, updated_at")

    def create_page(self, workspace_id: int, *, title: str = "", parent_id: int | None = None, icon: str = "",
                    content: list | None = None, text_index: str = "", author: str = "") -> Page:
        now = self._now_iso()
        order = self.conn.execute("SELECT COALESCE(MAX(sort_order), 0) AS m FROM pages WHERE workspace_id = ?",
                                  (workspace_id,)).fetchone()["m"] + 1
        cur = self.conn.execute(
            "INSERT INTO pages(workspace_id, parent_id, title, icon, content_json, text_index, sort_order, created_by, "
            "updated_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (workspace_id, parent_id, title, icon, dumps(content or []), text_index, order, author, author, now, now),
        )
        self.conn.commit()
        return self.get_page(cur.lastrowid)  # type: ignore[return-value]

    def get_page(self, page_id: int) -> Page | None:
        row = self.conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        return _page_from_row(row) if row else None

    def list_pages(self, workspace_id: int, archived: bool = False) -> list[Page]:
        rows = self.conn.execute(f"SELECT {self._PAGE_META} FROM pages WHERE workspace_id = ? AND archived = ? "
                                 "ORDER BY sort_order, id", (workspace_id, int(archived)))
        return [_page_from_row(r) for r in rows]

    def update_page(self, page_id: int, *, author: str, **fields: object) -> Page:
        allowed = {"title", "icon", "content", "text_index", "parent_id", "sort_order"}
        sets, values = [], []
        for key, value in fields.items():
            if key not in allowed:
                raise ValueError(f"unknown page field {key}")
            if key == "content":
                key, value = "content_json", dumps(value)
            sets.append(f"{key} = ?")
            values.append(value)
        sets += ["version = version + 1", "updated_by = ?", "updated_at = ?"]
        values += [author, self._now_iso()]
        self.conn.execute(f"UPDATE pages SET {', '.join(sets)} WHERE id = ?", (*values, page_id))
        self.conn.commit()
        return self.get_page(page_id)  # type: ignore[return-value]

    def page_subtree(self, page_id: int) -> list[int]:
        rows = self.conn.execute(
            "WITH RECURSIVE sub(id) AS (SELECT ? UNION SELECT p.id FROM pages p JOIN sub ON p.parent_id = sub.id) "
            "SELECT id FROM sub", (page_id,))
        return [r["id"] for r in rows]

    def set_pages_archived(self, page_ids: list[int], archived: bool) -> None:
        marks = ",".join("?" * len(page_ids))
        self.conn.execute(f"UPDATE pages SET archived = ?, archived_at = ? WHERE id IN ({marks})",
                          (int(archived), self._now_iso() if archived else None, *page_ids))
        self.conn.commit()

    def delete_pages(self, page_ids: list[int]) -> None:
        marks = ",".join("?" * len(page_ids))
        self.conn.execute(f"DELETE FROM pages WHERE id IN ({marks})", page_ids)
        self.conn.commit()

    def search_pages(self, workspace_id: int, query: str, limit: int = 20) -> list[Page]:
        # SQLite lower() не знает кириллицу, поэтому text_index хранится уже нормализованным (заголовок + текст).
        like = "%" + norm_text(query).replace("\\", "").replace("%", "").replace("_", "") + "%"
        rows = self.conn.execute(
            f"SELECT {self._PAGE_META} FROM pages WHERE workspace_id = ? AND archived = 0 "
            "AND text_index LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (workspace_id, like, limit))
        return [_page_from_row(r) for r in rows]
