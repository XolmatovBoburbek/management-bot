"""Обработчики бота на имитации Telegram API (без сети)."""
from datetime import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetFile,
    SendDocument,
    SendMessage,
    SetChatMenuButton,
)
from aiogram.types import CallbackQuery, Chat, Document, File, Message, Update, User

from app.bot import build_router
from app.models import DONE
from tests.conftest import TOKEN, member


class MockSession(BaseSession):
    def __init__(self, file_bytes: bytes = b""):
        super().__init__()
        self.requests = []
        self.file_bytes = file_bytes

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, (SendMessage, SendDocument, EditMessageText)):
            chat_id = getattr(method, "chat_id", None) or 1
            return Message(message_id=len(self.requests), date=datetime.now(), chat=Chat(id=chat_id, type="private"),
                           text=getattr(method, "text", None)).as_(bot)
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path="docs/erp.xlsx")
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield self.file_bytes

    async def close(self):
        pass

    def sent(self, kind=SendMessage):
        return [r for r in self.requests if isinstance(r, kind)]


@pytest.fixture
def env(service, workbook):
    session = MockSession(workbook.read_bytes())
    bot = Bot(TOKEN, session=session, default=None)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_router(service))
    return bot, dp, session


_ids = iter(range(1, 10_000))


def user(uid, username):
    return User(id=uid, is_bot=False, first_name=username, username=username)


def message(uid, username, text=None, chat_type="private", chat_id=None, **kw):
    return Message(message_id=next(_ids), date=datetime.now(), chat=Chat(id=chat_id or uid, type=chat_type),
                   from_user=user(uid, username), text=text, **kw)


async def feed(env, **kw):
    bot, dp, _ = env
    await dp.feed_update(bot, Update(update_id=next(_ids), **kw))


async def test_start_known_and_unknown_user(env, service, notifier):
    _, _, session = env
    await feed(env, message=message(4242, "asl_offf", "/start"))
    text = session.sent()[-1].text
    assert "Асл, вы подключены" in text
    assert session.sent(SetChatMenuButton)
    assert service.db.member_by_telegram(4242).name == "Асл"
    await feed(env, message=message(5555, "random_guy", "/start"))
    assert "нет в списке команды" in session.sent()[-1].text
    assert any("не из команды" in m for m in notifier.to(102))


async def test_admin_uploads_excel_and_member_marks_done(env, service, notifier):
    _, _, session = env
    doc = Document(file_id="f1", file_unique_id="u1", file_name="ERP.xlsx")
    await feed(env, message=message(106, "gflwwc", document=doc))
    assert "только администраторам" in session.sent()[-1].text
    await feed(env, message=message(102, "s_maxhan", document=doc))
    report = [r.text for r in session.requests if isinstance(r, EditMessageText)][-1]
    assert "Тестовое открытие" in report and "Задач: 5" in report

    project = service.default_project()
    kv = next(t for t in service.db.list_tasks(project.id) if t.title.startswith("Key Visual"))
    msg = message(106, "gflwwc", "📌 Вам назначена задача")
    cb = CallbackQuery(id="1", from_user=user(106, "gflwwc"), chat_instance="c", data=f"t:done:{kv.id}", message=msg)
    await feed(env, callback_query=cb)
    assert service.get_task(kv.id).status == DONE
    edit = [r for r in session.requests if isinstance(r, EditMessageText)][-1]
    assert "выполнено" in edit.text and session.sent(AnswerCallbackQuery)

    # чужой человек не может закрыть задачу
    press = next(t for t in service.db.list_tasks(project.id) if t.title == "Пресс-волл")
    cb = CallbackQuery(id="2", from_user=user(103, "raxmanov7777"), chat_instance="c", data=f"t:done:{press.id}",
                       message=message(103, "raxmanov7777", "x"))
    await feed(env, callback_query=cb)
    assert session.sent(AnswerCallbackQuery)[-1].show_alert
    assert service.get_task(press.id).status != DONE


async def test_problem_flow_via_buttons(env, service, notifier, workbook):
    _, _, session = env
    await service.import_file(workbook.read_bytes(), "erp.xlsx", member(service, "s_maxhan"))
    project = service.default_project()
    press = next(t for t in service.db.list_tasks(project.id) if t.title == "Пресс-волл")
    cb = CallbackQuery(id="3", from_user=user(107, "msnuzz"), chat_instance="c", data=f"t:prob:{press.id}",
                       message=message(107, "msnuzz", "Выполнили?"))
    await feed(env, callback_query=cb)
    assert "Опишите одним сообщением" in session.sent()[-1].text
    notifier.messages.clear()
    await feed(env, message=message(107, "msnuzz", "Типография не отвечает"))
    assert "Передал руководителю" in session.sent()[-1].text
    assert service.get_task(press.id).blocked
    assert any("Типография не отвечает" in m for m in notifier.to(102))


async def test_group_commands(env, service, notifier, workbook):
    _, _, session = env
    await service.import_file(workbook.read_bytes(), "erp.xlsx", member(service, "s_maxhan"))
    await feed(env, message=message(106, "gflwwc", "/bind_group", chat_type="supergroup", chat_id=-100777))
    assert "только администраторам" in session.sent()[-1].text
    await feed(env, message=message(102, "s_maxhan", "/bind_group", chat_type="supergroup", chat_id=-100777))
    assert service.settings()["group_chat_id"] == "-100777"
    await feed(env, message=message(102, "s_maxhan", "/add @gflwwc 02.10 Макет флагов", chat_type="supergroup",
                                    chat_id=-100777))
    reply = session.sent()[-1]
    assert "Задача добавлена" in reply.text and "Макет флагов" in reply.text
    # в группе нельзя web_app-кнопки
    assert all(b.web_app is None for row in reply.reply_markup.inline_keyboard for b in row)
    await feed(env, message=message(106, "gflwwc", "/my", chat_type="supergroup", chat_id=-100777))
    assert "в личку" in session.sent()[-1].text
    assert any("Макет флагов" in m for m in notifier.to(106))
    await feed(env, message=message(106, "gflwwc", "/summary", chat_type="supergroup", chat_id=-100777))
    assert "Тестовое открытие" in session.sent()[-1].text


async def test_export_and_calls_commands(env, service, workbook):
    _, _, session = env
    await service.import_file(workbook.read_bytes(), "erp.xlsx", member(service, "s_maxhan"))
    await feed(env, message=message(102, "s_maxhan", "/export"))
    assert session.sent(SendDocument)
    await feed(env, message=message(102, "s_maxhan", "/calls"))
    assert "Обзвон" in session.sent()[-1].text or "некого" in session.sent()[-1].text
    await feed(env, message=message(102, "s_maxhan", "/summary"))
    assert "Сводка" in session.sent()[-1].text
