"""Управление аккаунтами кабинета из консоли сервера (если забыли пароль администратора).

    python -m app.admin list
    python -m app.admin set-password ЛОГИН [ПАРОЛЬ]      # без пароля — сгенерирует
    python -m app.admin create ЛОГИН "Имя" [ПАРОЛЬ]       # главный администратор

В Docker:  docker compose exec bot python -m app.admin set-password rrkaier
"""
from __future__ import annotations

import sys
from datetime import datetime

from app.accounts import (
    LOGIN_RE,
    Accounts,
    generate_password,
    hash_password,
    normalize_login,
    validate_password,
)
from app.config import load_config
from app.db import Database


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in {"list", "set-password", "create"}:
        print(__doc__)
        return 1
    config = load_config()
    db = Database(config.db_path, config.tz)
    Accounts(db, lambda: datetime.now(config.tz)).bootstrap()
    command, args = argv[0], argv[1:]
    if command == "list":
        for user in db.list_users():
            flags = ["главный админ" if user.is_superadmin else "", "" if user.active else "отключён",
                     "пароль задан" if user.has_password else "только Telegram"]
            print(f"{user.login:<24} {user.name:<24} {', '.join(f for f in flags if f)}")
        return 0
    if not args:
        print(__doc__)
        return 1
    login = normalize_login(args[0])
    if command == "set-password":
        user = db.user_by_login(login)
        if not user:
            print(f"Пользователь «{login}» не найден")
            return 1
        password = validate_password(args[1]) if len(args) > 1 else generate_password()
        db.update_user(user.id, password_hash=hash_password(password), must_change_password=len(args) < 2,
                       active=True)
        db.delete_user_sessions(user.id, kind="password")
        print(f"Пароль для {login}: {password}")
        return 0
    if len(args) < 2:
        print(__doc__)
        return 1
    if not LOGIN_RE.fullmatch(login) or db.user_by_login(login):
        print(f"Логин «{login}» занят или недопустим (3–32 символа: латиница, цифры, . _ -)")
        return 1
    password = validate_password(args[2]) if len(args) > 2 else generate_password()
    db.create_user(login=login, name=args[1], password_hash=hash_password(password), must_change_password=True,
                   is_superadmin=True)
    print(f"Создан главный администратор {login}, пароль: {password}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
