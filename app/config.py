from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Optional


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_api_base: str
    db_path: str
    poll_interval_seconds: int
    alert_auto_delete_seconds: int
    tonapi_base_url: str
    tonapi_key: Optional[str]
    trongrid_base_url: str
    trongrid_api_key: Optional[str]
    allowed_chat_ids: FrozenSet[int]


def _read_optional(name: str) -> Optional[str]:
    value = os.getenv(name, "").strip()
    return value or None


def _read_allowed_chat_ids() -> FrozenSet[int]:
    raw = os.getenv("ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        return frozenset()

    values = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        values.add(int(chunk))
    return frozenset(values)


def _load_dotenv_if_present() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_settings() -> Settings:
    _load_dotenv_if_present()
    token = _read_optional("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    return Settings(
        telegram_bot_token=token,
        telegram_api_base=os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/"),
        db_path=os.getenv("DB_PATH", "data/bot.db"),
        poll_interval_seconds=max(10, int(os.getenv("POLL_INTERVAL_SECONDS", "20"))),
        alert_auto_delete_seconds=max(0, int(os.getenv("ALERT_AUTO_DELETE_SECONDS", "60"))),
        tonapi_base_url=os.getenv("TONAPI_BASE_URL", "https://tonapi.io").rstrip("/"),
        tonapi_key=_read_optional("TONAPI_KEY"),
        trongrid_base_url=os.getenv("TRONGRID_BASE_URL", "https://api.trongrid.io").rstrip("/"),
        trongrid_api_key=_read_optional("TRONGRID_API_KEY"),
        allowed_chat_ids=_read_allowed_chat_ids(),
    )
