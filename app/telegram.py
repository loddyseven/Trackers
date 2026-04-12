from __future__ import annotations

import socket

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode

from app.config import Settings


def build_bot(settings: Settings) -> Bot:
    session = AiohttpSession(api=TelegramAPIServer.from_base(settings.telegram_api_base))
    session._connector_init["family"] = socket.AF_INET
    return Bot(
        token=settings.telegram_bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
