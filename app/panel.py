from __future__ import annotations

from typing import Optional

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
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> int:
        reply_markup = reply_markup or self.build_home_markup()
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
    def build_home_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Добавить адрес",
                        callback_data="panel_add",
                        style="success",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Список адресов",
                        callback_data="panel_list",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="История",
                        callback_data="panel_history",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="CSV",
                        callback_data="panel_csv",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Назад в меню",
                        callback_data="panel_menu",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Очистить уведомления",
                        callback_data="clear_alerts",
                        style="danger",
                    ),
                ],
            ]
        )

    @staticmethod
    def build_back_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Назад в меню",
                        callback_data="panel_menu",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Очистить уведомления",
                        callback_data="clear_alerts",
                        style="danger",
                    )
                ],
            ]
        )

    @staticmethod
    def build_network_markup() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="TON",
                        callback_data="pick_network:ton",
                    ),
                    InlineKeyboardButton(
                        text="TRC20",
                        callback_data="pick_network:trc20",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Назад в меню",
                        callback_data="panel_menu",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Очистить уведомления",
                        callback_data="clear_alerts",
                        style="danger",
                    )
                ],
            ]
        )
