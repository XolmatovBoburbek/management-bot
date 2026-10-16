import io

import pytest

from app.backup import dump, restore
from app.db import Database
from app.service import Service
from tests.conftest import member


def counts(db: Database) -> dict[str, int]:
    tables = ["projects", "tasks", "members", "users", "workspaces", "pages", "task_events", "sessions"]
    return {t: db.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


async def test_dump_and_restore_keep_everything_but_sessions(tmp_path, config, notifier, clock, workbook):
    db = Database(tmp_path / "bot.sqlite3")
    service = Service(db, config, notifier, clock=clock)
    service.seed_team(config.team_file)
    service.accounts.bootstrap()
    await service.import_file(workbook.read_bytes(), "erp.xlsx", member(service, "s_maxhan"))
    task = db.list_tasks(db.list_projects()[0].id)[0]
    await service.add_comment(task, "Ждём макет", member(service, "s_maxhan"))
    workspace = db.list_workspaces()[0]
    db.create_page(workspace.id, title="Регламент «монтаж»", content=[{"id": "a1", "type": "p", "rich": [{"t": "Текст"}]}])
    user = db.list_users()[0]
    service.accounts.start_session(user, "telegram")
    before = counts(db)
    assert before["sessions"] == 1

    out = io.StringIO()
    dump(tmp_path / "bot.sqlite3", out)
    sql = out.getvalue()
    assert "CREATE TABLE" in sql and "Ждём макет" in sql

    dump_file = tmp_path / "db.sql"
    dump_file.write_text(sql, encoding="utf-8")
    target = tmp_path / "restored" / "bot.sqlite3"
    target.parent.mkdir()
    assert restore(target, dump_file) is None
    restored = Database(target)
    after = counts(restored)
    assert after == {**before, "sessions": 0}
    assert restored.list_pages(workspace.id)[0].title == "Регламент «монтаж»"
    assert restored.get_user(user.id).login == user.login

    # поверх существующей базы — только с --force, прежняя сохраняется рядом
    restored.conn.close()
    with pytest.raises(FileExistsError):
        restore(target, dump_file)
    previous = restore(target, dump_file, force=True)
    assert previous is not None and previous.exists()


def test_restore_rejects_non_dump(tmp_path):
    bad = tmp_path / "x.sql"
    bad.write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError):
        restore(tmp_path / "bot.sqlite3", bad)
