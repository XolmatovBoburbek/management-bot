from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path) -> None:
    """Минимальный загрузчик .env (без лишних зависимостей). Не перетирает уже заданные переменные."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    bot_token: str
    webapp_url: str
    host: str
    port: int
    data_dir: Path
    tz: ZoneInfo
    team_file: Path
    google_credentials_file: Path | None
    dev_auth_username: str | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bot.sqlite3"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


def load_config() -> Config:
    load_dotenv(ROOT / ".env")
    creds = os.environ.get("GOOGLE_CREDENTIALS_FILE", "").strip()
    return Config(
        bot_token=os.environ.get("BOT_TOKEN", "").strip(),
        webapp_url=os.environ.get("WEBAPP_URL", "").strip().rstrip("/"),
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        data_dir=Path(os.environ.get("DATA_DIR", str(ROOT / "data"))),
        tz=ZoneInfo(os.environ.get("TIMEZONE", "Asia/Tashkent")),
        team_file=Path(os.environ.get("TEAM_FILE", str(ROOT / "config" / "team.yaml"))),
        google_credentials_file=Path(creds) if creds else None,
        dev_auth_username=os.environ.get("DEV_AUTH_USERNAME", "").strip() or None,
    )
