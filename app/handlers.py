from __future__ import annotations

from typing import Optional

import aiohttp
from aiogram import F, Router, html
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.db import Database, DuplicateWatchError
from app.history import WalletHistoryService
from app.models import Watch
from app.panel import ChatPanelService
from app.validators import ValidationError, normalize_address, normalize_label, normalize_network


NETWORK_PROMPT = "Какая сеть? Напиши <code>ton</code> или <code>trc20</code>."
HELP_TEXT = """
<b><i>Команды</i></b>
<code>/add</code> - добавить адрес
<code>/list</code> - список отслеживаемых адресов
<code>/history &lt;id&gt;</code> - история, суммы и крупные транзакции
<code>/csv &lt;id&gt; &lt;1-100&gt;</code> - CSV таблица с нужным числом последних транзакций
<code>/clear</code> - очистить уведомления бота
<code>/remove &lt;id&gt;</code> - удалить адрес
<code>/pause &lt;id&gt;</code> - поставить на паузу
<code>/resume &lt;id&gt;</code> - снять с паузы
<code>/rename &lt;id&gt; &lt;label&gt;</code> - поменять имя кошелька
""".strip()


class AddWatchStates(StatesGroup):
    network = State()
    address = State()
    label = State()


def build_router(
    db: Database,
    allowed_chat_ids: frozenset[int],
    history_service: WalletHistoryService,
) -> Router:
    router = Router()
    panel = ChatPanelService(db)

    def is_allowed(chat_id: int) -> bool:
        return not allowed_chat_ids or chat_id in allowed_chat_ids

    async def deny_if_needed(message: Message) -> bool:
        if is_allowed(message.chat.id):
            return False
        await panel.cleanup_user_message(message)
        await panel.show(message.bot, message.chat.id, "Этот чат не авторизован для работы с ботом.")
        return True

    async def show_panel(message: Message, text: str, force_new: bool = False) -> None:
        await panel.show(message.bot, message.chat.id, text, force_new=force_new)

    @router.callback_query(F.data == "clear_alerts")
    async def clear_alerts_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            await callback.answer()
            return
        if not is_allowed(callback.message.chat.id):
            await callback.answer("Чат не авторизован.", show_alert=True)
            return

        await state.clear()
        removed = await _clear_alerts(
            bot=callback.bot,
            db=db,
            chat_id=callback.message.chat.id,
        )
        await callback.answer("Очищено: {0}".format(removed))
        await panel.show(
            callback.bot,
            callback.message.chat.id,
            HELP_TEXT,
        )

    @router.message(CommandStart())
    async def start_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await state.clear()
        await show_panel(message, HELP_TEXT)

    @router.message(Command("help"))
    async def help_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await state.clear()
        await show_panel(message, HELP_TEXT)

    @router.message(Command("list"))
    async def list_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await state.clear()

        watches = db.list_watches(message.chat.id)
        if not watches:
            await show_panel(message, "<i>Список пуст.</i> Добавь адрес через <code>/add</code>.")
            return

        await show_panel(message, _format_watch_list(watches))

    @router.message(Command("history"))
    async def history_handler(message: Message, state: FSMContext, command: CommandObject) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await state.clear()

        watch = await _resolve_watch_for_command(
            message=message,
            db=db,
            command=command,
            panel=panel,
            usage="Используй формат: /history <номер из /list или id>",
        )
        if not watch:
            return

        try:
            events = await history_service.fetch_recent_events(watch, limit=20)
        except aiohttp.ClientResponseError as exc:
            await show_panel(message, _render_api_error(watch, exc.status))
            return
        if not events:
            await show_panel(message, _render_empty_history(watch))
            return

        await show_panel(message, history_service.build_history_text(watch, events, recent_count=5))

    @router.message(Command("csv"))
    async def csv_handler(message: Message, state: FSMContext, command: CommandObject) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await state.clear()

        watch = await _resolve_watch_for_command(
            message=message,
            db=db,
            command=command,
            panel=panel,
            usage="Используй формат: <code>/csv &lt;номер из /list или id&gt; &lt;1-100&gt;</code>",
        )
        if not watch:
            return

        csv_count = _parse_csv_count(command)
        if csv_count is None:
            await show_panel(
                message,
                "Количество строк должно быть от <code>1</code> до <code>100</code>.\n"
                "Пример: <code>/csv {0} 25</code>".format(watch.id),
            )
            return

        try:
            events = await history_service.fetch_recent_events(watch, limit=csv_count)
        except aiohttp.ClientResponseError as exc:
            await show_panel(message, _render_api_error(watch, exc.status))
            return
        if not events:
            await show_panel(message, _render_empty_history(watch))
            return

        filename, payload = history_service.build_csv_export(watch, events, recent_count=csv_count)
        exported_count = min(csv_count, len(events))
        await show_panel(
            message,
            "<b><i>CSV готов</i></b>\n"
            "<i>Кошелек:</i> <b>{0}</b>\n"
            "<i>Строк:</i> <code>{1}</code>".format(html.quote(watch.label), exported_count),
        )
        sent = await message.bot.send_document(
            chat_id=message.chat.id,
            document=BufferedInputFile(payload, filename=filename),
            caption=(
                "<b>{0}</b>\n"
                "<i>Последние транзакции:</i> <code>{1}</code>"
            ).format(html.quote(watch.label), exported_count),
        )
        db.add_alert_message(message.chat.id, sent.message_id)

    @router.message(Command("clear"))
    async def clear_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await state.clear()
        removed = await _clear_alerts(
            bot=message.bot,
            db=db,
            chat_id=message.chat.id,
        )
        await show_panel(message, HELP_TEXT)

    @router.message(Command("remove"))
    async def remove_handler(message: Message, state: FSMContext, command: CommandObject) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await state.clear()

        watch = await _resolve_watch_for_command(
            message=message,
            db=db,
            command=command,
            panel=panel,
            usage="Используй формат: /remove <номер из /list или id>",
        )
        if not watch:
            return

        if db.remove_watch(message.chat.id, watch.id):
            await show_panel(message, "Адрес <b>{0}</b> удален.".format(html.quote(watch.label)))
            return
        await show_panel(message, "<i>Адрес с таким id не найден.</i>")

    @router.message(Command("pause"))
    async def pause_handler(message: Message, state: FSMContext, command: CommandObject) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await state.clear()

        watch = await _resolve_watch_for_command(
            message=message,
            db=db,
            command=command,
            panel=panel,
            usage="Используй формат: /pause <номер из /list или id>",
        )
        if not watch:
            return

        if db.set_watch_status(message.chat.id, watch.id, False):
            await show_panel(message, "Адрес <b>{0}</b> поставлен на паузу.".format(html.quote(watch.label)))
            return
        await show_panel(message, "<i>Адрес с таким id не найден.</i>")

    @router.message(Command("resume"))
    async def resume_handler(message: Message, state: FSMContext, command: CommandObject) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await state.clear()

        watch = await _resolve_watch_for_command(
            message=message,
            db=db,
            command=command,
            panel=panel,
            usage="Используй формат: /resume <номер из /list или id>",
        )
        if not watch:
            return

        if db.set_watch_status(message.chat.id, watch.id, True):
            await show_panel(message, "Адрес <b>{0}</b> снова активен.".format(html.quote(watch.label)))
            return
        await show_panel(message, "<i>Адрес с таким id не найден.</i>")

    @router.message(Command("rename"))
    async def rename_handler(message: Message, state: FSMContext, command: CommandObject) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await state.clear()

        if not command.args:
            await show_panel(
                message,
                "Используй формат: <code>/rename &lt;номер из /list или id&gt; &lt;новое имя&gt;</code>",
            )
            return

        parts = command.args.split(maxsplit=1)
        if len(parts) != 2:
            await show_panel(
                message,
                "Используй формат: <code>/rename &lt;номер из /list или id&gt; &lt;новое имя&gt;</code>",
            )
            return

        watch = db.resolve_watch_reference(message.chat.id, parts[0])
        if not watch:
            await show_panel(
                message,
                "Адрес не найден. Используй номер из <code>/list</code> или внутренний <code>id</code>.",
            )
            return

        label = parts[1].strip()[:80]
        if not label:
            await show_panel(message, "<i>Новое имя не должно быть пустым.</i>")
            return

        if db.rename_watch(message.chat.id, watch.id, label):
            await show_panel(message, "Имя кошелька обновлено на <b>{0}</b>.".format(html.quote(label)))
            return
        await show_panel(message, "<i>Адрес с таким id не найден.</i>")

    @router.message(Command("add"))
    async def add_handler(message: Message, state: FSMContext, command: CommandObject) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)

        if command.args:
            await state.clear()
            parts = command.args.split(maxsplit=2)
            if len(parts) < 2:
                await show_panel(message, "Формат: <code>/add &lt;ton|trc20&gt; &lt;address&gt; [label]</code>")
                return
            network_raw, address_raw = parts[0], parts[1]
            label_raw = parts[2] if len(parts) > 2 else address_raw
            await _try_create_watch(message, db, panel, network_raw, address_raw, label_raw)
            return

        await state.clear()
        await state.set_state(AddWatchStates.network)
        await show_panel(message, NETWORK_PROMPT)

    @router.message(AddWatchStates.network)
    async def add_network_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)

        try:
            network = normalize_network(message.text or "")
        except ValidationError as exc:
            await show_panel(message, "{0}\n\n{1}".format(str(exc), NETWORK_PROMPT))
            return
        await state.update_data(network=network)
        await state.set_state(AddWatchStates.address)
        await show_panel(message, "Пришли адрес кошелька для сети <code>{0}</code>.".format(network))

    @router.message(AddWatchStates.address)
    async def add_address_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)

        data = await state.get_data()
        network = data["network"]
        try:
            address = normalize_address(network, message.text or "")
        except ValidationError as exc:
            await show_panel(
                message,
                "{0}\n\nПришли адрес кошелька для сети <code>{1}</code>.".format(str(exc), network),
            )
            return
        await state.update_data(address=address)
        await state.set_state(AddWatchStates.label)
        await show_panel(
            message,
            "Напиши подпись для адреса или отправь <code>-</code>, чтобы оставить сам адрес.",
        )

    @router.message(AddWatchStates.label)
    async def add_label_handler(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)

        data = await state.get_data()
        label = normalize_label(message.text or "", data["address"])
        await _try_create_watch(
            message=message,
            db=db,
            panel=panel,
            network_raw=data["network"],
            address_raw=data["address"],
            label_raw=label,
        )
        await state.clear()

    @router.message(F.text.startswith("/"))
    async def fallback_commands(message: Message) -> None:
        if await deny_if_needed(message):
            return
        await panel.cleanup_user_message(message)
        await show_panel(message, "<i>Неизвестная команда.</i> Используй <code>/help</code>.")

    @router.message()
    async def fallback_text(message: Message, state: FSMContext) -> None:
        if await deny_if_needed(message):
            return
        if await state.get_state():
            return
        await panel.cleanup_user_message(message)
        await show_panel(message, "Используй команды из меню или напиши <code>/help</code>.")

    return router


def _extract_reference(command: CommandObject) -> Optional[str]:
    if not command.args:
        return None
    return command.args.split(maxsplit=1)[0].strip()


def _parse_csv_count(command: CommandObject) -> Optional[int]:
    if not command.args:
        return None

    parts = command.args.split()
    if len(parts) == 1:
        return 5
    if len(parts) != 2:
        return None
    if not parts[1].isdigit():
        return None

    value = int(parts[1])
    if 1 <= value <= 100:
        return value
    return None


async def _resolve_watch_for_command(
    message: Message,
    db: Database,
    command: CommandObject,
    panel: ChatPanelService,
    usage: str,
) -> Optional[Watch]:
    token = _extract_reference(command)
    if not token:
        await panel.show(message.bot, message.chat.id, usage)
        return None

    watch = db.resolve_watch_reference(message.chat.id, token)
    if watch:
        return watch

    await panel.show(
        message.bot,
        message.chat.id,
        "Адрес не найден. Используй номер строки из <code>/list</code> или внутренний <code>id</code>.",
    )
    return None


async def _try_create_watch(
    message: Message,
    db: Database,
    panel: ChatPanelService,
    network_raw: str,
    address_raw: str,
    label_raw: str,
) -> None:
    try:
        network = normalize_network(network_raw)
        address = normalize_address(network, address_raw)
        label = normalize_label(label_raw, address)
        watch = db.add_watch(
            chat_id=message.chat.id,
            network=network,
            address=address,
            label=label,
        )
    except ValidationError as exc:
        await panel.show(message.bot, message.chat.id, str(exc))
        return
    except DuplicateWatchError:
        await panel.show(
            message.bot,
            message.chat.id,
            "<i>Такой адрес уже есть в отслеживании для этого чата.</i>",
        )
        return

    await panel.show(
        message.bot,
        message.chat.id,
        "<b><i>Адрес добавлен</i></b>\n"
        "<i>Сеть:</i> <code>{0}</code>\n"
        "<i>Имя:</i> <b>{1}</b>\n"
        "<code>{2}</code>\n"
        "<i>watch id:</i> <code>{3}</code>\n"
        "<i>Исторические события пропускаю, дальше будут только новые.</i>".format(
            watch.network,
            html.quote(watch.label),
            html.quote(watch.address),
            watch.id,
        ),
    )


def _format_watch_list(watches: list[Watch]) -> str:
    lines = [
        "<b><i>Текущие адреса</i></b> <code>{0}</code>".format(len(watches)),
        "",
    ]
    for index, watch in enumerate(watches, start=1):
        status = "ON" if watch.is_active else "PAUSED"
        lines.append(
            "<b>{0}.</b> <code>{1}</code> <b>{2}</b>\n<code>{3}</code>\n<i>status:</i> <b>{4}</b> | <i>id:</i> <code>{5}</code>".format(
                index,
                watch.network,
                html.quote(watch.label),
                html.quote(watch.address),
                status,
                watch.id,
            )
        )
        if index != len(watches):
            lines.append("")
    return "\n".join(lines)


def _render_empty_history(watch: Watch) -> str:
    return (
        "<b><i>История кошелька</i></b>\n"
        "<i>Сеть:</i> <code>{0}</code> | <i>Имя:</i> <b>{1}</b>\n"
        "<code>{2}</code>\n"
        "<i>Недавних событий пока не найдено.</i>"
    ).format(
        watch.network,
        html.quote(watch.label),
        html.quote(watch.address),
    )


def _render_api_error(watch: Watch, status_code: int) -> str:
    if status_code == 429:
        details = "Внешний API временно ограничил запросы. Попробуй еще раз чуть позже."
    else:
        details = "Не удалось получить историю из внешнего API."

    return (
        "<b><i>История кошелька</i></b>\n"
        "<i>Сеть:</i> <code>{0}</code> | <i>Имя:</i> <b>{1}</b>\n"
        "<code>{2}</code>\n"
        "<i>{3}</i>"
    ).format(
        watch.network,
        html.quote(watch.label),
        html.quote(watch.address),
        details,
    )


async def _clear_alerts(bot, db: Database, chat_id: int) -> int:
    message_ids = db.list_alert_message_ids(chat_id)
    removed = 0
    for message_id in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            removed += 1
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        finally:
            db.remove_alert_message(chat_id, message_id)
    return removed
