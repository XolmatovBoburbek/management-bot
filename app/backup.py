"""Резервная копия базы: SQL-дамп для ежедневного бэкапа в приватный репозиторий GitHub.

    python -m app.backup dump [ФАЙЛ]                 # без файла — в stdout
    python -m app.backup restore ФАЙЛ.sql [--force]  # восстановить базу из дампа (бот должен быть остановлен)

В Docker:  docker compose exec -T bot python -m app.backup dump > db.sql
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

from app.config import load_config

# Активные входы не сохраняем: после восстановления люди просто войдут заново.
SKIP_DATA = ("sessions",)


def dump(db_path: Path, out: TextIO) -> None:
    """Согласованный снимок через backup API (не мешает работающему боту) и текстовый дамп."""
    if not db_path.exists():
        raise FileNotFoundError(f"База не найдена: {db_path}")
    source = sqlite3.connect(db_path)
    snapshot = sqlite3.connect(":memory:")
    try:
        source.backup(snapshot)
    finally:
        source.close()
    tables = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for table in SKIP_DATA:
        if table in tables:
            snapshot.execute(f"DELETE FROM {table}")
    for line in snapshot.iterdump():
        out.write(line + "\n")
    snapshot.close()


def restore(db_path: Path, dump_path: Path, force: bool = False) -> Path | None:
    """Собирает новую базу из дампа. Прежнюю (если есть и задан force) переименовывает, а не удаляет."""
    script = dump_path.read_text(encoding="utf-8")
    if "CREATE TABLE" not in script:
        raise ValueError(f"{dump_path} не похож на дамп базы")
    if db_path.exists() and not force:
        raise FileExistsError(f"База уже есть: {db_path}. Добавьте --force — прежняя будет сохранена рядом")
    tmp = db_path.with_name(db_path.name + ".restoring")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(script)
    finally:
        conn.close()
    previous = None
    if db_path.exists():
        previous = db_path.with_name(f"{db_path.name}.before-restore-{datetime.now():%Y%m%d-%H%M%S}")
        os.replace(db_path, previous)
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    os.replace(tmp, db_path)
    return previous


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"dump", "restore"}:
        print(__doc__)
        return 1
    db_path = load_config().db_path
    if argv[0] == "dump":
        if len(argv) > 1 and argv[1] != "-":
            with open(argv[1], "w", encoding="utf-8") as out:
                dump(db_path, out)
        else:
            dump(db_path, sys.stdout)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 1
    previous = restore(db_path, Path(argv[1]), force="--force" in argv[2:])
    print(f"База восстановлена: {db_path}" + (f" (прежняя сохранена в {previous})" if previous else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
