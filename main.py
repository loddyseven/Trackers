from __future__ import annotations

import asyncio
import logging

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot_commands import build_bot_commands
from app.chains.ton import TonApiClient
from app.chains.tron import TronGridClient
from app.config import load_settings
from app.db import Database
from app.handlers import build_router
from app.history import WalletHistoryService
from app.telegram import build_bot
from app.watchers import WatcherService


async def run() -> None:
    settings = load_settings()
    database = Database(settings.db_path)
    database.initialize()

    bot = build_bot(settings)
    dispatcher = Dispatcher(storage=MemoryStorage())

    ton_client = TonApiClient(settings.tonapi_base_url, settings.tonapi_key)
    tron_client = TronGridClient(settings.trongrid_base_url, settings.trongrid_api_key)
    history_service = WalletHistoryService(ton_client=ton_client, tron_client=tron_client)
    dispatcher.include_router(build_router(database, settings.allowed_chat_ids, history_service))
    watcher_service = WatcherService(
        db=database,
        bot=bot,
        ton_client=ton_client,
        tron_client=tron_client,
        poll_interval_seconds=settings.poll_interval_seconds,
        alert_auto_delete_seconds=settings.alert_auto_delete_seconds,
    )

    watcher_task = asyncio.create_task(watcher_service.run(), name="watcher-service")
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands(build_bot_commands())
        await dispatcher.start_polling(bot)
    finally:
        watcher_task.cancel()
        await asyncio.gather(watcher_task, return_exceptions=True)
        database.close()
        await bot.session.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
