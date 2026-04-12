from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.db import Database


class ChatPanelService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def cleanup_user_message(self, message: Message) -> None:
        try:
            await message.delete()
        except (TelegramBadRequest, TelegramForbiddenError):
            return

    async def show(
        self,
        bot: Bot,
        chat_id: int,
        text: str,
        force_new: bool = False,
    ) -> int:
        reply_markup = self._build_panel_markup()
        if not force_new:
            panel_message_id = self.db.get_panel_message_id(chat_id)
            if panel_message_id:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=panel_message_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                    return panel_message_id
                except TelegramBadRequest as exc:
                    if "message is not modified" in str(exc).lower():
                        return panel_message_id
                except TelegramForbiddenError:
                    pass

        sent = await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
        self.db.set_panel_message_id(chat_id, sent.message_id)
        return sent.message_id

    @staticmethod
    def _build_panel_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Очистить уведомления",
                        callback_data="clear_alerts",
                        style="danger",
                    )
                ]
            ]
        )
