from __future__ import annotations

import csv
import io
from collections import defaultdict
from decimal import Decimal
from typing import Dict, Iterable, List, Sequence, Tuple

import aiohttp
from aiogram import html

from app.chains.ton import TonApiClient
from app.chains.tron import TronGridClient
from app.models import ChainEvent, Watch
from app.utils import format_decimal, sanitize_filename, shorten_address


class WalletHistoryService:
    def __init__(
        self,
        ton_client: TonApiClient,
        tron_client: TronGridClient,
    ) -> None:
        self.ton_client = ton_client
        self.tron_client = tron_client

    async def fetch_recent_events(self, watch: Watch, limit: int = 20) -> List[ChainEvent]:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if watch.network == "ton":
                return await self.ton_client.fetch_recent_activity(session, watch.address, limit=limit)
            if watch.network == "trc20":
                return await self.tron_client.fetch_recent_activity(session, watch.address, limit=limit)
        return []

    def build_history_text(
        self,
        watch: Watch,
        events: Sequence[ChainEvent],
        recent_count: int = 5,
    ) -> str:
        ordered = self._sort_events(events)
        recent = ordered[:recent_count]
        incoming = [event for event in ordered if event.direction == "incoming"]
        outgoing = [event for event in ordered if event.direction == "outgoing"]
        large_moves = sorted(
            [event for event in ordered if event.amount_value > 0],
            key=lambda item: item.amount_value,
            reverse=True,
        )[:3]

        lines = [
            "<b><i>История кошелька</i></b>",
            "<i>Сеть:</i> <code>{0}</code> | <i>Имя:</i> <b>{1}</b>".format(
                watch.network,
                html.quote(watch.label),
            ),
            "<code>{0}</code>".format(html.quote(watch.address)),
            "<i>Окно:</i> последние <b>{0}</b> событий".format(len(ordered)),
            "<i>Входящие:</i> <b>{0}</b> tx | <code>{1}</code>".format(
                len(incoming),
                html.quote(self._format_totals(incoming)),
            ),
            "<i>Исходящие:</i> <b>{0}</b> tx | <code>{1}</code>".format(
                len(outgoing),
                html.quote(self._format_totals(outgoing)),
            ),
        ]

        if large_moves:
            lines.append("")
            lines.append("<b>Крупные движения</b>")
            for index, event in enumerate(large_moves, start=1):
                lines.append("{0}. {1}".format(index, self._render_event_line(event)))

        if recent:
            lines.append("")
            lines.append("<b>Последние {0}</b>".format(min(recent_count, len(recent))))
            for index, event in enumerate(recent, start=1):
                lines.append("{0}. {1}".format(index, self._render_event_line(event)))
        else:
            lines.append("")
            lines.append("<i>Недавних событий пока не найдено.</i>")

        lines.append("")
        lines.append(
            "<i>CSV:</i> <code>/csv {0} 5</code> или <code>/csv {0} 20</code>".format(watch.id)
        )
        return "\n".join(lines)

    def build_csv_export(
        self,
        watch: Watch,
        events: Sequence[ChainEvent],
        recent_count: int = 5,
    ) -> Tuple[str, bytes]:
        ordered = self._sort_events(events)
        recent = ordered[:recent_count]

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "datetime_utc",
                "network",
                "direction",
                "amount",
                "asset",
                "counterparty",
                "tx_hash",
                "summary",
                "explorer_url",
            ]
        )
        for event in recent:
            writer.writerow(
                [
                    event.occurred_at,
                    event.network,
                    event.direction,
                    event.amount or "",
                    event.asset or "",
                    event.counterparty or "",
                    event.tx_hash or event.id,
                    event.summary,
                    event.explorer_url or "",
                ]
            )

        filename = "{0}_{1}_last{2}.csv".format(
            sanitize_filename(watch.label or watch.address),
            watch.network,
            len(recent),
        )
        return filename, buffer.getvalue().encode("utf-8-sig")

    @staticmethod
    def _sort_events(events: Sequence[ChainEvent]) -> List[ChainEvent]:
        return sorted(
            events,
            key=lambda item: (item.occurred_at_ts or 0, item.id),
            reverse=True,
        )

    def _render_event_line(self, event: ChainEvent) -> str:
        amount_part = self._format_amount_part(event)
        counterparty = shorten_address(event.counterparty or "unknown")
        return "<code>{0}</code> | <b>{1}</b> | <code>{2}</code> | <code>{3}</code>".format(
            html.quote(event.occurred_at),
            html.quote(event.direction.upper()),
            html.quote(amount_part),
            html.quote(counterparty),
        )

    def _format_amount_part(self, event: ChainEvent) -> str:
        if event.amount and event.asset:
            return "{0} {1}".format(event.amount, event.asset)
        if event.amount:
            return event.amount
        if event.asset:
            return event.asset
        return event.summary

    def _format_totals(self, events: Iterable[ChainEvent]) -> str:
        totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for event in events:
            if not event.asset or event.amount_value <= 0:
                continue
            totals[event.asset] += event.amount_value

        if not totals:
            return "0"

        chunks = []
        for asset, total in totals.items():
            chunks.append("{0} {1}".format(format_decimal(total), asset))
        return ", ".join(chunks)
