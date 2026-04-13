from __future__ import annotations

import os
import sqlite3
from typing import List, Optional

from app.models import Watch
from app.utils import utc_now_iso


class DuplicateWatchError(ValueError):
    """Raised when the same watch already exists for chat/network/address."""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                network TEXT NOT NULL,
                address TEXT NOT NULL,
                label TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_cursor TEXT,
                last_checked_at TEXT,
                UNIQUE(chat_id, network, address)
            );

            CREATE TABLE IF NOT EXISTS chat_ui_state (
                chat_id INTEGER PRIMARY KEY,
                panel_message_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_messages (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            );
            """
        )
        self.connection.commit()

    def add_watch(self, chat_id: int, network: str, address: str, label: str) -> Watch:
        now = utc_now_iso()
        try:
            cursor = self.connection.execute(
                """
                INSERT INTO watches (chat_id, network, address, label, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (chat_id, network, address, label, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise DuplicateWatchError from exc
        self.connection.commit()
        return self.get_watch_by_id(chat_id, int(cursor.lastrowid))

    def list_watches(self, chat_id: int) -> List[Watch]:
        rows = self.connection.execute(
            """
            SELECT * FROM watches
            WHERE chat_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (chat_id,),
        ).fetchall()
        return [self._row_to_watch(row) for row in rows]

    def list_active_watches(self) -> List[Watch]:
        rows = self.connection.execute(
            """
            SELECT * FROM watches
            WHERE is_active = 1
            ORDER BY updated_at ASC, id ASC
            """
        ).fetchall()
        return [self._row_to_watch(row) for row in rows]

    def get_watch_by_id(self, chat_id: int, watch_id: int) -> Optional[Watch]:
        row = self.connection.execute(
            """
            SELECT * FROM watches
            WHERE chat_id = ? AND id = ?
            """,
            (chat_id, watch_id),
        ).fetchone()
        return self._row_to_watch(row) if row else None

    def remove_watch(self, chat_id: int, watch_id: int) -> bool:
        cursor = self.connection.execute(
            "DELETE FROM watches WHERE chat_id = ? AND id = ?",
            (chat_id, watch_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def rename_watch(self, chat_id: int, watch_id: int, label: str) -> bool:
        now = utc_now_iso()
        cursor = self.connection.execute(
            """
            UPDATE watches
            SET label = ?, updated_at = ?
            WHERE chat_id = ? AND id = ?
            """,
            (label, now, chat_id, watch_id),
        )
        self.connection.commit()
        return cursor.rowcount > 0

    def set_watch_status(self, chat_id: int, watch_id: int, active: bool) -> bool:
        now = utc_now_iso()
        if active:
            cursor = self.connection.execute(
                """
                UPDATE watches
                SET is_active = 1,
                    last_cursor = NULL,
                    last_checked_at = NULL,
                    updated_at = ?
                WHERE chat_id = ? AND id = ?
                """,
                (now, chat_id, watch_id),
            )
        else:
            cursor = self.connection.execute(
                """
                UPDATE watches
                SET is_active = 0,
                    updated_at = ?
                WHERE chat_id = ? AND id = ?
                """,
                (now, chat_id, watch_id),
            )
        self.connection.commit()
        return cursor.rowcount > 0

    def update_cursor(self, watch_id: int, cursor_value: Optional[str]) -> None:
        now = utc_now_iso()
        self.connection.execute(
            """
            UPDATE watches
            SET last_cursor = ?, last_checked_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (cursor_value, now, now, watch_id),
        )
        self.connection.commit()

    def get_panel_message_id(self, chat_id: int) -> Optional[int]:
        row = self.connection.execute(
            """
            SELECT panel_message_id
            FROM chat_ui_state
            WHERE chat_id = ?
            """,
            (chat_id,),
        ).fetchone()
        return int(row["panel_message_id"]) if row else None

    def set_panel_message_id(self, chat_id: int, message_id: int) -> None:
        now = utc_now_iso()
        self.connection.execute(
            """
            INSERT INTO chat_ui_state (chat_id, panel_message_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id)
            DO UPDATE SET
                panel_message_id = excluded.panel_message_id,
                updated_at = excluded.updated_at
            """,
            (chat_id, message_id, now),
        )
        self.connection.commit()

    def resolve_watch_reference(self, chat_id: int, token: str) -> Optional[Watch]:
        normalized = token.strip()
        if not normalized or not normalized.isdigit():
            return None

        numeric = int(normalized)
        exact = self.get_watch_by_id(chat_id, numeric)
        if exact:
            return exact

        watches = self.list_watches(chat_id)
        if 1 <= numeric <= len(watches):
            return watches[numeric - 1]
        return None

    def add_alert_message(self, chat_id: int, message_id: int) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO alert_messages (chat_id, message_id, created_at)
            VALUES (?, ?, ?)
            """,
            (chat_id, message_id, utc_now_iso()),
        )
        self.connection.commit()

    def list_alert_message_ids(self, chat_id: int) -> List[int]:
        rows = self.connection.execute(
            """
            SELECT message_id
            FROM alert_messages
            WHERE chat_id = ?
            ORDER BY created_at ASC, message_id ASC
            """,
            (chat_id,),
        ).fetchall()
        return [int(row["message_id"]) for row in rows]

    def remove_alert_message(self, chat_id: int, message_id: int) -> None:
        self.connection.execute(
            """
            DELETE FROM alert_messages
            WHERE chat_id = ? AND message_id = ?
            """,
            (chat_id, message_id),
        )
        self.connection.commit()

    def clear_alert_messages(self, chat_id: int) -> None:
        self.connection.execute(
            """
            DELETE FROM alert_messages
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        self.connection.commit()

    @staticmethod
    def _row_to_watch(row: sqlite3.Row) -> Watch:
        return Watch(
            id=int(row["id"]),
            chat_id=int(row["chat_id"]),
            network=row["network"],
            address=row["address"],
            label=row["label"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_cursor=row["last_cursor"],
            last_checked_at=row["last_checked_at"],
        )
