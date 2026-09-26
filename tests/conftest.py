from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.db import Database  # noqa: E402
from app.service import Service  # noqa: E402

TZ = ZoneInfo("Asia/Tashkent")
TOKEN = "123456:TEST-token"

HEADERS = ["№", "Блок", "Процедура", "Что конкретно сделать", "Приоритет", "Ответственный", "Статус процедуры",
           "Начало работы", "Окончание работы", "Срок (дн.)", "Фактическая дата завершения",
           "Поставщик / Исполнитель", "Доказательство выполнения", "Комментарии исполнителя"]

DEFAULT_TASKS = [
    (1, "PROJECT MANAGEMENT", "Подтверждение формата, даты и бюджета", "Закрепить дату", "Критично", "Азмиддин",
     "задача не начата", None, "Уточнить", "SIA Motors", "Письменное подтверждение", "Комментарий 1"),
    (2, "ДИЗАЙН / БРЕНДИНГ", "Key Visual мероприятия", "Сделать KV", "Критично", "Сарвар",
     "в процессе работы", None, date(2026, 10, 1), "IAC Agency / клиент", "", ""),
    (3, "ДИЗАЙН / БРЕНДИНГ", "Пресс-волл", "Макет 7×3", "Высокий", "Сарвар / Сардор",
     "задача не начата", None, "28.09.2026", "Рекламное производство", "", ""),
    (4, "ПЕРСОНАЛ", "Команда хостес", "Подобрать 6 хостес", "Средний", "Мухаммаджон",
     "выполнена", None, date(2026, 9, 20), "Модельное агентство", "", ""),
    (5, "ТЕХНИЧЕСКАЯ ЧАСТЬ", "Звук для ведущего", "Райдер", "Высокий", "Максуд",
     "", None, None, "Технический подрядчик", "", ""),
]


def make_workbook(path: Path, tasks=DEFAULT_TASKS, event_date: object = "Уточнить", code: str = "TEST_EVENT") -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "«ERP» (TEST)"
    ws.append(HEADERS + ["КОНТРОЛЬ ПРОЕКТА", "", "", "RACI / РОЛИ", "", "НАГРУЗКА КОМАНДЫ", "Задач"])
    for row_no, t in enumerate(tasks, start=2):
        no, block, title, desc, prio, resp, status, start, end, contractor, proof, comment = t
        ws.append([no, block, title, desc, prio, resp, status, start, end, f"=IF(H{row_no}=\"\",\"\",I{row_no}-H{row_no}+1)",
                   None, contractor, proof, comment])
    ws["O2"] = "Дата мероприятия"
    ws["V2"] = '=COUNTIF($F$2:$F$87,"Азмиддин")'

    info = wb.create_sheet("Информация о проекте")
    info["A1"] = "ERP — TEST"
    info["A4"] = "Название мероприятия:"
    info["C4"] = "Тестовое открытие"
    info["A5"] = "Код проекта:"
    info["C5"] = code
    info["A8"] = "Дата мероприятия:"
    info["C8"] = event_date
    info["A16"] = "№"
    info["B16"] = "Направление"

    ms = wb.create_sheet("MILESTONE")
    ms.append([])
    ms.append(["MILESTONE", "ДАТА", "КРИТЕРИЙ УСПЕХА", "СТАТУС"])
    ms.append(["M1: Дата подтверждена", "Уточнить", "Письменно", "Не начата"])
    ms.append(["M2: Монтаж", "T-1", "Зоны собраны", "Не начата"])

    risks = wb.create_sheet("Risk list")
    risks.append(["№", "Риск", "Вероятность", "Влияние", "Сценарий если случится", "Решение (что делать сейчас)",
                  "Ответственный", "Статус"])
    risks.append([1, "Дата не подтверждена", "HIGH", "CRITICAL", "Сорвётся всё", "Закрыть вводные", "Азмиддин", "Открыт"])
    risks.append([2, "Закрытый риск", "LOW", "LOW", "", "", "Асл", "Закрыт"])
    wb.save(path)
    return path


class FakeNotifier:
    def __init__(self):
        self.messages: list[tuple[int, str, object]] = []
        self.documents: list[tuple[int, str, bytes]] = []

    async def send(self, chat_id, text, keyboard=None):
        self.messages.append((chat_id, text, keyboard))
        return True

    async def send_document(self, chat_id, data, filename, caption=""):
        self.documents.append((chat_id, filename, data))
        return True

    def to(self, chat_id: int) -> list[str]:
        return [text for cid, text, _ in self.messages if cid == chat_id]


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 9, 26, 10, 0, tzinfo=TZ))


@pytest.fixture
def config(tmp_path) -> Config:
    return Config(
        bot_token=TOKEN, webapp_url="https://pm.example.com", host="127.0.0.1", port=0, data_dir=tmp_path,
        tz=TZ, team_file=ROOT / "config" / "team.yaml", google_credentials_file=None, dev_auth_username=None,
    )


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


@pytest.fixture
def service(config, notifier, clock) -> Service:
    db = Database(":memory:")
    svc = Service(db, config, notifier, clock=clock)
    svc.seed_team(config.team_file)
    svc.bot_username = "iacpmbot"
    # Азмиддин, Шахзод, Сарвар, Мухаммаджон «нажали Start»
    for tg_id, username in [(101, "gulyamov_330"), (102, "s_maxhan"), (106, "gflwwc"), (103, "raxmanov7777"),
                            (107, "msnuzz")]:
        member = db.member_by_username(username)
        db.bind_telegram(member.id, tg_id)
    return svc


@pytest.fixture
def workbook(tmp_path) -> Path:
    return make_workbook(tmp_path / "erp.xlsx")


def member(service: Service, username: str):
    return service.db.member_by_username(username)
