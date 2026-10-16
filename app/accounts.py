"""Аккаунты веб-кабинета: пароли, сессии, вход через Telegram и роли в пространствах.

Аккаунты создаёт администратор. Аккаунт можно связать с участником команды из бота —
тогда из Telegram человек входит без пароля, а в браузере — по логину и паролю.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Callable

from app.db import Database
from app.models import ADMIN, MEMBER, ROLES, VIEWER, AccessError, Member, User, Workspace, norm_text

SESSION_DAYS = 30
SESSION_EXTEND_AFTER = timedelta(days=1)
MIN_PASSWORD = 8
LOGIN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
# Вход по паролю: не больше 5 ошибок за 15 минут на логин и 30 — с одного адреса.
LOGIN_WINDOW = 15 * 60
MAX_LOGIN_FAILURES = 5
MAX_ADDRESS_FAILURES = 30
DEFAULT_WORKSPACE_NAME = "IAC Media"

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya", "o", "q", "g", "h"]))


class AuthError(Exception):
    """Неверный логин/пароль или недействительная сессия."""


class ThrottledError(Exception):
    """Слишком много неудачных попыток входа."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = _unb64(digest)
        actual = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def generate_password(length: int = 12) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_login(value: object) -> str:
    return str(value or "").strip().lstrip("@").lower()


def login_from_name(name: str) -> str:
    text = "".join(TRANSLIT.get(ch, ch) for ch in norm_text(name))
    text = re.sub(r"[^a-z0-9]+", ".", text).strip(".")
    return text[:32] or "user"


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Пароль должен быть не короче {MIN_PASSWORD} символов")
    if len(password) > 200:
        raise ValueError("Слишком длинный пароль")
    return password


class LoginThrottle:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        items = self._failures[key]
        cutoff = self._clock() - LOGIN_WINDOW
        while items and items[0] < cutoff:
            items.popleft()
        return items

    def check(self, login: str, address: str) -> None:
        if (len(self._recent("login:" + login)) >= MAX_LOGIN_FAILURES
                or len(self._recent("addr:" + address)) >= MAX_ADDRESS_FAILURES):
            raise ThrottledError("Слишком много попыток входа. Подождите 15 минут или попросите администратора "
                                 "сбросить пароль.")

    def fail(self, login: str, address: str) -> None:
        now = self._clock()
        self._failures["login:" + login].append(now)
        self._failures["addr:" + address].append(now)

    def reset(self, login: str) -> None:
        self._failures.pop("login:" + login, None)


class Accounts:
    def __init__(self, db: Database, now: Callable[[], datetime]):
        self.db = db
        self.now = now
        self.throttle = LoginThrottle()

    # ---------- первый запуск ----------
    def bootstrap(self, workspace_name: str = DEFAULT_WORKSPACE_NAME) -> int:
        """Основное пространство и аккаунты для команды при первом запуске новой версии.

        Проекты без пространства попадают в основное. Если аккаунтов ещё нет, каждый активный участник
        команды получает аккаунт без пароля (вход из Telegram) с ролью по его правам в боте.
        Дальше аккаунтами управляет администратор. Возвращает число созданных аккаунтов.
        """
        workspace = self.db.default_workspace() or self.db.create_workspace(workspace_name, "🏢")
        self.db.assign_orphan_projects(workspace.id)
        if self.db.count_users():
            return 0
        created = 0
        for member in self.db.list_members():
            login = self._free_login(member.username or login_from_name(member.name))
            user = self.db.create_user(login=login, name=member.name, is_superadmin=member.is_admin,
                                       member_id=member.id)
            role = ADMIN if member.is_admin else VIEWER if member.is_observer else MEMBER
            self.db.set_role(workspace.id, user.id, role)
            created += 1
        return created

    def _free_login(self, base: str) -> str:
        base = normalize_login(base)
        base = re.sub(r"[^a-z0-9._-]+", ".", base).strip(".-_")[:28] or "user"
        if len(base) < 3:
            base = (base + "user")[:28]
        login, n = base, 1
        while self.db.user_by_login(login):
            n += 1
            login = f"{base}{n}"
        return login

    # ---------- сессии ----------
    def start_session(self, user: User, kind: str) -> str:
        token = secrets.token_urlsafe(32)
        self.db.create_session(token_hash(token), user.id, kind, self.now() + timedelta(days=SESSION_DAYS))
        self.db.update_user(user.id, last_login_at=self.now().isoformat(timespec="seconds"))
        return token

    def session(self, token: str) -> tuple[User, dict] | None:
        if not token:
            return None
        hashed = token_hash(token)
        session = self.db.get_session(hashed)
        if not session:
            return None
        user = self.db.get_user(session["user_id"])
        if not user or not user.active:
            return None
        expires = datetime.fromisoformat(session["expires_at"])
        renewed = self.now() + timedelta(days=SESSION_DAYS)
        if renewed - expires > SESSION_EXTEND_AFTER:
            self.db.extend_session(hashed, renewed)
        return user, session

    def logout(self, token: str) -> None:
        self.db.delete_session(token_hash(token))

    def login(self, login: str, password: str, address: str) -> tuple[User, str]:
        login = normalize_login(login)
        self.throttle.check(login, address)
        user = self.db.user_by_login(login)
        if not user or not user.active or not user.has_password or not verify_password(password, user.password_hash):
            self.throttle.fail(login, address)
            raise AuthError("Неверный логин или пароль")
        self.throttle.reset(login)
        return user, self.start_session(user, "password")

    def telegram_login(self, member: Member | None) -> tuple[User, str]:
        user = self.db.user_by_member(member.id) if member else None
        if not user or not user.active:
            raise AuthError("Для вашего Telegram нет доступа к кабинету. Войдите по логину и паролю "
                            "или попросите администратора создать вам аккаунт.")
        return user, self.start_session(user, "telegram")

    def change_password(self, user: User, current: str, new: str, current_token: str) -> User:
        if user.has_password and not verify_password(current, user.password_hash):
            raise ValueError("Текущий пароль указан неверно")
        validate_password(new)
        if user.has_password and verify_password(new, user.password_hash):
            raise ValueError("Новый пароль совпадает со старым")
        updated = self.db.update_user(user.id, password_hash=hash_password(new), must_change_password=False)
        self.db.delete_user_sessions(user.id, keep=token_hash(current_token))
        return updated

    # ---------- права ----------
    def roles(self, user: User) -> dict[int, str]:
        if user.is_superadmin:
            return {w.id: ADMIN for w in self.db.list_workspaces()}
        return self.db.user_roles(user.id)

    def role(self, user: User, workspace_id: int | None) -> str | None:
        if workspace_id is None:
            return None
        if user.is_superadmin:
            return ADMIN if self.db.get_workspace(workspace_id) else None
        return self.db.user_roles(user.id).get(workspace_id)

    def workspaces(self, user: User) -> list[tuple[Workspace, str]]:
        roles = self.roles(user)
        return [(w, roles[w.id]) for w in self.db.list_workspaces() if w.id in roles]

    def actor(self, user: User, role: str | None) -> Member:
        """Участник, от имени которого веб-кабинет вызывает сервис: права — по роли в пространстве."""
        member = self.db.get_member(user.member_id) if user.member_id else None
        if member and member.active:
            return replace(member, is_admin=role == ADMIN, is_observer=role == VIEWER)
        # Аккаунт без связи с командой: отрицательный id не совпадает ни с одним участником.
        return Member(id=-user.id, name=user.name, is_admin=role == ADMIN, is_observer=role == VIEWER)

    # ---------- управление (суперадмин) ----------
    def _require_superadmin(self, actor: User) -> None:
        if not actor.is_superadmin:
            raise AccessError("Управлять пользователями и пространствами может только главный администратор")

    def _check_member_link(self, member_id: object, user_id: int | None) -> int | None:
        if not member_id:
            return None
        member = self.db.get_member(int(member_id))  # type: ignore[arg-type]
        if not member:
            raise ValueError("Участник команды не найден")
        linked = self.db.user_by_member(member.id)
        if linked and linked.id != user_id:
            raise ValueError(f"Участник {member.name} уже связан с аккаунтом «{linked.login}»")
        return member.id

    def _apply_roles(self, user_id: int, roles: object) -> None:
        if not isinstance(roles, dict):
            return
        for wid, role in roles.items():
            workspace = self.db.get_workspace(int(wid))
            if not workspace:
                raise ValueError("Пространство не найдено")
            if role not in (*ROLES, None, ""):
                raise ValueError("Неизвестная роль")
            self.db.set_role(workspace.id, user_id, role or None)

    def create_user(self, actor: User, data: dict) -> User:
        self._require_superadmin(actor)
        login = normalize_login(data.get("login"))
        if not LOGIN_RE.fullmatch(login):
            raise ValueError("Логин: 3–32 символа, латинские буквы, цифры, точка, дефис или подчёркивание")
        if self.db.user_by_login(login):
            raise ValueError(f"Логин «{login}» уже занят")
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Укажите имя")
        member_id = self._check_member_link(data.get("member_id"), None)
        password = str(data.get("password") or "")
        if not password and not member_id:
            raise ValueError("Задайте пароль — без него и без связи с Telegram человек не сможет войти")
        user = self.db.create_user(
            login=login, name=name, password_hash=hash_password(validate_password(password)) if password else "",
            must_change_password=bool(password), is_superadmin=bool(data.get("is_superadmin")), member_id=member_id)
        self._apply_roles(user.id, data.get("roles"))
        return user

    def update_user(self, actor: User, user_id: int, data: dict) -> User:
        self._require_superadmin(actor)
        user = self.db.get_user(user_id)
        if not user:
            raise LookupError("Пользователь не найден")
        fields: dict = {}
        if "name" in data:
            fields["name"] = str(data["name"]).strip() or user.name
        if "login" in data:
            login = normalize_login(data["login"])
            if not LOGIN_RE.fullmatch(login):
                raise ValueError("Логин: 3–32 символа, латинские буквы, цифры, точка, дефис или подчёркивание")
            other = self.db.user_by_login(login)
            if other and other.id != user.id:
                raise ValueError(f"Логин «{login}» уже занят")
            fields["login"] = login
        if "member_id" in data:
            fields["member_id"] = self._check_member_link(data["member_id"], user.id)
        for flag in ("is_superadmin", "active"):
            if flag in data:
                value = bool(data[flag])
                if user.id == actor.id and not value:
                    raise ValueError("Нельзя снять права главного администратора или отключить самого себя")
                fields[flag] = value
        if (user.is_superadmin and user.active and (fields.get("is_superadmin") is False or fields.get("active") is False)
                and self._active_superadmins() <= 1):
            raise ValueError("Должен остаться хотя бы один главный администратор")
        updated = self.db.update_user(user.id, **fields)
        self._apply_roles(user.id, data.get("roles"))
        if not updated.active:
            self.db.delete_user_sessions(user.id)
        return updated

    def _active_superadmins(self) -> int:
        return sum(1 for u in self.db.list_users() if u.is_superadmin and u.active)

    def reset_password(self, actor: User, user_id: int, password: str = "") -> tuple[User, str]:
        self._require_superadmin(actor)
        user = self.db.get_user(user_id)
        if not user:
            raise LookupError("Пользователь не найден")
        password = validate_password(password) if password else generate_password()
        updated = self.db.update_user(user.id, password_hash=hash_password(password),
                                      must_change_password=user.id != actor.id)
        self.db.delete_user_sessions(user.id, kind="password")
        return updated, password

    def create_workspace(self, actor: User, name: str, icon: str = "") -> Workspace:
        self._require_superadmin(actor)
        name = name.strip()
        if not name:
            raise ValueError("Укажите название пространства")
        return self.db.create_workspace(name[:80], icon.strip()[:8])

    def update_workspace(self, actor: User, workspace_id: int, name: str, icon: str) -> Workspace:
        self._require_superadmin(actor)
        workspace = self.db.get_workspace(workspace_id)
        if not workspace:
            raise LookupError("Пространство не найдено")
        return self.db.update_workspace(workspace.id, name=name.strip()[:80] or workspace.name, icon=icon.strip()[:8])
