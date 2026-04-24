from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, Iterable, List, Sequence, Tuple

import aiohttp
from aiogram import html

from app.chains.ton import TonApiClient
from app.chains.tron import TronGridClient
from app.models import ChainEvent, Watch
from app.utils import format_decimal, sanitize_filename, shorten_address


@dataclass
class CounterpartyInsight:
    address: str
    tx_count: int
    incoming_count: int
    outgoing_count: int
    total_by_asset: Dict[str, Decimal]
    total_volume: Decimal
    last_seen_ts: int
    confidence_score: int


@dataclass
class PatternMatch:
    watch: Watch
    score: int
    reasons: List[str]


@dataclass
class WalletPatternProfile:
    dominant_asset: str
    incoming_share: float
    avg_amount: Decimal
    tx_count: int
    active_hours: set[int]
    top_counterparties: set[str]


class WalletHistoryService:
    PATTERN_SAMPLE_SIZE = 40

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

    async def fetch_history_events(self, watch: Watch) -> List[ChainEvent]:
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if watch.network == "ton":
                return await self.ton_client.fetch_all_activity(session, watch.address)
            if watch.network == "trc20":
                return await self.tron_client.fetch_all_activity(session, watch.address)
        return []

    async def find_pattern_matches(
        self,
        target_watch: Watch,
        target_events: Sequence[ChainEvent],
        candidates: Sequence[Watch],
        limit: int = 3,
    ) -> List[PatternMatch]:
        sample_events = self._build_pattern_sample(target_events)
        if not sample_events:
            return []

        target_profile = self._build_pattern_profile(sample_events)
        timeout = aiohttp.ClientTimeout(total=45)
        matches: List[PatternMatch] = []
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for candidate in candidates:
                events = await self._fetch_pattern_events(session, candidate)
                profile = self._build_pattern_profile(events)
                if not profile:
                    continue
                score, reasons = self._score_pattern_match(target_profile, profile)
                if score <= 0:
                    continue
                matches.append(PatternMatch(watch=candidate, score=score, reasons=reasons))

        matches.sort(key=lambda item: (item.score, item.watch.label.lower()), reverse=True)
        return matches[:limit]

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
        shadow = self._build_shadow_balance(ordered)
        counterparties = self._build_top_counterparties(ordered)
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
            "<i>Событий найдено:</i> <b>{0}</b>".format(len(ordered)),
            "<i>Входящие:</i> <b>{0}</b> tx | <code>{1}</code>".format(
                len(incoming),
                html.quote(self._format_totals(incoming)),
            ),
            "<i>Исходящие:</i> <b>{0}</b> tx | <code>{1}</code>".format(
                len(outgoing),
                html.quote(self._format_totals(outgoing)),
            ),
            "",
            "<b>Shadow balance</b>",
            "<i>Total in:</i> <code>{0}</code>".format(html.quote(self._format_asset_totals(shadow["in"]))),
            "<i>Total out:</i> <code>{0}</code>".format(html.quote(self._format_asset_totals(shadow["out"]))),
            "<i>Net flow:</i> <code>{0}</code>".format(html.quote(self._format_asset_totals(shadow["net"], signed=True))),
            "<i>Turnover:</i> <code>{0}</code>".format(html.quote(self._format_asset_totals(shadow["turnover"]))),
            "<i>Peak observed:</i> <code>{0}</code>".format(html.quote(self._format_asset_totals(shadow["peak"]))),
        ]

        if counterparties:
            lines.append("")
            lines.append("<b>Top counterparties</b>")
            for index, counterparty in enumerate(counterparties, start=1):
                lines.append(
                    "{0}. <b>{1}/100</b> | <code>{2}</code> | <b>{3}</b> tx | <code>{4}</code>".format(
                        index,
                        counterparty.confidence_score,
                        html.quote(shorten_address(counterparty.address)),
                        counterparty.tx_count,
                        html.quote(self._format_asset_totals(counterparty.total_by_asset)),
                    )
                )
                lines.append(
                    "in <b>{0}</b> / out <b>{1}</b> | last <code>{2}</code>".format(
                        counterparty.incoming_count,
                        counterparty.outgoing_count,
                        html.quote(self._format_seen_time(counterparty.last_seen_ts)),
                    )
                )

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
        if watch.id > 0:
            lines.append("<i>CSV:</i> кнопка <b>CSV</b> или <code>/csv {0} 20</code>".format(watch.id))
            lines.append("<i>Pattern:</i> кнопка <b>Паттерны</b> или <code>/pattern {0}</code>".format(watch.id))
        else:
            lines.append("<i>CSV:</i> кнопка <b>CSV</b> или команда <code>/csv &lt;address&gt; 20</code>")
            lines.append("<i>Pattern:</i> кнопка <b>Паттерны</b> или команда <code>/pattern &lt;address&gt;</code>")
        return "\n".join(lines)

    def build_pattern_text(
        self,
        target_watch: Watch,
        matches: Sequence[PatternMatch],
        scanned_count: int,
        total_candidates: int,
    ) -> str:
        lines = [
            "<b><i>Pattern search</i></b>",
            "<i>Сеть:</i> <code>{0}</code> | <i>Цель:</i> <b>{1}</b>".format(
                target_watch.network,
                html.quote(target_watch.label),
            ),
            "<code>{0}</code>".format(html.quote(target_watch.address)),
            "<i>Сравнил кошельков:</i> <b>{0}</b> из <b>{1}</b>".format(scanned_count, total_candidates),
        ]

        if not matches:
            lines.append("")
            lines.append("<i>Похожих кошельков среди отслеживаемых пока не нашлось.</i>")
            return "\n".join(lines)

        lines.append("")
        lines.append("<b>Похожие среди отслеживаемых</b>")
        for index, match in enumerate(matches, start=1):
            lines.append(
                "{0}. <b>{1}/100</b> | <code>{2}</code> <b>{3}</b>".format(
                    index,
                    match.score,
                    html.quote(match.watch.network),
                    html.quote(match.watch.label),
                )
            )
            lines.append("<code>{0}</code>".format(html.quote(shorten_address(match.watch.address))))
            if match.reasons:
                lines.append("<i>{0}</i>".format(html.quote(", ".join(match.reasons))))

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
        amount_markup = "<code>{0}</code>".format(html.quote(amount_part))
        if event.explorer_url:
            amount_markup = '<a href="{0}"><b>{1}</b></a>'.format(
                html.quote(event.explorer_url),
                html.quote(amount_part),
            )

        return "<code>{0}</code> | <b>{1}</b> | {2} | <code>{3}</code>".format(
            html.quote(event.occurred_at),
            html.quote(event.direction.upper()),
            amount_markup,
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

    def _build_shadow_balance(self, events: Sequence[ChainEvent]) -> Dict[str, Dict[str, Decimal]]:
        incoming_totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        outgoing_totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        turnover_totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        running_balances: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        peak_balances: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

        chronological = sorted(events, key=lambda item: (item.occurred_at_ts or 0, item.id))
        for event in chronological:
            if not event.asset or event.amount_value <= 0:
                continue
            asset = event.asset
            if event.direction == "incoming":
                incoming_totals[asset] += event.amount_value
                running_balances[asset] += event.amount_value
            elif event.direction == "outgoing":
                outgoing_totals[asset] += event.amount_value
                running_balances[asset] -= event.amount_value
            else:
                continue
            turnover_totals[asset] += event.amount_value
            peak_balances[asset] = max(peak_balances[asset], running_balances[asset])

        net_totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for asset in set(incoming_totals) | set(outgoing_totals):
            net_totals[asset] = incoming_totals[asset] - outgoing_totals[asset]

        return {
            "in": dict(incoming_totals),
            "out": dict(outgoing_totals),
            "net": dict(net_totals),
            "turnover": dict(turnover_totals),
            "peak": dict(peak_balances),
        }

    def _build_top_counterparties(self, events: Sequence[ChainEvent], limit: int = 3) -> List[CounterpartyInsight]:
        grouped: Dict[str, Dict] = {}
        newest_seen = max((event.occurred_at_ts or 0 for event in events), default=0)
        for event in events:
            if not event.counterparty or not event.asset or event.amount_value <= 0:
                continue
            key = event.counterparty
            bucket = grouped.setdefault(
                key,
                {
                    "tx_count": 0,
                    "incoming_count": 0,
                    "outgoing_count": 0,
                    "last_seen_ts": 0,
                    "total_by_asset": defaultdict(lambda: Decimal("0")),
                },
            )
            bucket["tx_count"] += 1
            if event.direction == "incoming":
                bucket["incoming_count"] += 1
            elif event.direction == "outgoing":
                bucket["outgoing_count"] += 1
            bucket["last_seen_ts"] = max(bucket["last_seen_ts"], event.occurred_at_ts or 0)
            bucket["total_by_asset"][event.asset] += event.amount_value

        if not grouped:
            return []

        max_tx_count = max(data["tx_count"] for data in grouped.values())
        max_volume = max(
            sum(data["total_by_asset"].values(), start=Decimal("0"))
            for data in grouped.values()
        )
        insights: List[CounterpartyInsight] = []
        for address, data in grouped.items():
            total_volume = sum(data["total_by_asset"].values(), start=Decimal("0"))
            confidence = self._score_counterparty(
                tx_count=data["tx_count"],
                max_tx_count=max_tx_count,
                total_volume=total_volume,
                max_volume=max_volume,
                incoming_count=data["incoming_count"],
                outgoing_count=data["outgoing_count"],
                last_seen_ts=data["last_seen_ts"],
                newest_seen_ts=newest_seen,
            )
            insights.append(
                CounterpartyInsight(
                    address=address,
                    tx_count=data["tx_count"],
                    incoming_count=data["incoming_count"],
                    outgoing_count=data["outgoing_count"],
                    total_by_asset=dict(data["total_by_asset"]),
                    total_volume=total_volume,
                    last_seen_ts=data["last_seen_ts"],
                    confidence_score=confidence,
                )
            )

        insights.sort(key=lambda item: (item.confidence_score, item.total_volume, item.tx_count), reverse=True)
        return insights[:limit]

    def _score_counterparty(
        self,
        tx_count: int,
        max_tx_count: int,
        total_volume: Decimal,
        max_volume: Decimal,
        incoming_count: int,
        outgoing_count: int,
        last_seen_ts: int,
        newest_seen_ts: int,
    ) -> int:
        tx_score = tx_count / max_tx_count if max_tx_count else 0
        volume_score = float(total_volume / max_volume) if max_volume > 0 else 0
        recency_score = (last_seen_ts / newest_seen_ts) if newest_seen_ts else 0
        two_way_score = 1.0 if incoming_count and outgoing_count else 0.45
        score = (
            tx_score * 0.3
            + volume_score * 0.35
            + recency_score * 0.2
            + two_way_score * 0.15
        )
        return max(1, min(100, round(score * 100)))

    async def _fetch_pattern_events(
        self,
        session: aiohttp.ClientSession,
        watch: Watch,
    ) -> List[ChainEvent]:
        if watch.network == "ton":
            return await self.ton_client.fetch_recent_activity(
                session,
                watch.address,
                limit=self.PATTERN_SAMPLE_SIZE,
            )
        if watch.network == "trc20":
            return await self.tron_client.fetch_recent_activity(
                session,
                watch.address,
                limit=self.PATTERN_SAMPLE_SIZE,
            )
        return []

    def _build_pattern_sample(self, events: Sequence[ChainEvent]) -> List[ChainEvent]:
        sample = [event for event in self._sort_events(events) if event.amount_value > 0][: self.PATTERN_SAMPLE_SIZE]
        if sample:
            return sample
        return self._sort_events(events)[: self.PATTERN_SAMPLE_SIZE]

    def _build_pattern_profile(self, events: Sequence[ChainEvent]) -> WalletPatternProfile | None:
        if not events:
            return None

        incoming_count = 0
        amounts: List[Decimal] = []
        hours: set[int] = set()
        asset_totals: Dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        counterparty_counts: Dict[str, int] = defaultdict(int)

        for event in events:
            if event.direction == "incoming":
                incoming_count += 1
            if event.amount_value > 0:
                amounts.append(event.amount_value)
                if event.asset:
                    asset_totals[event.asset] += event.amount_value
            if event.counterparty:
                counterparty_counts[event.counterparty] += 1
            if event.occurred_at_ts:
                hours.add(datetime.fromtimestamp(event.occurred_at_ts, tz=timezone.utc).hour)

        dominant_asset = ""
        if asset_totals:
            dominant_asset = max(asset_totals.items(), key=lambda item: item[1])[0]

        top_counterparties = {
            address
            for address, _ in sorted(
                counterparty_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:5]
        }
        avg_amount = (
            sum(amounts, start=Decimal("0")) / Decimal(len(amounts))
            if amounts
            else Decimal("0")
        )

        return WalletPatternProfile(
            dominant_asset=dominant_asset,
            incoming_share=incoming_count / max(len(events), 1),
            avg_amount=avg_amount,
            tx_count=len(events),
            active_hours=hours,
            top_counterparties=top_counterparties,
        )

    def _score_pattern_match(
        self,
        target: WalletPatternProfile,
        candidate: WalletPatternProfile,
    ) -> Tuple[int, List[str]]:
        hour_overlap = self._jaccard(target.active_hours, candidate.active_hours)
        counterparty_overlap = self._jaccard(target.top_counterparties, candidate.top_counterparties)
        direction_score = max(0.0, 1.0 - abs(target.incoming_share - candidate.incoming_share))
        tx_count_score = self._ratio_score(target.tx_count, candidate.tx_count)
        amount_score = self._decimal_ratio_score(target.avg_amount, candidate.avg_amount)
        asset_score = 1.0 if target.dominant_asset and target.dominant_asset == candidate.dominant_asset else 0.0

        weighted = (
            counterparty_overlap * 0.3
            + hour_overlap * 0.22
            + direction_score * 0.18
            + amount_score * 0.15
            + tx_count_score * 0.1
            + asset_score * 0.05
        )
        score = min(100, max(1, round(weighted * 100)))

        reason_pairs = [
            (counterparty_overlap, "общие контрагенты"),
            (hour_overlap, "похожие часы активности"),
            (direction_score, "похожее соотношение входов и выходов"),
            (amount_score, "похожий средний чек"),
            (tx_count_score, "схожая частота движений"),
            (asset_score, "тот же основной актив"),
        ]
        reasons = [label for value, label in sorted(reason_pairs, reverse=True) if value >= 0.5][:2]
        return score, reasons

    @staticmethod
    def _jaccard(left: set, right: set) -> float:
        if not left or not right:
            return 0.0
        union = left | right
        if not union:
            return 0.0
        return len(left & right) / len(union)

    @staticmethod
    def _ratio_score(left: int, right: int) -> float:
        if left <= 0 or right <= 0:
            return 0.0
        return min(left, right) / max(left, right)

    @staticmethod
    def _decimal_ratio_score(left: Decimal, right: Decimal) -> float:
        if left <= 0 or right <= 0:
            return 0.0
        return float(min(left, right) / max(left, right))

    def _format_asset_totals(self, totals: Dict[str, Decimal], signed: bool = False) -> str:
        if not totals:
            return "0"

        parts = []
        for asset, total in sorted(totals.items()):
            absolute = total.copy_abs() if signed else total
            number = format_decimal(absolute)
            if signed:
                if total > 0:
                    number = "+{0}".format(number)
                elif total < 0:
                    number = "-{0}".format(number)
            parts.append("{0} {1}".format(number, asset))
        return ", ".join(parts)

    @staticmethod
    def _format_seen_time(timestamp: int) -> str:
        if not timestamp:
            return "unknown"
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%m-%d %H:%M UTC")

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
