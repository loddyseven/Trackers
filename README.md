# Trackers

Telegram-бот для отслеживания on-chain активности по кошелькам в сетях `TON` и `TRC20`.

## Возможности

- добавление адресов прямо из Telegram
- подписи для кошельков
- пауза и возобновление отслеживания
- история по кошельку: входящие, исходящие, крупные движения
- экспорт `CSV` по последним транзакциям с выбором количества `1-100`
- inline-кнопка в explorer для новых алертов
- автоочистка уведомлений и ручная очистка через `/clear`
- хранение состояния в `SQLite`
- работа с Telegram Bot API через `IPv4`

## Команды

- `/add` - добавить адрес
- `/list` - список отслеживаемых адресов
- `/history <id>` - история, суммы и крупные транзакции
- `/csv <id> <1-100>` - CSV таблица с нужным количеством последних транзакций
- `/clear` - очистить уведомления бота
- `/remove <id>` - удалить адрес
- `/pause <id>` - поставить на паузу
- `/resume <id>` - снять с паузы
- `/rename <id> <label>` - поменять имя кошелька
- `/cancel` - выйти

Примеры:

```bash
/add ton EQ...
/add trc20 T...
/history 1
/csv 1 25
/pause 2
/rename 1 Main wallet
```

## Стек

- `Python 3.9+`
- `aiogram`
- `aiohttp`
- `SQLite`

## Источники данных

- `TON`: `TonAPI` account events
- `TRC20`: `TronGrid` TRC20 transaction history

Для стабильной работы `TON` желательно указать `TONAPI_KEY`, иначе можно упираться в rate limit публичного доступа.

## Быстрый старт

1. Создай виртуальное окружение и установи зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Создай `.env` на основе `.env.example`.

Минимально нужен:

```env
TELEGRAM_BOT_TOKEN=...
```

Рекомендуемые переменные:

```env
TONAPI_KEY=...
TRONGRID_API_KEY=...
ALLOWED_CHAT_IDS=123456789
POLL_INTERVAL_SECONDS=60
ALERT_AUTO_DELETE_SECONDS=60
```

3. Запусти бота:

```bash
python3 main.py
```

`main.py` сам подхватывает `.env` из корня проекта.

## Как это работает

- при первом добавлении адреса бот пропускает старую историю и начинает слать только новые события
- каждый чат хранит свой собственный список отслеживаемых кошельков
- новые on-chain уведомления отправляются отдельно, а основная панель бота переиспользуется без спама

## Структура проекта

```text
app/
  chains/      # клиенты TonAPI и TronGrid
  bot_commands.py
  config.py
  db.py
  handlers.py  # Telegram-команды и UI
  history.py   # история и CSV
  panel.py     # верхняя панель и inline-кнопки
  watchers.py  # polling и алерты
main.py        # точка входа
```

## Деплой

Бот уже адаптирован под запуск на VPS через `systemd`.

Базовый сценарий:

```bash
python3 -m venv /opt/autolocal/.venv
/opt/autolocal/.venv/bin/pip install -r requirements.txt
python3 main.py
```

## Безопасность

- `.env`, базы данных и логи не должны попадать в git
- токены и ключи нужно хранить только в окружении или в локальном `.env`
- перед публикацией репозитория проверь, что в истории коммитов нет секретов

## Ограничения

- `TON` через публичный `TonAPI` может отвечать `429 Too Many Requests`
- polling-модель всегда дает небольшую задержку относительно реальной транзакции
- для почти realtime-алертов лучше переходить на streaming/websocket подход
