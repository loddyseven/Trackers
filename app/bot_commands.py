from __future__ import annotations

from aiogram.types import BotCommand


def build_bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command="start", description="Запуск и краткая справка"),
        BotCommand(command="add", description="добавить адрес"),
        BotCommand(command="list", description="список адресов"),
        BotCommand(command="history", description="История и крупные движения"),
        BotCommand(command="csv", description="CSV 1-100 строк"),
        BotCommand(command="clear", description="Убрать старые уведомления"),
        BotCommand(command="pause", description="Поставить адрес на паузу"),
        BotCommand(command="resume", description="Снять адрес с паузы"),
        BotCommand(command="rename", description="Поменять имя кошелька"),
        BotCommand(command="remove", description="Удалить адрес"),
        BotCommand(command="help", description="Показать все команды"),
    ]
