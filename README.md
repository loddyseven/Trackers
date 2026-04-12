# Trackers

[![License: MIT](https://img.shields.io/badge/license-MIT-0f766e.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-2563eb.svg)](#quick-start)
[![Networks](https://img.shields.io/badge/networks-TON%20%7C%20TRC20-111827.svg)](#features)

Clean Telegram wallet watcher for `TON` and `TRC20`.

Trackers monitors wallet activity, sends clean on-chain alerts, keeps recent history, exports CSV reports, and avoids chat spam with a reusable control panel inside Telegram.

## Features

- add wallets directly from Telegram
- assign labels to wallets for readable alerts
- pause, resume, rename, and remove trackers without touching the server
- view wallet history with incoming, outgoing, and large transaction summaries
- export `CSV` with any number of recent transactions from `1` to `100`
- open each detected transaction in the explorer through inline buttons
- auto-delete alert messages and clear notifications manually via `/clear`
- use `SQLite` for lightweight local storage
- connect to Telegram Bot API over `IPv4`

## Commands

- `/add` - добавить адрес
- `/list` - список отслеживаемых адресов
- `/history <id>` - история, суммы и крупные транзакции
- `/csv <id> <1-100>` - CSV таблица с нужным количеством последних транзакций
- `/clear` - очистить уведомления бота
- `/remove <id>` - удалить адрес
- `/pause <id>` - поставить на паузу
- `/resume <id>` - снять с паузы
- `/rename <id> <label>` - поменять имя кошелька

Examples:

```bash
/add ton EQ...
/add trc20 T...
/history 1
/csv 1 25
/pause 2
/rename 1 Main wallet
```

## Supported Chains

### TON

- source: `TonAPI account events`
- notifications include explorer links to the exact parsed transaction
- public access may hit rate limits without `TONAPI_KEY`

### TRC20

- source: `TronGrid TRC20 transaction history`
- suitable for `USDT TRC20` and other tracked token transfers
- confirmed transactions are used for cleaner alerts

## Stack

- `Python 3.9+`
- `aiogram`
- `aiohttp`
- `SQLite`

## Quick Start

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Create `.env` based on `.env.example`.

Minimum required:

```env
TELEGRAM_BOT_TOKEN=...
```

Recommended:

```env
TONAPI_KEY=...
TRONGRID_API_KEY=...
ALLOWED_CHAT_IDS=123456789
POLL_INTERVAL_SECONDS=60
ALERT_AUTO_DELETE_SECONDS=60
```

3. Start the bot:

```bash
python3 main.py
```

## How It Works

- on first wallet add, the bot skips old activity and starts alerting only on new events
- each Telegram chat keeps its own tracked wallet list
- alerts are sent as separate messages, while the main panel is edited in place to reduce spam
- `CSV` exports and watcher alerts can be cleaned with `/clear`

## Project Structure

```text
app/
  chains/      # TonAPI and TronGrid clients
  bot_commands.py
  config.py
  db.py
  handlers.py  # Telegram commands and UI
  history.py   # history and CSV exports
  panel.py     # persistent panel and inline buttons
  watchers.py  # polling and alert delivery
main.py        # entry point
```

## Deployment

The bot is ready to run on a VPS with `systemd`.

Basic flow:

```bash
python3 -m venv /opt/autolocal/.venv
/opt/autolocal/.venv/bin/pip install -r requirements.txt
python3 main.py
```

## Security

- never commit `.env`, databases, or logs
- store bot tokens and API keys only in environment variables or local `.env`
- review commit history before making the repository public

## Limitations

- public `TonAPI` access can return `429 Too Many Requests`
- polling introduces a small delay compared with real-time chain activity
- for near real-time alerts, a streaming or websocket-based architecture is better
