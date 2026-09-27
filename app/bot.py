"""Телеграм-бот: команды, кнопки под задачами, загрузка Excel, работа в группе."""
from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, time, timedelta

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    User,
    WebAppInfo,
)

from app import keyboards, logic, texts
from app.excel_io import WorkbookError
from app.gsheets import GSheetError
from app.models import CANCELLED, DONE, PROGRESS, Member, Project
from app.service import AccessError, Service

log = logging.getLogger(__name__)

TG_LIMIT = 3900
TEXT_INPUT_TTL = 15 * 60


class TelegramNotifier:
    def __init__(self, bot: Bot):
        self.bot = bot

    @staticmethod
    def _fit(text: str) -> str:
        if len(text) <= TG_LIMIT:
            return text
        cut = text.rfind("\n", 0, TG_LIMIT)
        return text[: cut if cut > 0 else TG_LIMIT] + "\n…"

    async def send(self, chat_id: int, text: str, keyboard: InlineKeyboardMarkup | None = None) -> bool:
        for _ in range(2):
            try:
                await self.bot.send_message(chat_id, self._fit(text), reply_markup=keyboard)
                return True
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after)
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                log.warning("Cannot send to %s: %s", chat_id, exc)
                return False
        return False

    async def send_document(self, chat_id: int, data: bytes, filename: str, caption: str = "") -> bool:
        try:
            await self.bot.send_document(chat_id, BufferedInputFile(data, filename), caption=caption)
            return True
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            log.warning("Cannot send document to %s: %s", chat_id, exc)
            return False


class AwaitText(StatesGroup):
    waiting = State()


def _is_private(message: Message) -> bool:
    return message.chat.type == ChatType.PRIVATE


def build_router(service: Service) -> Router:
    router = Router()

    def actor_for(user: User | None) -> Member | None:
        if user is None:
            return None
        return service.identify(user.id, user.username)

    async def require_member(message: Message) -> Member | None:
        member = actor_for(message.from_user)
        if member:
            return member
        uname = f"@{message.from_user.username}" if message.from_user and message.from_user.username else "без ника"
        await message.answer(
            f"Я не нашёл вас в команде ({texts.e(uname)}).\n"
            "Попросите администратора добавить ваш Telegram-ник в Mini App → «Команда».")
        return None

    async def require_admin(message: Message) -> Member | None:
        member = await require_member(message)
        if member and not member.is_admin:
            await message.answer("Эта команда доступна только администраторам.")
            return None
        return member

    async def require_project(message: Message) -> Project | None:
        project = service.default_project()
        if not project:
            await message.answer("Проектов пока нет. Администратор может прислать Excel-файл или создать проект в Mini App.")
        return project

    async def send_my(chat_id: int, member: Member) -> None:
        today = service.today()
        team = service.team()
        projects = service.projects()
        if not projects:
            await service.notifier.send(chat_id, "Проектов пока нет.")
            return
        for project in projects:
            tasks = service.db.list_tasks(project.id)
            focus = logic.personal_focus(member, tasks, team, today)
            if focus.open_total == 0 and len(projects) > 1:
                continue
            text = texts.personal_digest(member, project, focus, today, weekly=True, greeting=False)
            top = (focus.overdue + focus.today + focus.soon + focus.in_progress)[:6]
            rows = [[InlineKeyboardButton(text=f"{'🆘 ' if t.blocked else ''}{t.title[:48]}",
                                          callback_data=f"t:card:{t.id}")] for t in top]
            app = keyboards.app_button(service.webapp_url, "📋 Все мои задачи", "?tab=my")
            if app:
                rows.append([app])
            await service.notifier.send(chat_id, text, InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)

    # ---------- команды ----------
    @router.message(CommandStart())
    async def cmd_start(message: Message, command: CommandObject) -> None:
        if not _is_private(message):
            await message.answer(f"Напишите мне в личку: https://t.me/{service.bot_username}")
            return
        user = message.from_user
        member = actor_for(user)
        if not member:
            uname = f"@{user.username}" if user and user.username else "(ника нет)"
            await message.answer(
                f"Здравствуйте! Я бот проектного менеджмента IAC Media.\n\n"
                f"Вашего ника {texts.e(uname)} нет в списке команды. Попросите администратора добавить вас.")
            if user and service.db.mark_sent(f"unknown:{user.id}"):
                for pm in service._pms():
                    if pm.telegram_id:
                        await service.notifier.send(
                            pm.telegram_id,
                            f"👤 В бота зашёл человек не из команды: {texts.e(user.full_name)} {texts.e(uname)}.\n"
                            "Если это новый участник — добавьте его в Mini App → «Команда».")
            return
        if command.args == "my":
            await send_my(message.chat.id, member)
            return
        team = service.team()
        total_open = sum(
            len([t for t in team.tasks_of(member, service.db.list_tasks(p.id)) if t.is_open()])
            for p in service.projects())
        role = f" · {texts.e(member.role)}" if member.role else ""
        observer_view = member.is_observer and not member.is_admin and not total_open
        if observer_view:
            lines = [
                f"👋 {texts.e(member.name)}, вы подключены как наблюдатель.{role}",
                "",
                "Каждое утро я присылаю сводку по проекту: просрочки, блокеры, что закрыто за сутки. "
                "/summary — сводка сейчас, /calls — обзвон на сегодня.",
                "",
                "Кнопка <b>«Открыть»</b> внизу слева — все задачи, обзор команды и обзвон (только просмотр).",
            ]
        else:
            lines = [
                f"👋 {texts.e(member.name)}, вы подключены!{role}",
                "",
                f"Открытых задач: <b>{total_open}</b>." if service.projects() else "Проектов пока нет.",
                "",
                "Каждое утро я пишу ваши задачи на день и спрашиваю «выполнено?» по горящим срокам, "
                "вечером — «успеваете?» по завтрашним.",
                "",
                "Кнопка <b>«Открыть»</b> внизу слева — ваш личный кабинет с задачами и сроками.",
            ]
        if member.is_admin:
            lines += ["", "Вы администратор: пришлите сюда Excel-файл с задачами или ссылку на Google Таблицу "
                          "командой /sheet. /help — все команды."]
        await message.answer("\n".join(lines), reply_markup=keyboards.app_only(
            service.webapp_url, query="?tab=home" if observer_view else "?tab=my"))
        if service.webapp_url.startswith("https://"):
            try:
                await message.bot.set_chat_menu_button(
                    chat_id=message.chat.id,
                    menu_button=MenuButtonWebApp(text="Открыть", web_app=WebAppInfo(url=service.webapp_url + "/")))
            except TelegramBadRequest as exc:
                log.warning("Cannot set menu button: %s", exc)

    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(texts.HELP)

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Ок, отменил.")

    @router.message(Command("my"))
    async def cmd_my(message: Message) -> None:
        member = await require_member(message)
        if not member:
            return
        if not _is_private(message):
            if member.telegram_id:
                await send_my(member.telegram_id, member)
                await message.reply("Отправил ваши задачи в личку 📬")
            else:
                await message.reply(f"Сначала нажмите Start в личке: https://t.me/{service.bot_username}")
            return
        await send_my(message.chat.id, member)

    @router.message(Command("summary"))
    async def cmd_summary(message: Message) -> None:
        member = await require_member(message)
        project = await require_project(message) if member else None
        if not member or not project:
            return
        today = service.today()
        team = service.team()
        tasks = service.db.list_tasks(project.id)
        if _is_private(message) and member.sees_all:
            audit = logic.audit(project, tasks, team, service.db.list_milestones(project.id), today)
            calls = [i for i in service.call_items(project) if not i["check"]]
            text = texts.pm_digest(project, tasks, team, today, attention=logic.attention(tasks, today),
                                   done_yesterday=[], calls_count=len(calls), audit=audit)
            await message.answer(text, reply_markup=keyboards.pm_actions(service.webapp_url))
        else:
            await message.answer(texts.group_digest(project, tasks, team, today, logic.workload(tasks, team, today)))

    @router.message(Command("calls"))
    async def cmd_calls(message: Message) -> None:
        member = await require_member(message)
        project = await require_project(message) if member else None
        if not member or not project:
            return
        items = service.call_items(project)
        if not member.sees_all:
            items = [i for i in items if i["kind"] == "scheduled" and i.get("caller") == member.name]
        if not items:
            await message.answer("📞 На сегодня обзванивать некого 👌")
            return
        lines = [f"📞 <b>Обзвон на {service.today().strftime('%d.%m')}</b>"]
        for item in items:
            mark = "✅" if item["check"] else "▫️"
            who = texts.e(item["title"])
            if item.get("username"):
                who += f" @{texts.e(item['username'])}"
            if item.get("phone"):
                who += f" · {texts.e(item['phone'])}"
            lines.append(f"\n{mark} <b>{who}</b> — {texts.e(item.get('subtitle') or '')}")
            for t in item["tasks"][:4]:
                lines.append(f"   • {texts.e(t['title'])} — {texts.e(t['reason'])}")
            if item.get("owners"):
                lines.append(f"   звонит: {texts.e(', '.join(item['owners']))}")
        await message.answer("\n".join(lines), reply_markup=keyboards.app_only(
            service.webapp_url, "📞 Отметить в кабинете", "?tab=calls") if _is_private(message) else None)

    @router.message(Command("add"))
    async def cmd_add(message: Message, command: CommandObject) -> None:
        member = await require_admin(message)
        project = await require_project(message) if member else None
        if not member or not project:
            return
        if not command.args:
            await message.answer("Формат: <code>/add @username 30.09 Название задачи</code>\n"
                                 "Можно без ника/даты. <code>!!</code> — критично, <code>!</code> — высокий.")
            return
        try:
            task = await service.create_task(project, service.parse_quick_add(command.args), member)
        except (ValueError, AccessError) as exc:
            await message.answer(f"⚠️ {texts.e(exc)}")
            return
        await message.answer("➕ Задача добавлена\n\n" + texts.task_card(task, project, service.today(), service.team()),
                             reply_markup=keyboards.task_actions(task, service.webapp_url, _is_private(message)))

    @router.message(Command("sheet"))
    async def cmd_sheet(message: Message, command: CommandObject) -> None:
        member = await require_admin(message)
        if not member:
            return
        if not command.args:
            await message.answer("Пришлите ссылку: <code>/sheet https://docs.google.com/spreadsheets/d/…</code>\n\n"
                                 "Таблица должна быть открыта «Все, у кого есть ссылка — Читатель».")
            return
        wait = await message.answer("⏳ Загружаю таблицу…")
        try:
            result, parsed, audit = await service.connect_sheet(command.args.strip(), member)
        except (GSheetError, WorkbookError, ValueError, AccessError) as exc:
            await wait.edit_text(f"⚠️ {texts.e(exc)}")
            return
        minutes = service.settings()["sync_minutes"]
        text = texts.import_report(result, parsed, audit) + f"\n\n🔄 Синхронизация каждые {minutes} мин."
        if service.sheets.can_write:
            text += "\n✍️ Статусы из бота записываются обратно в таблицу."
        await wait.edit_text(text)

    @router.message(Command("sync"))
    async def cmd_sync(message: Message) -> None:
        member = await require_admin(message)
        if not member:
            return
        projects = [p for p in service.projects() if p.source_type == "gsheet"]
        if not projects:
            await message.answer("Нет проектов, связанных с Google Таблицей. Подключите: /sheet ссылка")
            return
        for project in projects:
            try:
                result, parsed, audit = await service.sync_project(project)
                await message.answer(texts.import_report(result, parsed, audit))
            except Exception as exc:  # noqa: BLE001
                await message.answer(f"⚠️ {texts.e(project.name)}: {texts.e(exc)}")

    @router.message(Command("export"))
    async def cmd_export(message: Message) -> None:
        member = await require_admin(message)
        project = await require_project(message) if member else None
        if not member or not project:
            return
        data, filename = service.export(project)
        await message.answer_document(BufferedInputFile(data, filename),
                                      caption=f"📥 {project.name} — статусы на {service.today().strftime('%d.%m.%Y')}")

    @router.message(Command("bind_group"))
    async def cmd_bind_group(message: Message) -> None:
        if _is_private(message):
            await message.answer("Добавьте меня в рабочую группу и отправьте /bind_group там.")
            return
        member = await require_admin(message)
        if not member:
            return
        service.db.set_setting("group_chat_id", str(message.chat.id))
        await message.answer("✅ Группа подключена. Сюда будут приходить утренняя сводка, закрытые задачи и просьбы о помощи.\n"
                             "Каждый участник должен один раз нажать Start в личке с ботом, чтобы получать личные напоминания: "
                             f"https://t.me/{service.bot_username}")

    @router.message(Command("unbind_group"))
    async def cmd_unbind_group(message: Message) -> None:
        member = await require_admin(message)
        if member:
            service.db.set_setting("group_chat_id", None)
            await message.answer("Группа отключена от сводок.")

    # ---------- загрузка Excel ----------
    @router.message(F.document)
    async def on_document(message: Message) -> None:
        if not _is_private(message):
            return
        doc = message.document
        name = (doc.file_name or "").lower()
        if not name.endswith((".xlsx", ".xlsm")):
            await message.answer("Пришлите таблицу в формате .xlsx (Excel). Из Google Таблиц: Файл → Скачать → Microsoft Excel.")
            return
        member = await require_admin(message)
        if not member:
            return
        wait = await message.answer("⏳ Читаю таблицу…")
        buffer = io.BytesIO()
        await message.bot.download(doc, destination=buffer)
        try:
            result, parsed, audit = await service.import_file(buffer.getvalue(), doc.file_name or "table.xlsx", member)
        except (WorkbookError, AccessError) as exc:
            await wait.edit_text(f"⚠️ {texts.e(exc)}")
            return
        await wait.edit_text(texts.import_report(result, parsed, audit),
                             reply_markup=keyboards.app_only(service.webapp_url, "📊 Открыть панель", "?tab=home"))

    # ---------- ввод текста (проблема / комментарий) ----------
    @router.message(AwaitText.waiting, F.text)
    async def on_text_input(message: Message, state: FSMContext) -> None:
        if message.text.startswith("/"):
            await state.clear()
            return
        data = await state.get_data()
        await state.clear()
        if service.now().timestamp() - data.get("asked_at", 0) > TEXT_INPUT_TTL:
            return  # спрашивали давно — это уже не ответ на вопрос о задаче
        member = actor_for(message.from_user)
        try:
            task = service.get_task(int(data["task_id"]))
            if data["kind"] == "problem":
                await service.report_problem(task, message.text, member)
                await message.answer("🆘 Передал руководителю проекта. Вам напишут.")
            else:
                await service.add_comment(task, message.text, member)
                await message.answer("💬 Комментарий сохранён.")
        except (AccessError, LookupError, ValueError) as exc:
            await message.answer(f"⚠️ {texts.e(exc)}")

    # ---------- кнопки ----------
    async def finish(callback: CallbackQuery, suffix: str, markup: InlineKeyboardMarkup | None = None) -> None:
        msg = callback.message
        if isinstance(msg, Message):
            try:
                await msg.edit_text(f"{msg.html_text}\n\n{suffix}", reply_markup=markup)
            except TelegramBadRequest:
                await msg.answer(suffix, reply_markup=markup)

    @router.callback_query(F.data.startswith("t:"))
    async def on_task_button(callback: CallbackQuery, state: FSMContext) -> None:
        _, action, raw_id = callback.data.split(":", 2)
        member = actor_for(callback.from_user)
        if not member:
            await callback.answer("Вас нет в команде проекта", show_alert=True)
            return
        try:
            task = service.get_task(int(raw_id))
        except LookupError:
            await callback.answer("Задача удалена или не найдена", show_alert=True)
            return
        in_private = isinstance(callback.message, Message) and callback.message.chat.type == ChatType.PRIVATE
        try:
            if action == "card":
                project = service.db.get_project(task.project_id)
                await callback.message.answer(texts.task_card(task, project, service.today(), service.team()),
                                              reply_markup=keyboards.task_actions(task, service.webapp_url, in_private))
            elif action == "start":
                updated = await service.set_status(task, PROGRESS, member)
                await finish(callback, f"▶️ {texts.e(member.name)}: взято в работу",
                             keyboards.task_actions(updated, service.webapp_url, in_private))
            elif action == "done":
                updated = await service.set_status(task, DONE, member)
                late = " (с просрочкой)" if updated.status != DONE else ""
                await finish(callback, f"✅ {texts.e(member.name)}: выполнено{late}. Спасибо!")
            elif action == "wip":
                service.require_task_access(member, task)
                await finish(callback, "⏳ Ещё в работе. Когда будет готово?", keyboards.eta_choice(task))
            elif action in ("eta0", "eta1", "eta3"):
                updated = await service.set_eta(task, int(action[-1]), member)
                await finish(callback, f"🗓 Принято: ждём к {updated.eta.strftime('%d.%m')}. Если что-то мешает — жмите «Нужна помощь».")
            elif action == "ok":
                await service.confirm_on_track(task, member)
                await finish(callback, "👍 Отлично, держим срок!")
            elif action in ("prob", "cmt"):
                service.require_task_access(member, task)
                if not in_private:
                    await callback.answer("Напишите мне в личку — там можно описать подробности", show_alert=True)
                    return
                await state.set_state(AwaitText.waiting)
                await state.update_data(task_id=task.id, kind="problem" if action == "prob" else "comment",
                                        asked_at=service.now().timestamp())
                prompt = ("🆘 Опишите одним сообщением, что мешает и какая помощь нужна:" if action == "prob"
                          else "💬 Напишите комментарий к задаче одним сообщением:")
                await callback.message.answer(f"{prompt}\n<i>{texts.e(task.title)}</i>\n\n/cancel — отмена")
            else:
                await callback.answer()
                return
            await callback.answer()
        except AccessError as exc:
            await callback.answer(str(exc), show_alert=True)

    @router.callback_query(F.data.startswith("c:"))
    async def on_call_button(callback: CallbackQuery) -> None:
        _, action, raw_id = callback.data.split(":", 2)
        member = actor_for(callback.from_user)
        call = service.db.get_call(int(raw_id))
        if not member or not call:
            await callback.answer("Звонок не найден", show_alert=True)
            return
        if action == "done":
            await service.finish_call(call, member)
            await finish(callback, "✅ Отмечено: созвонились")
        elif action == "h1":
            until = service.now() + timedelta(hours=1)
            await service.snooze_call(call, until)
            await finish(callback, f"⏰ Напомню в {until.strftime('%H:%M')}")
        elif action == "tm":
            until = datetime.combine(service.today() + timedelta(days=1), time(10, 0), service.config.tz)
            await service.snooze_call(call, until)
            await finish(callback, "📅 Напомню завтра в 10:00")
        await callback.answer()

    @router.my_chat_member()
    async def on_added_to_group(event) -> None:
        chat = event.chat
        if chat.type in (ChatType.GROUP, ChatType.SUPERGROUP) and event.new_chat_member.status in ("member", "administrator"):
            await service.notifier.send(
                chat.id,
                "👋 Привет! Я слежу за задачами и сроками проекта.\n"
                "Администратор, отправьте /bind_group, чтобы я присылал сюда сводки.\n"
                f"Всем участникам: нажмите Start в личке — https://t.me/{service.bot_username} — "
                "чтобы получать личные напоминания.")

    return router


async def setup_bot_profile(bot: Bot, webapp_url: str) -> None:
    private = [
        BotCommand(command="my", description="Мои задачи и сроки"),
        BotCommand(command="summary", description="Сводка по проекту"),
        BotCommand(command="calls", description="Обзвон на сегодня"),
        BotCommand(command="add", description="Добавить задачу"),
        BotCommand(command="export", description="Выгрузить Excel"),
        BotCommand(command="sheet", description="Подключить Google Таблицу"),
        BotCommand(command="sync", description="Синхронизировать таблицу"),
        BotCommand(command="help", description="Помощь"),
    ]
    group = [
        BotCommand(command="summary", description="Сводка по проекту"),
        BotCommand(command="my", description="Мои задачи (в личку)"),
        BotCommand(command="add", description="Добавить задачу"),
        BotCommand(command="bind_group", description="Присылать сводки в эту группу"),
    ]
    await bot.set_my_commands(private, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group, scope=BotCommandScopeAllGroupChats())
    if webapp_url.startswith("https://"):
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Открыть", web_app=WebAppInfo(url=webapp_url + "/")))
