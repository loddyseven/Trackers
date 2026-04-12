from __future__ import annotations

import asyncio
import logging
from typing import Optional, Sequence

import aiohttp
from aiogram import Bot, html
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.chains.ton import TonApiClient
from app.chains.tron import TronGridClient
from app.db import Database
from app.models import ChainEvent, Watch
from app.utils import shorten_address

logger = logging.getLogger(__name__)


class WatcherService:
    def __init__(
        self,
        db: Database,
        bot: Bot,
        ton_client: TonApiClient,
        tron_client: TronGridClient,
        poll_interval_seconds: int,
        alert_auto_delete_seconds: int,
    ) -> None:
        self.db = db
        self.bot = bot
        self.ton_client = ton_client
        self.tron_client = tron_client
        self.poll_interval_seconds = poll_interval_seconds
        self.alert_auto_delete_seconds = alert_auto_delete_seconds

    async def run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            while True:
                try:
                    await self._poll_all(session)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Watcher iteration failed")
                await asyncio.sleep(self.poll_interval_seconds)

    async def _poll_all(self, session: aiohttp.ClientSession) -> None:
        watches = self.db.list_active_watches()
        if not watches:
            return

        for watch in watches:
            try:
                await self._poll_watch(session, watch)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to poll watch #%s (%s)", watch.id, watch.address)
            await asyncio.sleep(0.2)

    async def _poll_watch(self, session: aiohttp.ClientSession, watch: Watch) -> None:
        try:
            events = await self._fetch_events(session, watch)
        except aiohttp.ClientResponseError as exc:
            if exc.status == 429:
                logger.warning("Rate limit from upstream for watch #%s (%s)", watch.id, watch.address)
                return
            raise
        if not events:
            return

        newest_cursor = events[0].id
        if not watch.last_cursor:
            self.db.update_cursor(watch.id, newest_cursor)
            return

        pending = []
        for event in events:
            if event.id == watch.last_cursor:
                break
            pending.append(event)

        if not pending:
            return

        for event in reversed(pending):
            await self._notify_watch(watch, event)

        self.db.update_cursor(watch.id, newest_cursor)

    async def _fetch_events(
        self,
        session: aiohttp.ClientSession,
        watch: Watch,
    ) -> Sequence[ChainEvent]:
        if watch.network == "ton":
            return await self.ton_client.fetch_recent_activity(session, watch.address)
        if watch.network == "trc20":
            return await self.tron_client.fetch_recent_activity(session, watch.address)
        return []

    async def _notify_watch(self, watch: Watch, event: ChainEvent) -> None:
        text = self._render_alert(watch, event)
        reply_markup = self._build_explorer_markup(event)
        try:
            sent = await self.bot.send_message(
                chat_id=watch.chat_id,
                text=text,
                reply_markup=reply_markup,
            )
            self.db.add_alert_message(watch.chat_id, sent.message_id)
            if self.alert_auto_delete_seconds > 0:
                asyncio.create_task(
                    self._delete_message_later(
                        chat_id=watch.chat_id,
                        message_id=sent.message_id,
                        delay_seconds=self.alert_auto_delete_seconds,
                    )
                )
        except TelegramForbiddenError:
            logger.warning("Bot lost access to chat %s, pausing watch #%s", watch.chat_id, watch.id)
            self.db.set_watch_status(watch.chat_id, watch.id, False)

    def _render_alert(self, watch: Watch, event: ChainEvent) -> str:
        lines = [
            "<b><i>New {0} activity</i></b>".format(event.network.upper()),
            "<i>Кошелек:</i> <b>{0}</b>".format(html.quote(watch.label)),
            "<code>{0}</code>".format(html.quote(watch.address)),
            "<i>Event:</i> <code>{0}</code>".format(html.quote(event.summary)),
            "<i>Time:</i> <code>{0}</code>".format(html.quote(event.occurred_at)),
        ]
        return "\n".join(lines)

    def _build_explorer_markup(self, event: ChainEvent) -> Optional[InlineKeyboardMarkup]:
        if not event.explorer_url:
            return None

        button_label = "Open {0} Explorer".format(event.network.upper())
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=button_label,
                        url=event.explorer_url,
                    )
                ]
            ]
        )

    async def _delete_message_later(self, chat_id: int, message_id: int, delay_seconds: int) -> None:
        await asyncio.sleep(delay_seconds)
        try:
            await self.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        self.db.remove_alert_message(chat_id, message_id)
