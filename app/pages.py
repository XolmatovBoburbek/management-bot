"""Страницы-документы рабочего пространства (как в Notion): дерево страниц и блоки внутри.

Контент хранится не HTML, а списком блоков с размеченным текстом — так в нём не может оказаться
исполняемого кода, даже если его прислать в обход интерфейса.
"""
from __future__ import annotations

import re
import secrets

from app.db import Database
from app.models import ADMIN, MEMBER, AccessError, Page, norm_text

TEXT_TYPES = {"p", "h1", "h2", "h3", "bullet", "number", "todo", "quote", "callout", "toggle"}
BLOCK_TYPES = TEXT_TYPES | {"code", "divider", "page", "tasks"}
TASK_VIEWS = {"board", "table", "calendar", "list"}
MARKS = ("b", "i", "u", "s", "c")
MAX_BLOCKS = 3000
MAX_BLOCK_TEXT = 20000
MAX_INDENT = 8
MAX_TITLE = 300
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
HREF_RE = re.compile(r"^(https?://|mailto:|tel:)[^\s<>\"']+$", re.IGNORECASE)


class ConflictError(Exception):
    """Страницу уже изменили: клиент прислал устаревшую версию."""

    def __init__(self, page: Page):
        super().__init__("Страницу только что изменил другой пользователь")
        self.page = page


def clean_href(value: object) -> str:
    href = str(value or "").strip()
    if href and not re.match(r"^[a-z][a-z0-9+.-]*:", href, re.IGNORECASE) and "." in href.split("/")[0]:
        href = "https://" + href
    return href[:2000] if HREF_RE.match(href) else ""


def clean_rich(value: object) -> list[dict]:
    if isinstance(value, str):
        return [{"t": value[:MAX_BLOCK_TEXT]}] if value else []
    if not isinstance(value, list):
        return []
    result: list[dict] = []
    budget = MAX_BLOCK_TEXT
    for seg in value:
        if not isinstance(seg, dict) or not isinstance(seg.get("t"), str) or not seg["t"] or budget <= 0:
            continue
        text = seg["t"][:budget]
        budget -= len(text)
        item: dict = {"t": text}
        for mark in MARKS:
            if seg.get(mark):
                item[mark] = 1
        href = clean_href(seg.get("href"))
        if href:
            item["href"] = href
        # соседние куски с одинаковым оформлением склеиваем
        if result and {k: v for k, v in result[-1].items() if k != "t"} == {k: v for k, v in item.items() if k != "t"}:
            result[-1]["t"] += text
        else:
            result.append(item)
    return result


def clean_blocks(blocks: object) -> list[dict]:
    if not isinstance(blocks, list):
        raise ValueError("Содержимое страницы должно быть списком блоков")
    if len(blocks) > MAX_BLOCKS:
        raise ValueError(f"На странице слишком много блоков (больше {MAX_BLOCKS}) — разбейте её на подстраницы")
    seen: set[str] = set()
    result: list[dict] = []
    for raw in blocks:
        if not isinstance(raw, dict) or raw.get("type") not in BLOCK_TYPES:
            continue
        block_id = str(raw.get("id") or "")
        if not ID_RE.fullmatch(block_id) or block_id in seen:
            block_id = secrets.token_hex(6)
        seen.add(block_id)
        kind = raw["type"]
        block: dict = {"id": block_id, "type": kind}
        try:
            indent = int(raw.get("indent") or 0)
        except (TypeError, ValueError):
            indent = 0
        if indent:
            block["indent"] = max(0, min(MAX_INDENT, indent))
        if kind in TEXT_TYPES:
            block["rich"] = clean_rich(raw.get("rich"))
        if kind == "todo":
            block["checked"] = bool(raw.get("checked"))
        if kind == "toggle":
            block["open"] = bool(raw.get("open"))
        if kind == "callout":
            block["icon"] = str(raw.get("icon") or "💡")[:8]
        if kind == "code":
            block["text"] = str(raw.get("text") or "")[:MAX_BLOCK_TEXT]
        if kind == "page":
            try:
                block["page_id"] = int(raw.get("page_id"))
            except (TypeError, ValueError):
                continue
        if kind == "tasks":  # доска задач проекта прямо на странице
            try:
                block["project_id"] = int(raw.get("project_id"))
            except (TypeError, ValueError):
                continue
            block["view"] = raw.get("view") if raw.get("view") in TASK_VIEWS else "board"
        result.append(block)
    return result


def plain_text(blocks: list[dict]) -> str:
    parts = []
    for block in blocks:
        if "rich" in block:
            parts.append("".join(seg["t"] for seg in block["rich"]))
        elif block.get("text"):
            parts.append(block["text"])
    return "\n".join(parts)


def _index(title: str, blocks: list[dict]) -> str:
    return norm_text(f"{title}\n{plain_text(blocks)}")[:200000]


class Pages:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _require_editor(role: str | None) -> None:
        if role not in (ADMIN, MEMBER):
            raise AccessError("У вас доступ только для просмотра")

    def get(self, page_id: int) -> Page:
        page = self.db.get_page(page_id)
        if not page:
            raise LookupError("Страница не найдена")
        return page

    def tree(self, workspace_id: int) -> list[Page]:
        return self.db.list_pages(workspace_id)

    def trash(self, workspace_id: int) -> list[Page]:
        archived = self.db.list_pages(workspace_id, archived=True)
        ids = {p.id for p in archived}
        return [p for p in archived if p.parent_id not in ids]

    def _valid_parent(self, workspace_id: int, parent_id: object, page_id: int | None = None) -> int | None:
        if parent_id in (None, "", 0):
            return None
        parent = self.db.get_page(int(parent_id))  # type: ignore[arg-type]
        if not parent or parent.workspace_id != workspace_id or parent.archived:
            raise ValueError("Родительская страница не найдена")
        if page_id is not None and parent.id in self.db.page_subtree(page_id):
            raise ValueError("Нельзя переместить страницу внутрь самой себя")
        return parent.id

    def _linked_pages_only(self, workspace_id: int, blocks: list[dict]) -> list[dict]:
        """Ссылки на страницы и доски проектов — только внутри своего пространства."""
        result = []
        for block in blocks:
            if block["type"] == "page":
                target = self.db.get_page(block["page_id"])
                if not target or target.workspace_id != workspace_id:
                    continue
            if block["type"] == "tasks":
                project = self.db.get_project(block["project_id"])
                if not project or project.workspace_id != workspace_id:
                    continue
            result.append(block)
        return result

    def create(self, workspace_id: int, role: str | None, author: str, data: dict) -> Page:
        self._require_editor(role)
        title = str(data.get("title") or "").strip()[:MAX_TITLE]
        parent_id = self._valid_parent(workspace_id, data.get("parent_id"))
        content = self._linked_pages_only(workspace_id, clean_blocks(data.get("content") or []))
        return self.db.create_page(workspace_id, title=title, parent_id=parent_id,
                                   icon=str(data.get("icon") or "")[:8], content=content,
                                   text_index=_index(title, content), author=author)

    def save(self, page: Page, role: str | None, author: str, data: dict) -> Page:
        self._require_editor(role)
        if page.archived:
            raise ValueError("Страница в корзине — сначала восстановите её")
        version = data.get("version")
        if version is not None and int(version) != page.version:
            raise ConflictError(page)
        fields: dict = {}
        title, content = page.title, page.content
        if "title" in data:
            title = fields["title"] = str(data["title"] or "").strip()[:MAX_TITLE]
        if "icon" in data:
            fields["icon"] = str(data["icon"] or "")[:8]
        if "content" in data:
            content = fields["content"] = self._linked_pages_only(page.workspace_id, clean_blocks(data["content"]))
        if "title" in fields or "content" in fields:
            fields["text_index"] = _index(title, content)
        if not fields:
            return page
        return self.db.update_page(page.id, author=author, **fields)

    def move(self, page: Page, role: str | None, author: str, parent_id: object, index: object = None) -> Page:
        self._require_editor(role)
        new_parent = self._valid_parent(page.workspace_id, parent_id, page.id)
        siblings = [p.id for p in self.db.list_pages(page.workspace_id) if p.parent_id == new_parent and p.id != page.id]
        position = len(siblings) if index is None else max(0, min(len(siblings), int(index)))  # type: ignore[arg-type]
        siblings.insert(position, page.id)
        updated = page
        if new_parent != page.parent_id:
            updated = self.db.update_page(page.id, author=author, parent_id=new_parent)
        for order, pid in enumerate(siblings, start=1):
            self.db.conn.execute("UPDATE pages SET sort_order = ? WHERE id = ?", (order, pid))
        self.db.conn.commit()
        return self.db.get_page(updated.id)  # type: ignore[return-value]

    def archive(self, page: Page, role: str | None) -> None:
        self._require_editor(role)
        self.db.set_pages_archived(self.db.page_subtree(page.id), True)

    def restore(self, page: Page, role: str | None, author: str) -> Page:
        self._require_editor(role)
        self.db.set_pages_archived(self.db.page_subtree(page.id), False)
        parent = self.db.get_page(page.parent_id) if page.parent_id else None
        if page.parent_id and (not parent or parent.archived):
            return self.db.update_page(page.id, author=author, parent_id=None)
        return self.get(page.id)

    def purge(self, page: Page, role: str | None) -> None:
        if role != ADMIN:
            raise AccessError("Удалить навсегда может только администратор пространства")
        if not page.archived:
            raise ValueError("Сначала переместите страницу в корзину")
        self.db.delete_pages(self.db.page_subtree(page.id))

    def search(self, workspace_id: int, query: str) -> list[Page]:
        return self.db.search_pages(workspace_id, query) if query.strip() else []
